/* Compact, page-scoped bulk edit UI for the existing job library. */
const JOB_LIST_PAGE_SIZE=25;
let jobListPage=1,jobSelectedIds=new Set(),jobBulkBusy=false;
let jobBulkField='priority',jobBulkValue='high';
let jobAnalysisIds=null;
function analysisJobs(){return jobAnalysisIds&&state.analysisFlow==='career'?state.jobs.filter(j=>jobAnalysisIds.includes(j.id)):selectedJobs()}
function chosenJobs(){return state.jobs.filter(j=>jobSelectedIds.has(j.id))}
function resetJobSelection(){jobListPage=1;jobSelectedIds.clear()}
// 截止排序：尚未截止的岗位优先；过期岗位随后供核实，无日期的最后。
function jobDeadlineRank(value){
 const days=deadlineDays(value);
 if(days===null)return [2,0];
 return days<0?[1,Math.abs(days)]:[0,days];
}
function sortJobRows(rows){
 const mode=state.jobSort||'priority';
 if(mode==='deadline'){
  return [...rows].sort((a,b)=>{
   const [ga,da]=jobDeadlineRank(a.deadline),[gb,db]=jobDeadlineRank(b.deadline);
   return ga-gb||da-db||b.id-a.id;
  });
 }
 if(mode==='recent')return [...rows].sort((a,b)=>b.id-a.id);
 return rows;
}
function jobWorkbenchPage(){
 const {q,direction,status,priority,city,soon}=state.filters;
 const matches=sortJobRows(selectedJobs()),filtered=Object.entries(state.filters).some(([key,value])=>!['headcount_min','headcount_max','include_unknown_size'].includes(key)&&Boolean(value))||sizeFilterActive(state.filters);
 const pages=Math.max(1,Math.ceil(matches.length/JOB_LIST_PAGE_SIZE));
 jobListPage=Math.min(jobListPage,pages);
 const start=(jobListPage-1)*JOB_LIST_PAGE_SIZE;
 const visible=matches.slice(start,start+JOB_LIST_PAGE_SIZE);
 const onPage=visible.filter(x=>jobSelectedIds.has(x.id)).length;
 const selected=jobSelectedIds.size;
 const updates=jobBulkField==='priority'?priorities:directionOptions(jobBulkValue,true);
 const companiesById=new Map(state.companies.map(c=>[c.id,c]));
 const tool=selected?'<div class="job-bulk-bar" role="group" aria-label="岗位批量操作">'+
  '<strong>已选 '+selected+' 个岗位</strong>'+
  '<label>修改字段<select id="job-bulk-field" class="select">'+opt({priority:'关注优先级',direction:'岗位方向'},jobBulkField)+'</select></label>'+
  '<div data-direction-editor><label>修改为<select id="job-bulk-value" class="select">'+opt(updates,jobBulkValue)+'</select></label>'+
  (jobBulkField==='direction'?customDirectionInput():'')+'</div>'+
  '<button class="btn" data-job-export-chosen>导出所选 JSON</button>'+ 
  '<button class="btn" data-job-analyze-chosen>分析所选岗位</button>'+
  '<button class="btn btn-primary" data-job-bulk-apply'+(jobBulkBusy?' disabled':'')+'>应用到所选岗位</button>'+
  '<button class="btn" data-job-clear>清除选择</button></div>':'';
 const page=matches.length?'<div class="job-list-pagination" aria-label="岗位库分页"><span>显示 '+(start+1)+'–'+
  (start+visible.length)+' 条，共 '+matches.length+' 条</span>'+
  '<div class="page-toolbar"><button class="btn btn-small" data-job-page="'+(jobListPage-1)+'"'+(jobListPage<=1?' disabled':'')+'>上一页</button>'+
  '<span>第 '+jobListPage+' / '+pages+' 页</span>'+
  '<button class="btn btn-small" data-job-page="'+(jobListPage+1)+'"'+(jobListPage>=pages?' disabled':'')+'>下一页</button></div></div>':'';
 return head('岗位库','勾选岗位后可批量修改优先级或方向；不影响原始 JD 和投递状态。')+
  '<div class="filter-bar"><input id="filter-q" class="search" type="search" aria-label="搜索职位、企业或 JD" placeholder="搜索职位、企业或 JD" value="'+esc(q)+'">'+
  '<select id="filter-direction" class="select" aria-label="岗位方向"><option value="">全部方向</option>'+opt(directionOptions(direction),direction)+'</select>'+
  '<select id="filter-city" class="select" aria-label="工作城市"><option value="">全部城市</option>'+
  opt(Object.fromEntries([...new Set(state.jobs.map(j=>j.city).filter(Boolean))].sort().map(v=>[v,v])),city)+'</select>'+
  '</div><details id="job-more-filters" class="job-more-filters"'+(state.jobFiltersOpen?' open':'')+'><summary>更多筛选</summary><div class="filter-bar">'+
  '<select id="filter-status" class="select" aria-label="核验状态"><option value="">全部核验状态</option>'+opt(statuses,status)+'</select>'+
  '<select id="filter-priority" class="select" aria-label="关注优先级"><option value="">全部优先级</option>'+opt(priorities,priority)+'</select>'+
  '<select id="filter-sort" class="select" aria-label="排序方式">'+
  opt({priority:'排序：优先级',deadline:'排序：截止日期（近→远）',recent:'排序：最近收录'},state.jobSort||'priority')+'</select>'+
  '<label class="filter-check"><input type="checkbox" id="filter-soon"'+(soon?' checked':'')+'> 只看 7 天内截止</label></div>'+
  '<div class="filter-bar">'+companyListJobFilter()+'</div><div class="filter-bar">'+companyFactFilters(state.filters,'job-filter')+'</div></details>'+
  '<div id="job-results">'+jobScopeMarkup()+'<div class="results-line"><div><strong>'+(filtered?'筛选结果':'全部岗位')+'</strong><span> · '+matches.length+
  ' 条</span>'+(state.filters.quality?' <span class="tag">只看资料缺失</span>':'')+'</div><div class="page-toolbar"><button class="btn btn-small" data-action="export-jobs-json"'+
  (!matches.length?' disabled':'')+'>导出筛选结果 JSON</button>'+
  (filtered?'<button class="btn btn-small btn-ghost" data-action="clear-filters">清除筛选</button>':'')+
  '</div></div>'+tool+
  (matches.length?'<div class="table-card"><table class="table"><thead><tr>'+
  '<th class="job-select-cell"><label><input type="checkbox" data-job-select-page'+(onPage===visible.length?' checked':'')+
  ' aria-label="选中当前页所有岗位"> 全选本页</label></th>'+
  '<th>岗位 / 公司</th><th>方向</th><th>城市</th><th>截止日期</th><th>企业规模</th><th>优先级</th><th>核验状态</th><th>投递状态</th></tr></thead><tbody>'+
  visible.map(j=>'<tr><td class="job-select-cell"><input type="checkbox" data-job-select="'+j.id+
   '" aria-label="选择岗位 '+esc(j.title)+'"'+(jobSelectedIds.has(j.id)?' checked':'')+'></td>'+
   '<td><button class="table-link" data-job="'+j.id+'">'+esc(j.title)+'</button><span class="company-small">'+esc(j.company)+'</span>'+'</td>'+
   '<td>'+esc(directions[j.direction]||j.direction)+'</td><td>'+fmt(j.city)+'</td><td>'+deadlineCell(j.deadline)+'</td>'+
   '<td>'+companyScaleSummary(companiesById.get(j.company_id))+'</td>'+
   '<td><span class="pill '+esc(j.priority)+'">'+esc(priorities[j.priority])+'</span></td>'+
   '<td><span class="pill '+esc(j.status)+'">'+esc(statuses[j.status])+'</span></td>'+
   '<td>'+esc(stages[j.application_stage]||'未申请')+'</td></tr>').join('')+
   '</tbody></table></div>'+page:
   empty(filtered?'没有匹配的岗位':'岗位库还没有记录',
    filtered?'调整筛选条件，或清除筛选。':'可手动添加或从采集收件箱核对后入库。',
    filtered?'<button class="btn" data-action="clear-filters">清除筛选</button>':
     '<button class="btn btn-primary" data-action="add-job">添加岗位</button>'))+'</div>';
}
async function exportChosenJobs(){
 const rows=chosenJobs();
 if(!rows.length)return;
 await saveExportRoute('/api/exports/jobs',{ids:rows.map(j=>j.id),filters:state.filters},'selected-jobs.json');
 toast('已导出所选 '+rows.length+' 个岗位');
}
function analyzeChosenJobs(){
 const rows=chosenJobs();
 if(!rows.length)return;
 jobAnalysisIds=rows.map(j=>j.id);
 state.analysisFlow='career';
 state.careerMode='full'; // Selected jobs can be analyzed even before JD extraction.
 navigate('analysis');
 toast('已带入 '+rows.length+' 个岗位，可调整分析目标并导出 JSON');
}
async function applyJobBulk(){
 if(jobBulkBusy||!jobSelectedIds.size)return;
 const ids=[...jobSelectedIds],field=jobBulkField;
 const value=jobBulkValue||$('#job-bulk-value').closest('[data-direction-editor]').querySelector('input').value.trim();
 if(!value)return toast('请填写自定义岗位方向');
 const label=field==='priority'?priorities[value]:directions[value]||value;
 if(!confirm('将所选 '+ids.length+' 个岗位的'+(field==='priority'?'优先级':'岗位方向')+
   '统一设为「'+label+'」？不会修改原始 JD 或投递记录。'))return;
 jobBulkValue=value;
 jobBulkBusy=true;render();
 try{
  const r=await api('/jobs/bulk',{method:'POST',
   body:JSON.stringify({ids,[field]:value})});
  jobSelectedIds.clear();
  await refreshAfterWrite('已更新 '+r.changed+' 个岗位（'+r.unchanged+' 个原值不变）',['jobs','summary']);
 }catch(error){toast('批量修改失败：'+error.message)}
 finally{jobBulkBusy=false;if(state.page==='jobs')render()}
}
document.addEventListener('change',event=>{
 const page=event.target.closest('[data-job-select-page]');
 if(page){
  for(const j of selectedJobs().slice((jobListPage-1)*JOB_LIST_PAGE_SIZE,jobListPage*JOB_LIST_PAGE_SIZE))
   if(page.checked)jobSelectedIds.add(j.id);else jobSelectedIds.delete(j.id);
  render();return;
 }
 const single=event.target.closest('[data-job-select]');
 if(single){
  const id=Number(single.dataset.jobSelect);
  if(single.checked)jobSelectedIds.add(id);else jobSelectedIds.delete(id);
  render();return;
 }
 if(event.target.id==='job-bulk-field'){
  jobBulkField=event.target.value;jobBulkValue=jobBulkField==='priority'?'high':'agent';render();return;
 }
 if(event.target.id==='job-bulk-value'){jobBulkValue=event.target.value;return}
 if(event.target.id==='filter-sort'){state.jobSort=event.target.value;jobListPage=1;render();return}
 if(event.target.id==='filter-soon'){state.filters.soon=event.target.checked?'1':'';resetJobSelection();render();return}
});
document.addEventListener('click',event=>{
 const page=event.target.closest('[data-job-page]');
 if(page){const next=Number(page.dataset.jobPage);const size=Math.ceil(selectedJobs().length/JOB_LIST_PAGE_SIZE);
  if(next>=1&&next<=size){jobListPage=next;render();document.querySelector('#content')?.scrollTo({top:0})}return}
 if(event.target.closest('[data-job-clear]')){jobSelectedIds.clear();render();return}
 if(event.target.closest('[data-job-bulk-apply]')){applyJobBulk();return}
 if(event.target.closest('[data-job-export-chosen]')){exportChosenJobs().catch(e=>toast('导出失败：'+e.message));return}
 if(event.target.closest('[data-job-analyze-chosen]')){analyzeChosenJobs();return}
});

document.addEventListener('toggle',event=>{if(event.target.id==='job-more-filters'&&event.target.isConnected)state.jobFiltersOpen=event.target.open;},true);
