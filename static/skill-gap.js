/* Unified skill-gap workbench. Reuses existing batch-bound GPT import APIs. */
let gapData=null, gapLoading=false, gapError='', gapExpanded='', gapBusy=false, gapRevision=0;
let gapFilters={scope:'target',direction:'',q:'',includePending:true,hideLearning:true};
const GAP_PAGE_SIZE=25;
let gapPage=1;
let gapEvidence=null;
let gapQuickLearningKey='';
const gapStatuses={unknown:'待评估',met:'已满足',partial:'部分满足',missing:'未满足'};
const gapStrength={must:'硬性要求',bonus:'加分项',mentioned:'仅提及',unknown:'不明确'};
function gapParams(){
 const p=new URLSearchParams({scope:gapFilters.scope,direction:gapFilters.direction,
  include_pending:String(gapFilters.includePending)});
 for(const key of ['headcount_min','headcount_max','include_unknown_size','sector'])if(gapFilters[key]!=null&&gapFilters[key]!=='')p.set(key,gapFilters[key]);
 return p;
}
async function refreshSkillGap(){
 const revision=++gapRevision;
 gapLoading=true;gapError='';if(state.page==='skills')render();
 try{
  const result=await api('/skill-gap?'+gapParams());
  if(revision===gapRevision)gapData=result;
 }catch(e){if(revision===gapRevision)gapError=e.message}
 finally{if(revision===gapRevision){gapLoading=false;if(state.page==='skills')render()}}
}
function gapHasActiveLearning(skill){
 return Boolean(skill.has_active_learning ||
  skill.learning_tasks.some(task=>task.status==='todo'||task.status==='doing'));
}
function gapIsHandled(skill){
 return skill.status==='met'||gapHasActiveLearning(skill);
}
function gapMatchingSkills(){
 if(!gapData)return [];
 return gapData.skills.filter(s=>!gapFilters.q||s.skill.toLowerCase().includes(gapFilters.q.toLowerCase()));
}
function gapVisibleSkills(){
 const matches=gapMatchingSkills();
 if(gapFilters.hideLearning)return matches.filter(s=>!gapIsHandled(s));
 // Keep original demand ranking within both groups.
 return [...matches].sort((a,b)=>Number(gapIsHandled(a))-Number(gapIsHandled(b)));
}
function gapPageData(items){
 const total=items.length, pages=Math.max(1,Math.ceil(total/GAP_PAGE_SIZE));
 const page=Math.min(Math.max(1,gapPage),pages);
 const first=(page-1)*GAP_PAGE_SIZE;
 return {total,pages,page,first,visible:items.slice(first,first+GAP_PAGE_SIZE)};
}
function gapGoToPage(requested){
 if(!gapData||gapLoading)return;
 const n=Number(requested);
 if(!Number.isFinite(n)||n<1)return;
 const pages=Math.max(1,Math.ceil(gapVisibleSkills().length/GAP_PAGE_SIZE));
 gapPage=Math.min(pages,Math.trunc(n));
 gapExpanded='';
 render();
 document.querySelector('#gap-list')?.scrollIntoView({block:'start'});
}
function gapPagination(data){
 if(!data.total)return '';
 const end=Math.min(data.total,data.first+GAP_PAGE_SIZE);
 const info='<span class="gap-page-summary">显示 '+(data.first+1)+'–'+end+' 项，共 '+data.total+' 项</span>';
 if(data.pages===1)return '<div class="gap-pagination">'+info+'</div>';
 return '<nav class="gap-pagination" aria-label="能力列表分页">'+info+
  '<div class="gap-page-controls"><button type="button" class="btn btn-small" data-gap-page="'+(data.page-1)+'"'+(data.page===1?' disabled':'')+'>上一页</button>'+
  '<span class="gap-page-current" aria-live="polite">第 '+data.page+' / '+data.pages+' 页</span>'+
  '<button type="button" class="btn btn-small" data-gap-page="'+(data.page+1)+'"'+(data.page===data.pages?' disabled':'')+'>下一页</button>'+
  '<form id="gap-page-jump" class="gap-page-jump"><label for="gap-page-number">跳至</label>'+
  '<input id="gap-page-number" name="page" type="number" min="1" max="'+data.pages+'" step="1" value="'+data.page+'" required aria-label="目标页码">'+
  '<button type="submit" class="btn btn-small">确定</button></form></div></nav>';
}
function skillGapPage(){
 const actions='<div class="page-toolbar"><button class="btn btn-small" data-page="evidence">管理我的能力与经历</button>'+
  '<button class="btn btn-primary" type="button" data-gpt-choose>导入 AI 结果</button></div>';
 const lead=head('能力差距','对照岗位里的能力要求和你记录的能力参考资料，找出被要求得多、你还没准备好的能力。',actions);
 const filters='<div class="filter-bar gap-filter-bar"><label>岗位范围<select class="select" data-gap-filter="scope">'+
  opt({target:'重点与适度关注（默认）',all:'全部已收录岗位'},gapFilters.scope)+'</select></label>'+
  '<label>岗位方向<select class="select" data-gap-filter="direction"><option value="">全部方向</option>'+
  opt(directionOptions(gapFilters.direction),gapFilters.direction)+'</select></label>'+
  companyFactFilters(gapFilters,'gap-filter')+
  '<label>筛选技能<input type="search" class="search" data-gap-search placeholder="输入技能名称" value="'+esc(gapFilters.q)+'"></label>'+
  '<label class="gap-candidate-toggle"><input type="checkbox" data-gap-filter="includePending"'+(gapFilters.includePending?' checked':'')+'> 包含待核候选</label>'+
  '<button class="btn btn-small" data-gap-action="advanced">高级 JD 统计</button></div>';
 const preview='';
 const metrics=gapData?'<div class="stats gap-metrics">'+
  stat('筛选岗位',gapData.selected_jobs,'默认：重点与适度关注')+
  stat('要求覆盖岗位',gapData.covered_jobs,'存在有效 JD 要求，非完整性证明')+
  stat('识别能力',gapData.skills.length,'包含明确标识的待核候选，按岗位去重')+
  stat('已评估能力',gapData.skills.filter(s=>s.status!=='unknown').length,
   '共 '+gapData.skills.length+' 项 · 未满足 '+gapData.skills.filter(s=>s.status==='missing').length+' 项')+'</div>':'';
 const notice='<details class="gap-help"><summary>统计说明：条形长度表示独立岗位数；“待核候选”不是已核实门槛</summary>'+
  '<p>技能出现频率不等于重要性或掌握水平。个人能力状态仅由你手动设置，不会被 AI 自动标记。'+
  '导入的待核候选可能包含误提取，可使用筛选项隐藏。</p>'+
  '<p>「清洗候选」来自离线技能清洗 V1 交接包，存放在独立暂存表中，按 (岗位, 能力) 去重后参与展示；'+
  '它同样只是候选，优先级低于人工确认与 AI 复核，不会写入已确认要求，也不会改动个人掌握状态。</p></details>';
 const importNotice='';
 if(gapError)return lead+filters+importNotice+preview+
  '<div class="notice" role="alert">统计读取失败：'+esc(gapError)+' <button class="btn btn-small" data-gap-action="refresh">重试</button></div>';
 // Never show stale-scope values or allow editing them while a new filter loads.
 if(gapLoading)return lead+filters+importNotice+preview+
  '<p class="subtitle" role="status">正在更新当前筛选范围，请稍候…</p>';
 if(!gapData)return lead+filters+importNotice+preview+
  '<p class="subtitle">尚未加载能力要求。<button class="btn btn-small" data-gap-action="refresh">读取数据</button></p>';
 if(gapData.selected_jobs===0){
  const sizeActive=sizeFilterActive(gapFilters);
  return lead+filters+metrics+'<div class="empty"><h3>'+(sizeActive?'没有符合人数范围的岗位':'当前范围没有岗位')+'</h3><p>'+
   (sizeActive?'可调整人数上下限，或清除人数筛选。':'可调整岗位范围、方向或企业行业。')+'</p>'+
   (sizeActive?'<button class="btn btn-small" data-gap-action="clearSize">清除人数筛选</button>':'')+'</div>';
 }
 const matching=gapMatchingSkills();
 const addedCount=matching.filter(gapHasActiveLearning).length;
 const metCount=matching.filter(s=>s.status==='met').length;
 const handledCount=matching.filter(gapIsHandled).length;
 const items=gapVisibleSkills();
 // Keep the chart scale stable as learned skills disappear from the queue.
 const max=Math.max(1,...matching.map(s=>s.job_count));
 const pagination=gapPageData(items);
 // A scope refresh can reduce the result count; never leave an invalid page selected.
 gapPage=pagination.page;
 return lead+filters+importNotice+preview+metrics+notice+
  '<section class="panel gap-list" id="gap-list"><div class="page-toolbar toolbar-split">'+
  '<h3 class="panel-title">能力需求与个人状态 <small>'+items.length+' 项</small></h3>'+
  '<div class="gap-list-tools"><span class="tip">待处理 '+(matching.length-handledCount)+' · 已满足 '+metCount+' · 已加入学习 '+addedCount+'</span>'+
  '<label class="gap-hide-learning"><input type="checkbox" data-gap-filter="hideLearning"'+
   (gapFilters.hideLearning?' checked':'')+'>隐藏已处理（已满足 / 已加入学习）</label></div></div>'+
  (gapLoading?'<p class="tip">正在更新统计…</p>':'')+
  (items.length?pagination.visible.map(s=>gapRow(s,max)).join(''):
    matching.length && handledCount===matching.length && gapFilters.hideLearning
     ? '<div class="empty"><h3>当前筛选的能力均已处理</h3>'+
       '<p>已满足或已加入学习计划的能力仍保留记录；取消隐藏后可在列表末尾查看和修改。</p>'+
       '<button class="btn btn-small" data-gap-action="showLearning">显示已处理的能力</button></div>'
     : gapFilters.q
     ? '<div class="empty"><h3>没有匹配的能力</h3><p>尝试修改搜索词或岗位范围。</p></div>'
     : '<div class="empty"><h3>当前范围还没有有效的 JD 技能要求</h3>'+
       '<p>可先在 AI 分析中导出 JD 提取包，将 AI 返回的 JSON 导入。也可以尝试“全部已收录岗位”。</p>'+
       '<button class="btn btn-small" data-gap-action="extract">前往 JD 提取</button></div>')+
  gapPagination(pagination)+'</section>';
}
function gapRow(s,max){
 const open=gapExpanded===s.key;
 const badges=(s.confirmed_jobs?'<span class="gap-source">人工确认 '+s.confirmed_jobs+' 岗</span>':'')+
  (s.manual_jobs?'<span class="gap-source">人工技能确认 '+s.manual_jobs+' 岗</span>':'')+
  (s.gpt_jobs?'<span class="gap-source">AI 复核 '+s.gpt_jobs+' 岗</span>':'')+
  (s.cleanup_jobs?'<span class="gap-source gap-pending">清洗候选 '+s.cleanup_jobs+' 岗</span>':'')+
  (s.candidate_jobs?'<span class="gap-source gap-pending">待核候选 '+s.candidate_jobs+' 岗</span>':'')+
  (s.actionable===false?'<span class="gap-source">通用素质 · 非专项学习</span>':'')+
  ((s.curation_flags||[]).length?'<span class="gap-source gap-pending">原文判断待核</span>':'');
 const statusBusy=Boolean(gapBusy||gapQuickLearningKey);
 const metButton='<button type="button" class="btn btn-small gap-mark-met'+
  (s.status==='met'?' gap-is-met':'')+'" data-gap-mark-met="'+esc(s.skill)+
  '" aria-pressed="'+(s.status==='met')+'" aria-label="将'+esc(s.skill)+'标记为已满足"'+
  (statusBusy?' disabled':'')+'>'+(s.status==='met'?'✓ 已满足':'已满足')+'</button>';
 const otherLabel=s.status==='met'?'其他状态':gapStatuses[s.status];
 const select='<select class="select gap-status gap-status-'+esc(s.status)+
  '" data-gap-status="'+esc(s.skill)+'" aria-label="修改'+esc(s.skill)+'的其他掌握状态"'+
  (statusBusy?' disabled':'')+'><option value="">'+esc(otherLabel)+' ▾</option>'+
  '<option value="partial">部分满足</option><option value="missing">未满足</option>'+
  '<option value="unknown">待评估</option></select>';
 const activeTask=s.learning_tasks.find(x=>x.status==='todo'||x.status==='doing');
 const queued=Boolean(s.has_active_learning||activeTask);
 const learningButton=queued
  ? '<button type="button" class="btn btn-small gap-quick-learn gap-learning-added" data-gap-open-learning="'+
    (activeTask?.id||'')+'" aria-label="查看'+esc(s.skill)+'的学习任务">已加入 · 查看</button>'
  : '<button type="button" class="btn btn-small gap-quick-learn" data-gap-quick-learn="'+esc(s.skill)+
    '" aria-label="将'+esc(s.skill)+'加入学习计划"'+(gapQuickLearningKey?' disabled':'')+'>'+
    (gapQuickLearningKey===s.key?'添加中…':'+ 加入学习')+'</button>';
 const examples=s.examples.map(x=>'<div class="gap-claim"><span class="tip">'+esc(x.company)+' · '+esc(x.job_title)+
  ' · '+esc(gapStrength[x.strength])+' · '+(({confirmed:'人工确认',manual_skill:'人工技能确认',gpt_assisted:'AI 复核',pending_candidate:'待核候选',cleanup_candidate:'技能清洗V1候选'}[x.origin]||'待核'))+'</span>'+
  '<p>「'+esc(x.text)+'」</p><button class="btn btn-small" data-job="'+x.job_id+'">查看岗位</button></div>').join('');
 const personal=s.personal_evidence.length?s.personal_evidence.map(x=>'<div class="gap-linked">'+
  '<strong>'+esc(x.description||x.skill)+'</strong><span class="tip"> '+esc(x.evidence_type)+' · '+
  '我的记录</span></div>').join(''):'<p class="tip">还没有记录这项能力的相关经历，可以直接添加。</p>';
 const learning=s.learning_tasks.length?s.learning_tasks.map(x=>'<div class="gap-linked">'+
  '<button class="table-link" data-learning="'+x.id+'">'+esc(x.title)+'</button>'+
  ' <span class="tip"> · '+esc(({todo:'待开始',doing:'进行中',done:'已完成'})[x.status]||x.status)+'</span></div>').join(''):'<p class="tip">暂无关联的学习任务。</p>';
 const provisional=(s.candidate_jobs||0)+(s.cleanup_jobs||0)===s.job_count;
 return '<div class="gap-item'+(provisional?' gap-provisional':'')+'"><div class="gap-row">'+
  '<button class="table-link gap-name" data-gap-expand="'+esc(s.key)+'" aria-expanded="'+open+'">'+esc(s.skill)+
  (provisional?' <small>待核</small>':(s.candidate_jobs||s.cleanup_jobs)?' <small>含待核</small>':'')+'</button>'+
  '<div class="gap-meter"><div class="bar-track"><div class="bar-fill" style="width:'+Math.round(100*s.job_count/max)+'%"></div></div></div>'+
  '<div class="gap-count">'+s.job_count+' 岗</div>'+
  '<div class="gap-row-actions">'+metButton+learningButton+select+'</div>'+
  '<button class="btn btn-small gap-expand" data-gap-expand="'+esc(s.key)+'" aria-expanded="'+open+'">'+(open?'收起':'详情')+'</button></div>'+
  (open?'<div class="gap-details">'+
   '<div class="gap-meta"><span>'+s.company_count+' 家企业</span><span>硬性要求 '+s.must_jobs+' 岗</span>'+
    '<span>加分项 '+s.bonus_jobs+' 岗</span>'+badges+'</div>'+
   (s.note?'<p class="tip">能力备注：'+esc(s.note)+'</p>':'')+
   ((s.curation_flags||[]).length?'<p class="tip">清洗复核提示：该候选原文涉及优先条件、任选组合、否定要求或加分参考资料不足；请结合完整 JD 核对“硬性/加分”判断。</p>':'')+
   '<div class="gap-detail-grid"><div><h4>JD 原文依据</h4>'+examples+
    (s.total_evidence>3?'<button class="btn btn-small" data-gap-evidence="'+esc(s.skill)+'">查看全部 '+s.total_evidence+' 条原文</button>':'')+
   '</div><div><h4>我的能力与经历</h4>'+personal+
   '<button class="btn btn-small" data-gap-add-evidence="'+esc(s.skill)+'">添加个人经历</button>'+
   '<h4>学习任务</h4>'+learning+
   '<button class="btn btn-small" data-gap-add-learning="'+esc(s.skill)+'">创建学习任务</button>'+
   '</div></div></div>':'')+'</div>';
}
async function gapAddLearningQuick(skill){
 if(gapQuickLearningKey||!gapData)return;
 const row=gapData.skills.find(x=>x.skill===skill);
 if(!row)return;
 if(row.has_active_learning){toast('该能力已有进行中或待开始的学习任务');return}
 gapQuickLearningKey=row.key;
 render();
 try{
  const result=await api('/skill-gap/learning',{
   method:'POST',body:JSON.stringify({skill})});
  const task=result.task;
  row.has_active_learning=true;
  if(!row.learning_tasks.some(x=>x.id===task.id))row.learning_tasks.unshift(task);
  if(!state.learning.some(x=>x.id===task.id))state.learning.unshift(task);
  if(gapExpanded===row.key)gapExpanded='';
  toast(result.created?'已加入学习计划：'+skill:'学习计划中已有该能力');
 }catch(e){toast('加入学习计划失败：'+e.message)}
 finally{gapQuickLearningKey='';if(state.page==='skills')render()}
}
async function gapSetStatus(skill,newStatus){
 if(gapBusy||gapQuickLearningKey||!gapData||!gapStatuses[newStatus])return;
 const row=gapData.skills.find(x=>x.skill===skill);
 if(!row||row.status===newStatus)return;
 gapBusy=true;
 render();
 try{
  const saved=await api('/skill-gap/status',{
   method:'PUT',body:JSON.stringify({skill,status:newStatus,note:row.note||''})});
  row.status=saved.status;
  if(gapIsHandled(row)&&gapExpanded===row.key)gapExpanded='';
  toast('已保存 '+skill+'：'+gapStatuses[saved.status]);
 }catch(e){toast('保存失败：'+e.message)}
 finally{gapBusy=false;if(state.page==='skills')render()}
}
async function gapEvidencePage(skill,offset=0){
 const params=gapParams();params.set('skill',skill);params.set('offset',String(offset));
 const result=await api('/skill-gap/evidence?'+params);
 gapEvidence=result;
 modal('JD 参考资料 · '+skill,'<p class="tip">仅展示当前 JD 有效的原文；AI 复核不等于人工确认。共 '+result.total+' 条。</p>'+
  '<div class="row-list">'+result.items.map(x=>'<div class="rowline"><div><strong>'+
  esc(x.company)+' · '+esc(x.job_title)+'</strong><p class="tip">'+esc(gapStrength[x.strength])+
  ' · '+(({confirmed:'人工确认',manual_skill:'人工技能确认',gpt_assisted:'AI 复核',pending_candidate:'待核候选',cleanup_candidate:'技能清洗V1候选'}[x.origin]||'待核'))+' · 「'+esc(x.evidence)+'」</p></div>'+
  '<button class="btn btn-small" data-job="'+x.job_id+'">岗位</button></div>').join('')+'</div>'+
  '<div class="page-toolbar"><button class="btn btn-small" data-gap-evidence-offset="'+Math.max(0,offset-30)+'" '+(!offset?'disabled':'')+'>上一页</button>'+
  '<span class="tip">'+(Math.floor(offset/30)+1)+' / '+Math.max(1,Math.ceil(result.total/30))+'</span>'+
  '<button class="btn btn-small" data-gap-evidence-offset="'+(offset+30)+'" '+(offset+30>=result.total?'disabled':'')+'>下一页</button></div>',true);
}
document.addEventListener('click',async event=>{
 try{
  const action=event.target.closest('[data-gap-action]');
  if(action){switch(action.dataset.gapAction){
   case 'advanced':navigate('demand');break;
   case 'extract':state.page='analysis';await openAnalysisFlow('extract');break;
   case 'refresh':await refreshSkillGap();break;
    case 'clearSize':delete gapFilters.headcount_min;delete gapFilters.headcount_max;delete gapFilters.include_unknown_size;gapPage=1;await refreshSkillGap();break;
   case 'showLearning':gapFilters.hideLearning=false;gapPage=1;render();break;
  }return}
  const pageButton=event.target.closest('[data-gap-page]');
  if(pageButton){gapGoToPage(pageButton.dataset.gapPage);return}
  const toggle=event.target.closest('[data-gap-expand]');
  if(toggle){gapExpanded=gapExpanded===toggle.dataset.gapExpand?'':toggle.dataset.gapExpand;render();return}
  const addE=event.target.closest('[data-gap-add-evidence]');
  if(addE){addEvidence();const input=document.querySelector('#workspace-evidence-form [name=skills]');if(input)input.value=addE.dataset.gapAddEvidence;return}
  const met=event.target.closest('[data-gap-mark-met]');
  if(met){await gapSetStatus(met.dataset.gapMarkMet,'met');return}
  const quick=event.target.closest('[data-gap-quick-learn]');
  if(quick){await gapAddLearningQuick(quick.dataset.gapQuickLearn);return}
  const existing=event.target.closest('[data-gap-open-learning]');
  if(existing){if(existing.dataset.gapOpenLearning)editLearning(Number(existing.dataset.gapOpenLearning));
   else navigate('learning');return}
  const addL=event.target.closest('[data-gap-add-learning]');
  if(addL){addLearning();const f=$('#learning-form');f.elements.namedItem('related_skill').value=addL.dataset.gapAddLearning;
   f.elements.namedItem('title').value='补足 '+addL.dataset.gapAddLearning+' 的实战能力';return}
  const evidence=event.target.closest('[data-gap-evidence]');
  if(evidence){await gapEvidencePage(evidence.dataset.gapEvidence);return}
  const page=event.target.closest('[data-gap-evidence-offset]');
  if(page&&gapEvidence){await gapEvidencePage(gapEvidence.skill,Number(page.dataset.gapEvidenceOffset));return}
 }catch(e){toast(e.message)}
});
document.addEventListener('change',async event=>{
 const filter=event.target.closest('[data-gap-filter]');
 if(filter){gapFilters[filter.dataset.gapFilter]=filter.type==='checkbox'?filter.checked:filter.value;
  gapPage=1;gapExpanded='';
  if(filter.dataset.gapFilter==='hideLearning'){render();return}
  await refreshSkillGap();return}
 const status=event.target.closest('[data-gap-status]');
 if(status){if(status.value)await gapSetStatus(status.dataset.gapStatus,status.value);return}

});
document.addEventListener('input',event=>{
 const el=event.target.closest('[data-gap-search]');
 if(el){gapFilters.q=el.value;gapPage=1;gapExpanded='';const pos=el.selectionStart;render();
  const next=document.querySelector('[data-gap-search]');next?.focus();
  if(pos!==null)next?.setSelectionRange(pos,pos)}
});

document.addEventListener('submit',event=>{
 if(event.target.id!=='gap-page-jump')return;
 event.preventDefault();
 const field=event.target.elements.namedItem('page');
 if(field?.checkValidity())gapGoToPage(field.value);
});
