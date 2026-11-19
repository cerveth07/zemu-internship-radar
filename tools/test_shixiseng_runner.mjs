import { test } from 'node:test'
import assert from 'node:assert/strict'
import { jobURL, searchURL, cityMatches, normalizeDetail } from './shixiseng_runner.mjs'
import { detectStopWall } from '../vendor/internship-workbench/stopRules.mjs'

const base = {url:'https://www.shixiseng.com/intern/inn_fixture',title:'列表标题',company:'列表企业',raw:'列表摘要'}
test('only canonical Shixiseng job links are accepted', () => {
  for(const url of ['http://www.shixiseng.com/intern/inn_fixture','https://www.shixiseng.com.evil.test/intern/inn_fixture','https://user@www.shixiseng.com/intern/inn_fixture','https://www.shixiseng.com:8686/intern/inn_fixture','https://www.shixiseng.com/interns'])assert.equal(jobURL(url),'')
  assert.equal(jobURL(base.url+'?tracking=1#view'),base.url)
})
test('query and city are encoded separately; missing city does not pass a city filter', () => {
  const url=new URL(searchURL('AI 产品 &研发','北京',2))
  assert.equal(url.searchParams.get('keyword'),'AI 产品 &研发');assert.equal(url.searchParams.get('city'),'北京');assert.equal(url.searchParams.get('page'),'2')
  assert.equal(cityMatches('北京市/朝阳区','北京'),true);assert.equal(cityMatches('上海','北京'),false);assert.equal(cityMatches('','北京'),false)
})
test('details repair glyph fields and keep source metadata separate from original JD', () => {
  const item=normalizeDetail({...base,title:'\ue012',company:'\ue013'},null,{iname:'AI 产品实习',cname:'样例企业',info:'<p>职责</p><p>设计 &amp; 研究</p>',city:'北京',minsal:100,maxsal:200,day:4,month:3,degree:'本科',refresh:'2026-10-09 10:20:00',endtime:'2026-12-31'})
  assert.equal(item.jd,'职责\n设计 & 研究');assert.equal(item.salary,'100-200/天');assert.equal(item.title,'AI 产品实习');assert.equal(item.company,'样例企业')
  assert.equal(item.onsite_days,'4天/周');assert.equal(item.duration,'实习3个月');assert.equal(item.eligibility,'本科');assert.equal(item.deadline,'2026-12-31')
})
test('a list summary is never presented as a full JD; glyphs remain visible for review', () => {
  assert.equal(normalizeDetail(base,null).jd,'');assert.equal(normalizeDetail(base,null).jd_available,false)
  const value=normalizeDetail(base,{raw:'原文含\ue012未读字符'})
  assert.equal(value.jd,'原文含\ue012未读字符');assert.equal(value.jd_available,false)
  assert.equal(normalizeDetail(base,null,{chance:'可转正'}).salary,'')
})
test('long descriptions are rejected rather than silently truncated', () => {
  assert.throws(()=>normalizeDetail(base,null,{info:'甲'.repeat(100001)}),/不截断/)
})
test('visible validation and rate limits stop reading', () => {
  assert.equal(detectStopWall('请完成安全验证').hit,true);assert.equal(detectStopWall('请求过于频繁').hit,true)
  assert.equal(detectStopWall('AI产品实习 岗位职责').hit,false)
})

import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { run, mergeCandidate, validatePreferences } from './shixiseng_runner.mjs'

