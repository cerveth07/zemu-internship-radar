let workspaceReturn=null,workspaceRestoring=false;
function clearWorkspaceReturn(){workspaceReturn=null;}
function workspaceModal(title,markup,wide=false){
 const form=document.querySelector('#job-update-form');
 if(document.querySelector('#modal').open&&form){
  workspaceReturn={job:Number(form.dataset.job),tab:document.querySelector('[data-dossier-tab][aria-selected=true]')?.dataset.dossierTab||'original',
   editor:!document.querySelector('#job-edit-area').hidden,editTab:document.querySelector('[data-detail-tab][aria-selected=true]')?.dataset.detailTab||'job',
   expandedAnalysis:Boolean(document.querySelector('#dossier-analysis .historical-findings')?.open),scroll:document.querySelector('#modal-body').scrollTop};
 }
 modal(title,markup,wide);
}
async function restoreWorkspaceReturn(){
 if(workspaceRestoring)return true;
 if(!workspaceReturn)return false;
 const back=workspaceReturn,body=document.querySelector('#modal-body').firstElementChild;
 workspaceReturn=null;workspaceRestoring=true;if(body)body.inert=true;modalReturnJob=null;
 try{
  await jobDetail(back.job);showDossierTab(back.tab);
  const older=document.querySelector('#dossier-analysis .historical-findings');if(older)older.open=back.expandedAnalysis;
  if(back.editor){showJobEdit();const tab=document.querySelector('[data-detail-tab="'+back.editTab+'"]');if(tab)setDetailTab(tab);}
  document.querySelector('#modal-body').scrollTop=back.scroll;
  document.querySelector(back.editor?'[data-detail-tab][aria-selected=true]':'[data-dossier-tab][aria-selected=true]')?.focus({preventScroll:true});
 }catch(error){workspaceReturn=back;toast('暂时无法返回原岗位，请再点一次关闭重试：'+error.message);}
 finally{workspaceRestoring=false;if(body?.isConnected)body.inert=false;}
 return true;
}
async function showSavedExperiences(){
 if(!canLeaveModal())return;
 clearWorkspaceReturn();modalReturnJob=null;await closeModal(true);personalPane='evidence';navigate('evidence');
}
function materialFileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('读取附件失败，请重新选择文件'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file);});}
/* Personal materials, user review decisions, preparation coverage and outcomes. */
let personalPane='evidence';
let activeJobDossier=null;
const reviewLabels={current:'当前采用',needs_review:'资料有更新',rejected:'不采用'};
const evidenceTypes={self_report:'能力自述',project:'项目',internship:'实习',course:'课程',other:'其他'};
const evidenceLevels={learning:'正在学习',basic:'了解基础',working:'有使用经验',strong:'熟练使用'};
function firstRunIntro(){return state.jobs.length?'':'<section class="first-run-intro"><h2 class="panel-title">先体验一份完整岗位档案</h2><p>从已有 JD 开始，或在空库中加入三个明确标注的示例岗位，了解岗位、分析建议和准备任务。</p><div class="page-toolbar"><button class="btn btn-primary" data-quick-job>粘贴已有 JD</button><button class="btn" data-install-examples>体验示例（加入 3 个示例岗位）</button><button class="btn" data-material-import>导入已有经历</button><button class="btn" data-company-list-example>查看企业名单示例</button></div></section>';}
function evidenceSkills(value){return [...new Set(String(value||'').split(/[,，、;；\n]/).map(s=>s.trim()).filter(Boolean))];}
function materialOptions(selected){return '<label class="field">关联实际材料版本<select name="material_version_id">'+
 opt([['','不关联材料'],...(state.materials||[]).map(m=>[String(m.id),m.title+' · '+m.created_at])],String(selected||''))+'</select></label>';}
