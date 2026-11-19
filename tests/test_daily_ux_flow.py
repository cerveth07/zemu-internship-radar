"""User-facing daily-flow regressions: deadline visibility, add-job defaults, intake continuity,
application shortcut and a compact analysis workbench."""
import json
import re
import shutil
import subprocess
from fe_helpers import frontend_source
from fe_helpers import HARNESS
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def run_node(script):
    done = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True,
                          timeout=30, check=False)
    stdout = (done.stdout or b"").decode("utf-8", "replace")
    stderr = (done.stderr or b"").decode("utf-8", "replace")
    assert done.returncode == 0, stderr + stdout
    return stdout


def test_deadline_helpers_mark_overdue_soon_and_missing_dates():
    if shutil.which("node") is None:
        pytest.skip("Node.js not installed")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('static/app.js','utf8');
const start=code.indexOf('const deadlineDays =');
const end=code.indexOf('async function load(){');
assert.ok(start>=0&&end>start);
const ctx={esc:x=>String(x??'')};vm.createContext(ctx);
// Arrow constants stay in the script's lexical scope, so publish them explicitly.
vm.runInContext(code.slice(start,end)+
 '\nthis.deadlineStatus=deadlineStatus;this.deadlinePill=deadlinePill;this.deadlineCell=deadlineCell;',ctx);
const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const day=n=>{const d=new Date();d.setDate(d.getDate()+n);return iso(d)};
assert.equal(ctx.deadlineStatus('').tone,'none');
assert.equal(ctx.deadlineStatus('2026/10/21').tone,'none');
assert.equal(ctx.deadlineStatus(day(-2)).tone,'overdue');
assert.ok(ctx.deadlineStatus(day(-2)).text.includes('已过期 2 天'));
assert.equal(ctx.deadlineStatus(day(0)).tone,'due');
assert.equal(ctx.deadlineStatus(day(0)).text,'今天截止');
assert.equal(ctx.deadlineStatus(day(3)).tone,'due');
assert.ok(ctx.deadlineStatus(day(3)).text.includes('即将截止'));
assert.equal(ctx.deadlineStatus(day(21)).tone,'later');
assert.ok(ctx.deadlineStatus(day(21)).text.includes('剩 21 天'));
assert.ok(ctx.deadlineCell('').includes('未注明截止日期'));
assert.ok(ctx.deadlineCell(day(3)).includes('class="deadline-date"'));
assert.ok(ctx.deadlineCell(day(-3)).includes('deadline-note deadline-overdue'),'过期必须在列表里上色');
assert.ok(ctx.deadlineCell(day(3)).includes('deadline-note deadline-due'),'临期必须在列表里上色');
assert.ok(ctx.deadlineCell(day(30)).includes('deadline-note deadline-later'));
assert.ok(ctx.deadlineCell('').includes('deadline-note deadline-none'));
assert.ok(ctx.deadlinePill(day(-1)).includes('deadline-overdue'));
"""
    run_node(script)


def test_job_list_and_reading_view_show_deadline():
    jobs = (STATIC / "job-bulk.js").read_text(encoding="utf-8")
    app = frontend_source()
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "<th>截止日期</th>" in jobs
    assert "deadlineCell(j.deadline)" in jobs
    assert "const deadlineStatus = value =>" in app
    assert "deadlinePill(j.deadline)" in app
    assert "job-deadline-notice" in app
    for name in ("deadline-overdue", "deadline-due", "deadline-later", "deadline-none"):
        assert ".pill." + name in css


def test_job_library_sorts_by_deadline_and_filters_the_next_seven_days():
    jobs = (STATIC / "job-bulk.js").read_text(encoding="utf-8")
    app = frontend_source()
    assert "function sortJobRows(rows)" in jobs
    assert "id=\"filter-sort\"" in jobs and "id=\"filter-soon\"" in jobs
    assert "if(event.target.id==='filter-soon')" in jobs
    assert "(!soon||deadlineUrgent(j.deadline))" in app
    if shutil.which("node") is None:
        pytest.skip("Node.js not installed")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const day=n=>{const d=new Date();d.setDate(d.getDate()+n);return iso(d)};
const rows=[
 {id:1,deadline:day(-5)},{id:2,deadline:day(2)},{id:3,deadline:day(20)},
 {id:4,deadline:''},{id:5,deadline:day(-1)},{id:6,deadline:'2026-13-40'},
];
const app=fs.readFileSync('static/app.js','utf8');
const bulk=fs.readFileSync('static/job-bulk.js','utf8');
const appSlice=app.slice(app.indexOf('const deadlineDays ='),app.indexOf('async function load(){'));
const state={jobSort:'priority',jobs:rows,filters:{q:'',direction:'',status:'',priority:'',city:'',tier:'',soon:''}};
const ctx={state,esc:x=>String(x??'')};vm.createContext(ctx);
vm.runInContext(appSlice+'\n'+
 bulk.slice(bulk.indexOf('function jobDeadlineRank('),bulk.indexOf('function jobWorkbenchPage(')),ctx);
vm.runInContext('this.sortJobRows=sortJobRows;this.deadlineUrgent=deadlineUrgent;this.deadlineDays=deadlineDays;this.deadlineStatus=deadlineStatus;',ctx);
const ids=list=>JSON.stringify(list.map(x=>x.id));
// 先展示仍有时间的机会，再展示已过期与未知日期。
state.jobSort='deadline';
assert.equal(ids(ctx.sortJobRows(rows)),'[2,3,5,1,6,4]');
state.jobSort='recent';
assert.equal(ids(ctx.sortJobRows(rows)),'[6,5,4,3,2,1]');
state.jobSort='priority';
assert.equal(ids(ctx.sortJobRows(rows)),'[1,2,3,4,5,6]');
// “7 天内截止”排除已过期、远期与无日期。
const urgent=rows.filter(j=>ctx.deadlineUrgent(j.deadline)).map(x=>x.id);
assert.equal(JSON.stringify(urgent),'[2]');
assert.equal(ctx.deadlineDays('2026-13-40'),null);
assert.equal(ctx.deadlineStatus('2026-13-40').tone,'none');
"""
    run_node(script)


