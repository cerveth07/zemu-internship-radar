/* User-initiated capture inbox. Collectors run only after explicit search confirmation. */
const capturePlatforms = {boss:'BOSS 直聘',shixiseng:'实习僧',company:'企业招聘页面',other:'其他来源',manual:'手动收集'};
const captureStages = {pending:'待核对',deferred:'暂时跳过',dismissed:'不感兴趣',imported:'已入岗位库',duplicate:'已有同一来源',review:'待重复审核'};

function capturePage(){
 const list=state.captureBatches||[];
 const active=list.find(x=>x.id===state.captureSelected)||null;
  const queue=active?`<section id="capture-results" class="panel capture-panel">${captureResultsMarkup(active)}</section>`:'';
 return head('采集收件箱','检索、筛选并核对实习岗位，支持集中确认入库；也可以保存链接、JD 或截图。')+
 `<div class="notice">搜索结果和截图识别内容均须人工核对后入库。采集不会发送消息或投递。<a href="/api/capture/backup" download>下载含截图的完整备份</a></div>
 <div class="capture-layout"><aside class="panel capture-sidebar" aria-label="采集批次"><h3 class="panel-title">采集批次 <small>${list.length} 个</small></h3>
 ${list.length?`<div class="capture-batch-list">${list.map(b=>`<button type="button" class="capture-batch ${b.id===state.captureSelected?'selected':''}" data-capture-select="${b.id}"><strong>#${b.id} ${esc(b.label)}</strong><small>${esc(capturePlatforms[b.platform]||b.platform)} · ${esc(b.city)} · ${b.counts.imported} 已入库 / ${b.remaining} 待处理 · ${esc({open:'进行中',paused:'已暂停',completed:'已完成'}[b.status])}</small></button>`).join('')}</div>`:'<p class="subtitle">暂无批次；先创建一个批次。</p>'}
 ${active?`<div class="form-actions"><button class="btn btn-small" data-capture-batch-status="${active.status==='open'?'pause':'resume'}" >${active.status==='open'?'暂停':'重新打开'}</button><button class="btn btn-small" data-capture-batch-status="complete" ${active.remaining?'disabled':''}>标记完成</button><button class="btn btn-small" data-capture-refresh="1">刷新进度</button></div>`:''}
 </aside><div class="capture-main"><div class="panel"><h3 class="panel-title">开始新的检索 / 收集资料</h3>
 <form id="capture-batch-form"><div class="field-grid">
 <label class="field">批次名称（可留空自动命名）<input name="label" maxlength="160" placeholder="例如：北京产品运营实习 · 今日搜索" value="${esc(captureSearchDefaults().label||'')}"></label>
 <label class="field">来源平台 <select name="platform">${opt(capturePlatforms,captureSearchDefaults().platform||'boss')}</select></label>
 <label class="field capture-keyword-field">搜索词（每行一个，或用逗号分隔）<textarea name="keyword" rows="3" maxlength="1000" placeholder="产品运营实习&#10;财务分析实习">${esc(captureSearchDefaults().keyword||'')}</textarea></label>
 ${captureCityField(captureCityValues(captureSearchDefaults().city??'北京'))}</div>
 <p class="tip">搜索词与城市的填写方式和首页“开始检索”一致。BOSS 与实习僧批次会在你确认后用于检索；其他来源仅作记录。只想检索的话，首页的“开始检索”更简单。</p><div class="form-actions"><button type="button" class="btn btn-primary" data-capture-search-new="1" ${!captureConnector(captureSearchDefaults().platform||'boss')?'hidden':''}>检索 ${captureSearchLabel(captureSearchDefaults().platform||'boss')} 岗位</button><button type="submit" class="btn">仅创建批次</button></div></form>
 </div>`+
 (active?`${captureConnector(active.platform)?bossSearchPanel(active):''}${queue}<details class="panel capture-panel" ${active.remaining||captureConnector(active.platform)?'':'open'}><summary class="panel-title">加入原始资料 <small>文字、链接或截图</small></summary>
 <form id="capture-input-form"><div class="field-grid">
 <label class="field">来源链接（可选）<input name="source_url" placeholder="https://..." maxlength="2000" value="${esc(captureManualDraft().source_url||'')}"></label>
 <label class="field">岗位名称（可选）<input name="title" placeholder="后续仍需人工核对" value="${esc(captureManualDraft().title||'')}"></label>
 <label class="field">公司名称（可选）<input name="company" placeholder="不确定时可留空" value="${esc(captureManualDraft().company||'')}"></label>
 <label class="field">城市（可选）<input name="city" placeholder="北京" value="${esc(captureManualDraft().city||'')}"></label></div>
 <label class="field">复制的职位说明 / 页面文字 <textarea name="raw_text" rows="7" maxlength="100000" placeholder="在 BOSS 或实习僧打开岗位后，复制页面中的完整 JD，粘贴在这里。可以先只保存部分文字，之后再补充，但不能将其视作完整 JD。">${esc(captureManualDraft().raw_text||'')}</textarea></label>
 <label class="field">附加原始截图（可选，PNG / JPG / WebP，不超过 6MB）<input id="capture-image" type="file" accept="image/png,image/jpeg,image/webp"></label>${state.captureManualFiles?.[active.id]?`<p class="tip">已选截图：${esc(state.captureManualFiles[active.id].name)}（切换页面后仍保留，可重新选择替换）</p>`:''}
 <div class="form-actions"><button type="button" class="btn" data-capture-paste="1">从剪贴板粘贴文字</button><button type="submit" class="btn btn-primary" ${active.status==='open'?'':'disabled'}>保存至收件箱</button></div>
 </form></details>
 <details class="panel capture-panel"><summary class="panel-title">其他导入工具 <small>历史库同步与插件文本</small></summary>
 ${active.platform==='boss'&&!captureNativeBoss()?'<p class="tip">历史库同步读取 BossHunter 的全部历史岗位，不属于本次搜索结果。需要浏览历史记录时，切换结果范围为“整个批次”。</p>':''}
 ${active.platform==='boss'&&!captureNativeBoss()?`<div class="form-actions"><button type="button" class="btn" data-bh-import="1">同步接下来 200 条历史记录</button><button type="button" class="btn" data-bh-reset="1">从头同步历史记录</button></div>`:''}
 <form id="capture-workbench-form"><label class="field">粘贴 Internship Workbench 扩展“采集本页岗位”复制的结果<textarea name="text" rows="5" placeholder="公司：...\n岗位：...\n岗位原文：...\n\n---\n\n公司：..."></textarea></label><div class="form-actions"><button type="submit" class="btn">解析并加入待核对队列</button></div></form>
 </details>
`:'<div class="panel capture-panel"><p class="subtitle">创建批次后即可添加收集到的职位原文。</p></div>')+'</div></div>';
}


