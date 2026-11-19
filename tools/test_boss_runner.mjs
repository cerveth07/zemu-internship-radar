import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import {jobURL,searchURL,cityName,cityMatches,normalizeDetail,validatePreferences,accessState,openBrowser,run} from './boss_runner.mjs'

const canonical=id=>'https://www.zhipin.com/job_detail/'+id+'.html'
const card=id=>({url:canonical(id)+'?securityId=transient-'+id,title:'AI 产品实习',company:'样例企业',city:'北京'})
const basic={keywords:['AI 产品'],cities:['北京'],max_pages:{boss:1},max_jobs:2,internship_only:true}

async function fixture(t,preferences={},hooks={}) {
  const directory=await mkdtemp(path.join(os.tmpdir(),'radar-boss-test-'))
  t.after(()=>rm(directory,{recursive:true,force:true}))
  const config={task_id:'b'.repeat(32),preferences:{...basic,...preferences},
    outputPath:path.join(directory,'result.json'),stopPath:path.join(directory,'stop'),
    resumePath:path.join(directory,'resume'),browserProfilePath:path.join(directory,'profile')}
  const visits=[],readings=[],scrolls=[],maintenanceCalls=[]
  let humanWaits=0,closed=0,foreground=0,newPages=0,pageCloses=0
  const pages=[]
  const makePage=()=>{
    const page={current:'',requested:'',async goto(url){
      this.requested=url;this.current=url;visits.push(url)
      const stored=JSON.parse(await readFile(config.outputPath,'utf8'))
      assert.equal(stored.receipts.at(-1).status,'started','Navigation must follow its persisted receipt')
      await hooks.goto?.(this,config,visits)
    },closed:false,isClosed(){return this.closed},async waitForTimeout(){},async bringToFront(){foreground++},
    checkLiveness:hooks.checkLiveness?async function(){return hooks.checkLiveness(this)}:undefined,
    async close(){await hooks.pageClose?.(this);this.closed=true;pageCloses++},
    mouse:{wheel:async()=>{scrolls.push(true)}},url(){return this.current},
    async evaluate(fn){
      if(fn.name==='readCompanyDOM')return hooks.profile?.(this)||{}
      if(fn.name==='inspectPageDOM')return hooks.inspect?.(this)||{url:this.current,title:'BOSS直聘',text:'AI 产品实习 职位描述',hasJobs:true,login:false,verification:false}
      if(fn.name==='listDOM'){
        readings.push('list')
        return {entries:hooks.cards?.(this,scrolls.length)||[card('same')],internshipFilter:hooks.internshipFilter!==false}
      }
      if(fn.name==='detailDOM'){
        readings.push('detail')
        return hooks.fields?.(this)||{title:'AI 产品实习',company:'样例企业',city:'北京',jd:'负责产品研究与需求分析。',salary:'150-200元/天'}
      }
      if(String(fn).includes('Boolean(document.querySelector'))return hooks.scrolling===true
      return undefined
    }}
    pages.push(page);return page
  }
  const context={async newPage(){newPages++;return makePage()}}
  const output=await run(config,{pause:hooks.pause?()=>hooks.pause(config):async()=>{},
    now:hooks.now,
    openBrowser:async()=>{
      let initial=hooks.initialPage?makePage():undefined
      return {contexts:()=>[context],isConnected:()=>hooks.connected?.()!==false,
        takeInitialPage:()=>{const page=initial;initial=undefined;return page},
        maintenance:hooks.maintenance?async options=>{maintenanceCalls.push(options);return hooks.maintenance({...options,pages,config,visits})}:undefined,
        close:async()=>{closed++;return hooks.browserClosed?.({pages,config,visits})}}
    },
    humanWait:async()=>{humanWaits++;await hooks.humanWait?.({pages,config,visits,readings,humanWaits})}})
  return {output,visits,readings,scrolls,config,closed,humanWaits,foreground,newPages,pageCloses,pages,maintenanceCalls}
}