function evidenceFields(item={}){
 return '<div class="field-grid">'+field('skills','能力标签（选填，用逗号分隔）','例如 Excel, 活动运营')+
 selectField('proficiency','实际程度',evidenceLevels)+selectField('evidence_type','经历类型',evidenceTypes)+

 field('source_url','作品链接（选填）','https://')+materialOptions(item.material_version_id)+'</div>'+
 '<label class="field">能力说明或相关经历<textarea name="description" rows="5" maxlength="4000" placeholder="描述你的能力、经历或已完成的成果"></textarea></label>';
}
function populateEvidence(form,item={}){
 form.elements.skills.value=(item.skills||[item.skill||'']).join(', ');
 for(const key of ['proficiency','evidence_type','source_url','description']){
  form.elements[key].value=item[key]||({proficiency:'working',evidence_type:'self_report'}[key]||'');
 }
}
function evidencePayload(form){
 const data=Object.fromEntries([...form.querySelectorAll('[name]')].map(el=>[el.name,el.value])),skills=evidenceSkills(data.skills);
 return {skill:skills[0]||'',skills,proficiency:data.proficiency,evidence_type:data.evidence_type,
  description:data.description,source_url:data.source_url,review_status:'documented',
  material_version_id:data.material_version_id?Number(data.material_version_id):null};
}
function workspaceEvidencePage(){
 const materials=state.materials||[];
 const tabs='<nav class="page-toolbar" aria-label="个人资料类型">'+[['evidence','个人经历'],['materials','材料版本']].map(([key,label])=>
  '<button class="btn '+(personalPane===key?'btn-primary':'')+'" data-personal-pane="'+key+'" aria-pressed="'+(personalPane===key)+'">'+label+'</button>').join('')+'</nav>';
 const actions='<button class="btn" data-material-import>导入个人经历</button><button class="btn" data-material-create>保存材料版本</button><button class="btn btn-primary" data-action="add-evidence">+ 添加能力或经历</button>';
 const body=personalPane==='materials'?(materials.length?'<div class="row-list">'+materials.map(m=>'<div class="rowline"><div><strong>'+esc(m.title)+
  '</strong><small>'+esc(m.name)+' · '+esc(m.filename||'文本快照')+' · '+esc(m.created_at)+'</small></div><div class="page-toolbar">'+
  '<button class="btn btn-small" data-material-open="'+m.id+'">查看 / 下载</button><button class="btn btn-small" data-material-create="'+m.material_id+'" data-material-version="'+m.id+'">保存新版本</button></div></div>').join('')+'</div>':empty('还没有材料版本','保存实际简历或项目说明，投递时即可选择当时使用的版本。')):
  (state.evidence.length?'<div class="row-list">'+state.evidence.map(e=>'<div class="rowline is-top"><div class="is-grow"><strong>'+esc((e.skills||[e.skill]).filter(Boolean).join(' · ')||'个人经历')+
  '</strong><small>'+esc(evidenceTypes[e.evidence_type])+' · '+esc(evidenceLevels[e.proficiency])+' · '+'我的记录'+'</small><p class="preserve-lines">'+esc(e.description)+'</p>'+
  (e.material_version_id?'<button class="btn btn-small" data-material-open="'+e.material_version_id+'">查看原材料版本</button>':'')+'</div><div class="page-toolbar">'+
  '<button class="btn btn-small" data-evidence-edit="'+e.id+'">编辑</button><button class="btn btn-small" data-evidence-history="'+e.id+'">修改记录</button><button class="btn btn-small" data-remove-evidence="'+e.id+'">删除</button></div></div>').join('')+'</div>':
  empty('先记录已有经历','直接记录你的能力、项目或实习经历；同一经历可以关联多个能力。','<button class="btn btn-primary" data-material-import>导入第一段经历</button>'));
 return head('我的能力与经历','记录能力、经历与作品，随时补充和修改。',actions)+tabs+body;
}
function openEvidenceEditor(id=null){
 if(!canLeaveModal())return;
 const item=id?state.evidence.find(row=>row.id===id):{};
 if(!item)throw new Error('这条经历已删除，请重新加载');
 workspaceModal(id?'编辑能力与经历':'添加个人经历','<form id="workspace-evidence-form" data-id="'+(id||'')+'" data-revision="'+(item.revision||1)+'">'+
  '<p class="tip">直接保存你的填写内容，保留原有编号；相关分析会提示资料有更新。</p>'+evidenceFields(item)+actions()+'</form>',true);
 populateEvidence($('#workspace-evidence-form'),item);
}
function preparationLinks(task){
 const jobs=(task.linked_jobs||[]).map(j=>'<button type="button" class="table-link" data-job="'+j.id+'">'+esc(j.title)+' · '+esc(j.company)+'</button>').join('、');
 const requirements=(task.linked_requirements||[]).map(r=>'<p><strong>'+esc(r.name)+'</strong> · 要求 #'+r.id+'</p><blockquote>'+esc(r.evidence)+'</blockquote>').join('');
 return '<div class="preparation-coverage"><strong>覆盖 '+(task.linked_jobs||[]).length+' 个岗位</strong><div>'+ (jobs||jobLinkLabel(task.source_job_id))+'</div>'+
  (requirements?'<details><summary>对应 '+task.linked_requirements.length+' 项要求</summary>'+requirements+'</details>':'')+'</div>';
}
function workspaceLearningPage(){
 return head('准备任务与成果','查看一项准备覆盖哪些岗位，完成后登记真实成果。','<button class="btn btn-primary" data-action="add-learning">+ 新建任务</button>')+
  (state.learning.length?'<div class="row-list">'+state.learning.map(t=>'<section class="dossier-item"><div class="page-toolbar"><button class="table-link" data-prep-open="'+t.id+'">'+esc(t.title)+
  '</button><span class="pill">'+esc({todo:'待开始',doing:'进行中',done:'已完成'}[t.status])+'</span></div>'+preparationLinks(t)+
  '<div class="page-toolbar"><button class="btn btn-small" data-prep-complete="'+t.id+'">'+(t.status==='done'?'登记新成果':'完成任务 / 登记成果')+'</button>'+
  (t.outcome_evidence_ids||[]).map(id=>'<button class="btn btn-small" data-evidence-edit="'+id+'">查看已保存的成果</button>').join('')+'</div></section>').join('')+'</div>':
  empty('还没有准备任务','可以从逐项判断的下一步行动创建任务，再同时保存到我的经历。'));
}
function openPreparationTask(id){
 const task=state.learning.find(t=>t.id===id);if(!task||!canLeaveModal())return;
 workspaceModal('准备任务','<h3>'+esc(task.title)+'</h3>'+preparationLinks(task)+'<p class="preserve-lines">'+esc(task.notes)+'</p><form id="workspace-task-form" data-id="'+id+'">'+
  '<div class="field-grid">'+field('title','任务名称 *','','text',true)+selectField('status','进度',{todo:'待开始',doing:'进行中',done:'已完成'})+'</div>'+
  '<label class="field">准备说明<textarea name="notes" rows="5">'+esc(task.notes)+'</textarea></label>'+actions()+'</form><button class="btn" data-prep-complete="'+id+'">完成任务 / 登记成果</button>',true);
 $('#workspace-task-form [name=title]').value=task.title;$('#workspace-task-form [name=status]').value=task.status;
}
function openTaskCompletion(id){
 const task=state.learning.find(t=>t.id===id);if(!task||!canLeaveModal())return;
 workspaceModal('完成准备任务','<h3>'+esc(task.title)+'</h3>'+preparationLinks(task)+
  '<form id="workspace-complete-form" data-id="'+id+'" data-revision="'+task.revision+'" data-completion="'+crypto.randomUUID()+'">'+
  '<p class="notice">完成后可把这次成果加入我的经历，供后续岗位分析使用。</p>'+
  '<label class="field-inline"><input type="checkbox" id="complete-register-evidence">同时保存到我的经历</label>'+
  '<fieldset id="completion-evidence" disabled hidden><p class="tip">可直接修改预填的描述，补充这次完成的内容。</p>'+evidenceFields()+'</fieldset>'+actions()+'</form>',true);
 populateEvidence($('#workspace-complete-form'),{skill:task.related_skill,description:(task.title+'\n'+(task.notes||'')).slice(0,4000)});
}
function findingDecisionControls(item){return '<div class="page-toolbar"><span class="pill">'+esc(reviewLabels[item.review_status]||'当前采用')+'</span>'+
 '<button type="button" class="btn btn-small" data-finding-review="'+item.id+'">调整建议</button></div>'+(item.review_note?'<p class="tip">你的备注：'+esc(item.review_note)+'</p>':'');}
