/* Shared policy for the toolbar and analysis-page result import. */
function analysisResultCommit(payload, preview){
 return {...payload,dry_run:false,acknowledge_stale:Boolean(preview.stale_job_ids?.length),
  acknowledge_career_stale_token:preview.career_stale_token||''};
}
function analysisResultChanges(preview, data){
 data=data||(typeof state!=='undefined'?state:{});
 const jobs=ids=>(ids||[]).map(id=>{const row=(data.jobs||[]).find(j=>j.id===id);return row?row.company+' · '+row.title:'岗位 #'+id;}).join('、');
 const experiences=ids=>(ids||[]).map(id=>{const row=(data.evidence||[]).find(e=>e.id===id);return row?(row.description||row.skill||'个人经历').slice(0,60):'原经历 #'+id;}).join('、');
 const parts=[];
 if(preview.career_stale_evidence_ids?.length)parts.push('个人经历已更新：'+experiences(preview.career_stale_evidence_ids)+'。');
 if(preview.career_missing_evidence_ids?.length)parts.push('部分原经历已移除：'+experiences(preview.career_missing_evidence_ids)+'。');
 const changedJobs=[...new Set([...(preview.stale_job_ids||[]),...(preview.career_stale_job_ids||[]),...(preview.career_stale_requirement_job_ids||[])])];
 if(changedJobs.length)parts.push('岗位或要求已有更新：'+jobs(changedJobs)+'。');
 if(preview.career_missing_job_ids?.length)parts.push('部分原岗位已移除：'+jobs(preview.career_missing_job_ids)+'。');
 if(!parts.length&&preview.career_stale_token)parts.push('分析所用资料已有更新。');
 return parts.length?parts.join(' ')+'可以继续导入，建议会保留来源变化提示。':'';
}
