"""Exercise the first-page search controller, file handling and visible task states."""
import json

from fe_helpers import HARNESS, run_node


SETUP = """
const fe=require(HARNESS_PATH);
const assert=require('node:assert/strict');
const crypto=require('node:crypto').webcrypto;
function boot(existingStore) {
 const store=existingStore||new Map(), nodes=new Map(), calls=[], timers=[];
 const ctx=fe.boot({assert,URL,crypto,TextDecoder,
  setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},clearTimeout(){},
  document:{visibilityState:'visible',addEventListener(){},
   querySelector(selector){return nodes.get(selector)||null;},querySelectorAll(){return[];},
   body:{classList:{add(){},remove(){},toggle(){return false;},contains(){return false;}},append(){}}},
  window:{matchMedia(){return{matches:false,addEventListener(){}};}},
  localStorage:{getItem(key){return store.get(key)||null;},setItem(key,value){store.set(key,value);}},
  fetch:async (route,options={})=>{
   calls.push({route,options,body:options.body?JSON.parse(options.body):null});
   return {ok:true,json:async()=>({search_run:null,batch:null,items:[],has_more:false,error:''})};
  }
 });
 fe.loadCore(ctx);
 fe.publish(ctx,'homeSearch','parseHomeKeywordFile','normalizeHomeKeywords','homeSearchPayload',
  'importHomeKeywordFile','homeSearchPanel','homeSearchProgress','startHomeSearch',
  'refreshHomeSearchCurrent','controlHomeSearch','homeSearchNeedsRefresh','adoptHomeSearchCurrent',
  'homeSearchRequestId','saveHomeSearchDraft','changeHomeSearchField','state','overview',
  'addHomeSearchCities','toggleHomeSearchCity','homeSearchScopeText','homeSearchCountText','homeSearchBudget',
  'homeSearchCityTable','loadHomeKeywordLibrary','homeKeywordLibraryWords','homeKeywordLibraryMarkup','useHomeSearchConditions');
 ctx.state.loaded=true;
 return {ctx,store,nodes,calls,timers};
}
function response(value){return {ok:true,json:async()=>value};}
function snapshot(status='running',id=9,extra={}){
 return {request_id:'',batch:{id:4},items:[],has_more:false,error:'',
  search_run:{id,batch_id:4,platform:'boss',status,total:0,synced_count:0,
   preferences:{keywords:['AI 产品'],cities:['北京']},platform_state:{}},...extra};
}
const ready={ready:{ok:true,messages:[]},warnings:[],
 preferences:{keywords:['AI 产品'],cities:['北京']},
 summary:{keyword_count:1,city:'北京',platform_label:'BOSS 直聘',list_pages:1,max_jobs:30}};
"""


def check(body):
    script = SETUP.replace("HARNESS_PATH", json.dumps(str(HARNESS)))
    run_node(script + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exitCode=1;});")


def test_keyword_files_preserve_phrases_and_validate_the_entire_file():
    check(r'''
const {ctx}=boot();
const parsed=ctx.parseHomeKeywordFile('\ufeffkeyword\r\nAI 产品\r\nAgent  产品\r\nai 产品\r\n"数据,分析"\r\n"AI ""产品"""','keywords.csv');
assert.equal(JSON.stringify(parsed.keywords),JSON.stringify(['AI 产品','Agent  产品','数据,分析','AI "产品"']));
assert.equal(parsed.duplicates,1);
const txt=ctx.parseHomeKeywordFile('AI 产品\n\nAgent 产品\nAI 产品','words.TXT');
assert.equal(txt.duplicates,1);assert.equal(txt.blanks,1);
assert.throws(()=>ctx.parseHomeKeywordFile('岗位名称\nAI 产品','words.csv'),/keyword/);
assert.throws(()=>ctx.parseHomeKeywordFile('keyword\nAI,北京','words.csv'),/第 2 行/);
assert.throws(()=>ctx.parseHomeKeywordFile('keyword\n"AI 产品','words.csv'),/第 2 行/);
assert.throws(()=>ctx.parseHomeKeywordFile('keyword\n'+('甲'.repeat(81)),'words.csv'),/第 2 行/);
assert.throws(()=>ctx.parseHomeKeywordFile('keyword\nAI\t产品','words.csv'),/无效/);
assert.throws(()=>ctx.parseHomeKeywordFile(Array.from({length:10},(_,n)=>'关键词'+n).join('\n'),'words.txt'),/最多 9/);
assert.throws(()=>ctx.parseHomeKeywordFile('keyword','words.csv'),/没有可用/);
assert.throws(()=>ctx.parseHomeKeywordFile('AI 产品','words.xlsx'),/CSV/);
// UTF-16 surrogate pairs do not count as two characters in the length limit.
assert.equal(ctx.parseHomeKeywordFile('𠮷'.repeat(80),'words.txt').keywords.length,1);
''')


