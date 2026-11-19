/* Evidence shown here is explicitly linked by a saved analysis finding. */
const assessmentLabels = {
 direct:'已有相关能力', transferable:'可迁移经历', not_documented:'资料未提及',
 gap:'建议提升', needs_check:'信息不足'
};

function assessmentMarkup(finding, context = {}) {
 const requirements = context.requirements || finding.requirement_details || [];
 const evidence = context.personal_evidence || [];
 return (finding.assessments || []).map(item => {
  const requirement = requirements.find(row => row.id === item.requirement_id);
  return '<section class="assessment-item">'+
   '<div class="page-toolbar"><strong>'+esc(requirement?.name || '岗位要求 #'+item.requirement_id)+
   '</strong><span class="pill">'+esc(assessmentLabels[item.judgment] || item.judgment)+'</span></div>'+
   '<p>'+esc(item.explanation)+'</p>'+
   '<p class="assessment-action"><strong>下一步</strong> '+esc(item.next_action)+'</p>'+
   (finding.id&&finding.review_status!=='rejected'?'<button class="btn btn-small" data-finding-action="'+finding.id+'" data-requirement="'+item.requirement_id+'" '+(finding.action_tasks?.[item.requirement_id]?'disabled':'')+'>'+(finding.action_tasks?.[item.requirement_id]?'已加入准备任务':'加入准备任务')+'</button>':'')+
   '<details><summary>查看要求 #'+item.requirement_id+' 与个人经历</summary>'+
   (requirement ? '<blockquote>'+esc(requirement.evidence)+'</blockquote>'+
    (requirement.stale ? '<p class="error">岗位原文已变化，请重新核对这项要求。</p>' : '') :
    '<p class="tip">要求 #'+item.requirement_id+'，可在关联岗位档案中查看原文。</p>')+
   (item.personal_evidence_ids.length ? item.personal_evidence_ids.map(id => {
    const row = evidence.find(record => record.id === id);
    return row ? '<div class="dossier-evidence"><strong>个人经历 #'+id+' · '+esc(row.skill)+'</strong>'+
     '<p>'+esc(row.description)+'</p>'+

     (row.source_url ? '<a href="'+esc(secureLink(row.source_url))+'" target="_blank" rel="noopener noreferrer">参考资料来源 ↗</a>' : '')+'</div>' :
     '<p class="tip">个人经历 #'+id+(context.missing_personal_evidence_ids?.includes(id)?' 已删除，可调整这项建议引用的资料。':'，可在关联岗位档案中查看原记录。')+'</p>';
   }).join('') : '<p class="tip">可结合你的能力与经历调整这项建议。</p>')+
   '</details></section>';
 }).join('');
}

function jobDossierTabs() {
 return '<nav class="detail-tabs dossier-tabs" role="tablist" aria-label="岗位档案资料">'+
  [['original','原始 JD'],['requirements','岗位要求'],['analysis','经历与建议'],['tasks','准备任务']].map(([key,label],index)=>
   '<button type="button" role="tab" class="detail-tab" id="dossier-tab-'+key+
   '" aria-controls="dossier-'+key+'" aria-selected="'+(index===0)+'" tabindex="'+(index===0?0:-1)+
   '" data-dossier-tab="'+key+'">'+label+'</button>').join('')+'</nav>';
}

