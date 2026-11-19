/** Small adapter for the public chrome-use CLI, tested against v1.5.281.
 * Uses the extension's existing Chrome profile and task-owned tabs only.
 * Protocol: https://github.com/leeguooooo/chrome-use/tree/v1.5.281
 * No shell, remote-debugging port, browser launch, adoption or force-close.
 */
import {execFile} from 'node:child_process'
import {statSync} from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const MIN_CLI_VERSION = '1.5.281'
export const MIN_EXTENSION_VERSION = '0.5.105'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const fatal = (message, code = 'chrome_use_error') => Object.assign(new Error(message), {fatal:true, code})
const record = value => Boolean(value && typeof value === 'object' && !Array.isArray(value))
const protocolError = () => fatal('Chrome Use 返回的格式不兼容，请更新组件后重试', 'chrome_use_protocol')

function supportedVersion(value, minimum) {
  if (typeof value !== 'string' || !/^\d+\.\d+\.\d+$/.test(value)) return false
  const parts=value.split('.').map(Number), floor=minimum.split('.').map(Number)
  return parts[0]===floor[0] && parts[1]===floor[1] && parts[2]>=floor[2]
}

export function chromeUseEnvironment(source=process.env) {
  // These are connection/lifecycle options, not site approval policies. Keep
  // the user's existing action-policy and permission settings in force.
  const env={...source, CHROME_USE_AUTO_UPGRADE:'0', AGENT_BROWSER_NO_AUTO_RECONNECT:'1',
    AGENT_BROWSER_DEDICATED_WINDOW:'0', AGENT_BROWSER_HUMANIZE:'off'}
  // Upstream interprets CI + a missing relay as permission to launch Chrome.
  delete env.CI
  // Some upstream paths check presence rather than truthiness: '0' is unsafe.
  delete env.AGENT_BROWSER_FORCE_LAUNCH
  delete env.AGENT_BROWSER_NO_AUTO_CONNECT
  return env
}

export function findChromeUse({env=process.env, platform=process.platform, home=os.homedir(), isFile}={}) {
  isFile ||= filename => {try{return statSync(filename).isFile()}catch{return false}}
  const paths=platform==='win32'?path.win32:path.posix
  if (env.RADAR_CHROME_USE_PATH) {
    const value=env.RADAR_CHROME_USE_PATH
    return paths.isAbsolute(value) && isFile(value) ? value : ''
  }
  const executable=platform==='win32'?'chrome-use.exe':'chrome-use'
  const candidates=[]
  if (platform==='win32' && env.LOCALAPPDATA) candidates.push(paths.join(env.LOCALAPPDATA,'Programs','chrome-use',executable))
  candidates.push(paths.join(home,'.local','bin',executable))
  for (const entry of (env.PATH || env.Path || '').split(platform==='win32'?';':':')) {
    const folder=entry.replace(/^"|"$/g,'')
    if (paths.isAbsolute(folder)) candidates.push(paths.join(folder,executable))
  }
  return candidates.find(isFile) || ''
}

export function executeCLI(executable,args,{input='',timeout=35000,env=chromeUseEnvironment(),cwd}={}) {
  return new Promise(resolve=>{
    const child=execFile(executable,args,{encoding:'utf8',windowsHide:true,shell:false,
      timeout,maxBuffer:2*1024*1024,env,cwd},(error,stdout)=>{
      resolve({exitCode:error ? (typeof error.code==='number'?error.code:-1) : 0,
        stdout:stdout || '',timedOut:Boolean(error?.killed),spawnCode:typeof error?.code==='string'?error.code:''})
    })
    child.stdin?.on('error',()=>{})
    child.stdin?.end(input,'utf8')
  })
}