def test_native_capture_results_keep_resume_and_legacy_controls_separate():
    check(r'''
const {ctx}=boot();
fe.publish(ctx,'captureNativeBoss','captureResumable','bossSearchPanel','capturePage');
const batch={id:4,platform:'boss',label:'本次检索',keyword:'AI 产品',city:'北京',status:'open',remaining:0,counts:{imported:0}};
const run=snapshot().search_run;run.preferences.connector='native';run.platform_state={phase:'login_required'};
ctx.state.captureSelected=4;ctx.state.captureBatches=[batch];ctx.state.captureItems=[];
ctx.state.bossConnector={batch_id:4,connector:'native',connected:true,search_run:run,capabilities:{matched_results:true}};
assert.equal(ctx.captureNativeBoss(),true);assert.equal(ctx.captureResumable(),true);
assert.match(ctx.bossSearchPanel(batch),/data-bh-panel hidden/);
assert.ok(!ctx.capturePage().includes('data-bh-import="1"'),'native batch must not offer BossHunter history import');
run.status='stopped';
assert.equal(ctx.captureResumable(),false,'a stopped task cannot resume even if the last phase was login');
assert.match(ctx.bossSearchPanel(batch),/data-bh-resume="1" hidden/);
delete run.preferences.connector;delete ctx.state.bossConnector.connector;
assert.equal(ctx.captureNativeBoss(),false);
assert.ok(ctx.capturePage().includes('data-bh-import="1"'),'legacy batches retain their own history import');
''')


def test_consumed_but_rejected_results_do_not_cause_endless_terminal_polling():
    check(r'''
const {ctx,calls}=boot();
const completed=snapshot('completed');
Object.assign(completed.search_run,{total:2,sync_offset:2,synced_count:1});
ctx.adoptHomeSearchCurrent(completed);
assert.equal(ctx.homeSearchNeedsRefresh(),false,'consumed invalid items must not keep the finished task polling');
await ctx.refreshHomeSearchCurrent(false);
assert.equal(calls.length,0);
completed.search_run.sync_offset=1;
assert.equal(ctx.homeSearchNeedsRefresh(),true,'unconsumed items must still sync');
''')


def test_import_fills_the_form_without_starting_and_bad_files_keep_input():
    check(r'''
const {ctx,calls}=boot();ctx.homeSearch.form.keywords='保留这条关键词';
const file=(name,text)=>({name,size:Buffer.byteLength(text),arrayBuffer:async()=>new TextEncoder().encode(text).buffer});
await ctx.importHomeKeywordFile(file('broken.csv','wrong\n替换内容'));
assert.equal(ctx.homeSearch.form.keywords,'保留这条关键词');
assert.match(ctx.homeSearch.error,/keyword/);
await ctx.importHomeKeywordFile(file('jobs.txt','AI 产品\nAgent 产品\nAI 产品'));
assert.equal(ctx.homeSearch.form.keywords,'AI 产品\nAgent 产品');
assert.match(ctx.homeSearch.importNote,/合并 1 行/);assert.match(ctx.homeSearch.importNote,/尚未开始/);
assert.equal(calls.length,0,'import must never send a search request');
const before=ctx.homeSearch.form.keywords;
await ctx.importHomeKeywordFile({name:'large.txt',size:65537,arrayBuffer:async()=>{throw Error('must not read');}});
assert.equal(ctx.homeSearch.form.keywords,before);assert.match(ctx.homeSearch.error,/64 KB/);
await ctx.importHomeKeywordFile({name:'bad.txt',size:2,arrayBuffer:async()=>Uint8Array.of(0xff,0xff).buffer});
assert.equal(ctx.homeSearch.form.keywords,before);assert.match(ctx.homeSearch.error,/UTF-8/);
''')


