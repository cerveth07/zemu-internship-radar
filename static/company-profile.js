const unknownCompanyFact='待录入';
function jobScopeLabels(filters=state.filters){
 const labels=[];
 const list=state.companyListData?.lists?.find(x=>String(x.id)===String(filters.company_list_id));
 const company=state.companies?.find(x=>String(x.id)===String(filters.company_id));
 if(filters.company_list_id)labels.push('企业名单：'+(list?.name||'已删除名单'));
 if(filters.company_id)labels.push('企业：'+(company?.name||'已删除企业'));
 for(const [key,title,map] of [['q','关键词'],['direction','方向',directions],['status','状态',statuses],['priority','优先级',priorities],['city','城市'],['sector','行业']])if(filters[key])labels.push(title+'：'+(map?.[filters[key]]||filters[key]));
 if(Number(filters.headcount_min)>0||filters.headcount_max!=null&&filters.headcount_max!=='')labels.push('企业规模：'+sizeRangeLabel(filters));
 if(filters.include_unknown_size===false||filters.include_unknown_size==='false')labels.push('规模待录入：不包含');
 if(filters.soon)labels.push('7 天内截止');if(filters.quality)labels.push('资料缺失');
 return labels;
}
function jobScopeMarkup(analysis=false){
 const chosen=analysis&&typeof jobAnalysisIds!=='undefined'&&jobAnalysisIds&&state.analysisFlow==='career';
 const labels=chosen?['手动选择的岗位']:jobScopeLabels();
 const count=(analysis?analysisJobs():selectedJobs()).length;
 return '<div class="job-scope" role="status"><strong>当前范围 · 共 '+count+' 个岗位</strong><div>'+ (labels.length?labels.map(x=>'<span class="scope-tag">'+esc(x)+'</span>').join(''):'<span>岗位库全部岗位</span>')+'</div></div>';
}
function hasRecordedHeadcount(company){return company?.headcount_min!=null||company?.headcount_max!=null;}
function companySizeLabel(company){return hasRecordedHeadcount(company)?company.headcount_text||headcountLabel(company):'待录入';}
function headcountLabel({headcount_min:min,headcount_max:max}){
 if(min!=null&&max!=null)return min===max?min+' 人':min+'–'+max+' 人';
 return min!=null?min+' 人以上':max!=null?max+' 人以下':'待录入';
}
function sizeFilterActive(filters){return Number(filters.headcount_min)>0||filters.headcount_max!=null&&filters.headcount_max!==''||filters.include_unknown_size==='false'||filters.include_unknown_size===false;}
function companyFactMatch(company,filters){
 if(filters.sector&&(company?.sector||unknownCompanyFact)!==filters.sector)return false;
 if(!hasRecordedHeadcount(company))return filters.include_unknown_size!=='false'&&filters.include_unknown_size!==false;
 const lower=filters.headcount_min==null||filters.headcount_min===''?null:Number(filters.headcount_min);
 const upper=filters.headcount_max==null||filters.headcount_max===''?null:Number(filters.headcount_max);
 return (upper===null||company.headcount_min==null||company.headcount_min<=upper)&&(lower===null||company.headcount_max==null||company.headcount_max>=lower);
}
function companySource(url,date,label){return url?' <a href="'+esc(secureLink(url))+'" target="_blank" rel="noopener noreferrer">'+label+' ↗</a>'+(date?' <small>'+esc(date)+'</small>':''):'';}
function companyScaleSummary(company,links=false){return '<span class="company-scale">'+esc(companySizeLabel(company))+(links?companySource(company?.headcount_source_url,company?.headcount_observed_on,'规模来源'):'')+'</span>';}
function companyFactSummary(company,links=false){return '<div class="company-facts"><span>行业：'+esc(company?.sector||'待录入')+(links?companySource(company?.sector_source_url,company?.sector_observed_on,'行业来源'):'')+'</span><span>企业规模：'+companyScaleSummary(company,links)+'</span></div>';}
function sizeFilterCeiling(filters){
 const counts=(state.companies||[]).flatMap(c=>[c.headcount_min,c.headcount_max]).concat([Number(filters.headcount_min),Number(filters.headcount_max)]).filter(v=>Number.isFinite(v)&&v>0);
 return Math.min(1e9,10**Math.ceil(Math.log10(Math.max(10000,...counts))));
}
function sizeSliderPosition(value,ceiling,upper=false){return value==null||value===''?(upper?1001:0):Math.round(Math.log1p(Number(value))/Math.log1p(ceiling)*1000);}
function sizeSliderPeople(value,ceiling,upper=false){return upper&&Number(value)===1001?'':String(Math.round(Math.expm1(Number(value)/1000*Math.log1p(ceiling))));}
function sizeRangeLabel(filters){const min=Number(filters.headcount_min||0),max=filters.headcount_max;if(max==null||max==='')return min?min.toLocaleString('zh-CN')+' 人以上':'不限人数';return min.toLocaleString('zh-CN')+'–'+Number(max).toLocaleString('zh-CN')+' 人';}
function companyFactFilters(filters,scope){
 const sectors=[...new Set((state.companies||[]).map(c=>c.sector).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
 if(filters.sector&&filters.sector!==unknownCompanyFact&&!sectors.includes(filters.sector))sectors.push(filters.sector);
 const ceiling=sizeFilterCeiling(filters),min=filters.headcount_min??'',max=filters.headcount_max??'';
 const lo=sizeSliderPosition(min,ceiling),hi=sizeSliderPosition(max,ceiling,true);
 return '<label class="company-industry-filter">企业行业<select id="'+scope+'-industry" class="select" data-company-fact-filter="sector" data-scope="'+scope+'">'+opt([['','全部行业'],...sectors.map(s=>[s,s]),[unknownCompanyFact,'行业待录入']],filters.sector)+'</select></label>'+
  '<fieldset class="company-size-filter" data-size-scope="'+scope+'" data-size-ceiling="'+ceiling+'"><legend class="size-legend">企业规模（人数）</legend>'+
  '<div class="size-head" aria-hidden="true">企业规模</div><output class="size-range-label" aria-live="polite">'+esc(sizeRangeLabel(filters))+'</output>'+
  '<div class="size-control" title="拖动滑块，或输入人数后按回车；与企业人数区间有交集即显示">'+
  '<input type="number" min="0" max="1000000000" step="1" value="'+esc(min)+'" placeholder="0" data-size-number="min" aria-label="输入企业规模人数下限"><span class="size-sep" aria-hidden="true">–</span>'+
  '<input type="number" min="0" max="1000000000" step="1" value="'+esc(max)+'" placeholder="不限" data-size-number="max" aria-label="输入企业规模人数上限"><span class="size-unit" aria-hidden="true">人</span>'+
  '<div class="size-track"><span class="size-mask size-mask-lo" style="width:'+(lo/10)+'%"></span><span class="size-mask size-mask-hi" style="width:'+(100-hi/10.01)+'%"></span>'+
  '<input id="'+scope+'-size-min" type="range" min="0" max="1000" step="1" value="'+lo+'" data-size-bound="min" aria-label="企业规模人数下限" aria-valuetext="'+(min||0)+' 人">'+
  '<input id="'+scope+'-size-max" type="range" min="0" max="1001" step="1" value="'+hi+'" data-size-bound="max" aria-label="企业规模人数上限" aria-valuetext="'+(max===''?'不限':max+' 人')+'"></div></div>'+
  '<label class="filter-check"><input type="checkbox" data-size-unknown '+(filters.include_unknown_size!=='false'&&filters.include_unknown_size!==false?'checked':'')+'> 包含规模待录入企业</label>'+
  '<small class="size-input-error" data-size-error role="status" hidden></small></fieldset>';
}
function companyEvidenceFields(){return '<section class="mt-4"><h3 class="panel-title">企业行业与规模</h3><p class="tip">采集到的规模会自动填入，也可以直接录入人数或区间。来源链接选填；只知道一端时，另一端可留空。</p><div class="field-grid">'+field('sector','企业行业','例如 制造业')+field('sector_source_url','行业来源链接（选填）','https://')+field('sector_observed_on','行业记录日期','','date')+field('headcount_text','企业规模（人数或区间）','例如 100–499人')+field('headcount_min','人数下限（选填）','例如 100','number')+field('headcount_max','人数上限（选填）','例如 499','number')+field('headcount_source_url','规模来源链接（选填）','https://')+field('headcount_observed_on','规模记录日期','','date')+'</div></section>';}
function companyEvidencePayload(data){for(const key of ['headcount_min','headcount_max'])data[key]=data[key]===''?null:Number(data[key]);return data;}
function sizeScopeFilters(scope){return scope==='company-filter'?state.companyFilters:scope==='demand-filter'?state.demandFilters:scope==='gap-filter'?gapFilters:state.filters;}
let sizePaintFrame=null;
function paintSizeResults(scope){
 if(scope==='demand-filter'||scope==='gap-filter')return;
 if(scope==='company-filter')resetListingPage('companies');else resetJobSelection();
 if(sizePaintFrame!==null)cancelAnimationFrame(sizePaintFrame);
 sizePaintFrame=requestAnimationFrame(()=>{
  sizePaintFrame=null;
  const id=scope==='company-filter'?'company-results':'job-results',current=document.getElementById(id);if(!current)return;
  const template=document.createElement('template');template.innerHTML=scope==='company-filter'?companiesPage():jobWorkbenchPage();
  const next=template.content.querySelector('#'+id);if(next)current.replaceWith(next);
 });
}
function updateSizeControls(box,filters){
 const ceiling=Number(box.dataset.sizeCeiling);
 box.querySelector('output').textContent=sizeRangeLabel(filters);
 const track=box.querySelector('.size-track');
 for(const bound of ['min','max']){
  const value=filters['headcount_'+bound]??'',slider=box.querySelector('[data-size-bound='+bound+']'),number=box.querySelector('[data-size-number='+bound+']');
  slider.value=sizeSliderPosition(value,ceiling,bound==='max');slider.setAttribute('aria-valuetext',value===''?(bound==='min'?'0 人':'不限'):value+' 人');
  if(document.activeElement!==number)number.value=value;
 }
 if(track){track.querySelector('.size-mask-lo').style.width=box.querySelector('[data-size-bound=min]').value/10+'%';track.querySelector('.size-mask-hi').style.width=(100-box.querySelector('[data-size-bound=max]').value/10.01)+'%';}
}
function applySizeInput(input){
 const box=input.closest('[data-size-scope]');if(!box)return false;
 const filters=sizeScopeFilters(box.dataset.sizeScope),bound=input.dataset.sizeBound||input.dataset.sizeNumber;
 const key=()=>[filters.headcount_min??'',filters.headcount_max??'',filters.include_unknown_size!=='false'&&filters.include_unknown_size!==false].join('|'),before=key();
 const error=box.querySelector('[data-size-error]');
 if(error){error.hidden=true;error.textContent='';}box.querySelectorAll('[aria-invalid]').forEach(el=>el.removeAttribute('aria-invalid'));
 if(input.hasAttribute('data-size-unknown'))filters.include_unknown_size=String(input.checked);
 else{
  const isNumber=input.hasAttribute('data-size-number');
  const value=isNumber?input.value:sizeSliderPeople(input.value,Number(box.dataset.sizeCeiling),bound==='max');
  const draft={...filters,['headcount_'+bound]:value},lower=Number(draft.headcount_min||0),upper=draft.headcount_max==null||draft.headcount_max===''?Infinity:Number(draft.headcount_max);
  if(isNumber&&(!input.validity.valid||lower>upper)){
   input.setAttribute('aria-invalid','true');
   if(error){error.textContent=!input.validity.valid?'请输入 0–1,000,000,000 之间的整数，修改尚未应用。':'下限不能大于上限，修改尚未应用。';error.hidden=false;}
   return false;
  }
  filters['headcount_'+bound]=value==='0'&&bound==='min'?'':value;
  if(lower>upper)filters['headcount_'+(bound==='min'?'max':'min')]=String(bound==='min'?lower:upper);
  if(isNumber)box.dataset.sizeCeiling=String(Math.max(Number(box.dataset.sizeCeiling),sizeFilterCeiling(filters)));
 }
 updateSizeControls(box,filters);
 if(key()!==before){box.dataset.sizeChanged='true';paintSizeResults(box.dataset.sizeScope);}
 return true;
}
function commitSizeInput(input){
 const box=input.closest('[data-size-scope]');if(!box||!applySizeInput(input)||box.dataset.sizeChanged!=='true')return;
 delete box.dataset.sizeChanged;
 if(box.dataset.sizeScope==='demand-filter')refreshDemandStats();
 if(box.dataset.sizeScope==='gap-filter'){gapPage=1;refreshSkillGap();}
}
if(typeof document!=='undefined'){
 document.addEventListener('input',event=>{if(event.target.matches('[data-size-bound]'))applySizeInput(event.target);});
 document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('[data-size-number]')){event.preventDefault();commitSizeInput(event.target);}});
 document.addEventListener('change',event=>{
  const box=event.target.closest('[data-size-scope]');
  if(box){commitSizeInput(event.target);return;}
  const input=event.target.closest('[data-company-fact-filter]');if(!input)return;
  const filters=sizeScopeFilters(input.dataset.scope);filters[input.dataset.companyFactFilter]=input.value;
  if(input.dataset.scope==='demand-filter')refreshDemandStats();else if(input.dataset.scope==='gap-filter')refreshSkillGap();else{if(input.dataset.scope==='company-filter')resetListingPage('companies');else resetJobSelection();render();document.getElementById(input.id)?.focus();}
 });
}
