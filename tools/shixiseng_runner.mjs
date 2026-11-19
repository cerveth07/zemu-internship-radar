import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { chromium } from './shixiseng-node/node_modules/playwright-core/index.mjs'
import { cleanText, mergeDetail } from '../vendor/internship-workbench/normalize.mjs'
import { detectStopWall } from '../vendor/internship-workbench/stopRules.mjs'
import { cleanHtml, normalizeDate } from '../vendor/offer-harvester/text.mjs'
import { runCollection, mergeCandidate as mergeSharedCandidate, validatePreferences as validateSharedPreferences } from './collector_runtime.mjs'
import {companyFacts,readCompanyDOM,companyEnricher,companyProfileURL} from './company_profile_dom.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const exec = promisify(execFile)
const garbled = /[\uE000-\uF8FF\uFFFD]/u
const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms))
const clean = value => { const text = cleanText(value); return garbled.test(text) ? '' : text }

export function jobURL(input) {
  try {
    const url = new URL(input)
    if (url.protocol !== 'https:' || url.hostname !== 'www.shixiseng.com' || url.username || url.password
        || (url.port && url.port !== '443') || !/^\/intern\/inn_[A-Za-z0-9_-]+$/.test(url.pathname)) return ''
    return `https://www.shixiseng.com${url.pathname}`
  } catch { return '' }
}

export function searchURL(keyword, city, page) {
  return `https://www.shixiseng.com/interns?${new URLSearchParams({keyword, city, page: String(page)})}`
}

export function cityMatches(city, requested) {
  return requested === '全国' || Boolean(city && city.includes(requested))
}

export function normalizeDetail(list, detail, fields = {}) {
  const merged = mergeDetail(list, detail)
  const title = clean(fields.iname || fields.name) || clean(detail?.title) || clean(merged.title)
  const company = clean(fields.cname) || clean(detail?.company) || clean(merged.company)
  const info = cleanHtml([fields.info, ...(fields.hope_you_v2 || [])].filter(v => typeof v === 'string').join('\n\n'))
  const jd = info || cleanText(detail?.raw)
  if (jd.length > 100000) throw new Error('岗位 JD 超过 100000 字，已保留来源待人工处理，不截断原文')
  const num = value => value !== '' && value != null && Number.isFinite(Number(value)) ? Number(value) : null
  const min = num(fields.minsal ?? fields.minsalary), max = num(fields.maxsal ?? fields.maxsalary)
  const salary = clean(fields.salary_desc) || (min != null && max != null ? `${min}-${max}/天` : '') || clean(detail?.salary) || clean(merged.salary)
  const days = num(fields.day ?? fields.dayperweek), months = num(fields.month_num ?? fields.month)
  const companyEvidence = companyFacts(fields,company,jobURL(list.url),'shixiseng')
  return {title, company, ...companyEvidence, city: clean(fields.city) || clean(detail?.city) || clean(merged.city), salary,
    duration: months != null && months > 0 ? `实习${months}个月` : clean(fields.duration),
    onsite_days: days != null && days > 0 && days <= 7 ? `${days}天/周` : clean(fields.onsite_days),
    eligibility: clean(fields.degree), posted_at: normalizeDate(fields.refresh), deadline: normalizeDate(fields.endtime),
    employment_type: 'internship', jd, jd_available: Boolean(jd && !garbled.test(jd)),
    url: jobURL(list.url), observed_at: new Date().toISOString(), reused_snapshot: false}
}

function doctor() {
  const dirs = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA].filter(Boolean)
  const browser = dirs.flatMap(dir => [path.join(dir,'Google/Chrome/Application/chrome.exe'), path.join(dir,'Microsoft/Edge/Application/msedge.exe')]).find(existsSync)
  return {ready: process.platform === 'win32' && Boolean(browser), browser: browser || '',
    message: process.platform !== 'win32' ? '源码运行需要 Windows Node.js；桌面 EXE 已内置连接器运行时' : browser ? '实习僧连接器与浏览器已准备好；登录或验证请在专用窗口处理' : '请安装 Google Chrome 或 Microsoft Edge 后重试'}
}

async function collect(page, mode) {
  const source = await readFile(path.join(HERE,'../vendor/internship-workbench/collector.js'),'utf8')
  await page.addScriptTag({content: source})
  return page.evaluate(mode => window.__iwbCollectJobs({mode, maxJd: 100001}), mode)
}

async function visibleWall(page) {
  const text = await page.locator('body').innerText({timeout: 5000})
  const wall = detectStopWall(text)
  if (wall.hit) throw new Error(`页面要求${wall.label}；请在专用浏览器中人工处理，再重新检索`)
  const host = new URL(page.url()).hostname
  if (!['www.shixiseng.com','shixiseng.com'].includes(host)) throw new Error('页面跳转到未授权来源，已停止检索')
  return text
}

