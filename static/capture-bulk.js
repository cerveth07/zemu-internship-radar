/* Bulk intake requires a fresh preview and explicit selection of reviewed rows. */
let captureSelectedForBulk=new Set(),captureBulkBatch=null,captureBulkBusy=false;
let captureIntakeReturn=null,captureIntakePreselect=new Map(),captureIntakeResuming=false;
function captureBulkScope(){
 if(captureBulkBatch!==state.captureSelected){
  captureSelectedForBulk.clear();captureBulkBatch=state.captureSelected;
 }
 return captureVisibleItems().filter(x=>x.status==='pending');
}
function captureBulkActions(items,active){
 const pending=captureBulkScope(),visible=new Set(pending.map(x=>x.id));
 const selected=[...captureSelectedForBulk].filter(id=>visible.has(id));
 const permitted=active.status==='open';
 if(!pending.length||!permitted)return '';
 return '<div class="capture-bulk-toolbar">'+
  '<button type="button" class="btn btn-primary btn-small" data-capture-bulk-review'+(captureBulkBusy?' disabled':'')+'>集中核对并入库</button>'+
  '<label><input type="checkbox" data-capture-bulk-page'+
  (selected.length===pending.length?' checked':'')+'> 选择当前筛选的待核对资料（'+pending.length+'）</label>'+
  (selected.length?'<span class="tip">已选 '+selected.length+' 条</span>'+
  '<button type="button" class="btn btn-small" data-capture-bulk="defer"'+(captureBulkBusy?' disabled':'')+'>批量暂缓</button>'+
  '<button type="button" class="btn btn-small" data-capture-bulk="dismiss"'+(captureBulkBusy?' disabled':'')+'>批量不感兴趣</button>'+
  '<button type="button" class="btn btn-small" data-capture-bulk-clear>清除选择</button>':'')+
  '</div>';
}
function captureBulkCheckbox(item){
 if(item.status!=='pending')return '';
 const active=(state.captureBatches||[]).find(x=>x.id===state.captureSelected);
 return '<label class="capture-bulk-choice"><input type="checkbox" data-capture-bulk-item="'+item.id+'"'+
  (captureSelectedForBulk.has(item.id)?' checked':'')+(active?.status!=='open'?' disabled':'')+
  ' aria-label="选择 '+esc(item.draft?.title||'待核对岗位')+'"></label>';
}
async function captureBulkApply(action){
 if(captureBulkBusy)return;
 const visible=new Set(captureBulkScope().map(x=>x.id));
 const ids=[...captureSelectedForBulk].filter(id=>visible.has(id));
 if(!ids.length)return;
 const word=action==='defer'?'暂缓':'标记为不感兴趣';
 if(!confirm('将选中的 '+ids.length+' 条资料批量'+word+'？仅改变处理状态，不会删除原文或创建岗位。'))return;
 captureBulkBusy=true;updateCaptureResults();
 try{
  const r=await api('/capture/items/disposition-bulk',{method:'POST',
    body:JSON.stringify({ids,batch_id:state.captureSelected,action})});
  captureSelectedForBulk.clear();
  await refreshCapture();
  toast('已将 '+r.changed+' 条资料'+word);
 }catch(error){toast('批量处理失败：'+error.message)}
 finally{captureBulkBusy=false;updateCaptureResults()}
}
document.addEventListener('change',event=>{
 const item=event.target.closest('[data-capture-bulk-item]');
 if(item){
  captureBulkScope();
  const id=Number(item.dataset.captureBulkItem);
  if(item.checked)captureSelectedForBulk.add(id);else captureSelectedForBulk.delete(id);
  updateCaptureResults();return;
 }
 if(event.target.closest('[data-capture-bulk-page]')){
  const rows=captureBulkScope();
  for(const row of rows){
   if(event.target.checked)captureSelectedForBulk.add(row.id);
   else captureSelectedForBulk.delete(row.id);
  }
  updateCaptureResults();
 }
});
document.addEventListener('click',event=>{
 if(event.target.closest('[data-capture-bulk-review]')){openCaptureBulkReview();return}
 const btn=event.target.closest('[data-capture-bulk]');
 if(btn){captureBulkApply(btn.dataset.captureBulk);return}
 if(event.target.closest('[data-capture-bulk-clear]')){
  captureSelectedForBulk.clear();updateCaptureResults();
 }
});

