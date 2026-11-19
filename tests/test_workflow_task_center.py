"""P3 workflow UX and JD extraction batch progress.

The status is deliberately weaker than 'fully extracted': confirmation of one
requirement never establishes completeness of the entire JD.
"""
import json
from fe_helpers import frontend_source
import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db, SCHEMA_VERSION
from app.main import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("RADAR_PRIVATE_RC_DB_PATH",str(tmp_path/"workflow.sqlite"))
    with TestClient(app) as c:
        yield c


def new_job(client,index=1):
    company=client.post("/api/companies",json={
        "name":f"模型团队-{index}","headcount_text":"10000人以上",
    })
    assert company.status_code==201,company.text
    job=client.post("/api/jobs",json={
        "company_id":company.json()["id"],"title":f"Agent 实习-{index}",
        "jd_text":"要求熟悉 Python 与 Agent 框架",
        "direction":"agent","priority":"high",
    })
    assert job.status_code==201,job.text
    return job.json()["id"]


def export(client,ids):
    response=client.post("/api/exports/jobs",json={
        "ids":ids,"filters":{"direction":"agent","priority":"high"},"intent":"jd_extraction",
    })
    assert response.status_code==200,response.text[:500]
    return response.json()["package_id"]


def submit_requirements(client,package_id,job_id):
    result=client.post("/api/requirements/import",json={
        "package_id":package_id,"dry_run":False,
        "requirements":[{
            "job_id":job_id,"requirement_type":"technical_skill",
            "name":"Python","strength":"must",
            "evidence":"要求熟悉 Python","source_field":"jd_text",
            "option_logic":"single","alternatives":[],
        }],
    })
    assert result.status_code==200,result.text
    return result


def overview(client):
    result=client.get("/api/workflow")
    assert result.status_code==200,result.text
    return result.json()


def test_general_export_does_not_create_false_waiting_for_gpt_batch(client):
    jid=new_job(client)
    response=client.post("/api/exports/jobs",json={"ids":[jid],"filters":{}})
    assert response.status_code==200,response.text
    assert response.json()["package_id"]
    assert overview(client)["batches"]==[]


def test_new_export_is_resumable_without_fake_gpt_completion(client):
    a=new_job(client,1)
    before=overview(client)
    assert before["batches"]==[]
    assert before["jobs"]==[{"job_id":a,"confirmed":0,"pending":0,"rejected":0,"stale":0}]
    pid=export(client,[a])
    state=overview(client)
    assert state["batches"][0]["package_id"]==pid
    assert state["batches"][0]["stage"]=="awaiting_gpt"
    assert state["batches"][0]["job_count"]==1
    assert state["batches"][0]["pending"]==0
    init_db() # opening program again does not forget the batch
    assert overview(client)["batches"][0]["stage"]=="awaiting_gpt"
    assert SCHEMA_VERSION>=16


def test_import_and_review_progress_tracks_actual_candidate_states(client):
    a=new_job(client)
    pid=export(client,[a])
    submit_requirements(client,pid,a)
    state=overview(client)
    assert state["batches"][0]["stage"]=="needs_review"
    assert state["batches"][0]["pending"]==1
    assert state["jobs"][0]["pending"]==1
    reqs=client.get("/api/requirements?status=pending&package_id="+pid).json()
    assert reqs["total"]==1
    rid=reqs["items"][0]["id"]
    confirmed=client.post(f"/api/requirements/{rid}/review",json={"action":"confirm"})
    assert confirmed.status_code==200,confirmed.text
    updated=overview(client)
    assert updated["batches"][0]["stage"]=="reviewed"
    assert updated["batches"][0]["confirmed"]==1
    assert updated["jobs"][0]["confirmed"]==1
    # Neither endpoint claims the full JD was extracted.
    assert "completeness" not in updated["batches"][0]


def test_one_batch_review_does_not_show_other_batch_records(client):
    a=new_job(client,1); b=new_job(client,2)
    first=export(client,[a]); second=export(client,[b])
    submit_requirements(client,first,a);submit_requirements(client,second,b)
    one=client.get("/api/requirements?status=pending&package_id="+first).json()
    two=client.get("/api/requirements?status=pending&package_id="+second).json()
    assert one["total"]==1 and two["total"]==1
    assert one["items"][0]["job_id"]==a
    assert two["items"][0]["job_id"]==b
    assert client.get("/api/requirements?status=pending").json()["total"]==2


def test_changed_jd_marks_export_as_stale_even_before_any_gpt_response(client):
    a=new_job(client)
    pid=export(client,[a])
    with connect() as conn:
        conn.execute("UPDATE jobs SET jd_text='现在要求 Java 而非 Python' WHERE id=?",(a,))
    state=overview(client)
    assert state["batches"][0]["stage"]=="stale"
    assert state["batches"][0]["changed_job_ids"]==[a]
    assert client.post("/api/workflow/batches/"+pid+"/no-candidates").status_code==409
    assert overview(client)["batches"][0]["stage"]=="stale"