def test_start_is_one_action_and_uses_server_validation_before_execution():
    check(r'''
const {ctx,calls}=boot();ctx.homeSearch.form.keywords='AI 产品';
const final=snapshot('completed');
ctx.fetch=async(route,options={})=>{
 const body=options.body?JSON.parse(options.body):null;calls.push({route,body});
 if(route==='/api/job-search/preview')return response(ready);
 if(route==='/api/job-search/start')return response({batch_id:4,request_id:body.request_id,search_run:snapshot().search_run});
 if(route==='/api/job-search/9/refresh')return response(final);
 throw Error('Unexpected '+route);
};
await ctx.startHomeSearch();await ctx.refreshHomeSearchCurrent(false);
assert.deepEqual(calls.map(x=>x.route),['/api/job-search/preview','/api/job-search/start','/api/job-search/9/refresh']);
assert.equal(calls[1].body.confirmed,true);
assert.equal(calls[0].body.browser_mode,'managed');
assert.equal(calls[1].body.browser_mode,'managed');
assert.match(calls[1].body.request_id,/^[a-f0-9-]{36}$/);
assert.equal(ctx.homeSearch.current.search_run.status,'completed');
assert.equal(ctx.homeSearch.attempt,null);
assert.equal(ctx.homeSearchNeedsRefresh(),false);
assert.equal(ctx.homeSearch.preview,null);
assert.ok(!ctx.homeSearchPanel().includes('确认并开始'),'no second user confirmation');
''')


def test_browser_mode_is_explicit_persistent_and_scoped_to_boss():
    check(r'''
const {ctx,store,nodes}=boot();ctx.homeSearch.form.keywords='AI 产品';
const choice={hidden:false},help={innerHTML:''};
nodes.set('#home-search-browser-choice',choice);nodes.set('#home-search-browser-help',help);
assert.equal(ctx.homeSearch.form.browser_mode,'managed');
assert.equal(ctx.homeSearchPayload().browser_mode,'managed');
const pending=ctx.homeSearchRequestId(ctx.homeSearchPayload());
const restored=boot(store).ctx;
assert.equal(restored.homeSearch.form.browser_mode,'managed');
assert.equal(restored.homeSearchRequestId(restored.homeSearchPayload()),pending);
ctx.changeHomeSearchField({name:'browser_mode',value:'chrome_use'});
assert.equal(ctx.homeSearchPayload().browser_mode,'chrome_use');
assert.match(help.innerHTML,/连接说明/);
assert.notEqual(ctx.homeSearchRequestId(ctx.homeSearchPayload()),pending,'different mode is a different search');
assert.equal(boot(store).ctx.homeSearch.form.browser_mode,'chrome_use');
ctx.changeHomeSearchField({name:'platform',value:'shixiseng'});
assert.equal(choice.hidden,true);
assert.equal(Object.hasOwn(ctx.homeSearchPayload(),'browser_mode'),false,'non-BOSS requests omit the browser mode');
ctx.changeHomeSearchField({name:'platform',value:'boss'});
assert.equal(choice.hidden,false);assert.equal(ctx.homeSearchPayload().browser_mode,'chrome_use');
ctx.homeSearch.form.browser_mode='unknown';
assert.throws(()=>ctx.homeSearchPayload(),/当前 Chrome/);
''')