test('canonical BOSS identity rejects unrelated hosts and never stores link parameters',()=>{
  assert.equal(jobURL('/job_detail/id_-~.html?securityId=secret'),canonical('id_-~'))
  assert.equal(jobURL('https://zhipin.com/job_detail/test.html?ka=search'),canonical('test'))
  for(const url of ['http://www.zhipin.com/job_detail/test.html','https://www.zhipin.com.evil.test/job_detail/test.html',
    'https://user@www.zhipin.com/job_detail/test.html','https://www.zhipin.com:8443/job_detail/test.html',
    'https://www.zhipin.com/job_detail/a/b.html','https://www.zhipin.com/web/geek/job'])assert.equal(jobURL(url),'')
})

test('keywords, cities, internship selection and page bounds are explicit',()=>{
  const url=new URL(searchURL('Agent & 产品','北京市',2,basic))
  assert.equal(url.pathname,'/web/geek/jobs')
  assert.equal(url.searchParams.get('query'),'Agent & 产品')
  assert.equal(url.searchParams.get('city'),'101010100');assert.equal(url.searchParams.get('jobType'),'1902')
  assert.equal(url.searchParams.get('page'),'2')
  assert.equal(new URL(searchURL('AI','全国',1,{internship_only:false})).searchParams.has('jobType'),false)
  assert.equal(cityName('上海市'),'上海');assert.equal(cityMatches('北京市·朝阳区','北京'),true)
  assert.equal(cityMatches('','北京'),false);assert.equal(cityMatches('北京','上海'),false)
  assert.throws(()=>validatePreferences({...basic,cities:['未知城市']}),/不会改为全国/)
  assert.throws(()=>validatePreferences({...basic,keywords:Array(10).fill('AI')}))
  assert.throws(()=>validatePreferences({...basic,max_jobs:61}))
  assert.throws(()=>validatePreferences({...basic,max_pages:{boss:0}}))
  assert.throws(()=>validatePreferences({...basic,max_pages:{boss:4}}))
  assert.throws(()=>validatePreferences({...basic,collection_mode:'hydrate'}))
})

test('keyword bounds count Unicode code points rather than UTF-16 units',()=>{
  const character=String.fromCodePoint(0x20000)
  assert.doesNotThrow(()=>validatePreferences({...basic,keywords:[character.repeat(80)]}))
  assert.doesNotThrow(()=>validatePreferences({...basic,keywords:[character.repeat(120)]}))
  assert.throws(()=>validatePreferences({...basic,keywords:[character.repeat(121)]}))
})

test('browser mode is explicit and a failed Chrome Use connection never falls back to managed',async()=>{
  const calls=[],control={checkStop(){}}
  const transports={managed:async()=>{calls.push('managed');return 'owned browser'},
    chrome_use:async()=>{calls.push('chrome_use');throw Object.assign(new Error('Chrome Use unavailable'),{fatal:true})}}
  assert.equal(await openBrowser({preferences:basic},control,transports),'owned browser')
  await assert.rejects(()=>openBrowser({preferences:{...basic,browser_mode:'chrome_use'}},control,transports),/Chrome Use unavailable/)
  assert.deepEqual(calls,['managed','chrome_use'])
  for(const mode of ['',null,'auto'])assert.throws(()=>validatePreferences({...basic,browser_mode:mode}),/浏览器模式无效/)
})

test('only an extracted JD is accepted; unreadable salary digits are never guessed',()=>{
  const item=normalizeDetail(card('a'),{jd:'原始职责',salary:'\ue123-\ue124K'},true)
  assert.equal(item.jd,'原始职责');assert.equal(item.salary,'');assert.equal(item.salary_unreadable,true)
  assert.equal(item.employment_type,'internship');assert.equal(item.jd_available,true)
  assert.equal(normalizeDetail({...card('a'),raw:'这是列表摘要'},{},true).jd_available,false)
  assert.equal(normalizeDetail(card('a'),{jd:'职责含\ue123'},true).jd_available,false)
  assert.equal(normalizeDetail(card('a'),{jd:'全职岗位职责',employment_type:'全职'},true).employment_type,'other')
  assert.throws(()=>normalizeDetail(card('a'),{jd:'甲'.repeat(100001)}),/不截断/)
})