const canonical = id => `https://www.shixiseng.com/intern/inn_${id}`
const hit = (keyword='AI',city='北京',page=1) => ({keyword,city,page})
const entry = id => ({url:canonical(id),title:'AI 产品实习',company:'样例企业',city:'北京',hits:[hit()],detail_state:'pending'})
const cacheEntry = id => ({url:canonical(id),item:{url:canonical(id),title:'AI 产品实习',company:'样例企业',city:'北京',jd:'完整职责',jd_available:true},last_detail_at:'2026-10-01T10:00:00.000Z'})
async function fixture(t, preferences, hooks={}) {
  const directory=await mkdtemp(path.join(os.tmpdir(),'radar-stage-'))
  t.after(()=>rm(directory,{recursive:true,force:true}))
  const config={task_id:'a'.repeat(32),preferences,outputPath:path.join(directory,'result.json'),stopPath:path.join(directory,'stop')}
  const visits=[]
  const page={current:'',async goto(url){
    this.current=url;visits.push(url)
    const snapshot=JSON.parse(await readFile(config.outputPath,'utf8'))
    assert.equal(snapshot.receipts.at(-1).status,'started')
    if(hooks.goto)await hooks.goto(url,config,snapshot)
  },async waitForTimeout(){},async addScriptTag(){},async close(){},url(){return this.current},
    locator(selector){const current=this.current;return selector==='body'?{innerText:async()=>hooks.body?.(current)||'AI 产品实习 岗位职责'}:{evaluateAll:async()=>[]}},
    async evaluate(fn,mode){
      if(fn.name==='readCompanyDOM')return hooks.profile?.(this.current)||{}
      if(!['list','detail'].includes(mode))return hooks.fields?.(this.current)||{iname:'AI 产品实习',cname:'样例企业',city:'北京',info:'<p>完整职责</p>'}
      return {jobs:mode==='list'?(hooks.lists?.(this.current)||[entry('same')]):[hooks.detail?.(this.current)||{title:'AI 产品实习',company:'样例企业',city:'北京',raw:'完整职责'}]}
    }}
  let opened=0,tabCloses=0
  const pages=[]
  const browser={contexts:()=>[{newPage:async()=>{
    const taskPage=Object.assign(Object.create(page),{current:'',closed:false,isClosed(){return this.closed},async close(){this.closed=true;tabCloses++}})
    pages.push(taskPage);return taskPage
  }}],close:async()=>{}}
  const output=await run(config,{pause:hooks.pause|| (async()=>{}),openBrowser:async()=>{opened++;return browser}})
  return {output,visits,opened,config,pages,tabCloses}
}

test('semantic overlap retains every query hit and reads one detail after all lists',async t=>{
  const {output,visits,pages,tabCloses}=await fixture(t,{keywords:['Agent 产品','智能体产品'],cities:['北京'],max_pages:{shixiseng:1},max_jobs:1})
  assert.deepEqual(visits.map(url=>url.includes('/interns?')?'list':'detail'),['list','list','detail'])
  assert.equal(output.candidates.length,1);assert.equal(output.candidates[0].hits.length,2)
  assert.equal(output.receipts.filter(v=>v.kind==='detail').length,1);assert.equal(output.items.length,1)
  assert.equal(pages.length,1);assert.equal(tabCloses,1)
  assert.equal('browser_mode' in output.platform_state.shixiseng,false)
})
test('discover collects identities without reading JD',async t=>{
  const {output,visits}=await fixture(t,{collection_mode:'discover',keywords:['AI'],cities:['北京']})
  assert.equal(visits.length,1);assert.equal(output.items.length,0);assert.equal(output.candidates[0].detail_state,'pending')
})

