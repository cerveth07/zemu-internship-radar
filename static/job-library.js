// 来源平台显示为中文名；未知值原样显示。
const SOURCE_PLATFORM_NAMES = {manual:'手动录入', boss:'BOSS 直聘', shixiseng:'实习僧', company:'企业招聘页面', other:'其他来源', import:'文件导入', gpt:'AI 导入'};
function sourcePlatformName(value){return SOURCE_PLATFORM_NAMES[value]||value||'打开来源'}
/* Job reading, application entry and the add-job form. Loaded before app.js. */
function applicationsPage(){
 const today=actionDay();
 const words=applicationFilters.q.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
 const rows=state.applications.filter(row=>(!applicationDueOnly||isDueFollowup(row,today))&&
  words.every(word=>(row.title+' '+row.company).toLocaleLowerCase().includes(word))&&
  (!applicationFilters.stage||row.stage===applicationFilters.stage)&&
  (!applicationFilters.followup||(applicationFilters.followup==='unscheduled'?isUnscheduledApplication(row):row.follow_up_on>today)));
 const unplanned=state.applications.filter(isUnscheduledApplication).length;
 rows.sort((a,b)=>(a.follow_up_on||'9999').localeCompare(b.follow_up_on||'9999')||b.job_id-a.job_id);
 return head('投递追踪','记录实际投递与下次跟进日期，安排下一步行动。','<button class="btn btn-primary" data-action="choose-apply-job">记录一次投递</button>')+
  '<form id="application-search-form" class="filter-bar"><input class="search" name="q" type="search" aria-label="搜索投递岗位或企业" placeholder="搜索岗位或企业" value="'+esc(applicationFilters.q)+'"><button class="btn" type="submit">搜索</button>'+
  '<select id="application-stage-filter" class="select" aria-label="投递阶段">'+opt([['','全部阶段'],...Object.entries(stages)],applicationFilters.stage)+'</select>'+
  '<select id="application-followup-filter" class="select" aria-label="跟进安排">'+opt([['','全部跟进安排'],['unscheduled','未安排跟进'],['future','已安排未来跟进']],applicationFilters.followup)+'</select></form>'+
  (unplanned?'<p class="notice">进行中的投递有 '+unplanned+' 条未安排跟进日期。<button class="btn btn-small" data-unplanned-applications>查看并安排</button></p>':'')+
  '<div class="filter-bar"><label class="filter-check"><input type="checkbox" id="application-due-only"'+
  (applicationDueOnly?' checked':'')+'> 只看今天及逾期跟进</label><span class="tip">按下次跟进日期排序 · '+rows.length+' 个岗位</span></div>'+
  (rows.length?'<div class="table-card"><table class="table"><thead><tr><th>岗位</th><th>阶段</th><th>投递日期</th><th>下次跟进</th><th>投递材料</th><th>下一步</th></tr></thead><tbody>'+
   rows.map(a=>'<tr><td><button class="table-link" data-job="'+a.job_id+'">'+esc(a.title)+'</button><span class="company-small">'+esc(a.company)+
    '</span></td><td><span class="pill '+esc(a.stage)+'">'+esc(stages[a.stage]||a.stage)+'</span></td>'+
    '<td>'+fmt(a.applied_on)+'</td><td>'+fmt(a.follow_up_on)+
    (isDueFollowup(a,today)?'<span class="pill uncertain">'+(a.follow_up_on<today?'已逾期':'今天')+'</span>':'')+
    '</td><td>'+applicationMaterialCell(a)+'</td><td>'+fmt(a.next_action)+
    '<div><button type="button" class="btn btn-small" data-followup-job="'+a.job_id+'">更新跟进</button></div></td></tr>').join('')+
   '</tbody></table></div>':
   (state.applications.length?empty('没有符合筛选的记录','可调整筛选条件，或查看全部投递记录。','<button type="button" class="btn btn-primary" data-clear-application-filters>清除筛选</button>'):
    empty('暂未记录申请进度','选择已经投递的岗位，记录阶段、实际材料版本和下一步行动。','<button class="btn btn-primary" data-action="choose-apply-job">选择岗位，记录进度</button>')));
}
function isUnscheduledApplication(row){return !['not_applied','rejected','withdrawn'].includes(row.stage)&&!row.follow_up_on;}
function applicationMaterialCell(row){
 const version=(state.materials||[]).find(m=>m.id===row.material_version_id);
 return (row.material_version_id?'<button class="table-link" data-material-open="'+row.material_version_id+'">'+esc(version?.title||'查看已绑定的材料版本')+'</button>':'<span class="muted">未绑定材料</span>')+
  (row.resume_version?'<small class="company-small">备注：'+esc(row.resume_version)+'</small>':'');
}