test('company lookup is deduplicated, sourced and does not spend another job detail',async t=>{
  const profile='https://www.zhipin.com/gongsi/acme.html'
  const {output,visits}=await fixture(t,{}, {
    cards:()=>[card('a'),card('b')],
    profile:page=>({profile_company:'样例企业',company_profile_url:profile,
      ...(page.current===profile?{company_headcount:'20-99人',company_sector:'制造业'}:{})}),
  })
  assert.equal(output.items.length,2)
  assert.equal(visits.filter(url=>url===profile).length,1)
  assert.equal(output.platform_state.boss.detail_attempts,2)
  assert.equal(output.platform_state.boss.company_attempts,1)
  assert.equal(output.receipts.filter(row=>row.kind==='company').length,1)
  for(const row of output.items){assert.equal(row.company_headcount,'20-99人');assert.equal(row.company_headcount_source_url,profile)}
})

test('a failed optional company visit still keeps the job that was already saved',async t=>{
  const profile='https://www.zhipin.com/gongsi/acme.html'
  const {output}=await fixture(t,{max_jobs:1},{
    profile:()=>({profile_company:'样例企业',company_profile_url:profile}),
    goto:page=>{if(page.current===profile)throw new Error('connection failed')},
  })
  assert.equal(output.items.length,1);assert.equal(output.items[0].jd_available,true)
  assert.equal(output.items[0].company_headcount,undefined)
  assert.equal(output.receipts.at(-1).kind,'company');assert.equal(output.receipts.at(-1).status,'failed')
})

test('company lookups stop after eight extra pages and keep all requested jobs',async t=>{
  const {output}=await fixture(t,{max_jobs:10},{
    cards:()=>Array.from({length:10},(_,i)=>card('company'+i)),
    profile:page=>{const id=/\/job_detail\/(company\d+)\.html/.exec(page.current)?.[1];return id?{
      profile_company:'样例企业',company_profile_url:'https://www.zhipin.com/gongsi/'+id+'.html'}:{}},
  })
  assert.equal(output.items.length,10);assert.equal(output.platform_state.boss.company_attempts,8)
  assert.equal(output.receipts.filter(row=>row.kind==='company').length,8)
  assert.ok(output.items.every(row=>!row.company_lookup_pending))
})

test('login links in normal content do not trigger a blocking login state',()=>{
  const page={url:searchURL('AI','北京'),title:'BOSS直聘',text:'登录 注册 AI产品实习 岗位职责中有验证码产品',hasJobs:true}
  assert.equal(accessState(page),'readable')
  assert.equal(accessState({...page,login:true}),'login_required')
  assert.equal(accessState({...page,verification:true}),'verification_required')
  assert.equal(accessState({...page,hasJobs:false,text:'请登录后查看职位'}),'login_required')
  assert.equal(accessState({...page,hasJobs:false,text:'没有更多职位，尝试登录查看全部职位'}),'login_required')
  assert.equal(accessState({...page,hasJobs:false,text:'请求过于频繁，请稍后再试'}),'blocked')
  assert.equal(accessState({...page,dialogText:'操作过于频繁，请稍后再试'}),'blocked')
  assert.throws(()=>accessState({...page,url:'https://evil.test/job_detail/a.html'}),/未授权来源/)
})

test('overlapping keywords read each job once and keep transient navigation links in memory only',async t=>{
  const {output,visits,config}=await fixture(t,{keywords:['AI 产品','Agent 产品']})
  assert.equal(output.status,'completed');assert.equal(output.items.length,1)
  assert.equal(output.candidates.length,1);assert.equal(output.candidates[0].hits.length,2)
  assert.equal(visits.filter(url=>url.includes('/job_detail/')).length,1)
  assert.ok(visits.at(-1).includes('securityId=transient-same'),'Actual card navigation argument must survive until the visit')
  assert.equal(output.items[0].url,canonical('same'))
  assert.equal(output.receipts.at(-1).url,canonical('same'))
  assert.equal((await readFile(config.outputPath,'utf8')).includes('transient-same'),false)
})

test('BOSS revisits known identities and returns only current JD',async t=>{
  const detail_cache=[{url:canonical('same'),item:{...card('same'),url:canonical('same'),jd:'历史 JD',jd_available:true},last_detail_at:'2026-10-01T10:00:00.000Z'}]
  for(let n=0;n<2;n++){
    const {output,visits}=await fixture(t,{detail_cache,force_refresh:false},{fields:()=>({title:'产品实习',company:'样例企业',city:'北京',jd:'最新职责 '+n})})
    assert.equal(visits.filter(url=>url.includes('/job_detail/')).length,1)
    assert.equal(output.items[0].jd,'最新职责 '+n)
    assert.equal(output.items[0].reused_snapshot,false)
    assert.equal(output.platform_state.boss.cache_hits,0)
  }
})

