/** BOSS's rendered pages, through Chrome Use or an app-owned Chrome/Edge profile.
 * No platform API, browser fingerprint patch, CAPTCHA solver or applicant action.
 * Search parameter/DOM observations: BossHunter collection/platforms/boss.py and
 * browser/runtime/site-patterns/zhipin.com.md, inspected 2026-11-05. The task
 * lifecycle is shared with Radar's existing Shixiseng collector.
 */
import { readFile, unlink, mkdir } from 'node:fs/promises'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import net from 'node:net'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from './shixiseng-node/node_modules/playwright-core/index.mjs'
import { cleanText } from '../vendor/internship-workbench/normalize.mjs'
import { detectStopWall } from '../vendor/internship-workbench/stopRules.mjs'
import { runCollection, mergeCandidate, validatePreferences as validateSharedPreferences } from './collector_runtime.mjs'
import {companyFacts,readCompanyDOM,companyEnricher,companyProfileURL} from './company_profile_dom.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
async function settleWithin(promise,ms) {
  let timer
  try{await Promise.race([promise,new Promise(resolve=>{timer=setTimeout(resolve,ms)})])}
  finally{clearTimeout(timer)}
}
const garbled = /[\uE000-\uF8FF\uFFFD]/u
const clean = value => { const text=cleanText(value); return garbled.test(text)?'':text }
export const BOSS_CITIES = Object.freeze(JSON.parse(readFileSync(path.join(HERE,'boss-cities.json'),'utf8')).cities)

export function cityName(value) {
  const name=String(value || '').trim()
  if(Object.hasOwn(BOSS_CITIES,name))return name
  if(name.endsWith('市')&&Object.hasOwn(BOSS_CITIES,name.slice(0,-1)))return name.slice(0,-1)
  throw new Error('BOSS 暂不支持城市“'+name+'”，请从城市列表选择；不会改为全国检索')
}

export function jobURL(value) {
  try {
    const url=new URL(value,'https://www.zhipin.com')
    if(url.protocol!=='https:' || !['www.zhipin.com','zhipin.com'].includes(url.hostname)
      || url.username || url.password || (url.port&&url.port!=='443')
      || !/^\/job_detail\/[A-Za-z0-9_~-]{1,128}\.html$/.test(url.pathname))return ''
    return 'https://www.zhipin.com'+url.pathname
  }catch{return ''}
}

function navigationURL(value) {
  if(!jobURL(value))return ''
  const url=new URL(value,'https://www.zhipin.com');url.hash=''
  return url.href
}

export function searchURL(keyword,city,page=1,prefs={}) {
  const query=new URLSearchParams({query:keyword,city:BOSS_CITIES[cityName(city)],page:String(page)})
  if(prefs.internship_only!==false)query.set('jobType','1902')
  return 'https://www.zhipin.com/web/geek/jobs?'+query
}

export const cityMatches = (actual,requested) => cityName(requested)==='全国'||Boolean(actual&&actual.includes(cityName(requested)))

function browserMode(prefs={}) {
  const mode=prefs.browser_mode===undefined?'managed':prefs.browser_mode
  if(!['managed','chrome_use'].includes(mode))throw new Error('浏览器模式无效，请选择 Chrome Use 或专用采集浏览器')
  return mode
}

export function validatePreferences(prefs) {
  const validated=validateSharedPreferences(prefs,'boss',jobURL)
  if(!['collect','discover'].includes(validated.mode))throw new Error('BOSS 请重新检索以取得当前岗位链接，不支持用过期链接补全')
  if(prefs.internship_only!==undefined&&typeof prefs.internship_only!=='boolean')throw new Error('仅实习选项必须为布尔值')
  browserMode(prefs)
  if(validated.keywords.length>9)throw new Error('每次最多 9 个关键词')
  for(const city of validated.cities)cityName(city)
  return validated
}

