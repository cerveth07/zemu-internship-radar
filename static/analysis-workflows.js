/* Task-oriented ChatGPT workbench. All GPT calls remain manual in the user's browser. */
const workflowStages = {
 awaiting_gpt: '已导出 · 等待 AI 结果',
 needs_review: '候选要求待审核',
 reviewed: '候选已审核 · 不代表 JD 提取完整',
 imported_duplicates: 'AI 已返回 · 没有新增要求',
 no_candidates: '已标记：本批无候选',
 stale: 'JD 已变化 · 需重新提取',
};
function workflowSummary(){
 const statuses=new Map((state.workflow?.jobs||[]).map(j=>[j.job_id,j]));
 const jobs=analysisJobs();
 const covered=jobs.filter(j=>(statuses.get(j.id)?.confirmed||0)>0).length;
 const pending=jobs.reduce((n,j)=>n+(statuses.get(j.id)?.pending||0),0);
 const stale=jobs.filter(j=>(statuses.get(j.id)?.stale||0)>0).length;
 const reserved=new Set(state.workflow?.reserved_job_ids||[]);
 const untouched=jobs.filter(j=>{
  const row=statuses.get(j.id);
  return !reserved.has(j.id) && (!row||!(row.confirmed||row.pending));
 }).length;
 return {jobs,covered,pending,stale,untouched};
}
function workflowBatchSummary(batch){
 return '<span class="tip">'+batch.job_count+' 岗位 · '+batch.pending+
  ' 条待审核 · '+batch.confirmed+' 条已确认'+(batch.stale?' · '+batch.stale+' 条原文过期':'')+'</span>';
}
function workflowBatchList(limit=12){
 const rows=(state.workflow?.batches||[]).slice(0,limit);
 if(!rows.length)return '<p class="tip">暂无 JD 提取批次。请先导出岗位包，并交给 AI 助手提取。</p>';
 return '<div class="row-list">'+rows.map(batch=>
  '<div class="rowline workflow-batch-row"><div class="workflow-batch-detail">'+
  '<strong>批次 '+esc(batch.package_id.slice(0,8))+'</strong>'+
  '<small>'+esc(batch.activity_at||batch.created_at)+' · '+esc(workflowStages[batch.stage]||batch.stage)+
  (batch.origin==='legacy_import'?' · 旧版已导回批次':'')+'</small>'+
  workflowBatchSummary(batch)+
  '</div><div class="form-actions"><button class="btn btn-small" data-analysis-batch="'+
  esc(batch.package_id)+'">查看 / 继续</button></div></div>'
 ).join('')+'</div>';
}
function workflowNavigation(){
 const current=state.analysisFlow;
 const entries=[['home','工作台首页'],['extract','JD 提取与审核'],
                ['career','求职分析与学习规划'],['advanced','高级：旧版分析']];
 return '<nav class="workflow-nav" aria-label="分析工作台任务">'+
  entries.map(([id,title])=>
   '<button type="button" data-analysis-flow="'+id+'" class="btn btn-small'+
   (id===current?' workflow-active':'')+'"'+(id===current?' aria-current="page"':'')+
   '>'+title+'</button>').join('')+'</nav>';
}
function workflowFilterContext(){
 return jobScopeMarkup(true)+'<button class="btn btn-small" data-page="jobs">修改岗位筛选</button>';
}
function workflowHome(){
 const summary=workflowSummary();
 const recent=state.workflow?.batches||[];
 return '<section class="panel workflow-overview"><h3 class="panel-title">当前数据准备情况</h3>'+
  '<p class="subtitle">显示的是本地数据库的真实记录；存在已确认要求不代表整份 JD 提取完整。</p>'+workflowFilterContext()+
  '<div class="workflow-metrics">'+
  '<div><strong>'+summary.jobs.length+'</strong><small>当前筛选岗位</small></div>'+
  '<div><strong>'+summary.covered+'</strong><small>已有已确认要求的岗位</small></div>'+
  '<div><strong>'+summary.pending+'</strong><small>待审核要求</small></div></div>'+
  (summary.stale?'<p class="notice">当前筛选中有 '+summary.stale+' 个岗位存在旧版 JD 要求，建议先核对更新。</p>':'')+
  '</section>'+
  '<div class="workflow-cards">'+
   '<section class="panel workflow-card"><h3>提取 JD 要求</h3>'+
   '<p class="subtitle">导出含完整 JD 的岗位包，手动让 AI 提取原文参考资料，再逐条审核。</p>'+
   '<p class="tip">当前筛选下有 '+summary.untouched+' 个岗位尚无有效要求，也不在已有处理批次中。</p>'+
   '<button class="btn btn-primary" data-analysis-flow="extract">开始 / 继续 JD 提取</button>'+
   '<button class="btn btn-small" data-action="analyze-full-jd">没有时间提取？直接用完整 JD 分析</button></section>'+
   '<section class="panel workflow-card"><h3>求职分析与学习规划</h3>'+
   '<p class="subtitle">根据已确认要求或完整原始 JD，结合我的能力与经历分析方向与学习优先级。</p>'+
   '<p class="tip">精简版已确认要求覆盖：'+summary.covered+' / '+summary.jobs.length+' 岗位。</p>'+
   '<button class="btn btn-primary" data-analysis-flow="career">开始求职分析</button></section>'+
  '</div>'+
  '<section class="panel workflow-batch-panel"><div class="page-toolbar toolbar-split">'+
   '<h3 class="panel-title">最近 JD 提取批次</h3>'+
   '<span class="tip">本地保留处理进度，关闭程序后仍可继续</span></div>'+
   workflowBatchList(8)+'</section>';
}
function workflowExtraction(){
 const summary=workflowSummary();
 const statuses=new Map((state.workflow?.jobs||[]).map(j=>[j.job_id,j]));
 const reserved=new Set(state.workflow?.reserved_job_ids||[]);
 const outstanding=summary.jobs.filter(j=>{
  const st=statuses.get(j.id);
  return !reserved.has(j.id)&&(!st||(st.confirmed===0&&st.pending===0)||st.stale>0);
 });
 const sample=outstanding.slice(0,50);
 const active=state.workflow?.batches?.find(b=>b.package_id===state.analysisBatch);
 // 只有真正导回了候选要求，才展示批量复核这类进阶入口。
 const hasCandidates=Boolean(summary.pending)||Number(state.requirementData?.total||0)>0;
 const batchNotice=active?
  '<p class="workflow-selected-batch"><strong>当前批次 '+esc(active.package_id.slice(0,8))+'</strong> '+
    esc(workflowStages[active.stage]||active.stage)+' · '+workflowBatchSummary(active)+'</p>':
  '<p class="tip">尚未选定批次。导出后会自动关联新批次，也可从下方历史记录恢复。</p>';
 const next=active?.stale?'岗位原文已变化，重新导出当前批次后再分析':
  active?.pending?'导回已完成，继续核对下方候选要求':
  active&&active.stage!=='awaiting_gpt'?'本批候选已处理，可转到求职分析或继续下一批':
  active?'将本批岗位包交给 AI 助手，然后导回 JSON':'导出下方待处理岗位包，开始一次分析';
 return '<section class="panel"><h3 class="panel-title">当前任务：提取任职条件</h3>'+
  '<p>范围：'+(active?active.job_count:sample.length)+' 个岗位 · 完成后得到带 JD 原文参考资料的候选条件，供你核对。</p>'+
  '<p role="status"><strong>下一步：</strong>'+esc(next)+'</p>'+batchNotice+
  '<details><summary>查看本次岗位</summary><ul>'+summary.jobs.filter(j=>!active||active.job_ids?.includes(j.id)).slice(0,50)
   .map(j=>'<li>'+esc(j.company)+' · '+esc(j.title)+'</li>').join('')+'</ul></details></section>'+
  '<section class="panel workflow-step"><h3 class="panel-title">第一阶段：准备完整 JD 数据包</h3>'+
  '<p class="subtitle">使用「岗位库」当前筛选范围，共 '+summary.jobs.length+
  ' 条。导出的 JSON 内已包含完整 JD 与提取指令，只需上传岗位包。首次提取使用完整 JD。</p>'+
  '<div class="form-actions">'+
   '<button class="btn btn-primary" data-action="export-unprocessed-jobs"'+(!sample.length?' disabled':'')+'>'+
      '导出最多 50 条待处理岗位（'+sample.length+'）</button>'+
   '<button class="btn" data-action="export-jobs-json"'+(!summary.jobs.length?' disabled':'')+'>导出当前筛选全部 JD</button>'+
   '<button class="btn" data-action="copy-requirement-prompt" title="复制提取规则，连同导出的岗位 JSON 一起发给你的 AI 助手">复制提示词</button>'+
   '<button class="btn" data-action="download-requirement-prompt">下载 AI 提取说明</button>'+
  '</div><p class="tip">批量导出自动拆为多个独立文件，每批拥有自己的 package_id；一个批次单独上传与导回。已导出文件可到「数据与帮助」定位；文件丢失或 JD 更新时应重新导出生成新批次。</p>'+
  '<p class="tip">不想先做提取？<button class="btn btn-small" data-action="analyze-full-jd">直接用完整 JD 分析</button>，用原始 JD 做方向与能力分析，之后再回来补充候选要求。</p></section>'+
  '<section class="panel workflow-step"><h3 class="panel-title">第二阶段：交给 AI 助手处理</h3>'+
  '<p class="subtitle">把岗位 JSON 交给 AI 助手，告诉它“按 task_instructions 处理，返回 JSON 文件”。若下载的是 ZIP，请解压后逐批上传。岗位 JSON 已自带提取指令；“下载 AI 提取说明”只在你想先单独查看规则时使用。</p>'+
  '<p class="tip">返回内容会带上 package_id，下一步导回时据此匹配批次；不要让它改写岗位原文或补写没写过的要求。</p></section>'+
  '<section class="panel workflow-step"><h3 class="panel-title">第三阶段：导回结果</h3>'+
  batchNotice+
  '<p class="subtitle">取得返回 JSON 后，可直接选择文件或粘贴文本；系统检查每条 evidence 是否为 JD 或 eligibility 中的逐字原文，不合格的内容不会写入。</p>'+
  '<div class="page-toolbar"><label class="btn btn-small workflow-file-label">选择 AI 返回的 JSON 文件'+
  '<input type="file" data-analysis-file="requirements" accept=".json,application/json" hidden></label>'+
  '<button class="btn btn-small" data-action="open-extraction-batches">选择已有批次</button>'+
  '<button class="btn btn-small" data-page="sources">打开导出文件记录</button>'+
  (hasCandidates?'<button class="btn btn-small" data-action="review-all-requirements">查看所有待审核要求</button>':'')+
  (active?'<button class="btn btn-small" data-action="reexport-extraction-batch">重新导出当前批次 JD</button>':'')+
  '</div>'+
  '</section>'+
  requirementsPanel()+
  (hasCandidates?aiReviewPanel():
   '<section class="panel workflow-step"><h3 class="panel-title">可选：AI 辅助复核</h3>'+
   '<p class="tip">导回候选要求后，这里才会出现 AI 辅助复核与按技能组批量处理的入口。当前没有待审候选，先把上一步的候选导回来即可。</p></section>')+
  '<section class="panel workflow-batch-panel" id="workflow-extraction-batches">'+
  '<h3 class="panel-title">批次历史与继续处理</h3>'+workflowBatchList(30)+
  (active&&active.stage==='awaiting_gpt'?
   '<p class="tip">如果 AI 确实没有返回任何候选要求，可人工将本批标记为“无候选”，这并不代表 JD 没有任职要求。</p>'+
   '<button class="btn btn-small" data-action="mark-no-candidates">人工确认本批无候选</button>':'')+
  '</section>';
}
function workflowCareerStatus(){
 const summary=workflowSummary();
 const fraction=summary.jobs.length?Math.round(100*summary.covered/summary.jobs.length):0;
 const full=state.careerMode==='full';
 return '<section class="panel workflow-ready"><h3 class="panel-title">求职分析数据就绪情况</h3>'+
  '<p>当前筛选 '+summary.jobs.length+' 个岗位，'+summary.covered+
  ' 个岗位至少有一项已确认 JD 要求（'+fraction+'%）。'+
  (summary.pending?' 另有 '+summary.pending+' 条要求尚待人工审核。':'')+'</p>'+
  (full?
   '<p class="notice">当前选择「完整版」：包含完整原始 JD，可先探索岗位，但仍应核对招聘事实与我的能力与经历。</p>':
   state.careerIncludeAI?
   '<p class="notice">已选择附带 AI 辅助复核要求：它们会单独标记来源，不等于人工确认。仅当前 JD 版本有效、判为 verified 的要求才会导出；尚无有效复核内容的岗位仍不具备完整要求信息。</p>':
   summary.covered<summary.jobs.length?
   '<div class="notice" role="status">精简版不包含原始 JD，未确认的岗位不会提供完整任职要求。建议先提取并审核，或改用完整 JD 分析。'+
   '<div class="form-actions"><button class="btn btn-small" data-analysis-flow="extract">去提取 JD</button>'+
   '<button class="btn btn-small" data-action="switch-career-full">改用完整 JD 分析</button></div></div>':
   '<p class="tip">已确认要求覆盖每个所选岗位，但不代表提取完整，分析时仍需注明覆盖局限。</p>')+
  '</section>';
}
function workflowCareer(){
 return workflowFilterContext()+workflowCareerStatus()+careerExportPanel()+analysisFindingsPanel();
}