let applicationDueOnly=false;
let applicationFilters={q:'',stage:'',followup:''};
function actionDay(){
 const now=new Date();
 return [now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
}
function isDueFollowup(row,today=actionDay()){
 return Boolean(row.follow_up_on&&row.follow_up_on<=today&&!['rejected','withdrawn'].includes(row.stage));
}
function todayFollowups(summary){
 const rows=summary.due_followups||[];
 if(!rows.length)return '';
 return '<section class="workbench-followups"><div class="page-toolbar"><h2 class="panel-title">今日待办</h2>'+
  '<button type="button" class="btn btn-small" data-due-followups>查看全部到期跟进（'+Number(summary.due_followup_count||rows.length)+'）</button></div>'+
  rows.slice(0,3).map(row=>'<div class="rowline"><div><strong>'+esc(row.title)+'</strong><small>'+
   esc(row.company)+' · '+esc(row.follow_up_on)+' · '+esc(row.next_action||'更新投递进度和下一次行动')+
   '</small></div><button type="button" class="btn btn-small" data-followup-job="'+row.job_id+'">更新跟进</button></div>').join('')+'</section>';
}
if(typeof document!=='undefined'){
 document.addEventListener('change',event=>{
  if(['application-stage-filter','application-followup-filter'].includes(event.target.id)){
   applicationFilters[event.target.id==='application-stage-filter'?'stage':'followup']=event.target.value;
   if(event.target.id==='application-followup-filter')applicationDueOnly=false;render();return;
  }
  if(event.target.id!=='application-due-only')return;
  applicationDueOnly=event.target.checked;if(applicationDueOnly)applicationFilters.followup='';
  render();
  document.getElementById('application-due-only')?.focus();
 });
 document.addEventListener('click',async event=>{
  if(event.target.closest('[data-clear-application-filters]')){applicationDueOnly=false;applicationFilters={q:'',stage:'',followup:''};render();return;}
  if(event.target.closest('[data-unplanned-applications]')){applicationDueOnly=false;applicationFilters={q:'',stage:'',followup:'unscheduled'};render();return;}
  if(event.target.closest('[data-due-followups]')){
   applicationFilters={q:'',stage:'',followup:''};
   applicationDueOnly=true;
   navigate('applications');
   return;
  }
  const button=event.target.closest('[data-followup-job]');
  if(!button||!canLeaveModal())return;
  try{
   await jobDetail(Number(button.dataset.followupJob));
   openApplicationForm();
  }catch(error){toast(error.message);}
 });
}

function showJobEdit(){
 const read=$('#job-read-view'),editor=$('#job-edit-area');
 if(read&&editor){read.hidden=true;editor.hidden=false;editor.querySelector('input,textarea,select')?.focus({preventScroll:true})}
}
// 阅读页直接进入投递记录，避免先去岗位库找编辑入口。
function openApplicationForm(){
 showJobEdit();
 const tab=document.querySelector('[data-detail-tab="application"]');
 if(tab)setDetailTab(tab);
 $('#job-update-form [name="stage"]')?.focus({preventScroll:true});
}
function chooseApplyJob(){
 modal('选择岗位，记录投递进度',
  '<p class="notice">选择已经联系或投递的岗位，记录实际进度。</p>'+
  '<label class="field">搜索岗位或企业<input id="apply-job-search" type="search" data-modal-transient placeholder="输入岗位名或企业名"></label>'+
  '<div id="apply-job-results" class="row-list scroll-list-md"></div>',true);
 renderApplyJobResults();
 $('#apply-job-search').focus();
}
function renderApplyJobResults(query=''){
 const words=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
 const matches=state.jobs.filter(j=>words.every(word=>(j.title+' '+j.company).toLocaleLowerCase().includes(word)));
 $('#apply-job-results').innerHTML='<p class="tip" role="status">共 '+matches.length+' 个匹配岗位'+
  (matches.length>25?'，先显示 25 个；输入更具体的关键词查找。':'。')+'</p>'+
  (matches.length?matches.slice(0,25).map(j=>
   '<div class="rowline"><div><strong>'+esc(j.title)+'</strong><small>'+esc(j.company)+' · 当前：'+
   esc(stages[j.application_stage]||'未申请')+'</small></div><button type="button" class="btn btn-small" data-apply-job="'+j.id+'">记录投递</button></div>'
  ).join(''):'<p class="tip">'+(state.jobs.length?'没有匹配的岗位，试试企业名或更短的关键词。':'岗位库还没有记录，请先添加岗位或从采集收件箱核对入库。')+'</p>');
}
// Only write fields the user actually changed; unrelated edits in another window survive.
function jobUpdatePayload(form,data){
 const initial=JSON.parse(form.dataset.initial),payload={},expected={};
 for(const key of ['title','direction','city','salary','duration','onsite_days','team','eligibility','posted_at','deadline','status','priority','jd_text','notes']){
  if(data[key]!==initial[key]){payload[key]=data[key];expected[key]=initial[key]}
 }
 if(Object.keys(expected).length)payload.expected_fields=expected;
 const application={stage:data.stage,resume_version:data.resume_version,recruiter:data.recruiter,last_contact:data.last_contact,next_action:data.next_action,notes:data.application_notes};
 for(const field of ['applied_on','follow_up_on'])if(Object.hasOwn(data,field))application[field]=data[field];
 if(Object.hasOwn(data,'material_version_id'))application.material_version_id=data.material_version_id?Number(data.material_version_id):null;
 if(JSON.stringify(application)!==form.dataset.application){
  payload.application=application;payload.expected_application=JSON.parse(form.dataset.application);
 }
 return payload;
}
function setDetailTab(tab){
 document.querySelectorAll('[data-detail-tab]').forEach(el=>{const active=el===tab;el.setAttribute('aria-selected',String(active));el.tabIndex=active?0:-1});
 document.querySelectorAll('[data-detail-panel]').forEach(el=>{el.hidden=el.dataset.detailPanel!==tab.dataset.detailTab});
 $('#job-update-form').hidden=tab.dataset.detailTab==='sources';
}
function addJob(){if(!state.loaded){toast('请先重新加载本地数据');return}if(!state.companies.length){toast('请先添加企业');state.page='companies';render();addCompany();return}const companyOptions=[['','请选择企业'],...state.companies.map(c=>[String(c.id),c.name])];modal('录入岗位',`<form id="job-form"><div class="field-grid">${selectField('company_id','关联企业 *',companyOptions,'',true)}${field('title','岗位名称 *','产品运营实习生','text',true)}${directionField()}${field('city','工作城市','北京 / 远程')}${field('salary','薪资原文','例如 200–300 元/天')}${field('duration','实习时长','3 个月起')}${field('onsite_days','每周到岗','每周 4 天')}${field('team','所属部门 / 团队','例如 用户增长团队')}${field('eligibility','资格条件原文','毕业届别、学历等')}${field('posted_at','发布日','','date')}${field('deadline','截止日','','date')}${selectField('priority','关注优先级',priorities)}${selectField('status','岗位核验状态',statuses)}${field('source_platform','来源平台','BOSS / 实习僧 / 官网')}${field('source_url','来源链接','https://')}</div><p class="tip">企业默认不预选，方向默认“待判断 / 其他”：先确认岗位属于哪家公司，避免把资料挂到错误企业。</p><label class="field">完整 JD / 原始说明<textarea name="jd_text" rows="7" placeholder="粘贴岗位原文；缺失内容不自动补写"></textarea></label><div class="mt-4">${field('skill_names','人工技能标签（英文逗号分隔）','Excel, SQL, 用户研究')}</div><div class="mt-4"><label class="field">个人备注<textarea name="notes" placeholder="对岗位的观察、待核验事项"></textarea></label></div>${actions()}</form>`,true)}
let jobDetailRevision=0;
async function jobDetail(id){
 if(typeof clearWorkspaceReturn==='function')clearWorkspaceReturn();
 const revision=++jobDetailRevision;
 const j=await api('/jobs/'+id);
 if(revision!==jobDetailRevision)return;
 const a=j.application||{};
 const neighbors=state.page==='jobs'?selectedJobs():state.jobs;
 const position=neighbors.findIndex(item=>item.id===j.id);
 const previous=position>0?neighbors[position-1].id:null;
 const following=position>=0&&position+1<neighbors.length?neighbors[position+1].id:null;
 const latestRating=j.history.find(x=>x.event==='priority_assessed');
 let priorityReason='';
 if(latestRating){try{const rated=JSON.parse(latestRating.payload_json);if(rated.after===j.priority)priorityReason=rated.reason||''}catch{}}

 const issues=typeof jobIssues==='function'?jobIssues(j.id):[];
 const sources=j.sources.map(s=>`<div class="rowline"><div><strong>${esc(sourcePlatformName(s.platform))}</strong><small>${s.url?`<a href="${esc(secureLink(s.url))}" rel="noopener noreferrer" target="_blank">${esc(s.url)}</a>`:'未记录链接'}</small></div><div class="source-status"><span class="pill">${esc({unverified:'未核验',verified:'已核验',unavailable:'不可访问'}[s.verification]||s.verification)}</span><button type="button" class="btn btn-small" data-sourcecheck="${s.id}" data-sourcejob="${j.id}" data-current="${s.verification}">核验来源</button></div></div>`).join('');
 const tabs=[['job','岗位资料'],['sources','来源与技能'],['application','投递记录']].map(([key,label],i)=>`<button type="button" class="detail-tab" role="tab" id="tab-${key}" aria-controls="detail-${key}" aria-selected="${i===0}" tabindex="${i===0?0:-1}" data-detail-tab="${key}">${label}</button>`).join('');
 modal('岗位档案',`<h2 class="detail-title">${esc(j.title)}</h2><div class="detail-meta">${esc(j.company.name)} · ${esc(directions[j.direction]||j.direction)} · ${esc(j.city||'地点未填写')} <span class="pill ${esc(j.priority)}">${esc(priorities[j.priority])}</span></div><div class="info-grid"><div class="info-item"><small>薪资原文</small><strong>${fmt(j.salary)}</strong></div><div class="info-item"><small>实习时长 / 到岗</small><strong>${fmt([j.duration,j.onsite_days].filter(Boolean).join(' · '))}</strong></div><div class="info-item"><small>截止日期</small><strong>${j.deadline?esc(j.deadline):'未注明'}</strong> ${deadlinePill(j.deadline)}</div><div class="info-item"><small>发布日期</small><strong>${fmt(j.posted_at)}</strong></div></div>
 ${priorityReason?`<p class="tip"><strong>最近一次 AI 优先级理由：</strong>${esc(priorityReason)}</p>`:``}<section id="job-read-view" class="job-reading">
  <div class="page-toolbar job-reading-actions">
   <span class="job-reading-nav" role="group" aria-label="切换岗位"><button type="button" class="btn btn-small" data-job-reading-to="${previous??''}" ${previous?'':'disabled'} aria-label="上一岗位" title="上一岗位">‹ 上一个</button><button type="button" class="btn btn-small" data-job-reading-to="${following??''}" ${following?'':'disabled'} aria-label="下一岗位" title="下一岗位">下一个 ›</button></span>
   <button type="button" class="btn btn-primary btn-small" data-job-reading-apply>记录投递</button>
   <button type="button" class="btn btn-small" data-job-reading-edit>编辑岗位资料 / 投递记录</button>
   <span class="job-reading-secondary">${j.sources.some(s=>/^https:\/\/(?:www\.)?zhipin\.com\/job_detail\//.test(s.url||''))?`<button type="button" class="btn btn-small btn-ghost" data-boss-favorite="job" data-job="${j.id}">收藏到 BOSS</button>`:''}
   ${j.status==='verified'?'':`<button type="button" class="btn btn-small btn-ghost" data-job-verify="${j.id}" data-current="${esc(j.status)}" title="确认岗位信息与原来源一致后标记；不代表岗位仍在招聘">标记为已核验</button>`}</span>
  </div>
  ${issues.length?`<div class="job-issues" role="note"><strong>待补充：</strong>${issues.map(k=>`<span class="tag">${esc((typeof issueNames==='object'&&issueNames[k])||k)}</span>`).join('')}<span class="tip">在“编辑岗位资料 / 投递记录”中补充</span></div>`:''}
  <div class="job-read-meta"><strong>方向：</strong>${esc(directions[j.direction]||j.direction)} ·
   <strong>状态：</strong>${esc(statuses[j.status]||j.status)} · <strong>部门：</strong>${fmt(j.team)}</div>
  ${deadlineStatus(j.deadline).tone==='overdue'?`<p class="notice job-deadline-notice" role="status">截止日期 ${esc(j.deadline)} 已过 · 请在原来源核实是否仍在招聘，再决定是否投递。</p>`:''}
  ${j.eligibility?'<div class="job-read-eligibility"><strong>实习资格：</strong>'+esc(j.eligibility)+'</div>':''}
  <div class="dossier-application"><strong>投递进度：</strong>${esc(stages[a.stage]||'未申请')}
   ${a.applied_on?' · 投递于 '+esc(a.applied_on):''}${a.follow_up_on?' · 下次跟进 '+esc(a.follow_up_on):''}
   ${a.next_action?'<p><strong>下一步：</strong>'+esc(a.next_action)+'</p>':''}${a.material_version_id?'<button class="btn btn-small" data-material-open="'+a.material_version_id+'">查看投递时的材料版本</button>':''}</div>
  ${jobDossierTabs()}
  <section id="dossier-original" role="tabpanel" aria-labelledby="dossier-tab-original" data-dossier-panel="original">
  <h3 class="detail-label">完整岗位说明</h3>
  <div class="jd-box job-read-jd">${esc(j.jd_text||'尚未保存 JD 原文')}</div>
  ${j.notes?'<p class="tip"><strong>个人备注：</strong>'+esc(j.notes)+'</p>':''}
  ${companyFactSummary(j.company,true)}
  <div class="job-read-sources"><strong>招聘来源：</strong>${j.sources.filter(x=>x.url).map(x=>
   '<a href="'+esc(secureLink(x.url))+'" target="_blank" rel="noopener noreferrer">'+esc(sourcePlatformName(x.platform))+' ↗</a>').join(' · ')||'未记录来源链接'}</div>
 </section>${jobDossierPanels(j)}
 </section><div id="job-edit-area" hidden>
 <div class="page-toolbar job-reading-actions"><button type="button" class="btn btn-small" data-job-reading-back>返回阅读</button></div>
 <div class="detail-tabs" role="tablist" aria-label="岗位档案内容">${tabs}</div>
 <section id="detail-sources" role="tabpanel" aria-labelledby="tab-sources" data-detail-panel="sources" hidden><div class="detail-label">招聘来源</div>${sources||'<p class="tip">尚无来源。</p>'}<form id="source-form" data-job="${j.id}"><div class="field-grid">${field('platform','来源平台','BOSS / 实习僧 / 企业官网','text',true)}${field('url','职位原始链接','https://')}</div><div class="form-actions"><button class="btn btn-small" type="submit">添加来源</button></div><div id="form-error" class="error"></div></form><div class="detail-label">岗位技能标签</div><div class="page-toolbar"><button type="button" class="btn btn-small" data-skills="${j.id}">核对 JD 技能</button>${j.skills.length?j.skills.map(x=>`<span class="tag">${esc(x.name)}</span>`).join(''):'<span class="tip">尚未标注</span>'}</div><p class="tip">来源可访问不等于职位仍开放，请分别核对。</p></section>
 <form id="job-update-form" data-job="${j.id}"><section id="detail-job" role="tabpanel" aria-labelledby="tab-job" data-detail-panel="job"><label class="field">完整 JD（修改会保留历史记录）<textarea name="jd_text" rows="7">${esc(j.jd_text||'')}</textarea></label><div class="field-grid mt-5">${field('title','岗位名称 *','','text',true)}${directionField(j.direction)}${field('city','城市')}${field('salary','薪资原文')}${field('duration','实习时长')}${field('onsite_days','每周到岗')}${field('team','所属部门 / 团队')}${field('eligibility','资格条件原文')}${field('posted_at','发布日期','','date')}${field('deadline','截止日期','','date')}${selectField('status','核验状态',statuses)}${selectField('priority','关注优先级',priorities)}</div><p class="tip">创建于 ${esc(j.created_at)} · ${j.history.length} 条历史记录</p></section>
 <section id="detail-application" role="tabpanel" aria-labelledby="tab-application" data-detail-panel="application" hidden><div class="field-grid">${selectField('stage','投递阶段',stages)}${field('applied_on','投递日期','','date')}${field('follow_up_on','下次跟进日期','','date')}${field('resume_version','材料版本备注','例如 运营简历 v1')}${materialOptions(a.material_version_id)}${field('recruiter','招聘负责人','可留空')}${field('last_contact','上次联系记录','例如 2026-10-08')}${field('next_action','下一步行动','例如准备项目作品')}</div><label class="field">沟通备注<textarea name="application_notes">${esc(a.notes||'')}</textarea></label><label class="field">个人岗位备注<textarea name="notes">${esc(j.notes||'')}</textarea></label><p class="tip">记录你实际完成的联系、投递和面试进度。</p></section>${actions()}</form></div>`,true);
 for(const key of ['title','direction','city','salary','duration','onsite_days','team','eligibility','posted_at','deadline']){$('#job-update-form [name='+key+']').value=j[key]??''}
 for(const key of ['status','priority'])$('#job-update-form [name='+key+']').value=j[key];
 for(const key of ['stage','resume_version','recruiter','last_contact','next_action','applied_on','follow_up_on'])$('#job-update-form [name='+key+']').value=a[key]||(key==='stage'?'not_applied':'');
 $('#job-update-form').dataset.application=JSON.stringify({stage:a.stage||'not_applied',resume_version:a.resume_version||'',recruiter:a.recruiter||'',last_contact:a.last_contact||'',next_action:a.next_action||'',notes:a.notes||'',applied_on:a.applied_on||'',follow_up_on:a.follow_up_on||'',material_version_id:a.material_version_id||null});
 $('#job-update-form').dataset.initial=JSON.stringify(formdata($('#job-update-form')));
}

// 一键核验：只把状态改为“已核验”，并带上原状态防止覆盖其他窗口的修改。
if(typeof document!=='undefined')document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-job-verify]');if(!button||button.disabled)return;
 const id=Number(button.dataset.jobVerify);button.disabled=true;
 try{
  await api('/jobs/'+id,{method:'PATCH',body:JSON.stringify({status:'verified',expected_fields:{status:button.dataset.current}})});
  toast('已标记为已核验');
  try{await load()}catch(error){toast('已保存，但列表刷新失败：'+error.message)}
  await jobDetail(id);
 }catch(error){toast(error.message);button.disabled=false}
});

if(typeof document!=='undefined')document.addEventListener('submit',event=>{
 if(event.target.id!=='application-search-form')return;
 event.preventDefault();applicationFilters.q=event.target.elements.q.value;render();
});
