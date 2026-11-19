/** Shared task lifecycle for the BOSS and Shixiseng DOM collectors.
 * Platform adapters own navigation and page interpretation; this file owns only
 * bounded work, source identity, ordered snapshots and interruption handling.
 */
import { writeFile, rename } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { cleanText } from '../vendor/internship-workbench/normalize.mjs'

const garbled = /[\uE000-\uF8FF\uFFFD]/u
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
const clean = value => { const text=cleanText(value); return garbled.test(text)?'':text }

export function mergeCandidate(candidates, list, hit, jobURL) {
  const url = jobURL(list?.url)
  if (!url) return null
  let candidate = candidates.find(value => value.url === url)
  if (!candidate) {
    if (candidates.length >= 400) return null
    candidate = {url, title:clean(list.title).slice(0,2000), company:clean(list.company).slice(0,2000), city:clean(list.city).slice(0,2000), hits:[], detail_state:'pending', observed_at:typeof list.observed_at==='string'?list.observed_at:''}
    candidates.push(candidate)
  }
  for (const field of ['title','company','city']) if (!candidate[field]) candidate[field] = clean(list[field]).slice(0,2000)
  for (const field of ['company_headcount','company_sector','company_profile_url','company_headcount_source_url','company_sector_source_url'])
    if(!candidate[field]&&clean(list[field]))candidate[field]=clean(list[field]).slice(0,2000)
  if (hit && !candidate.hits.some(value => value.keyword === hit.keyword && value.city === hit.city && value.page === hit.page)) candidate.hits.push({...hit})
  return candidate
}

export function validatePreferences(prefs, platform, jobURL) {
  if (!prefs || typeof prefs !== 'object') throw new Error('检索条件超出允许范围')
  const mode = prefs.collection_mode || 'collect', keywords = prefs.keywords || [], cities = prefs.cities || []
  const pages = prefs.max_pages?.[platform] ?? 1, maxJobs = prefs.max_jobs ?? 60, maxLists = prefs.max_list_attempts ?? 12
  const text = value => typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= 120
  if (!Number.isInteger(maxLists) || maxLists < (mode==='hydrate'?0:1) || maxLists > 12 || !['collect','discover','hydrate'].includes(mode) || !Number.isInteger(maxJobs) || maxJobs < 1 || maxJobs > 60
      || !Array.isArray(keywords) || !Array.isArray(cities) || [...keywords,...cities].some(value => !text(value))
      || !Number.isInteger(pages) || pages < 1 || pages > 3
      || (mode !== 'hydrate' && (!keywords.length || !cities.length || keywords.length*cities.length > 9 || keywords.length*cities.length*pages > 12))) throw new Error('检索条件超出允许范围')
  for (const name of ['candidates','detail_cache']) {
    const entries = prefs[name] || []
    if (!Array.isArray(entries) || entries.length > 400 || entries.some(value => !value || jobURL(value.url) !== value.url)) throw new Error('岗位或缓存来源无效')
  }
  if (new Set((prefs.candidates || []).map(value=>value.url)).size !== (prefs.candidates || []).length)throw new Error('候选身份重复')
  if ((prefs.candidates || []).some(value => !Array.isArray(value.hits) || value.hits.length > 108
      || value.hits.some(hit => !hit || !text(hit.keyword) || !text(hit.city) || !Number.isInteger(hit.page) || hit.page < 1 || hit.page > 3))) throw new Error('岗位命中记录无效')
  if (prefs.deadline_at !== undefined && (!Number.isFinite(prefs.deadline_at) || prefs.deadline_at <= 0 || prefs.deadline_at > Date.now()/1000+86400)) throw new Error('任务时间预算无效')
  return {mode,keywords,cities,pages,maxJobs,maxLists}
}