test('BOSS closed jobs are reported separately instead of returning an old snapshot',async t=>{
  const detail_cache=[{url:canonical('same'),item:{...card('same'),url:canonical('same'),jd:'历史 JD',jd_available:true},last_detail_at:'2026-10-01T10:00:00.000Z'}]
  const {output,readings}=await fixture(t,{detail_cache},{inspect:page=>({url:page.current,title:'BOSS直聘',hasJobs:!page.current.includes('/job_detail/'),text:page.current.includes('/job_detail/')?'当前职位已下线':'岗位列表'})})
  assert.deepEqual(readings,['list'])
  assert.equal(output.items.length,0);assert.equal(output.candidates[0].detail_state,'closed')
  assert.equal(output.platform_state.boss.closed_jobs[0].url,canonical('same'))
  assert.match(output.platform_state.boss.message,/已关闭 1 条/)
})

test('login pauses the current task until explicit resume, without extra navigation',async t=>{
  let loggedIn=false
  const {output,visits,humanWaits}=await fixture(t,{}, {
    inspect:page=>({url:page.current,title:'BOSS直聘',
      text:loggedIn?'岗位列表':'没有更多职位，尝试登录查看全部职位',hasJobs:loggedIn}),
    humanWait:async({config,visits,readings,humanWaits})=>{
      const stored=JSON.parse(await readFile(config.outputPath,'utf8'))
      assert.equal(stored.status,'running');assert.equal(stored.platform_state.boss.phase,'login_required')
      assert.ok(stored.heartbeat>Date.now()/1000-5);assert.equal(visits.length,1);assert.deepEqual(readings,[])
      if(humanWaits===2){loggedIn=true;await writeFile(config.resumePath,'continue')}
    },
  })
  assert.equal(humanWaits,2);assert.equal(output.status,'completed')
  assert.equal(visits.length,2);assert.equal(output.items.length,1)
})

test('the old and current list paths both require the exact search scope',async t=>{
  const previous=await fixture(t,{},{
    goto:page=>{page.current=page.current.replace('/web/geek/jobs?','/web/geek/job?')},
  })
  assert.equal(previous.output.items.length,1)
  const wrongCity=await fixture(t,{},{
    goto:page=>{page.current=page.current.replace('city=101010100','city=101020100')},
  })
  assert.equal(wrongCity.output.status,'failed')
  assert.match(wrongCity.output.error,/没有显示本次检索目标/)
  assert.deepEqual(wrongCity.readings,[])
})

test('resume while a verification is still visible keeps waiting and does not bypass it',async t=>{
  let verified=false
  const {output,humanWaits}=await fixture(t,{}, {
    inspect:page=>({url:page.current,title:'BOSS直聘',text:verified?'职位描述':'请完成安全验证',hasJobs:verified,verification:!verified}),
    humanWait:async({config,visits,readings,humanWaits})=>{
      assert.equal(visits.length,1);assert.deepEqual(readings,[])
      const stored=JSON.parse(await readFile(config.outputPath,'utf8'))
      assert.equal(stored.platform_state.boss.phase,'verification_required')
      if(humanWaits===2)verified=true
      await writeFile(config.resumePath,'continue')
    },
  })
  assert.equal(humanWaits,2);assert.equal(output.items.length,1);assert.equal(output.status,'completed')
})

test('a login redirect returns to the originally requested keyword only after resume',async t=>{
  let login=true
  const {output,visits}=await fixture(t,{}, {
    goto:page=>{if(login)page.current='https://www.zhipin.com/web/user/'},
    inspect:page=>({url:page.current,title:'BOSS直聘',text:login?'扫码登录':'招聘首页',hasJobs:!login,login}),
    humanWait:async({pages,config})=>{login=false;pages[0].current='https://www.zhipin.com/';await writeFile(config.resumePath,'continue')},
  })
  assert.equal(output.items.length,1)
  assert.equal(visits[0],visits[1]);assert.equal(visits.length,3)
})