function captureConnector(platform=state.captureBatches.find(b=>b.id===state.captureSelected)?.platform){return {boss:'bosshunter',shixiseng:'shixiseng'}[platform]||null}
function captureSearchLabel(platform=state.captureBatches.find(b=>b.id===state.captureSelected)?.platform){return platform==='shixiseng'?'实习僧':'BOSS'}
function captureSearchDefaults(){return {...captureDefaults(),...(state.captureCreationDraft||{})}}
function captureManualDraft(){return state.captureManualDrafts?.[state.captureSelected]||{}}
const capturePrefsKey='radar.capture.search.v1';
function captureDefaults(){try{return JSON.parse(localStorage.getItem(capturePrefsKey)||'{}')}catch{return {}}}
function captureCityValues(value){return [...new Set(String(value||'').split(/[,，;；、\n]+/).map(x=>x.trim()).filter(Boolean))]}
const CAPTURE_COMMON_CITIES=typeof HOME_SEARCH_COMMON_CITIES!=='undefined'?HOME_SEARCH_COMMON_CITIES:['北京','上海','深圳','杭州','广州','成都','南京','武汉','西安','苏州'];
function captureCityChips(cities){
 const selected=new Set(cities),common=CAPTURE_COMMON_CITIES.map(city=>`<button type="button" class="city-chip" data-capture-city="${esc(city)}" aria-pressed="${selected.has(city)}">${esc(city)}</button>`).join('');
 const custom=cities.filter(city=>!CAPTURE_COMMON_CITIES.includes(city)).map(city=>`<span class="city-chip is-custom" aria-pressed="true">${esc(city)}<button type="button" class="city-chip-remove" data-capture-city-remove="${esc(city)}" aria-label="移除城市 ${esc(city)}">×</button></span>`).join('');
 const status=cities.length?`已选 ${cities.length} 个城市：${cities.join('、')}`:'尚未选择城市；检索前至少选一个。';
 return `<div class="city-chips" role="group" aria-label="常用城市">${common}</div>${custom?`<div class="city-chips city-chips-custom" aria-label="其他城市">${custom}</div>`:''}<p class="tip" id="capture-city-status" role="status">${esc(status)}</p>`;
}
function captureCityField(cities){
 const names=typeof homeSearchCityTable!=='undefined'&&homeSearchCityTable.boss.length?homeSearchCityTable.boss:CAPTURE_COMMON_CITIES;
 return `<fieldset class="field home-search-city-field capture-city-field"><legend>城市 <small>可多选，可添加其他城市</small></legend>
 <input type="hidden" name="city" value="${esc(cities.join('，'))}"><div id="capture-city-picker">${captureCityChips(cities)}</div>
 <div class="home-search-city-add"><input id="capture-city-input" list="capture-cities" maxlength="60" autocomplete="off" placeholder="其他城市，输入后回车添加" aria-label="添加其他城市" aria-describedby="capture-city-status"><button type="button" class="btn btn-small" data-capture-city-add="1">添加</button></div>
 <datalist id="capture-cities">${names.map(city=>`<option value="${esc(city)}"></option>`).join('')}</datalist></fieldset>`;
}
function setCaptureCities(cities){
 const form=$('#capture-batch-form'),input=form?.querySelector('[name=city]');if(!input)return false;
 const next=[...new Set(cities)],joined=next.join('，');
 if(joined.length>100){toast('城市合计最多 100 字，请减少城市');return false}
 input.value=joined;const picker=$('#capture-city-picker');if(picker)picker.innerHTML=captureCityChips(next);
 state.captureCreationDraft=formdata(form);return true;
}
function commitCaptureCityInput(){
 const input=$('#capture-city-input');if(!input)return true;
 const add=captureCityValues(input.value);if(!add.length)return true;
 if(add.some(city=>city.length>60||/[\u0000-\u001f\u007f]/.test(city))){toast('城市名称最多 60 字，不能包含控制字符');return false}
 const form=$('#capture-batch-form');
 if(!setCaptureCities([...captureCityValues(form.querySelector('[name=city]').value),...add]))return false;
 input.value='';input.focus();return true;
}
function normalizeCaptureEntries(value){return captureCityValues(value).join('，')}
function rememberCaptureDefaults(extra={}){try{localStorage.setItem(capturePrefsKey,JSON.stringify({...captureDefaults(),...extra}))}catch{}}
function captureRun(){const run=state.bossConnector?.search_run;return run&&Number(run.batch_id)===Number(state.captureSelected)&&(!run.platform||captureConnector(run.platform)===captureConnector())?run:null}
function captureNativeBoss(){return captureConnector()==='bosshunter'&&(captureRun()?.preferences?.connector==='native'||Number(state.bossConnector?.batch_id)===Number(state.captureSelected)&&state.bossConnector?.connector==='native')}
function captureRunning(){return ['starting','running','stopping','pending','queued','unknown'].includes(captureRun()?.status)}
function captureStoppable(){return captureRunning()&&Boolean(captureRun()?.task_id)}
function captureResumable(){return captureNativeBoss()&&['pending','running'].includes(captureRun()?.status)&&['login_required','verification_required'].includes(captureRun()?.platform_state?.phase)}
function bossAnyRunning(){return captureRunning()||['starting','running','stopping'].includes(state.bossConnector?.task?.status)}
function captureVisibleItems(){
 const f=state.captureResultFilters||{status:'pending',city:'',q:'',salary:'',availability:''};
 return (state.captureItems||[]).filter(item=>{
  const d=item.draft||{},text=[d.title,d.company,d.city,d.salary,d.duration,d.onsite_days,item.raw_text,d.jd_text].join(' ').toLowerCase();
  return (!f.status||item.status===f.status)&&(!f.city||String(d.city||'').includes(f.city))&&(!f.q||text.includes(f.q.toLowerCase()))&&(!f.salary||String(d.salary||'').toLowerCase().includes(f.salary.toLowerCase()))&&(!f.availability||text.includes(f.availability.toLowerCase()));
 });
}
function captureResultsMarkup(active){
 const run=captureRun(),items=captureVisibleItems(),all=state.captureItems||[],f=state.captureResultFilters||{status:'pending',city:'',q:'',salary:'',availability:''};
 const selected=items.find(x=>x.id===state.captureDetailSelected)||items[0];if(selected)state.captureDetailSelected=selected.id;
 const latest=active.latest_search_run_id||run?.id;
 return `<h3 class="panel-title">${latest&&!state.captureHistory?'本次搜索结果':'批次资料'} <small>${items.length} / ${all.length} 条 · ${all.filter(x=>x.status==='pending').length} 条待核对</small></h3>
 <div class="capture-result-filters"><label class="field">处理状态<select data-capture-filter="status">${opt({'pending':'待核对','':'全部状态',deferred:'暂时跳过',dismissed:'不感兴趣',imported:'已入库',duplicate:'已有来源',review:'待重复审核'},f.status)}</select></label><label class="field">城市<select data-capture-filter="city">${opt({'':'全部城市',...Object.fromEntries([...new Set(all.map(x=>x.draft?.city).filter(Boolean))].map(c=>[c,c]))},f.city)}</select></label><label class="field">岗位 / 企业 / JD<input data-capture-filter="q" value="${esc(f.q)}" placeholder="筛选当前结果"></label><label class="field">薪资包含<input data-capture-filter="salary" value="${esc(f.salary)}" placeholder="例如：200"></label><label class="field">出勤 / 时长原文包含<input data-capture-filter="availability" value="${esc(f.availability)}" placeholder="例如：3天、6个月"></label>${latest?`<label class="field">结果范围<select id="capture-result-scope"><option value="run" ${state.captureHistory?'':'selected'}>本次搜索</option><option value="history" ${state.captureHistory?'selected':''}>整个批次（含历史同步）</option></select></label>`:''}</div>
 <p class="tip">以上筛选仅在已采集结果中匹配文字；岗位未写明的薪资、出勤与时长不会推断。采集时间不代表岗位仍在招聘。</p>
 ${captureBulkActions(items,active)}<div id="capture-result-body">${captureResultBody(items,selected,run)}</div>`;
}
function captureResultBody(items,selected,run){
 if(!items.length)return `<div class="capture-empty">${(state.captureItems||[]).length?'没有符合当前筛选的岗位。可切换“全部状态”或清空条件。':captureRunning()?'正在采集；结束后会自动显示本次结果。已有部分结果时可以点击“同步本次结果”。':run?'本次搜索还没有显示的岗位。采集结束后会自动同步，也可点击“同步本次结果”。':'还没有岗位。填写关键词与城市后启动检索，或手动加入资料。'}</div>`;
 return `<div class="capture-results-split"><div class="capture-result-list" aria-label="岗位结果">${items.map(i=>`<article class="capture-result-row ${i.id===selected?.id?'selected':''}">${captureBulkCheckbox(i)}<button class="capture-result-select" data-capture-detail="${i.id}" aria-pressed="${i.id===selected?.id}"><strong>${esc(i.draft?.title||'岗位名称待核对')}</strong><span>${esc(i.draft?.company||'企业待核对')} · ${esc(i.draft?.city||'城市未写明')}</span><span>${esc(i.draft?.salary||'薪资未写明')} · ${esc(captureStages[i.status]||i.status)}</span><small>${esc({new:'新来源',already_saved:'岗位库已收录',jd_changed:'JD 有变化'}[i.source_state]||'')}${!String(i.draft?.jd_text||i.raw_text||'').trim()?' · 缺少 JD':''}</small></button><div class="capture-actions">${i.status==='pending'?`<button class="btn btn-small" data-capture-review="${i.id}">核对并入库</button>`:''}${['deferred','dismissed'].includes(i.status)?`<button class="btn btn-small" data-capture-disposition="reopen" data-item="${i.id}">恢复待核对</button>`:''}</div></article>`).join('')}</div><div class="capture-result-detail" aria-label="岗位详情">${captureDetailMarkup(selected)}</div></div>`;
}
function captureDetailMarkup(i){if(!i)return '';const d=i.draft||{},url=d.source_url??i.source_url;
 return `<h3>${esc(d.title||'岗位名称待核对')}</h3><p>${esc(d.company||'企业待核对')} · ${esc(d.city||'城市未写明')} · ${esc(d.salary||'薪资未写明')}</p>${companyFactSummary(d.company_evidence?.company===d.company?d.company_evidence:{},true)}<p class="tip">采集 / 最近观察：${esc(i.observed_at||i.created_at||'未记录')}${i.reused_snapshot?' · 使用本地已有快照，建议打开来源核验最新 JD':''}${d.last_detail_at?' · 详情读取：'+esc(d.last_detail_at):''}</p><p>${url?`<a href="${esc(secureLink(url))}" target="_blank" rel="noopener noreferrer">打开 ${esc(capturePlatforms[i.platform]||capturePlatforms[state.captureBatches.find(b=>b.id===state.captureSelected)?.platform]||'来源')} 原始岗位 ↗</a>`:'未提供原始链接'} ${i.has_screenshot?`<a href="/api/capture/items/${i.id}/screenshot" target="_blank" rel="noopener noreferrer">查看截图</a>`:''}</p><div class="capture-detail-actions">${i.status==='pending'?`<button class="btn btn-primary" data-capture-open-review="${i.id}">核对并入库</button><button class="btn" data-capture-disposition="defer" data-item="${i.id}">暂时跳过</button><button class="btn" data-capture-disposition="dismiss" data-item="${i.id}">不感兴趣</button>`:['deferred','dismissed'].includes(i.status)?`<button class="btn" data-capture-disposition="reopen" data-item="${i.id}">恢复待核对</button>`:''}${i.job_id||i.known_job_id?`<button class="btn" data-job="${i.job_id||i.known_job_id}">打开已入库岗位</button>`:''}${i.review_id?'<span class="pill">到重复审核处理</span>':''}</div><pre class="jd-box capture-result-jd">${esc(d.jd_text??i.raw_text??'没有 JD，请打开来源补充。')}</pre>`;
}
function updateCaptureResults(){const active=state.captureBatches.find(x=>x.id===state.captureSelected),box=$('#capture-results');if(!active||!box)return;const focused=document.activeElement,key=focused?.dataset?.captureFilter,range=key&&focused.tagName==='INPUT'?[focused.selectionStart,focused.selectionEnd]:null;const scroll=box.querySelector('.capture-result-list')?.scrollTop||0;box.innerHTML=captureResultsMarkup(active);const list=box.querySelector('.capture-result-list');if(list)list.scrollTop=scroll;if(key){const input=box.querySelector('[data-capture-filter='+CSS.escape(key)+']');input?.focus({preventScroll:true});if(range)input?.setSelectionRange(...range);}}
function bossSearchPanel(active){
 const shixi=active.platform==='shixiseng',native=captureNativeBoss();
 return `<section class="panel capture-panel"><h3 class="panel-title">${captureSearchLabel(active.platform)} 批量检索</h3>
 <p class="subtitle">${esc(active.keyword||'搜索词未填写')} · ${esc(active.city||'城市未填写')}。检索结束自动显示本次匹配的岗位，再集中或逐条核对入库。</p>
 <div id="boss-connection-status" class="notice" aria-live="polite">${bossStatusMarkup()}</div>
 <div class="page-toolbar capture-search-toolbar"><label class="field">每个关键词、每个城市的页数<select id="boss-max-pages" class="select">${[1,2,3].map(n=>`<option value="${n}" ${n===(state.bossMaxPages||captureDefaults().max_pages||1)?'selected':''}>${n} 页${n===1?'（初次验证）':''}</option>`).join('')}</select></label>
 ${shixi?`<label class="field">采集方式<select id="shixi-collection-mode">${opt({collect:'发现岗位并读取详情',discover:'仅发现岗位（不读详情）'},state.shixiCollectionMode||'collect')}</select></label><label class="field">本次详情尝试上限<input id="shixi-max-jobs" type="number" min="1" max="60" step="1" value="${esc(state.shixiMaxJobs||60)}"></label><span class="tip">每次采集都重新读取原始岗位详情；读取失败时保留失败状态，不使用旧详情。实习僧仅检索实习岗位。</span>`:`<label class="capture-check"><input type="checkbox" id="boss-internship-only" ${(state.bossInternshipOnly??captureDefaults().internship_only)!==false?'checked':''}> 仅实习岗位</label><a class="btn btn-small" data-bh-panel ${native?'hidden':''} href="http://127.0.0.1:8686" target="_blank" rel="noopener noreferrer">打开 BossHunter 面板</a>`}</div>
 <div class="form-actions"><button type="button" class="btn" data-bh-status="1">检查连接 / 刷新采集状态</button>
 <button type="button" class="btn btn-primary" data-bh-collect="1" ${active.status==='open'&&state.bossConnector?.capabilities?.matched_results&&state.bossConnector?.connected&&!state.bossConnectionError&&!bossAnyRunning()&&!state.bossStartBusy&&!state.bossSyncBusy?'':'disabled'}>预览并启动检索</button>
 <button type="button" class="btn" data-bh-sync-run="1" ${captureRun()?'':'disabled'}>同步本次结果</button>
 <button type="button" class="btn" data-bh-resolve="1" ${!shixi&&!native&&['starting','unknown'].includes(captureRun()?.status)&&!captureRun()?.task_id?'':'hidden'}>检查并解除启动等待</button>
 <button type="button" class="btn btn-primary" data-bh-resume="1" ${captureResumable()?'':'hidden'}>继续检索</button>
 <button type="button" class="btn" data-bh-stop="1" ${captureStoppable()?'':'disabled'}>停止采集，保留结果</button></div>
 <p class="tip">${shixi?'采集时会显示实习僧浏览器窗口；需要登录或遇到验证码时，请在该窗口人工完成。':native?'应用自动打开 Chrome 或 Edge；需要登录或验证时，在该窗口完成后点击继续检索。':'保持专用 Chrome 开启并登录 BOSS；遇到验证码请在该窗口完成，然后检查连接。'}本次搜索包含已见过且再次匹配的岗位；不会发送消息或投递。</p>
 ${shixi?'<div id="capture-discovery">'+captureDiscoveryMarkup()+'</div>':''}
 </section>`
}
function captureDiscovery(){const data=state.captureDiscovery;return data&&Number(data.batch_id)===Number(state.captureSelected)&&Number(data.run_id)===Number(captureRun()?.id)?data:null}
function captureHydratable(){return captureConnector()==='shixiseng'&&Boolean(captureRun())&&!bossAnyRunning()&&Number(captureDiscovery()?.pending_count||0)>0}
function captureDiscoveryMarkup(){
 const d=captureDiscovery();
 if(!d)return captureRun()?`<p class="tip">${state.captureDiscoveryError?esc(state.captureDiscoveryError)+'；点击检查连接重试。':'正在读取列表候选与请求成本…'}</p>`:'<p class="tip">实习僧可先发现列表岗位，再补齐详情；尚未读取的候选不会进入 JD 核对队列。</p>';
 const costs=d.costs||{},candidates=d.candidates||[],pending=Number(d.pending_count||0);
 return `<h4>列表候选与采集成本</h4>${d.costs_reported===false?'<p class="notice">此任务缺少完整成本回执，历史访问次数未知；以下仅显示已记录的访问。</p>':''}<p class="tip">列表尝试 ${Number(costs.list_attempts||0)} 次 · 详情尝试 ${Number(costs.detail_attempts||0)} 次${Number(costs.cache_hits||0)?' · 历史缓存复用 '+Number(costs.cache_hits)+' 条':''}${Number(costs.company_attempts||0)?' · 公司页 '+Number(costs.company_attempts)+' 次':''}${Number(costs.unknown_attempts||0)?' · '+Number(costs.unknown_attempts)+' 次访问结果待核对':''}</p>${Number(costs.unknown_attempts||0)?'<p class="notice">部分访问回执不完整，实际消耗存在不确定性；这些访问已保留预算记录。</p>':''}<div class="form-actions"><button type="button" class="btn" data-bh-hydrate="1" ${captureHydratable()&&!state.bossStartBusy&&!state.bossSyncBusy?'':'disabled'}>补齐未读详情（${pending} 条）</button></div><p class="tip">以下是列表发现记录；只有取得 JD 后才会进入下方核对队列。补齐操作读取本批次待处理候选，不重新搜索列表；额度不足的候选继续保留。</p>${candidates.length?`<details><summary>查看 ${candidates.length} 条列表候选${pending?' · '+pending+' 条待补齐':''}</summary><div class="row-list">${candidates.map(c=>{const link=secureLink(c.url||''),hits=Array.isArray(c.hits)?c.hits:[];return `<div class="rowline"><div><strong>${esc(c.title||'岗位名称未写明')}</strong><small>${esc(c.company||'企业未写明')} · ${esc(c.city||'城市未写明')} · ${esc({pending:'待读取详情',unread:'待读取详情',cached:'复用已有详情',complete:'详情已读取',completed:'详情已读取',hydrated:'详情已读取',failed:'详情读取失败，待重试',incomplete:'详情不完整，待补齐',filtered:'已按条件筛除',unknown:'详情状态待确认',closed:'来源提示已关闭',skipped:'未读取详情'}[c.detail_state]||'待读取详情')}</small>${c.last_detail_at?'<small>最近详情读取：'+esc(c.last_detail_at)+'</small>':''}${hits.length?'<small>命中：'+esc(hits.map(h=>typeof h==='string'?h:[h.keyword,h.city].filter(Boolean).join(' · ')).join('；'))+'</small>':''}</div>${link!=='#'?'<a class="btn btn-small" href="'+esc(link)+'" target="_blank" rel="noopener noreferrer">打开原始岗位</a>':''}</div>`}).join('')}</div></details>`:'<p class="tip">本次尚未发现列表候选。</p>'}`;
}
async function refreshCaptureDiscovery(run,batch){
 if(captureConnector()!=='shixiseng'||!run)return;
 try{const result=await api(`/capture/search-runs/${run.id}/discovery`);if(state.page!=='capture'||Number(state.captureSelected)!==Number(batch)||Number(captureRun()?.id)!==Number(run.id))return;state.captureDiscovery={...result,batch_id:batch,run_id:run.id};state.captureDiscoveryError=''}
 catch(error){if(Number(state.captureSelected)!==Number(batch)||Number(captureRun()?.id)!==Number(run.id))return;state.captureDiscovery=null;state.captureDiscoveryError='候选与成本读取失败：'+error.message}
 const box=$('#capture-discovery');if(box)box.innerHTML=captureDiscoveryMarkup();updateBossControls();
}
let bossPollTimer,bossPollFailures=0,bossSyncFailures=0,bossConnectionRequest=null;
function captureClosedJobsMarkup(progress){
 const jobs=Array.isArray(progress.closed_jobs)?progress.closed_jobs:[];
 return jobs.length?'<details><summary>来源提示已关闭（'+jobs.length+' 条）</summary><p class="tip">以下岗位在本次读取时已关闭；旧资料仍保留。</p><div class="row-list">'+jobs.map(job=>'<div class="rowline"><div><strong>'+esc(job.title||'岗位名称未写明')+'</strong><small>'+esc(job.company||'企业未写明')+(job.checked_at?' · 读取于 '+esc(job.checked_at):'')+'</small></div><a class="btn btn-small" href="'+esc(secureLink(job.url||''))+'" target="_blank" rel="noopener noreferrer">原始岗位</a></div>').join('')+'</div></details>':'';
}
function bossStatusMarkup(){
 const result=state.bossConnector;if(!result||Number(result.batch_id)!==Number(state.captureSelected))return '正在恢复本批次采集状态…';
 if(state.bossConnectionError)return `<span class="error">${esc(state.bossConnectionError)}</span><div>已保留本地结果。可点击“检查连接 / 刷新采集状态”重试。</div>`;
 const shixi=captureConnector()==='shixiseng',native=captureNativeBoss(),checks=result.readiness?.checks||[];
 if(!result.connected&&native)return 'BOSS 浏览器采集尚未就绪。'+(checks.map(c=>esc(c.message||c.id)).join('；')||esc((result.readiness?.messages||[]).join('；'))||'请使用完整应用，并安装 Chrome 或 Edge。');
 if(!result.connected)return shixi?`实习僧采集尚未就绪。${checks.map(c=>esc(c.message||c.id)).join('；')||esc((result.readiness?.messages||[]).join('；'))||'请检查采集运行环境后重试。'}`:'BOSS 连接器尚未安装或未启动。<button class="btn btn-small" data-boss-settings>配置与启动</button> <button class="btn btn-small" data-quick-job>先粘贴 JD</button>';
 const run=captureRun(),progress=run?.platform_state||result.platform_state||{},labels={starting:'正在启动',unknown:'状态待确认，请检查服务',completed_with_shortage:'采集结束，结果不足',completed_with_errors:'采集结束，部分失败',pending:'准备中',queued:'准备中',running:'采集中',stopping:'正在停止',completed:'采集结束',failed:'采集失败',stopped:'已停止'},active=state.captureBatches.find(b=>b.id===state.captureSelected),prefs=run?.preferences||{keywords:captureCityValues(active?.keyword),cities:captureCityValues(active?.city)};
 return `<strong>${shixi?'实习僧本地采集已就绪':native?'BOSS 浏览器采集已就绪':'BossHunter 本地服务已连接'}</strong>${result.capabilities?.matched_results?'':`<div class="error">${shixi?'实习僧采集器尚未支持本次任务结果，请检查采集环境。':'当前 BossHunter 尚未支持按本次检索返回结果。请运行链路启动脚本加载本机适配后重试。'}</div>`}<div>${run?'本次搜索':'本批次搜索条件'}：${esc((prefs.keywords||[]).join('、')||'未开始')} · ${esc((prefs.cities||[]).join('、')||'未设置城市')}</div>${checks.length?`<div>${checks.map(c=>`${esc(c.message||c.id)}${['pass','ok','ready','passed'].includes(c.status)?'':'（需处理）'}`).join(' · ')}</div><div>${shixi?'请在可见的实习僧浏览器窗口完成登录或验证码。':native?'首次使用时请在自动打开的 BOSS 窗口完成登录。':'检测到 BOSS 页面连接并不代表已登录；请在专用 Chrome 确认登录状态。'}</div>`:''}${bossAnyRunning()&&!captureRunning()?'<div>其他采集任务正在运行，请等待其结束后再开始新检索。</div>':''}${run?`<div>本批次任务：${esc(labels[run.status]||run.status)} · ${esc(run.task_id||'')} · 匹配 ${Number(run.total||0)} 条 / 已同步 ${Number(run.synced_count||0)} 条${run.error?`<div class="error">${esc(run.error)}</div>`:''}</div>`:'<div>本批次尚未开始检索。</div>'}${(shixi||native)&&(progress.phase||progress.page||progress.seen||progress.message)?`<div>采集进度：${esc({starting:'正在启动',login_required:'等待登录',verification_required:'等待人工验证',search:'正在检索',list:'读取列表',detail:'读取岗位详情',details:'读取岗位详情',completed:'采集完成',stopped:'已停止',failed:'采集失败'}[progress.phase]||progress.phase||'处理中')}${progress.page?' · 第 '+esc(progress.page)+' 页':''}${progress.seen!==undefined?' · 已发现 '+esc(progress.seen)+' 条':''}${progress.message?'<div>'+esc(progress.message)+'</div>':''}</div>`:''}${captureClosedJobsMarkup(progress)}${result.result_error||run?.result_error?`<div class="error">结果读取失败：${esc(result.result_error||run.result_error)}。请检查连接后重试；已有资料会保留。</div>`:''}${state.bossSyncError?`<div class="error">本次结果同步失败：${esc(state.bossSyncError)}。点击“同步本次结果”重试。</div>`:''}`;
}
function updateBossControls(){
 const box=$('#boss-connection-status');if(box)box.innerHTML=bossStatusMarkup();
 const active=state.captureBatches.find(x=>x.id===state.captureSelected),running=captureRunning();
 document.querySelectorAll('[data-bh-panel]').forEach(b=>b.hidden=captureNativeBoss());
 document.querySelectorAll('[data-bh-resume]').forEach(b=>{b.hidden=!captureResumable();b.disabled=Boolean(state.bossResumeBusy)});
 document.querySelectorAll('[data-bh-collect]').forEach(b=>b.disabled=bossAnyRunning()||state.bossStartBusy||state.bossSyncBusy||active?.status!=='open'||!state.bossConnector?.connected||Boolean(state.bossConnectionError)||!state.bossConnector?.capabilities?.matched_results);
 document.querySelectorAll('[data-bh-hydrate]').forEach(b=>b.disabled=!captureHydratable()||state.bossStartBusy||state.bossSyncBusy||active?.status!=='open'||!state.bossConnector?.connected||Boolean(state.bossConnectionError));
 document.querySelectorAll('[data-bh-import],[data-bh-reset]').forEach(b=>b.disabled=bossAnyRunning()||active?.status!=='open');
 document.querySelectorAll('[data-bh-sync-run]').forEach(b=>b.disabled=!captureRun()||state.bossSyncBusy);
 document.querySelectorAll('[data-bh-resolve]').forEach(b=>b.hidden=!(captureConnector()==='bosshunter'&&!captureNativeBoss()&&['starting','unknown'].includes(captureRun()?.status)&&!captureRun()?.task_id));
 document.querySelectorAll('[data-bh-stop]').forEach(b=>b.disabled=!captureStoppable()||state.bossStopBusy);
}
function scheduleBossPoll(delay=4000){clearTimeout(bossPollTimer);const batch=state.captureSelected;if(state.page==='capture')bossPollTimer=setTimeout(()=>{if(state.page==='capture'&&state.captureSelected===batch)refreshBossConnection().catch(()=>{})},delay)}
async function refreshBossConnection(preflight=false){
 const batchId=state.captureSelected,connector=captureConnector();if(!batchId||!connector)return;
 if(bossConnectionRequest){try{await bossConnectionRequest}catch{}if(Number(state.captureSelected)!==Number(batchId)||captureConnector()!==connector)return;}
 clearTimeout(bossPollTimer);
 const request=(async()=>{
  try{
   const result=await api(`/capture/connectors/${connector}/status?batch_id=${batchId}${preflight?'&preflight=true':''}`);
   if(state.captureSelected!==batchId||state.page!=='capture'||captureConnector()!==connector)return result;
   const prior=state.bossConnector;
   if(Number(prior?.batch_id)===Number(batchId)){if(!preflight&&!result.readiness)result.readiness=prior.readiness;const current=prior.search_run;if(current&&Number(current.id)>Number(result.search_run?.id||0))result.search_run=current;}
   state.bossConnector={...result,batch_id:batchId};state.bossConnectionError='';if(result.connected)bossPollFailures=0;
   updateBossControls();
   const run=captureRun();
   if(run){const active=state.captureBatches.find(b=>b.id===batchId);if(active)active.latest_search_run_id=run.id;
    if(result.connected&&['completed','completed_with_shortage','completed_with_errors','failed','stopped','interrupted'].includes(run.status)&&!state.captureHistory&&state.bossAutoSyncedRun!==`${run.id}:${run.total}:${run.status}`){await syncBossRun(true);}
   }
   if(connector==='shixiseng'&&run)await refreshCaptureDiscovery(run,batchId);
   if(state.page!=='capture'||Number(state.captureSelected)!==Number(batchId)||captureConnector()!==connector)return result;
   if(!result.connected){bossPollFailures++;if(run&&bossPollFailures<=5)scheduleBossPoll(Math.min(30000,2000*2**(bossPollFailures-1)));}else if(bossAnyRunning()&&(captureRun()?.status!=='unknown'||captureRun()?.task_id))scheduleBossPoll();else if(state.bossSyncError&&bossSyncFailures<=3)scheduleBossPoll(Math.min(30000,3000*2**(bossSyncFailures-1)));
   return result;
  }catch(error){
   if(state.captureSelected!==batchId||state.page!=='capture'||captureConnector()!==connector)throw error;
   state.bossConnectionError=error.message;updateBossControls();
   bossPollFailures++;if(bossPollFailures<=5)scheduleBossPoll(Math.min(30000,2000*2**(bossPollFailures-1)));
   throw error;
  }
 })();bossConnectionRequest=request;
 try{return await request}finally{if(bossConnectionRequest===request)bossConnectionRequest=null}
}
async function syncBossRun(automatic=false){
 const run=captureRun(),batch=state.captureSelected;if(!run||state.bossSyncBusy)return;
 state.bossSyncBusy=true;state.bossSyncError='';updateBossControls();
 try{
  let result,offset=-1;do{
   result=await api(`/capture/search-runs/${run.id}/sync`,{method:'POST',body:JSON.stringify({confirmed:true,limit:200})});
   if(result.has_more&&(Number(result.scanned||0)===0||Number(result.next_offset)===offset))throw new Error('同步未推进，请重试');offset=Number(result.next_offset);
  }while(result.has_more);
  if(state.captureSelected!==batch||Number(captureRun()?.id)!==Number(run.id))return;
  if(result.search_run)state.bossConnector.search_run=result.search_run;
  await refreshCapture(false);updateCaptureResults();
  state.bossAutoSyncedRun=`${run.id}:${result.search_run?.total??run.total}:${result.search_run?.status??run.status}`;bossSyncFailures=0;
  if(!automatic)toast('本次搜索结果已同步，仍须逐条核对后入库');
 }catch(error){if(state.captureSelected===batch&&Number(captureRun()?.id)===Number(run.id)){state.bossSyncError=error.message;bossSyncFailures++;}if(!automatic)throw error}
 finally{state.bossSyncBusy=false;updateBossControls()}
}
async function previewBossSearch(modeOverride=null){
 if(state.bossStartBusy||state.bossSyncBusy||bossAnyRunning())return;state.bossStartBusy=true;updateBossControls();
 const batchId=Number(state.captureSelected),connector=captureConnector(),label=captureSearchLabel(),pages=Number($('#boss-max-pages')?.value||1),internshipOnly=connector==='shixiseng'||$('#boss-internship-only')?.checked!==false,requestedMode=connector==='shixiseng'?(modeOverride||$('#shixi-collection-mode')?.value||state.shixiCollectionMode||'collect'):'collect',forceRefresh=true;
 try{
  const connection=await refreshBossConnection(true);if(state.page!=='capture'||Number(state.captureSelected)!==batchId||captureConnector()!==connector)return;if(connection?.readiness?.ok===false)throw new Error((connection.readiness.messages||[]).join('；')||'浏览器或登录尚未准备好，请完成后检查连接');
  if(!connection?.capabilities?.matched_results)throw new Error(connector==='shixiseng'?'请检查实习僧采集运行环境后重试':'请运行 BOSS 链路启动脚本更新本机采集服务');
  const result=await api(`/capture/connectors/${connector}/search-preview`,{method:'POST',body:JSON.stringify({batch_id:batchId,max_pages:pages,internship_only:internshipOnly,...(connector==='shixiseng'?{max_jobs:Number($('#shixi-max-jobs')?.value||state.shixiMaxJobs||60),collection_mode:requestedMode,force_refresh:forceRefresh}:{})})});
  if(state.page!=='capture'||Number(state.captureSelected)!==batchId)return;
  const native=result.connector==='native',prefs=result.preferences,actualPages=Number((prefs.max_pages&&typeof prefs.max_pages==='object'?prefs.max_pages[connector==='shixiseng'?'shixiseng':'boss']:prefs.max_pages)??pages),total=prefs.keywords.length*prefs.cities.length*actualPages,maxJobs=Number(result.max_jobs??prefs.max_jobs??60),mode=result.collection_mode||prefs.collection_mode||requestedMode;
  const scope=mode==='hydrate'?`读取本批次待处理候选；不访问搜索列表；本次最多尝试 ${maxJobs} 条详情`: `每个词、每个城市 ${actualPages} 页，最多 ${total} 页搜索结果${connector==='shixiseng'?(mode==='discover'?'；仅保存列表候选，不读取详情':`；本次最多重新读取 ${maxJobs} 条详情`):''}`;
  modal(`确认 ${label} 只读检索`,`<div class="notice">${connector==='shixiseng'?'将按以下条件在可见浏览器中检索实习僧；需要登录或验证码时，请在采集窗口人工完成。':native?'应用会打开 BOSS 浏览器窗口；需要登录或验证时，处理完成后点击继续检索。':'以下搜索设置会保存到本机 BossHunter。请确认专用 Chrome 已登录 BOSS。'}</div><dl class="search-confirmation"><dt>搜索词</dt><dd>${prefs.keywords.map(k=>`<span class="tag">${esc(k)}</span>`).join('')}</dd><dt>城市</dt><dd>${esc(prefs.cities.join('、'))}</dd><dt>类型</dt><dd>${internshipOnly?'仅实习岗位':'包含实习与其他岗位'}</dd><dt>范围</dt><dd>${scope}</dd>${mode!=='discover'?'<dt>详情</dt><dd>每次重新打开原始岗位；已关闭和读取失败会分别提示，不复用旧详情。</dd>':''}<dt>采集后</dt><dd>${mode==='discover'?'显示列表候选，可随后补齐详情；列表记录不直接入库':'取得 JD 的岗位进入核对队列；核对确认后入库，未读详情继续保留'}</dd>${result.warnings?.length?'<dt>注意</dt><dd>'+result.warnings.map(w=>esc(w)).join('；')+'</dd>':''}</dl><p class="tip">仅采集，不调用 AI、不发送消息或投递。遇到验证码，请在${connector==='shixiseng'?'实习僧采集窗口':native?'BOSS 采集窗口':'专用 Chrome'}中完成。</p><form id="boss-collect-form" data-connector="${connector}" data-batch="${batchId}" data-pages="${actualPages}" data-force-refresh="${forceRefresh}" data-max-jobs="${maxJobs}" data-collection-mode="${mode}" data-internship="${internshipOnly}">${actions()}</form>`,true);
  $('#boss-collect-form button[type=submit]').textContent=mode==='hydrate'?'确认补齐未读详情':mode==='discover'?'确认发现列表岗位':'确认设置并开始采集';
 }finally{state.bossStartBusy=false;updateBossControls()}
}
async function refreshCapture(fullRender=true){
 const previous=state.captureSelected,batches=await api('/capture/batches');
 if(previous&&!batches.some(b=>b.id===previous))batches.unshift(await api('/capture/batches/'+previous));
 state.captureBatches=batches;
 if(!batches.some(b=>b.id===state.captureSelected))state.captureSelected=batches.find(b=>b.id===Number(captureDefaults().batch_id))?.id||batches[0]?.id||null;
 if(previous!==state.captureSelected)state.captureHistory=false;
 const batch=state.captureSelected,active=batches.find(b=>b.id===batch),runId=Math.max(Number(active?.latest_search_run_id||0),Number(captureRun()?.id||0))||null,historyAtRequest=Boolean(state.captureHistory);if(active&&runId)active.latest_search_run_id=runId;
 const items=batch?await api(`/capture/batches/${batch}/items${runId&&!state.captureHistory?'?run_id='+runId:''}`):[];
 if(state.captureSelected!==batch||Boolean(state.captureHistory)!==historyAtRequest||(!historyAtRequest&&Number(captureRun()?.id||0)>Number(runId||0)))return;
 state.captureItems=items;rememberCaptureDefaults({batch_id:batch});
 if(state.page==='capture'&&fullRender)render();
}
function resumeCaptureSearch(){clearTimeout(bossPollTimer);if(state.page!=='capture')return;const batch=state.captureSelected;refreshCapture(false).then(()=>{if(state.page==='capture'&&state.captureSelected===batch){updateCaptureResults();if(captureConnector(state.captureBatches.find(b=>b.id===batch)?.platform))refreshBossConnection().catch(()=>{});}}).catch(error=>toast('资料刷新失败：'+error.message));}

