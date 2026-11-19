/* Company lists use employer identities, independently of employee-count filters. */
const companyListsView={id:null,detail:null,loading:false,deleting:false,error:'',query:'',status:'',page:1,revision:0,anchor:null};
let companyListMatchRequest=0;
function companyListAnchor(entryId=null){
 const content=document.getElementById('content'),rows=[...document.querySelectorAll('[data-list-entry]')],row=rows.find(x=>String(x.dataset.listEntry)===String(entryId));
 return {scroll:content?.scrollTop||0,id:entryId,index:row?rows.indexOf(row):0,offset:row?row.getBoundingClientRect().top-content.getBoundingClientRect().top:null};
}
function restoreCompanyListAnchor(anchor){
 if(!anchor)return;const content=document.getElementById('content');if(!content)return;
 content.scrollTop=anchor.scroll;
 const rows=[...content.querySelectorAll('[data-list-entry]')],row=rows.find(x=>String(x.dataset.listEntry)===String(anchor.id))||rows[Math.min(anchor.index,rows.length-1)];
 if(row&&anchor.offset!=null)content.scrollTop+=row.getBoundingClientRect().top-content.getBoundingClientRect().top-anchor.offset;
}
function companyListBadge(list){return list.builtin?'<span class="tag">内置示例'+(list.customized?' · 已自定义':'')+'</span>':'<span class="tag">我的名单</span>';}
function companyListDownloads(){return '<div class="page-toolbar company-list-downloads"><a class="btn btn-small" href="/api/company-lists/builtin.csv" download="synthetic-employers-v1.csv">下载原始示例 CSV</a><a class="btn btn-small" href="/api/company-lists/template.csv" download="company-list-template.csv">下载空白模板</a></div>';}
function companyListJobFilter(){
 const lists=state.companyListData?.lists||[],selected=String(state.filters.company_list_id||'');
 const missing=selected&&!lists.some(x=>String(x.id)===selected);
 const company=state.companies.find(c=>String(c.id)===String(state.filters.company_id));
 return '<label class="company-list-job-filter">企业名单<select id="filter-company-list" class="select" aria-label="企业名单"><option value="">不限名单</option>'+
  opt(lists.map(x=>[String(x.id),x.name+'（本库 '+x.job_count+' 岗）']),selected)+(missing?'<option selected value="'+esc(selected)+'">名单已删除</option>':'')+'</select></label>'+
  (selected?'<button class="btn btn-small" data-clear-company-list>清除名单筛选</button>':'')+
  (company?'<span class="company-list-scope">招聘主体：'+esc(company.name)+' <button class="btn btn-small" data-clear-list-company>清除企业条件</button></span>':'');
}
function companyListJobMatch(job){
 if(state.filters.company_id&&String(job.company_id)!==String(state.filters.company_id))return false;
 const id=state.filters.company_list_id;if(!id)return true;
 const list=state.companyListData?.lists?.find(x=>String(x.id)===String(id));
 return Boolean(list?.company_ids.includes(job.company_id));
}
async function refreshCompanyLists(id=companyListsView.id){
 const revision=++companyListsView.revision;
 companyListsView.loading=true;companyListsView.error='';if(state.page==='companyLists')render();
 try{
  const summary=await api('/company-lists');
  if(revision!==companyListsView.revision)return;
  state.companyListData=summary;
  const selected=summary.lists.find(x=>String(x.id)===String(id))||summary.lists[0];
  const params=new URLSearchParams({q:companyListsView.query,status:companyListsView.status,page:companyListsView.page,page_size:20});
  const detail=selected?await api('/company-lists/'+selected.id+'?'+params):null;
  if(revision!==companyListsView.revision)return;
  companyListsView.id=selected?.id||null;companyListsView.detail=detail;companyListsView.page=detail?.page||1;
 }catch(error){if(revision===companyListsView.revision)companyListsView.error=error.message;}
 finally{if(revision===companyListsView.revision){companyListsView.loading=false;if(state.page==='companyLists')render();}}
}
function companyListsPage(){
 const data=state.companyListData||{lists:[],builtin_id:null},view=companyListsView,list=view.detail;
 if(view.deleting)return head('企业名单','')+'<p role="status">正在删除名单并刷新…</p>';
 const picker='<label class="list-picker-label">切换名单<select id="company-list-picker" class="select">'+opt(data.lists.map(x=>[x.id,x.name]),view.id)+'</select></label><button class="btn btn-small" data-page="companies">企业观察库</button>';
 const title='<div class="company-list-title">'+head(list?.name||'企业名单',list?.builtin?(list.publisher+' · '+list.published_on.slice(0,4)+' · 内置示例'+(list.customized?' · 已自定义':'')):'我的企业名单',picker)+'</div>';
 if(view.error)return title+'<div class="notice" role="alert">读取失败：'+esc(view.error)+' <button class="btn" data-company-list-refresh>重新加载</button></div>';
 if(view.loading)return title+'<p role="status">正在读取当前页与匹配结果…</p>';
 if(!list)return title+empty('还没有企业名单','可粘贴企业名称、导入 CSV/TXT，或恢复内置示例。','<button class="btn btn-primary" data-company-list-new>新建 / 导入名单</button><button class="btn" data-company-list-restore>恢复内置示例</button>');
 const meta=list.builtin?'<p>'+esc(list.order_note)+'</p>'+(list.customized?'<p>名单已自定义。下载示例 CSV 可取得原始合成名单。</p>':''):'<p>来源资料可在各条目的详细资料中查看。</p>';
 const rows=list.entries,total=list.filtered_count,pages=list.pages,start=(list.page-1)*list.page_size;
 return title+'<section class="company-list-heading"><div class="stats company-list-stats">'+stat('企业',list.entry_count,'')+stat('已关联',list.matched_count,'')+stat('本库岗位',list.job_count,'')+'</div>'+
 '<div class="page-toolbar company-list-actions"><button class="btn '+(list.entry_count?'btn-primary':'')+'" data-company-list-jobs="'+list.id+'">查看本库岗位（'+list.job_count+'）</button><button class="btn" data-company-list-append>追加企业 / 导入</button>'+
 '<details class="list-manage"><summary>管理名单</summary><div class="page-toolbar"><button class="btn btn-small" data-company-list-new>新建 / 导入名单</button><button class="btn btn-small" data-company-list-rename>重命名</button><button class="btn btn-small" data-company-list-refresh>刷新匹配结果</button><button class="btn btn-small" data-company-list-delete>删除名单</button>'+(!data.builtin_id?'<button class="btn btn-small" data-company-list-restore>恢复内置示例</button>':'')+'</div></details></div>'+
 '<details class="company-list-source"><summary>来源与下载'+(list.customized?' · 已自定义':'')+'</summary>'+meta+companyListDownloads()+'<p>CSV 带 UTF-8 BOM，适合 Windows Excel。只填“企业名称”也能导入；支持 CSV/TXT 或复制一列名称粘贴。</p></details></section>'+
 '<form id="company-list-filter" class="filter-bar"><input name="query" class="search" type="search" placeholder="搜索企业、品牌或地区" aria-label="搜索名单条目" value="'+esc(view.query)+'"><button class="btn btn-small" type="submit">筛选</button><label>匹配状态<select name="status" class="select" id="company-list-status">'+opt([['','全部 '+list.entry_count],['matched','已匹配 '+list.matched_count],['pending','待选择 '+list.pending_count],['unmatched','本库暂未匹配 '+list.unmatched_count],['cancelled','已取消关联 '+list.cancelled_count]],view.status)+'</select></label></form>'+
 (rows.length?'<div class="company-list-entries">'+rows.map(companyListEntryRow).join('')+'</div>':list.entry_count===0?empty('名单还是空的','粘贴企业名称或导入文件，开始整理自己的目标企业。','<button class="btn btn-primary" data-company-list-append>粘贴企业名称 / 导入文件</button>'):empty('没有符合条件的名单条目','可清除条目搜索与匹配状态。','<button class="btn" data-company-list-clear-query>清除条目筛选</button>'))+
 (total?'<div class="job-list-pagination"><span>显示 '+(start+1)+'–'+Math.min(start+list.page_size,total)+'，共 '+total+' 家</span><div class="page-toolbar"><button class="btn btn-small" data-company-list-page="'+(list.page-1)+'"'+(list.page===1?' disabled':'')+'>上一页</button><span>'+list.page+' / '+pages+'</span><button class="btn btn-small" data-company-list-page="'+(list.page+1)+'"'+(list.page>=pages?' disabled':'')+'>下一页</button></div></div>':'');
}
function companyListEntryRow(entry){
 const labels={matched:'已匹配',pending:'待选择',unmatched:'本库暂未匹配',cancelled:'已取消关联'};
 const matches=entry.matches.map(c=>'<div class="list-employer"><span>'+esc(c.name)+'</span>'+(c.job_count?'<button class="table-link" data-company-list-jobs="'+companyListsView.id+'" data-list-company="'+c.id+'">查看 '+c.job_count+' 个岗位 ↗</button>':'')+'</div>').join('');
 const source=companyListsView.detail.builtin&&entry.origin!=='user'?'':(entry.publisher?'<p>'+esc(entry.publisher)+' '+esc(entry.published_on)+'</p>':'')+(entry.source_url?'<a href="'+esc(secureLink(entry.source_url))+'" target="_blank" rel="noopener noreferrer">条目来源 ↗</a>':'')+(entry.report_url?' · <a href="'+esc(secureLink(entry.report_url))+'" target="_blank" rel="noopener noreferrer">报告 ↗</a>':'');
 return '<article class="company-list-entry" data-list-entry="'+entry.id+'"><div class="list-identity"><h3>'+esc(entry.company_name)+(companyListsView.detail.builtin&&entry.origin==='user'?' <span class="tag">自行添加</span>':'')+'</h3>'+(entry.business_brands?'<p class="list-brands">'+esc(entry.business_brands)+'</p>':'')+
 '<details class="list-entry-details"><summary>详细资料与匹配线索</summary><p>地区：'+esc(entry.region||'未填写')+'</p>'+(entry.business_brands?'<p>主要业务和品牌：'+esc(entry.business_brands)+'</p>':'')+(entry.aliases.length?'<p>名单别名：'+esc(entry.aliases.join('、'))+'</p>':'')+source+entry.candidates.map(c=>'<p>'+esc(c.name)+'：'+esc(c.reason)+'</p>').join('')+'</details></div>'+
 '<div class="company-list-matching"><span class="list-state list-state-'+entry.match_status+'">'+labels[entry.match_status]+'</span>'+matches+(!entry.job_count?'<small>本库尚未收录匹配岗位</small>':'')+(entry.match_method==='remembered'?'<small>沿用已保存的对应关系</small>':'')+'</div>'+
 '<div class="list-entry-actions"><button class="btn btn-small" data-company-list-match="'+entry.id+'">'+(entry.matches.length?'调整关联':'选择对应企业')+'</button><button class="btn btn-small btn-ghost" data-company-list-remove="'+entry.id+'">移除条目</button></div></article>';
}