def test_input_errors_and_unready_browser_never_start_collection():
    check(r'''
const {ctx,calls}=boot();
await ctx.startHomeSearch();assert.equal(calls.length,0);assert.match(ctx.homeSearch.error,/关键词/);
ctx.homeSearch.form.keywords='一\n二\n三\n四\n五';ctx.homeSearch.form.max_pages=3;
await ctx.startHomeSearch();assert.equal(calls.length,0);assert.match(ctx.homeSearch.error,/12 页/);
ctx.homeSearch.form={keywords:'AI 产品',platform:'boss',browser_mode:'chrome_use',cities:['北京'],max_pages:1,max_jobs:30};
for(const city of ['城'.repeat(61),'北\t京','北\u0000京','北\u001f京','北\u007f京']){
 ctx.homeSearch.form.cities=[city];
 await ctx.startHomeSearch();assert.equal(calls.length,0);assert.match(ctx.homeSearch.error,/城市/);
}
ctx.homeSearch.form.cities=[];
await ctx.startHomeSearch();assert.equal(calls.length,0);assert.match(ctx.homeSearch.error,/至少选择或填写一个城市/);
ctx.homeSearch.form.cities=['城'.repeat(60)];
assert.equal(ctx.homeSearchPayload().cities[0].length,60);
assert.match(ctx.homeSearchPanel(),/id="home-search-city-input"[^>]*maxlength="60"/);
ctx.homeSearch.form.cities=['北京'];
ctx.fetch=async(route,options)=>{calls.push({route,body:JSON.parse(options.body)});return response({...ready,ready:{ok:false,messages:['请安装 Chrome Use 并完成扩展连接。']}});};
await ctx.startHomeSearch();
assert.equal(calls.length,1);assert.equal(calls[0].route,'/api/job-search/preview');
assert.equal(ctx.homeSearch.form.keywords,'AI 产品');
assert.match(ctx.homeSearchPanel(),/请安装 Chrome Use 并完成扩展连接/);
assert.match(ctx.homeSearchPanel(),/重新检查并检索/);
assert.equal(calls[0].body.browser_mode,'chrome_use');
assert.equal(ctx.homeSearch.form.browser_mode,'chrome_use','connection failure must not silently select managed mode');
assert.match(ctx.homeSearchPanel(),/data-home-search="connection"/);
assert.match(ctx.homeSearchPanel(),/https:\/\/chromewebstore.google.com\/detail\/chrome-use\/knfcmbamhjmaonkfnjhldjedeobeafmk/);
assert.equal(ctx.homeSearch.busy,'');
''')


def test_lost_start_response_keeps_request_identity_across_retry_and_reload():
    check(r'''
const {ctx,calls,store}=boot();ctx.homeSearch.form.keywords='AI 产品';let starts=0;
ctx.fetch=async(route,options={})=>{
 const body=options.body?JSON.parse(options.body):null;calls.push({route,body});
 if(route==='/api/job-search/preview')return response(ready);
 if(route==='/api/job-search/current')return response({search_run:null,batch:null,items:[],error:''});
 if(route==='/api/job-search/start'){starts++;throw Error('connection lost');}
 throw Error('Unexpected '+route);
};
await ctx.startHomeSearch();await ctx.refreshHomeSearchCurrent();
const first=calls.find(x=>x.route==='/api/job-search/start').body;
assert.equal(starts,1);assert.ok(ctx.homeSearch.attempt);assert.equal(ctx.homeSearch.form.keywords,'AI 产品');
const reloaded=boot(store).ctx;
assert.equal(reloaded.homeSearch.attempt.id,first.request_id);
const retry=[];
reloaded.fetch=async(route,options={})=>{
 const body=options.body?JSON.parse(options.body):null;retry.push({route,body});
 if(route==='/api/job-search/preview')return response(ready);
 if(route==='/api/job-search/start')return response({batch_id:4,request_id:body.request_id,search_run:snapshot('completed').search_run});
 throw Error('Unexpected '+route);
};
await reloaded.startHomeSearch();
assert.equal(retry.find(x=>x.route==='/api/job-search/start').body.request_id,first.request_id);
assert.equal(reloaded.homeSearch.attempt,null);
// After a known terminal result, a deliberate new search gets a new identity.
await reloaded.startHomeSearch();
const ids=retry.filter(x=>x.route==='/api/job-search/start').map(x=>x.body.request_id);
assert.notEqual(ids[0],ids[1]);
''')