function captureReview(id){
 const item=(state.captureItems||[]).find(x=>x.id===id);if(!item)return;
 if(item.status!=='pending')return toast('该岗位已经处理');
 modal('人工核对岗位 · #'+id,`<div class="notice">提交后才会建立岗位档案。JD 默认使用你复制的文字，但必须人工检查是否完整。如果截图已进行 OCR，下面是待核对文本；请对照截图补漏并纠错。相似岗位只会进入重复审核，不会覆盖已有 JD。</div>
 ${item.has_screenshot?`<p><a class="btn btn-small" target="_blank" rel="noopener noreferrer" href="/api/capture/items/${id}/screenshot">打开原始截图核对 ↗</a> <button type="button" class="btn btn-small" data-capture-ocr="${id}">重新 OCR 识别截图</button></p>`:''}
 ${item.draft.company_evidence?.company===item.draft.company?companyFactSummary(item.draft.company_evidence,true):''}
 <form id="capture-review-form" data-capture-item="${id}"><div class="field-grid">
 ${field('company','企业名称 *','','text',true)}${field('title','岗位名称 *','','text',true)}
 ${field('city','城市')}${directionField(item.draft.direction||'other')}
 ${field('salary','薪资原文')}${field('source_url','原始招聘链接')}${field('duration','实习时长原文')}${field('onsite_days','每周出勤原文')}
 ${field('posted_at','发布日期','','date')}${field('deadline','截止日期','','date')}
 </div><label class="field">原始 JD（核对全部段落后再提交）<textarea name="jd_text" rows="11" maxlength="100000" required>${esc(item.draft.jd_text ?? item.raw_text)}</textarea></label>
 <label class="field">任职 / 毕业资格原文（可选）<textarea name="eligibility" rows="2"></textarea></label>
 <div class="form-actions"><button type="button" class="btn" data-capture-save-draft="${id}">保存核对草稿</button><button type="submit" class="btn btn-primary">确认内容并提交岗位库</button><button type="button" class="btn" data-capture-review-next="1">入库并查看下一条</button></div><div id="form-error" class="error"></div></form>`,true);
 for(const k of ['company','title','city','salary','eligibility','duration','onsite_days','posted_at','deadline'])$('#capture-review-form [name='+k+']').value=item.draft[k]||'';
 $('#capture-review-form [name=source_url]').value=item.draft.source_url??item.source_url??'';
}