function jobDossierPanels(job) {
 activeJobDossier=job;
 const data = job.dossier || {requirements:[],findings:[],personal_evidence:[],learning_tasks:[]};
 const requirements = data.requirements.map(item => {
  const status = item.stale ? '原文已变化' : {confirmed:'已确认',pending:'待核对',rejected:'已拒绝'}[item.review_status];
  return '<section class="dossier-item" id="dossier-requirement-'+item.id+'">'+
   '<div class="page-toolbar"><strong>'+esc(item.name)+'</strong><span class="pill">'+esc(status)+
   '</span><span class="tip">'+esc({must:'硬性要求',bonus:'加分项',mentioned:'仅提及',unknown:'强度待核实'}[item.strength])+'</span></div>'+
   '<blockquote>'+esc(item.evidence)+'</blockquote><p class="tip">来源于'+
   (item.source_field==='eligibility'?'资格说明':'原始 JD')+' · 要求 #'+item.id+'</p>'+
   (item.review_status==='pending'&&!item.stale ? '<div class="page-toolbar">'+
    '<button class="btn btn-small" type="button" data-dossier-review="'+item.id+'" data-action-name="confirm">确认要求</button>'+
    '<button class="btn btn-small" type="button" data-dossier-review="'+item.id+'" data-action-name="reject">拒绝候选</button></div>' : '')+'</section>';
 }).join('');
 const findings=maintainedFindings(data.findings,data);
 const tasks = data.learning_tasks.map(item => '<section class="dossier-item">'+
  '<div class="page-toolbar"><strong>'+esc(item.title)+'</strong><span class="pill">'+
  esc({todo:'待开始',doing:'进行中',done:'已完成'}[item.status])+'</span></div>'+
  '<p class="dossier-task-notes">'+esc(item.notes)+'</p>'+preparationLinks(item)+
  '<button type="button" class="btn btn-small" data-prep-complete="'+item.id+'">'+(item.status==='done'?'登记新成果':'完成任务 / 登记成果')+'</button></section>').join('');
 return '<section id="dossier-requirements" role="tabpanel" aria-labelledby="dossier-tab-requirements" data-dossier-panel="requirements" hidden>'+
  (requirements||'<p class="tip">还没有提取岗位要求。可在 AI 分析中提取要求，再回到这里逐条核对。</p>')+'</section>'+
  '<section id="dossier-analysis" role="tabpanel" aria-labelledby="dossier-tab-analysis" data-dossier-panel="analysis" hidden>'+
  (findings||'<p class="tip">还没有关联到这个岗位的分析。将岗位加入求职分析包，主动选择个人经历后交给 AI 助手分析。</p>')+'</section>'+
  '<section id="dossier-tasks" role="tabpanel" aria-labelledby="dossier-tab-tasks" data-dossier-panel="tasks" hidden>'+
  (tasks||'<p class="tip">还没有关联的准备任务。可以采纳本岗位的学习建议，或在学习计划中新建并关联这个岗位。</p>')+'</section>';
}

function showDossierTab(key) {
 document.querySelectorAll('[data-dossier-tab]').forEach(tab => {
  const selected=tab.dataset.dossierTab===key;
  tab.setAttribute('aria-selected',String(selected));
  tab.tabIndex=selected?0:-1;
 });
 document.querySelectorAll('[data-dossier-panel]').forEach(panel=>{
  panel.hidden=panel.dataset.dossierPanel!==key;
 });
}

if(typeof document!=='undefined') {
 document.addEventListener('click',async event=>{
  const tab=event.target.closest('[data-dossier-tab]');
  if(tab){showDossierTab(tab.dataset.dossierTab);return;}
  const button=event.target.closest('[data-dossier-review],[data-dossier-adopt],[data-dossier-task]');
  if(!button||button.disabled||!canLeaveModal())return;
  const jobId=Number(document.querySelector('#job-update-form')?.dataset.job);
  if(!jobId)return;
  const origin=button.closest('.modal-body-content'),initialValues=modalValues();
  button.disabled=true;
  try {
   let target='requirements';
   if(button.dataset.dossierReview){
    await api('/requirements/'+button.dataset.dossierReview+'/review',{
     method:'POST',body:JSON.stringify({action:button.dataset.actionName})});
   }else if(button.dataset.dossierAdopt){
    await api('/analysis/findings/'+button.dataset.dossierAdopt+'/adopt-learning-task',{method:'POST'});
    target='tasks';
   }else{
    await api('/learning/'+button.dataset.dossierTask,{method:'PATCH',body:JSON.stringify({status:'done'})});
    target='tasks';
   }
   await refreshAfterWrite('已保存到岗位档案');
   if(button.isConnected&&document.querySelector('#modal').open&&
      document.querySelector('#modal-body').firstElementChild===origin&&modalValues()===initialValues){
    await jobDetail(jobId);
    showDossierTab(target);
    document.querySelector('[data-dossier-tab][aria-selected=true]')?.focus({preventScroll:true});
   }
  }catch(error){toast(error.message);}
  finally{if(button.isConnected)button.disabled=false;}
 });
 document.addEventListener('keydown',event=>{
  const tab=event.target.closest('[data-dossier-tab]');
  if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();
  const tabs=[...document.querySelectorAll('[data-dossier-tab]')],index=tabs.indexOf(tab);
  const next=event.key==='Home'?tabs[0]:event.key==='End'?tabs.at(-1):
   tabs[(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length];
  showDossierTab(next.dataset.dossierTab);
  next.focus();
 });
}