def test_terminal_start_replay_preserves_saved_results_and_refreshes_remaining_errors():
    check(r'''
for(const error of ['', '部分结果仍待保存']){
 const {ctx,calls}=boot();ctx.homeSearch.form.keywords='AI 产品';
 const pending=ctx.homeSearchRequestId(ctx.homeSearchPayload());
 const replay=snapshot('completed',9,{request_id:pending,batch_id:4,batch:{id:4,label:'已完成的检索'},
  items:[{id:1,title:'已保存岗位',company:'企业',city:'北京',review_status:'imported',jd_available:true}],has_more:true,error});
 replay.search_run.total=35;replay.search_run.synced_count=35;
 let finishRefresh;
 ctx.fetch=async(route,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({route,body});
  if(route==='/api/job-search/preview')return response(ready);
  if(route==='/api/job-search/start'){assert.equal(body.request_id,pending);return response(replay);}
  if(route==='/api/job-search/9/refresh')return await new Promise(resolve=>{finishRefresh=()=>resolve(response(replay));});
  throw Error('Unexpected '+route);
 };
 await ctx.startHomeSearch();
 assert.equal(ctx.homeSearch.current.batch.label,'已完成的检索');
 assert.equal(ctx.homeSearch.current.items[0].title,'已保存岗位');
 assert.equal(ctx.homeSearch.current.has_more,true);
 assert.equal(ctx.homeSearch.current.error,error);
 assert.equal(ctx.homeSearch.attempt,null);
 assert.match(ctx.homeSearchProgress(),/已保存岗位/);
 assert.equal(calls.filter(x=>x.route.endsWith('/refresh')).length,error?1:0);
 if(error){
  assert.match(ctx.homeSearchProgress(),/部分结果仍待保存/);
  const refresh=ctx.refreshHomeSearchCurrent(false);finishRefresh();await refresh;
 }
}
''')


def test_current_task_recovers_a_lost_response_and_auto_refresh_uses_post():
    check(r'''
const {ctx,calls}=boot();ctx.homeSearch.form.keywords='AI 产品';
const pending=ctx.homeSearchRequestId(ctx.homeSearchPayload());
ctx.homeSearch.error='启动响应丢失';
const found=snapshot('running',9,{request_id:pending});
const waiting=snapshot('running',9,{request_id:pending,items:[{id:1,title:'AI 产品',company:'企业',city:'北京',review_status:'pending',jd_available:true}]});
waiting.search_run.platform_state={phase:'login_required',message:'请在打开的浏览器中登录 BOSS。'};
waiting.search_run.total=1;waiting.search_run.synced_count=1;
ctx.fetch=async(route,options={})=>{calls.push({route,method:options.method||'GET'});
 if(route==='/api/job-search/current')return response(found);
 if(route==='/api/job-search/9/refresh')return response(waiting);
 throw Error('Unexpected '+route);
};
await ctx.refreshHomeSearchCurrent();
assert.deepEqual(calls,[{route:'/api/job-search/current',method:'GET'},{route:'/api/job-search/9/refresh',method:'POST'}]);
assert.equal(ctx.homeSearch.attempt,null);assert.equal(ctx.homeSearch.error,'');
assert.match(ctx.homeSearchProgress(),/我已完成登录，继续检索/);
assert.match(ctx.homeSearchProgress(),/已保存 <strong>1<\/strong>/);
assert.ok(ctx.homeSearchNeedsRefresh());
''')


def test_resume_and_stop_keep_results_and_terminal_state_stops_polling():
    check(r'''
const {ctx,calls}=boot();const waiting=snapshot();
waiting.search_run.platform_state={phase:'verification_required'};
waiting.search_run.total=1;waiting.search_run.synced_count=1;
waiting.items=[{id:1,title:'完整 JD 岗位',company:'企业',city:'北京',jd_available:true,review_status:'pending'}];
ctx.adoptHomeSearchCurrent(waiting);
assert.match(ctx.homeSearchProgress(),/我已完成验证，继续检索/);
ctx.fetch=async(route,options={})=>{calls.push({route,body:JSON.parse(options.body)});
 const next=JSON.parse(JSON.stringify(waiting));
 next.search_run.platform_state={phase:'details'};
 if(route.endsWith('/stop'))next.search_run.status='stopped';
 return response(next);
};
await ctx.controlHomeSearch('resume');
assert.equal(ctx.homeSearch.current.items.length,1);assert.equal(ctx.homeSearch.busy,'');
await ctx.controlHomeSearch('stop');
assert.deepEqual(calls.map(x=>x.route),['/api/job-search/9/resume','/api/job-search/9/stop']);
assert.ok(calls.every(x=>x.body.confirmed===true));
assert.match(ctx.homeSearchProgress(),/已停止/);assert.match(ctx.homeSearchProgress(),/完整 JD 岗位/);
assert.equal(ctx.homeSearchNeedsRefresh(),false);
const stopped=JSON.parse(JSON.stringify(waiting));stopped.search_run.status='stopped';
ctx.adoptHomeSearchCurrent(stopped);
assert.match(ctx.homeSearchProgress(),/已停止/);
assert.ok(!ctx.homeSearchProgress().includes('我已完成验证'),'terminal task must not ask for a challenge again');
''')


