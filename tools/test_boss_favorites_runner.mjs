import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,rm,readFile,writeFile} from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import {run,clickFavorite,autoSelection,validate} from './boss_favorites_runner.mjs'

const url=id=>'https://www.zhipin.com/job_detail/'+id+'.html'
const target=(id=1,extra={})=>({id,title:'运营实习',company:'样例企业',city:'北京',source_url:'',...extra})
const candidate=(id='a',extra={})=>({url:url(id),title:'运营实习',company:'样例企业',city:'北京',...extra})

async function fixture(t,action,targets,hooks={}){
 const directory=await mkdtemp(path.join(os.tmpdir(),'radar-favorite-'));t.after(()=>rm(directory,{recursive:true,force:true}))
 const config={task_id:'f'.repeat(32),preferences:{action,targets,browser_mode:hooks.chrome?'chrome_use':'managed',internship_only:true},
  outputPath:path.join(directory,'result.json'),stopPath:path.join(directory,'stop'),resumePath:path.join(directory,'resume')}
 const visits=[],clicks=[],saved=new Set(hooks.saved||[]),closed=[],states=[]
 let login=false
 const page={current:'about:blank',isClosed:()=>false,url(){return this.current},route:async()=>{},close:async()=>closed.push('page'),
  request:{},
  async evaluate(fn){
   if(fn.name==='favoriteDOM')return {url:this.current,title:'运营实习',state:hooks.missing?'unavailable':saved.has(this.current)?'saved':'available',label:'感兴趣',count:hooks.ambiguous?2:1}
   if(fn.name==='inspectPageDOM')return {url:this.current,title:'BOSS直聘',text:login?'扫码登录':'岗位职责',hasJobs:!login,login}
   throw Error('Unexpected DOM read '+fn.name)
  },
  locator(selector){
   assert.equal(selector,'a.btn-interest:visible,button.btn-interest:visible','Only the interest control may be targeted')
   return {filter({hasText}){assert.equal(hasText.test('感兴趣'),true);assert.equal(hasText.test('立即沟通'),false);return this},count:async()=>1,
    click:async()=>{
     const snapshot=JSON.parse(await readFile(config.outputPath,'utf8'))
     assert(snapshot.results.every(r=>r.favorite_status==='unknown'),'Persist uncertainty before the mutation')
     clicks.push(page.current);await hooks.click?.({page,config,saved});
     if(!hooks.noConfirm)saved.add(page.current)
     if(hooks.loginAfterClick)login=true
     if(hooks.throwAfterClick)throw new Error('click outcome unknown')
    }}
  }}
 if(hooks.chrome)page.request=async args=>{
  if(args[0]==='snapshot')return {origin:page.current,refs:{e1:{role:'link',name:'感兴趣'},e2:{role:'link',name:'立即沟通'}}}
  assert.deepEqual(args,['click','@e1']);clicks.push(page.current);saved.add(page.current);return {clicked:true}
 }
 const adapter={detailURL:c=>c.url,
  async navigate(p,control){p.current=control.url;visits.push(control.url);await hooks.navigate?.({page:p,control,config})},
  async readList(p,hit){return hooks.list?.(hit)||[candidate()]},
  async readDetail(p,c,control){
   await hooks.detail?.({page:p,candidate:c,control,config})
   if(hooks.closed?.includes(c.url))return null
   if(hooks.failed?.includes(c.url))throw Error('读取失败')
   return {...c,title:c.title||'运营实习',company:c.company||'样例企业',city:c.city||'北京',jd:'本次读取的完整职责',jd_available:true,salary:'100元/天'}
  }}
 const output=await run(config,{adapter,openBrowser:async()=>({contexts:()=>[{newPage:async()=>page}],close:async()=>{closed.push('browser');return hooks.cleanup||{cleanup_status:'complete'}}}),
  pause:async()=>{},verifyWait:async()=>{},humanWait:async()=>{states.push(JSON.parse(await readFile(config.outputPath,'utf8')).platform_state.boss.phase);login=false;await writeFile(config.resumePath,'continue')}})
 return {output,visits,clicks,saved,config,closed,states}
}

test('matching reads fresh details, auto-selects an exact company/title and never clicks',async t=>{
 const {output,clicks,visits}=await fixture(t,'match',[target()])
 assert.equal(output.status,'completed');assert.equal(clicks.length,0);assert.equal(visits.length,2)
 assert.equal(output.results[0].candidates[0].selected,true)
 assert.equal(output.results[0].candidates[0].jd,'本次读取的完整职责')
})

test('name-only and approximate-company matches require a choice, brands are not silently treated as employers',()=>{
 const active={...candidate(),availability:'active',favorite_status:'not_checked'}
 assert.deepEqual(autoSelection(target(1,{company:''}),[active]),[])
 assert.deepEqual(autoSelection(target(1,{company:'集团品牌'}),[active]),[])
 assert.deepEqual(autoSelection(target(),[active,{...active,url:url('b')}]),[])
 assert.deepEqual(autoSelection(target(1,{source_url:url('a')}),[active]),[url('a')])
})