export function normalizeDetail(list,fields={},listedAsInternship=false) {
  // Only the actual description container is a JD. A list snippet or page body
  // containing recommendations must never become a full job description.
  const jd=cleanText(fields.jd)
  if(jd.length>100000)throw new Error('岗位 JD 超过 100000 字，不截断原文；请人工核对')
  const kind=clean(fields.employment_type),title=clean(fields.title)||clean(list.title)
  const internship=/实习|\bintern(?:ship)?\b/i.test(kind+' '+title)
  const fullTime=/全职|兼职|full[ -]?time|part[ -]?time/i.test(kind)
  const salary=clean(fields.salary),salaryUnreadable=Boolean(fields.salary&&garbled.test(fields.salary))
  const item={url:jobURL(list.url),title,company:clean(fields.company)||clean(list.company),
    city:clean(fields.city)||clean(list.city),salary,
    duration:clean(fields.duration),onsite_days:clean(fields.onsite_days),eligibility:clean(fields.eligibility),
    employment_type:fullTime?'other':internship||listedAsInternship?'internship':'unknown',
    jd,jd_available:Boolean(jd&&!garbled.test(jd)),observed_at:new Date().toISOString(),reused_snapshot:false}
  Object.assign(item,companyFacts({...list,profile_company:list.company},item.company,list.url,'boss'),
    companyFacts(fields,item.company,item.url,'boss'))
  if(salaryUnreadable){item.salary_unreadable=true;item.incomplete_reason='薪资使用特殊字体，未猜测数值，请在原始页面核对'}
  return item
}

export function doctor() {
  const roots=[process.env.ProgramFiles,process.env['ProgramFiles(x86)'],process.env.LOCALAPPDATA].filter(Boolean)
  const browser=roots.flatMap(root=>['Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe'].map(tail=>path.join(root,tail))).find(existsSync)
  const ready=process.platform==='win32'&&Boolean(browser)
  return {ready,browser:browser||'',cities:Object.keys(BOSS_CITIES),
    message:process.platform!=='win32'?'BOSS 桌面采集需在 Windows 中运行完整应用':browser?'BOSS 浏览器已准备好；首次检索时按提示完成登录':'请安装 Google Chrome 或 Microsoft Edge 后重新检索'}
}

const fatal = message => Object.assign(new Error(message),{fatal:true})

async function availablePort() {
  const server=net.createServer()
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)})
  const port=server.address().port
  await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()))
  return port
}

