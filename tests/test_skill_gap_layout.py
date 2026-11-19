"""Guard the skill-gap filter fix against the original giant-control regression.

The generic .filter-bar sets flex-basis for bare inputs. Inside flex-column
labels, that basis becomes height unless the skill-gap selectors override it.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_gap_filter_inputs_have_fixed_height_without_flex_basis():
    css = (ROOT / "static" / "skill-gap.css").read_text(encoding="utf-8")
    assert ".gap-filter-bar > label > .select," in css
    assert ".gap-filter-bar > label > .search" in css
    assert "flex:none;" in css
    assert "height:38px;" in css
    assert "max-height:38px;" in css
    assert "grid-template-columns:" in css
    assert "@media(max-width:1400px)" in css
    assert "@media(max-width:980px)" in css
    assert "@media(max-width:600px)" in css


def test_summary_distinguishes_unassessed_skills_and_keeps_help_compact():
    js = (ROOT / "static" / "skill-gap.js").read_text(encoding="utf-8")
    assert "已评估能力" in js
    assert "s.status!=='unknown'" in js
    assert "<details class=\"gap-help\">" in js
    assert "gap-metrics" in js



def test_skill_gap_large_list_is_paginated_and_filters_reset_page():
    js = (ROOT / "static" / "skill-gap.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "skill-gap.css").read_text(encoding="utf-8")
    assert "const GAP_PAGE_SIZE=25;" in js
    assert "items.slice(first,first+GAP_PAGE_SIZE)" in js
    assert "pagination.visible.map(s=>gapRow(s,max))" in js
    assert "data-gap-page=" in js
    assert "gap-page-jump" in js
    assert "gapGoToPage(field.value)" in js
    assert "gapPage=1;gapExpanded='';" in js
    assert "await refreshSkillGap()" in js
    assert "gapFilters.q=el.value;gapPage=1;gapExpanded=''" in js
    assert "#gap-list" in js
    assert ".gap-pagination{" in css
    assert ".gap-page-jump input{" in css


def test_each_skill_row_has_learning_shortcut_without_changing_mastery():
    js = (ROOT / "static" / "skill-gap.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "skill-gap.css").read_text(encoding="utf-8")
    assert "data-gap-quick-learn" in js
    assert "data-gap-open-learning" in js
    assert "已加入 · 查看" in js
    assert "'/skill-gap/learning'" in js
    assert "row.has_active_learning=true" in js
    assert "state.learning.unshift(task)" in js
    assert ".gap-quick-learn{" in css
    assert ".gap-learning-added{" in css
    assert "grid-template-columns:minmax(145px,200px)" in css


def test_hidden_learning_filter_defaults_on_and_added_go_last_when_shown():
    """Learned skills must be removed before pagination, never just hidden by CSS."""
    js = (ROOT / "static" / "skill-gap.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "skill-gap.css").read_text(encoding="utf-8")
    assert "hideLearning:true" in js
    assert "data-gap-filter=\"hideLearning\"" in js
    assert "const items=gapVisibleSkills()" in js
    assert "gapVisibleSkills().length/GAP_PAGE_SIZE" in js
    assert "function gapHasActiveLearning(" in js
    assert "return [...matches].sort(" in js
    assert "case 'showLearning':" in js
    assert "if(filter.dataset.gapFilter==='hideLearning'){render();return}" in js
    assert ".gap-hide-learning{" in css


def test_hidden_and_last_learning_order_in_52_skill_browser_simulation():
    import shutil
    import subprocess
    import pytest

    if shutil.which("node") is None:
        pytest.skip("Node.js not available")

    script = r"""
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const mock={
 document:{addEventListener:()=>{},querySelector:()=>null},
 state:{page:'skills',learning:[]},
 head:(title)=>title,
 stat:()=>'',esc:x=>String(x??''),
 opt:()=>'',directions:{},tiers:{},
 toast:()=>{},render:()=>{},
 api:async()=>({created:true,task:{
   id:901,title:'学习：Skill-02',related_skill:'Skill-02',
   status:'todo',priority:'medium',notes:'',source_job_id:null
 }})
};
const ctx=vm.createContext(mock);
vm.runInContext(fs.readFileSync('static/skill-gap.js','utf8'),ctx);
const rows=Array.from({length:52},(_,i)=>{
 const active=i===0||i===2;
 return {skill:'Skill-'+String(i+1).padStart(2,'0'),
  key:'skill-'+String(i+1).padStart(2,'0'),
  job_count:52-i,company_count:1,must_jobs:0,bonus_jobs:0,
  confirmed_jobs:1,gpt_jobs:0,candidate_jobs:0,
  status:'unknown',note:'',examples:[],personal_evidence:[],
  has_active_learning:active,
  learning_tasks:active?[{id:i+1,title:'学习任务',status:'todo'}]:[],
  total_evidence:0};
});
ctx.rows=rows;
vm.runInContext('gapData={skills:rows,selected_jobs:52,covered_jobs:52};',ctx);
assert.equal(vm.runInContext('gapFilters.hideLearning',ctx),true);
assert.equal(vm.runInContext('gapVisibleSkills().length',ctx),50);
assert.equal(vm.runInContext('gapPageData(gapVisibleSkills()).pages',ctx),2);
assert.equal(vm.runInContext('gapVisibleSkills()[0].skill',ctx),'Skill-02');
assert.equal(vm.runInContext('gapMatchingSkills().length',ctx),52);
vm.runInContext('gapFilters.hideLearning=false;',ctx);
assert.equal(vm.runInContext('gapVisibleSkills().length',ctx),52);
assert.equal(vm.runInContext('gapPageData(gapVisibleSkills()).pages',ctx),3);
assert.equal(vm.runInContext('gapVisibleSkills().at(-2).skill',ctx),'Skill-01');
assert.equal(vm.runInContext('gapVisibleSkills().at(-1).skill',ctx),'Skill-03');
assert.equal(vm.runInContext('gapVisibleSkills()[0].skill',ctx),'Skill-02');
// Adding a third task should push it to the end without changing status or JD count.
(async()=>{
 await vm.runInContext("gapAddLearningQuick('Skill-02')",ctx);
 assert.equal(vm.runInContext('gapVisibleSkills().at(-1).skill',ctx),'Skill-03');
 assert.equal(vm.runInContext('gapVisibleSkills().at(-3).skill',ctx),'Skill-01');
 assert.equal(vm.runInContext('gapData.skills[1].status',ctx),'unknown');
 assert.equal(vm.runInContext('state.learning.length',ctx),1);
 vm.runInContext('gapFilters.hideLearning=true;',ctx);
 assert.equal(vm.runInContext('gapVisibleSkills().length',ctx),49);
 assert.equal(vm.runInContext('gapPageData(gapVisibleSkills()).pages',ctx),2);
 vm.runInContext('gapFilters.q="Skill-03";',ctx);
 assert.equal(vm.runInContext('gapVisibleSkills().length',ctx),0);
 vm.runInContext('gapFilters.hideLearning=false;',ctx);
 assert.equal(vm.runInContext('gapVisibleSkills()[0].skill',ctx),'Skill-03');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_met_quick_action_keeps_detail_and_other_statuses():
    """The shortcut must not replace the original evidence/details flow."""
    import shutil
    import subprocess
    import pytest

    js = (ROOT / "static" / "skill-gap.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "skill-gap.css").read_text(encoding="utf-8")
    assert "data-gap-mark-met" in js
    assert "data-gap-quick-learn" in js
    assert "data-gap-status" in js
    assert "data-gap-expand" in js
    assert "JD 原文依据" in js
    assert "我的能力与经历" in js
    assert "学习任务" in js
    assert ".gap-row-actions{" in css
    assert ".gap-row-actions .gap-status{" in css
    if shutil.which("node") is None:
        pytest.skip("Node.js not available")

    script = r"""
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const writes=[];
const mock={
 document:{addEventListener:()=>{},querySelector:()=>null},
 state:{page:'skills',learning:[]},
 esc:v=>String(v??''),opt:()=>'',render:()=>{},toast:()=>{},
 api:async(path,opts)=>{
   const body=JSON.parse(opts.body);writes.push({path,body});
   return {status:body.status};
 }
};
const ctx=vm.createContext(mock);
vm.runInContext(fs.readFileSync('static/skill-gap.js','utf8'),ctx);
const sample={skill:'Python',key:'python',job_count:8,company_count:3,
 must_jobs:5,bonus_jobs:2,confirmed_jobs:8,gpt_jobs:0,candidate_jobs:0,
 status:'unknown',note:'保留用户笔记',has_active_learning:false,
 personal_evidence:[{description:'实习项目',skill:'Python',evidence_type:'project',
 review_status:'documented'}],
 learning_tasks:[],examples:[{company:'示例企业',job_title:'AI 实习',
 strength:'must',origin:'confirmed',text:'熟悉 Python',job_id:10}],
 total_evidence:1};
ctx.sample=sample;
ctx.rows=[{...sample},{...sample,skill:'SQL',key:'sql',job_count:4}];
vm.runInContext("gapData={skills:rows,selected_jobs:2,covered_jobs:2};",ctx);
const before=vm.runInContext('gapRow(gapData.skills[0],8)',ctx);
assert.ok(before.includes('data-gap-mark-met="Python"'));
assert.ok(before.includes('data-gap-quick-learn="Python"'));
assert.ok(before.includes('data-gap-status="Python"'));
assert.ok(before.includes('data-gap-expand="python"'));
assert.ok(before.includes('>详情</button>'));
assert.ok(before.includes('<option value="partial">部分满足</option>'));
assert.ok(before.includes('<option value="missing">未满足</option>'));
assert.ok(before.includes('<option value="unknown">待评估</option>'));
vm.runInContext("gapExpanded='python';",ctx);
const expanded=vm.runInContext('gapRow(gapData.skills[0],8)',ctx);
for(const text of ['JD 原文依据','我的能力与经历','学习任务','添加个人经历']){
 assert.ok(expanded.includes(text),text);
}
(async()=>{
 await vm.runInContext("gapSetStatus('Python','met')",ctx);
 assert.equal(writes.length,1);
 assert.equal(writes[0].path,'/skill-gap/status');
 assert.deepEqual(writes[0].body,{skill:'Python',status:'met',note:'保留用户笔记'});
 assert.equal(vm.runInContext('gapVisibleSkills().length',ctx),1);
 vm.runInContext('gapFilters.hideLearning=false;',ctx);
 assert.equal(vm.runInContext('gapVisibleSkills().at(-1).skill',ctx),'Python');
 const after=vm.runInContext('gapRow(gapData.skills[0],8)',ctx);
 assert.ok(after.includes('>✓ 已满足</button>'));
 assert.ok(after.includes('data-gap-expand="python"'));
 await vm.runInContext("gapSetStatus('Python','partial')",ctx);
 assert.equal(writes.at(-1).body.status,'partial');
 assert.equal(vm.runInContext('gapData.skills[0].note',ctx),'保留用户笔记');
 assert.equal(vm.runInContext('gapVisibleSkills()[0].skill',ctx),'Python');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True,
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
