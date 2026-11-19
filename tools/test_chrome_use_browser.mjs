import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,rm} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {assessChromeUseStatus,chromeUseDoctor,chromeUseEnvironment,executeCLI,findChromeUse,openChromeUse} from './chrome_use_browser.mjs'

const healthy=()=>({cliVersion:'1.5.281',extension:{hostInstalled:true,hostHealthy:true,relayUp:true,
  liveVersion:'0.5.105',profileId:'profile-abc',connectedProfiles:[{id:'profile-abc'}],
  health:{transportResponsive:true,hostDiagnostic:true,nativeHostVersion:'1.5.281'}}})
const reply=(data,extra={})=>({exitCode:0,stdout:JSON.stringify({success:true,data,...extra})})
const target=(id,ownership='created',url='about:blank')=>({tabId:'t'+id,targetId:'target-'+id,label:'radar',ownership,url})

async function fixture(t,hooks={}) {
  const directory=await mkdtemp(path.join(os.tmpdir(),'radar-chrome-use-'))
  t.after(()=>rm(directory,{recursive:true,force:true}))
  const config={task_id:'e'.repeat(32),outputPath:path.join(directory,'result.json')}
  const calls=[],tabs=[],closed=[]
  let clock=0,currentURL='about:blank'
  const execute=async(executable,args,options)=>{
    const at=args.findIndex(value=>['status','session','tab','open','eval','close','scroll'].includes(value))
    const command=args.slice(at)
    const call={executable,args,options,command};calls.push(call)
    const override=await hooks.execute?.(call,{tabs,closed})
    if(override)return override
    if(command[0]==='status')return reply(healthy())
    if(command[0]==='session')return {exitCode:0,stdout:JSON.stringify({ok:true})}
    if(command[0]==='tab'&&command[1]==='new'){
      const row=target(1);tabs.push(row);return reply({...row,total:tabs.length})
    }
    if(command[0]==='tab'&&command[1]==='list')return command.includes('--all')?
      reply({readOnly:true,browserTabs:tabs.map(({tabId,...row})=>({...row,owner:{kind:row.ownership==='created'?'self':'user'}}))}):reply({tabs})
    if(command[0]==='tab'&&command[1]==='select')return reply({tabId:command[2]})
    if(command[0]==='tab'&&command[1]==='close'){
      const row=tabs.find(row=>row.tabId===command[2])
      assert.equal(row?.ownership,'created','A personal/adopted tab must never be closed')
      tabs.splice(tabs.indexOf(row),1);closed.push(row.tabId)
      return reply({closed:true,verifiedAbsent:true,...row,
        sessionClosed:!tabs.some(value=>value.ownership==='created'),tabsClosed:[row]})
    }
    if(command[0]==='open'){currentURL=command[1];return reply({url:currentURL})}
    if(command[0]==='eval')return reply({result:{title:'中文岗位',count:2},origin:currentURL})
    if(command[0]==='scroll')return reply({scrolled:true})
    if(command[0]==='close'){
      const own=tabs.filter(row=>row.ownership==='created')
      for(const row of own){closed.push(row.tabId);tabs.splice(tabs.indexOf(row),1)}
      return reply({closed:true,verifiedAbsent:true,tabsClosed:own})
    }
    throw new Error('Unexpected fixture command: '+command.join(' '))
  }
  const browser=await openChromeUse(config,{executable:'/test/chrome-use',execute,now:()=>clock,
    env:{PATH:'/test',CI:'1',AGENT_BROWSER_FORCE_LAUNCH:'1',CHROME_USE_AUTO_UPGRADE:'1'}})
  return {browser,calls,tabs,closed,setClock:value=>{clock=value}}
}

test('readiness requires a live matching host, supported versions and an unambiguous profile',()=>{
  assert.equal(assessChromeUseStatus(healthy()).ready,true)
  for(const alter of [
    value=>{value.cliVersion='1.5.280'},value=>{value.cliVersion='2.0.0'},
    value=>{value.extension.hostHealthy=false},value=>{value.extension.relayUp=false},
    value=>{value.extension.health.transportResponsive=false},
    value=>{value.extension.health.hostDiagnostic=false},
    value=>{value.extension.health.nativeHostVersion='1.5.280'},
    value=>{value.extension.liveVersion='0.5.20'},
    value=>{value.extension.connectedProfiles.push({id:'another-profile'})},
    value=>{value.extension.profileId='different-profile'},
  ]) {const value=healthy();alter(value);assert.equal(assessChromeUseStatus(value).ready,false)}
  const newer=healthy();newer.cliVersion='1.5.282';newer.extension.health.nativeHostVersion='1.5.282'
  assert.equal(assessChromeUseStatus(newer).ready,true)
})