def test_chrome_use_handoff_and_cleanup_keep_results_visible():
    check(r'''
const {ctx}=boot();const current=snapshot();
current.search_run.preferences.browser_mode='chrome_use';
current.search_run.platform_state={boss:{browser_mode:'chrome_use',phase:'verification_required',tabs_opened:2}};
current.items=[{id:1,title:'已保存的岗位',jd_available:true,review_status:'pending'}];
ctx.adoptHomeSearchCurrent(current);
assert.match(ctx.homeSearchProgress(),/Chrome 中的“择木”任务标签/);
assert.match(ctx.homeSearchProgress(),/我已完成验证，继续检索/);
assert.match(ctx.homeSearchProgress(),/已保存的岗位/);
current.search_run.status='stopped';
Object.assign(current.search_run.platform_state.boss,{cleanup_status:'incomplete',cleanup_message:'<img src=x> 部分任务标签未能关闭'});
const stopped=ctx.homeSearchProgress();
assert.match(stopped,/已保存的岗位/);assert.match(stopped,/部分任务标签未能关闭/);
assert.ok(!stopped.includes('<img'));assert.ok(!stopped.includes('我已完成验证'));
Object.assign(current.search_run.platform_state.boss,{cleanup_status:'complete',cleanup_message:'',tabs_closed:2});
assert.match(ctx.homeSearchProgress(),/本次任务标签已关闭/);
''')


def test_terminal_cleanup_refreshes_until_finished_but_has_a_deadline():
    check(r'''
const {ctx,calls}=boot();const current=snapshot('completed');
current.search_run.preferences.browser_mode='chrome_use';
current.search_run.platform_state={browser_mode:'chrome_use',cleanup_status:'pending'};
current.items=[{id:1,title:'清理时保留的岗位',jd_available:true}];
ctx.adoptHomeSearchCurrent(current);
assert.equal(ctx.homeSearchNeedsRefresh(),true);
assert.match(ctx.homeSearchProgress(),/正在关闭本次任务标签/);
ctx.fetch=async(route)=>{
 calls.push(route);current.search_run.platform_state.cleanup_status='complete';return response(current);
};
await ctx.refreshHomeSearchCurrent(false);
assert.deepEqual(calls,['/api/job-search/9/refresh']);
assert.equal(ctx.homeSearchNeedsRefresh(),false);
assert.match(ctx.homeSearchProgress(),/本次任务标签已关闭/);
// A disconnected runner must not keep a terminal task polling forever.
current.search_run.platform_state={boss:{browser_mode:'chrome_use',cleanup_status:'pending',cleanup_message:'正在关闭标签'}};
current.search_run.status='failed';ctx.adoptHomeSearchCurrent(current);
ctx.homeSearch.cleanupWait.deadline=Date.now()-1;
assert.equal(ctx.homeSearchNeedsRefresh(),false);
calls.length=0;await ctx.refreshHomeSearchCurrent(false);
assert.equal(calls.length,0);
assert.match(ctx.homeSearchProgress(),/未能确认本次任务标签已关闭/);
assert.match(ctx.homeSearchProgress(),/清理时保留的岗位/);
''')