def test_typography_and_measure_come_from_scale_tokens():
    files = ["style.css", "skill-gap.css", "analysis-workflows.css", "task-actions.css",
             "export-manager.css", "listing-pagination.css"]
    css = "\n".join((STATIC / name).read_text(encoding="utf-8") for name in files)
    assert "--fs-xs: 12px" in css and "--fs-base: 14px" in css and "--fs-2xl: 24px" in css
    assert "font: var(--fs-base)/var(--lh-base) var(--ui-font)" in css
    assert "small { font-size: var(--fs-xs); }" in css
    assert not re.search(r"font-size:\s*\d+px", css), "字号必须走 token，不要写死像素"
    assert not re.search(r"font:\s*\d+px", css), "font 简写也不能写死像素"
    assert "--measure: 46em" in css and "max-width: var(--measure)" in css
    assert "max-width: 1280px" in css


def test_palette_radii_and_layout_styles_are_tokenised():
    for path in sorted(STATIC.glob("*.css")):
        body = path.read_text(encoding="utf-8")
        if ":root" in body:
            start = body.index(":root")
            depth = 0
            for i in range(body.index("{", start), len(body)):
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        body = body[:start] + body[i + 1:]
                        break
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?=[\s;,)'\"}])", body), f"{path.name}: 颜色必须走 token"
        for value in re.findall(r"border-radius:\s*([^;}]+)", body):
            assert "var(--radius" in value or value.strip() == "0", f"{path.name}: 圆角必须走 token: {value}"
    base = (STATIC / "style.css").read_text(encoding="utf-8")
    for token in ("--accent:", "--success:", "--warn:", "--danger:", "--info:", "--line:", "--text:", "--muted:"):
        assert token in base, token
    scripts = "\n".join(p.read_text(encoding="utf-8") for p in sorted(STATIC.glob("*.js")))
    leftovers = [value for value in re.findall(r'style="([^"]*)"', scripts) if not value.startswith("width:")]
    assert leftovers == [], f"布局 inline style 应改用工具类: {leftovers}"