async function detailFields(page, expectedId) {
  // Read already-rendered browser state; never evaluate remote JavaScript in Node.
  return page.evaluate(expectedId => {
    const profile = {
      profile_company: document.querySelector('.job-about .com-name')?.innerText || '',
      company_sector: document.querySelector('.job-about .com-detail .iconhangyelingyu')?.parentElement.innerText || '',
      company_headcount: document.querySelector('.job-about .com-detail .iconqiyeguimo')?.parentElement.innerText || '',
    }
    const seen = new Set(), stack = [window.__NUXT__]; let count = 0
    while (stack.length && count++ < 20000) {
      const value = stack.pop()
      if (!value || typeof value !== 'object' || seen.has(value)) continue
      seen.add(value)
      if (typeof value.cname === 'string' && (typeof value.iname === 'string' || typeof value.info === 'string')
          && (!value.uuid || value.uuid === expectedId)) {
        const keys = ['iname','name','cname','city','info','degree','refresh','endtime','salary_desc','minsal','maxsal','minsalary','maxsalary','day','dayperweek','month_num','month']
        const result = Object.fromEntries(keys.filter(k => ['string','number'].includes(typeof value[k])).map(k => [k,value[k]]))
        if (Array.isArray(value.hope_you_v2)) result.hope_you_v2 = value.hope_you_v2.filter(v => typeof v === 'string')
        return {...result,...profile}
      }
      for (const next of Object.values(value)) if (next && typeof next === 'object') stack.push(next)
    }
    return {
      ...profile,
      salary_desc: document.querySelector('.job_money')?.innerText || '',
      city: document.querySelector('.job_position')?.innerText || '',
      degree: document.querySelector('.job_academic')?.innerText || '',
      onsite_days: document.querySelector('.job_week')?.innerText || '',
      duration: document.querySelector('.job_time')?.innerText || '',
      cname: document.querySelector('.com-name')?.innerText || '',
    }
  }, expectedId)
}

export const mergeCandidate = (candidates, list, hit) => mergeSharedCandidate(candidates, list, hit, jobURL)
export const validatePreferences = prefs => validateSharedPreferences(prefs, 'shixiseng', jobURL)

async function openBrowser(_config, {checkStop}) {
  if(!doctor().ready)throw new Error(doctor().message)
  await exec('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(HERE,'start-collection-browser.ps1'),'-Platform','shixiseng'],{windowsHide:true,timeout:20000,maxBuffer:64000})
  let connected=false
  for(let i=0;i<30;i++){
    checkStop()
    try{const response=await fetch('http://127.0.0.1:9334/json/version',{signal:AbortSignal.timeout(1000)});if(response.ok){connected=true;break}}catch{}
    await wait(500)
  }
  if(!connected)throw new Error('专用实习僧浏览器没有就绪，请检查 9334 端口及启动日志')
  return chromium.connectOverCDP('http://127.0.0.1:9334',{timeout:15000})
}

async function readList(tab) {
  const body=await visibleWall(tab),extracted=await collect(tab,'list')
  const anchors=await tab.locator('a[href*="/intern/inn_"]').evaluateAll(nodes=>nodes.map(n=>({url:n.href,title:n.innerText,company:'',city:''})))
  const byURL=new Map()
  for(const list of [...(extracted?.jobs||[]),...anchors]){const url=jobURL(list.url);if(url&&!byURL.has(url))byURL.set(url,{...list,url})}
  if(!byURL.size&&!/暂无|没有.*岗位|无相关|未找到|没有找到|没有.*职位/.test(body))throw new Error('页面没有可读取的岗位链接，可能尚未加载或页面结构已变化；未当成零结果')
  return [...byURL.values()]
}

async function readDetail(tab,candidate) {
  const text=await visibleWall(tab)
  if(/当前职位已下线|该职位已下线|职位已关闭/.test(text))return null
  const extracted=await collect(tab,'detail'),fields=await detailFields(tab,new URL(candidate.url).pathname.split('/').pop())
  const profile=await tab.evaluate(readCompanyDOM,{platform:'shixiseng',jobId:new URL(candidate.url).pathname.split('/').pop()})
  return normalizeDetail(candidate,extracted?.jobs?.[0],{...fields,...profile})
}

async function readCompany(tab) {
  const body=await tab.locator('body').innerText({timeout:5000})
  const wall=detectStopWall(body)
  if(wall.id==='risk_control'||wall.id==='captcha')throw Object.assign(new Error('实习僧公司页要求'+wall.label+'，已停止并保留岗位与已读取的企业资料'),{fatal:true})
  if(wall.hit)return {company_lookup_unavailable:true}
  return tab.evaluate(readCompanyDOM,{platform:'shixiseng',mode:'company'})
}

export const run = (config, dependencies={}) => runCollection(config, {
  platform:'shixiseng', jobURL, validatePreferences, mergeCandidate,
  cityMatches, openBrowser, searchURL, readList, readDetail,readCompany,enrichCompany:companyEnricher('shixiseng'),
  companyURL:url=>companyProfileURL(url,'shixiseng'),
}, dependencies)

async function main() {
  if(process.argv.includes('--doctor')){const info=doctor();console.log(JSON.stringify(info));return info.ready?0:1}
  if(process.argv.includes('--self-test')){
    if(!jobURL('https://www.shixiseng.com/intern/inn_fixture')||jobURL('https://evil.test/intern/inn_fixture'))throw new Error('URL validation failed')
    const item=normalizeDetail({url:'https://www.shixiseng.com/intern/inn_fixture',title:'\ue012',company:'\ue013'},null,{iname:'AI 产品实习',cname:'样例企业',city:'北京',info:'<p>岗位职责</p><p>参与产品研究</p>',minsal:100,maxsal:200,day:4,month:3})
    if(item.title!=='AI 产品实习'||item.jd!=='岗位职责\n参与产品研究'||item.salary!=='100-200/天')throw new Error('Detail normalization failed')
    console.log(JSON.stringify({result:'passed',extractor:'internship-workbench',detail_helpers:'offer-harvester'}));return 0
  }
  const index=process.argv.indexOf('--config')
  if(index<0||!process.argv[index+1])throw new Error('Missing task config')
  await run(JSON.parse(await readFile(process.argv[index+1],'utf8')));return 0
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  main().then(code=>process.exit(code)).catch(error=>{console.error(error.message);process.exit(1)})
}