test('stopping during verification preserves prior results and leaves the unread job pending',async t=>{
  const {output,visits}=await fixture(t,{}, {
    cards:()=>[card('first'),card('blocked')],
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'岗位',hasJobs:true,verification:page.current.includes('blocked.html')}),
    humanWait:async({config})=>writeFile(config.stopPath,'stop'),
  })
  assert.equal(output.status,'stopped');assert.equal(output.items.length,1)
  assert.equal(output.items[0].url,canonical('first'));assert.equal(output.candidates[1].detail_state,'pending')
  assert.equal(visits.length,3)
})

test('closing the login page ends the task without waiting for a resume marker',async t=>{
  const {output,closed,humanWaits}=await fixture(t,{}, {
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'扫码登录',hasJobs:false,login:true}),
    humanWait:async({pages,config,humanWaits})=>{
      await pages[0].close()
      // Bound the old broken loop so a regression fails without hanging CI.
      if(humanWaits>1)await writeFile(config.stopPath,'stop')
    },
  })
  assert.equal(output.status,'failed');assert.match(output.error,/浏览器.*关闭|连接中断/)
  assert.equal(humanWaits,1);assert.equal(closed,1)
})

test('a browser disconnect during verification preserves previously saved details',async t=>{
  let connected=true
  const {output,closed,humanWaits}=await fixture(t,{}, {
    connected:()=>connected,cards:()=>[card('first'),card('blocked')],
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'岗位',hasJobs:true,verification:page.current.includes('blocked.html')}),
    humanWait:async({config,humanWaits})=>{
      connected=false
      if(humanWaits>1)await writeFile(config.stopPath,'stop')
    },
  })
  assert.equal(output.status,'completed_with_errors');assert.match(output.error,/浏览器.*关闭|连接中断/)
  assert.equal(output.items.length,1);assert.equal(output.items[0].url,canonical('first'))
  assert.equal(humanWaits,1);assert.equal(closed,1)
})

test('a failed heartbeat snapshot interrupts human waiting and closes the browser',async t=>{
  let closed=false,waits=0,config
  await assert.rejects(()=>fixture(t,{}, {
    cards:()=>[card('first'),card('blocked')],browserClosed:()=>{closed=true},
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'岗位',hasJobs:true,verification:page.current.includes('blocked.html')}),
    humanWait:async state=>{
      config=state.config;waits++
      if(waits===1){
        await mkdir(config.outputPath+'.tmp')
        // Exercise the real two-second heartbeat while the main task is paused.
        await new Promise(resolve=>setTimeout(resolve,2200))
      }else await writeFile(config.stopPath,'stop')
    },
  }),/无法保存采集结果/)
  assert.equal(waits,1);assert.equal(closed,true)
  const saved=JSON.parse(await readFile(config.outputPath,'utf8'))
  assert.equal(saved.items.length,1);assert.equal(saved.items[0].url,canonical('first'))
})

test('risk limits stop collection immediately, keep prior results and never retry',async t=>{
  const {output,visits,humanWaits}=await fixture(t,{}, {
    cards:()=>[card('first'),card('blocked'),card('never')],
    inspect:page=>({url:page.current,title:'BOSS直聘',text:page.current.includes('blocked.html')?'访问被拒绝，请稍后再试':'岗位职责',hasJobs:!page.current.includes('blocked.html')}),
  })
  assert.equal(output.status,'completed_with_errors');assert.equal(output.items.length,1)
  assert.equal(visits.length,3);assert.equal(humanWaits,0);assert.match(output.error,/访问受限/)
})

test('fatal transport navigation errors stop the current connection without trying later jobs',async t=>{
  const {output,visits}=await fixture(t,{max_jobs:3,browser_mode:'chrome_use'}, {
    cards:()=>[card('first'),card('blocked'),card('never')],
    goto:page=>{if(page.current.includes('blocked.html'))throw Object.assign(new Error('Chrome Use 连接已断开'),{fatal:true})},
  })
  assert.equal(output.status,'completed_with_errors');assert.equal(output.items.length,1)
  assert.equal(visits.length,3);assert.match(output.error,/Chrome Use 连接已断开/)
})