def test_reading_view_shows_deadline_once_plus_an_overdue_warning():
    app = frontend_source()
    assert '· <strong>截止：</strong>' not in app          # 元信息行不再重复
    assert "deadlineStatus(j.deadline).tone==='overdue'" in app  # 只有过期才给提示条
    assert "<small>截止日期</small><strong>${j.deadline?esc(j.deadline):'未注明'}</strong> ${deadlinePill(j.deadline)}" in app
    assert "job-deadline-notice" in app


def test_add_job_form_requires_explicit_company_and_defaults_to_undecided():
    app = frontend_source()
    assert "function selectField(name,label,options,selected='',required=false)" in app
    # An ordered list keeps the empty placeholder first; integer-like object keys (company ids)
    # would be enumerated before it, so the browser would not treat it as the required placeholder.
    assert "const companyOptions=[['','请选择企业'],...state.companies.map(c=>[String(c.id),c.name])]" in app
    assert "selectField('company_id','关联企业 *',companyOptions,'',true)" in app
    assert "function directionField(current='other')" in app
    assert "directionField()" in (STATIC / "job-library.js").read_text(encoding="utf-8")
    assert "企业默认不预选" in app


def test_intake_keeps_other_selections_when_one_row_is_edited():
    bulk = (STATIC / "capture-bulk.js").read_text(encoding="utf-8")
    capture = (STATIC / "capture.js").read_text(encoding="utf-8")
    assert "if(edit&&canLeaveModal())" not in bulk
    assert "(!captureIntakeResuming&&!canLeaveModal())" in bulk
    assert "captureIntakeResuming=true" in bulk
    assert "rememberIntakeReturn(id)" in bulk
    assert "resumeCaptureIntake(itemId)" in capture
    assert "时长尚未整理，请查看 JD" in bulk
    assert "出勤尚未整理，请查看 JD" in bulk
    if shutil.which("node") is None:
        pytest.skip("Node.js not installed")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const checked=[{dataset:{intakeSelect:'1'},checked:true,disabled:false},
               {dataset:{intakeSelect:'2'},checked:true,disabled:false}];
const stub=()=>({document:{addEventListener:()=>{},querySelectorAll:()=>checked,querySelector:()=>null},
                 esc:x=>String(x??''),toast:()=>{}});

// Editing row 2 must remember the remaining rows and the other ticked rows.
const editing=stub();vm.createContext(editing);
vm.runInContext(fs.readFileSync('static/capture-bulk.js','utf8'),editing);
vm.runInContext('captureIntakePreview={ids:[1,2,3],batch_id:7,items:[{id:1,version:"v1"},{id:2,version:"v2"}]};rememberIntakeReturn(2);'+
 'this.kept=captureIntakeReturn;',editing);
assert.equal(JSON.stringify(editing.kept.ids),'[1,2,3]');
assert.equal(JSON.stringify(editing.kept.selected),'[[1,"v1"]]'); // the edited row itself is about to be committed
assert.equal(editing.kept.edited,2);

// Returning the same row's save reopens the table with the other ticks intact.
const resuming=stub();vm.createContext(resuming);
vm.runInContext(fs.readFileSync('static/capture-bulk.js','utf8'),resuming);
vm.runInContext('captureIntakeReturn={ids:[1,2,3],batch_id:7,selected:[[1,"v1"]],edited:2};'+
 'openCaptureBulkReview=async function(ids,batch){this.reopened=[ids,batch]};',resuming);