function findingJobLinks(item){
 const jobs=item.job_details||item.job_ids.map(id=>(state.jobs||[]).find(j=>j.id===id)).filter(Boolean);
 return '<div class="page-toolbar">'+jobs.map(j=>'<button type="button" class="btn btn-small" data-job="'+j.id+'">'+esc(j.company+' · '+j.title)+'</button>').join('')+'</div>';
}
function findingRequirement(item,id){return (item.requirement_details||activeJobDossier?.dossier?.requirements||[]).find(r=>r.id===id);}
function maintainedFindings(items,context={}){
 const card=item=>'<section class="dossier-item"><h3 class="detail-label">'+esc(item.title)+'</h3>'+findingDecisionControls(item)+'<p>'+esc(item.content)+'</p>'+findingJobLinks(item)+
  (item.source_changed?'<p class="notice">个人资料已有更新，可继续使用或调整这条建议。</p>':'')+assessmentMarkup(item,context)+
  '<details><summary>原始 AI 分析与引用</summary><p>批次 '+esc(item.package_id)+' · 岗位 '+esc(item.job_ids.join('、'))+'</p><p>要求 '+esc(item.requirement_ids.join('、'))+' · 个人经历 '+esc(item.personal_evidence_ids.join('、'))+'</p>'+
  (careerChangeSummary(item.career_stale_ack)?'<p class="tip">导入时的资料变化：'+esc(careerChangeSummary(item.career_stale_ack))+'</p>':'')+
  (!(item.assessments||[]).length?(context.requirements||[]).filter(r=>item.requirement_ids.includes(r.id)).map(r=>'<blockquote>'+esc(r.evidence)+'</blockquote>').join('')+
   (context.personal_evidence||[]).filter(e=>item.personal_evidence_ids.includes(e.id)).map(e=>'<p>'+esc(e.description)+'</p>').join(''):'')+
  (item.original_assessments||item.assessments||[]).map(a=>'<p>'+esc(assessmentLabels[a.judgment])+'：'+esc(a.explanation)+'；下一步 '+esc(a.next_action)+'</p>').join('')+'</details>'+
  (item.kind==='learning_task'&&item.review_status!=='rejected'?'<button class="btn btn-small" data-dossier-adopt="'+item.id+'" '+(item.adopted_task_id?'disabled':'')+'>'+(item.adopted_task_id?'已采纳为任务':'采纳整条准备建议')+'</button>':'')+'</section>';
 const current=items.filter(i=>(i.review_status||'current')==='current'),old=items.filter(i=>i.review_status&&i.review_status!=='current');
 return current.map(card).join('')+(old.length?'<details class="historical-findings"><summary>资料有更新与不采用的分析（'+old.length+'）</summary>'+old.map(card).join('')+'</details>':'');
}
function openFindingReview(id){
 const item=state.analysisFindings.find(f=>f.id===id)||activeJobDossier?.dossier?.findings.find(f=>f.id===id);if(!item||!canLeaveModal())return;
 const fields=item.assessments.map((a,index)=>{const requirement=findingRequirement(item,a.requirement_id);return '<details class="assessment-editor" data-assessment-id="'+a.requirement_id+'" '+(!index?'open':'')+'><summary>'+esc(requirement?.name||'原要求已移除')+'</summary>'+(requirement?'<blockquote>'+esc(requirement.evidence)+'</blockquote>':'')+
  '<label class="field">判断状态<select data-judgment>'+opt(Object.entries(assessmentLabels),a.judgment)+'</select></label>'+
  '<label class="field">判断说明<textarea data-explanation maxlength="2000" required>'+esc(a.explanation)+'</textarea></label>'+
  '<label class="field">下一步行动<textarea data-next-action maxlength="1000" required>'+esc(a.next_action)+'</textarea></label>'+
  '<label class="field">本次判断引用的个人经历（可多选）<select multiple data-evidence-ids size="4">'+state.evidence.map(e=>'<option value="'+e.id+'" '+(a.personal_evidence_ids.includes(e.id)?'selected':'')+'>'+esc([e.skill,e.description.slice(0,80)].filter(Boolean).join(' · ')||'个人经历')+'</option>').join('')+'</select></label></details>';}).join('');
 workspaceModal('调整分析建议','<h3>'+esc(item.title)+'</h3><p>'+esc(item.content)+'</p>'+findingJobLinks(item)+'<form id="workspace-finding-form" data-id="'+id+'" data-revision="'+item.revision+'">'+
  selectField('state','这条结论的使用状态',reviewLabels)+'<label class="field">取舍或修订说明<textarea name="note" maxlength="4000">'+esc(item.review_note||'')+'</textarea></label>'+
  '<p class="tip">修订单独保存，原始 AI 判断和历史记录保留。后续资料有变化时会显示更新提示，不影响继续使用。</p>'+fields+actions()+'</form>'+
  '<button class="btn btn-small" data-finding-history="'+id+'">查看建议调整记录</button>',true);
 $('#workspace-finding-form [name=state]').value=item.review_state==='rejected'?'rejected':'current';
}
async function openMaterialCreator(materialId=null,versionId=null){
 if(!canLeaveModal())return;
 const original=versionId?(state.materials||[]).find(m=>m.id===versionId):(state.materials||[]).find(m=>m.material_id===materialId);
 workspaceModal('保存实际材料版本','<form id="workspace-material-form" data-parent="'+(materialId||'')+'" data-copy="'+(original?.id||'')+'"><p class="tip">文字与附件一起保存。每次保存产生独立版本，投递绑定的旧版本保持不变。</p>'+
  '<div class="field-grid">'+field('name','材料名称 *','例如 产品运营简历','text',true)+selectField('kind','材料类型',{resume:'简历',project:'项目说明',other:'其他材料'})+'</div>'+
  '<label class="field">文本快照<textarea name="text" rows="8" maxlength="50000"></textarea></label>'+
  (original?.filename?'<label class="field-inline"><input type="checkbox" name="keep_attachment" checked>沿用附件 '+esc(original.filename)+'（选择新文件可替换）</label>':'')+
  '<label class="field">附件（可选，可以同时填写文字，最多 8 MB）<input type="file" name="attachment" accept=".pdf,.docx,.txt,.md"></label>'+actions()+'</form>',true);
 const form=$('#workspace-material-form');
 if(!original)return;
 form.inert=true;form.querySelector('[type=submit]').disabled=true;
 try{
  const current=await api('/materials/versions/'+original.id);
  if(!form.isConnected||!$('#modal').open)return;
  for(const key of ['name','kind'])form.elements[key].value=current[key];
  form.elements.text.value=current.text_snapshot;
  form.querySelector('[type=submit]').disabled=false;
  modalInitialValues=modalValues();
 }catch(error){if(form.isConnected)showFormError(form,error);}
 finally{if(form.isConnected)form.inert=false;}
}