def test_no_candidates_must_be_explicit_and_cannot_hide_saved_candidates(client):
    a=new_job(client)
    pid=export(client,[a])
    result=client.post("/api/workflow/batches/"+pid+"/no-candidates")
    assert result.status_code==200,result.text
    assert overview(client)["batches"][0]["stage"]=="no_candidates"
    bogus=client.post("/api/workflow/batches/not-a-batch/no-candidates")
    assert bogus.status_code==404
    b=new_job(client,2)
    other=export(client,[b])
    submit_requirements(client,other,b)
    denied=client.post("/api/workflow/batches/"+other+"/no-candidates")
    assert denied.status_code==409
    assert overview(client)["batches"][0]["stage"]=="needs_review"



def test_imported_duplicate_only_is_not_mistaken_for_unreturned_gpt(client):
    jid=new_job(client)
    first=export(client,[jid])
    submit_requirements(client,first,jid)
    second=export(client,[jid])
    again=submit_requirements(client,second,jid)
    assert again.json()["new_count"]==0
    record=next(b for b in overview(client)["batches"] if b["package_id"]==second)
    assert record["stage"]=="imported_duplicates"
    assert record["requirements_total"]==0
    assert record["import_count"]==1
    assert record["imported_at"]
    assert client.post("/api/workflow/batches/"+second+"/no-candidates").status_code==409



def test_legacy_imports_remain_visible_and_database_survives_migration(client):
    jid=new_job(client)
    package=client.post("/api/analysis/packages",json={"job_ids":[jid]})
    assert package.status_code==200,package.text
    pid=package.json()["package"]["package_id"]
    submit_requirements(client,pid,jid)
    batch=next(b for b in overview(client)["batches"] if b["package_id"]==pid)
    assert batch["origin"]=="legacy_import"
    init_db()
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0]==SCHEMA_VERSION
        assert conn.execute("PRAGMA quick_check").fetchone()[0]=="ok"
        assert conn.execute("SELECT COUNT(*) FROM job_requirements").fetchone()[0]==1



def test_v15_existing_database_adds_only_batch_tracking_table(client):
    jid=new_job(client)
    legacy=client.post("/api/analysis/packages",json={"job_ids":[jid]})
    assert legacy.status_code==200,legacy.text
    pid=legacy.json()["package"]["package_id"]
    with connect() as conn:
        conn.execute("DROP TABLE jd_extraction_batches")
        conn.execute("PRAGMA user_version=15")
    init_db()
    with connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0]==SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM analysis_packages WHERE id=?",(pid,)).fetchone()[0]==1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='jd_extraction_batches'").fetchone()
        assert conn.execute("PRAGMA quick_check").fetchone()[0]=="ok"



def test_old_extraction_batches_remain_reserved_after_history_is_paginated(client):
    from app.requirements import source_hash
    first=new_job(client,1)
    second=new_job(client,2)
    with connect() as conn:
        hashes={}
        for jid in (first,second):
            job=conn.execute("SELECT jd_text,eligibility FROM jobs WHERE id=?",(jid,)).fetchone()
            hashes[jid]=source_hash(job)
        for number in range(85):
            jid=first if number==0 else second
            package_id=f"11111111-2222-3333-4444-{number:012d}"
            conn.execute("""
                INSERT INTO analysis_packages
                  (id,filters_json,snapshot_json,included_ids_json,item_count)
                VALUES(?,?,?,?,?)
            """,(package_id,"{}","{}",json.dumps([jid]),1))
            conn.execute("""
                INSERT INTO jd_extraction_batches(package_id,jd_hashes_json)
                VALUES(?,?)
            """,(package_id,json.dumps({str(jid):hashes[jid]})))
    state=overview(client)
    assert len(state["batches"])==80
    assert first not in {jid for batch in state["batches"] for jid in batch["job_ids"]}
    assert set(state["reserved_job_ids"])=={first,second}
    with connect() as conn:
        conn.execute("UPDATE jobs SET jd_text='更改后的 JD' WHERE id=?",(first,))
    assert first not in overview(client)["reserved_job_ids"]
    assert second in overview(client)["reserved_job_ids"]