async function refreshSavedCapture(message){try{await refreshCapture();toast(message)}catch(error){state.loadError=error.message;render();toast(message+'；刷新失败，请重新加载。')}}
async function saveCaptureDraft(){
 const form=$('#capture-review-form');if(!form||!beginFormWrite(form))return;
 try{
  const itemId=Number(form.dataset.captureItem);
  await api('/capture/items/'+itemId,{method:'PATCH',body:JSON.stringify(formdata(form))});
  await refreshSavedCapture('核对草稿已保存，尚未提交岗位库');
  if(typeof resumeCaptureIntake==='function'&&await resumeCaptureIntake(itemId,false))return;
  await closeModal(true);
 }catch(error){showFormError(form,error)}finally{endFormWrite(form)}
}
async function readCaptureScreenshot(id, control){
 const original=control?.textContent,wasDisabled=control?.disabled;
 if(control){control.disabled=true;control.textContent='正在识别截图…'}
 try{
  const status=await api('/capture/ocr/status');
  if(status.on_demand&&!status.installed){
   const label='首次使用，正在下载文字识别组件（约 '+Math.ceil(status.download_bytes/1048576)+' MiB）…';
   if(control)control.textContent=label;
   toast(label);
  }
  return await api(`/capture/items/${id}/ocr`,{method:'POST'});
 }finally{
  if(control){control.textContent=original;control.disabled=wasDisabled}
 }
}
async function recordCapture(event){
 event.preventDefault();const f=event.target,submittedBatch=state.captureSelected,startAfterCreate=f.dataset.startAfterCreate==='1';if(!beginFormWrite(f))return;
 try{
  if(f.id==='boss-collect-form'){
   if(state.bossSyncBusy)throw new Error('本次结果正在同步，请等待完成后再检索');
   state.bossStartBusy=true;updateBossControls();
   const result=await api(`/capture/connectors/${f.dataset.connector||'bosshunter'}/start-collect`,{method:'POST',body:JSON.stringify({confirmed:true,batch_id:Number(f.dataset.batch),max_pages:Number(f.dataset.pages),internship_only:f.dataset.internship!=='false',...(f.dataset.connector==='shixiseng'?{max_jobs:Number(f.dataset.maxJobs||60),collection_mode:f.dataset.collectionMode||'collect',force_refresh:f.dataset.forceRefresh==='true'}:{})})});
   if(state.captureSelected===submittedBatch){state.captureHistory=false;state.bossAutoSyncedRun=null;state.captureDiscovery=null;state.captureDiscoveryError='';if(result.search_run){state.bossConnector={...(state.bossConnector||{}),batch_id:Number(f.dataset.batch),search_run:result.search_run};const batch=state.captureBatches.find(x=>x.id===Number(f.dataset.batch));if(batch)batch.latest_search_run_id=result.search_run.id;}}
   await closeModal(true);toast('只读采集已启动，结束后自动显示本次结果');await refreshCapture(false);updateCaptureResults();
   try{await refreshBossConnection()}catch{toast('采集已启动，但状态暂未刷新，请点击检查连接重试')}
  }else if(f.id==='capture-batch-form'){
    if(!commitCaptureCityInput())return;
    const data=formdata(f);data.keyword=normalizeCaptureEntries(data.keyword);data.city=normalizeCaptureEntries(data.city);
    if(startAfterCreate&&(!data.keyword||!data.city))throw new Error('检索前请填写关键词和城市');
    rememberCaptureDefaults({platform:data.platform,keyword:data.keyword,city:data.city});const r=await api('/capture/batches',{method:'POST',body:JSON.stringify(data)});
   if(state.page==='capture'&&state.captureSelected===submittedBatch){state.captureCreationDraft=null;state.captureSelected=r.id;state.captureHistory=false;state.bossConnector=null;}await refreshSavedCapture('采集批次已创建');if(startAfterCreate&&state.page==='capture'&&state.captureSelected===r.id)await previewBossSearch();else resumeCaptureSearch();
  }else if(f.id==='capture-input-form'){
   const data=formdata(f);const file=$('#capture-image').files?.[0]||state.captureManualFiles?.[submittedBatch];
   if(!data.raw_text.trim()&&!data.source_url.trim()&&!file)throw new Error('请提供文字、链接或截图');
   if(file && file.size>6*1024*1024)throw new Error('图片不能超过 6MB');
   const created=await api(`/capture/batches/${submittedBatch}/items`,{method:'POST',body:JSON.stringify(data)});
   let intakeMessage=created.already_exists?'已存在相同原始资料，不重复新增':'已加入本地采集收件箱';
   if(file&&!created.already_exists){
     const response=await fetch(`/api/capture/items/${created.id}/screenshot`,{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});
     if(!response.ok)throw new Error('文字已保存，但截图上传失败；记录 #'+created.id+' 可重新检查');
     // OCR automatically after a screenshot-only intake. Never automatically commit.
     if(!data.raw_text.trim()){
       try{
         const result=await readCaptureScreenshot(created.id,f.querySelector('button[type=submit]'));
         if(result.text)await api(`/capture/items/${created.id}`,{method:'PATCH',body:JSON.stringify({jd_text:result.text})});
         intakeMessage=result.text?'OCR 已写入待核对草稿，须检查全部段落':'截图已保存，OCR 没有识别到文字，请人工补录';
       }catch(err){intakeMessage='截图已保存，可在人工核对时重新识别。'+err.message}
     }
   }
   if(state.captureSelected===submittedBatch){state.captureHistory=true;if(state.captureManualDrafts)delete state.captureManualDrafts[submittedBatch];if(state.captureManualFiles)delete state.captureManualFiles[submittedBatch];}await refreshSavedCapture(intakeMessage);
  }else if(f.id==='capture-workbench-form'){
   if(!state.captureSelected)throw new Error('请先建立批次');
   const result=await api(`/capture/batches/${submittedBatch}/workbench-text`,{method:'POST',body:JSON.stringify({text:f.querySelector('[name=text]').value})});
   if(state.captureSelected===submittedBatch)state.captureHistory=true;await refreshSavedCapture(`已从插件文本新增 ${result.created} 条候选，${result.already_exists} 条重复`);
  }else if(f.id==='capture-review-form'){
   const itemId=Number(f.dataset.captureItem), data=formdata(f),next=f.dataset.reviewNext==='1';
   // The confirmed JD is allowed to differ from the initial clipboard text: manual proofreading.
   await api(`/capture/items/${itemId}`,{method:'PATCH',body:JSON.stringify(data)});
   const r=await api(`/capture/items/${itemId}/commit`,{method:'POST',body:JSON.stringify(data)});
   await refreshAfterWrite({imported:'岗位已收录',duplicate:'相同来源已存在，未重复录入',review:'已进入人工重复审核'}[r.status]||'已处理',['jobs','summary','reviews','quality']);
   let queueFresh=true;
   try{await refreshCapture(false)}catch(error){queueFresh=false;toast('处理已保存，但收件箱刷新失败，请手动重新加载')}
   await closeModal(true);
   if(typeof resumeCaptureIntake==='function'&&await resumeCaptureIntake(itemId))return;
   if(next){
    if(!queueFresh)return;
    const following=captureVisibleItems().find(x=>x.status==='pending'&&x.id!==itemId);
    if(following)captureReview(following.id);else toast('当前筛选范围没有其他待核对岗位');
   }
  }
 }catch(error){showFormError(f,error)}finally{endFormWrite(f);delete f.dataset.reviewNext;if(f.id==='boss-collect-form'){state.bossStartBusy=false;updateBossControls()}}
}