async function invoke(executable,args,options,execute=executeCLI,{plain=false}={}) {
  const response=await execute(executable,args,options)
  if (response.timedOut) throw fatal('Chrome Use 未及时响应，检索已停止；请检查 Chrome 的检索标签，已有结果保留','chrome_use_timeout')
  let body
  try {body=JSON.parse(response.stdout)} catch {throw protocolError()}
  if (!record(body)) throw protocolError()
  if (response.exitCode===3 || body.data?.confirmation_required===true || body.confirmation_required===true)
    throw fatal('Chrome Use 正在等待站点操作授权，请在 Chrome Use 中处理后重新检索','chrome_use_approval_required')
  if (response.exitCode!==0 || (plain?body.ok!==true:body.success!==true)) {
    // CLI errors can contain complete URLs/account metadata; keep them out of
    // snapshots and the inbox. Preserve only a known-safe error classification.
    const closeFailed=String(body.error || '').includes('close incomplete')
    throw fatal(closeFailed?'结果已保存，部分检索标签未能确认关闭；请检查 Chrome 中的择木标签组':
      'Chrome Use 操作未完成；请检查浏览器扩展连接与检索标签，已有结果保留',
      closeFailed?'chrome_use_close_incomplete':'chrome_use_command_failed')
  }
  if (!plain && !record(body.data)) throw protocolError()
  return plain?body:body.data
}

export function assessChromeUseStatus(data) {
  const extension=data?.extension, health=extension?.health
  const unavailable=message=>({ready:false,browser_mode:'chrome_use',message})
  if (!supportedVersion(data?.cliVersion,MIN_CLI_VERSION))
    return unavailable(`请安装 Chrome Use ${MIN_CLI_VERSION} 或更新的 1.5.x 版本；本应用使用已验证的扩展接口`)
  if (extension?.hostInstalled!==true || extension?.hostHealthy!==true)
    return unavailable('请完成 Chrome Use 的一次性连接：安装官方 Chrome 扩展，并运行 chrome-use extension install')
  if (extension.relayUp!==true || health?.transportResponsive!==true)
    return unavailable('尚未连接到 Chrome；请打开已安装 Chrome Use 扩展的浏览器，连接后重新开始检索')
  if (health.hostDiagnostic!==true || health.nativeHostVersion!==data.cliVersion)
    return unavailable('Chrome Use 的本地连接组件需要更新；请重新连接扩展后再检索')
  if (!supportedVersion(extension.liveVersion,MIN_EXTENSION_VERSION))
    return unavailable(`请将 Chrome Use 扩展更新到 ${MIN_EXTENSION_VERSION} 或更新的 0.5.x 版本`)
  const profiles=extension.connectedProfiles
  if (!Array.isArray(profiles) || profiles.length!==1 || !record(profiles[0])
    || typeof profiles[0].id!=='string' || profiles[0].id!==extension.profileId || !/^[A-Za-z0-9_-]{1,128}$/.test(extension.profileId))
    return unavailable('请在 Chrome Use 中只连接本次检索使用的一个 Chrome 配置，再回到应用开始检索')
  return {ready:true,browser_mode:'chrome_use',profileId:extension.profileId,
    cliVersion:data.cliVersion,extensionVersion:extension.liveVersion,
    message:'已连接当前 Chrome；在后台复用检索标签，结束后仅关闭本次任务标签'}
}

export async function chromeUseDoctor(dependencies={}) {
  const executable=dependencies.executable || findChromeUse(dependencies)
  if (!executable) return {ready:false,browser_mode:'chrome_use',
    message:'尚未安装 Chrome Use；请展开首页“连接说明”，完成官方 CLI 和 Chrome 扩展的一次性安装'}
  try {
    // A fresh diagnostic session makes status entirely passive: it never
    // bootstraps a worker or creates/activates a tab.
    const data=await invoke(executable,['--session','radar-diagnostic','--json','--no-timing','status'],
      {timeout:11000,env:chromeUseEnvironment(dependencies.env)},dependencies.execute)
    return {...assessChromeUseStatus(data),executable}
  } catch(error) {
    return {ready:false,browser_mode:'chrome_use',message:error.message}
  }
}

function bossLocation(value) {
  try {
    const url=new URL(value)
    return url.protocol==='https:' && (url.hostname==='zhipin.com' || url.hostname.endsWith('.zhipin.com'))
      && !url.username && !url.password && (!url.port || url.port==='443')
  } catch {return false}
}