test('closed, unreadable and empty search results remain distinct without cached JD',async t=>{
 const {output,clicks}=await fixture(t,'match',[target(1,{source_url:url('closed')}),target(2,{source_url:url('failed')}),target(3)],
  {closed:[url('closed')],failed:[url('failed')],list:()=>[]})
 assert.deepEqual(output.results.map(r=>r.state),['closed','failed','not_found']);assert.equal(clicks.length,0)
 assert.equal(output.results[0].candidates[0].jd,'')
})

test('favorite confirms saved state and duplicate URLs consume exactly one click',async t=>{
 const {output,clicks,closed}=await fixture(t,'favorite',[target(1,{url:url('a')}),target(2,{url:url('a')})])
 assert.equal(output.status,'completed');assert.deepEqual(clicks,[url('a')]);assert.equal(output.platform_state.boss.saved,1)
 assert.equal(output.results.length,2);assert(output.results.every(r=>r.favorite_status==='saved'))
 assert.deepEqual(closed,['page','browser'])
})

test('already saved and closed jobs never receive a click',async t=>{
 const {output,clicks}=await fixture(t,'favorite',[target(1,{url:url('saved')}),target(2,{url:url('closed')})],{saved:[url('saved')],closed:[url('closed')]})
 assert.equal(clicks.length,0);assert.deepEqual(output.results.map(r=>r.favorite_status),['already_saved','not_checked'])
 assert.equal(output.results[1].availability,'closed')
})

test('an uncertain click outcome stays unknown and is never retried blindly',async t=>{
 const {output,clicks}=await fixture(t,'favorite',[target(1,{url:url('a')})],{throwAfterClick:true})
 assert.equal(clicks.length,1);assert.equal(output.results[0].favorite_status,'unknown')
 assert.equal(output.platform_state.boss.saved,0)
 const retry=await fixture(t,'favorite',[target(1,{url:url('a')})],{saved:[url('a')]})
 assert.equal(retry.clicks.length,0);assert.equal(retry.output.results[0].favorite_status,'already_saved')
})

test('a missing success response never becomes saved',async t=>{
 const {output,clicks}=await fixture(t,'favorite',[target(1,{url:url('a')})],{noConfirm:true})
 assert.equal(clicks.length,1);assert.equal(output.results[0].favorite_status,'unknown');assert.match(output.results[0].error,/未重复点击/)
})

test('stop before a mutation preserves an unknown result without clicking',async t=>{
 const {output,clicks}=await fixture(t,'favorite',[target(1,{url:url('a')})],{detail:async({config})=>writeFile(config.stopPath,'stop')})
 assert.equal(output.status,'stopped');assert.equal(clicks.length,0)
 assert.equal(output.results[0].favorite_status,'unknown')
})

test('a login after the favorite click waits for resume and does not click again',async t=>{
 const {output,clicks,states}=await fixture(t,'favorite',[target(1,{url:url('a')})],{loginAfterClick:true})
 assert.deepEqual(states,['login_required']);assert.equal(clicks.length,1);assert.equal(output.results[0].favorite_status,'saved')
})

test('Chrome Use clicks only the freshly located exact interest reference',async t=>{
 const {output,clicks}=await fixture(t,'favorite',[target(1,{url:url('a')})],{chrome:true})
 assert.equal(clicks.length,1);assert.equal(output.results[0].favorite_status,'saved')
})

test('ambiguous or missing favorite controls do not fall through to contact buttons',async t=>{
 const ambiguous=await fixture(t,'favorite',[target(1,{url:url('a')})],{ambiguous:true})
 assert.equal(ambiguous.clicks.length,0);assert.equal(ambiguous.output.results[0].favorite_status,'unknown')
 const missing=await fixture(t,'favorite',[target(1,{url:url('a')})],{missing:true})
 assert.equal(missing.clicks.length,0);assert.equal(missing.output.results[0].availability,'unread')
})

test('search limits leave later entries unprocessed for the next run',async t=>{
 const {output}=await fixture(t,'match',Array.from({length:13},(_,i)=>target(i+1,{company:''})))
 assert.equal(output.platform_state.boss.list_attempts,12);assert.equal(output.results.length,12)
})

test('one live detail can match different input cities without reusing the first city decision',async t=>{
 const {output,visits}=await fixture(t,'match',[target(1),target(2,{city:'上海'})],{list:()=>[candidate('a',{city:'上海'})]})
 assert.equal(visits.filter(value=>value.includes('/job_detail/')).length,1)
 assert.equal(output.results[0].candidates[0].availability,'unread')
 assert.equal(output.results[1].candidates[0].availability,'active')
})

test('arbitrary navigation targets and oversized task batches are rejected',()=>{
 const config={task_id:'a'.repeat(32),preferences:{action:'favorite',browser_mode:'managed',targets:[target(1,{url:'https://www.zhipin.com/web/geek/chat'})]}}
 assert.throws(()=>validate(config))
 assert.throws(()=>validate({...config,preferences:{...config.preferences,targets:Array.from({length:61},(_,i)=>target(i+1,{url:url('a')}))}}))
})