export async function runCollection(config, adapter, dependencies = {}) {
  const {platform, validatePreferences, mergeCandidate, cityMatches} = adapter
  if (!/^[a-f0-9]{32}$/.test(config.task_id)) throw new Error('任务标识无效')
  const prefs = config.preferences, {mode,keywords,cities,pages,maxJobs,maxLists} = validatePreferences(prefs)
  const state = {status:'running',phase:'准备检索',seen:0,new:0,filtered:0,closed:0,closed_jobs:[],parse_failed:0,page:0,max_pages:pages,message:'',detail_attempts:0,list_attempts:0,cache_hits:0,
    tabs_opened:0,tabs_closed:0,cleanup_status:'complete',cleanup_message:''}
  if(adapter.browserMode)state.browser_mode=adapter.browserMode(prefs)
  const output = {task_id:config.task_id,run_id:config.task_id,status:'running',total:0,items:[],candidates:[],receipts:[],error:'',finished_at:null,collection_mode:mode,platform_state:{[platform]:state}}
  for (const initial of prefs.candidates || []) {
    const candidate = mergeCandidate(output.candidates,initial)
    for (const hit of initial.hits) mergeCandidate(output.candidates,initial,hit)
    candidate.detail_state = 'pending'
  }
  let writes = Promise.resolve(), writeError, stopping = false, browser, context, taskPage, lastMaintenance
  const now=dependencies.now||Date.now
  const applyBrowserStats=stats=>{
    if(!stats||typeof stats!=='object')return
    for(const name of ['tabs_opened','tabs_closed'])if(Number.isSafeInteger(stats[name])&&stats[name]>=0)
      state[name]=Math.max(state[name],stats[name])
    if(['pending','complete','incomplete'].includes(stats.cleanup_status))state.cleanup_status=stats.cleanup_status
    if(typeof stats.cleanup_message==='string')state.cleanup_message=stats.cleanup_message.slice(0,2000)
  }
  const persist = () => {
    output.total=output.items.length;output.heartbeat=Date.now()/1000;output.updated_at=new Date().toISOString()
    const snapshot=JSON.stringify(output)
    writes=writes.then(async()=>{
      await writeFile(config.outputPath+'.tmp',snapshot,'utf8')
      for(let attempt=0;;attempt++) {
        try {await rename(config.outputPath+'.tmp',config.outputPath);break}
        catch(error){if(attempt>=4||!['EPERM','EACCES','EBUSY'].includes(error.code))throw error;await wait(50)}
      }
    }).catch(error=>{
      writeError ||= Object.assign(new Error('无法保存采集结果，检索已停止；请检查数据目录权限和可用空间'),{cause:error,code:error.code,fatal:true})
      throw writeError
    })
    return writes
  }
  const tick=setInterval(()=>{if(existsSync(config.stopPath))stopping=true;persist().catch(()=>{})},2000)
  const checkStop=()=>{
    if(writeError)throw writeError
    if(stopping||existsSync(config.stopPath))throw new Error('USER_STOPPED')
    if(prefs.deadline_at && Date.now()/1000>=prefs.deadline_at)throw new Error('TIME_BUDGET')
    if(browser?.isConnected?.()===false||taskPage?.isClosed?.())
      throw Object.assign(new Error('采集浏览器已关闭或连接中断；已采集结果保留，请重新检索'),{fatal:true})
  }
  const pause=dependencies.pause || (()=>wait(1600+Math.random()*500))
  const openBrowser=async()=>{
    if(context)return context
    checkStop()
    browser = dependencies.openBrowser?await dependencies.openBrowser():await adapter.openBrowser(config, {checkStop})
    context = browser.contexts()[0]
    state.cleanup_status='pending';lastMaintenance=now()
    return context
  }
  const maintainBetweenVisits=async()=>{
    const humanWaiting=['login_required','verification_required'].includes(state.phase)
    if(!browser?.maintenance||humanWaiting||now()-lastMaintenance<30000)return
    lastMaintenance=now()
    let failure
    try{applyBrowserStats(await browser.maintenance({protectedPages:taskPage?[taskPage]:[],humanWaiting}))}
    catch(error){
      failure=error
      applyBrowserStats(error.cleanupStats)
      state.cleanup_status='incomplete';state.cleanup_message='闲置任务标签未能全部清理：'+String(error.message).slice(0,1800)
    }
    await persist()
    if(failure?.fatal)throw failure
  }
  const startReceipt=async(kind,url,hit={})=>{
    const receipt={id:`${config.task_id}-${output.receipts.length+1}`,kind,status:'started',url,...hit,started_at:new Date().toISOString(),finished_at:null,error:''}
    output.receipts.push(receipt)
    if(kind==='list')state.list_attempts++
    if(kind==='detail')state.detail_attempts++
    if(kind==='cache')state.cache_hits++
    if(kind==='company')state.company_attempts=(state.company_attempts||0)+1
    await persist();return receipt
  }
  const finishReceipt=async(receipt,error)=>{
    Object.assign(receipt,{status:error?'failed':'completed',finished_at:new Date().toISOString(),error:error?String(error.message).slice(0,2000):''})
    await persist()
  }
  const navigate=async(kind,url,hit,read)=>{
    checkStop();await openBrowser()
    // Discovery finishes before hydration, so both stages can use the same
    // task-owned page. Never choose a page from a user's existing context.
    if(!taskPage){taskPage=await browser.takeInitialPage?.()||await context.newPage();state.tabs_opened++}
    const page=taskPage
    await pause();checkStop()
    // This is deliberately not a timer: no reclamation runs during navigation,
    // DOM reading or a login/CAPTCHA wait inside the read callback.
    await maintainBetweenVisits();checkStop()
    // The receipt and reserved cost exist on disk before the navigation is issued.
    const receipt=await startReceipt(kind,kind==='detail'?adapter.jobURL(url):url,hit)
    try {
      const control = {config, state, persist, checkStop, dependencies}
      if (adapter.navigate) await adapter.navigate(page, {kind, url, hit, ...control})
      else {
        await page.goto(url,{waitUntil:'domcontentloaded',timeout:30000})
        await page.waitForTimeout(kind==='list'?2400:1200)
      }
      const value=await read(page);await finishReceipt(receipt);return value
    }catch(error){await finishReceipt(receipt,error);throw error}
  }
  let individualErrors=0,limitReached=false
  const shortages=[]
  try {
    await persist();checkStop()
    if(mode!=='hydrate') {
      outer: for(const keyword of keywords)for(const city of cities){
        // Global identity deduplication must not decide whether this query's
        // pagination advanced: another keyword may already have found the row.
        const querySeen=new Set()
        for(let page=1;page<=pages;page++){
          checkStop();if(state.list_attempts>=maxLists){limitReached=true;state.reason_code='list_limit';break outer};Object.assign(state,{phase:'发现岗位列表',keyword,city,page});await persist()
          const scope={keyword,city,page}
          const found=await navigate('list',adapter.searchURL(keyword,city,page,prefs),scope,
            tab=>adapter.readList(tab,scope,{config,state,persist,checkStop,dependencies}))
          const advanced=found.some(value=>!querySeen.has(value.url))
          for(const list of found){
            querySeen.add(list.url)
            const candidate=mergeCandidate(output.candidates,list,{keyword,city,page})
            if(!candidate){limitReached=true;state.reason_code='candidate_limit';break outer}
            candidate.observed_at=new Date().toISOString()
          }
          state.seen=output.candidates.length;await persist()
          if(!found.length)break
          if(page>1&&!advanced){
            shortages.push(scope);state.shortage_queries=shortages;state.reason_code='search_page_not_advanced'
            state.message='本查询翻页后没有新增岗位，未能确认已加载下一批，保留已发现结果'
            break
          }
        }
      }
    }
    const ensureItemFits=item=>{
      if(Buffer.byteLength(JSON.stringify(output),'utf8')+Buffer.byteLength(JSON.stringify(item),'utf8')>24*1024*1024)throw new Error('OUTPUT_LIMIT')
    }
    const appendItem=item=>{ensureItemFits(item);output.items.push(item)}
    if(mode!=='discover')for(const candidate of output.candidates){
      checkStop();Object.assign(state,{phase:'补全岗位详情',message:`已保存 ${output.items.length} 条，待补全 ${output.candidates.filter(v=>v.detail_state==='pending').length} 条`});await persist()
      // Saved snapshots remain historical records. Even callers with old
      // preferences must visit the source again on every collection run.
      if(state.detail_attempts>=maxJobs){limitReached=true;continue}
      let item
      try{
        const target=adapter.detailURL?adapter.detailURL(candidate):candidate.url
        item=await navigate('detail',target,candidate.hits[0] || {},
          tab=>adapter.readDetail(tab,candidate,{config,state,persist,checkStop,dependencies}))
      }catch(error){
        candidate.detail_state=/USER_STOPPED|TIME_BUDGET/.test(error.message)?'pending':'failed';candidate.error=String(error.message).slice(0,2000);individualErrors++
        if(error.fatal || /人工处理|未授权来源|USER_STOPPED|TIME_BUDGET/.test(error.message))throw error
        state.message=`一条详情未读取完整：${error.message}；保留候选供稍后重试`;await persist();continue
      }
      candidate.last_detail_at=new Date().toISOString()
      if(!item){
        candidate.detail_state='closed';candidate.filter_reason='source_closed'
        state.closed_jobs.push({url:candidate.url,title:candidate.title,company:candidate.company,checked_at:candidate.last_detail_at})
        state.closed=state.closed_jobs.length
        state.message=`来源提示岗位已关闭：${candidate.company} · ${candidate.title}`
        await persist();continue
      }
      if(!item.title||!item.company){candidate.detail_state='incomplete';state.parse_failed++;await persist();continue}
      const filtered=adapter.filterItem?.(item,prefs)
      if(filtered){candidate.detail_state='filtered';candidate.filter_reason=filtered;state.filtered++;await persist();continue}
      if(item.city&&candidate.hits.length&&candidate.hits.every(hit=>!cityMatches(item.city,hit.city))){candidate.detail_state='filtered';candidate.filter_reason='city_mismatch';state.filtered++;await persist();continue}
      item.last_detail_at=candidate.last_detail_at
      item.observed_at=candidate.last_detail_at
      item.reused_snapshot=false
      if(!item.city){item.incomplete_reason='城市未知，请人工核对';item.city_unknown=true}
      if(adapter.enrichCompany&&item.jd_available)item.company_lookup_pending=true
      appendItem(item)
      candidate.detail_state=item.jd_available&&item.city?'complete':'incomplete'
      if(candidate.detail_state==='incomplete')individualErrors++
      state.new=output.items.length;await persist()
      if(adapter.enrichCompany&&item.jd_available) {
        try {
          await adapter.enrichCompany(item,{visit:async(url,company)=>{
            if((state.company_attempts||0)>=8)return {}
            Object.assign(state,{phase:'补充企业人数',message:'已保存岗位，正在查看 '+company+' 的公开人数规模'});await persist()
            return navigate('company',url,{},async tab=>{
              if(adapter.companyURL(tab.url())!==adapter.companyURL(url))return {}
              return adapter.readCompany(tab)
            })
          }})
        }catch(error){
          if(error.fatal||/USER_STOPPED|TIME_BUDGET/.test(error.message))throw error
          state.company_lookup_failed=(state.company_lookup_failed||0)+1
        }finally{delete item.company_lookup_pending;await persist()}
        state.company_with_headcount=output.items.filter(value=>value.company_headcount).length;await persist()
      }
    }
    checkStop();output.status=individualErrors||state.parse_failed?'completed_with_errors':shortages.length?'completed_with_shortage':'completed';state.phase='检索结束'
    const pending=output.candidates.filter(value=>value.detail_state==='pending').length
    if(limitReached&&!['candidate_limit','list_limit'].includes(state.reason_code))state.reason_code='limit_reached'
    const failed=output.candidates.filter(value=>['failed','incomplete'].includes(value.detail_state)).length
    state.message=`发现 ${output.candidates.length} 个岗位身份，本次重新读取并保存 ${output.items.length} 条详情，来源提示已关闭 ${state.closed} 条，读取失败或不完整 ${failed} 条，未读取 ${pending} 条`
    if(mode==='discover')state.message=`发现 ${output.candidates.length} 个列表候选；本次未读取详情，尚未确认岗位是否仍在招聘`
    if(state.company_with_headcount)state.message+='；'+state.company_with_headcount+' 条岗位带有企业人数，额外查看公司页 '+(state.company_attempts||0)+' 次'
    if(shortages.length){
      if(!limitReached)state.reason_code='search_page_not_advanced'
      state.message+='；'+shortages.map(hit=>`${hit.keyword} / ${hit.city} 第 ${hit.page} 页`).join('、')+'未能确认已加载下一批，已保留已发现结果'
    }
  }catch(error){
    applyBrowserStats(error.cleanupStats)
    const stopped=error.message==='USER_STOPPED',timed=error.message==='TIME_BUDGET',oversized=error.message==='OUTPUT_LIMIT'
    output.status=stopped||timed||oversized?'stopped':output.items.length?'completed_with_errors':'failed'
    output.error=stopped||timed||oversized?'':String(error.message).slice(0,2000)
    state.reason_code=stopped?'user_stopped':timed?'time_budget':oversized?'output_limit':'collection_error';state.phase=stopped||timed||oversized?'已停止':'检索中断';state.message=stopped||timed||oversized?'已保留结果与待补全岗位，可继续下一批':output.error
  }finally{
    clearInterval(tick)
    const earlierCleanupFailure=state.cleanup_status==='incomplete'?state.cleanup_message:''
    let pageError='',browserError='',finalStats
    const closingPage=taskPage;taskPage=undefined
    if(closingPage){
      try{
        if(!closingPage.isClosed?.())await closingPage.close()
        // This one local page is a lower bound, not an addition to the
        // transport's cumulative count, which may already include it.
        state.tabs_closed=Math.max(state.tabs_closed,1)
      }catch(error){pageError=String(error.message).slice(0,1800)}
    }
    if(browser){
      try{finalStats=await browser.close();applyBrowserStats(finalStats)}
      catch(error){browserError=String(error.message).slice(0,1800);applyBrowserStats(error.cleanupStats)}
      // A verified session-wide close may recover an earlier failed tab close.
      // A mere disconnect with no such evidence must not hide that failure.
      const verified=finalStats?.cleanup_status==='complete'&&state.tabs_closed>=state.tabs_opened
      if(verified&&!browserError){state.cleanup_status='complete'}
      else if(pageError||browserError||earlierCleanupFailure||(finalStats&&!verified)){
        state.cleanup_status='incomplete'
        state.cleanup_message='任务标签清理未完成：'+(browserError||pageError||earlierCleanupFailure||state.cleanup_message||'未能确认本次标签全部关闭')
      }else{state.cleanup_status='complete';state.cleanup_message='本任务标签已关闭'}
    }
    state.seen=output.candidates.length;output.finished_at=new Date().toISOString();state.status=output.status
    // Report cleanup only after close attempts have actually completed. A disk
    // error still propagates, but it cannot prevent the browser cleanup above.
    await persist();await writes
  }
  return output
}