function openCompanyListImport(target=null){
 companyListsView.anchor=companyListAnchor();
 modal(target?'追加企业到「'+target.name+'」':'新建 / 导入企业名单',
  '<form id="company-list-import-form" data-target="'+(target?.id||'')+'">'+(!target?field('name','名单名称 *','例如 我的制造业目标企业','text',true):'')+
  '<label class="field">输入方式<select name="mode"><option value="text">粘贴企业名称（每行一家）</option><option value="file">导入文件（CSV / TXT）</option></select></label>'+
  '<div data-list-input-mode="text"><label class="field">企业名称<textarea name="content" rows="7" placeholder="每行一家，也可从 Excel 复制一列粘贴"></textarea></label></div>'+
  '<div data-list-input-mode="file" hidden><label class="field">选择 UTF-8 CSV 或 TXT<input name="file" type="file" accept=".csv,.txt,text/csv,text/plain"></label><p class="tip">支持 Excel 导出的 CSV UTF-8（逗号分隔），不支持 XLSX。只填“企业名称”即可；别名、业务品牌、地区、来源均选填。</p></div>'+
  '<div data-list-import-error class="notice" role="alert" hidden></div><div data-list-import-preview></div><div class="form-actions company-list-import-actions">'+
  '<button type="button" class="btn" data-company-list-preview>预览识别结果</button><button type="submit" class="btn btn-primary" disabled>确认导入</button>'+(!target?'<button type="button" class="btn btn-ghost" data-company-list-empty>只创建空名单</button>':'')+'</div></form>',true);
 const form=$('#company-list-import-form');form.dataset.revision='0';
}
function invalidateCompanyListPreview(form){
 form.dataset.revision=String(Number(form.dataset.revision||0)+1);form.companyListPreview=null;
 form.querySelector('[type=submit]').disabled=true;form.querySelector('[data-list-import-preview]').innerHTML='';
 const box=form.querySelector('[data-list-import-error]');box.hidden=true;
 const empty=form.querySelector('[data-company-list-empty]');if(empty)empty.hidden=Boolean(form.elements.content.value.trim()||form.elements.file.files.length);
}
function companyListImportError(form,message){const box=form.querySelector('[data-list-import-error]');box.textContent=message;box.hidden=false;}
async function companyListImportInput(form){
 const mode=form.elements.mode.value;
 let format='text',content=form.elements.content.value;
 if(mode==='file'){
  const file=form.elements.file.files[0];if(!file)throw new Error('请选择 CSV 或 TXT 文件，或切换到粘贴企业名称');
  if(!/\.(csv|txt)$/i.test(file.name))throw new Error('支持 CSV、TXT；XLSX 请在 Excel 中另存为 CSV UTF-8，或复制一列名称粘贴');
  if(file.size>2_000_000)throw new Error('文件超过 2 MB，请分批导入');
  try{content=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}
  catch{throw new Error('文件不是 UTF-8 编码。请在 Excel 中另存为 CSV UTF-8，或直接复制企业名称一列粘贴');}
  format=/\.csv$/i.test(file.name)?'csv':'text';
 }
 return {name:form.elements.name?.value||'',target_id:form.dataset.target?Number(form.dataset.target):null,format,content};
}
async function previewCompanyListImport(form){
 const revision=Number(form.dataset.revision),button=form.querySelector('[data-company-list-preview]');button.disabled=true;
 try{
  const input=await companyListImportInput(form);if(revision!==Number(form.dataset.revision))return;
  const result=await api('/company-lists/import/preview',{method:'POST',body:JSON.stringify(input)});
  if(!form.isConnected||revision!==Number(form.dataset.revision))return;
  form.companyListPreview={input,token:result.preview_token,revision,valid:result.valid};
  const html='<p role="status"><strong>识别 '+result.total+' 行：可新增 '+result.new_count+' 家，重复跳过 '+result.duplicate_count+' 家。</strong></p>'+
   (result.errors.length?'<div class="notice" role="alert">'+result.errors.map(esc).join('<br>')+'<p>请修改后重新预览。本次不会部分保存。</p></div>':'')+
   result.warnings.map(w=>'<p class="tip">'+esc(w)+'</p>').join('')+
   '<div class="company-list-preview-rows">'+result.rows.map(r=>'<div class="company-list-preview-row '+r.status+'"><span>第 '+r.line+' 行 · '+esc(r.company_name||'名称缺失')+'</span><small>'+esc(r.message)+'</small><small>'+esc([r.aliases?.length?'别名：'+r.aliases.join('、'):'',r.business_brands,r.region,r.publisher,r.published_on,r.source_url,r.report_url].filter(Boolean).join(' · '))+'</small></div>').join('')+'</div>';
  form.querySelector('[data-list-import-preview]').innerHTML=html;form.querySelector('[type=submit]').disabled=!result.valid;
  form.querySelector('[data-list-import-error]').hidden=true;
 }catch(error){if(revision===Number(form.dataset.revision))companyListImportError(form,error.message+'；输入已保留，可修改后重新预览。');}
 finally{button.disabled=false;}
}
async function finishCompanyListWrite(id,message){
 const changed=String(companyListsView.id)!==String(id),anchor=changed?null:companyListsView.anchor||companyListAnchor();companyListsView.anchor=null;
 if(changed){companyListsView.query='';companyListsView.status='';companyListsView.page=1;}
 companyListsView.id=id;companyListsView.detail=null;companyListsView.loading=true;
 if(state.page==='companyLists')render();
 await closeModal(true);
 let reloadFailed=false;try{await load();}catch{reloadFailed=true;}
 await refreshCompanyLists(id);restoreCompanyListAnchor(anchor);toast(message+(reloadFailed?'；工作空间刷新失败，可重新加载。':''));
}
async function commitCompanyListImport(form){
 const preview=form.companyListPreview;
 if(!preview?.valid||preview.revision!==Number(form.dataset.revision))return companyListImportError(form,'内容已变化或有格式问题，请先重新预览');
 if(!beginFormWrite(form))return;
 try{
  const current=await companyListImportInput(form);
  if(JSON.stringify(current)!==JSON.stringify(preview.input)){invalidateCompanyListPreview(form);throw new Error('文件或文字已变化，请重新预览');}
  const result=await api('/company-lists/import/commit',{method:'POST',body:JSON.stringify({...current,preview_token:preview.token})});
  await finishCompanyListWrite(result.id,'已保存 '+result.added+' 家名单企业'+(result.duplicates_skipped?'，跳过 '+result.duplicates_skipped+' 家重复项':''));
 }catch(error){companyListImportError(form,error.message+'。输入已保留，可修改或重试。');}
 finally{endFormWrite(form);form.querySelector('[type=submit]').disabled=!form.companyListPreview?.valid;}
}
async function openCompanyListMatches(entry){
 const listId=companyListsView.id,revision=companyListsView.revision,modalEpoch=modalRevision,navEpoch=navigationRevision,request=++companyListMatchRequest;
 const anchor=companyListAnchor(entry.id),companies=await api('/companies');
 if(request!==companyListMatchRequest||state.page!=='companyLists'||String(companyListsView.id)!==String(listId)||revision!==companyListsView.revision||modalEpoch!==modalRevision||navEpoch!==navigationRevision)return;
 companyListsView.anchor=anchor;
 const selected=new Set(entry.matches.map(x=>x.id)),hints=new Map(entry.candidates.map(x=>[x.id,x.reason]));
 const rows=[...companies].sort((a,b)=>Number(selected.has(b.id)||hints.has(b.id))-Number(selected.has(a.id)||hints.has(a.id))||a.name.localeCompare(b.name,'zh-CN'));
 modal('选择「'+entry.company_name+'」对应的招聘主体','<form id="company-list-match-form" data-entry="'+entry.id+'" data-list="'+listId+'">'+
  '<p class="tip">企业名称一致且唯一时可自动对应。品牌、近似名称、集团关系只作为候选；可选择多个实际招聘主体，也可不选以取消关联。</p><p class="tip">保存后，其他名单中的同名企业会沿用这个对应关系；不会合并或重命名企业档案。</p>'+
  '<label class="field">查找本库企业<input type="search" data-list-match-search placeholder="输入企业名称"></label><div class="company-list-match-options">'+
  rows.map(c=>'<label class="company-list-match-option"><input type="checkbox" name="company_ids" value="'+c.id+'"'+(selected.has(c.id)?' checked':'')+'><span>'+esc(c.name)+'<small>'+c.job_count+' 个本库岗位'+(hints.has(c.id)?' · '+esc(hints.get(c.id)):'')+'</small></span></label>').join('')+
  (!rows.length?'<p class="tip">本库尚无企业档案。名单条目会保留，后续录入企业再回来关联。</p>':'')+'</div><p data-list-match-count class="tip">已选 '+selected.size+' 家</p>'+
  '<div class="notice" data-list-match-error role="alert" hidden></div><p class="tip">保存后会用于其他名单中的同名条目。</p><div class="form-actions"><button type="submit" class="btn btn-primary">保存对应关系</button><button type="button" class="btn" data-list-match-clear>清空勾选</button><button type="button" class="btn" data-action="close">取消</button></div></form>',true);
}
if(typeof document!=='undefined'){
document.addEventListener('input',event=>{
 const form=event.target.closest('#company-list-import-form');if(form && event.target.matches('textarea,input:not([type=file])'))invalidateCompanyListPreview(form);
 if(event.target.matches('[data-list-match-search]')){
  const q=event.target.value.trim().toLowerCase();document.querySelectorAll('.company-list-match-option').forEach(label=>{label.hidden=q&&!label.textContent.toLowerCase().includes(q);});
 }
});
document.addEventListener('change',event=>{
 const form=event.target.closest('#company-list-import-form');
 if(form && event.target.matches('select,input[type=file]')){invalidateCompanyListPreview(form);form.querySelectorAll('[data-list-input-mode]').forEach(el=>el.hidden=el.dataset.listInputMode!==form.elements.mode.value);}
 if(event.target.name==='company_ids'&&event.target.closest('#company-list-match-form'))$('#company-list-match-form [data-list-match-count]').textContent='已选 '+$('#company-list-match-form').querySelectorAll('[name=company_ids]:checked').length+' 家';
 if(event.target.id==='company-list-picker'){companyListsView.page=1;companyListsView.query='';companyListsView.status='';companyListsView.anchor=null;refreshCompanyLists(Number(event.target.value));}
 if(event.target.id==='company-list-status'){companyListsView.status=event.target.value;companyListsView.query=$('#company-list-filter [name=query]').value.trim();companyListsView.page=1;refreshCompanyLists();}
 if(event.target.id==='filter-company-list'){state.filters.company_list_id=event.target.value;delete state.filters.company_id;resetJobSelection();render();}
});
document.addEventListener('submit',async event=>{
 const form=event.target;
 if(form.id==='company-list-filter'){event.preventDefault();companyListsView.query=form.elements.query.value.trim();companyListsView.page=1;await refreshCompanyLists();return;}
 if(form.id==='company-list-import-form'){event.preventDefault();await commitCompanyListImport(form);return;}
 if(form.id==='company-list-rename-form'){
  event.preventDefault();if(!beginFormWrite(form))return;
  try{await api('/company-lists/'+form.dataset.list,{method:'PATCH',body:JSON.stringify({name:form.elements.name.value})});await finishCompanyListWrite(Number(form.dataset.list),'名单已重命名');}
  catch(error){form.querySelector('[role=alert]').textContent=error.message;}
  finally{endFormWrite(form);}return;
 }
 if(form.id==='company-list-match-form'){
  event.preventDefault();if(!beginFormWrite(form))return;
  try{await api('/company-lists/'+form.dataset.list+'/entries/'+form.dataset.entry+'/matches',{method:'PUT',body:JSON.stringify({company_ids:[...form.querySelectorAll('[name=company_ids]:checked')].map(x=>Number(x.value))})});await finishCompanyListWrite(Number(form.dataset.list),'对应关系已保存');}
  catch(error){const box=form.querySelector('[role=alert]');box.textContent=error.message+'；选择已保留，可重试。';box.hidden=false;}
  finally{endFormWrite(form);}
 }
});
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-company-list-new],[data-company-list-example],[data-company-list-append],[data-company-list-preview],[data-company-list-empty],[data-company-list-refresh],[data-company-list-restore],[data-company-list-rename],[data-company-list-delete],[data-company-list-remove],[data-company-list-match],[data-company-list-jobs],[data-company-list-page],[data-company-list-clear-query],[data-clear-company-list],[data-clear-list-company],[data-list-match-clear]');
 if(!button||button.disabled)return;
 try{
  const list=companyListsView.detail;
  if(button.hasAttribute('data-company-list-new')){openCompanyListImport();return;}
  if(button.hasAttribute('data-company-list-example')){companyListsView.id=state.companyListData?.builtin_id||null;navigate('companyLists');return;}
  if(button.hasAttribute('data-company-list-append')){openCompanyListImport(list);return;}
  if(button.hasAttribute('data-company-list-preview')){await previewCompanyListImport(button.closest('form'));return;}
  if(button.hasAttribute('data-company-list-empty')){
   const form=button.closest('form');if(!form.elements.name.reportValidity()||!beginFormWrite(form))return;
   try{const r=await api('/company-lists',{method:'POST',body:JSON.stringify({name:form.elements.name.value})});await finishCompanyListWrite(r.id,'空名单已创建，可以追加企业');}
   catch(error){companyListImportError(form,error.message+'；输入已保留，可重试。');}finally{endFormWrite(form);form.querySelector('[type=submit]').disabled=!form.companyListPreview?.valid;}return;
  }
  if(button.hasAttribute('data-company-list-refresh')){await refreshCompanyLists();return;}
  if(button.hasAttribute('data-company-list-restore')){const r=await api('/company-lists/builtin/restore',{method:'POST'});await refreshCompanyLists(r.id);toast('内置示例已恢复');return;}
  if(button.hasAttribute('data-company-list-rename')){companyListsView.anchor=companyListAnchor();modal('重命名企业名单','<form id="company-list-rename-form" data-list="'+list.id+'">'+field('name','名单名称 *','','text',true)+'<p role="alert"></p>'+actions()+'</form>');$('#company-list-rename-form [name=name]').value=list.name;return;}
  if(button.hasAttribute('data-company-list-delete')){
   if(companyListsView.deleting||!confirm('删除「'+list.name+'」？只移除名单，不删除企业档案、岗位或投递记录。'+(list.builtin?'之后可恢复内置示例。':'')))return;
   companyListsView.deleting=true;render();
   try{
    await api('/company-lists/'+list.id,{method:'DELETE'});
    Object.assign(companyListsView,{id:null,detail:null,query:'',status:'',page:1,anchor:null});
    await refreshCompanyLists(null);
    toast('名单已删除，原有企业和岗位已保留');
   }finally{
    companyListsView.deleting=false;if(state.page==='companyLists')render();
   }
   return;
  }
  if(button.hasAttribute('data-company-list-remove')){const entry=list.entries.find(x=>x.id===Number(button.dataset.companyListRemove));if(!confirm('移除「'+entry.company_name+'」？条目的别名、地区和来源资料将一并移除；企业档案、岗位和投递记录保留。'))return;const anchor=companyListAnchor(entry.id);await api('/company-lists/'+list.id+'/entries/'+entry.id,{method:'DELETE'});await refreshCompanyLists(list.id);restoreCompanyListAnchor(anchor);toast('已从名单移除，原有企业和岗位已保留');return;}
  if(button.hasAttribute('data-company-list-match')){await openCompanyListMatches(list.entries.find(x=>x.id===Number(button.dataset.companyListMatch)));return;}
  if(button.hasAttribute('data-list-match-clear')){button.closest('form').querySelectorAll('[name=company_ids]').forEach(el=>el.checked=false);button.closest('form').querySelector('[data-list-match-count]').textContent='已选 0 家 · 点击保存后生效';return;}
  if(button.hasAttribute('data-company-list-jobs')){await load();navigate('jobs',{company_list_id:button.dataset.companyListJobs,...(button.dataset.listCompany?{company_id:button.dataset.listCompany}:{})});return;}
  if(button.hasAttribute('data-clear-company-list')){delete state.filters.company_list_id;delete state.filters.company_id;resetJobSelection();render();return;}
  if(button.hasAttribute('data-clear-list-company')){delete state.filters.company_id;resetJobSelection();render();return;}
  if(button.hasAttribute('data-company-list-page')){companyListsView.page=Number(button.dataset.companyListPage);await refreshCompanyLists();document.querySelector('.company-list-entries')?.scrollIntoView({block:'start'});return;}
  if(button.hasAttribute('data-company-list-clear-query')){companyListsView.query='';companyListsView.status='';companyListsView.page=1;await refreshCompanyLists();}
 }catch(error){toast('操作未完成：'+error.message+'。可以修改或重试。');}
});
}