function validTab(row) {
  return record(row) && /^t[1-9]\d*$/.test(row.tabId) && typeof row.targetId==='string' && Boolean(row.targetId)
}

export async function openChromeUse(config,{checkStop=()=>{},...dependencies}={}) {
  if (!/^[a-f0-9]{32}$/.test(config.task_id || '')) throw fatal('任务标识无效')
  checkStop()
  const info=await chromeUseDoctor(dependencies)
  if (!info.ready) throw fatal(info.message)
  const session='radar-'+config.task_id, env=chromeUseEnvironment(dependencies.env)
  const prefix=['--session',session,'--browser',info.profileId,'--auto-connect','--window','user',
    '--humanize','off','--json','--no-timing']
  const cwd=path.dirname(config.outputPath)
  const now=dependencies.now || Date.now
  const known=new Map(), closedTargets=new Set(), pages=new Set()
  let connected=true, ended=false, started=false
  const stats=(status='pending',message='')=>({browser_mode:'chrome_use',tabs_opened:known.size,
    tabs_closed:closedTargets.size,cleanup_status:status,cleanup_message:message})
  const command=async(args,options={},plain=false)=>{
    const value=await invoke(info.executable,[...prefix,...args],{env,cwd,...options},dependencies.execute,{plain})
    return value
  }
  const remember=row=>{
    if (!validTab(row)) throw protocolError()
    if (!known.has(row.targetId)) known.set(row.targetId,{...row,firstSeen:now()})
    return row
  }
  const markClosed=row=>{
    if (row?.targetId && known.has(row.targetId)) closedTargets.add(row.targetId)
    for (const page of pages) if (page.targetId===row?.targetId) page.closed=true
  }
  const readTabs=async()=>{
    const value=await command(['tab','list'],{timeout:10000})
    if (!Array.isArray(value.tabs) || value.tabs.length>2000) throw protocolError()
    for (const row of value.tabs) if (row.ownership==='created') remember(row)
    return value.tabs
  }
  const verifyClosed=(value,row,wholeSession=false)=>{
    if (value.closed!==true || value.verifiedAbsent!==true || value.browserClosed===true) throw protocolError()
    for (const item of value.tabsClosed || []) markClosed(item)
    if (row) markClosed(row)
    if (value.sessionClosed===true || wholeSession) {
      // The session-wide receipt verifies absence, including previously known
      // tabs the user already closed. They need not occur in tabsClosed again.
      for (const saved of known.values()) markClosed(saved)
      ended=true
    }
  }
  const finish=async()=>{
    if (!started || ended) {connected=false;return stats('complete','本次检索标签已关闭')}
    try {
      // Read session-owned tabs to account for retained scratch tabs or popups.
      // If that diagnostic fails, still attempt the ownership-checked close.
      await readTabs().catch(()=>{})
      const result=await command(['close'],{timeout:25000})
      verifyClosed(result,undefined,true)
      if (result.nothingToClose!==true && !Array.isArray(result.tabsClosed)) throw protocolError()
      ended=true;connected=false
      for (const page of pages) page.closed=true
      return stats('complete','本次检索标签已关闭')
    } catch(error) {
      connected=false
      error.cleanupStats=stats('incomplete','结果已保存，仍有检索标签未能确认关闭；请检查 Chrome 中的择木标签组')
      throw error
    }
  }
  const context={async newPage(){
    checkStop()
    if (ended) throw fatal('本次 Chrome 检索会话已经结束，请重新开始检索')
    if (!started) {
      // This names our session only; it does not adopt or activate a user tab.
      await command(['session','name','择木'],{},true)
      started=true
    }
    let result
    try {result=remember(await command(['tab','new','--label','radar','about:blank']))}
    catch(error) {
      // A timed-out create may have opened a tab. Never create another to retry.
      // Close this exact session and attach truthful cleanup information.
      try {error.cleanupStats=await finish()} catch(closeError) {error.cleanupStats=closeError.cleanupStats}
      throw error
    }
    const page={tabId:result.tabId,targetId:result.targetId,closed:false,currentURL:'about:blank',
      isClosed(){return this.closed},url(){return this.currentURL},
      async goto(url){
        if (!bossLocation(url)) throw fatal('检索页面来源无效，已停止导航')
        // v1.5.281's navigate handler does not apply the generic --tab switch.
        // Select explicitly without --activate, then verify any reuse receipt.
        const selected=await this.request(['tab','select',this.tabId])
        if (selected.tabId!==this.tabId) throw protocolError()
        const value=await this.request(['open',url,'--wait-until','domcontentloaded'])
        if (value.reusedTab===true && value.tabId!==this.tabId)
          throw fatal('Chrome Use 切到了另一张任务标签，已停止读取；请重新检索','chrome_use_target_changed')
        this.currentURL=value.url || url
      },
      async checkLiveness(){
        checkStop()
        try {
          // Passive inventory is the upstream API that cannot bootstrap a
          // browser/session. During a human handoff do not evaluate page JS,
          // attach, navigate, focus, reclaim, or recreate the missing tab.
          const value=await command(['tab','list','--all','--limit','1000'],{timeout:11000})
          if (value.readOnly!==true || !Array.isArray(value.browserTabs)) throw protocolError()
          // The passive, inventory-only connection has no t<N> handles; its
          // stable targetId and saved session ownership are authoritative.
          const row=value.browserTabs.find(row=>row.targetId===this.targetId
            && row.owner?.kind==='self' && row.ownership==='created')
          if (!row) {
            this.closed=true
            throw fatal('检索标签已关闭或不再属于本次任务，已有结果保留','chrome_use_tab_gone')
          }
        } catch(error) {connected=false;throw error}
      },
      async evaluate(fn,arg){
        if (typeof fn!=='function') throw protocolError()
        const script=`(()=>{if(location.protocol!=='https:'||!(location.hostname==='zhipin.com'||location.hostname.endsWith('.zhipin.com')))throw new Error('radar_source_mismatch');return (${fn.toString()})(${arg===undefined?'undefined':JSON.stringify(arg)});})()`
        const value=await this.request(['eval','--stdin'],{input:script})
        if (!Object.hasOwn(value,'result') || !bossLocation(value.origin)) throw protocolError()
        this.currentURL=value.origin
        return value.result
      },
      async request(args,options){
        checkStop()
        if (this.closed || ended || !connected) throw fatal('检索标签已关闭或连接中断，已有结果保留')
        try {return await command(['--tab',this.tabId,...args],options)}
        catch(error){connected=false;throw error}
      },
      waitForTimeout:delay,
      async close(){
        if (this.closed || ended) return
        const value=await command(['tab','close',this.tabId],{timeout:25000})
        verifyClosed(value,this)
      },
    }
    page.mouse={wheel:async(dx,dy)=>{
      if (dx!==0 || !Number.isFinite(dy)) throw protocolError()
      await page.request(['scroll',dy<0?'up':'down',String(Math.abs(dy))])
    }}
    pages.add(page)
    return page
  }}
  return {contexts:()=>[context],isConnected:()=>connected,
    async maintenance({protectedPages=[],humanWaiting=false}={}){
      if (humanWaiting || !started || ended) return stats()
      const protectedIds=new Set(protectedPages.map(page=>page.targetId))
      const rows=await readTabs()
      for (const row of rows) {
        // Never infer ownership from domain/title/group, and keep login,
        // verification and unknown-origin popups for the human handoff.
        if (row.ownership!=='created' || protectedIds.has(row.targetId)) continue
        const saved=known.get(row.targetId)
        if (!saved || now()-saved.firstSeen<30000) continue
        if (row.url!=='about:blank' && (!bossLocation(row.url) || /\/web\/user|captcha|verify|security-check/i.test(row.url))) continue
        const value=await command(['tab','close',row.tabId],{timeout:25000})
        verifyClosed(value,row)
      }
      return stats()
    },close:finish}
}