test('scroll lists advance through visible scroll actions instead of reloading a page parameter',async t=>{
  const {output,visits,scrolls}=await fixture(t,{max_pages:{boss:3},max_jobs:3},{scrolling:true,
    cards:(_page,count)=>Array.from({length:count+1},(_,index)=>card('job'+index)),
  })
  assert.equal(scrolls.length,2);assert.equal(output.candidates.length,3);assert.equal(output.items.length,3)
  assert.equal(visits.filter(url=>!url.includes('/job_detail/')).length,1)
  assert.equal(output.platform_state.boss.list_attempts,3)
})

test('overlap with another keyword does not prevent reaching the current query third page',async t=>{
  const rows={A:['x','y','z'],B:['x','y','new_b3']}
  const {output,visits}=await fixture(t,{keywords:['A','B'],max_pages:{boss:3},max_jobs:4},{
    cards:page=>{const params=new URL(page.current).searchParams;return [card(rows[params.get('query')][Number(params.get('page'))-1])]},
  })
  const lists=visits.filter(url=>url.includes('/web/geek/jobs?')).map(url=>{const params=new URL(url).searchParams;return params.get('query')+params.get('page')})
  assert.deepEqual(lists,['A1','A2','A3','B1','B2','B3'])
  assert.equal(output.status,'completed');assert.equal(output.items.length,4)
  assert.ok(output.items.some(item=>item.url===canonical('new_b3')))
})

test('an unadvanced list retains a shortage status and explanation after detail collection',async t=>{
  const {output,scrolls}=await fixture(t,{max_pages:{boss:3}},{scrolling:true,cards:()=>[card('same')]})
  assert.equal(scrolls.length,1);assert.equal(output.items.length,1)
  assert.equal(output.status,'completed_with_shortage')
  assert.equal(output.platform_state.boss.reason_code,'search_page_not_advanced')
  assert.match(output.platform_state.boss.message,/未能确认.*下一批/)
  assert.match(output.platform_state.boss.message,/AI 产品.*北京.*2/)
})

test('an empty shell is a failure, while an explicit no-result page is completed',async t=>{
  const empty=await fixture(t,{}, {cards:()=>[]})
  assert.equal(empty.output.status,'failed');assert.match(empty.output.error,/没有按零结果处理/)
  const zero=await fixture(t,{}, {cards:()=>[],inspect:page=>({url:page.current,title:'BOSS直聘',text:'暂无相关职位',hasJobs:false})})
  assert.equal(zero.output.status,'completed');assert.equal(zero.output.items.length,0)
})

test('unconfirmed internship filter stops rather than silently mixing full-time jobs',async t=>{
  const {output,visits}=await fixture(t,{}, {internshipFilter:false})
  assert.equal(output.status,'failed');assert.match(output.error,/实习.*筛选/);assert.equal(visits.length,1)
})

test('detail limits preserve unvisited identities and stop creates no extra navigation',async t=>{
  const {output,visits}=await fixture(t,{max_jobs:1},{cards:()=>[card('one'),card('two')]})
  assert.equal(output.items.length,1);assert.equal(output.candidates[1].detail_state,'pending');assert.equal(visits.length,2)
  const stopped=await fixture(t,{}, {pause:config=>writeFile(config.stopPath,'stop')})
  assert.equal(stopped.output.status,'stopped');assert.deepEqual(stopped.visits,[])
})

test('unknown city is retained as incomplete and never inferred from the query',async t=>{
  const {output}=await fixture(t,{}, {cards:()=>[{...card('unknown'),city:''}],
    fields:()=>({title:'AI 产品实习',company:'样例企业',jd:'完整原文',city:''}),
  })
  assert.equal(output.items.length,1);assert.equal(output.items[0].city,'')
  assert.equal(output.items[0].city_unknown,true);assert.equal(output.candidates[0].detail_state,'incomplete')
})

test('login, scrolling and details reuse one task tab without activating it',async t=>{
  let loggedIn=false
  const result=await fixture(t,{max_pages:{boss:2}},{scrolling:true,
    inspect:page=>({url:page.current,title:'BOSS直聘',text:loggedIn?'岗位列表':'扫码登录',hasJobs:loggedIn,login:!loggedIn}),
    cards:(_page,count)=>[card('first'),...(count?[card('second')]:[])],
    humanWait:async({config})=>{loggedIn=true;await writeFile(config.resumePath,'continue')},
  })
  assert.equal(result.foreground,0)
  assert.equal(result.newPages,1);assert.equal(result.pageCloses,1);assert.equal(result.pages.length,1)
  assert.equal(result.output.items.length,2)
  const state=result.output.platform_state.boss
  assert.equal(state.browser_mode,'managed');assert.equal(state.tabs_opened,1);assert.equal(state.tabs_closed,1)
  assert.equal(state.cleanup_status,'complete')
})