def test_double_click_and_old_poll_response_cannot_start_twice_or_replace_new_task():
    check(r'''
const {ctx,calls}=boot();ctx.homeSearch.form.keywords='AI 产品';let finishRead,finishPreview;
ctx.fetch=async(route,options={})=>{calls.push(route);
 if(route==='/api/job-search/current')return await new Promise(resolve=>{finishRead=()=>resolve(response({search_run:null,items:[],error:''}));});
 if(route==='/api/job-search/preview')return await new Promise(resolve=>{finishPreview=()=>resolve(response(ready));});
 if(route==='/api/job-search/start')return response({batch_id:4,search_run:snapshot('running',22).search_run});
 throw Error('Unexpected '+route);
};
const oldPoll=ctx.refreshHomeSearchCurrent();
const start=ctx.startHomeSearch();await ctx.startHomeSearch();
assert.equal(calls.filter(x=>x==='/api/job-search/preview').length,1);
finishPreview();await start;finishRead();await oldPoll;
assert.equal(calls.filter(x=>x==='/api/job-search/start').length,1);
assert.equal(ctx.homeSearch.current.search_run.id,22,'an earlier empty response must not erase a new run');
''')


def test_first_page_and_results_render_without_executable_imported_markup():
    check(r'''
const {ctx}=boot();ctx.homeSearch.form.keywords='<script>alert(1)</script>';
const html=ctx.overview();
assert.ok(html.indexOf('id="home-search-form"')<html.indexOf('收录岗位'),'search must precede statistics');
assert.match(html,/开始检索/);assert.match(html,/下载关键词 CSV 模板/);
assert.ok(!html.includes('<script>alert(1)</script>'));assert.match(html,/&lt;script&gt;/);
const current=snapshot('completed');current.search_run.total=1;current.search_run.synced_count=1;
current.items=[{id:1,title:'<img src=x onerror=alert(1)>',company:'<b>企业</b>',city:'北京',source_url:'javascript:alert(1)',review_status:'pending',jd_available:true}];
ctx.adoptHomeSearchCurrent(current);
const result=ctx.homeSearchProgress();
assert.ok(!result.includes('<img'));assert.ok(!result.includes('href="javascript:'));
assert.match(result,/核对本次结果/);assert.match(result,/检索完成/);
assert.ok(!result.includes('undefined'));
''')


def test_result_labels_match_capture_review_states():
    check(r'''
const {ctx}=boot();
const labels={pending:'待核对',review:'待重复审核',imported:'已入库',accepted:'已入库',
 deferred:'暂时跳过',dismissed:'不感兴趣',duplicate:'已有同一来源'};
const current=snapshot('completed');current.search_run.total=7;current.search_run.synced_count=7;
current.items=Object.keys(labels).map((review_status,id)=>({id,title:'岗位 '+id,review_status,jd_available:true}));
ctx.adoptHomeSearchCurrent(current);
const rendered=[...ctx.homeSearchProgress().matchAll(/class="home-search-result-state">([^<]+)</g)].map(match=>match[1]);
assert.deepEqual(rendered,Object.values(labels));
''')


def test_csv_template_has_a_utf8_bom_and_matches_the_import_contract():
    template = HARNESS.parents[2] / "static" / "templates" / "job-keywords.csv"
    data = template.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    check("const {ctx}=boot(); const result=ctx.parseHomeKeywordFile(" +
          json.dumps(data.decode("utf-8")) + ", 'job-keywords.csv');" +
          "assert.equal(result.keywords.length,3); assert.equal(result.errors.length,0);")