resuming.resumeCaptureIntake(2).then(ok=>{
 vm.runInContext('this.preset=[...captureIntakePreselect]',resuming);
 assert.equal(ok,true);
 assert.equal(JSON.stringify(resuming.reopened),'[[1,3],7]');
 assert.equal(JSON.stringify(resuming.preset),'[[1,"v1"]]');
 // A save for a different row must not reopen a stale table.
 vm.runInContext('captureIntakeReturn={ids:[1],batch_id:7,selected:[],edited:2}',resuming);
 return resuming.resumeCaptureIntake(99);
}).then(ok=>assert.equal(ok,false)).catch(e=>{console.error(e);process.exit(1)});
"""
    run_node(script)


def test_application_can_be_recorded_from_reading_view_and_empty_tracking_page():
    app = frontend_source()
    assert "data-job-reading-apply" in app
    assert "function openApplicationForm()" in app
    assert "function chooseApplyJob()" in app
    assert "'choose-apply-job':chooseApplyJob" in app
    if shutil.which("node") is None:
        pytest.skip("Node.js not installed")
    script = r"""
const fe=require(FE_HARNESS_PATH);
const assert=require('node:assert/strict');
const code=fe.slice('function applicationsPage(){','const issueNames=');
const ctx=fe.boot({state:{applications:[]},
 head:(title,desc,action='')=>'<h1>'+title+'</h1>'+action,
 empty:(title,desc,button)=>'<div class="empty">'+title+'|'+button+'</div>',
 esc:x=>String(x??''),fmt:x=>x?String(x):'<span class="muted">未填写</span>',
 stages:{not_applied:'未申请'}});
fe.run(ctx,code);
const emptyPage=ctx.applicationsPage();
assert.ok(emptyPage.includes('选择岗位，记录进度'),emptyPage);
assert.equal((emptyPage.match(/choose-apply-job/g)||[]).length,2);
fe.run(ctx,"state.applications=[{job_id:3,title:'岗位甲',company:'甲公司',stage:'contacted',resume_version:'v1',recruiter:'',next_action:'约面试'}]");
const filled=ctx.applicationsPage();
assert.ok(filled.includes('choose-apply-job')&&filled.includes('记录一次投递'));
assert.ok(filled.includes('岗位甲'));
""".replace("FE_HARNESS_PATH", json.dumps(str(HARNESS)))
    run_node(script)


def test_extraction_page_keeps_the_first_run_to_four_steps():
    app = frontend_source()
    assert "'analyze-full-jd':analyzeFullJd" in app
    assert "function analyzeFullJd()" in app
    assert "第四阶段：人工核对候选要求" in app
    if shutil.which("node") is None:
        pytest.skip("Node.js not installed")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const state={workflow:{jobs:[],batches:[],reserved_job_ids:[]},requirementData:{total:0},jobs:[{id:1}]};
const ctx={state,esc:x=>String(x??''),
 requirementsPanel:()=>'<div id="review-panel">审核候选</div>',
 aiReviewPanel:()=>'<div id="ai-review-panel">AI 复核</div>',
 directions:{},statuses:{},priorities:{}};
ctx.analysisJobs=()=>ctx.state.jobs;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/analysis-workflows.js','utf8'),ctx);

// First run: nothing imported yet, so the advanced batch-review area stays closed.
state.workflow={jobs:[{job_id:1,pending:0,confirmed:0,stale:0}],batches:[],reserved_job_ids:[]};
const first=ctx.workflowExtraction();
assert.ok(!first.includes('id="ai-review-panel"'),'advanced review must stay hidden');
assert.ok(first.includes('可选：AI 辅助复核'));
assert.ok(first.includes('analyze-full-jd'));
const order=['第一阶段：准备完整 JD','第二阶段：交给 AI 助手处理','第三阶段：导回结果','id="review-panel"']
 .map(needle=>first.indexOf(needle));
order.forEach((at,i)=>assert.ok(at>=0&&(i===0||at>order[i-1]),'step order '+i));

// After candidates arrive the optional assisted review opens next to the review list.
state.workflow={jobs:[{job_id:1,pending:3,confirmed:0,stale:0}],batches:[],reserved_job_ids:[]};
const later=ctx.workflowExtraction();
assert.ok(later.includes('id="ai-review-panel"'));
assert.ok(later.indexOf('id="review-panel"')<later.indexOf('id="ai-review-panel"'));
"""
    run_node(script)