document.addEventListener('submit',e=>{if(['capture-batch-form','capture-input-form','capture-workbench-form','capture-review-form','boss-collect-form'].includes(e.target.id))recordCapture(e)});
document.addEventListener('input',e=>{const creation=e.target.closest('#capture-batch-form');if(creation)state.captureCreationDraft=formdata(creation);const manual=e.target.closest('#capture-input-form');if(manual){state.captureManualDrafts={...(state.captureManualDrafts||{}),[state.captureSelected]:formdata(manual)};}const key=e.target.dataset.captureFilter;if(!key)return;state.captureResultFilters={status:'pending',city:'',q:'',salary:'',availability:'',...(state.captureResultFilters||{}),[key]:e.target.value};captureSelectedForBulk.clear();updateCaptureResults();});
document.addEventListener('change',async e=>{
 if(e.target.closest('#capture-batch-form')){state.captureCreationDraft=formdata(e.target.closest('form'));const search=$('[data-capture-search-new]');if(search){const platform=$('#capture-batch-form [name=platform]').value;search.hidden=!captureConnector(platform);search.textContent=`检索 ${captureSearchLabel(platform)} 岗位`;}}
 if(e.target.id==='capture-image'){state.captureManualFiles={...(state.captureManualFiles||{}),[state.captureSelected]:e.target.files?.[0]};}
 if(e.target.id==='shixi-collection-mode')state.shixiCollectionMode=e.target.value;
 if(e.target.id==='shixi-max-jobs')state.shixiMaxJobs=Number(e.target.value);
 if(e.target.id==='boss-max-pages'){state.bossMaxPages=Number(e.target.value);rememberCaptureDefaults({max_pages:state.bossMaxPages})}
 if(e.target.id==='boss-internship-only'){state.bossInternshipOnly=e.target.checked;rememberCaptureDefaults({internship_only:e.target.checked})}
 if(e.target.id==='capture-result-scope'){captureSelectedForBulk.clear();state.captureHistory=e.target.value==='history';await refreshCapture(false);updateCaptureResults();}
});
document.addEventListener('click',async e=>{
 const cityChip=e.target.closest('[data-capture-city]'),cityRemove=e.target.closest('[data-capture-city-remove]');
 if(cityChip||cityRemove){const current=captureCityValues($('#capture-batch-form [name=city]')?.value),city=cityChip?cityChip.dataset.captureCity:cityRemove.dataset.captureCityRemove;setCaptureCities(cityRemove||current.includes(city)?current.filter(c=>c!==city):[...current,city]);return}
 if(e.target.closest('[data-capture-city-add]')){commitCaptureCityInput();return}
 if(e.target.closest('[data-capture-search-new]')){const form=$('#capture-batch-form');if(!form||form.inert||state.bossStartBusy||state.bossSyncBusy)return;if(bossAnyRunning()){toast('已有采集任务正在运行，请等待结束后再检索');return;}if(!form.reportValidity())return;form.dataset.startAfterCreate='1';form.requestSubmit(form.querySelector('button[type=submit]'));delete form.dataset.startAfterCreate;return}
 if(e.target.closest('[data-capture-review-next]')){const form=$('#capture-review-form');if(form&&!form.inert){if(!form.reportValidity())return;form.dataset.reviewNext='1';form.requestSubmit(form.querySelector('button[type=submit]'));delete form.dataset.reviewNext;}return}
 const detail=e.target.closest('[data-capture-detail]');if(detail){state.captureDetailSelected=Number(detail.dataset.captureDetail);const items=captureVisibleItems();$('#capture-result-body').innerHTML=captureResultBody(items,items.find(x=>x.id===state.captureDetailSelected),captureRun());return}
 const disposition=e.target.closest('[data-capture-disposition]');if(disposition){if(disposition.disabled)return;disposition.disabled=true;try{await api(`/capture/items/${Number(disposition.dataset.item)}/disposition`,{method:'PATCH',body:JSON.stringify({action:disposition.dataset.captureDisposition})});await refreshCapture(false);updateCaptureResults();toast('岗位处理状态已更新')}catch(error){toast(error.message)}finally{if(disposition.isConnected)disposition.disabled=false}return}

 if(e.target.closest('[data-capture-save-draft]')){await saveCaptureDraft();return}
 const ocr=e.target.closest('[data-capture-ocr]');if(ocr){
  const id=Number(ocr.dataset.captureOcr),area=$('#capture-review-form [name=jd_text]');
  if(area?.value.trim() && !confirm('将 OCR 识别内容替换当前未保存的 JD 草稿？'))return;
  try{const r=await readCaptureScreenshot(id,ocr);if(!r.text)throw new Error('截图没有识别出文字');
   area.value=r.text;toast('已填入识别文本，仍须人工核对后提交');}catch(err){toast('OCR 失败：'+err.message)}return;
 }
 const bh=e.target.closest('[data-bh-status],[data-bh-collect],[data-bh-hydrate],[data-bh-import],[data-bh-reset],[data-bh-sync-run],[data-bh-stop],[data-bh-resume],[data-bh-resolve]');if(bh){
  try{
   if(bh.hasAttribute('data-bh-status')){bossPollFailures=0;bossSyncFailures=0;await refreshBossConnection(true);}
   else if(bh.hasAttribute('data-bh-resolve')){const run=captureRun();if(!run)return;if(!confirm('检查 BossHunter 是否仍有采集任务。只有确认没有任务运行时才解除启动等待；已保存的结果会保留。继续？'))return;bh.disabled=true;try{await api(`/capture/search-runs/${run.id}/resolve-start`,{method:'POST',body:JSON.stringify({confirmed:true})});await refreshBossConnection();toast('启动等待已解除，可以重新预览检索')}finally{if(bh.isConnected)bh.disabled=false}}
   else if(bh.hasAttribute('data-bh-sync-run')){await syncBossRun();}
   else if(bh.hasAttribute('data-bh-resume')){const run=captureRun();if(!run)return;state.bossResumeBusy=true;updateBossControls();try{await api(`/capture/search-runs/${run.id}/resume`,{method:'POST',body:JSON.stringify({confirmed:true})});await refreshBossConnection();toast('正在检查登录与验证状态')}finally{state.bossResumeBusy=false;updateBossControls()}}
   else if(bh.hasAttribute('data-bh-stop')){const run=captureRun();if(!run)return;state.bossStopBusy=true;updateBossControls();try{await api(`/capture/search-runs/${run.id}/stop`,{method:'POST',body:JSON.stringify({confirmed:true})});await refreshBossConnection();toast('停止请求已发送，已采集结果会保留')}finally{state.bossStopBusy=false;updateBossControls()}}
   else if(bh.hasAttribute('data-bh-hydrate')){if(bh.disabled||!captureHydratable())return;await previewBossSearch('hydrate');}
   else if(bh.hasAttribute('data-bh-collect')){await previewBossSearch();
   }else{
    const status=await refreshBossConnection();if(bossAnyRunning())throw new Error('采集尚未结束，请完成后再同步');
    const reset=bh.hasAttribute('data-bh-reset');
    if(reset&&!confirm('重新扫描 BossHunter 岗位列表第一页？不会删除已保存岗位，也不会重发消息。'))return;
    const r=await api('/capture/connectors/bosshunter/import',{method:'POST',body:JSON.stringify({batch_id:state.captureSelected,confirmed:true,limit:200,restart_from_first:reset})});
    state.captureHistory=true;await refreshCapture();toast(`扫描 ${r.scanned} 条（游标 ${r.next_offset}），新收录 ${r.created} 条，重复 ${r.already_exists} 条，缺 JD ${r.missing_jd} 条；仍须人工审核`);
   }
  }catch(err){toast('连接器：'+err.message)}return;
 }
 const choose=e.target.closest('[data-capture-select]');if(choose){state.captureSelected=Number(choose.dataset.captureSelect);state.captureHistory=false;state.captureDetailSelected=null;state.captureDiscovery=null;state.captureDiscoveryError='';state.bossConnector=null;state.bossConnectionError='';state.bossSyncError='';bossPollFailures=0;bossSyncFailures=0;await refreshCapture();resumeCaptureSearch();return}
 const review=e.target.closest('[data-capture-review],[data-capture-open-review]');if(review){captureReview(Number(review.dataset.captureReview||review.dataset.captureOpenReview));return}
 const change=e.target.closest('[data-capture-batch-status]');if(change){try{await api(`/capture/batches/${state.captureSelected}`,{method:'PATCH',body:JSON.stringify({action:change.dataset.captureBatchStatus})});await refreshCapture();toast('批次状态已更新')}catch(err){toast(err.message)}return}
 if(e.target.closest('[data-capture-refresh]')){await refreshCapture();resumeCaptureSearch();return}
 if(e.target.closest('[data-capture-paste]')){try{const text=await navigator.clipboard.readText();const field=$('#capture-input-form [name=raw_text]');field.value=text;field.dispatchEvent(new Event('input',{bubbles:true}));toast('已粘贴剪贴板文字')}catch{toast('浏览器不允许直接读取剪贴板，请手动 Ctrl+V')}return}
});