test('an explicitly owned initial page is reused without creating another tab',async t=>{
  const {output,newPages,pages,pageCloses}=await fixture(t,{}, {initialPage:true})
  assert.equal(newPages,0);assert.equal(pages.length,1);assert.equal(pageCloses,1)
  assert.equal(output.items.length,1);assert.equal(output.platform_state.boss.tabs_opened,1)
})

test('idle maintenance runs only between visits at thirty-second intervals and protects the task page',async t=>{
  let clock=0
  const result=await fixture(t,{max_jobs:3,browser_mode:'chrome_use'},{now:()=>clock,
    cards:()=>[card('first'),card('second'),card('third')],
    goto:()=>{clock+=11000},
    maintenance:({protectedPages,humanWaiting,pages,visits})=>{
      assert.equal(humanWaiting,false);assert.deepEqual(protectedPages,[pages[0]])
      assert.equal(visits.length,3,'Maintenance must run after the completed read, before the next visit')
      return {tabs_opened:2,tabs_closed:1,cleanup_status:'pending',cleanup_message:'已回收一个闲置任务标签'}
    },
    browserClosed:()=>({tabs_opened:2,tabs_closed:2,cleanup_status:'complete',cleanup_message:'本会话标签已关闭'}),
  })
  assert.equal(result.maintenanceCalls.length,1);assert.equal(result.pages.length,1)
  assert.equal(result.output.platform_state.boss.browser_mode,'chrome_use')
  assert.equal(result.output.platform_state.boss.tabs_opened,2);assert.equal(result.output.platform_state.boss.tabs_closed,2)
})

test('human verification waits remain protected even when maintenance becomes due',async t=>{
  let clock=0,verified=false,maintenanceCount=0
  const {output,maintenanceCalls}=await fixture(t,{}, {now:()=>clock,
    inspect:page=>({url:page.current,title:'BOSS直聘',text:verified?'岗位':'请完成验证',hasJobs:verified,verification:!verified}),
    humanWait:async({config,humanWaits})=>{
      clock+=45000
      assert.equal(maintenanceCount,0,'The waiting page must not be reclaimed by a heartbeat or timer')
      if(humanWaits===2){verified=true;await writeFile(config.resumePath,'continue')}
    },
    maintenance:({humanWaiting,protectedPages,pages})=>{
      maintenanceCount++;assert.equal(humanWaiting,false);assert.deepEqual(protectedPages,[pages[0]])
      return {tabs_opened:1,tabs_closed:0,cleanup_status:'pending'}
    },
  })
  assert.equal(output.items.length,1);assert.equal(maintenanceCalls.length,1)
})

for(const failure of ['task_tab_closed','connection_lost'])test(`passive liveness detects ${failure} during human waiting and preserves earlier results`,async t=>{
  let clock=0,externalFailure=false,probes=0
  const {output,humanWaits,visits}=await fixture(t,{browser_mode:'chrome_use'},{now:()=>clock,
    cards:()=>[card('first'),card('blocked')],
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'岗位',hasJobs:true,verification:page.current.includes('blocked.html')}),
    checkLiveness:()=>{
      probes++
      if(externalFailure)throw Object.assign(new Error(failure==='task_tab_closed'?'检索标签已关闭':'Chrome Use 连接中断'),{fatal:true})
    },
    humanWait:async({config,humanWaits})=>{
      externalFailure=true;clock+=10000
      // Bound the previous polling loop while proving no resume was necessary.
      if(humanWaits>1)await writeFile(config.stopPath,'stop')
    },
  })
  assert.equal(output.status,'completed_with_errors');assert.equal(output.items.length,1)
  assert.equal(output.items[0].url,canonical('first'));assert.equal(visits.length,3)
  assert.equal(humanWaits,1);assert.equal(probes,2);assert.match(output.error,/已关闭|连接中断/)
})