async function openMaterial(id){
 if(!canLeaveModal())return;
 const m=await api('/materials/versions/'+id);
 workspaceModal('材料版本','<h3>'+esc(m.title)+'</h3><p class="tip">保存于 '+esc(m.created_at)+' · '+esc(m.filename||'文本快照')+'</p>'+
  '<pre class="material-snapshot">'+esc(m.text_snapshot||'此版本保存了原始附件，可直接下载。')+'</pre>'+
  '<a class="btn btn-primary" href="/api/materials/versions/'+id+'/download" download>下载此版本</a>',true);
}
function openMaterialImport(){
 if(!canLeaveModal())return;
 workspaceModal('导入个人经历','<form id="workspace-import-direct"><p class="tip">粘贴你的简历、项目说明或能力描述，直接保存。标签可以留空，之后随时编辑。</p>'+
  field('name','材料名称','例如 个人经历初稿')+field('skills','能力标签（选填，用逗号分隔）','例如 Excel, 活动运营')+
  '<label class="field">个人经历或能力描述<textarea name="text" rows="10" required maxlength="50000"></textarea></label>'+actions()+'</form>',true);
 $('#workspace-import-direct [type=submit]').textContent='保存经历';
}


document.addEventListener('change',event=>{
 if(event.target.closest('.assessment-editor'))event.target.closest('form').dataset.judgmentDirty='1';
 if(event.target.id==='complete-register-evidence'){
  const area=$('#completion-evidence');area.disabled=!event.target.checked;area.hidden=!event.target.checked;
 }
 if(event.target.matches('[data-candidate-pick]'))$('[data-candidate-fields="'+event.target.dataset.candidatePick+'"]').disabled=!event.target.checked;
});
document.addEventListener('input',event=>{
 if(event.target.closest('.assessment-editor'))event.target.closest('form').dataset.judgmentDirty='1';
});
document.addEventListener('click',async event=>{
 const b=event.target.closest('[data-personal-pane],[data-evidence-edit],[data-evidence-history],[data-prep-open],[data-prep-complete],[data-finding-review],[data-finding-history],[data-material-create],[data-material-open],[data-material-import],[data-finding-action]');
 if(!b)return;
 try{
  if(b.hasAttribute('data-personal-pane')){personalPane=b.dataset.personalPane;render();}
  else if(b.hasAttribute('data-evidence-edit'))openEvidenceEditor(Number(b.dataset.evidenceEdit));
  else if(b.hasAttribute('data-prep-open'))openPreparationTask(Number(b.dataset.prepOpen));
  else if(b.hasAttribute('data-prep-complete'))openTaskCompletion(Number(b.dataset.prepComplete));
  else if(b.hasAttribute('data-finding-review'))openFindingReview(Number(b.dataset.findingReview));
  else if(b.hasAttribute('data-material-create'))await openMaterialCreator(Number(b.dataset.materialCreate)||null,Number(b.dataset.materialVersion)||null);
  else if(b.hasAttribute('data-material-open'))await openMaterial(Number(b.dataset.materialOpen));
  else if(b.hasAttribute('data-material-import'))openMaterialImport();
  else if(b.hasAttribute('data-finding-action')){
   if(b.disabled||!canLeaveModal())return;
   const origin=b.closest('.modal-body-content'),values=modalValues(),job=Number($('#job-update-form')?.dataset.job);
   const originalText=b.textContent;let saved=false;
   b.disabled=true;
   try{await api('/analysis/findings/'+b.dataset.findingAction+'/assessments/'+b.dataset.requirement+'/task',{method:'POST'});saved=true;await refreshAfterWrite('已加入准备任务');
    if(job&&$('#modal').open&&$('#modal-body').firstElementChild===origin&&modalValues()===values){await jobDetail(job);showDossierTab('tasks');}}
   finally{if(b.isConnected){b.disabled=saved;b.textContent=saved?'已加入准备任务':originalText;}}
  }else{
   if(!canLeaveModal())return;
   const evidence=b.hasAttribute('data-evidence-history');
   const history=await api(evidence?'/evidence/'+b.dataset.evidenceHistory+'/history':'/analysis/findings/'+b.dataset.findingHistory+'/history');
   workspaceModal(evidence?'经历修改记录':'分析建议调整记录',history.length?history.map(row=>'<section class="dossier-item"><strong>版本 '+row.revision+' · '+esc(row.created_at)+'</strong><p class="preserve-lines">'+esc(evidence?row.snapshot.description:(reviewLabels[row.state]+' · '+row.note))+'</p></section>').join(''):'<p class="tip">还没有修改记录。</p>',true);
  }
 }catch(error){toast(error.message);}
});
document.addEventListener('submit',async event=>{
 const form=event.target;
 if(!form.id.startsWith('workspace-'))return;
 event.preventDefault();
 if(!beginFormWrite(form))return;
 try{
  const data=formdata(form);let savedExperiences=null;
  if(form.id==='workspace-import-direct'){
   const result=await api('/materials/candidates/preview',{method:'POST',body:JSON.stringify({text:data.text})});
   const skills=evidenceSkills(data.skills),tag=skills[0]||'';
   const candidates=result.candidates.map(item=>({...item,skill:tag,skills,review_status:'documented'}));
   form.dataset.importId||=crypto.randomUUID();
   savedExperiences=await api('/materials/import-evidence',{method:'POST',body:JSON.stringify({name:data.name.trim()||'个人经历',text:data.text,import_id:form.dataset.importId,candidates})});
  }
  if(form.id==='workspace-import-direct'){
   // Saved above in one step.
  }else if(form.id==='workspace-evidence-form'){
   const payload=evidencePayload(form),id=Number(form.dataset.id);
   if(id)payload.expected_revision=Number(form.dataset.revision);
   await api('/evidence'+(id?'/'+id:''),{method:id?'PATCH':'POST',body:JSON.stringify(payload)});
  }else if(form.id==='workspace-finding-form'){
   const assessments=[...form.querySelectorAll('[data-assessment-id]')].map(el=>({requirement_id:Number(el.dataset.assessmentId),
    judgment:el.querySelector('[data-judgment]').value,explanation:el.querySelector('[data-explanation]').value,next_action:el.querySelector('[data-next-action]').value,
    personal_evidence_ids:[...el.querySelector('[data-evidence-ids]').selectedOptions].map(o=>Number(o.value))}));
   const payload={expected_revision:Number(form.dataset.revision),state:data.state,note:data.note};
   if(data.state==='current'||form.dataset.judgmentDirty==='1')payload.assessments=assessments;
   await api('/analysis/findings/'+form.dataset.id+'/review',{method:'POST',body:JSON.stringify(payload)});
  }else if(form.id==='workspace-complete-form'){
   await api('/learning/'+form.dataset.id+'/complete',{method:'POST',body:JSON.stringify({completion_id:form.dataset.completion,expected_revision:Number(form.dataset.revision),
    evidence:$('#complete-register-evidence').checked?evidencePayload(form):null})});
  }else if(form.id==='workspace-task-form'){
   await api('/learning/'+form.dataset.id,{method:'PATCH',body:JSON.stringify({title:data.title,status:data.status,notes:data.notes})});
  }else if(form.id==='workspace-material-form'){
   const file=form.elements.attachment.files[0],material_id=Number(form.dataset.parent)||null;
   if(file){
    if(file.size>8*1024*1024)throw new Error('材料文件最多 8 MB');
    await api('/materials/versions/attachment',{method:'POST',body:JSON.stringify({name:data.name,kind:data.kind,text:data.text,material_id,filename:file.name,content_base64:await materialFileBase64(file)})});
   }else await api('/materials/versions',{method:'POST',body:JSON.stringify({name:data.name,kind:data.kind,text:data.text,material_id,copy_attachment_from:form.elements.keep_attachment?.checked?Number(form.dataset.copy):null})});
  }
  if(savedExperiences)personalPane='evidence';
  await refreshAfterWrite(savedExperiences?'已保存 '+savedExperiences.evidence_ids.length+' 段经历':'已保存到本地，原始资料与引用保留');
  if(form.isConnected)await closeModal(true);
  if(savedExperiences)toast('已保存 '+savedExperiences.evidence_ids.length+' 段经历',{label:'查看我的经历',run:showSavedExperiences});
 }catch(error){showFormError(form,error);}
 finally{endFormWrite(form);}
});

document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-install-examples]');if(!button||button.disabled||!canLeaveModal())return;
 button.disabled=true;
 try{const result=await api('/examples/install',{method:'POST'});await load();await jobDetail(result.job_ids[0]);showDossierTab('analysis');toast('已加入三个明确标注的示例岗位');}
 catch(error){toast(error.message);if(button.isConnected)button.disabled=false;}
});

function exampleNotice(){
 const info=state.summary?.examples;if(!info?.active)return '';
 return '<aside class="notice example-notice"><span>正在体验示例'+(info.jobs?' · '+info.jobs+' 个示例岗位':'')+'。你的新增内容会保留。</span><button class="btn btn-small" data-exit-examples>结束体验并移除示例</button></aside>';
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-exit-examples]');if(!button||button.disabled||!canLeaveModal())return;
 button.disabled=true;const content=document.querySelector('#content');content.inert=true;
 try{await api('/examples/remove',{method:'POST'});await load();toast('示例已移除，你自己新增的内容已保留');}
 catch(error){toast('未能结束体验：'+error.message);if(button.isConnected)button.disabled=false;}
 finally{content.inert=false;}
});
