"""Behaviour tests for the frontend: run the real render code in node instead of asserting on source strings."""
import json
import shutil

import pytest

from fe_helpers import HARNESS, run_node, script_files

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")


def harness(body):
    return ("const fe=require(" + json.dumps(str(HARNESS)) + ");\n"
            "const assert=require('node:assert/strict');\n" + body)


STUBS = """
const ctx=fe.boot({
 document:{addEventListener(){},querySelector(){return null},querySelectorAll(){return []},
  body:{classList:{add(){},remove(){},toggle(){return false},contains(){return false}},append(){}}},
 window:{matchMedia(){return{matches:false,addEventListener(){}}}},
 localStorage:{getItem(){return null},setItem(){}}
});
fe.loadCore(ctx);
fe.publish(ctx,'state','overview','jobWorkbenchPage','companiesPage','demandPage','sourcesPage',
 'reviewsPage','applicationsPage','evidencePage','learningPage','analysisPage','capturePage',
 'plannerPage','skillsPage','selectedJobs','analysisJobs','captureBulkActions','captureBulkCheckbox',
 'recognizeGPTData');
const state=ctx.state;
state.loaded=true;
const iso=n=>{const d=new Date();d.setDate(d.getDate()+n);
 return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')};
state.companies=[{id:1,name:'月之暗面',sector:'大模型',city:'北京',size_band:'大厂',tier_reason:'规模',job_count:30,status:'watching'}];
state.jobs=Array.from({length:30},(_,i)=>({id:i+1,title:'岗位 '+i,company:'月之暗面',company_tier:'大厂',
 company_tier_reason:'规模',direction:'pm',city:'北京',priority:i%3?'medium':'high',status:'verified',
 application_stage:'not_applied',deadline:iso(i-5),jd_text:'JD'}));
state.applications=[{job_id:1,title:'岗位 1',company:'月之暗面',stage:'contacted',resume_version:'v1',
 recruiter:'',next_action:'电话'}];
state.evidence=[{id:1,skill:'Prompt',proficiency:'working',evidence_type:'project',description:'x',review_status:'documented'}];
state.learning=[{id:1,title:'学习',related_skill:'评测',priority:'high',status:'doing',source_job_id:1}];
state.reviews=[{id:1,input:{title:'重复岗位',company:'x',city:'北京',source_platform:'boss',source_url:'https://e.com'},
 reason:'相似',match_job_id:2,match_title:'岗位 2'}];
state.summary={jobs:30,priority:10,verified:20,companies:1,recent:state.jobs.slice(0,5),directions:[{direction:'pm',count:30}]};
state.captureBatches=[{id:1,label:'批次',platform:'boss',city:'北京',status:'open',remaining:2,
 counts:{imported:0},keyword:'AI',latest_search_run_id:null}];
state.captureSelected=1;
state.captureItems=[{id:1,status:'pending',draft:{title:'候选岗位',company:'企业',city:'北京'},raw_text:'JD'},
 {id:2,status:'pending',draft:{title:'候选二',company:'企业二'},raw_text:'JD2'}];
"""


def test_every_page_renders_with_representative_data():
    script = harness(STUBS + """
const pages={overview:ctx.overview,jobs:ctx.jobWorkbenchPage,companies:ctx.companiesPage,
 demand:ctx.demandPage,reviews:ctx.reviewsPage,applications:ctx.applicationsPage,
 analysis:ctx.analysisPage,capture:ctx.capturePage,planner:ctx.plannerPage,skills:ctx.skillsPage};
for(const [name,fn] of Object.entries(pages)){
 const html=fn();
 assert.ok(typeof html==='string'&&html.length>100,name+' 渲染结果为空');
 assert.ok(!html.includes('undefined'),name+' 渲染出 undefined');
}
const jobs=ctx.jobWorkbenchPage();
assert.ok(jobs.includes('显示 1–25 条，共 30 条'),'岗位库应分页显示 25 条');
assert.ok(jobs.includes('全选本页'));
assert.ok(jobs.includes('deadline-overdue'),'列表里过期岗位要带颜色类');
assert.ok(jobs.includes('deadline-due'),'列表里临期岗位要带颜色类');
assert.ok(jobs.includes('只看 7 天内截止'));
const analysis=ctx.analysisPage();
assert.ok(analysis.includes('workflow-nav'),'分析页应有任务导航');
assert.ok(!analysis.includes('id="ai-review-panel"'),'首次进入不应出现批量复核');
""")
    run_node(script)