test('diagnosis is passive and a failed check never creates a tab or worker',async()=>{
  const calls=[]
  const result=await chromeUseDoctor({executable:'/test/chrome-use',execute:async(_file,args)=>{
    calls.push(args);const data=healthy();data.extension.relayUp=false;return reply(data)
  }})
  assert.equal(result.ready,false);assert.equal(calls.length,1);assert.equal(calls[0].at(-1),'status')
  assert.ok(!calls[0].some(value=>['open','connect','new','extension'].includes(value)))
})

test('Windows CLI lookup supports official install and explicit paths but no relative command',()=>{
  const installed='C:\\Users\\U\\AppData\\Local\\Programs\\chrome-use\\chrome-use.exe'
  assert.equal(findChromeUse({platform:'win32',home:'C:\\Users\\U',env:{LOCALAPPDATA:'C:\\Users\\U\\AppData\\Local'},isFile:value=>value===installed}),installed)
  assert.equal(findChromeUse({platform:'win32',env:{RADAR_CHROME_USE_PATH:'.\\chrome-use.exe'},isFile:()=>true}),'')
  assert.equal(findChromeUse({platform:'win32',env:{RADAR_CHROME_USE_PATH:'D:\\Tools\\chrome-use.exe'},isFile:()=>true}),'D:\\Tools\\chrome-use.exe')
})

test('connection environment prevents implicit launch/update while retaining user approval policy',()=>{
  const env=chromeUseEnvironment({CI:'1',AGENT_BROWSER_FORCE_LAUNCH:'1',CHROME_USE_AUTO_UPGRADE:'1',AGENT_BROWSER_ACTION_POLICY:'restrict.json'})
  assert.equal(env.CI,undefined);assert.equal(env.AGENT_BROWSER_FORCE_LAUNCH,undefined)
  assert.equal(env.AGENT_BROWSER_NO_AUTO_CONNECT,undefined)
  assert.equal(env.AGENT_BROWSER_NO_AUTO_RECONNECT,'1');assert.equal(env.CHROME_USE_AUTO_UPGRADE,'0')
  assert.equal(env.AGENT_BROWSER_ACTION_POLICY,'restrict.json')
})

test('one owned page remains background, commands pin its ID, and Chinese eval travels on stdin',async t=>{
  const {browser,calls}=await fixture(t)
  const page=await browser.contexts()[0].newPage()
  await page.goto('https://www.zhipin.com/web/geek/job?query=产品实习')
  const value=await page.evaluate(()=>({title:document.title,text:'中文：` $() " \\'}))
  assert.deepEqual(value,{title:'中文岗位',count:2})
  await page.mouse.wheel(0,1000)
  const evaluation=calls.find(call=>call.command[0]==='eval')
  const navigation=calls.findIndex(call=>call.command[0]==='open')
  assert.deepEqual(calls[navigation-1].command,['tab','select','t1'])
  assert.deepEqual(evaluation.command,['eval','--stdin']);assert.ok(evaluation.options.input.includes('中文'))
  for(const call of calls.filter(call=>['open','eval','scroll'].includes(call.command[0])))
    assert.equal(call.args[call.args.indexOf('--tab')+1],'t1')
  for(const call of calls){
    assert.ok(!call.args.some(value=>['--activate','--front','--launch','--force','adopt','--all'].includes(value)))
    if(call.command[0]!=='status')assert.equal(call.args[call.args.indexOf('--browser')+1],'profile-abc')
    assert.equal(call.options.env.AGENT_BROWSER_NO_AUTO_RECONNECT,'1')
  }
  await page.close();const stats=await browser.close()
  assert.equal(stats.cleanup_status,'complete');assert.equal(stats.tabs_opened,1);assert.equal(stats.tabs_closed,1)
  assert.equal(calls.filter(call=>call.command[0]==='close').length,0,'Closing the last owned tab already ended this session')
})

test('idle reclamation protects the working page, handoff, foreign/adopted tabs and login popups',async t=>{
  const {browser,calls,tabs,closed,setClock}=await fixture(t)
  const page=await browser.contexts()[0].newPage()
  tabs.push(target(2),target(3,'foreign'),target(4,'adopted'),target(5,'created','https://www.zhipin.com/web/user/'))
  const count=calls.length
  await browser.maintenance({protectedPages:[page],humanWaiting:true})
  assert.equal(calls.length,count,'No CLI call may clean up during human handoff')
  await browser.maintenance({protectedPages:[page]})
  assert.deepEqual(closed,[])
  setClock(30000)
  const stats=await browser.maintenance({protectedPages:[page]})
  assert.deepEqual(closed,['t2']);assert.equal(page.isClosed(),false)
  assert.equal(stats.tabs_opened,3);assert.equal(stats.tabs_closed,1)
  await page.close();const end=await browser.close()
  assert.deepEqual(tabs.map(row=>row.tabId),['t3','t4']);assert.equal(end.cleanup_status,'complete')
  assert.equal(end.tabs_opened,3);assert.equal(end.tabs_closed,3)
})

