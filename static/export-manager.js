/* Shared export flow. Native pywebview is authoritative for the saved path;
   a normal browser controls its own Downloads location instead. */
let exportFolderState = null;
let exportLastResult = null;
let exportSaving = false;

function exportBridge(){
 return window.pywebview && window.pywebview.api &&
        typeof window.pywebview.api.save_endpoint === 'function'
        ? window.pywebview.api : null;
}
function exportEscape(v){
 return String(v == null ? '' : v).replaceAll('&','&amp;').replaceAll('<','&lt;')
  .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
}
function exportResultView(){
 if(!exportLastResult)return '';
 const item=exportLastResult;
 const desktop=Boolean(item.desktop);
 const name=exportEscape(item.name);
 const detail=desktop?exportEscape(item.path):'由浏览器管理下载位置，可按 Ctrl+J 查找';
 return '<div class="export-result-info"><strong>'+(desktop?'文件已保存':'已交给浏览器下载')+
  '</strong><span>'+name+'</span><small title="'+detail+'">'+detail+'</small>'+
  '<div class="export-result-actions">'+(desktop?
    '<button class="btn btn-small" data-export-tool="reveal" data-export-id="'+exportEscape(item.id)+'">打开文件所在位置</button>'+
    '<button class="btn btn-small" data-export-tool="copy" data-export-id="'+exportEscape(item.id)+'">复制路径</button>':
    '<span class="tip">浏览器模式：可使用 Ctrl+J 查看下载记录</span>')+
    '<button class="btn btn-small" data-export-tool="dismiss">关闭</button></div></div>';
}
function showExportResult(result){
 exportLastResult=result;
 const box=document.querySelector('#export-feedback');
 if(box){box.innerHTML=exportResultView();box.hidden=false;}
}
function exportManagerPanel(){
 const isDesktop=Boolean(exportBridge());
 const state=exportFolderState;
 const last=state&&state.recent||[];
 const files=last.length?last.map(file=>{
  const name=exportEscape(file.name),path=exportEscape(file.path);
  const actions=file.exists?
   '<button class="btn btn-small" data-export-tool="reveal" data-export-id="'+exportEscape(file.id)+'">定位</button>'+
   '<button class="btn btn-small" data-export-tool="copy" data-export-id="'+exportEscape(file.id)+'">复制路径</button>':
   '<span class="tip">文件已移动或删除</span>';
  return '<div class="rowline export-history-row"><div class="export-file-detail"><strong title="'+path+'">'+name+
   '</strong><small>'+exportEscape(new Date(file.saved_at).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}))+' · '+Math.ceil(file.size/1024)+' KB</small>'+
   '<small class="export-path" title="'+path+'">'+path+'</small></div><div class="form-actions">'+actions+'</div></div>';
 }).join(''):'<p class="tip">暂无导出记录。保存后的文件会显示在这里。</p>';
 return '<section class="panel export-manager-panel"><h3 class="panel-title">导出文件位置</h3>'+
   (isDesktop?
    '<p class="subtitle">桌面版统一保存到以下目录。导出成功后可直接在资源管理器中定位文件。</p>'+
    '<p class="export-current-path" title="'+exportEscape(state?.folder||'正在读取…')+'">'+exportEscape(state?.folder||'正在读取…')+'</p>'+
    '<div class="page-toolbar"><button class="btn btn-small" data-export-tool="folder">打开导出文件夹</button>'+
    '<button class="btn btn-small" data-export-tool="change">更改保存位置</button></div>'+
    '<h4>最近导出</h4>'+files:
    '<p class="subtitle">浏览器版由 Chrome、Edge 等浏览器决定下载位置，网页无法获取或打开实际保存路径。可按 Ctrl+J 查看下载记录；使用 Windows EXE 时将提供「打开文件所在位置」。</p>')+
   '</section>';
}
async function refreshExportFolder(){
 const bridge=exportBridge();
 if(!bridge)return;
 exportFolderState=await bridge.get_state();
 if(typeof state!=='undefined' && state.loaded && state.page==='sources' && typeof render==='function')render();
}
function browserDownload(filename,blob){
 const url=URL.createObjectURL(blob);
 const anchor=document.createElement('a');
 anchor.href=url;anchor.download=filename;
 document.body.append(anchor);anchor.click();anchor.remove();
 setTimeout(()=>URL.revokeObjectURL(url),5000);
 showExportResult({name:filename,desktop:false});
}
function responseFilename(response,fallback){
 const header=response.headers.get('Content-Disposition')||'';
 const match=header.match(/filename="([a-zA-Z0-9_.-]+)"/);
 return match?match[1]:fallback;
}
async function saveExportRoute(route,payload=null,fallback='radar-export.json'){
 const native=exportBridge();
 if(native){
  const file=await native.save_endpoint(route,payload===null?null:JSON.stringify(payload));
  showExportResult(file);await refreshExportFolder();
  return file;
 }
 const init=payload===null?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};
 const response=await fetch(route,init);
 if(!response.ok){
  let result;try{result=await response.json()}catch{}
  throw new Error(typeof result?.detail==='string'?result.detail:'导出失败 ('+response.status+')');
 }
 const filename=responseFilename(response,fallback);
 browserDownload(filename,await response.blob());
 return {name:filename,desktop:false};
}
async function saveExportText(filename,text,mime='text/plain;charset=utf-8'){
 const native=exportBridge();
 if(native){
  const file=await native.save_text(filename,String(text));
  showExportResult(file);await refreshExportFolder();
  return file;
 }
 browserDownload(filename,new Blob([text],{type:mime}));
 return {name:filename,desktop:false};
}
async function copyExportPath(fileId){
 const file=(exportFolderState?.recent||[]).find(x=>x.id===fileId)||
  (exportLastResult?.id===fileId?exportLastResult:null);
 if(!file?.path)throw new Error('此记录没有可复制的路径');
 try{
  if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');
  await navigator.clipboard.writeText(file.path);
 }catch{
  const input=document.createElement('textarea');
  input.value=file.path;input.setAttribute('readonly','');
  input.style.position='fixed';input.style.opacity='0';
  document.body.append(input);input.select();
  const copied=document.execCommand('copy');input.remove();
  if(!copied)throw new Error('复制失败，请在最近导出列表中手动选中路径');
 }
 if(typeof toast==='function')toast('文件路径已复制');
}
document.addEventListener('pywebviewready',()=>{
 refreshExportFolder().catch(e=>{if(typeof toast==='function')toast('读取导出目录失败：'+e.message)});
});
document.addEventListener('click',async event=>{
 const command=event.target.closest('[data-export-tool]');
 if(command){
  event.preventDefault();
  try{
   if(command.dataset.exportTool==='dismiss'){
    exportLastResult=null;const box=document.querySelector('#export-feedback');
    if(box){box.hidden=true;box.innerHTML='';}return;
   }
   const bridge=exportBridge();
   if(!bridge)throw new Error('仅 Windows 桌面版支持此操作');
   switch(command.dataset.exportTool){
    case 'folder': await bridge.open_folder();break;
    case 'change': await bridge.change_folder();await refreshExportFolder();break;
    case 'reveal': await bridge.reveal_file(command.dataset.exportId);break;
    case 'copy': await copyExportPath(command.dataset.exportId);break;
   }
  }catch(error){if(typeof toast==='function')toast(error.message)}
  return;
 }
 const link=event.target.closest('a[download]');
 if(!link || event.defaultPrevented || event.button!==0 || event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
 const route=new URL(link.href,location.href);
 if(route.origin!==location.origin)return;
 if(exportBridge()){
  event.preventDefault();
  if(exportSaving)return;
  exportSaving=true;
  try{await saveExportRoute(route.pathname,null,link.download||route.pathname.split('/').pop())}
  catch(error){if(typeof toast==='function')toast('保存失败：'+error.message)}
  finally{exportSaving=false}
 }else{
  showExportResult({name:link.download||route.pathname.split('/').pop(),desktop:false});
 }
},true);