test('passive liveness is checked on handoff and then at most once every ten seconds',async t=>{
  let clock=0,verified=false
  const probes=[]
  const {output,foreground}=await fixture(t,{browser_mode:'chrome_use'},{now:()=>clock,
    inspect:page=>({url:page.current,title:'BOSS直聘',text:'岗位',hasJobs:verified,verification:!verified}),
    checkLiveness:()=>{probes.push(clock)},
    humanWait:async({config,humanWaits})=>{
      clock+=2000
      if(humanWaits===6){verified=true;await writeFile(config.resumePath,'continue')}
    },
  })
  assert.deepEqual(probes,[0,10000]);assert.equal(output.items.length,1);assert.equal(foreground,0)
})

test('a browser cleanup failure preserves results and persists an incomplete cleanup after the close attempt',async t=>{
  const {output,config}=await fixture(t,{}, {
    browserClosed:async({config})=>{
      const before=JSON.parse(await readFile(config.outputPath,'utf8'))
      assert.equal(before.platform_state.boss.cleanup_status,'pending')
      assert.equal(before.finished_at,null)
      throw new Error('fixture session close failed')
    },
  })
  assert.equal(output.status,'completed');assert.equal(output.items.length,1)
  assert.equal(output.platform_state.boss.cleanup_status,'incomplete')
  assert.match(output.platform_state.boss.cleanup_message,/fixture session close failed/)
  const saved=JSON.parse(await readFile(config.outputPath,'utf8'))
  assert.equal(saved.platform_state.boss.cleanup_status,'incomplete');assert.equal(saved.items.length,1)
  assert.ok(saved.finished_at)
})

test('a failed tab close is not reported as complete by an unverified browser disconnect',async t=>{
  const {output,closed}=await fixture(t,{}, {pageClose:()=>{throw new Error('fixture tab close failed')}})
  assert.equal(closed,1);assert.equal(output.items.length,1)
  assert.equal(output.platform_state.boss.tabs_closed,0)
  assert.equal(output.platform_state.boss.cleanup_status,'incomplete')
  assert.match(output.platform_state.boss.cleanup_message,/fixture tab close failed/)
})

test('verified session cleanup can recover a failed first tab-close attempt',async t=>{
  const {output}=await fixture(t,{}, {
    pageClose:()=>{throw new Error('fixture tab close failed')},
    browserClosed:()=>({tabs_opened:1,tabs_closed:1,cleanup_status:'complete',cleanup_message:'已确认本会话标签全部关闭'}),
  })
  assert.equal(output.items.length,1);assert.equal(output.platform_state.boss.tabs_closed,1)
  assert.equal(output.platform_state.boss.cleanup_status,'complete')
  assert.equal(output.platform_state.boss.cleanup_message,'已确认本会话标签全部关闭')
})

test('a task page already included in cumulative maintenance cleanup is not counted twice at exit',async t=>{
  let clock=0
  const allClosed={tabs_opened:2,tabs_closed:2,cleanup_status:'complete',cleanup_message:'本会话标签已关闭'}
  const {output,pageCloses}=await fixture(t,{browser_mode:'chrome_use'},{now:()=>clock,
    cards:()=>[card('first'),card('second')],
    goto:page=>{if(page.current.includes('first.html'))clock=31000},
    maintenance:({pages})=>{
      // The user closed the task page while maintenance closed the last idle
      // tab; the transport's session-close receipt accounts for both targets.
      pages[0].closed=true;return allClosed
    },
    browserClosed:()=>allClosed,
  })
  assert.equal(output.items.length,1);assert.equal(pageCloses,0)
  assert.equal(output.platform_state.boss.tabs_opened,2)
  assert.equal(output.platform_state.boss.tabs_closed,2)
  assert.equal(output.platform_state.boss.cleanup_status,'complete')
})

test('fatal maintenance errors stop before another visit and retain completed details',async t=>{
  let clock=0
  const {output,visits}=await fixture(t,{browser_mode:'chrome_use'},{now:()=>clock,
    cards:()=>[card('first'),card('second')],
    goto:page=>{if(page.current.includes('first.html'))clock=31000},
    maintenance:()=>{throw Object.assign(new Error('Chrome Use 会话不可用'),{fatal:true})},
  })
  assert.equal(output.status,'completed_with_errors');assert.equal(output.items.length,1)
  assert.equal(visits.length,2);assert.match(output.error,/Chrome Use 会话不可用/)
})
