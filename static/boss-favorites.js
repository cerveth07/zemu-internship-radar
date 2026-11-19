/* Imported target lists and explicit BOSS favorites. No messaging controls. */
const bossFavorites={batches:[],current:null,options:null,busy:'',error:'',preview:null,
 text:'',format:'text',name:'我的岗位清单',city:'北京',browser_mode:'managed',internship_only:true,
 creating:false,sequence:0,inputRevision:0,request:null,timer:null,returnJob:null};
const bossFavoriteActive=()=>['starting','pending','running','stopping','unknown'].includes(bossFavorites.current?.run?.status);
function bossFavoritesPage(){
 const s=bossFavorites,d=s.current,batch=d?.batch,active=bossFavoriteActive(),progress=d?.run?.progress||{};
 const targets=d?.targets||[],candidates=targets.flatMap(t=>t.candidates),chosen=candidates.filter(c=>c.selected);
 const pending=targets.filter(t=>t.state==='pending').length,chosenCount=new Set(chosen.map(c=>c.url)).size;
 const phase=progress.phase||'正在准备';
 const waiting=['login_required','verification_required'].includes(phase);
 const status={starting:'正在启动',running:'正在处理',pending:'正在准备',stopping:'正在停止',unknown:'状态待确认',completed:'已完成',completed_with_errors:'已完成，部分岗位需重试',failed:'已中断',stopped:'已停止'};
 const input=(!batch||s.creating)?`<section class="panel boss-favorite-import"><h2 class="panel-title">导入岗位清单</h2>
  <p class="tip">粘贴一列岗位名称即可；有企业、城市、BOSS 链接时可更准确地找到对应职位。每份清单最多 100 条。</p>
  <form id="boss-favorite-import-form"><div class="field-grid">
   <label class="field">清单名称<input name="name" value="${esc(s.name)}" maxlength="120" required></label>
   <label class="field">默认城市<select name="city">${opt((s.options?.cities||['北京']).map(x=>[x,x]),s.city)}</select></label>
   <label class="field">浏览器<select name="browser_mode">${opt({managed:'独立采集窗口（无需扩展）',chrome_use:'使用当前 Chrome（需 Chrome Use）'},s.browser_mode)}</select></label>
  </div><label class="field">岗位名称或清单内容<textarea name="text" rows="5" maxlength="262144" placeholder="产品运营实习&#10;财务分析实习&#10;&#10;也可复制含企业、岗位、城市、链接表头的 Excel 内容">${esc(s.text)}</textarea></label>
  <div class="page-toolbar"><button class="btn btn-small" type="button" data-boss-favorite="file">选择 CSV / TXT</button>
   <a class="btn btn-small" href="/api/boss-favorites/template" download="boss-job-list-template.csv">下载 CSV 模板</a>
   <label class="capture-check"><input type="checkbox" name="internship_only" ${s.internship_only?'checked':''}>名称检索仅找实习岗位</label></div>
  <p class="tip">支持 UTF-8 CSV、TXT 和 Excel 复制内容；.xlsx 请另存为 CSV UTF-8。指定原始链接时直接读取该职位。</p>
  <input type="file" id="boss-favorite-file" accept=".csv,.txt,text/csv,text/plain" hidden>
  <div id="boss-favorite-preview">${bossFavoritePreview()}</div>
  <div class="form-actions"><button class="btn" type="submit" ${s.busy?'disabled':''}>${s.busy==='preview'?'正在识别…':'预览清单'}</button>
   <button class="btn btn-primary" type="button" data-boss-favorite="create" ${s.busy||!s.preview||s.preview.errors.length||!s.preview.items.length?'disabled':''}>保存清单并查找</button></div></form></section>`:'';
 const rows=targets.map(t=>{
  const label={pending:'等待查找',matched:'已匹配',needs_choice:'请选择对应职位',not_found:'本轮未找到',closed:'原岗位已关闭',failed:'查找未完成'}[t.state]||t.state;
  return `<section class="boss-favorite-target" aria-label="清单第 ${t.position} 项"><header><div><span class="tip">${t.position}</span> <strong>${esc(t.title||'按原始链接定位')}</strong><small>${esc([t.company,t.city].filter(Boolean).join(' · '))}</small></div><span class="pill">${esc(label)}</span></header>
   ${t.error?`<p class="error">${esc(t.error)}</p>`:''}
   ${t.candidates.length?`<div class="boss-favorite-candidates">${t.candidates.map(c=>{
    const favorite={not_checked:'未执行收藏',saved:'本次操作已收藏',already_saved:'读取时已收藏',failed:'收藏未完成',unknown:'收藏结果待确认'}[c.favorite_status]||'未执行收藏';
    const availability={active:'本次已读取',closed:'已关闭',unread:'待读取完整详情',failed:'读取失败'}[c.availability]||'待读取';
    return `<label class="boss-favorite-candidate${c.selected?' is-selected':''}"><input type="checkbox" data-boss-favorite-choice="${c.id}" ${c.selected?'checked':''} ${active||s.busy||c.availability!=='active'?'disabled':''} aria-label="选择 ${esc(c.company+' · '+c.title)}">
     <div><strong>${esc(c.company||'企业未读取')} · ${esc(c.title||'岗位未读取')}</strong><small>${esc([c.city,c.salary].filter(Boolean).join(' · ')||'城市、薪资未读取')}</small>
      <p class="tip">${esc(c.match_reason||'原始岗位链接')} · ${esc(availability)}${c.checked_at?' · '+esc(c.checked_at):''}</p>
      <p class="${c.favorite_status==='unknown'||c.favorite_status==='failed'?'error':'tip'}">${esc(favorite)}${c.favorite_at&&['saved','already_saved','unknown'].includes(c.favorite_status)?' · '+esc(c.favorite_at):''}${c.error?'：'+esc(c.error):''}</p>
     </div><a href="${esc(secureLink(c.url))}" target="_blank" rel="noopener noreferrer">原始岗位 ↗</a></label>`;
   }).join('')}</div>`:'<p class="tip">'+esc(t.state==='pending'?'点击查找，读取 BOSS 当前结果。':t.state==='not_found'?'本轮未找到对应岗位，可以调整清单后再查找；不代表该企业没有招聘。':'尚无可收藏的候选，清单内容已保留。')+'</p>'}
   <div class="page-toolbar"><button type="button" class="btn btn-small" data-boss-favorite="retry" data-target="${t.id}" ${active||s.busy?'disabled':''}>重新查找</button>
   ${t.company&&t.state==='closed'?`<button type="button" class="btn btn-small" data-boss-favorite="similar" data-target="${t.id}" ${active||s.busy?'disabled':''}>查找同公司类似岗位</button>`:''}
   ${t.source_url?`<a href="${esc(secureLink(t.source_url))}" target="_blank" rel="noopener noreferrer">清单原始链接 ↗</a>`:''}</div></section>`;
 }).join('');
 return head('BOSS 岗位收藏','导入清单，找到当前职位，再集中收藏。')+
  `<div class="page-toolbar"><button class="btn btn-small" data-boss-favorite="back" ${s.busy?'disabled':''}>${s.returnJob?'返回原岗位':'返回工作台'}</button>
   <label class="boss-favorite-batch-picker">已保存清单<select id="boss-favorite-batch" ${s.busy?'disabled':''}>${s.batches.length?opt(s.batches.map(b=>[b.id,b.name+'（'+b.total+' 条）']),batch?.id):'<option value="">尚无清单</option>'}</select></label>
   <button class="btn btn-small" data-boss-favorite="new" ${s.busy?'disabled':''}>新建清单</button><button class="btn btn-small" data-boss-favorite="refresh" ${s.busy?'disabled':''}>刷新进度</button></div>
  ${s.error?`<div class="notice" role="alert">${esc(s.error)}</div>`:''}${input}
  ${batch&&!s.creating?`<section class="boss-favorite-summary"><div><h2>${esc(batch.name)}</h2><p class="tip">BOSS 直聘 · ${esc(batch.city)} · ${batch.browser_mode==='chrome_use'?'当前 Chrome':'独立采集窗口'} · ${targets.length} 条清单</p></div>
    ${d.run?`<div role="status"><strong>${esc(waiting?(phase==='login_required'?'请在浏览器完成登录':'请在浏览器完成验证'):status[d.run.status]||d.run.status)}</strong>
    <p class="tip">${esc(progress.message||'任务已保存')}${active&&progress.total?' · '+Number(progress.processed||0)+' / '+Number(progress.total):''}</p>
    ${d.run.error?`<p class="error">${esc(d.run.error)}</p>`:''}${progress.cleanup_message?`<p class="tip">${esc(progress.cleanup_message)}</p>`:''}</div>`:''}</section>
   <div class="boss-favorite-actions" role="group" aria-label="清单批量操作"><span>已选 <strong>${chosenCount}</strong> 个岗位</span>
    ${active?`${waiting?'<button class="btn btn-primary" data-boss-favorite="resume">我已处理，继续</button>':''}<button class="btn" data-boss-favorite="stop" ${s.busy||d.run.status==='stopping'?'disabled':''}>停止，保留进度</button>`:
     `<button class="btn" data-boss-favorite="match" ${s.busy||!pending?'disabled':''}>查找剩余 ${pending} 条</button>
      <button class="btn btn-small" data-boss-favorite="select-all" ${s.busy||!candidates.some(c=>c.availability==='active')?'disabled':''}>全选已读取候选</button>
      <button class="btn btn-small" data-boss-favorite="clear" ${s.busy||!chosen.length?'disabled':''}>清空选择</button>
      <button class="btn btn-primary" data-boss-favorite="favorite" ${s.busy||!chosen.length?'disabled':''}>收藏所选岗位到 BOSS</button>`}</div>
   <p class="tip">每轮最多 12 页搜索、60 次详情读取；剩余条目可继续。收藏前重新读取状态，已收藏的跳过；失败或待确认可重试。${active?'处理期间先保留当前选择。':''}</p>
   <div class="boss-favorite-targets">${rows}</div>`:''}`;
}
function bossFavoritePreview(){
 const p=bossFavorites.preview;
 if(!p)return '<p class="tip">输入或文件变化后需要重新预览，防止导入上一次内容。</p>';
 return `<div class="notice"><strong>识别 ${p.items.length} 条 · 重复 ${p.duplicates.length} 条 · 格式问题 ${p.errors.length} 处</strong>
  ${p.ignored_columns?.length?`<p class="tip">未使用的列：${esc(p.ignored_columns.join('、'))}</p>`:''}
  ${p.errors.map(e=>`<p class="error">第 ${e.line} 行：${esc(e.message)}</p>`).join('')}
  ${p.duplicates.map(e=>`<p class="tip">第 ${e.line} 行：${esc(e.title||'同一链接')}，重复项将合并</p>`).join('')}
  <details><summary>查看识别内容</summary><div class="row-list">${p.items.map(row=>`<div class="rowline"><div><strong>${esc(row.title||'原始链接')}</strong><small>${esc([row.company,row.city,row.url].filter(Boolean).join(' · '))}</small></div></div>`).join('')}</div></details></div>`;
}
function renderBossFavorites(){if(state.page==='bossFavorites')render()}
async function openBossFavorites(batchId,returnJob=null){
 const s=bossFavorites,generation=++s.sequence;s.error='';clearTimeout(s.timer);
 if(!canLeaveModal())return;
 if($('#modal')?.open){if(typeof clearWorkspaceReturn==='function')clearWorkspaceReturn();modalReturnJob=null;await closeModal(true)}
 s.returnJob=returnJob;navigate('bossFavorites');
 try{
  const [options,batches]=await Promise.all([api('/boss-favorites/options'),api('/boss-favorites/batches')]);
  if(generation!==s.sequence)return;
  s.options=options;s.batches=batches;
  const id=batchId||s.current?.batch.id||batches[0]?.id;
  const current=id?await api('/boss-favorites/batches/'+id):null;
  if(generation!==s.sequence)return;
  s.creating=!id;s.current=current;
  renderBossFavorites();if(s.current?.run)await refreshBossFavorites();
 }catch(error){s.error=error.message;renderBossFavorites()}
}
async function refreshBossFavorites(){
 const s=bossFavorites,current=s.current;if(!current)return;
 const id=current.batch.id,generation=s.sequence;clearTimeout(s.timer);
 try{
  const data=current.run&&bossFavoriteActive()?await api('/boss-favorites/runs/'+current.run.task_id+'/refresh',{method:'POST'}):await api('/boss-favorites/batches/'+id);
  if(s.sequence!==generation||s.current?.batch.id!==id)return;
  s.current=data;s.error='';renderBossFavorites();
 }catch(error){if(s.current?.batch.id===id){s.error=error.message;renderBossFavorites()}}
 if(state.page==='bossFavorites'&&bossFavoriteActive())s.timer=setTimeout(refreshBossFavorites,3000);
}
async function startBossFavoriteRun(action,targetIds=[],similar=false){
 const s=bossFavorites,id=s.current.batch.id,generation=s.sequence,body={action,target_ids:targetIds,similar};
 const signature=JSON.stringify([id,body]);
 if(s.request?.signature!==signature)s.request={signature,request_id:crypto.randomUUID().replaceAll('-','')};
 try{
  const result=await api('/boss-favorites/batches/'+id+'/runs',{method:'POST',body:JSON.stringify({...body,request_id:s.request.request_id})});
  if(generation!==s.sequence||s.current?.batch.id!==id)return;
  s.request=null;s.current.run=result.run;await refreshBossFavorites();
 }catch(error){
  try{
   const stored=await api('/boss-favorites/batches/'+id);
   if(generation!==s.sequence||s.current?.batch.id!==id)return;
   if(stored.run?.task_id===s.request?.request_id){
    s.current=stored;
    if(['starting','pending','running','stopping','unknown'].includes(stored.run.status)){s.request=null;await refreshBossFavorites();return}
    s.request=null;
   }
  }catch{}
  throw error;
 }
}
async function changeBossFavoriteSelection(ids){
 const s=bossFavorites,id=s.current.batch.id,generation=s.sequence;
 const current=await api('/boss-favorites/batches/'+id+'/selection',{method:'PUT',body:JSON.stringify({ids})});
 if(generation===s.sequence&&s.current?.batch.id===id)s.current=current;
}
function invalidateBossFavoritePreview(){
 bossFavorites.inputRevision++;bossFavorites.preview=null;
 const box=$('#boss-favorite-preview');if(box)box.innerHTML=bossFavoritePreview();
 const button=$('[data-boss-favorite="create"]');if(button)button.disabled=true;
}
if(typeof document!=='undefined'){
 document.addEventListener('input',event=>{
  const input=event.target;if(!input.closest('#boss-favorite-import-form'))return;
  if(['name','text'].includes(input.name)){bossFavorites[input.name]=input.value;if(input.name==='text'){bossFavorites.format='text';invalidateBossFavoritePreview()}}
 });
 document.addEventListener('change',async event=>{
  const input=event.target,s=bossFavorites;
  if(!input.matches('#boss-favorite-batch,#boss-favorite-file,[data-boss-favorite-choice]')&&
     !(input.closest('#boss-favorite-import-form')&&['city','browser_mode','internship_only'].includes(input.name)))return;
  try{
   if(input.id==='boss-favorite-batch'){
    const generation=++s.sequence,current=await api('/boss-favorites/batches/'+input.value);
    if(generation!==s.sequence)return;
    s.creating=false;s.current=current;renderBossFavorites();await refreshBossFavorites();
   }
   else if(input.closest('#boss-favorite-import-form')&&['city','browser_mode','internship_only'].includes(input.name)){
    s[input.name]=input.type==='checkbox'?input.checked:input.value;if(input.name==='city')invalidateBossFavoritePreview();
   }else if(input.id==='boss-favorite-file'&&input.files[0]){
    const file=input.files[0];invalidateBossFavoritePreview();
    const revision=s.inputRevision;
    if(!/\.(csv|txt)$/i.test(file.name)||file.size>262144)throw new Error('请选择不超过 256 KiB 的 CSV / TXT；Excel 请另存为 CSV UTF-8');
    let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer())}catch{throw new Error('文件不是 UTF-8 编码，请另存为 CSV UTF-8 或直接粘贴内容')}
    if(revision!==s.inputRevision)return;
    s.text=text;s.format=/\.csv$/i.test(file.name)?'csv':'txt';renderBossFavorites();
   }else if(input.matches('[data-boss-favorite-choice]')){
    if(s.busy||bossFavoriteActive())return;
    const ids=s.current.targets.flatMap(t=>t.candidates).filter(c=>c.selected&&c.availability==='active').map(c=>c.id),id=Number(input.dataset.bossFavoriteChoice);
    s.busy='selection';await changeBossFavoriteSelection(input.checked?[...new Set([...ids,id])]:ids.filter(x=>x!==id));
   }
  }catch(error){s.error=error.message}
  finally{if(input.matches('[data-boss-favorite-choice]'))s.busy='';renderBossFavorites()}
 });
 document.addEventListener('submit',async event=>{
  if(event.target.id!=='boss-favorite-import-form')return;event.preventDefault();
  const s=bossFavorites;if(s.busy)return;s.busy='preview';s.error='';renderBossFavorites();
  const revision=s.inputRevision;
  try{const preview=await api('/boss-favorites/preview',{method:'POST',body:JSON.stringify({text:s.text,format:s.format,city:s.city})});if(revision===s.inputRevision)s.preview=preview}catch(error){s.error=error.message}
  finally{s.busy='';renderBossFavorites()}
 });
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-boss-favorite]');if(!button||button.disabled)return;event.preventDefault();
  const s=bossFavorites,action=button.dataset.bossFavorite;
  if(action==='open'){await openBossFavorites();return}
  if(action==='back'){clearTimeout(s.timer);navigate(s.returnJob?'jobs':'overview');if(s.returnJob)await jobDetail(s.returnJob);return}
  if(action==='new'){s.creating=true;s.error='';s.preview=null;renderBossFavorites();return}
  if(action==='file'){$('#boss-favorite-file').click();return}
  if(s.busy)return;s.busy=action;s.error='';renderBossFavorites();
  try{
   if(action==='create'){
    const data=await api('/boss-favorites/batches',{method:'POST',body:JSON.stringify({name:s.name,text:s.text,format:s.format,city:s.city,browser_mode:s.browser_mode,internship_only:s.internship_only,preview_hash:s.preview.preview_hash})});
    s.current=data;s.creating=false;s.batches=await api('/boss-favorites/batches');await startBossFavoriteRun('match');
   }else if(action==='match'||action==='retry'||action==='similar')await startBossFavoriteRun('match',button.dataset.target?[Number(button.dataset.target)]:[],action==='similar');
   else if(action==='favorite')await startBossFavoriteRun('favorite');
   else if(action==='select-all'||action==='clear')await changeBossFavoriteSelection(action==='clear'?[]:s.current.targets.flatMap(t=>t.candidates).filter(c=>c.availability==='active').map(c=>c.id));
   else if(action==='stop'||action==='resume'){s.current=await api('/boss-favorites/runs/'+s.current.run.task_id+'/'+action,{method:'POST'});await refreshBossFavorites()}
   else if(action==='refresh')await refreshBossFavorites();
   else if(action==='job'){
    if(!canLeaveModal())return;
    const data=await api('/boss-favorites/from-job/'+button.dataset.job,{method:'POST'});await openBossFavorites(data.batch.id,Number(button.dataset.job));
   }
  }catch(error){s.error=error.message}
  finally{s.busy='';renderBossFavorites()}
 });
}