def test_multiple_cities_keyword_budget_and_old_drafts():
    check(r'''
const same=(a,b,m)=>assert.equal(JSON.stringify(a),JSON.stringify(b),m);
const {ctx,store}=boot();
ctx.homeSearch.form.keywords='AI 产品，Agent 产品\n模型评测';
same(ctx.homeSearchPayload().keywords,['AI 产品','Agent 产品','模型评测'],'commas also separate keywords');
same(ctx.homeSearchPayload().cities,['北京']);
assert.equal(Object.hasOwn(ctx.homeSearchPayload(),'city'),false);
ctx.addHomeSearchCities('上海、 深圳 ,上海');
same(ctx.homeSearch.form.cities,['北京','上海','深圳'],'pasted lists are split and deduplicated');
same(ctx.homeSearchPayload().cities,['北京','上海','深圳']);
assert.match(ctx.homeSearchScopeText(),/3 个关键词 × 3 个城市 × 每词 1 页 = 9 \/ 12 页/);
assert.match(ctx.homeSearchCountText(),/已填 3 个.*最多 3 个/);
ctx.toggleHomeSearchCity('杭州');
assert.match(ctx.homeSearchBudget().over,/9 组/);
assert.throws(()=>ctx.homeSearchPayload(),/9 组/);
ctx.toggleHomeSearchCity('杭州');ctx.toggleHomeSearchCity('深圳');
ctx.homeSearch.form.max_pages=3;
assert.throws(()=>ctx.homeSearchPayload(),/12 页/);
ctx.homeSearch.form.max_pages=1;
same(ctx.homeSearch.form.cities,['北京','上海']);
const panel=ctx.homeSearchPanel();
assert.match(panel,/data-home-search="toggle-city" data-city="北京" aria-pressed="true"/);
assert.match(panel,/data-home-search="toggle-city" data-city="杭州" aria-pressed="false"/);
assert.match(panel,/每行一个，或用逗号分隔/);
assert.match(panel,/从词库选/);assert.match(panel,/更多设置/);
ctx.addHomeSearchCities('喀什地区');
assert.match(ctx.homeSearchPanel(),/移除城市 喀什地区/);
assert.throws(()=>ctx.addHomeSearchCities('a,b,c,d,e,f,g,h'),/最多选择 9 个城市/);
same(boot(store).ctx.homeSearch.form.cities,['北京','上海','喀什地区'],'cities survive reload');
const legacy=new Map([['radar.home-search.v1',JSON.stringify({form:{keywords:'AI',city:'上海'}})]]);
same(boot(legacy).ctx.homeSearch.form.cities,['上海'],'older single-city drafts are kept');
ctx.homeSearchCityTable.boss=['北京','上海'];ctx.homeSearchCityTable.loaded=true;
assert.throws(()=>ctx.homeSearchPayload(),/BOSS 暂不支持「喀什地区」/);
ctx.changeHomeSearchField({name:'platform',value:'shixiseng'});
same(ctx.homeSearchPayload().cities,['北京','上海','喀什地区'],'other platforms accept typed cities');
''')


def test_keyword_library_fills_representatives_within_the_budget():
    check(r'''
const same=(a,b,m)=>assert.equal(JSON.stringify(a),JSON.stringify(b),m);
const {ctx}=boot();
const families=[{id:'B01',route:'Agent',name:'Agent 平台产品',priority:'P0',representative:'Agent 产品经理',aliases:['智能体产品经理']},
 {id:'B02',route:'Agent',name:'AI 应用开发',representative:'AI 应用开发',aliases:[]},{id:'X',route:'停用',representative:'停用词',enabled:false}];
const routes=[];
ctx.fetch=async route=>{routes.push(route);
 if(route==='/api/planner/library')return {ok:true,json:async()=>({families:[]})};
 if(route==='/static/templates/ai-keywords-v1.json')return {ok:true,json:async()=>({families})};
 throw Error('Unexpected '+route);};
const library=await ctx.loadHomeKeywordLibrary();
same(routes,['/api/planner/library','/static/templates/ai-keywords-v1.json'],'empty library falls back to the bundled template, read-only');
assert.match(library.source,/内置 AI 方向示例词库/);
same(ctx.homeKeywordLibraryWords(['B01'],false,library),['Agent 产品经理']);
same(ctx.homeKeywordLibraryWords(['B01','B02'],true,library),['Agent 产品经理','智能体产品经理','AI 应用开发']);
const markup=ctx.homeKeywordLibraryMarkup(library);
assert.match(markup,/<legend>Agent<\/legend>/);assert.match(markup,/data-modal-transient/);
ctx.useHomeSearchConditions({keywords:['Agent 产品经理'],cities:['上海'],platform:'shixiseng'});
assert.equal(ctx.homeSearch.form.keywords,'Agent 产品经理');
same(ctx.homeSearch.form.cities,['上海']);assert.equal(ctx.homeSearch.form.platform,'shixiseng');
assert.match(ctx.homeSearch.importNote,/规划/);
''')