test('a failed or unverified close never reports successful reclamation',async t=>{
  const {browser}=await fixture(t,{execute:call=>{
    if(call.command[0]==='close'||call.command[0]==='tab'&&call.command[1]==='close')
      return reply({closed:true,verifiedAbsent:false})
  }})
  const page=await browser.contexts()[0].newPage()
  await assert.rejects(page.close())
  await assert.rejects(browser.close(),error=>{
    assert.equal(error.cleanupStats.cleanup_status,'incomplete')
    assert.equal(error.cleanupStats.tabs_closed,0);return true
  })
})

test('verified session close accounts for a previously known popup closed by the user',async t=>{
  const {browser,tabs}=await fixture(t)
  const page=await browser.contexts()[0].newPage()
  tabs.push(target(2))
  await browser.maintenance({protectedPages:[page]})
  tabs.splice(1,1)
  await page.close()
  const stats=await browser.close()
  assert.equal(stats.cleanup_status,'complete');assert.equal(stats.tabs_opened,2);assert.equal(stats.tabs_closed,2)
})

test('handoff liveness is read-only and notices a closed task tab without recreating it',async t=>{
  const {browser,tabs,calls}=await fixture(t)
  const page=await browser.contexts()[0].newPage()
  await page.checkLiveness()
  const before=calls.length
  tabs.length=0
  await assert.rejects(page.checkLiveness(),{code:'chrome_use_tab_gone'})
  assert.equal(page.isClosed(),true);assert.equal(browser.isConnected(),false)
  assert.deepEqual(calls.slice(before).map(call=>call.command),[['tab','list','--all','--limit','1000']])
  await browser.close()
})

test('navigation refuses a same-session URL reuse that changed the target',async t=>{
  const {browser,calls}=await fixture(t,{execute:call=>call.command[0]==='open'?
    reply({reusedTab:true,tabId:'t2',url:call.command[1]}):undefined})
  const page=await browser.contexts()[0].newPage()
  await assert.rejects(page.goto('https://www.zhipin.com/web/geek/job'),{code:'chrome_use_target_changed'})
  assert.equal(calls.filter(call=>call.command[0]==='open').length,1)
  assert.equal(calls.filter(call=>call.command[0]==='eval').length,0)
  await browser.close()
})

test('timeout creating a tab is not retried and the retained task tab is cleaned',async t=>{
  const {browser,calls}=await fixture(t,{execute:(call,{tabs})=>{
    if(call.command[0]==='tab'&&call.command[1]==='new'){
      tabs.push(target(1));return {timedOut:true,exitCode:-1,stdout:''}
    }
  }})
  await assert.rejects(browser.contexts()[0].newPage(),error=>{
    assert.equal(error.code,'chrome_use_timeout');assert.equal(error.cleanupStats.cleanup_status,'complete')
    assert.equal(error.cleanupStats.tabs_closed,1);return true
  })
  assert.equal(calls.filter(call=>call.command[0]==='tab'&&call.command[1]==='new').length,1)
  assert.equal(calls.filter(call=>call.command[0]==='close').length,1)
})

test('an approval request is preserved as a stop, never treated as successful navigation',async t=>{
  const {browser,calls}=await fixture(t,{execute:call=>call.command[0]==='open'?{
    exitCode:3,stdout:JSON.stringify({success:true,data:{confirmation_required:true}})}:undefined})
  const page=await browser.contexts()[0].newPage()
  await assert.rejects(page.goto('https://www.zhipin.com/web/geek/job'),{code:'chrome_use_approval_required'})
  assert.equal(browser.isConnected(),false)
  assert.equal(calls.filter(call=>call.command[0]==='open').length,1)
  await browser.close()
})

test('unexpected CLI errors are sanitized instead of copying account URLs into snapshots',async()=>{
  const result=await chromeUseDoctor({executable:'/test/chrome-use',execute:async()=>({exitCode:1,
    stdout:JSON.stringify({success:false,error:'failure https://example.com/?token=secret user@example.com'})})})
  assert.equal(result.ready,false);assert.ok(!/secret|user@example/.test(result.message))
})

test('the process transport preserves UTF-8 and shell metacharacters literally',async()=>{
  const script="process.stdin.setEncoding('utf8');let s='';process.stdin.on('data',v=>s+=v);process.stdin.on('end',()=>process.stdout.write(JSON.stringify({input:s,arg:process.argv[1]})))"
  const literal='岗位 `not-a-command` $(also-literal)'
  const result=await executeCLI(process.execPath,['-e',script,literal],{input:'中文脚本\n下一行',timeout:5000})
  assert.equal(result.exitCode,0);assert.deepEqual(JSON.parse(result.stdout),{input:'中文脚本\n下一行',arg:literal})
})