async function openManagedBrowser(config,{checkStop}) {
  const info=doctor()
  if(!info.ready)throw fatal(info.message)
  // This profile belongs only to Radar. Never read or modify a personal Chrome
  // profile, and never add automation-concealment flags or initialization code.
  const profile=config.browserProfilePath||path.join(process.env.LOCALAPPDATA||os.homedir(),'InternshipRadarPrivateRC','BossRadarChrome')
  await mkdir(profile,{recursive:true})
  const port=await availablePort(),origin='http://127.0.0.1:'+port
  const child=spawn(info.browser,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+port,
    '--user-data-dir='+profile,'--no-first-run','--no-default-browser-check','--disable-background-mode','about:blank'],{stdio:'ignore',windowsHide:false})
  let launchError,exited=false
  const didExit=new Promise(resolve=>{
    child.once('exit',()=>{exited=true;resolve()})
    child.once('error',error=>{launchError=error;resolve()})
  })
  try {
    for(let step=0;step<40;step++) {
      checkStop()
      if(launchError)throw fatal('无法打开采集浏览器，请检查 Chrome 或 Edge 是否能正常启动')
      if(exited)throw fatal('采集浏览器已退出；请关闭上一次的 BOSS 采集窗口后重试')
      try {
        const response=await fetch(origin+'/json/version',{signal:AbortSignal.timeout(800)})
        if(response.ok) {
          const browser=await chromium.connectOverCDP(origin,{timeout:10000})
          const context=browser.contexts()[0],ownedPages=new Set(context.pages())
          context.on('page',page=>ownedPages.add(page))
          let initialPage=[...ownedPages].find(page=>page.url()==='about:blank'&&!page.isClosed())
          const cleanupStats=complete=>({tabs_opened:ownedPages.size,
            tabs_closed:complete?ownedPages.size:[...ownedPages].filter(page=>page.isClosed()).length,
            cleanup_status:complete?'complete':'incomplete',
            cleanup_message:complete?'本次专用采集窗口已关闭':'未能确认专用采集窗口已关闭，请检查该窗口'})
          return {contexts:()=>browser.contexts(),isConnected:()=>browser.isConnected(),
            takeInitialPage:()=>{const page=initialPage;initialPage=undefined;return page&&!page.isClosed()?page:undefined},
            close:async()=>{
            try{
              await settleWithin((async()=>{
                  try{const session=await browser.newBrowserCDPSession();await session.send('Browser.close')}catch{}
                  await browser.close().catch(()=>{})
                  await didExit
                })(),5000)
              if(!exited){child.kill();await settleWithin(didExit,2000)}
              if(!exited)throw Object.assign(new Error('专用采集浏览器未能退出'),{cleanupStats:cleanupStats(false)})
              return cleanupStats(true)
            }finally{
              child.unref()
            }
          }}
        }
      }catch{}
      await wait(300)
    }
    throw fatal('采集浏览器未能就绪；请关闭上一次的 BOSS 采集窗口后重试')
  }catch(error){
    if(!exited&&!launchError){child.kill();await settleWithin(didExit,1000)}
    child.unref()
    if(!exited&&!launchError)error.cleanupStats={tabs_opened:0,tabs_closed:0,cleanup_status:'incomplete',cleanup_message:'无法确认本次启动的采集窗口已退出，请检查该窗口'}
    throw error
  }
}

export async function openBrowser(config,control,transports={}) {
  if(browserMode(config.preferences)==='chrome_use'){
    const open=transports.chrome_use||(await import('./chrome_use_browser.mjs')).openChromeUse
    return open(config,control)
  }
  return (transports.managed||openManagedBrowser)(config,control)
}

export async function browserDoctor(mode='managed') {
  browserMode({browser_mode:mode})
  const info=mode==='chrome_use'?await (await import('./chrome_use_browser.mjs')).chromeUseDoctor():doctor()
  return {...info,browser_mode:mode,cities:Object.keys(BOSS_CITIES)}
}

// Runs inside the page: inspect only rendered content, never network response
// internals, cookies, browser storage, contact controls or recruiter profiles.
function inspectPageDOM() {
  const visible=element=>Boolean(element&&element.getClientRects().length&&getComputedStyle(element).visibility!=='hidden')
  const any=selector=>Array.from(document.querySelectorAll(selector)).some(visible)
  const dialogText=Array.from(document.querySelectorAll('[role="dialog"],.dialog-wrap,.dialog-container,.ui-dialog,.modal'))
    .filter(visible).map(el=>el.innerText||'').join('\n').slice(0,10000)
  return {url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,20000),
    dialogText,
    hasJobs:any('.job-card-wrap,.job-card-wrapper,.job-sec-text'),
    login:any('.sign-form,.sign-wrap,[role="dialog"] .login-form,.login-dialog'),
    verification:any('.geetest_panel,.captcha,[id*="captcha"],iframe[src*="captcha"],iframe[src*="verify"]')}
}