def test_frontend_task_separation_and_json_file_import():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    main=frontend_source()
    flow=(root/"static"/"analysis-workflows.js").read_text(encoding="utf-8")
    html=(root/"static"/"index.html").read_text(encoding="utf-8")
    assert html.index("analysis-workflows.js")<html.index("/static/app.js")
    assert "analysis-workflows.css" in html
    for name in ("workflowHome","workflowExtraction","workflowCareer",
                 "workflowBatchList","workflowSummary"):
        assert ("function "+name+"(") in flow
    for name in ("openAnalysisFlow","selectAnalysisBatch","readGPTFile",
                 "markNoCandidates","exportOutstandingJds","reexportExtractionBatch",
                 "analysisLegacyPanel","analysisFindingsPanel"):
        assert ("function "+name+"(") in main
    assert "data-analysis-file" in flow
    assert "data-analysis-file" in main
    assert "data-analysis-batch" in flow


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="Node.js not installed")
def test_workbench_renders_real_home_extraction_and_career_preflight():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const state={
 analysisFlow:'home',analysisBatch:'',
 analysisFilters:{direction:'',status:'',priority:'',city:'',job_ids:'',baseline_id:'',include_private:false},
 analysisPreview:null,analysisPackages:[],analysisFindings:[],analysisImportRaw:'',analysisImportPreview:null,
 workflow:{jobs:[{job_id:1,pending:0,confirmed:0,stale:0}],batches:[]},
 careerMode:'compact',careerGoal:'gap',careerEvidenceIds:[],evidence:[],careerBusy:false,requirementData:{total:0},
 jobs:[{id:1}],filters:{q:'',direction:'',status:'',priority:'',city:'',tier:''}
};
const ctx={state,selectedJobs:()=>state.jobs,analysisJobs:()=>state.jobs,
  esc:x=>String(x??''),head:title=>'<h1>'+title+'</h1>',
  requirementsPanel:()=>'<div>审核任务</div>',
  aiReviewPanel:()=>'<div>AI 复核</div>',
  careerExportPanel:()=>'<div>求职分析数据包</div>',
  opt:()=>'',directionOptions:()=>[],directions:{},statuses:{},priorities:{},
  findingTypes:{},};
vm.createContext(ctx);
const code=fs.readFileSync('static/analysis-view.js','utf8');
const begin=code.indexOf('function analysisLegacyPanel(){');
const end=code.indexOf('\nconst reqTypes=',begin);
assert.ok(begin>=0 && end>begin);
vm.runInContext(fs.readFileSync('static/company-profile.js','utf8')+'\n'+fs.readFileSync('static/analysis-workflows.js','utf8')+'\n'+code.slice(begin,end),ctx);
for(const [mode,needle] of [
 ['home','最近 JD 提取批次'],['extract','第一阶段：准备完整 JD'],
 ['career','求职分析数据包'],['advanced','旧版通用分析数据包']
]){
  state.analysisFlow=mode;
  const page=ctx.analysisPage();
  assert.ok(page.includes(needle),mode);
  assert.ok(page.includes('workflow-nav'),mode);
  assert.ok(!page.includes('00 · 求职分析数据包'),mode);
}
state.analysisFlow='career';
assert.ok(ctx.workflowCareerStatus().includes('精简版不包含原始 JD'));
state.careerMode='full';
assert.ok(ctx.workflowCareerStatus().includes('完整原始 JD'));
"""
    done=subprocess.run(["node","-e",script],cwd=root,capture_output=True,text=True,
                        check=False,timeout=20)
    assert done.returncode==0,done.stderr+done.stdout


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="Node.js not installed")
def test_quick_batch_switch_never_exposes_old_review_items():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const js=fs.readFileSync('static/analysis-view.js','utf8');
const section=(a,b)=>{const i=js.indexOf(a),j=js.indexOf(b,i+a.length);
 assert.ok(i>=0&&j>i);return js.slice(i,j);};
const body=section('let requirementQueryRevision=0;','async function reviewRequirement(')+
 section('async function openAnalysisFlow(','async function exportOutstandingJds(');
let resolves=[],renders=[];
const state={analysisFlow:'home',analysisBatch:'',
 workflow:{batches:[{package_id:'A',stage:'needs_review'},{package_id:'B',stage:'awaiting_gpt'}]},
 requirementFilters:{status:'pending',offset:0},
 requirementData:{items:[{id:999}],total:1},requirementSelected:[999]};
const ctx={state,
 api:route=>new Promise(resolve=>resolves.push({route,resolve})),
 render:()=>renders.push([state.analysisBatch,...state.requirementData.items.map(x=>x.id)]),
 $:()=>({scrollTop:0}),toast:()=>{}};
vm.createContext(ctx);vm.runInContext(body,ctx);
(async()=>{
 const pendingA=ctx.openAnalysisFlow('extract');
 assert.ok(resolves[0].route.includes('package_id=A'));
 assert.equal(renders[0][1],undefined); // old data must be erased immediately
 const pendingB=ctx.selectAnalysisBatch('B');
 assert.ok(resolves[1].route.includes('package_id=B'));
 resolves[1].resolve({items:[{id:22}],total:1,offset:0});
 await pendingB;
 resolves[0].resolve({items:[{id:11}],total:1,offset:0});
 await pendingA;
 assert.equal(state.analysisBatch,'B');
 assert.equal(state.requirementData.items[0].id,22);
 assert.ok(!renders.some(x=>x.includes(999)||x.includes(11)));
})().catch(e=>{console.error(e);process.exit(1)});
"""
    finished=subprocess.run(["node","-e",script],cwd=root,capture_output=True,
                            text=True,check=False,timeout=20)
    assert finished.returncode==0,finished.stdout+finished.stderr