let captureIntakePreview=null;
async function openCaptureBulkReview(ids=null,batchId=state.captureSelected){
 if(captureBulkBusy||(!captureIntakeResuming&&!canLeaveModal()))return;
 if(!captureIntakeResuming){captureIntakeReturn=null;captureIntakePreselect.clear()}
 const pending=captureBulkScope(),selected=pending.filter(x=>captureSelectedForBulk.has(x.id));
 const scope=ids||(selected.length?selected:pending).map(x=>x.id);
 if(!scope.length)return;
 const limited=scope.slice(0,100);
 captureIntakePreview=null;
 modal('集中核对并入库','<div id="capture-intake-loading" role="status">正在检查资料与重复岗位…</div>',true);
 const loading=$('#capture-intake-loading');
 try{
  const preview=await api('/capture/items/preview-bulk',{method:'POST',body:JSON.stringify({batch_id:batchId,ids:limited})});
  if(!loading.isConnected||!$('#modal').open)return;
  captureIntakePreview={...preview,ids:limited};
  renderCaptureIntake(scope.length);
 }catch(error){
  if(loading.isConnected&&$('#modal').open){
   loading.setAttribute('role','alert');loading.textContent='无法读取核对资料：'+error.message+'。关闭后可重新打开。';
  }
 }
}
function renderCaptureIntake(total){
 const preview=captureIntakePreview,rows=preview.items,eligible=rows.filter(x=>!x.issue);
 modal('集中核对并入库',
  '<p class="subtitle">核对企业、岗位与 JD 后，勾选要收录的资料。单条修改或保存草稿后会返回此表，内容未变的勾选会保留；有变化的资料需要重新核对。</p>'+
  '<p id="capture-intake-return-notice" class="tip" role="status"></p>'+
  '<p class="tip">本次 '+rows.length+' 条'+(total>rows.length?'（当前范围共 '+total+' 条，每次最多 100 条）':'')+' · 可选 '+eligible.length+' 条 · 需单独处理 '+(rows.length-eligible.length)+' 条。未选资料继续保留在收件箱。标为“尚未整理，请查看 JD”的字段只是还没单独录入，不代表岗位原文没写。</p>'+
  '<form id="capture-intake-form"><div class="table-card capture-intake-table"><table class="table"><thead><tr>'+
  '<th><label><input type="checkbox" data-intake-all'+(!eligible.length?' disabled':'')+'> 全选可入库</label></th><th>企业 / 岗位</th><th>城市 / 薪资</th><th>实习条件</th><th>核对结果</th></tr></thead><tbody>'+
  rows.map(row=>{
   const item=row.item,d=row.data||item.draft||{},jd=d.jd_text??item.raw_text??'',source=d.source_url??item.source_url??'';
   return '<tr data-intake-row="'+row.id+'"><td><label><input type="checkbox" data-intake-select="'+row.id+'"'+(row.issue?' disabled':'')+' aria-label="已核对 '+esc(d.title||'资料 #'+row.id)+'"> 已核对</label></td>'+
    '<td><strong>'+esc(d.title||'岗位名称缺失')+'</strong><span class="company-small">'+esc(d.company||'企业名称缺失')+'</span><details class="capture-intake-evidence"><summary>查看完整 JD 与来源</summary>'+
    '<p>'+esc(directions[d.direction]||d.direction||'其他方向')+' · 发布 '+esc(d.posted_at||'未注明')+' · 截止 '+esc(d.deadline||'未注明')+'</p>'+
    '<p>资格条件：'+esc(d.eligibility||'未单独填写')+'</p><p>'+ (source?'<a href="'+esc(secureLink(source))+'" target="_blank" rel="noopener noreferrer">打开原始岗位</a>':'未提供来源链接')+
    (item.has_screenshot?' · <a href="/api/capture/items/'+row.id+'/screenshot" target="_blank" rel="noopener noreferrer">对照截图</a>':'')+'</p>'+
    (item.reused_snapshot?'<p class="tip">使用已有快照，请核对来源是否更新。</p>':'')+
    '<pre class="capture-intake-jd">'+esc(jd||'尚无 JD，请先补充')+'</pre></details></td>'+
    '<td>'+esc(d.city||'城市未注明')+'<span class="company-small">'+esc(d.salary||'薪资未注明')+'</span></td>'+
    '<td>'+esc(d.duration||'时长尚未整理，请查看 JD')+'<span class="company-small">'+esc(d.onsite_days||'出勤尚未整理，请查看 JD')+'</span></td>'+
    '<td><span class="'+(row.issue?'capture-intake-issue':'tip')+'">'+esc(row.issue||'资料齐全，待你核对')+'</span>'+
    (item.status==='pending'?'<button type="button" class="btn btn-small" data-intake-edit="'+row.id+'">'+(row.issue?'单独处理':'修改资料')+'</button>':'')+'</td></tr>';
  }).join('')+'</tbody></table></div><div id="form-error" class="error" role="alert"></div>'+
  '<div class="form-actions"><span id="capture-intake-count" role="status">已核对 0 条</span><button type="button" class="btn" data-intake-reload>重新检查</button>'+
  '<button type="button" class="btn" data-action="close">取消</button><button class="btn btn-primary" type="submit" disabled>确认入库（0）</button></div></form>',true);
 applyIntakePreselect();
}
function applyIntakePreselect(){
 if(!captureIntakePreselect.size)return;
 let changed=0;
 for(const [id,version] of captureIntakePreselect){
  const box=document.querySelector('#capture-intake-form [data-intake-select="'+id+'"]');
  const row=captureIntakePreview.items.find(x=>x.id===id);
  if(box&&!box.disabled&&row?.version===version)box.checked=true;
  else changed++;
 }
 if(changed)$('#capture-intake-return-notice').textContent=changed+' 条原先勾选的资料已变化或不可入库，已取消勾选，请重新核对。';
 captureIntakePreselect.clear();
 updateCaptureIntakeSelection();
}
function rememberIntakeReturn(editedId){
 if(!captureIntakePreview)return;
 const checked=[...document.querySelectorAll('#capture-intake-form [data-intake-select]:checked:not(:disabled)')]
  .map(x=>Number(x.dataset.intakeSelect));
 captureIntakeReturn={ids:[...captureIntakePreview.ids],batch_id:captureIntakePreview.batch_id,
  selected:captureIntakePreview.items.filter(row=>checked.includes(row.id)&&row.id!==editedId)
   .map(row=>[row.id,row.version]),edited:editedId};
}
async function resumeCaptureIntake(committedId,committed=true){
 const back=captureIntakeReturn;
 if(!back||back.edited!==committedId){captureIntakeReturn=null;return false}
 captureIntakeReturn=null;
 captureIntakePreselect=new Map(back.selected);
 if(committed)back.ids=back.ids.filter(id=>id!==committedId);
 if(!back.ids.length){captureIntakePreselect.clear();return false}
 // 单条表单此时仍在保存流程里（modalBusy），跳过“放弃修改”询问，直接回到核对表。
 captureIntakeResuming=true;
 try{await openCaptureBulkReview(back.ids,back.batch_id)}
 finally{captureIntakeResuming=false}
 return true;
}
async function returnFromCaptureReview(){
 if(!captureIntakeReturn||!$('#capture-review-form'))return false;
 return resumeCaptureIntake(captureIntakeReturn.edited,false);
}
function updateCaptureIntakeSelection(){
 const form=$('#capture-intake-form');if(!form)return;
 const boxes=[...form.querySelectorAll('[data-intake-select]:not(:disabled)')],count=boxes.filter(x=>x.checked).length;
 const all=form.querySelector('[data-intake-all]');all.checked=boxes.length>0&&count===boxes.length;all.indeterminate=count>0&&count<boxes.length;
 $('#capture-intake-count').textContent='已核对 '+count+' 条';
 const submit=form.querySelector('[type=submit]');submit.textContent='确认入库（'+count+'）';submit.disabled=!count||form.inert;
}
document.addEventListener('change',event=>{
 if(event.target.matches('[data-intake-all]')){
  document.querySelectorAll('[data-intake-select]:not(:disabled)').forEach(x=>x.checked=event.target.checked);
  updateCaptureIntakeSelection();
 }else if(event.target.matches('[data-intake-select]'))updateCaptureIntakeSelection();
});
document.addEventListener('click',async event=>{
 if(event.target.closest('[data-intake-reload]')){
  const preview=captureIntakePreview;if(preview)await openCaptureBulkReview(preview.ids,preview.batch_id);
 }
 const edit=event.target.closest('[data-intake-edit]');
 if(edit){
  const id=Number(edit.dataset.intakeEdit);
  // 勾选是选择而不是未保存的修改：进入单条核对时不再拦截，返回后继续这张表。
  rememberIntakeReturn(id);
  try{await refreshCapture(false);captureReview(id)}catch(error){toast('读取资料失败：'+error.message)}
 }
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(form.id!=='capture-intake-form')return;
 event.preventDefault();
 const preview=captureIntakePreview,ids=new Set([...form.querySelectorAll('[data-intake-select]:checked:not(:disabled)')].map(x=>Number(x.dataset.intakeSelect)));
 if(!preview||!ids.size||!beginFormWrite(form))return;
 const submit=form.querySelector('[type=submit]');submit.textContent='正在入库…';
 try{
  const result=await api('/capture/items/commit-bulk',{method:'POST',body:JSON.stringify({batch_id:preview.batch_id,
   items:preview.items.filter(x=>ids.has(x.id)&&!x.issue).map(x=>({id:x.id,version:x.version}))})});
  captureSelectedForBulk.clear();captureIntakePreview=null;
  await closeModal(true);
  await refreshAfterWrite('已入库 '+result.imported+' 条'+(result.already_processed?'，另有 '+result.already_processed+' 条此前已成功入库':''),['jobs','companies','summary','reviews','quality']);
  try{await refreshCapture()}catch{toast('入库已完成，收件箱刷新失败，请重新加载；不必重复提交。')}
 }catch(error){showFormError(form,error)}
 finally{endFormWrite(form);updateCaptureIntakeSelection()}
});