export function accessState(info) {
  let url
  try{url=new URL(info.url)}catch{throw fatal('页面来源无效，已停止检索')}
  if(url.protocol!=='https:'||!(url.hostname==='zhipin.com'||url.hostname.endsWith('.zhipin.com'))||url.username||url.password)
    throw fatal('页面跳转到未授权来源，已停止检索')
  const text=String(info.text||''),title=String(info.title||'').trim(),dialog=String(info.dialogText||'')
  const blockedPath=/(?:^|\/)(?:403|forbidden|access-denied)(?:\/|$)/i.test(url.pathname)
  if(blockedPath||/^(?:403|forbidden|access denied|访问被拒绝|账号异常|账号受限)/i.test(title)
    ||/操作(?:过于)?频繁|访问(?:过于)?频繁|请求(?:过于)?频繁|频率限制|账号异常|账号受限|访问被拒绝/.test(dialog))return 'blocked'
  if(info.verification||/captcha|security-check|\/verify/i.test(url.pathname))return 'verification_required'
  if(info.login||/^\/web\/user(?:\/|$)/.test(url.pathname))return 'login_required'
  if(!info.hasJobs) {
    const wall=detectStopWall(text)
    if(wall.id==='risk_control'||/操作频繁|访问频繁|请求频繁|频率限制|账号异常|账号受限|访问被拒绝/.test(text))return 'blocked'
    if(wall.id==='captcha')return 'verification_required'
    if(wall.id==='login_wall'||/尝试登录查看全部职位/.test(text))return 'login_required'
  }
  return 'readable'
}

export async function ensureReadable(page,target,control) {
  const {config,state,persist,checkStop,dependencies}=control
  const now=dependencies.now||Date.now
  let resumed=false,lastLiveness
  while(true) {
    checkStop()
    const info=await page.evaluate(inspectPageDOM)
    const access=accessState(info)
    if(access==='blocked')throw fatal('BOSS 提示访问受限或操作频繁，本次已停止；已采集结果保留，请稍后再试')
    if(access==='readable') {
      const current=new URL(info.url),expected=new URL(target)
      // BOSS redirects the legacy singular list path to the plural route.
      // Both routes still have to prove the same keyword, city and job type.
      const matches=jobURL(target) ? jobURL(info.url)===jobURL(target)
        : ['www.zhipin.com','zhipin.com'].includes(current.hostname)
          && (!current.port||current.port==='443')
          && /^\/web\/geek\/jobs?$/.test(current.pathname)
          && ['query','city','jobType'].every(key=>
            current.searchParams.getAll(key).length===expected.searchParams.getAll(key).length
            && current.searchParams.get(key)===expected.searchParams.get(key))
      if(!matches) {
        if(!resumed)throw fatal('页面没有显示本次检索目标，已停止读取以免混入其他岗位')
        resumed=false
        await goto(page,target)
        continue
      }
      if(resumed||['login_required','verification_required'].includes(state.phase)){
        state.phase=jobURL(target)?'读取岗位详情':'读取岗位列表';state.message='已完成页面验证，继续本次检索';await persist()
      }
      return info
    }
    if(!config.resumePath)throw fatal('请在采集窗口完成人工处理后重新检索')
    state.phase=access
    state.message=access==='login_required'?'请在已打开的 BOSS 窗口登录，完成后点击“继续检索”':'请在 BOSS 窗口完成人工验证，完成后点击“继续检索”'
    await persist()
    while(!existsSync(config.resumePath)) {
      checkStop()
      // CLI-backed pages have no persistent CDP close event. This optional
      // metadata-only probe detects a closed tab/connection without reclaiming,
      // navigating, opening or activating anything during the human handoff.
      if(page.checkLiveness&&(lastLiveness===undefined||now()-lastLiveness>=10000)){
        await page.checkLiveness();lastLiveness=now();checkStop()
      }
      await (dependencies.humanWait||wait)(400)
    }
    await unlink(config.resumePath).catch(error=>{if(error.code!=='ENOENT')throw error})
    checkStop();resumed=true
  }
}

async function goto(page,url) {
  try {await page.goto(url,{waitUntil:'domcontentloaded',timeout:30000})}
  catch(error){if(error.fatal)throw error;throw new Error('BOSS 页面未能加载，请检查网络或采集窗口；已有结果保留')}
  await page.waitForTimeout(2400)
}

