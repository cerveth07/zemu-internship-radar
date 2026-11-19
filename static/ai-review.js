/* Local GPT-assisted JD review. Works with existing manual ChatGPT workflow.
   Does not mutate human-confirmed requirement statuses. */
let aiReviewDiagnostic=null;
let aiReviewGroups=null;
let aiReviewRaw='';
let aiReviewPreview=null;
let aiReviewBusy=false;
let aiReviewError='';
function aiReviewPanel(){
 const d=aiReviewDiagnostic,queue=aiReviewGroups;
 return '<section class="panel ai-review-panel" id="ai-review-panel">'+
  '<h3 class="panel-title">AI 批量审核 · 减少逐条确认</h3>'+
  '<p class="subtitle">先做规则诊断，再把有效待审核要求打包给 AI 助手复核，导回后可按技能组处理重要异常。结果独立标为“AI 已复核”，不会自动更改人工确认状态。</p>'+
  '<div class="page-toolbar"><button class="btn btn-small" data-ai-action="refresh"'+(aiReviewBusy?' disabled':'')+'>分析待审核要求</button>'+
  '<button class="btn btn-primary btn-small" data-ai-action="export"'+(aiReviewBusy?' disabled':'')+'>下载 AI 审核 ZIP（当前筛选岗位）</button>'+
  '<label class="field ai-review-recheck"><input type="checkbox" id="ai-review-include-reviewed"> 重新包含已有有效审核结果</label></div>'+
  (aiReviewError?'<p role="alert" class="notice">'+esc(aiReviewError)+'</p>':'')+
  (d?'<div class="workflow-metrics">'+
    '<div><strong>'+d.pending+'</strong><small>待审候选</small></div>'+
    '<div><strong>'+d.groups+'</strong><small>名称/条件保守分组</small></div>'+
    '<div><strong>'+d.ai_verified+'</strong><small>当前有效辅助复核通过</small></div>'+
    '<div><strong>'+d.not_reviewed+'</strong><small>尚未得到辅助复核结论</small></div>'+
   '</div><p class="tip">可能异常：长名称 '+(d.flags.long_name||0)+
   ' · 工作职责/其他 '+(d.flags.non_skill||0)+
   ' · 组合要求 '+(d.flags.compound||0)+
   ' · 过期原文 '+(d.flags.stale||0)+
   ' · 建议排除 '+(d.ai_excluded||0)+' · 保留不确定 '+(d.ai_uncertain||0)+
   '。共享参考资料不是重复技能；诊断不执行删除或确认。</p>':'<p class="tip">点击「分析待审核要求」查看实际命中数量。</p>')+
  '<h4>导回 AI 批量复核结果</h4>'+
  '<p class="tip">解压导出的 ZIP，将 review-instructions.md 和每个 review-xxx.json 分别交给 AI 助手；每份输出一个带 batch_id 的 JSON。允许逐批导回；多个批次可以合并为 {"results":[{"batch_id":"...","decisions":[...]},...]} 一次导入。所有组和 ID 必须完整覆盖；不完整会拒绝，整包不写入。</p>'+
  '<label class="btn btn-small ai-file-label">选择审核结果 JSON 文件<input type="file" data-ai-review-file accept=".json,application/json" hidden></label>'+
  '<textarea class="analysis-json" id="ai-review-json" rows="5" placeholder=\'{"batch_id":"...","decisions":[...]}\'>'+
   esc(aiReviewRaw)+'</textarea>'+
  '<div class="form-actions"><button class="btn btn-small" data-ai-action="preview"'+(aiReviewBusy?' disabled':'')+'>预览全部引用</button>'+
  '<button class="btn btn-primary btn-small" data-ai-action="commit"'+(!aiReviewPreview||aiReviewBusy?' disabled':'')+'>确认导回 AI 复核结果</button></div>'+
  (aiReviewPreview?'<p class="notice" role="status">审核结果符合批次约束，共覆盖 '+
   aiReviewPreview.covered+' 条。类型：'+esc(JSON.stringify(aiReviewPreview.verdicts))+
   '。保存后仍须决定哪些 AI 结果参与 P2/P3 分析。</p>':'')+
  '<h4>重点人工决策（最多 40 组）</h4>'+
  (queue?
   '<p class="tip">共 '+queue.total_groups+' 个组，当前展示 40 组以内；余下 '+
   queue.deferred_groups+' 组维持原复核状态。每组仅展示最多 3 条样例；不能只凭样例批量排除未经 AI 逐条覆盖的要求。</p>'+
   (queue.manual_queue.length?'<div class="row-list">'+queue.manual_queue.map(g=>
     '<div class="rowline ai-review-group"><div class="ai-group-text">'+
     '<strong>'+esc(g.name)+'</strong><small>'+esc(g.type)+' · '+esc(g.strength)+' · '+
     g.count+' 条 · 已复核 '+(g.verified+g.excluded)+' / '+g.count+'</small>'+
     '<small>AI 已覆盖 '+(g.gpt_reviewed||0)+'/'+g.count+' · 当前辅助层通过 '+g.verified+'/'+g.count+' · '+esc(g.flags.join('、')||'未命中异常规则')+'</small>'+
     '<p class="tip">原文样例：'+g.samples.map(x=>'「'+esc(x.evidence)+'」').join('；')+'</p></div>'+
     '<div class="form-actions"><button class="btn btn-small" data-ai-key="'+g.key+
     '" data-ai-verdict="verified"'+(g.gpt_verified===g.count?'':' disabled title="必须先完成本组全部 AI 复核"')+'>确认 AI 复核</button>'+
     '<button class="btn btn-small" data-ai-key="'+g.key+
     '" data-ai-verdict="excluded"'+((g.gpt_reviewed===g.count&&(g.count<=40||g.gpt_excluded===g.count))?'':' disabled title="排除整组前需 AI 逐条复核；超 40 条且意见不一时不能批量排除"')+'>不纳入分析</button>'+
     '<button class="btn btn-small" data-ai-key="'+g.key+
     '" data-ai-verdict="uncertain">保留待定</button></div></div>'
   ).join('')+'</div>':'<p class="tip">当前没有有效待审分组。</p>'):
   '<p class="tip">先读取诊断，查看需要重点判断的技能组。</p>')+
  '<div class="page-toolbar"><button class="btn btn-small" data-ai-action="history">查看最近审核操作 / 撤销</button></div>'+
 '</section>';
}
async function reloadAIReview(){
 if(aiReviewBusy)return;
 aiReviewBusy=true;aiReviewError='';render();
 try{
  [aiReviewDiagnostic,aiReviewGroups]=await Promise.all([
   api('/ai-review/diagnosis'),api('/ai-review/groups')]);
 }catch(e){aiReviewError=e.message;throw e}
 finally{aiReviewBusy=false;render()}
}
async function exportAIReview(){
 if(aiReviewBusy)return;
 aiReviewBusy=true;
 try{
  const ids=selectedJobs().map(j=>j.id);
  if(!ids.length)throw new Error('当前筛选没有岗位');
  const includeReviewed=Boolean(document.querySelector('#ai-review-include-reviewed')?.checked);
  const file=await saveExportRoute('/api/ai-review/export',{
    job_ids:ids,include_reviewed:includeReviewed},'jd-ai-review.zip');
  toast('已导出 '+file.name+'。请逐批交给 AI 助手，结果回来后导入。');
 }finally{aiReviewBusy=false;render()}
}
async function importAIReview(dryRun){
 const field=document.querySelector('#ai-review-json');
 const raw=field?.value?.trim()||aiReviewRaw;
 const data=JSON.parse(raw);
 if(!data||typeof data!=='object')throw new Error('请输入有效的 JSON 对象');
 if(!dryRun){
  if(!aiReviewPreview||aiReviewRaw!==raw)
   throw new Error('提交前请重新预览，确保输入内容没有变化');
  if(!confirm('将导入 AI 的复核建议，但不会自动写成人工 confirmed。继续？'))return;
 }
 aiReviewRaw=raw;aiReviewBusy=true;
 try{
  const route=Array.isArray(data.results)?'/ai-review/import-bulk':'/ai-review/import';
  const result=await api(route,{method:'POST',
    body:JSON.stringify({...data,dry_run:dryRun})});
  if(dryRun)aiReviewPreview=result;
  else{aiReviewPreview=null;await reloadAIReviewAfterCommit();toast('已保存 AI 复核意见，未修改人工审核状态。')}
  render();
 }finally{aiReviewBusy=false;render()}
}
async function reloadAIReviewAfterCommit(){
 [aiReviewDiagnostic,aiReviewGroups]=await Promise.all([
  api('/ai-review/diagnosis'),api('/ai-review/groups')]);
}
async function decideAIGroup(key,verdict){
 const group=aiReviewGroups?.manual_queue.find(g=>g.key===key);
 if(!group)throw new Error('审核组已更新，请刷新');
 const labels={verified:'按组保留为 AI/辅助复核',excluded:'不参与分析',uncertain:'保持待核验'};
 const note=prompt('将对本组 '+group.count+' 条要求执行「'+labels[verdict]+'」。\n请填写决定依据，记录将支持撤销：');
 if(note===null)return;
 if(!note.trim())throw new Error('请填写审核依据');
 if(!confirm('确认对本组 '+group.count+' 条要求应用此规则？不会修改 JD 或人工确认状态。'))return;
 const result=await api('/ai-review/decide',{method:'POST',
    body:JSON.stringify({key,verdict,note:note.trim()})});
 await reloadAIReviewAfterCommit();render();
 toast('已处理 '+result.affected+' 条，撤销事件 #'+result.event_id);
}
async function showAIHistory(){
 const history=await api('/ai-review/history');
 modal('AI 辅助审核 · 操作记录',
  '<p class="tip">只能撤销当前尚未被其他决定覆盖的记录，不会撤销人工 confirmed。</p>'+
  (history.length?'<div class="row-list">'+history.map(h=>
   '<div class="rowline"><div><strong>事件 #'+h.id+'</strong><small>'+
   esc(h.created_at)+' · '+esc(h.batch_id||'组级人工决策')+
   (h.undone_at?' · 已撤销':'')+'</small></div>'+
   (!h.undone_at?'<button class="btn btn-small" data-ai-undo="'+h.id+'">撤销</button>':'')+
   '</div>').join('')+'</div>':'<p>无操作记录。</p>'),true);
}
async function undoAIEvent(id){
 if(!confirm('撤销事件 #'+id+' 并恢复应用前的 AI 辅助审核结果？'))return;
 const result=await api('/ai-review/undo/'+id,{method:'POST'});
 await reloadAIReviewAfterCommit();
 render();toast('已恢复 '+result.restored+' 条要求的辅助审核记录');
}
document.addEventListener('change',async e=>{
 const input=e.target.closest('input[data-ai-review-file]');
 if(!input)return;
 try{
  const file=input.files?.[0];if(!file)return;
  if(!file.name.toLowerCase().endsWith('.json')||file.size>8*1024*1024)
   throw new Error('请选择 8MB 以内 JSON；ZIP 请先解压');
  aiReviewRaw=await file.text();
  JSON.parse(aiReviewRaw);
  aiReviewPreview=null;render();
  toast('已读取 '+file.name+'，先预览再保存');
 }catch(err){toast('读取失败：'+err.message)}
},true);
document.addEventListener('input',e=>{
 if(e.target.id==='ai-review-json'){
  aiReviewRaw=e.target.value;aiReviewPreview=null;
  const button=document.querySelector('[data-ai-action="commit"]');
  if(button)button.disabled=true;
 }
},true);
document.addEventListener('click',async e=>{
 const action=e.target.closest('[data-ai-action]');
 const group=e.target.closest('[data-ai-key][data-ai-verdict]');
 const undo=e.target.closest('[data-ai-undo]');
 if(!action&&!group&&!undo)return;
 e.preventDefault();e.stopImmediatePropagation();
 try{
  if(group)await decideAIGroup(group.dataset.aiKey,group.dataset.aiVerdict);
  else if(undo)await undoAIEvent(Number(undo.dataset.aiUndo));
  else{
   switch(action.dataset.aiAction){
    case 'refresh':await reloadAIReview();break;
    case 'export':await exportAIReview();break;
    case 'preview':await importAIReview(true);break;
    case 'commit':await importAIReview(false);break;
    case 'history':await showAIHistory();break;
   }
  }
 }catch(err){toast('辅助审核操作失败：'+err.message)}
},true);
