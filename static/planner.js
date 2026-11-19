/* Persistent library and representative plans; collection still requires confirmation. */
const plannerState = {
 library:null, plans:[], loading:false, error:'', tab:'library', importText:'', importFormat:'auto', importPreview:null,
 importRevision:0, importBusy:false, planBusy:false, planRevision:0, previewRequest:null, preview:null, opened:null,
 form:{family_ids:[],cities:'北京',platforms:['boss'],list_page_budget:12,detail_attempt_budget:30,time_budget_minutes:30,pages_per_query:1}
};
const plannerTerms = text => [...new Set(String(text||'').split(/[\n;；]/).map(v=>v.trim()).filter(Boolean))];
function plannerPaint(){if(state.page==='planner')render()}
async function refreshPlanner(){
 if(plannerState.loading)return;
 plannerState.loading=true;plannerState.error='';
 try{
  const [library,plans]=await Promise.all([api('/planner/library'),api('/planner/plans')]);
  plannerState.library=library;plannerState.plans=Array.isArray(plans)?plans:plans.plans||[];
  if(plannerState.opened){const latest=plannerState.plans.find(p=>p.id===plannerState.opened.id);if(latest)plannerState.opened=latest}
  plannerState.form.family_ids=plannerState.form.family_ids.filter(id=>library.families.some(f=>f.id===id));
 }catch(error){plannerState.error=error.message}finally{plannerState.loading=false;plannerPaint()}
}
function plannerPage(){
 const s=plannerState;
 if(!s.library&&!s.loading&&!s.error)refreshPlanner();
 return head('词库与计划','管理常用搜索词，保存按城市和平台执行的检索计划。',`<div class="page-toolbar"><button type="button" class="btn" data-page="overview">返回工作台</button><button type="button" class="btn" data-planner-action="reload" ${s.loading?'disabled':''}>刷新词库与进度</button></div>`)+
 `<div class="planner-tabs" role="group" aria-label="规划工作区">${[['library','岗位词库'],['plan','检索计划']].map(([id,label])=>`<button class="btn" data-planner-tab="${id}" aria-pressed="${s.tab===id||(id==='library'&&s.tab==='import')}">${label}</button>`).join('')}</div>`+
 (s.error?`<div class="notice page-error" role="alert"><span>${esc(s.error)}</span><button class="btn btn-small" data-planner-action="reload">重新加载</button></div>`:'')+
 (s.loading&&!s.library?'<div class="loading" role="status">正在读取词库和检索计划…</div>':s.tab==='import'?plannerImportPage():s.tab==='library'?plannerLibraryPage():plannerPlanPage());
}
function plannerImportPage(){
 const s=plannerState,p=s.importPreview;
 return `<section class="panel"><h2 class="panel-title">导入关键词资料</h2><p class="subtitle">支持 JSON、CSV 和 Markdown 表格。预览只统计实际解析的内容，确认后才写入本地词库。</p>
 <div class="planner-route-actions"><button class="btn btn-small" data-planner-action="load-template" ${s.importBusy?'disabled':''}>加载 AI 方向示例词库</button><a class="btn btn-small" href="/static/templates/ai-keywords-v1.json" download>下载示例 JSON</a></div><div class="field-grid"><label class="field">文件（最多 2 MB / 30 万字符）<input id="planner-file" type="file" accept=".json,.csv,.md,.txt,application/json,text/csv,text/plain" ${s.importBusy?'disabled':''}></label><label class="field">解析格式<select id="planner-format" ${s.importBusy?'disabled':''}>${opt({auto:'自动识别',json:'JSON',csv:'CSV',markdown:'Markdown'},s.importFormat)}</select></label></div>
 <label class="field">粘贴词库内容<textarea id="planner-import-text" rows="10" maxlength="300000" placeholder="粘贴岗位族表格，或包含 route、name、representative、aliases 等字段的 JSON / CSV" ${s.importBusy?'disabled':''}>${esc(s.importText)}</textarea></label>
 <details class="planner-help"><summary>查看最小导入示例</summary><pre>${esc('id,route,name,representative,aliases\nB01,Agent / AI DevTools,Agent 产品经理,AI Agent 产品实习生,智能体产品经理;Agent 平台产品')}</pre><p class="tip">岗位搜索词、JD 特征、资格条件和风险词分别保存；示例中的别名用分号分隔。</p></details>
 <div class="form-actions"><button class="btn btn-primary" data-planner-action="import-preview" ${s.importBusy?'disabled':''}>${s.importBusy?'正在处理…':'解析并预览'}</button></div></section>`+
 (p?`<section class="panel planner-section"><h2 class="panel-title">导入前核对</h2><div class="planner-summary">${[['families','岗位族'],['search_terms','搜索词'],['jd_features','JD 特征'],['qualification','资格词'],['risk','风险词']].map(([key,label])=>`<span><strong>${Number(p.counts?.[key]||0)}</strong> ${label}</span>`).join('')}</div>
 ${p.warnings?.length?`<div class="notice"><strong>需要核对</strong><ul>${p.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>`:''}
 ${plannerFamilyTable(p.families||[],false)}<p class="tip">同 ID 的岗位族会合并；未提供的词条以预览结果为准。未识别的内容请调整格式后重新预览。</p><div class="form-actions"><button class="btn btn-primary" data-planner-action="import-save" ${s.importBusy||!p.families?.length||!s.library?'disabled':''}>确认保存 ${p.families?.length||0} 个岗位族</button></div></section>`:'');
}
function plannerFamilyTable(families,edit=true){
 if(!families.length)return '<p class="tip">尚无岗位族。导入关键词资料后，可选择代表词并建立检索计划。</p>';
 return `<div class="table-card planner-table"><table class="table"><thead><tr><th>职业路线 / 岗位族</th><th>代表搜索词</th><th>别名及特征</th>${edit?'<th>操作</th>':''}</tr></thead><tbody>${families.map(f=>`<tr><td><strong>${esc(f.name)}</strong><span class="company-small">${esc(f.route)} · ${esc(f.priority)}${f.enabled===false?' · 已停用':''}</span></td><td>${esc(f.representative)}</td><td>${esc((f.aliases||[]).join('、')||'无别名')}<span class="company-small">JD ${(f.jd_features||[]).length} · 资格 ${(f.qualification||[]).length} · 风险 ${(f.risk||[]).length}</span></td>${edit?`<td><button class="btn btn-small" data-planner-edit="${esc(f.id)}">编辑</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
function plannerLibraryPage(){
 const l=plannerState.library;
 return `<section class="panel"><h2 class="panel-title">岗位族与代表词 <button class="btn btn-small" data-planner-tab="import">导入词库</button></h2><p class="subtitle">代表词用于首轮搜索。别名保留为后续探索；JD、资格和风险词不自动变成搜索任务。</p>${plannerFamilyTable(l?.families||[])}
 ${[['global_jd_features','通用 JD 特征'],['global_qualification','通用资格词'],['global_risk','通用风险词']].map(([key,label])=>l?.[key]?.length?`<details class="planner-help"><summary>${label} · ${l[key].length}</summary><p>${esc(l[key].join('、'))}</p></details>`:'').join('')}</section>`;
}
function plannerPlanPage(){
 const s=plannerState,f=s.form,families=s.library?.families||[],routes=[...new Set(families.map(x=>x.route))];
 return `<section class="panel"><h2 class="panel-title">建立代表词检索计划</h2><p class="subtitle">选择岗位族、地点和额度，先核对本轮覆盖范围，再保存计划。</p>
 ${!families.length?'<div class="notice">当前没有关键词资料。可以直接加载内置的 AI 方向示例词库（40 个岗位族），预览后保存即可使用；也可以导入自己的词库。 <button class="btn btn-small btn-primary" data-planner-action="quick-template">加载内置词库</button> <button class="btn btn-small" data-planner-tab="import">导入自己的词库</button></div>':`<div class="planner-route-actions">${routes.map(route=>`<button class="btn btn-small" data-planner-route="${esc(route)}">选择 ${esc(route)}</button>`).join('')}<button class="btn btn-small" data-planner-action="clear-families">清空选择</button></div><div class="planner-families">${families.map(x=>`<label><input type="checkbox" data-planner-family="${esc(x.id)}" ${f.family_ids.includes(x.id)?'checked':''} ${x.enabled===false?'disabled':''}><span><strong>${esc(x.name)}</strong><small>${esc(x.route)} · ${esc(x.representative)}${x.enabled===false?' · 已停用':''}</small></span></label>`).join('')}</div>`}
 <form id="planner-plan-form"><div class="field-grid"><label class="field">城市（用逗号或换行分隔）<input name="cities" required maxlength="300" value="${esc(f.cities)}" placeholder="北京，上海，深圳"></label><fieldset class="planner-platforms"><legend>平台</legend>${[['boss','BOSS 直聘'],['shixiseng','实习僧']].map(([id,label])=>`<label><input type="checkbox" name="platforms" value="${id}" ${f.platforms.includes(id)?'checked':''}>${label}</label>`).join('')}</fieldset>
 ${[['list_page_budget','列表页总额度',1,200],['detail_attempt_budget','详情尝试总额度',1,600],['time_budget_minutes','计划时间额度（分钟）',1,240],['pages_per_query','每项计划页数',1,3]].map(([key,label,min,max])=>`<label class="field">${label}<input type="number" name="${key}" min="${min}" max="${max}" required value="${f[key]}"></label>`).join('')}</div>
 <p class="tip">列表页总额度：整份计划最多翻多少页搜索结果。详情尝试总额度：最多打开多少个岗位详情。计划时间额度只用于估算，不会自动停止检索。每项计划页数：每个“搜索词 + 城市”翻几页。</p>
 <p class="tip">本阶段保存规划和人工执行进度；不会自动连续运行，也不保证两个平台都支持计划中的时间和详情限制。保存计划后，每一项都可以“在首页检索”，或“准备检索”到采集收件箱按分配额度执行。</p><div class="form-actions"><button class="btn btn-primary" type="submit" ${s.planBusy||!families.length?'disabled':''}>${s.planBusy?'正在生成…':'预览检索计划'}</button></div></form></section>`+
 (s.preview?plannerPreview(s.preview,false):'')+
 `<section class="panel planner-section"><h2 class="panel-title">已保存的计划</h2>${s.plans.length?`<div class="row-list">${s.plans.map(p=>`<div class="rowline"><div><strong>计划 #${esc(p.id)}</strong><small>${esc(p.created_at||'')} · ${(p.actions||[]).length} 项检索</small></div><button class="btn btn-small" data-planner-open="${esc(p.id)}">打开计划</button></div>`).join('')}</div>`:'<p class="tip">还没有保存计划。预览覆盖范围后，可将计划保存到本地。</p>'}</section>`+
 (s.opened?plannerPreview(s.opened,true):'');
}
function plannerPreview(p,saved){
 const actions=p.actions||[],uncovered=p.uncovered||[],aliases=p.remaining_aliases||[];
 return `<section class="panel planner-section" id="${saved?'planner-saved-plan':'planner-preview'}"><h2 class="panel-title">${saved?'已保存计划 #'+esc(p.id):'本轮计划预览'}</h2><p class="subtitle">${actions.length} 项代表词检索 · ${uncovered.length} 项暂未覆盖 · ${aliases.reduce((n,x)=>n+(x.aliases||[]).length,0)} 个别名保留待探索</p>
 <div class="notice">额度是规划分配值；平台实际支持的限制以采集确认页为准。准备检索会创建或打开采集批次；请在采集页检查连接、预览并确认启动。</div>${(p.runtime_warnings||p.warnings)?.length?`<ul class="tip">${(p.runtime_warnings||p.warnings).map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}
 ${actions.length?`<div class="table-card planner-table"><table class="table"><thead><tr><th>搜索词</th><th>城市 / 平台</th><th>分配额度</th>${saved?'<th>下一步</th>':''}</tr></thead><tbody>${actions.map((a,i)=>`<tr><td>${esc(a.keyword)}</td><td>${esc(a.city)}<span class="company-small">${esc({boss:'BOSS 直聘',shixiseng:'实习僧'}[a.platform]||a.platform)}</span></td><td>列表 ${Number(a.list_pages)} 页<span class="company-small">详情 ${Number(a.detail_attempts)} 次</span>${a.costs_unverified?'<span class="company-small">历史成本未核实，保留原额度</span>':''}${a.runtime_costs?`<span class="company-small">已用列表 ${Number(a.runtime_costs.list_attempts||0)} 次 · 详情 ${Number(a.runtime_costs.detail_attempts||0)} 次 · 缓存 ${Number(a.runtime_costs.cache_hits||0)} 条${Number(a.runtime_costs.unknown_attempts||0)?' · 待核对访问 '+Number(a.runtime_costs.unknown_attempts):''}</span>`:''}</td>${saved?`<td><button class="btn btn-small btn-primary" data-planner-home="${i}" title="把这一项的搜索词、城市和平台填入首页检索">在首页检索</button> <button class="btn btn-small" data-planner-prepare="${i}">${a.batch_id?'打开采集':'准备检索'}</button>${a.batch_id?`<span class="company-small">采集批次 #${esc(a.batch_id)}${a.collection_status?' · '+esc({starting:'正在启动',running:'检索中',stopping:'正在停止',completed:'已完成',stopped:'已停止',failed:'失败',unknown:'状态待核对'}[a.collection_status]||a.collection_status):''}</span>`:''}</td>`:''}</tr>`).join('')}</tbody></table></div>`:'<p class="tip">当前条件没有可执行的代表词检索，请调整选择或额度。</p>'}
 ${uncovered.length?`<details class="planner-help"><summary>暂未覆盖的范围 · ${uncovered.length}</summary><ul>${uncovered.map(x=>`<li>${esc(x.keyword)} · ${esc(x.city)} · ${esc({boss:'BOSS 直聘',shixiseng:'实习僧'}[x.platform]||x.platform)} — ${esc(x.reason)}</li>`).join('')}</ul></details>`:''}
 ${aliases.length?`<details class="planner-help"><summary>保留的同义词探索</summary><ul>${aliases.map(x=>`<li>${esc((p.families||plannerState.library?.families||[]).find(f=>f.id===x.family_id)?.name||x.family_id)}：${esc((x.aliases||[]).join('、'))}</li>`).join('')}</ul></details>`:''}
 ${!saved?`<div class="form-actions"><button class="btn btn-primary" data-planner-action="plan-save" ${plannerState.planBusy||!actions.length?'disabled':''}>保存计划</button></div>`:''}</section>`;
}
function plannerInvalidateImport(){plannerState.importRevision++;plannerState.importPreview=null;document.querySelector('#planner-import-text')?.closest('section')?.nextElementSibling?.remove()}
function plannerInvalidatePlan(){plannerState.planRevision++;plannerState.preview=null;plannerState.previewRequest=null;document.querySelector('#planner-preview')?.remove()}
function plannerPlanRequest(){
 const f=plannerState.form;
 return {...f,cities:[...new Set(f.cities.split(/[\n,，;；]/).map(x=>x.trim()).filter(Boolean))],expected_version:plannerState.library.version};
}
async function plannerLoadTemplate(){
 const s=plannerState;if(s.importBusy)return;
 plannerInvalidateImport();const revision=s.importRevision;s.importBusy=true;plannerPaint();
 try{const response=await fetch('/static/templates/ai-keywords-v1.json');if(!response.ok)throw new Error('词库模板暂不可用，请自行粘贴或选择文件');const text=await response.text();if(revision===s.importRevision){s.importText=text;s.importFormat='json'}}catch(e){toast(e.message)}finally{s.importBusy=false;plannerPaint()}
}
async function plannerImportPreview(){
 const s=plannerState;if(s.importBusy)return;
 const revision=s.importRevision;s.importBusy=true;plannerPaint();
 try{if(s.importText.length>300000)throw new Error('内容超过 30 万字符，请拆分后导入');const preview=await api('/planner/import-preview',{method:'POST',body:JSON.stringify({format:s.importFormat,text:s.importText})});if(revision===s.importRevision)s.importPreview=preview}
 catch(e){toast(e.message)}finally{s.importBusy=false;plannerPaint()}
}
async function plannerImportSave(){
 const s=plannerState;if(s.importBusy||!s.importPreview)return;
 s.importBusy=true;plannerPaint();
 try{await api('/planner/library/import',{method:'POST',body:JSON.stringify({...s.importPreview,expected_version:s.library.version})});s.importPreview=null;await refreshPlanner();toast('词库已保存');s.tab='library'}
 catch(e){toast(e.message)}finally{s.importBusy=false;plannerPaint()}
}
async function plannerPreviewPlan(){
 const s=plannerState;if(s.planBusy)return;
 s.planBusy=true;const request=plannerPlanRequest(),revision=s.planRevision;plannerPaint();
 try{const preview=await api('/planner/plans/preview',{method:'POST',body:JSON.stringify(request)});if(revision===s.planRevision){s.preview=preview;s.previewRequest=request}}catch(e){toast(e.message)}finally{s.planBusy=false;plannerPaint()}
}
async function plannerSavePlan(){
 const s=plannerState;if(s.planBusy||!s.preview)return;
 s.planBusy=true;plannerPaint();
 try{s.opened=await api('/planner/plans',{method:'POST',body:JSON.stringify(s.previewRequest)});s.preview=null;s.previewRequest=null;await refreshPlanner();toast('检索计划已保存')}
 catch(e){toast(e.message)}finally{s.planBusy=false;plannerPaint()}
}
function plannerEditFamily(id){
 const f=plannerState.library.families.find(x=>x.id===id);if(!f)return;
 modal('编辑岗位族',`<form id="planner-family-form" data-id="${esc(id)}" data-version="${plannerState.library.version}"><div class="field-grid"><label class="field">职业路线<input name="route" required maxlength="160" value="${esc(f.route)}"></label><label class="field">岗位族名称<input name="name" required maxlength="160" value="${esc(f.name)}"></label><label class="field">代表搜索词<input name="representative" required maxlength="120" value="${esc(f.representative)}"></label><label class="field">优先级<select name="priority">${opt({P0:'P0',P1:'P1',P2:'P2',P3:'P3'},f.priority)}</select></label></div>
 ${[['aliases','同义词'],['jd_features','JD 特征词'],['qualification','资格条件词'],['risk','风险词']].map(([key,label])=>`<label class="field">${label}（每行一个）<textarea name="${key}" rows="3">${esc((f[key]||[]).join('\n'))}</textarea></label>`).join('')}<label class="capture-check"><input type="checkbox" name="enabled" ${f.enabled!==false?'checked':''}>参与检索规划</label>${actions()}</form>`,true);
}
document.addEventListener('input',event=>{
 const el=event.target;
 if(el.id==='planner-import-text'){plannerState.importText=el.value;plannerInvalidateImport()}
 if(el.closest('#planner-plan-form')){
  if(el.name==='platforms')plannerState.form.platforms=[...document.querySelectorAll('#planner-plan-form [name=platforms]:checked')].map(x=>x.value);
  else if(el.name)plannerState.form[el.name]=el.name==='cities'?el.value:Number(el.value);
  plannerInvalidatePlan();
 }
});
document.addEventListener('change',async event=>{
 const el=event.target;
 if(el.id==='planner-format'){plannerState.importFormat=el.value;plannerInvalidateImport()}
 if(el.dataset.plannerFamily){const ids=new Set(plannerState.form.family_ids);el.checked?ids.add(el.dataset.plannerFamily):ids.delete(el.dataset.plannerFamily);plannerState.form.family_ids=[...ids];plannerInvalidatePlan()}
 if(el.id==='planner-file'){
  const file=el.files[0];if(!file)return;
  plannerInvalidateImport();const revision=plannerState.importRevision;
  try{if(file.size>2*1024*1024)throw new Error('文件超过 2 MB，请拆分后导入');const text=await file.text();if(text.length>300000)throw new Error('内容超过 30 万字符，请拆分后导入');if(revision!==plannerState.importRevision)return;plannerState.importText=text;document.querySelector('#planner-import-text').value=text;toast('文件已读取，请解析并预览')}
  catch(e){toast(e.message)}
 }
});
document.addEventListener('submit',async event=>{
 const form=event.target;
 if(form.id==='planner-plan-form'){event.preventDefault();await plannerPreviewPlan()}
 if(form.id==='planner-family-form'){
  event.preventDefault();if(!beginFormWrite(form))return;
  try{const old=plannerState.library.families.find(x=>x.id===form.dataset.id),values=Object.fromEntries(new FormData(form));
   const family={...old,route:values.route.trim(),name:values.name.trim(),representative:values.representative.trim(),priority:values.priority,enabled:form.elements.enabled.checked};
   for(const key of ['aliases','jd_features','qualification','risk'])family[key]=plannerTerms(values[key]);
   await api('/planner/families/'+encodeURIComponent(family.id),{method:'PUT',body:JSON.stringify({family,expected_version:Number(form.dataset.version)})});plannerInvalidatePlan();await closeModal(true);await refreshPlanner();toast('岗位族已保存');
  }catch(e){showFormError(form,e)}finally{endFormWrite(form)}
 }
});
document.addEventListener('click',async event=>{
 const el=event.target.closest('[data-planner-action],[data-planner-tab],[data-planner-edit],[data-planner-route],[data-planner-open],[data-planner-prepare],[data-planner-home]');if(!el)return;
 try{
  if(el.dataset.plannerTab){plannerState.tab=el.dataset.plannerTab;plannerPaint()}
  else if(el.dataset.plannerEdit)plannerEditFamily(el.dataset.plannerEdit);
  else if(el.dataset.plannerRoute){plannerState.form.family_ids=[...new Set([...plannerState.form.family_ids,...plannerState.library.families.filter(x=>x.route===el.dataset.plannerRoute&&x.enabled!==false).map(x=>x.id)])];plannerInvalidatePlan();plannerPaint()}
  else if(el.dataset.plannerOpen){plannerState.opened=await api('/planner/plans/'+encodeURIComponent(el.dataset.plannerOpen));plannerPaint();document.querySelector('#planner-saved-plan')?.scrollIntoView({block:'start'})}
  else if(el.dataset.plannerHome!==undefined){const a=plannerState.opened.actions[Number(el.dataset.plannerHome)];if(!a||typeof useHomeSearchConditions!=='function')throw new Error('首页检索尚未加载，请重新打开应用');useHomeSearchConditions({keywords:[a.keyword],cities:[a.city],platform:a.platform,max_pages:a.list_pages,max_jobs:a.detail_attempts});navigate('overview')}
  else if(el.dataset.plannerPrepare!==undefined){if(typeof window.preparePlannerSearch!=='function')throw new Error('采集入口尚未加载，请重新打开应用');await window.preparePlannerSearch(plannerState.opened.actions[Number(el.dataset.plannerPrepare)],plannerState.opened.id)}
  else await ({reload:refreshPlanner,'load-template':plannerLoadTemplate,'quick-template':async()=>{plannerState.tab='import';await plannerLoadTemplate();await plannerImportPreview();document.querySelector('[data-planner-action="import-save"]')?.scrollIntoView({block:'center'})},'import-preview':plannerImportPreview,'import-save':plannerImportSave,'plan-save':plannerSavePlan,'clear-families':()=>{plannerState.form.family_ids=[];plannerInvalidatePlan();plannerPaint()}}[el.dataset.plannerAction]||(()=>{}))();
 }catch(e){toast(e.message)}
});