function listDOM() {
  const text=element=>element?.innerText?.trim()||''
  const entries=[]
  for(const anchor of document.querySelectorAll('a[href*="/job_detail/"]')) {
    const card=anchor.closest('.job-card-wrap,.job-card-wrapper,.job-card-body,li')
    if(!card)continue
    entries.push({url:anchor.href,title:text(card.querySelector('.job-name'))||text(anchor),
      company:text(card.querySelector('.company-name,.company-info .name,.boss-name')),
      company_headcount:Array.from(card.querySelectorAll('.company-tag-list li,.company-info .company-text,.company-tag-list span')).map(text).find(value=>/^\d[\d,，]*\s*(?:[-–~]\s*\d[\d,，]*\s*人|人(?:以上|以下)?)$/.test(value))||'',
      company_headcount_source_url:location.origin+location.pathname+'?'+new URLSearchParams([...new URL(location.href).searchParams].filter(([key])=>['query','city','page','jobType'].includes(key))),
      company_profile_url:card.querySelector('a[href*="/gongsi/"]')?.href||'',
      city:text(card.querySelector('.job-area,.company-location')),
      salary:text(card.querySelector('.job-salary,.salary'))})
  }
  const selected=Array.from(document.querySelectorAll('.condition-filter-select [ka*="jobType"].active'))
  const internshipFilter=new URL(location.href).searchParams.get('jobType')==='1902'&&selected.some(el=>text(el)==='实习'&&el.getAttribute('ka')?.endsWith('-1902'))
  return {entries,internshipFilter}
}

function detailDOM() {
  const text=selector=>document.querySelector(selector)?.innerText?.trim()||''
  const company=Array.from(document.querySelectorAll('.sider-company .company-info a')).map(el=>el.innerText.trim()).find(value=>value&&!/^https?:/.test(value))||''
  return {title:text('.info-primary h1')||text('.job-primary h1')||text('h1'),
    company,
    company_sector:document.querySelector('.sider-company .icon-industry')?.parentElement.innerText.trim()||'',
    company_headcount:document.querySelector('.sider-company .icon-scale')?.parentElement.innerText.trim()||'',
    city:text('.text-city')||text('.job-location'),salary:text('.info-primary .salary')||text('.salary'),
    jd:text('.job-sec-text'),eligibility:text('.text-degree'),employment_type:text('.text-experiece')||text('.text-experience'),
    onsite_days:text('.text-week'),duration:text('.text-month')}
}

