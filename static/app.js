const state = {page:'overview',loaded:false,loadError:'',analysisImportBusy:false, jobs:[], companies:[], skills:[], learning:[], applications:[], evidence:[], reviews:[], summary:{}, quality:{jobs_checked:0,issue_counts:{},jobs_with_issues:[]}, filters:{q:'',direction:'',status:'',priority:'',city:'',soon:'',quality:''}, jobSort:'priority', importRows:[],importPreview:null,importFileName:'',analysisPackages:[],analysisFindings:[],analysisPreview:null,analysisImportPreview:null,analysisImportRaw:'',analysisFilters:{direction:'',status:'',priority:'',city:'',job_ids:'',include_private:false,baseline_id:''},captureBatches:[],captureItems:[],captureSelected:null,companyFilters:{q:''},listPagination:{companies:{page:1,size:12}},requirementData:{items:[],total:0,status:'pending',offset:0,limit:30},requirementFilters:{status:'pending',offset:0},requirementImportRaw:'',requirementImportPreview:null,requirementBusy:false,requirementSelected:[],careerMode:'compact',careerGoal:'gap',careerEvidenceIds:[],careerIncludeAI:false,careerBusy:false,analysisFlow:'home',analysisBatch:'',workflow:{jobs:[],batches:[]},workflowBusy:false,demandData:null,demandAliases:null,demandFilters:{q:'',direction:'',priority:'',city:'',job_status:'',include_pending:false,include_ai_reviewed:false},demandLoading:false,demandError:'',demandEvidence:null};
state.companyListData={lists:[],builtin_id:null};
const names = {overview:'工作台',jobs:'岗位库',companies:'企业观察库',companyLists:'企业名单',skills:'能力差距',learning:'学习计划',applications:'投递追踪',sources:'数据与帮助',reviews:'重复审核',evidence:'我的能力与经历',analysis:'AI 分析工作台',demand:'JD 需求画像',capture:'采集收件箱',planner:'词库与计划'};
const directions = Object.assign(Object.create(null),{pm:'AI 产品',agent:'Agent / AI 应用',fde:'AI Solution / FDE',research:'研究与策略',other:'其他'});
const statuses = {candidate:'待核验',verified:'已核验',uncertain:'状态不明',closed:'已关闭'};
const stages = {not_applied:'未申请',contacted:'已联系',resume_sent:'已发简历',interview:'面试中',offer:'收到 Offer',rejected:'未通过',withdrawn:'主动放弃'};
const priorities = {unrated:'未评估',high:'重点关注',medium:'适度关注',low:'低优先级',skip:'暂不考虑'};
function directionOptions(current='',allowCustom=false){
 const values=new Set([...Object.keys(directions),...state.jobs.map(j=>j.direction),current].filter(Boolean));
 const options=[...values].map(value=>[value,allowCustom&&value==='other'?'待判断 / 其他':directions[value]||value]);
 if(allowCustom)options.push(['','＋ 自定义方向…']);
 return options;
}
function customDirectionInput(){
 return '<label class="field" data-custom-direction hidden>自定义岗位方向<input name="custom_direction" maxlength="80" placeholder="例如 审计与鉴证" disabled></label>';
}
function directionField(current='other'){
 return '<div data-direction-editor>'+selectField('direction','岗位方向',directionOptions(current,true),current)+customDirectionInput()+'</div>';
}
if(typeof document!=='undefined')document.addEventListener('change',event=>{
 const select=event.target.closest('[data-direction-editor] select');
 const label=select?.closest('[data-direction-editor]').querySelector('[data-custom-direction]');
 if(!label)return;
 const input=label.querySelector('input');
 label.hidden=select.value!=='';
 input.disabled=label.hidden;
 input.required=!label.hidden;
 if(!label.hidden)input.focus();
});
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
// Pass [[value,label],…] when order matters: object keys that look like integers (record ids) are always enumerated first.
const opt = (obj,selected='') => (Array.isArray(obj)?obj:Object.entries(obj)).map(([k,v])=>`<option value="${esc(k)}" ${String(k)===String(selected)?'selected':''}>${esc(v)}</option>`).join('');
const api = async (route, options={}) => {
 let res;try{res=await fetch('/api'+route,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}})}catch{throw new Error('无法连接本地服务，请检查服务并重新加载')}
 if(!res.ok){let body;try{body=await res.json()}catch{}
  const fields=Array.isArray(body?.detail)?body.detail.map(x=>String(x.loc?.at(-1)||'')):[];
  const detail=Array.isArray(body?.detail)?body.detail.map(x=>{
   const name=String(x.loc?.at(-1)||'');const input=document.querySelector('#modal-body [name="'+CSS.escape(name)+'"]');
   const label=input?.closest('label')?.firstChild?.textContent?.trim()||name;
   const msg=x.type==='string_too_short'?`至少填写 ${x.ctx.min_length} 个字符`:x.type==='string_too_long'?`不能超过 ${x.ctx.max_length} 个字符`:x.type==='literal_error'?'请选择有效选项':String(x.msg||'内容不符合要求').replace(/^Value error, /,'');
   return label+'：'+msg;
  }).slice(0,3).join('；'):typeof body?.detail==='string'?body.detail:'操作失败 ('+res.status+')';
  throw Object.assign(new Error(detail),{fields});
 }
 return res.json();
};
const fmt = x => x ? esc(x) : '<span class="muted">未填写</span>';
// 截止日期只做本地区间提示，不推断招聘状态；日期缺失或格式异常如实标出。
const deadlineDays = value => {
 const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value??'').trim());
 if(!match)return null;
 const [year,month,date]=[Number(match[1]),Number(match[2]),Number(match[3])];
 const target=new Date(year,month-1,date);
 // 月份/日期越界时 Date 会顺延成合法日期，这里必须逐项核对，不能把 2026-13-40 当成有效截止日。
 if(target.getFullYear()!==year||target.getMonth()!==month-1||target.getDate()!==date)return null;
 const now=new Date(),today=new Date(now.getFullYear(),now.getMonth(),now.getDate());
 return Math.round((target-today)/86400000);
};
const deadlineStatus = value => {
 const text=String(value??'').trim();
 if(!text)return {text:'未注明截止日期',tone:'none'};
 const days=deadlineDays(text);
 if(days===null)return {text:'截止日期格式待核对',tone:'none'};
 if(days<0)return {text:'已过期 '+(0-days)+' 天 · 需核实',tone:'overdue'};
 if(days===0)return {text:'今天截止',tone:'due'};
 if(days<=7)return {text:'剩 '+days+' 天 · 即将截止',tone:'due'};
 return {text:'剩 '+days+' 天',tone:'later'};
};
// 只包含今天至未来七天；历史过期岗位不挤占当前机会。
const deadlineUrgent = value => deadlineStatus(value).tone==='due';
const deadlinePill = value => {const info=deadlineStatus(value);return info.tone==='none'?'':'<span class="pill deadline-'+info.tone+'">'+esc(info.text)+'</span>'};
// 列表里也按紧急度上色：过期/临期不再和普通日期同样轻重。
const deadlineCell = value => {
 const info=deadlineStatus(value);
 const note='<span class="deadline-note deadline-'+info.tone+'">'+esc(info.text)+'</span>';
 return value?('<strong class="deadline-date">'+esc(value)+'</strong>'+note):note;
};
const toast = (text,action=null) => {
 const el=$('#toast');($('#modal').open?$('#modal'):document.body).append(el);el.textContent=text;
 if(action){const button=document.createElement('button');button.type='button';button.className='btn btn-small';button.textContent=action.label;button.addEventListener('click',action.run);el.append(' ',button);}
 el.classList.remove('hidden');clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>el.classList.add('hidden'),action?10000:2800);
};
async function load(){
 try{
  const [summary,jobs,companies,skills,learning,applications,evidence,reviews,quality,analysisPackages,analysisFindings,requirementData,workflow,materials,companyListData]=await Promise.all(['/dashboard','/jobs','/companies','/skills','/learning','/applications','/evidence','/reviews','/quality','/analysis/packages','/analysis/findings',`/requirements?status=${state.requirementFilters.status}&offset=${state.requirementFilters.offset}&limit=30${state.analysisFlow==='extract'&&state.analysisBatch?'&package_id='+encodeURIComponent(state.analysisBatch):''}`,'/workflow','/materials','/company-lists'].map(p=>api(p)));
  Object.assign(state,{summary,jobs,companies,skills,learning,applications,evidence,reviews,quality,analysisPackages,analysisFindings,requirementData,workflow,materials,companyListData});
  if(typeof refreshCapture==='function')await refreshCapture();
  state.loaded=true;state.loadError='';render();
  if(typeof resumeHomeSearch==='function')resumeHomeSearch();
  if(state.page==='skills')await refreshSkillGap();
 }catch(error){state.loadError=error.message;throw error}
}
async function reloadData(){try{await load()}catch{render()}}
async function refreshAfterWrite(message,resources=null){
 try{
  if(!resources){await load()}
  else{
   const endpoints={jobs:'/jobs',summary:'/dashboard',reviews:'/reviews',
    quality:'/quality',workflow:'/workflow',applications:'/applications',
    learning:'/learning',companies:'/companies',evidence:'/evidence'};
   const changes=await Promise.all(resources.map(async key=>[key,await api(endpoints[key])]));
   for(const [key,value] of changes)state[key]=value;
   state.loadError='';render();
   if(state.page==='skills'&&resources.includes('jobs'))await refreshSkillGap();
  }
  toast(message);
 }catch{render();toast(message+'；列表刷新失败，请重新加载。')}
}
function beginFormWrite(form){if(form.inert)return false;form.inert=true;form.querySelector('button[type=submit]')?.setAttribute('disabled','');if(form.closest('#modal'))modalBusy=form;return true}
function endFormWrite(form){form.inert=false;form.querySelector('button[type=submit]')?.removeAttribute('disabled');if(modalBusy===form)modalBusy=null}
function showFormError(form,error){
 form.inert=false;const target=form.querySelector('#form-error');if(target){target.textContent=error.message;target.setAttribute('role','alert')}else toast(error.message);
 const input=error.fields?.map(name=>form.querySelector('[name="'+CSS.escape(name)+'"]')).find(Boolean);if(input){const panel=input.closest('[data-detail-panel]');if(panel)setDetailTab(document.querySelector('[data-detail-tab="'+panel.dataset.detailPanel+'"]'));input.focus()}
}
function icon(name){return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`}
function workspaceSubnav(){
 const capture=[['capture','采集批次'],['reviews','重复审核']];
 const preparation=[['skills','能力差距'],['evidence','我的能力与经历'],['learning','学习计划']];
 const group=[capture,preparation].find(entries=>entries.some(([page])=>page===state.page));
 if(!group)return '';
 const label=group===capture?'采集收件箱':'能力准备';
 return '<nav class="workflow-nav" aria-label="'+label+'">'+group.map(([page,title])=>{
  const count=page==='reviews'&&state.reviews.length?' <span class="nav-count attention">'+state.reviews.length+'</span>':'';
  return '<button type="button" class="btn" data-workspace-tab data-page="'+page+'"'+
   (state.page===page?' aria-current="page"':'')+'>'+title+count+'</button>';
 }).join('')+'</nav>';
}
function head(title,desc,action=''){return `<div class="page-head"><div><h1>${esc(title)}</h1><p class="subtitle">${esc(desc)}</p></div>${action}</div>`+workspaceSubnav()}
function empty(title,desc,button=''){return `<div class="empty"><div class="empty-icon">${icon('inbox')}</div><h3>${esc(title)}</h3><p>${desc}</p>${button}</div>`}
function stat(label,num,foot){return `<div class="stat-card"><div class="label">${label}</div><div class="stat-number">${num??0}</div><div class="stat-bottom">${foot}</div></div>`}
function jobsRows(jobs){return `<div class="table-card"><table class="table"><thead><tr><th>岗位 / 公司</th><th>方向</th><th>城市</th><th>企业规模</th><th>优先级</th><th>核验状态</th><th>投递状态</th></tr></thead><tbody>${jobs.map(j=>`<tr><td><button class="table-link" data-job="${j.id}">${esc(j.title)}</button><span class="company-small">${esc(j.company)}</span></td><td>${esc(directions[j.direction]||j.direction)}</td><td>${fmt(j.city)}</td><td>${companyScaleSummary(state.companies.find(c=>c.id===j.company_id))}</td><td><span class="pill ${esc(j.priority)}">${esc(priorities[j.priority])}</span></td><td><span class="pill ${esc(j.status)}">${esc(statuses[j.status])}</span></td><td>${esc(stages[j.application_stage]||'未申请')}</td></tr>`).join('')}</tbody></table></div>`}
function overviewKpis(d,capturePending,pending){
 const kpi=(page,label,num,foot,attr='',hot=false)=>`<button type="button" class="kpi${hot?' kpi-hot':''}" data-page="${page}" ${attr}><span class="kpi-label">${label}</span><strong class="kpi-num">${num??0}</strong><small>${foot}</small></button>`;
 return `<div class="kpi-strip" role="group" aria-label="工作空间概况">${kpi('capture','待核对',capturePending,'采集资料等你确认','',capturePending>0)}${kpi('jobs','收录岗位',d.jobs,'完整 JD 与原始来源')}${kpi('jobs','重点关注',d.priority,'已标记优先级','data-filter-priority="high"')}${kpi('jobs','待核验岗位',pending,'确认岗位与招聘状态','data-filter-status="candidate"')}${kpi('companies','观察企业',d.companies,'持续积累企业动态')}</div>`;
}
function overview(){
 const d=state.summary, recent=d.recent||[], distribution=d.directions||[];
 const pending=state.jobs.filter(j=>j.status==='candidate').length;
 const capturePending=state.captureBatches.reduce((n,b)=>n+b.remaining,0);
 const date=new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});
 const task=(page,name,desc,count,svg,filter='')=>`<button class="task-row" data-page="${page}" ${filter}>${icon(svg)}<div><strong>${name}</strong><small>${desc}</small></div><span class="task-count">${count}</span>${icon('arrow')}</button>`;
 return head('工作台','检索岗位与查看进度',`<span class="workspace-date">${esc(date)}</span>`)+firstRunIntro()+homeSearchPanel()+overviewKpis(d,capturePending,pending)+
 todayFollowups(d)+`  <div class="grid-two overview-grid"><section class="panel"><h2 class="panel-title">最近收录 <button class="btn btn-small btn-ghost" data-page="jobs">查看岗位库 ${icon('arrow')}</button></h2>
 ${recent.length?`<div class="row-list">${recent.map(j=>`<div class="rowline"><div><button class="table-link" data-job="${j.id}">${esc(j.title)}</button><small>${esc(j.company)} · ${esc(j.city||'地点未填写')}</small></div><span class="pill ${esc(j.status)}">${esc(statuses[j.status])}</span></div>`).join('')}</div>`:
 `<div class="welcome"><h2>从第一条岗位线索开始</h2><p>把值得关注的公司与完整岗位说明留在这里，逐步建立自己的求职资料库。</p><ol class="workflow"><li><span class="step-number">1</span><div><strong>添加岗位</strong><small>粘贴完整 JD，填写企业与岗位名</small></div></li><li><span class="step-number">2</span><div><strong>判断是否值得投</strong><small>查看资格条件、标记关注优先级</small></div></li><li><span class="step-number">3</span><div><strong>记录进度</strong><small>记录已联系、已投递和下一步行动</small></div></li></ol><div class="page-toolbar"><button class="btn btn-primary" data-quick-job>${icon('plus')}粘贴第一条岗位 JD</button><button class="btn" data-page="capture">打开采集收件箱</button></div></div>`}
 </section><section class="panel"><h2 class="panel-title">待处理事项 <small>来自本地记录</small></h2><div class="task-list">
 ${task('capture','核对采集资料','原始文字、链接与截图',capturePending,'inbox')}
 ${task('jobs','核验候选岗位','确认岗位信息与招聘状态',pending,'jobs','data-filter-status="candidate"')}
 ${task('reviews','审核疑似重复','由你决定关联或独立保存',state.reviews.length,'review')}
 ${task('jobs','补充岗位资料','定位缺失字段与过期参考资料',state.quality.jobs_with_issues.length,'evidence','data-filter-quality="issues"')}
 </div></section></div>
 <section class="panel direction-panel"><h2 class="panel-title">岗位方向</h2><div class="direction-content">${distribution.length?distribution.map(x=>`<div class="skills-bar"><div class="skills-name">${esc(directions[x.direction]||x.direction)}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,100*x.count/Math.max(1,d.jobs))}%"></div></div><div class="skill-count">${x.count}</div></div>`).join(''):'<p class="subtitle">收录并标注岗位方向后，可在这里比较自己的关注分布。</p>'}</div><button class="btn btn-small" data-page="skills">查看能力差距</button></section>
 <p class="workspace-note">${icon('check')}所有记录保存在本地。采集资料须人工核对，投递进度由你更新。</p>`;
}
// “资料缺失”筛选沿用数据质量检查的结果，不在前端重新判断。
function jobIssues(id){return (state.quality?.jobs_with_issues||[]).find(x=>x.job_id===id)?.issues||[]}
function selectedJobs(){const {q,direction,status,priority,city,soon,quality}=state.filters;
 const companiesById=new Map(state.companies.map(c=>[c.id,c]));
 const issueIds=quality?new Set((state.quality?.jobs_with_issues||[]).map(x=>x.job_id)):null;return state.jobs.filter(j=>(!q||(j.title+j.company+j.jd_text).toLowerCase().includes(q.toLowerCase()))&&(!direction||j.direction===direction)&&(!status||j.status===status)&&(!priority||j.priority===priority)&&(!city||j.city===city)&&(!soon||deadlineUrgent(j.deadline))&&(!issueIds||issueIds.has(j.id))&&companyFactMatch(companiesById.get(j.company_id),state.filters)&&companyListJobMatch(j))}
function selectedCompanies(){const cf=state.companyFilters;const cq=(cf.q||'').trim().toLowerCase();return state.companies.filter(c=>(!cq||(c.name+' '+(c.sector||'')+' '+(c.notes||'')+' '+(c.city||'')).toLowerCase().includes(cq))&&companyFactMatch(c,cf))}
function jobsPage(){return jobWorkbenchPage()}
function companiesPage(){
 const cf=state.companyFilters,cs=selectedCompanies(),listing=listingPage('companies',cs),active=Boolean((cf.q||'').trim()||cf.sector||sizeFilterActive(cf));
 return head('企业观察库','查看企业行业与人数规模，保留来源并持续补充资料。','<div class="page-toolbar"><button class="btn" data-page="companyLists">企业名单</button><button class="btn btn-primary" data-action="add-company">+ 新增企业</button></div>')+
 '<div class="filter-bar"><input id="company-filter-q" class="search" type="search" aria-label="搜索企业、行业或简介" placeholder="搜索企业、行业或简介" value="'+esc(cf.q||'')+'">'+companyFactFilters(cf,'company-filter')+'</div>'+
 '<div id="company-results"><div class="results-line"><div><strong>'+(active?'筛选结果':'全部企业')+'</strong><span> · '+cs.length+' 家</span></div><div class="page-toolbar"><button class="btn btn-small" data-action="export-companies-json" '+(!cs.length?'disabled':'')+'>导出筛选全部 JSON</button>'+(active?'<button class="btn btn-small btn-ghost" data-action="clear-company-filters">清除筛选</button>':'')+'</div></div>'+
 (cs.length?'<div class="company-grid">'+listing.items.map(c=>'<div class="company-card"><h3>'+esc(c.name)+'</h3>'+companyFactSummary(c,true)+'<p>'+esc(c.notes||'尚未补充企业介绍。')+'</p><div class="small-data">'+esc(c.city||'城市待录入')+' · '+c.job_count+' 个关联岗位</div><div class="form-actions"><button class="btn btn-small" data-company-jobs="'+c.id+'">查看关联岗位（'+c.job_count+'）</button><button class="btn btn-small" data-company="'+c.id+'">编辑档案</button><button class="btn btn-small" data-observations="'+c.id+'">观察记录</button></div>'+(c.website?'<p><a href="'+esc(secureLink(c.website))+'" target="_blank" rel="noopener noreferrer">企业官网 ↗</a></p>':'')+'</div>').join('')+'</div>'+listingPager('companies',listing,'家企业'):
 empty(active?'没有匹配的企业':'企业观察库还没有记录',active?'调整人数范围或清除筛选。':'可新增关注的企业，再关联同一企业的不同岗位。',active?'<button class="btn" data-action="clear-company-filters">清除筛选</button>':'<button class="btn btn-primary" data-action="add-company">新增企业</button>'))+'</div>';
}
let demandRevision=0;
const demandTypeLabels=Object.assign(Object.create(null),directions,{fde:'FDE'});
function demandParams(){
 const f=state.demandFilters,p=new URLSearchParams();
 for(const [k,v] of Object.entries(f))if(v!==''&&v!=null)p.set(k,String(v));
 return p;
}
async function refreshDemandStats(){
 const revision=++demandRevision;
 state.demandLoading=true;state.demandError='';
 if(state.page==='demand')render();
 try{
  const [stats,aliases]=await Promise.all([api('/requirements/stats?'+demandParams()),api('/requirements/stats/aliases')]);
  if(revision!==demandRevision)return;
  state.demandData=stats;state.demandAliases=aliases;
 }catch(e){if(revision===demandRevision)state.demandError=e.message}
 finally{if(revision===demandRevision){state.demandLoading=false;if(state.page==='demand')render()}}
}
function demandComparison(title,rows,labels){
 return `<section class="panel"><h3 class="panel-title">${esc(title)}</h3><div class="table-card"><table class="table"><thead><tr><th>分类</th><th>筛选岗位</th><th>确认要求覆盖岗位</th><th>覆盖率</th><th>本组常见技能（岗位数）</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${esc(labels[x.group]||x.group)}</td><td>${x.selected_jobs}</td><td>${x.confirmed_covered_jobs}</td><td>${x.selected_jobs?(100*x.confirmed_covered_jobs/x.selected_jobs).toFixed(1)+'%':'—'}</td><td>${x.top_skills.map(t=>esc(t.skill)+' '+t.jobs).join(' / ')||'尚无'}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function demandPage(){
 const f=state.demandFilters,s=state.demandData,a=state.demandAliases;
 const filters=`<form id="demand-search-form" class="filter-bar"><input class="search" id="demand-filter-q" type="search" placeholder="岗位、企业或 JD 中的关键词" aria-label="筛选 JD 需求" value="${esc(f.q)}"><button type="submit" class="btn btn-small">搜索</button>
 <select class="select" data-demand-filter="direction" aria-label="岗位方向"><option value="">全部方向</option>${opt(directionOptions(f.direction),f.direction)}</select>
 <select class="select" data-demand-filter="priority" aria-label="岗位优先级"><option value="">全部优先级</option>${opt(priorities,f.priority)}</select>
 <select class="select" data-demand-filter="job_status" aria-label="岗位核验状态"><option value="">全部核验状态</option>${opt(statuses,f.job_status)}</select>
 <select class="select" data-demand-filter="city" aria-label="城市"><option value="">全部城市</option>${opt(Object.fromEntries([...new Set(state.jobs.map(j=>j.city).filter(Boolean))].sort().map(x=>[x,x])),f.city)}</select>
 <label class="field field-inline"><input type="checkbox" data-demand-filter="include_pending" ${f.include_pending?'checked':''}>展示全部待确认候选</label>
 <label class="field field-inline"><input type="checkbox" data-demand-filter="include_ai_reviewed" ${f.include_ai_reviewed?'checked':''}>额外展示 AI 复核技能（独立统计）</label>
 <button type="button" class="btn btn-small" data-action="clear-demand-filters">清除筛选</button>${companyFactFilters(f,'demand-filter')}</form>`;
 const top=head('JD 需求画像','按独立岗位去重，区分明确必需、加分与仅提及；所有统计都能追溯到 JD 原文。');
 if(state.demandLoading&&!s)return top+filters+'<p class="subtitle">正在读取需求统计…</p>';
 if(state.demandError)return top+filters+`<div class="notice" role="alert">统计读取失败：${esc(state.demandError)} <button class="btn btn-small" data-action="refresh-demand">重试</button></div>`;
 if(!s)return top+filters+'<div class="notice">尚未加载需求统计。<button class="btn btn-small" data-action="refresh-demand">读取数据</button></div>';
 const ratio=s.selected_jobs?(100*s.confirmed_covered_jobs/s.selected_jobs).toFixed(1)+'%':'—';
 const skillRows=rows=>`<div class="table-card"><table class="table"><thead><tr><th>技能（人工别名归一）</th><th>岗位数</th><th>企业数</th><th>明确必需</th><th>加分</th><th>仅提及</th><th>占筛选岗位</th></tr></thead><tbody>${rows.map(x=>`<tr><td><button class="table-link" data-demand-skill="${esc(x.skill)}" data-demand-status="${rows===s.pending_skills?'pending':'confirmed'}">${esc(x.skill)}</button></td><td>${x.job_count}</td><td>${x.company_count}</td><td>${x.must_jobs}</td><td>${x.bonus_jobs}</td><td>${x.mentioned_jobs}</td><td>${x.share_of_selected_jobs_pct}%</td></tr>`).join('')}</tbody></table></div>`;
 const combos=s.composite_requirements;
 return top+filters+`<div class="stats">${stat('筛选岗位',s.selected_jobs,'包含没有提取结果的岗位')}${stat('已确认要求覆盖',s.confirmed_covered_jobs,ratio+' · 覆盖全部筛选岗位')}${stat('当前有效已确认要求',s.confirmed_requirements,'不含已过期和待审核记录')}${stat('过期要求',s.stale_excluded_requirements,'已排除，建议重新提取')}</div>
 <div class="notice">统计单位是<strong>独立岗位</strong>，不是关键词出现次数。覆盖率分母为筛选岗位总数；未完成 JD 要求提取的岗位仍在分母内。每条技能的必需/加分/仅提及按同岗位最高要求只记一次，不重复相加。当前词表版本：${s.alias_revision}。 ${state.demandLoading?'数据正在刷新…':''}</div>
 <div class="panel analysis-panel"><div class="page-toolbar toolbar-split"><h3 class="panel-title">已确认技能要求 · ${s.skills.length} 类</h3><button class="btn btn-small" data-action="download-demand-json">导出统计 JSON</button></div>
 ${s.skills.length?skillRows(s.skills):'<p class="subtitle">当前筛选范围没有已确认的单项技能要求。请先在 AI 分析工作台导回 JD 要求并人工确认。</p>'}
 <p class="tip">点击技能名称查看每条岗位原文、企业及分析包编号。组合要求不会拆成多个技能统计。</p></div>
 ${f.include_ai_reviewed?`<div class="panel analysis-panel"><h3 class="panel-title">AI 辅助复核技能 · ${s.ai_skills.length} 类</h3><p class="tip">这不是人工已确认数据。仅当前 JD 有效、AI 建议 verified 的单项技能独立统计，不和已确认岗位数合并。</p><div class="table-card"><table class="table"><thead><tr><th>技能</th><th>独立岗位</th><th>企业数</th><th>必需</th><th>加分</th></tr></thead><tbody>${s.ai_skills.map(x=>`<tr><td>${esc(x.skill)}</td><td>${x.job_count}</td><td>${x.company_count}</td><td>${x.must_jobs}</td><td>${x.bonus_jobs}</td></tr>`).join('')}</tbody></table></div></div>`:''}
 ${f.include_pending?`<div class="panel analysis-panel"><h3 class="panel-title">AI 待确认候选 · ${s.pending_skills.length} 类</h3><p class="tip">以下内容尚未人工确认，不混入上方已确认统计，当前覆盖 ${s.pending_covered_jobs} 个岗位。</p>${s.pending_skills.length?skillRows(s.pending_skills):'<p class="subtitle">当前没有有效待审核技能要求。</p>'}</div>`:''}
 <div class="grid-two">${demandComparison('岗位方向比较',s.comparisons.direction,demandTypeLabels)}${demandComparison('企业规模比较',s.comparisons.company_scale,{})}</div>${demandComparison('岗位优先级比较',s.comparisons.priority,priorities)}
 <div class="panel analysis-panel"><h3 class="panel-title">组合要求 · ${combos.length} 条</h3><p class="subtitle">例如“Python、Java 至少一种”，独立列出，不计入单个技能的硬性要求人数。</p>${combos.length?`<div class="row-list">${combos.map(x=>`<div class="rowline"><div><strong>${esc(x.name)}</strong><small>岗位 #${x.job_id} · ${x.option_logic==='any_of'?'满足任意一项':'全部满足'} · ${esc(x.strength)} · ${x.status==='pending'?'AI 待审核':'已确认'}</small><p class="tip">${esc(x.alternatives.join(' / '))} · 「${esc(x.evidence)}」</p></div><button class="btn btn-small" data-job="${x.job_id}">岗位原文</button></div>`).join('')}</div>`:'<p class="subtitle">本范围暂无组合要求。</p>'}</div>
 <div class="panel analysis-panel"><h3 class="panel-title">技能别名词表 <small>人工维护，不改变原始 JD</small></h3><p class="subtitle">只合并明确等价的术语。例如“工具调用”与“Tool Calling”；有实质差异的技能不要合并。添加/删除映射会重新计算统计，不修改原有已确认记录。</p>
 <form id="demand-alias-form" class="page-toolbar is-wrap"><input class="search" name="alias" placeholder="别名，例如 工具调用" aria-label="技能别名" required maxlength="120"><input class="search" name="canonical" placeholder="标准名称，例如 Tool Calling" aria-label="标准技能名称" required maxlength="120"><button class="btn btn-primary btn-small" type="submit">新增归一映射</button></form>
 ${a?.items.length?`<div class="row-list">${a.items.map(x=>`<div class="rowline"><div><strong>${esc(x.alias)}</strong><small>归一为 ${esc(x.canonical)}</small></div><button class="btn btn-small" data-delete-demand-alias="${esc(x.alias)}">删除映射</button></div>`).join('')}</div>`:'<p class="tip">目前没有人工别名。相同名称忽略大小写自动合并。</p>'}</div>`;
}
async function refreshDemandEvidence(skill,status,offset=0){
 const params=demandParams();params.set('skill',skill);params.set('review_status',status);params.set('offset',String(offset));params.set('limit','50');
 const data=await api('/requirements/stats/evidence?'+params);
 state.demandEvidence=data;
 const page=Math.floor(offset/50)+1,totalPages=Math.max(1,Math.ceil(data.total_requirements/50));
 const comparisons=Object.entries(data.comparisons).map(([axis,items])=>`<p class="tip">${axis==='direction'?'方向':'企业规模'}：${items.map(x=>esc((axis==='direction'?demandTypeLabels:{})[x.group]||x.group)+' '+x.jobs+' 岗').join(' / ')||'无'}</p>`).join('');
 modal('JD 参考资料：'+data.skill,`<div class="notice">${status==='pending'?'AI 待审核':'人工已确认'} · ${data.distinct_jobs} 个独立岗位 · ${data.total_requirements} 条记录。以下是原文摘录，并非自动证明真实招聘状态。</div>${comparisons}
 <div class="row-list">${data.items.map(x=>`<div class="rowline is-top"><div><strong>${esc(x.company)} · ${esc(x.job_title)}</strong><small>岗位 #${x.job_id} · ${esc(x.strength)} · ${esc(x.source_field)} · 分析包 ${esc(x.package_id.slice(0,8))}</small><p class="tip">「${esc(x.evidence)}」</p></div><button class="btn btn-small" data-job="${x.job_id}">查看岗位</button></div>`).join('')}</div>
 <div class="page-toolbar"><button class="btn btn-small" data-demand-evidence-page="${offset-50}" ${!offset?'disabled':''}>上一页</button><span class="tip">${page} / ${totalPages} 页</span><button class="btn btn-small" data-demand-evidence-page="${offset+50}" ${offset+50>=data.total_requirements?'disabled':''}>下一页</button></div>`,true);
}
async function addDemandAlias(form){
 const payload={alias:form.elements.namedItem('alias').value.trim(),canonical:form.elements.namedItem('canonical').value.trim()};
 await api('/requirements/stats/aliases',{method:'POST',body:JSON.stringify(payload)});
 await refreshDemandStats();toast('归一映射已保存');
}
async function deleteDemandAlias(alias){
 if(!confirm('删除“'+alias+'”的技能归一映射？不会删除任何原始要求。'))return;
 await api('/requirements/stats/aliases?'+new URLSearchParams({alias}),{method:'DELETE'});
 await refreshDemandStats();toast('映射已删除');
}
async function downloadDemandStats(){
 if(!state.demandData)return;
 await saveExportText('jd-demand-stats-'+new Date().toISOString().slice(0,10)+'.json',
  JSON.stringify(state.demandData,null,2),'application/json;charset=utf-8');
}
function skillsPage(){return skillGapPage()}
function learningPage(){return workspaceLearningPage()}
const issueNames={jd:'JD 缺失',source_link:'缺来源链接',city:'城市未填',eligibility:'资格未核实',unverified_source:'来源未核验',skills:'未标技能',stale_skill_evidence:'技能参考资料已过期'};
function qualityPanel(){const q=state.quality;const counts=q.issue_counts||{};return `<div class="panel mt-5"><h3 class="panel-title">岗位数据质量 <small>${q.jobs_checked} 条岗位</small></h3><p class="subtitle">只检查是否留存参考资料，不把缺失字段猜测补齐；来源已核验不代表岗位正在招聘。</p><div class="quality-counts">${Object.entries(counts).map(([k,n])=>`<span class="pill">${esc(issueNames[k]||k)}：${n}</span>`).join('')}</div>${q.jobs_with_issues.length?`<div class="row-list">${q.jobs_with_issues.slice(0,12).map(j=>`<div class="rowline"><div><strong>${esc(j.company)} · ${esc(j.title)}</strong><small>${j.issues.map(k=>esc(issueNames[k]||k)).join(' / ')}</small></div><button class="btn btn-small" data-job="${j.job_id}">补充资料</button></div>`).join('')}</div>`:'<p class="tip">没有发现缺失项。</p>'}${q.jobs_with_issues.length>12?'<p class="tip">仅展示最近 12 条有缺失的岗位。</p>':''}</div>`}
function sourcesPage(){
 const preview=state.importPreview;
 const count=preview?.counts;
 const previewBox=preview?`<div class="panel mt-5" id="import-preview"><h3 class="panel-title">${preview.dry_run?'导入前核对':'导入完成'} <small>${esc(state.importFileName)}</small></h3><div class="rowline"><strong>准备新增 ${count.new} 条</strong><span class="pill">已有 ${count.duplicate} 条 · 待人工判重 ${count.review} 条</span></div><div class="table-card mt-3"><table class="table"><thead><tr><th>企业 / 岗位</th><th>处理结果</th><th>原因</th></tr></thead><tbody>${preview.results.slice(0,50).map(r=>`<tr><td>${esc(r.company)}<span class="company-small">${esc(r.title)}</span></td><td>${esc({new:'新增',duplicate:'跳过已有',review:'暂缓人工核验'}[r.action])}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table></div>${preview.results.length>50?'<p class="tip">仅显示前 50 行，完整结果可通过 API 查看。</p>':''}${preview.dry_run?`<div class="form-actions"><button class="btn btn-primary" data-action="confirm-import" ${!count.new&&!count.review?'disabled':''}>${count.new?'确认导入 '+count.new+' 条新岗位':'提交重复审核'}</button></div>`:''}</div>`:'';
 const anchor=id=>'<span class="section-anchor" id="data-'+id+'"></span>';
 const sectionNav='<nav class="section-nav" aria-label="本页目录">'+[['backup','备份与恢复'],['agent','AI 与自动化'],['import','导入'],['export','导出文件'],['quality','资料质量'],['platform','平台连接']].map(([k,l])=>'<button type="button" class="btn btn-small btn-ghost" data-scroll-to="data-'+k+'">'+l+'</button>').join('')+'</nav>';
 return head('数据与帮助','备份恢复、可选连接器与故障诊断。')+sectionNav+anchor('backup')+desktopSupportPanel()+anchor('agent')+(typeof agentAccessPanel==='function'?agentAccessPanel():'')+
 anchor('import')+`<div class="grid-two"><div class="panel"><h3 class="panel-title">当前可用</h3><div class="rowline"><div><strong>手动录入岗位</strong><small>保存完整 JD、来源和技能标签</small></div><span class="pill verified">已可用</span></div><div class="rowline"><div><strong>CSV / JSON 批量导入</strong><small>先检查重复，确认后写入；最多每批 500 条</small></div><span class="pill verified">已可用</span></div><div class="rowline"><div><strong>SQLite 可恢复备份</strong><small>高级：仅数据库，不含截图；日常请使用上方完整备份</small></div><a class="btn btn-small" href="/api/backup/sqlite" download>下载 SQLite</a></div><div class="rowline"><div><strong>JSON 数据导出（不能直接恢复）</strong><small>导出数据库全部业务表；备份格式不等于岗位导入格式</small></div><a class="btn btn-small" href="/api/export" download>导出 JSON</a></div></div><div class="panel"><h3 class="panel-title">本地岗位导入</h3><p class="subtitle">提供 company（企业）与 title（岗位）两列即可导入；推荐附加 city、direction、jd_text、source_url、source_platform、skills 等字段。</p><p><a class="btn btn-small" href="/static/templates/job-import-example.csv" download>下载 CSV 示例</a></p><label class="field">选择岗位资料（.csv 或 .json）<input id="import-file" type="file" accept=".csv,.json,text/csv,application/json"></label><div class="form-actions"><button class="btn btn-primary" data-action="preview-import">解析并预览</button></div><p class="tip">同一个来源链接不会重复导入；企业、职位及城市相似但没有相同链接时会标记人工核验，不自动合并。导入的数据不会上传到外部服务。</p></div></div>`+
 previewBox+anchor('export')+exportManagerPanel()+anchor('quality')+qualityPanel()+anchor('platform')+`<div class="panel mt-5"><h3 class="panel-title">平台连接与后续能力</h3><div class="rowline"><strong>BOSS / 实习僧只读检索</strong><span class="pill">从首页开始检索</span></div><div class="rowline"><strong>JD 要求提取与人工核对</strong><span class="pill">在 AI 分析中使用</span></div><div class="notice">首页展示检索范围，点击“开始检索”后运行；采集收件箱的高级入口保留预览步骤。不发送求职消息，也不自动投递。请勿将 Cookie、登录凭据或私人聊天记录放入仓库。</div></div>`
}
function evidencePage(){return workspaceEvidencePage()}
function reviewActions(x){return `<button class="btn btn-small" data-review="${x.id}" data-resolve="link_source" ${!x.match_job_id||!x.input.source_url?'disabled':''}>关联到现有岗位</button><button class="btn btn-small" data-review="${x.id}" data-resolve="create_separate">另存为独立岗位</button><button class="btn btn-small" data-review="${x.id}" data-resolve="dismiss">忽略</button>`}
function reviewsPage(){return head('采集收件箱','对照候选原文与已收录档案，再决定关联来源、独立保存或忽略。')+(state.reviews.length?`<div class="company-grid">${state.reviews.map(x=>`<div class="company-card"><h3>${esc(x.input.title)}</h3><div class="small-data">${esc(x.input.company)} · ${esc(x.input.city||'城市不详')}</div><p>${esc(x.reason)}</p><p class="tip">已收录岗位：${x.match_job_id?'#'+x.match_job_id+' · '+esc(x.match_title):'未定位'}<br>候选来源：${esc(x.input.source_platform)}</p>${x.input.source_url?`<a href="${esc(secureLink(x.input.source_url))}" target="_blank" rel="noopener noreferrer">打开候选来源</a>`:''}<div class="form-actions"><button class="btn btn-primary btn-small" data-review-compare="${x.id}">对照原始 JD</button></div><div class="form-actions">${reviewActions(x)}</div></div>`).join('')}</div>`:empty('暂无待审核记录','疑似重复岗位会保留候选原文，供你决定关联或独立保存。'))}
async function compareReview(id){
 const x=state.reviews.find(r=>r.id===id);if(!x)return;
 const old=x.match_job_id?await api('/jobs/'+x.match_job_id):null;
 modal('对照岗位参考资料',`<div class="notice">来源不同或 JD 变化不等于同一个岗位。关联只增加来源并保留审核参考资料，不覆盖现有 JD。</div><div class="grid-two review-comparison"><section><h3>待审核资料</h3><p class="tip">${esc(x.input.company)} · ${esc(x.input.title)} · ${esc(x.input.city||'地点未填')}</p><div class="jd-box">${esc(x.input.jd_text||'候选未提供 JD，请先核对原始来源。')}</div></section><section><h3>已收录档案</h3><p class="tip">${old?esc(old.company.name)+' · '+esc(old.title)+' · '+esc(old.city||'地点未填'):'尚未匹配到岗位'}</p><div class="jd-box">${esc(old?.jd_text||'没有可对照的原始 JD')}</div></section></div><div class="form-actions">${reviewActions(x)}</div>`,true);
}

function addEvidence(){return openEvidenceEditor()}
async function viewCompanyJobs(companyId){
 const company=state.companies.find(x=>x.id===companyId);
 if(!company)throw new Error('企业已不在当前列表中');
 const jobs=await api('/companies/'+companyId+'/jobs');
 const rows=jobs.length?'<div class="row-list">'+jobs.map(j=>
  '<div class="rowline company-job-row"><div><strong>'+esc(j.title)+'</strong>'+
  '<small>'+esc(directions[j.direction]||j.direction)+' · '+
  esc(j.city||'地点未填写')+' · '+esc(priorities[j.priority]||j.priority)+'</small></div>'+
  '<button type="button" class="btn btn-small" data-company-job="'+j.id+'">去岗位库查看</button></div>'
 ).join('')+'</div>':'<p class="tip">这家企业尚无关联岗位，可在岗位库新建岗位并选择该企业。</p>';
 modal(esc(company.name)+' · 关联岗位',
  '<p class="subtitle">共 '+jobs.length+' 条岗位 · 企业规模：'+esc(companySizeLabel(company))+
  '</p>'+rows,true);
}
async function jumpToCompanyJob(jobId){
 if(!state.jobs.some(x=>x.id===jobId))throw new Error('该岗位已删除或未加载，请重新加载');
 await closeModal(true);
 navigate('jobs',{});
 const button=document.querySelector('button[data-job="'+jobId+'"]');
 button?.closest('tr')?.scrollIntoView({block:'center'});
 await jobDetail(jobId);
}
function editCompany(id){const c=state.companies.find(x=>x.id===id);if(!c)return;modal('编辑企业档案',`<form id="company-edit-form" data-company="${id}"><div class="field-grid">${field('name','企业名称 *','','text',true)}${field('city','城市')}${field('website','官方网站')}${selectField('status','关注状态',{watching:'重点观察',active:'正在招聘',paused:'暂不关注'})}</div>${companyEvidenceFields()}<label class="field">备注<textarea name="notes" rows="5"></textarea></label>${actions()}</form>`);for(const [k,v] of Object.entries(c)){const el=$('#company-edit-form [name="'+k+'"]');if(el)el.value=v??''}}
function editLearning(id){const t=state.learning.find(x=>x.id===id);if(!t)return;modal('编辑学习任务',`<form id="learning-edit-form" data-learning="${id}"><div class="field-grid">${field('title','任务名称 *','','text',true)}${field('related_skill','关联技能')}${selectField('priority','优先级',{high:'高',medium:'中',low:'低'})}${selectField('status','任务状态',{todo:'待开始',doing:'进行中',done:'已完成'})}${jobChoiceField('source_job_id','关联岗位')}</div><label class="field">备注<textarea name="notes"></textarea></label>${actions()}</form>`);for(const [k,v] of Object.entries(t)){const el=$('#learning-edit-form [name="'+k+'"]');if(el)el.value=v??''}}
async function resolveReview(id,action){
 const details={link_source:'确认是同一岗位，关联来源（不覆盖原 JD）',create_separate:'确认应视作另一个独立岗位并入库',dismiss:'忽略这一待审核记录'};
 if(!confirm(details[action]+'？'))return;
 const body=$('#modal-body').firstElementChild,buttons=[...document.querySelectorAll('button[data-review="'+id+'"]')];buttons.forEach(button=>button.disabled=true);
 try{await api('/reviews/'+id+'/resolve',{method:'POST',body:JSON.stringify({action})});await refreshAfterWrite('审核结果已保存');if($('#modal').open&&body===$('#modal-body').firstElementChild)await closeModal(true)}catch(error){toast(error.message)}finally{buttons.forEach(button=>button.disabled=false)}
}

const csvParse = text => {
 const rows=[];let cells=[],cell='',quoted=false;
 text=String(text).replace(/^\uFEFF/,'');
 for(let i=0;i<text.length;i++){const ch=text[i];if(quoted){if(ch==='"'&&text[i+1]==='"'){cell+='"';i++}else if(ch==='"'){quoted=false}else{cell+=ch}}else if(ch==='"'){quoted=true}else if(ch===','){cells.push(cell);cell=''}else if(ch==='\n'){cells.push(cell.replace(/\r$/,''));rows.push(cells);cells=[];cell=''}else{cell+=ch}}
 if(quoted)throw new Error('CSV 引号未正确闭合');
 if(cell||cells.length){cells.push(cell.replace(/\r$/,''));rows.push(cells)}
 if(rows.length<2)throw new Error('CSV 缺少数据行');
 const headers=rows.shift().map(x=>x.trim());
 if(!headers.includes('company')||!headers.includes('title'))throw new Error('CSV 至少需要 company 和 title 两列');
 return rows.filter(r=>r.some(v=>v.trim())).map(r=>{const o=Object.fromEntries(headers.map((h,i)=>[h,r[i]??'']));o.skills=o.skills?o.skills.split(/[;；]/).map(x=>x.trim()).filter(Boolean):[];return o});
};
let importRevision=0,importCommitting=false;
function clearImportPreview(){importRevision++;Object.assign(state,{importRows:[],importPreview:null,importFileName:''});$('#import-preview')?.remove()}
async function previewImport(){
 const file=$('#import-file')?.files?.[0];clearImportPreview();const revision=importRevision;
 if(!file){toast('请先选择 CSV 或 JSON 文件');return}
 try{
  if(file.size>2*1024*1024)throw new Error('文件超过 2 MB，请分批导入');
  const content=await file.text();const parsed=file.name.toLowerCase().endsWith('.csv')?csvParse(content):JSON.parse(content);
  const rows=Array.isArray(parsed)?parsed:parsed?.rows;
  if(!Array.isArray(rows)||rows.length<1)throw new Error('请提供岗位数组；数据库完整备份不能直接作为岗位导入');
  if(rows.length>500)throw new Error('每批最多 500 条岗位，请拆分文件');
  const normalized=rows.map(x=>({...x,skills:Array.isArray(x.skills)?x.skills:(x.skills?String(x.skills).split(/[;；]/).map(v=>v.trim()).filter(Boolean):[])}));
  const preview=await api('/import/jobs',{method:'POST',body:JSON.stringify({rows:normalized,dry_run:true})});
  if(revision!==importRevision)return;
  Object.assign(state,{importRows:normalized,importPreview:preview,importFileName:file.name});render();toast('解析完成，请核对导入计划');
 }catch(error){if(revision===importRevision)toast('导入预览失败：'+error.message)}
}
async function confirmImport(){
 if(importCommitting||!state.importPreview?.dry_run||!state.importRows.length)return;
 importCommitting=true;const button=$('[data-action=confirm-import]');if(button)button.disabled=true;
 const input=$('#import-file');if(input)input.disabled=true;
 try{
  const result=await api('/import/jobs',{method:'POST',body:JSON.stringify({rows:state.importRows,dry_run:false})});
  state.importPreview=result;
  await refreshAfterWrite(`已新增 ${result.counts.new} 条岗位，${result.counts.review} 条需人工核验`);
  if(result.counts.review&&state.page==='sources')navigate('reviews');
 }catch(error){toast('导入失败：'+error.message)}finally{importCommitting=false;if(input)input.disabled=false;if(button?.isConnected)button.disabled=!state.importPreview?.dry_run}
}

const secureLink = value => {try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:'#'}catch{return '#'}};
const mobileNavigation=window.matchMedia('(max-width:760px)');
let navigationRevision=0;
function navigate(page,filters){
 navigationRevision++;
 state.page=page;
 if(filters){state.filters={q:'',direction:'',status:'',priority:'',city:'',soon:'',quality:'',...filters};resetJobSelection()}
 render();if(page==='companyLists')refreshCompanyLists();if(page==='demand'&&!state.demandLoading)refreshDemandStats();if(page==='skills')refreshSkillGap();if(typeof resumeCaptureSearch==='function')resumeCaptureSearch();if(typeof resumeHomeSearch==='function')resumeHomeSearch();$('#content').scrollTop=0;closeNavigation();if(mobileNavigation.matches)$('#content').focus();
}
function closeNavigation(){document.body.classList.remove('sidebar-open');$('#nav-toggle').setAttribute('aria-expanded','false');$('#nav-toggle').setAttribute('aria-label','展开导航');$('#sidebar').inert=mobileNavigation.matches;$('.main').inert=false}
function render(){
 // Preserve an unfinished number input when an async statistics refresh renders.
 const sizeInput=document.activeElement?.matches('[data-size-number]')?document.activeElement:null;
 const sizeBox=sizeInput?.closest('[data-size-scope]');
 const primary=({bossFavorites:'overview',planner:'overview',reviews:'capture',evidence:'skills',learning:'skills',demand:'skills',companyLists:'companies'})[state.page]||state.page;
 $('#breadcrumb').textContent=({bossFavorites:'BOSS 岗位收藏',skills:'能力准备',evidence:'能力准备 / 我的能力与经历',learning:'能力准备 / 学习计划',reviews:'采集收件箱 / 重复审核'})[state.page]||names[state.page];
 $('#top-add-job').disabled=!state.loaded;
 $('#content').classList.toggle('workbench',state.page==='overview');
 document.querySelectorAll('.nav-item[data-page]').forEach(el=>{const active=el.dataset.page===primary;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});
 $('#nav-jobs-count').textContent=state.loaded?state.jobs.length:'—';
 const pending=state.captureBatches.reduce((n,b)=>n+b.remaining,0);
 const inbox=$('#nav-capture-count');
 inbox.textContent=pending+state.reviews.length;
 inbox.hidden=!(pending||state.reviews.length);
 if(!state.loaded){$('#content').innerHTML=state.loadError?empty('本地数据尚未加载',esc(state.loadError)+'。加载成功前不会显示空库结果。','<button class="btn btn-primary" data-action="reload-data">重新加载</button>'):'<div class="loading" role="status">正在加载本地工作空间…</div>';return}
 $('#global-search').value=state.filters.q;
 $('#content').innerHTML=(state.loadError?'<div class="notice page-error" role="alert">数据刷新失败，当前显示上次加载的结果。 <button class="btn btn-small" data-action="reload-data">重新加载</button></div>':'')+(typeof exampleNotice==='function'?exampleNotice():'')+({bossFavorites:bossFavoritesPage,overview,jobs:jobsPage,companies:companiesPage,companyLists:companyListsPage,skills:skillsPage,learning:learningPage,applications:applicationsPage,analysis:analysisPage,demand:demandPage,sources:sourcesPage,capture:capturePage,planner:plannerPage,reviews:reviewsPage,evidence:evidencePage}[state.page])();
 if(sizeBox){const replacement=$('#content').querySelector('[data-size-scope='+sizeBox.dataset.sizeScope+']');if(replacement){replacement.replaceWith(sizeBox);sizeInput.focus({preventScroll:true});}}
}
let modalInitialValues='',modalBusy=null,modalReturnJob=null,modalRevision=0;
const modalValues=()=>JSON.stringify([...document.querySelectorAll('#modal-body input,#modal-body select,#modal-body textarea')]
 .filter(el=>!el.hasAttribute('data-modal-transient')).map(el=>[el.name,
 el.type==='checkbox'||el.type==='radio'?el.checked:el.multiple?[...el.selectedOptions].map(o=>o.value):
 el.type==='file'?[...(el.files||[])].map(file=>[file.name,file.size,file.lastModified]):el.value]));
function canLeaveModal(){if(modalBusy){toast('正在保存，请稍候。');return false}return !$('#modal').open||modalValues()===modalInitialValues||confirm('有尚未保存的修改，确认放弃并离开？')}
function modal(title,markup,wide=false){
 modalRevision++;
 $('#modal-title').textContent=title;$('#modal-body').innerHTML=`<div class="modal-body-content">${markup}</div>`;
 const dialog=$('#modal');dialog.classList.remove('hidden');$('.modal').classList.toggle('wide',wide);
 if(!dialog.open){modalReturnJob=null;dialog.showModal()}
 const body=$('#modal-body').firstElementChild;queueMicrotask(()=>{if(body===$('#modal-body').firstElementChild)modalInitialValues=modalValues()});
 $('#modal-body').scrollTop=0;$('#modal-title').focus({preventScroll:true});
}
async function closeModal(force=false){
 if(force!==true&&!canLeaveModal())return false;
 if(typeof restoreWorkspaceReturn==='function'&&await restoreWorkspaceReturn())return true;
 if(force!==true&&typeof returnFromCaptureReview==='function'&&await returnFromCaptureReview())return true;
 const back=modalReturnJob;modalReturnJob=null;
 if(back){try{await jobDetail(back);showJobEdit();setDetailTab(document.querySelector('[data-detail-tab=sources]'));return true}catch(error){toast('无法返回岗位：'+error.message)}}
 modalRevision++;$('#modal').close();document.body.append($('#toast'));$('#modal').classList.add('hidden');return true;
}
// 学习任务关联岗位：按岗位名称选择，不需要记住编号。
function jobChoiceField(name,label){return `<label class="field">${label}<select name="${name}"><option value="">不关联岗位</option>${[...state.jobs].sort((a,b)=>String(a.title).localeCompare(String(b.title),'zh')).map(j=>`<option value="${j.id}">${esc(j.title)} · ${esc(j.company)}</option>`).join('')}</select></label>`}
function jobLinkLabel(id){if(!id)return '<span class="muted">未关联</span>';const j=state.jobs.find(x=>x.id===Number(id));return j?`<button class="table-link" data-job="${j.id}">${esc(j.title)}</button><span class="company-small">${esc(j.company)}</span>`:'<span class="muted">岗位已删除</span>'}
function field(name,label,placeholder='',type='text',required=false){return `<label class="field">${label}<input name="${name}" type="${type}" placeholder="${esc(placeholder)}" ${required?'required':''}></label>`}
function selectField(name,label,options,selected='',required=false){return `<label class="field">${label}<select name="${name}" ${required?'required':''}>${opt(options,selected)}</select></label>`}
function actions(){return `<div id="form-error" class="error"></div><div class="form-actions"><button type="button" class="btn" data-action="close">取消</button><button class="btn btn-primary" type="submit">保存记录</button></div>`}
function addCompany(){modal('新增企业',`<form id="company-form"><div class="field-grid">${field('name','企业名称 *','公司或团队的正式名称','text',true)}${field('city','城市','北京')}${field('website','官网链接','https://')}${selectField('status','关注状态',{watching:'重点观察',active:'正在招聘',paused:'暂不关注'})}</div>${companyEvidenceFields()}<label class="field">企业备注<textarea name="notes" placeholder="产品方向、团队特点、企业背景（注明待核验信息）"></textarea></label>${actions()}</form>`)}
function addLearning(){modal('新增学习任务',`<form id="learning-form"><div class="field-grid">${field('title','学习任务 *','完成一个可复现的 Agent 评测项目','text',true)}${field('related_skill','对应技能','AI 评测')}${selectField('priority','优先级',{high:'高',medium:'中',low:'低'})}${selectField('status','状态',{todo:'待开始',doing:'进行中',done:'已完成'})}</div>${jobChoiceField('source_job_id','关联岗位（可选）')}<div class="mt-4"><label class="field">备注<textarea name="notes"></textarea></label></div>${actions()}</form>`)}


const necessityNames={must:'硬性要求',bonus:'加分项',unspecified:'未判定'};
const observationTypes={note:'一般记录',hiring:'招聘变化',product:'产品动态',funding:'融资信息',team:'团队变化'};
async function companyObservations(id){
 const company=state.companies.find(c=>c.id===id);
 const items=await api('/companies/'+id+'/observations');
 modal('企业观察记录 · '+(company?.name||''),`<div class="notice">每次更新记录观察日期和来源。未经确认的融资或团队信息不要当作事实。</div><div class="row-list">${items.length?items.map(x=>`<div class="rowline"><div><strong>${esc(x.observed_on)} · ${esc(observationTypes[x.event_type]||x.event_type)}</strong><small>${esc(x.description)}</small>${x.source_url?`<small><a target="_blank" rel="noopener noreferrer" href="${esc(secureLink(x.source_url))}">查看参考资料来源 ↗</a></small>`:''}</div></div>`).join(''):'<p class="tip">暂无观察记录。岗位关闭后仍可继续记录企业动态。</p>'}</div><div class="detail-label">新增观察</div><form id="observation-form" data-company="${id}"><div class="field-grid">${selectField('event_type','记录类型',observationTypes)}${field('observed_on','观察日期 *','','date',true)}${field('source_url','来源链接','https://')}</div><label class="field">观察内容（描述事实及不确定性）<textarea name="description" rows="4" required maxlength="4000"></textarea></label>${actions()}</form>`,true);
}
function verifySourceModal(jobId,sourceId,current){
 if(!canLeaveModal())return;modalReturnJob=jobId;
 modal('核验招聘来源',`<div class="notice">只记录你实际检查到的来源页面状态。来源可访问不等于岗位仍在招聘，不会自动修改岗位开放状态。</div><form id="source-verify-form" data-job="${jobId}" data-source="${sourceId}">${selectField('verification','来源核验结果',{unverified:'未核验',verified:'已人工核验来源',unavailable:'当前无法访问'})}${actions()}</form>`);
 $('#source-verify-form [name=verification]').value=current;
}
async function reviewSkills(jobId){
 const origin=$('#modal-body').firstElementChild;
 const [job,result]=await Promise.all([api('/jobs/'+jobId),api('/jobs/'+jobId+'/skill-suggestions')]);
 if(origin!==$('#modal-body').firstElementChild||!$('#modal').open||!canLeaveModal())return;modalReturnJob=jobId;
 const existing=job.skills.map(x=>({...x,checked:true}));
 const names=new Set(existing.map(x=>x.name.toLowerCase()));
 state.skillReview=[...existing,...result.candidates.filter(x=>!names.has(x.name.toLowerCase())).map(x=>({...x,checked:false}))];
 const rows=state.skillReview.map((x,i)=>`<div class="skill-review-row"><label class="skill-review-choice"><input class="skill-choice" type="checkbox" data-idx="${i}" ${x.checked?'checked':''}><strong>${esc(x.name)}</strong><span class="pill">${x.checked?'现有标签':'关键词候选'}</span></label><select class="select" data-necessity="${i}">${opt(necessityNames,x.necessity)}</select>${x.evidence?`<div class="skill-review-evidence">${esc(x.evidence)}</div>`:'<div class="tip">未提供 JD 参考资料</div>'}</div>`).join('');
 modal('岗位技能核验',`<div class="notice">本版本仅做本地关键词提示，不会自动写入技能，也不代表 AI 已理解任职要求。逐条核对原文，再勾选确认。</div><form id="skill-review-form" data-job="${jobId}"><div class="skill-review-list">${rows||'<p class="tip">没有可识别的关键词。可手工增加技能。</p>'}</div>${field('extra_skills','补充人工技能标签（逗号分隔）','其他明确要求')}<p class="tip">保存时会以你勾选的技能替换当前标签，未勾选标签会被移除；操作保留审计记录。</p>${actions()}</form>`,true);
}



let exportBusy=false;
async function exportSelected(kind){
 if(exportBusy)return;
 if(!state.loaded)throw new Error('本地数据尚未加载');
 const rows=kind==='jobs'?selectedJobs():selectedCompanies();
 if(!rows.length){toast('当前没有可导出的记录');return}
 exportBusy=true;
 try{
  const result=await saveExportRoute('/api/exports/'+kind,{
    ids:rows.map(x=>x.id),filters:kind==='jobs'?state.filters:state.companyFilters,
    ...(kind==='jobs'&&state.page==='analysis'&&state.analysisFlow==='extract'?{intent:'jd_extraction'}:{})
  },'radar-'+kind+'-export.json');
  if(kind==='jobs'&&state.page==='analysis'){
   const oldBatches=new Set((state.workflow?.batches||[]).map(b=>b.package_id));
   const fresh=await api('/workflow');
   state.workflow=fresh;
   const newest=fresh.batches.find(b=>!oldBatches.has(b.package_id));
   if(newest){state.analysisBatch=newest.package_id;await changeRequirementPage('pending',0)}
  }
  toast('已导出 '+rows.length+' 条记录：'+result.name);
 }catch(error){toast(error.message)}finally{exportBusy=false}
}

function formdata(form){
 const data=Object.fromEntries(new FormData(form).entries());
 if(Object.hasOwn(data,'custom_direction')){
  data.direction=data.custom_direction.trim();
  delete data.custom_direction;
 }
 return data;
}
document.addEventListener('click',async event=>{try{
 const seq=event.target.closest('[data-req-seq]');
 if(seq){await applyRequirementSequence(seq.dataset.reqSeq,Number(seq.dataset.reqId));return}
 const readingTo=event.target.closest('[data-job-reading-to]');
 if(readingTo){if(readingTo.disabled||!canLeaveModal())return;await jobDetail(Number(readingTo.dataset.jobReadingTo));return}
 if(event.target.closest('[data-job-reading-edit]')){showJobEdit();return}
 if(event.target.closest('[data-job-reading-apply]')){openApplicationForm();return}
 const applyJob=event.target.closest('[data-apply-job]');
 if(applyJob){const id=Number(applyJob.dataset.applyJob);await closeModal(true);try{await jobDetail(id);openApplicationForm()}catch(error){toast(error.message)}return}
 if(event.target.closest('[data-job-reading-back]')){if(!canLeaveModal())return;$('#job-read-view').hidden=false;$('#job-edit-area').hidden=true;return}
 if(event.target.closest('[data-clear-analysis-selection]')){jobAnalysisIds=null;render();return}
 const mode=event.target.closest('[data-analysis-flow]');
 if(mode){await openAnalysisFlow(mode.dataset.analysisFlow);return}
 const batch=event.target.closest('[data-analysis-batch]');
 if(batch){await selectAnalysisBatch(batch.dataset.analysisBatch);return}
 const evidencePage=event.target.closest('[data-demand-evidence-page]');if(evidencePage){const d=state.demandEvidence;if(d)await refreshDemandEvidence(d.skill,d.review_status,Number(evidencePage.dataset.demandEvidencePage));return}const skillEvidence=event.target.closest('[data-demand-skill]');if(skillEvidence){await refreshDemandEvidence(skillEvidence.dataset.demandSkill,skillEvidence.dataset.demandStatus);return}const aliasDelete=event.target.closest('[data-delete-demand-alias]');if(aliasDelete){await deleteDemandAlias(aliasDelete.dataset.deleteDemandAlias);return}const nav=event.target.closest('[data-page]');if(nav){navigate(nav.dataset.page,nav.dataset.filterStatus?{status:nav.dataset.filterStatus}:nav.dataset.filterQuality?{quality:nav.dataset.filterQuality}:nav.dataset.filterPriority?{priority:nav.dataset.filterPriority}:undefined);if(nav.hasAttribute('data-workspace-tab'))$('[data-workspace-tab][aria-current="page"]')?.focus({preventScroll:true});return}const reqEdit=event.target.closest('[data-req-edit]');if(reqEdit){editRequirement(Number(reqEdit.dataset.reqEdit));return}const reqButton=event.target.closest('[data-req-review]');if(reqButton){await reviewRequirement(Number(reqButton.dataset.reqReview),reqButton.dataset.reqAction,reqButton.dataset.reqSync==='true');return}const detailTab=event.target.closest('[data-detail-tab]');if(detailTab){setDetailTab(detailTab);return}const observation=event.target.closest('button[data-observations]');if(observation){try{await companyObservations(Number(observation.dataset.observations))}catch(e){toast(e.message)}return}const sourceCheck=event.target.closest('button[data-sourcecheck]');if(sourceCheck){verifySourceModal(Number(sourceCheck.dataset.sourcejob),Number(sourceCheck.dataset.sourcecheck),sourceCheck.dataset.current);return}const skillButton=event.target.closest('button[data-skills]');if(skillButton){if(!canLeaveModal())return;try{await reviewSkills(Number(skillButton.dataset.skills))}catch(e){toast(e.message)}return}const companyJob=event.target.closest('[data-company-job]');if(companyJob){await jumpToCompanyJob(Number(companyJob.dataset.companyJob));return}
 const companyJobs=event.target.closest('[data-company-jobs]');if(companyJobs){await viewCompanyJobs(Number(companyJobs.dataset.companyJobs));return}
 const comp=event.target.closest('button[data-company]');if(comp){editCompany(Number(comp.dataset.company));return}const task=event.target.closest('button[data-learning]');if(task){editLearning(Number(task.dataset.learning));return}const remove=event.target.closest('[data-remove-evidence]');if(remove){if(confirm('删除这条我的能力与经历？')){await api('/evidence/'+remove.dataset.removeEvidence,{method:'DELETE'});await load();toast('已删除')}return}const comparison=event.target.closest('[data-review-compare]');if(comparison){await compareReview(Number(comparison.dataset.reviewCompare));return}const review=event.target.closest('button[data-review]');if(review){await resolveReview(Number(review.dataset.review),review.dataset.resolve);return}const adopt=event.target.closest('button[data-adopt-finding]');if(adopt){try{if(confirm('将这条 AI 助手建议采纳为本地学习任务？原始岗位数据不会改变。')){await api('/analysis/findings/'+Number(adopt.dataset.adoptFinding)+'/adopt-learning-task',{method:'POST'});await load();toast('学习任务已建立')}}catch(e){toast(e.message)}return}const job=event.target.closest('button[data-job]');if(job){try{await jobDetail(Number(job.dataset.job))}catch(e){toast(e.message)}return}const a=event.target.closest('[data-action]');if(a){await ({ 'reload-data':reloadData,'export-career-json':exportCareerPackage,'download-career-prompt':downloadCareerPrompt,'copy-career-prompt':()=>copyPromptText(careerPromptFor(state.careerGoal)),'copy-requirement-prompt':()=>copyPromptText(requirementPrompt),'refresh-demand':refreshDemandStats,'clear-demand-filters':()=>{state.demandFilters={q:'',direction:'',priority:'',city:'',job_status:'',include_pending:false,include_ai_reviewed:false};refreshDemandStats()},'download-demand-json':downloadDemandStats,'clear-filters':()=>{state.jobSort='priority';navigate('jobs',{})},'clear-company-filters':()=>{state.companyFilters={q:''};resetListingPage('companies');render()},'download-requirement-prompt':downloadRequirementPrompt,'preview-requirements':()=>requirementImport(true),'commit-requirements':()=>requirementImport(false),'requirement-review-sequence':()=>openRequirementSequence(),'requirement-select-page':()=>{state.requirementSelected=[...new Set([...state.requirementSelected,...state.requirementData.items.map(x=>x.id)])];render()},'requirement-clear-selection':()=>{state.requirementSelected=[];render()},'requirement-batch-confirm':()=>reviewRequirementBatch('confirm'),'requirement-batch-reject':()=>reviewRequirementBatch('reject'),'requirement-prev':()=>changeRequirementPage(state.requirementFilters.status,state.requirementFilters.offset-30),'requirement-next':()=>changeRequirementPage(state.requirementFilters.status,state.requirementFilters.offset+30),'export-unprocessed-jobs':exportOutstandingJds,
 'open-extraction-batches':()=>{$('#workflow-extraction-batches')?.scrollIntoView({behavior:'smooth',block:'start'})},
 'mark-no-candidates':markNoCandidates,
 'reexport-extraction-batch':reexportExtractionBatch,'switch-career-full':switchCareerFull,
 'review-all-requirements':async()=>{state.analysisBatch='';await changeRequirementPage('pending',0)},
 'export-jobs-json':()=>exportSelected('jobs'),'export-companies-json':()=>exportSelected('companies'),'add-company':addCompany,'add-job':quickAddJob,'choose-apply-job':chooseApplyJob,'analyze-full-jd':analyzeFullJd,'add-learning':addLearning,'add-evidence':addEvidence,'preview-import':previewImport,'confirm-import':confirmImport,'download-analysis-md':()=>downloadAnalysis('markdown'),'download-analysis-json':()=>downloadAnalysis('json'),'preview-analysis-import':()=>handleAnalysisImport(true),'commit-analysis-import':()=>handleAnalysisImport(false),'close':closeModal}[a.dataset.action]||(()=>{}))()}}catch(error){toast(error.message)}});
$('#top-add-job').addEventListener('click',()=>{if(canLeaveModal())quickAddJob()});
$('#modal-close').addEventListener('click',closeModal);
document.addEventListener('input',event=>{
 if(event.target.id==='apply-job-search')renderApplyJobResults(event.target.value);
});
$('#modal').addEventListener('click',event=>{if(event.target.id==='modal')closeModal()});
$('#modal').addEventListener('cancel',event=>{event.preventDefault();closeModal()});
$('#nav-toggle').addEventListener('click',()=>{const open=document.body.classList.toggle('sidebar-open');$('#nav-toggle').setAttribute('aria-expanded',String(open));$('#nav-toggle').setAttribute('aria-label',open?'收起导航':'展开导航');$('#sidebar').inert=!open&&mobileNavigation.matches;$('.main').inert=open;if(open)$('.sidebar .nav-item.active').focus()});
mobileNavigation.addEventListener('change',closeNavigation);closeNavigation();
$('#sidebar-scrim').addEventListener('click',()=>{closeNavigation();$('#nav-toggle').focus()});
$('#global-search-form').addEventListener('submit',event=>{event.preventDefault();navigate('jobs',{q:$('#global-search').value.trim()});$('#filter-q').focus()});
document.addEventListener('keydown',event=>{
 if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!$('#modal').open){event.preventDefault();closeNavigation();$('#global-search').focus();$('#global-search').select()}
 if(event.key==='Escape'&&document.body.classList.contains('sidebar-open')){closeNavigation();$('#nav-toggle').focus()}
 const tab=event.target.closest('[data-detail-tab]');
 if(tab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const tabs=[...document.querySelectorAll('[data-detail-tab]')];const idx=tabs.indexOf(tab);const next=event.key==='Home'?tabs[0]:event.key==='End'?tabs.at(-1):tabs[(idx+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length];setDetailTab(next);next.focus()}
});
let searchComposing=false;
function updateJobSearch(input){state.filters.q=input.value;resetJobSelection();const pos=input.selectionStart;render();const next=$('#filter-q');next.focus();if(pos!==null)try{next.setSelectionRange(pos,pos)}catch{}}
function updateCompanySearch(input){state.companyFilters.q=input.value;resetListingPage('companies');const pos=input.selectionStart;render();const next=$('#company-filter-q');if(next){next.focus();if(pos!==null)try{next.setSelectionRange(pos,pos)}catch{}}}
document.addEventListener('compositionstart',event=>{if(event.target.id==='filter-q'||event.target.id==='company-filter-q')searchComposing=true});
document.addEventListener('compositionend',event=>{if(event.target.id==='filter-q'||event.target.id==='company-filter-q'){searchComposing=false;if(event.target.id==='filter-q')updateJobSearch(event.target);else updateCompanySearch(event.target)}});
document.addEventListener('input',event=>{if(event.target.id==='requirement-import-json'){requirementRevision++;state.requirementImportRaw=event.target.value;state.requirementImportPreview=null}if(event.target.id==='analysis-import-json'){analysisRevision++;state.analysisImportPreview=null;state.analysisImportRaw=event.target.value;updateAnalysisButtons()}if(event.target.id==='filter-q'&&!searchComposing&&!event.isComposing)updateJobSearch(event.target);if(event.target.id==='company-filter-q'&&!searchComposing&&!event.isComposing)updateCompanySearch(event.target)});
document.addEventListener('change',event=>{
 if(event.target.matches('input[data-analysis-file]')){
  const input=event.target,file=input.files?.[0];input.value='';
  if(file)importGPTFile(file).catch(error=>toast('读取 AI JSON 失败：'+error.message));
  return;
 }
 if(event.target.hasAttribute('data-career-evidence')){const id=Number(event.target.dataset.careerEvidence);state.careerEvidenceIds=event.target.checked?[...new Set([...state.careerEvidenceIds,id])]:state.careerEvidenceIds.filter(x=>x!==id);if(state.page==='analysis')render();return}if(event.target.id==='career-include-ai'){state.careerIncludeAI=event.target.checked;render();return}if(event.target.id==='career-export-mode'){state.careerMode=event.target.value;render();return}if(event.target.id==='career-export-goal'){state.careerGoal=event.target.value;render();return}if(event.target.dataset.demandFilter){const k=event.target.dataset.demandFilter;state.demandFilters.q=$('#demand-filter-q')?.value||state.demandFilters.q;state.demandFilters[k]=(k==='include_pending'||k==='include_ai_reviewed')?event.target.checked:event.target.value;refreshDemandStats();return}if(event.target.dataset.reqSelect){const id=Number(event.target.dataset.reqSelect);state.requirementSelected=event.target.checked?[...new Set([...state.requirementSelected,id])]:state.requirementSelected.filter(x=>x!==id);render();return}if(event.target.id==='import-file')clearImportPreview();if(event.target.id==='filter-direction'){state.filters.direction=event.target.value;resetJobSelection();render()}if(event.target.id==='filter-status'){state.filters.status=event.target.value;resetJobSelection();render()}if(event.target.id==='filter-priority'){state.filters.priority=event.target.value;resetJobSelection();render()}if(event.target.id==='filter-city'){state.filters.city=event.target.value;resetJobSelection();render()}if(event.target.id==='requirement-status-filter'){changeRequirementPage(event.target.value,0).catch(e=>toast(e.message))}});
document.addEventListener('submit',async event=>{const form=event.target;if(form.id==='demand-search-form'){event.preventDefault();state.demandFilters.q=$('#demand-filter-q').value.trim();await refreshDemandStats();return}if(form.id==='demand-alias-form'){event.preventDefault();try{await addDemandAlias(form)}catch(e){toast(e.message)}return}if(form.id==='analysis-export-form'){event.preventDefault();await buildAnalysisPackage(form);return}if(!['company-form','company-edit-form','requirement-edit-form','job-form','learning-form','learning-edit-form','job-update-form','source-form','evidence-form','observation-form','skill-review-form','source-verify-form'].includes(form.id))return;event.preventDefault();const data=formdata(form);const submit=form.querySelector('button[type=submit]');if(!beginFormWrite(form))return;try{if(form.id==='requirement-edit-form'){const input=formdata(form);const payload={job_id:Number(form.dataset.job),requirement_type:input.requirement_type,name:input.name,strength:input.strength,source_field:input.source_field,option_logic:input.option_logic,evidence:input.evidence,alternatives:input.alternatives.split(/[;；\n]/).map(x=>x.trim()).filter(Boolean)};await api('/requirements/'+Number(form.dataset.requirement),{method:'PUT',body:JSON.stringify(payload)})}if(form.id==='company-form'){await api('/companies',{method:'POST',body:JSON.stringify(companyEvidencePayload(data))})}if(form.id==='company-edit-form'){await api('/companies/'+Number(form.dataset.company),{method:'PATCH',body:JSON.stringify(companyEvidencePayload(data))})}if(form.id==='observation-form'){await api('/companies/'+Number(form.dataset.company)+'/observations',{method:'POST',body:JSON.stringify(data)})}if(form.id==='source-verify-form'){await api('/jobs/'+Number(form.dataset.job)+'/sources/'+Number(form.dataset.source),{method:'PATCH',body:JSON.stringify(data)})}if(form.id==='skill-review-form'){const skills=[...form.querySelectorAll('.skill-choice')].filter(box=>box.checked).map(box=>{const entry=state.skillReview[Number(box.dataset.idx)];return {name:entry.name,category:entry.category,necessity:form.querySelector('[data-necessity="'+box.dataset.idx+'"]').value,evidence:entry.evidence||''}});const custom=(form.querySelector('[name=extra_skills]').value||'').split(/[,，]/).map(x=>x.trim()).filter(Boolean);for(const name of custom){if(!skills.some(x=>x.name.toLowerCase()===name.toLowerCase()))skills.push({name,necessity:'unspecified',evidence:''})}await api('/jobs/'+Number(form.dataset.job)+'/skills',{method:'PUT',body:JSON.stringify({confirmed:true,skills})})}if(form.id==='evidence-form'){await api('/evidence',{method:'POST',body:JSON.stringify(data)})}if(form.id==='learning-edit-form'){data.source_job_id=data.source_job_id?Number(data.source_job_id):null;await api('/learning/'+Number(form.dataset.learning),{method:'PATCH',body:JSON.stringify(data)})}if(form.id==='job-form'){data.company_id=Number(data.company_id);data.skills=[...new Set(data.skill_names.split(/[,，]/).map(x=>x.trim()).filter(Boolean))].map(name=>({name}));delete data.skill_names;await api('/jobs',{method:'POST',body:JSON.stringify(data)})}if(form.id==='learning-form'){data.source_job_id=data.source_job_id?Number(data.source_job_id):null;await api('/learning',{method:'POST',body:JSON.stringify(data)})}if(form.id==='source-form'){await api('/jobs/'+Number(form.dataset.job)+'/sources',{method:'POST',body:JSON.stringify(data)})}if(form.id==='job-update-form'){const payload=jobUpdatePayload(form,data);if(!Object.keys(payload).length){await closeModal(true);toast('没有修改需要保存');return}await api('/jobs/'+Number(form.dataset.job),{method:'PATCH',body:JSON.stringify(payload)})}await refreshAfterWrite('记录已保存到本地数据库');if(form.isConnected)await closeModal(true)}catch(e){showFormError(form,e)}finally{endFormWrite(form)}});
document.addEventListener('invalid',event=>{const panel=event.target.closest('[data-detail-panel]');if(panel){setDetailTab(document.querySelector('[data-detail-tab="'+panel.dataset.detailPanel+'"]'));queueMicrotask(()=>event.target.focus())}},true);
initHomeSearch();
load().catch(()=>render());
// 页内目录：滚动到对应分区（#content 是滚动容器，锚点已为吸顶目录留出位置）。
if(typeof document!=='undefined')document.addEventListener('click',event=>{const target=event.target.closest?.('[data-scroll-to]');if(!target)return;const reduce=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;document.getElementById(target.dataset.scrollTo)?.scrollIntoView({behavior:reduce?'auto':'smooth',block:'start'});});
