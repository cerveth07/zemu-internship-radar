/* One entry for GPT JSON/ZIP batches; each existing API still validates its own data. */
let unifiedGPTBusy=false, unifiedGPTPreview=null;
function chooseGPTFile(){
 if(unifiedGPTBusy)return;
 if(typeof canLeaveModal==='function'&&!canLeaveModal())return;
 document.querySelector('#gpt-json-file')?.click();
}
function recognizeGPTData(value){
 if(Array.isArray(value)&&value.length&&value.every(x=>x&&
    Number.isInteger(x.job_id)&&typeof x.priority==='string'))
  return {kind:'priority',label:'岗位优先级评价',route:'/jobs/priority-import',payload:{rows:value}};
 if(!value||typeof value!=='object'||Array.isArray(value))
  throw new Error('无法识别：需要带来源批次的 JSON 对象，或岗位优先级数组');
 if(Array.isArray(value.priorities)||Array.isArray(value.rows)){
  const parsed=recognizeGPTData(value.priorities||value.rows);
  return {...parsed,payload:{...parsed.payload,package_id:value.package_id||''}};
 }
 if(typeof value.package_id==='string'&&Array.isArray(value.requirements))
  return {kind:'requirements',label:'JD 要求提取',route:'/requirements/import',payload:value};
 if(typeof value.batch_id==='string'&&Array.isArray(value.decisions))
  return {kind:'review',label:'JD 要求复核',route:'/ai-review/import',payload:value};
 if(Array.isArray(value.results)&&value.results.length&&value.results.every(
   x=>typeof x.batch_id==='string'&&Array.isArray(x.decisions)))
  return {kind:'review',label:'JD 批量复核',route:'/ai-review/import-bulk',payload:value};
 if(typeof value.package_id==='string'&&Array.isArray(value.findings))
  return {kind:'findings',label:'求职分析结论',route:'/analysis/import',payload:value};
 throw new Error('不属于支持的 AI 结果（可能是原始岗位数据包）');
}
function gptSummary(kind,result){
 if(kind==='priority')return '岗位 '+result.total+' 条 · 将改变 '+result.changed+' 条';
 if(kind==='requirements')return '要求 '+result.total+' 条 · 可新增 '+result.new_count+' 条';
 if(kind==='review')return '复核 '+result.covered+' 条';
 return '分析结论 '+result.total+' 条 · 可新增 '+result.new_count+' 条';
}
function gptPreviewBlocked(kind,preview){
 return (kind==='requirements'||kind==='priority')&&Boolean(preview.stale_job_ids?.length);
}
function gptPendingRows(){
 return (unifiedGPTPreview||[]).filter(x=>!x.done&&!x.error&&!x.blocked);
}
function renderGPTImportPreview(){
 const rows=unifiedGPTPreview;
 if(!rows)return;
 const ready=gptPendingRows().length,done=rows.filter(x=>x.done).length;
 const report=rows.map(x=>{
  const sample=x.kind==='priority'&&x.preview?.items?.length?
   '<details class="gpt-priority-sample"><summary>展开查看前 5 条优先级变化</summary>'+
    x.preview.items.slice(0,5).map(y=>'<p>岗位 #'+y.job_id+'：'+
     esc(priorities[y.before]||y.before)+' → '+esc(priorities[y.priority]||y.priority)+
     (y.reason?' · '+esc(y.reason):'')+'</p>').join('')+'</details>':
   x.kind==='findings'&&x.preview?.items?.length?
    '<details class="analysis-preview-items"><summary>查看建议、引用与逐项判断</summary>'+
    x.preview.items.map(item=>'<section class="dossier-item"><strong>'+esc(item.finding.title)+
     '</strong><p>'+esc(item.finding.content)+'</p><p class="tip">岗位 '+esc(item.finding.job_ids.join('、'))+
     ' · 要求 '+esc(item.finding.requirement_ids.join('、'))+'</p>'+
     assessmentMarkup(item.finding,{personal_evidence:state.evidence||[]})+'</section>').join('')+'</details>':'';
  return '<div class="rowline"><div><strong>'+esc(x.fileName)+'</strong>'+
   '<small>'+esc(x.label||'未识别')+' · '+
    esc(x.done?'已导入':x.error?'失败：'+x.error:x.blocked?'岗位资料、优先级或分析依据已变化，须重新导出（岗位 '+(x.preview.stale_job_ids||[]).join('、')+'）':gptSummary(x.kind,x.preview))+
   '</small>'+(x.kind==='findings'&&x.preview&&analysisResultChanges(x.preview)?'<p class="notice">'+esc(analysisResultChanges(x.preview))+'</p>':'')+sample+'</div></div>';
 }).join('');
 modal('导入 AI 结果', '<div class="gpt-preview"><p class="tip">逐文件验证后集中导入；失败项不会阻止其他文件。</p>'+
  '<div class="row-list">'+report+'</div>'+
  '<div class="form-actions"><span class="tip">'+rows.length+' 个结果 · 已完成 '+done+
  ' · 待导入 '+ready+' · 需处理 '+(rows.length-ready-done)+'</span>'+
  '<button type="button" class="btn" data-gpt-cancel>关闭</button>'+
  '<button type="button" class="btn btn-primary" data-gpt-commit'+(!ready||unifiedGPTBusy?' disabled':'')+
  '>导入有效的 '+ready+' 个结果</button></div></div>',true);
}
async function gptReadFiles(files){
 const entries=[];
 for(const file of files){
  if(file.name.toLowerCase().endsWith('.zip')){
   if(file.size>16*1024*1024){entries.push({fileName:file.name,error:'ZIP 超过 16 MB'});continue}
   try{
    const archive=await api('/gpt-import/expand-zip',{method:'POST',body:file,
     headers:{'Content-Type':'application/zip'}});
    entries.push(...archive.entries.map(x=>({fileName:file.name+' / '+x.name,data:x.data,error:x.error})));
   }catch(e){entries.push({fileName:file.name,error:e.message})}
  }else{
   if(!file.name.toLowerCase().endsWith('.json')||file.size>8*1024*1024){
    entries.push({fileName:file.name,error:'不是不超过 8 MB 的 JSON 或 16 MB 的 ZIP'});continue;
   }
   try{entries.push({fileName:file.name,data:JSON.parse((await file.text()).replace(/^\uFEFF/,''))})}
   catch(e){entries.push({fileName:file.name,error:'JSON 格式错误。请让 AI 助手只返回完整 JSON 对象，不要包含解释或代码围栏；重新下载 .json 后导入。原有数据未改变。'})}
  }
 }
 if(entries.length>80)throw new Error('一次最多导入 80 个 JSON 结果，请拆分');
 return entries;
}
async function importGPTFiles(files){
 if(unifiedGPTBusy||!files?.length)return;
 unifiedGPTBusy=true;unifiedGPTPreview=null;
 const rows=[];
 try{
  const entries=await gptReadFiles([...files]);
  for(const entry of entries){
   if(entry.error){rows.push(entry);continue}
   try{
    const parsed=recognizeGPTData(entry.data);
    const preview=await api(parsed.route,{method:'POST',
     body:JSON.stringify({...parsed.payload,dry_run:true})});
    rows.push({...entry,...parsed,preview,blocked:gptPreviewBlocked(parsed.kind,preview)});
   }catch(e){rows.push({fileName:entry.fileName,error:e.message})}
  }
  unifiedGPTPreview=rows;
 }finally{unifiedGPTBusy=false}
 renderGPTImportPreview();
}
function importGPTFile(file){return importGPTFiles([file])}
async function commitGPTImport(){
 if(unifiedGPTBusy||!gptPendingRows().length)return;
 const rows=unifiedGPTPreview;
 unifiedGPTBusy=true;
 const button=document.querySelector('[data-gpt-commit]');
 if(button)button.disabled=true;
 let completed=0,firstKind='';
 try{
  for(const item of gptPendingRows()){
   try{
    await api(item.route,{method:'POST',body:JSON.stringify(item.kind==='findings'?analysisResultCommit(item.payload,item.preview):{...item.payload,dry_run:false})});
    item.done=true;completed++;firstKind||=item.kind;
   }catch(e){item.error=e.message}
  }
  if(completed){
   // Once for the whole group, not once for every JSON.
   try{await load()}catch{toast('结果已保存，但刷新失败；请重新加载，不必再次导入成功项')}
  }
  const unfinished=(unifiedGPTPreview||[]).some(x=>!x.done);
  if(!unfinished&&completed){
   unifiedGPTPreview=null;
   await closeModal(true);
   if(firstKind==='priority')navigate('jobs',{});
   else if(firstKind==='findings'){state.analysisFlow='career';navigate('analysis')}
   else if(firstKind==='requirements'){
    const imported=rows.find(x=>x.kind==='requirements'&&x.done);
    state.analysisFlow='extract';
    if(imported)state.analysisBatch=imported.payload.package_id;
    await navigate('analysis');await changeRequirementPage('pending',0);
   }else navigate('skills');
   toast('已导入 '+completed+' 个 AI 结果');
  }else{
   if(completed)toast('已导入 '+completed+' 个，失败项可核对后单独重新选择');
   renderGPTImportPreview();
  }
 }finally{unifiedGPTBusy=false}
}
document.addEventListener('click',async event=>{
 if(event.target.closest('[data-gpt-choose]')){chooseGPTFile();return}
 if(event.target.closest('[data-gpt-cancel]')){if(!unifiedGPTBusy){unifiedGPTPreview=null;closeModal()}return}
 if(event.target.closest('[data-gpt-commit]')){await commitGPTImport();return}
});
document.addEventListener('change',async event=>{
 const input=event.target.closest('#gpt-json-file');
 if(!input)return;
 const files=[...(input.files||[])];input.value='';
 if(!files.length)return;
 try{await importGPTFiles(files)}catch(e){toast('读取 AI 文件失败：'+e.message)}
});