test('Shixiseng company pages supply a missing scale once for multiple jobs',async t=>{
  const profile='https://www.shixiseng.com/com/com_example'
  const {output,visits}=await fixture(t,{keywords:['财务'],cities:['北京'],max_pages:{shixiseng:1},max_jobs:2},{
    lists:()=>[entry('a'),entry('b')],
    profile:url=>({profile_company:'样例企业',company_profile_url:profile,...(url===profile?{company_headcount:'500-2000人',company_sector:'金融'}:{})}),
  })
  assert.equal(output.items.length,2);assert.equal(visits.filter(url=>url===profile).length,1)
  for(const row of output.items){assert.equal(row.company_headcount,'500-2000人');assert.equal(row.company_headcount_source_url,profile)}
})
test('every run reads a known job again and replaces its old JD despite legacy cache preferences',async t=>{
  const preferences={collection_mode:'hydrate',candidates:[entry('same')],detail_cache:[cacheEntry('same')],force_refresh:false,max_jobs:1}
  for(let n=0;n<2;n++){
    const {output,visits,opened}=await fixture(t,preferences,{fields:()=>({iname:'AI 产品实习',cname:'样例企业',city:'北京',info:'本次更新职责 '+n})})
    assert.equal(opened,1);assert.deepEqual(visits,[canonical('same')]);assert.equal(output.receipts[0].kind,'detail')
    assert.equal(output.items[0].jd,'本次更新职责 '+n);assert.equal(output.items[0].reused_snapshot,false)
    assert.equal(output.items[0].observed_at,output.items[0].last_detail_at)
    assert.notEqual(output.items[0].last_detail_at,preferences.detail_cache[0].last_detail_at)
    assert.equal(output.platform_state.shixiseng.cache_hits,0)
  }
})
test('known jobs consume fresh detail budget; unvisited candidates remain pending',async t=>{
  const {output,visits}=await fixture(t,{collection_mode:'hydrate',candidates:[entry('cached'),entry('new1'),entry('new2')],detail_cache:[cacheEntry('cached')],max_jobs:1})
  assert.deepEqual(visits,[canonical('cached')]);assert.equal(output.items.length,1)
  assert.equal(output.candidates[1].detail_state,'pending')
  assert.equal(output.candidates[2].detail_state,'pending');assert.equal(output.platform_state.shixiseng.detail_attempts,1)
})
test('closed and failed fresh reads never fall back to saved JD and consume detail budget',async t=>{
  const {output,visits}=await fixture(t,{collection_mode:'hydrate',candidates:[entry('failed'),entry('closed'),entry('pending')],detail_cache:[cacheEntry('failed'),cacheEntry('closed')],max_jobs:2},
    {goto:async url=>{if(url.endsWith('failed'))throw new Error('fixture timeout')},body:url=>url.endsWith('closed')?'该职位已下线':'岗位职责'})
  assert.equal(visits.length,2);assert.equal(output.receipts[0].status,'failed')
  assert.deepEqual(output.candidates.map(v=>v.detail_state),['failed','closed','pending'])
  assert.equal(output.items.length,0)
  assert.equal(output.platform_state.shixiseng.closed,1)
  assert.equal(output.platform_state.shixiseng.closed_jobs[0].url,canonical('closed'))
  assert.equal(output.platform_state.shixiseng.closed_jobs[0].checked_at,output.candidates[1].last_detail_at)
  assert.equal(output.candidates[0].last_detail_at,undefined)
  assert.match(output.platform_state.shixiseng.message,/已关闭 1 条/)
})
test('unknown city is retained for review and never guessed from search context',async t=>{
  const candidate={...entry('unknown'),city:''}
  const {output}=await fixture(t,{collection_mode:'hydrate',candidates:[candidate],max_jobs:1},{fields:()=>({iname:'AI 产品实习',cname:'样例企业',info:'完整职责'}),
    lists:()=>[],body:()=> '岗位职责',detail:()=>({title:'AI 产品实习',company:'样例企业',raw:'完整职责'})})
  assert.equal(output.items[0].city,'');assert.equal(output.items[0].city_unknown,true);assert.equal(output.candidates[0].detail_state,'incomplete')
  assert.equal(output.items.length,1)
})
test('stop during polite wait does not spend a navigation or lose pending candidates',async t=>{
  let config
  const directory=await mkdtemp(path.join(os.tmpdir(),'radar-stop-'));t.after(()=>rm(directory,{recursive:true,force:true}))
  config={task_id:'b'.repeat(32),preferences:{collection_mode:'hydrate',candidates:[entry('new')],max_jobs:1},outputPath:path.join(directory,'result.json'),stopPath:path.join(directory,'stop')}
  let visits=0
  const output=await run(config,{openBrowser:async()=>({contexts:()=>[{newPage:async()=>({goto:async()=>visits++,close:async()=>{}})}],close:async()=>{}}),pause:async()=>writeFile(config.stopPath,'stop')})
  assert.equal(visits,0);assert.equal(output.receipts.length,0);assert.equal(output.status,'stopped');assert.equal(output.candidates[0].detail_state,'pending')
})
test('list cap leaves bounded discoveries and zero further list visits',async t=>{
  const {output,visits}=await fixture(t,{collection_mode:'discover',keywords:['AI','Agent'],cities:['北京'],max_list_attempts:1})
  assert.equal(visits.length,1);assert.equal(output.platform_state.shixiseng.reason_code,'list_limit')
})
test('stage config rejects arbitrary targets, excessive candidates, and unknown modes',()=>{
  assert.throws(()=>validatePreferences({collection_mode:'other'}))
  assert.throws(()=>validatePreferences({collection_mode:'hydrate',candidates:[{...entry('a'),url:'https://evil.test'}]}))
  assert.throws(()=>validatePreferences({collection_mode:'hydrate',candidates:Array.from({length:401},()=>entry('a'))}))
  assert.throws(()=>validatePreferences({collection_mode:'hydrate',max_list_attempts:13}))
  const candidates=[];mergeCandidate(candidates,entry('same'),hit());mergeCandidate(candidates,entry('same'),hit());assert.equal(candidates[0].hits.length,1)
})
test('time budget stops before browser launch and retains seeded observations',async t=>{
  const candidates=[{...entry('same'),observed_at:'2026-10-01T12:00:00.000Z'}]
  const {output,visits,opened}=await fixture(t,{collection_mode:'hydrate',candidates,max_list_attempts:0,deadline_at:Date.now()/1000-1})
  assert.equal(opened,0);assert.equal(visits.length,0);assert.equal(output.status,'stopped')
  assert.equal(output.platform_state.shixiseng.reason_code,'time_budget');assert.equal(output.candidates[0].observed_at,candidates[0].observed_at)
  assert.equal(output.receipts.length,0);assert.equal(output.candidates[0].detail_state,'pending')
})