// Preparing a saved query creates no platform requests until explicit confirmation.
window.preparePlannerSearch = async function(action, planId) {
 if(!canLeaveModal()||state.plannerPrepareBusy)return;
 if(state.bossStartBusy||state.bossSyncBusy)throw new Error('采集正在启动或同步，请稍后准备下一项');
 state.plannerPrepareBusy=true;
 try {
  const prepared=await api(`/planner/plans/${Number(planId)}/actions/${encodeURIComponent(action.id)}/prepare`,{method:'POST'});
  if(state.page!=='planner'){toast('计划查询已准备，返回规划页可打开采集');return;}
  const query=prepared.action;
  state.captureSelected=prepared.batch_id;
  state.captureHistory=false;state.bossConnector=null;state.bossConnectionError='';
  state.bossMaxPages=query.list_pages;state.shixiMaxJobs=query.detail_attempts;state.shixiCollectionMode='collect';state.shixiForceRefresh=true;state.captureDiscovery=null;state.captureDiscoveryError='';
  state.bossInternshipOnly=true;
  rememberCaptureDefaults({batch_id:prepared.batch_id});
  await refreshCapture(false);
  if(state.page!=='planner')return;
  navigate('capture');
  toast('已准备计划查询；检查连接后，预览并确认检索');
 } finally {state.plannerPrepareBusy=false;}
};
if(typeof document!=='undefined')document.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&e.target.id==='capture-city-input'){e.preventDefault();commitCaptureCityInput()}});