export function createAdapter() {
  // Link parameters belong only to this browser run. Persist identities without
  // transient securityId values; do not export them in snapshots or diagnostics.
  const links=new Map(),internships=new Set(),enrichCompany=companyEnricher('boss')
  return {
    platform:'boss',jobURL,validatePreferences,cityMatches,openBrowser,searchURL,browserMode,
    mergeCandidate:(candidates,list,hit)=>mergeCandidate(candidates,list,hit,jobURL),
    detailURL:candidate=>links.get(candidate.url)||candidate.url,
    filterItem:(item,prefs)=>prefs.internship_only!==false&&item.employment_type!=='internship'?'internship_unconfirmed':'',
    enrichCompany,companyURL:url=>companyProfileURL(url,'boss'),
    async readCompany(page) {
      const info=await page.evaluate(inspectPageDOM),access=accessState(info)
      if(access==='blocked')throw fatal('BOSS 提示访问受限，已保留岗位与已读取的企业资料')
      if(access==='verification_required')throw fatal('BOSS 公司页要求验证，已停止并保留岗位与已读取的企业资料')
      if(access!=='readable')return {company_lookup_unavailable:true}
      return page.evaluate(readCompanyDOM,{platform:'boss',mode:'company'})
    },
    async navigate(page,control) {
      if(control.kind==='list'&&control.hit.page>1) {
        const scrolling=await page.evaluate(()=>Boolean(document.querySelector('.page-jobs .job-list-container .rec-job-list')))
        if(scrolling) {
          await page.evaluate(()=>{
            const last=document.querySelector('.rec-job-list')?.lastElementChild
            last?.scrollIntoView({block:'end'})
          })
          await page.mouse.wheel(0,1000)
          await page.waitForTimeout(2400)
          return
        }
      }
      await goto(page,control.url)
    },
    async readList(page,scope,control) {
      // Infinite-scroll pages retain page=1 in the address bar.
      const expected=searchURL(scope.keyword,scope.city,1,control.config.preferences)
      const info=await ensureReadable(page,expected,control)
      const {entries,internshipFilter}=await page.evaluate(listDOM)
      if(control.config.preferences.internship_only!==false&&!internshipFilter)
        throw fatal('未能确认 BOSS 的“实习”筛选已生效，请在采集窗口核对筛选条件后重新检索')
      const found=new Map()
      for(const row of entries) {
        const url=jobURL(row.url),target=navigationURL(row.url)
        if(!url||!target)continue
        links.set(url,target)
        if(internshipFilter)internships.add(url)
        if(!found.has(url))found.set(url,{...row,url})
      }
      if(!found.size&&!/暂无相关职位|暂无职位|没有找到.*职位|没有.*相关.*职位|未找到.*职位|没有更多职位/.test(info.text))
        throw fatal('BOSS 页面未显示可读取的岗位，可能未加载完成或页面结构已变化；没有按零结果处理')
      return [...found.values()]
    },
    async readDetail(page,candidate,control) {
      const info=await ensureReadable(page,links.get(candidate.url)||candidate.url,control)
      if(/当前职位已下线|该职位已下线|职位已关闭|该职位已停止招聘/.test(info.text))return null
      const fields=await page.evaluate(detailDOM),profile=await page.evaluate(readCompanyDOM,{platform:'boss'})||{}
      return normalizeDetail(candidate,{...fields,...profile,company:fields.company||profile.profile_company},internships.has(candidate.url))
    },
  }
}

export const run = (config,dependencies={}) => runCollection(config,createAdapter(),dependencies)

async function main() {
  if(process.argv.includes('--doctor')){
    const modeIndex=process.argv.indexOf('--browser-mode')
    if(modeIndex>=0&&!process.argv[modeIndex+1])throw new Error('缺少浏览器模式')
    const info=await browserDoctor(modeIndex<0?'managed':process.argv[modeIndex+1]);console.log(JSON.stringify(info));return info.ready?0:1
  }
  if(process.argv.includes('--self-test')){
    const chromeUse=await import('./chrome_use_browser.mjs')
    if(typeof chromeUse.openChromeUse!=='function'||typeof chromeUse.chromeUseDoctor!=='function'
      ||typeof chromeUse.chromeUseEnvironment!=='function'
      ||chromeUse.chromeUseEnvironment({}).AGENT_BROWSER_NO_AUTO_RECONNECT!=='1')throw new Error('Chrome Use adapter module check failed')
    if(!jobURL('https://www.zhipin.com/job_detail/fixture.html?securityId=ephemeral')||jobURL('https://evil.test/job_detail/fixture.html'))throw new Error('BOSS URL check failed')
    if(new URL(searchURL('AI 产品','北京')).searchParams.get('city')!=='101010100')throw new Error('BOSS city check failed')
    const item=normalizeDetail({url:'/job_detail/fixture.html',title:'AI 产品实习',company:'样例企业'}, {city:'北京',jd:'完整岗位职责',salary:'\ue123'},true)
    if(!item.jd_available||item.salary!==''||item.employment_type!=='internship')throw new Error('BOSS detail check failed')
    console.log(JSON.stringify({result:'passed',platform:'boss',runtime:'native_windows_dom'}));return 0
  }
  const index=process.argv.indexOf('--config')
  if(index<0||!process.argv[index+1])throw new Error('Missing task config')
  await run(JSON.parse(await readFile(process.argv[index+1],'utf8')));return 0
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  main().then(code=>process.exit(code)).catch(error=>{console.error(error.message);process.exit(1)})
}