def test_job_selection_survives_rendering_and_bulk_actions_scope():
    script = harness(STUBS + """
fe.run(ctx,'jobSelectedIds=new Set([1,2]);jobListPage=1;');
const page=ctx.jobWorkbenchPage();
assert.ok(page.includes('已选 2 个岗位'),page.slice(0,200));
assert.ok(page.includes('应用到所选岗位')&&page.includes('导出所选 JSON')&&page.includes('分析所选岗位'));
assert.ok(page.includes('aria-label="选择岗位 岗位 0"'),'每行应有可读名称的勾选框');
assert.equal(ctx.selectedJobs().length,30);
fe.run(ctx,'state.filters.soon="1";');
const urgent=ctx.selectedJobs();
assert.ok(urgent.length>0&&urgent.length<30,'7 天内截止应缩小范围');
const tones=new Set(urgent.map(j=>ctx.state.jobs.find(x=>x.id===j.id)?'ok':'missing'));
assert.equal([...tones].join(','),'ok');
""")
    run_node(script)


def test_capture_bulk_toolbar_only_shows_for_pending_rows():
    script = harness(STUBS + """
const active={status:'open'};
fe.run(ctx,'captureBulkScope=()=>state.captureItems.filter(x=>x.status==="pending");');
const toolbar=ctx.captureBulkActions(ctx.state.captureItems,active);
assert.ok(toolbar.includes('集中核对并入库'),toolbar);
fe.run(ctx,'captureSelectedForBulk=new Set([1]);');
const withSelection=ctx.captureBulkActions(ctx.state.captureItems,active);
assert.ok(withSelection.includes('批量暂缓')&&withSelection.includes('批量不感兴趣'),withSelection);
assert.ok(withSelection.includes('已选 1 条'));
fe.run(ctx,'state.captureItems=state.captureItems.map(x=>({...x,status:"imported"}));');
assert.equal(ctx.captureBulkActions([],active),'','没有待核对资料时不应出现批量工具栏');
fe.run(ctx,'state.captureItems=[{id:1,status:"pending"}];state.captureSelected=1;');
const box=ctx.captureBulkCheckbox(ctx.state.captureItems[0]);
assert.ok(box.includes('data-capture-bulk-item="1"'),box);
assert.ok(!box.includes('disabled'),'批次开启时应可勾选');
assert.equal(ctx.captureBulkCheckbox({id:9,status:'imported'}),'','已入库资料不可再勾选');
""")
    run_node(script)


def test_gpt_import_recognizes_every_payload_shape():
    script = harness(STUBS + """
// Evaluate the payloads inside the sandbox so they are realm-native arrays/objects.
const cases=[
 ['[{"job_id":1,"priority":"high","reason":"x"}]','priority','/jobs/priority-import'],
 ['{"package_id":"p","priorities":[{"job_id":1,"priority":"high"}]}','priority','/jobs/priority-import'],
 ['{"package_id":"p","requirements":[{"job_id":1,"name":"Python","evidence":"熟悉 Python"}]}','requirements','/requirements/import'],
 ['{"package_id":"p","findings":[{"kind":"skill_gap","title":"x","content":"y"}]}','findings','/analysis/import'],
 ['{"batch_id":"b","decisions":[{"group":"g","verdict":"verified"}]}','review','/ai-review/import'],
 ['{"results":[{"batch_id":"b","decisions":[{"group":"g","verdict":"verified"}]}]}','review','/ai-review/import-bulk'],
];
for(const [literal,kind,route] of cases){
 const result=fe.run(ctx,'recognizeGPTData('+literal+')');
 assert.equal(result.kind,kind,literal);
 assert.equal(result.route,route);
}
const nested=fe.run(ctx,'recognizeGPTData({"package_id":"p","priorities":[{"job_id":1,"priority":"high"}]})');
assert.equal(nested.payload.package_id,'p','带批次的优先级结果必须保留 package_id');
let rejected=false;
try{ fe.run(ctx,'recognizeGPTData({"unrelated":true})') }catch(error){ rejected=true }
assert.ok(rejected,'无法识别的 JSON 必须报错，而不是猜一个格式');
""")
    run_node(script)


def test_index_html_loads_existing_modules_in_a_working_order():
    files = script_files()
    assert "job-library.js" in files and "analysis-view.js" in files
    assert files.index("job-library.js") < files.index("app.js")
    assert files.index("analysis-view.js") < files.index("app.js")
    assert files.index("analysis-workflows.js") < files.index("app.js")
    assert files.index("listing-pagination.js") < files.index("app.js")
    for name in files:
        assert (HARNESS.parent.parent.parent / "static" / name).exists(), name


def test_long_single_line_templates_do_not_grow():
    """Ratchet instead of a rewrite: the remaining long lines may only go down."""
    baseline = 14
    static = HARNESS.parent.parent.parent / "static"
    over = []
    for name in script_files():
        for number, line in enumerate((static / name).read_text(encoding="utf-8").splitlines(), 1):
            if len(line) > 1200:
                over.append(f"{name}:{number}({len(line)})")
    assert len(over) <= baseline, (
        f"单行超 1200 字符的模板从 {baseline} 条涨到 {len(over)} 条：{', '.join(over[:5])}；"
        "请拆行，并把基线调低")

