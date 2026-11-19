/* Collector HTTP is mocked. Intake and commit use a temporary database only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-shixiseng-ui-'));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function run(){
 const port=await freePort(),origin=`http://127.0.0.1:${port}`,python=process.env.RADAR_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.db')},stdio:['ignore','ignore','pipe']});
 let log='',browser,page;server.stderr.on('data',b=>log+=b);
 try{
  let ready=false;for(let n=0;n<60;n++){try{ready=(await fetch(origin+'/api/health')).ok}catch{}if(ready)break;await wait(150)}assert(ready,log);
  browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1320,height:840}});page.setDefaultTimeout(10000);
  const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
  const preferences={keywords:['AI 产品 实习'],cities:['北京','上海'],platform_order:['shixiseng'],max_pages:{shixiseng:2},allow_internship:true};
  let searchRun=null,offline=false,previewError=false,batchId,runItems=[];
  const closedJob={url:'https://www.shixiseng.com/intern/inn_closedfixture',title:'演示已关闭岗位',company:'演示企业',checked_at:'2026-11-16T02:00:00Z'};
  let delayStatus=false,statusHeld,releaseStatus;const held=new Promise(r=>statusHeld=r);
  const json=async(route,body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  await page.route('**/api/job-search/current',route=>json(route,{search_run:{id:999,platform:'shixiseng',status:'completed',synced_count:0,preferences,platform_state:{closed:1,closed_jobs:[closedJob],message:'本次重新读取，来源提示已关闭 1 条'}},items:[]}));
  await page.route('**/api/capture/connectors/**',async route=>{
   const req=route.request(),u=new URL(req.url()),endpoint=u.pathname.split('/').pop(),connector=u.pathname.split('/').at(-2),payload=req.method()==='POST'?req.postDataJSON():null;
   calls.push({connector,endpoint,payload,query:u.search});
   if(endpoint==='status'){
    const body={batch_id:Number(u.searchParams.get('batch_id')),connected:!offline,capabilities:{matched_results:true},readiness:{ok:!offline,checks:[{status:offline?'fail':'pass',message:offline?'请安装 Node.js 并配置浏览器':'演示浏览器已就绪'}]},preferences,task:null,search_run:connector==='shixiseng'&&searchRun?.batch_id===Number(u.searchParams.get('batch_id'))?searchRun:null};
    if(delayStatus&&connector==='shixiseng'){delayStatus=false;statusHeld();await new Promise(r=>releaseStatus=r)}
    return json(route,body);
   }
   assert.equal(connector,'shixiseng','Only the selected platform may start collection');
   if(endpoint==='search-preview')return previewError?json(route,{detail:'演示检查：浏览器准备失败'},503):json(route,{preferences,max_jobs:60,requires_confirmation:true,internship_only:true});
   if(endpoint==='start-collect'){searchRun={id:201,platform:'shixiseng',batch_id:batchId,task_id:'shixi-demo-task',status:'running',preferences,internship_only:true,total:0,synced_count:0,platform_state:{phase:'details',page:2,seen:2,message:'演示：已读取岗位详情',closed:1,closed_jobs:[closedJob]}};return json(route,{started:true,search_run:searchRun})}
   throw Error('Unexpected connector '+endpoint);
  });
  await page.route('**/api/capture/search-runs/**',async route=>{
   const req=route.request(),endpoint=new URL(req.url()).pathname.split('/').pop(),payload=req.postDataJSON();calls.push({endpoint,payload});
   if(endpoint==='stop'){searchRun={...searchRun,status:'stopped',total:2};return json(route,{search_run:searchRun})}
   if(endpoint==='sync'){searchRun={...searchRun,synced_count:2,sync_offset:2};return json(route,{created:2,scanned:2,next_offset:2,has_more:false,search_run:searchRun})}
   if(endpoint==='discovery')return json(route,{costs:{list_attempts:2,detail_attempts:2,cache_hits:0},costs_reported:true,candidates:[],pending_count:0});
   throw Error('Unexpected run request '+endpoint);
  });
  await page.route(url=>url.pathname.startsWith('/api/capture/batches'),async route=>{
   const req=route.request(),u=new URL(req.url());if(req.method()!=='GET')return route.continue();
   if(/\/items$/.test(u.pathname)){
    calls.push({endpoint:'items',query:u.search});const response=await route.fetch({url:origin+u.pathname});let items=await response.json();
    if(u.searchParams.get('run_id'))items=items.filter(i=>runItems.includes(i.id));return json(route,items);
   }
   const response=await route.fetch();const rows=await response.json();return json(route,rows.map(b=>({...b,latest_search_run_id:searchRun?.batch_id===b.id?searchRun.id:null})));
  });
  const backend=async(url,data)=>{const response=await fetch(origin+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert(response.ok,await response.clone().text());return response.json()};
  await page.goto(origin);await page.locator('#home-search-title').waitFor();
  assert.equal(await page.title(),'择木 · 实习工作台');
  for(const [name,width,height] of [['desktop',1320,840],['compact',980,650]]){
   await page.setViewportSize({width,height});
   const closed=page.locator('.home-search-progress details').filter({has:page.locator('summary').filter({hasText:'来源提示已关闭'})});
   await closed.locator('summary').click();assert(await closed.getByText(closedJob.title,{exact:true}).isVisible());
   assert.match(await page.locator('.home-search-saved').textContent(),/已关闭 1 条/);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Home overflow at '+width);
   if(process.env.RADAR_SHIXISENG_SCREENSHOTS){fs.mkdirSync(process.env.RADAR_SHIXISENG_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.RADAR_SHIXISENG_SCREENSHOTS,'closed-home-'+name+'.png'),animations:'disabled'})}
   await closed.locator('summary').click();
  }
  await page.setViewportSize({width:1320,height:840});await page.locator('[data-page=capture]').first().click();
  await page.locator('#capture-batch-form [name=platform]').selectOption('shixiseng');
  assert.equal(await page.locator('[data-capture-search-new]').textContent(),'检索 实习僧 岗位');assert(await page.locator('[data-capture-search-new]').isVisible());
  await page.locator('#capture-batch-form [name=keyword]').fill('AI 产品 实习');await page.locator('[data-capture-city=上海]').click();assert.equal(await page.locator('#capture-batch-form [name=city]').inputValue(),'北京，上海');
  await page.locator('[data-capture-search-new]').click();await page.locator('#boss-collect-form').waitFor();
  batchId=(await (await fetch(origin+'/api/capture/batches')).json())[0].id;
  assert.equal(await page.locator('#boss-collect-form').getAttribute('data-connector'),'shixiseng');assert.match(await page.locator('#modal').textContent(),/确认 实习僧.*可见浏览器/s);
  assert.equal(await page.locator('#boss-internship-only').count(),0);assert.equal(await page.locator('[data-bh-import]').count(),0);assert.equal(await page.locator('a[href="http://127.0.0.1:8686"]').count(),0);assert(await page.locator('[data-bh-resolve]').isHidden());
  assert.equal(calls.filter(c=>c.endpoint==='start-collect').length,0);
  assert.equal(await page.locator('#shixi-force-refresh').count(),0);
  assert.match(await page.locator('#modal').textContent(),/每次重新打开原始岗位/);
  await page.locator('#boss-collect-form [data-action=close]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.locator('#boss-max-pages').selectOption('2');await page.locator('[data-bh-collect]').click();await page.locator('#boss-collect-form').waitFor();
  assert.deepEqual(calls.filter(c=>c.endpoint==='search-preview').at(-1).payload,{batch_id:batchId,max_pages:2,internship_only:true,max_jobs:60,collection_mode:'collect',force_refresh:true});assert.match(await page.locator('.search-confirmation').textContent(),/最多 4 页/);assert.match(await page.locator('.search-confirmation').textContent(),/最多重新读取 60 条详情/);
  await page.locator('#boss-collect-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('shixi-demo-task'));
  assert.deepEqual(calls.find(c=>c.endpoint==='start-collect').payload,{confirmed:true,batch_id:batchId,max_pages:2,internship_only:true,max_jobs:60,collection_mode:'collect',force_refresh:true});assert(await page.locator('[data-bh-stop]').isEnabled());
  for(const title of ['演示历史岗位','演示实习僧 AI 产品实习','演示实习僧 Agent 产品实习']){
   const item=await backend(`/api/capture/batches/${batchId}/items`,{title,company:title,city:'北京',raw_text:'【演示资料】参与产品研发、用户研究，每周3天，至少6个月。',source_url:'https://www.shixiseng.com/intern/demo-'+encodeURIComponent(title)});if(!title.includes('历史'))runItems.push(item.id);
  }
  await page.locator('[data-bh-stop]').click();await page.locator('.capture-result-row').first().waitFor();assert.equal(await page.locator('.capture-result-row').count(),2);assert.match(await page.locator('#boss-connection-status').textContent(),/已停止/);assert.match(await page.locator('#boss-connection-status').textContent(),/第 2 页.*已发现 2 条.*已读取岗位详情/s);
  await page.locator('#boss-connection-status summary').filter({hasText:'来源提示已关闭'}).click();
  assert(await page.locator('#boss-connection-status').getByText(closedJob.title,{exact:true}).isVisible());
  assert.equal(await page.locator('#boss-connection-status a').filter({hasText:'原始岗位'}).getAttribute('href'),closedJob.url);
  assert.deepEqual(calls.find(c=>c.endpoint==='stop').payload,{confirmed:true});assert(calls.some(c=>c.endpoint==='sync'));assert(calls.some(c=>c.endpoint==='items'&&c.query.includes('run_id=201')));
  assert.match(await page.locator('.capture-result-detail').textContent(),/打开 实习僧 原始岗位/);assert(!await page.locator('#capture-results').textContent().then(t=>t.includes('演示历史岗位')));
  for(const [name,width,height] of [['desktop',1320,840],['compact',980,650],['narrow',430,932]]){
   await page.setViewportSize({width,height});await page.locator('#capture-results').scrollIntoViewIfNeeded();assert(await page.locator('.capture-result-detail').isVisible());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Overflow at '+width);
   if(process.env.RADAR_SHIXISENG_SCREENSHOTS){fs.mkdirSync(process.env.RADAR_SHIXISENG_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.RADAR_SHIXISENG_SCREENSHOTS,'shixiseng-'+name+'.png'),animations:'disabled'})}
  }
  await page.setViewportSize({width:1320,height:840});await page.locator(`[data-capture-detail="${runItems[0]}"]`).click();await page.locator('.capture-result-detail [data-capture-open-review]').click();await page.locator('[data-capture-review-next]').click();await page.waitForFunction(id=>document.querySelector('#capture-review-form')?.dataset.captureItem===String(id),runItems[1]);
  const jobs=await (await fetch(origin+'/api/jobs')).json();assert.equal(jobs.length,1);assert.equal(jobs[0].source_platform,'shixiseng');await page.keyboard.press('Escape');await page.locator('#modal').waitFor({state:'hidden'});
  offline=true;await page.locator('[data-bh-status]').click();await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('请安装 Node.js'));assert(await page.locator('[data-bh-collect]').isDisabled());
  offline=false;await page.locator('[data-bh-status]').click();await page.waitForFunction(()=>!document.querySelector('[data-bh-collect]').disabled);
  previewError=true;await page.locator('[data-bh-collect]').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('浏览器准备失败'));assert.equal(await page.locator('#modal').isVisible(),false);assert.equal(calls.filter(c=>c.endpoint==='start-collect').length,1);previewError=false;
  // Hold a stale response while switching to another platform: it must not overwrite BOSS state.
  const boss=await backend('/api/capture/batches',{platform:'boss',keyword:'演示 BOSS',city:'上海',label:'演示 BOSS 批次'});
  await page.locator('[data-capture-refresh]').click();await page.waitForFunction(id=>!!document.querySelector('[data-capture-select="'+id+'"]'),boss.id);
  delayStatus=true;await page.locator('[data-bh-status]').click({noWaitAfter:true});await held;
  await page.locator(`[data-capture-select="${boss.id}"]`).click();releaseStatus();await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('BossHunter 本地服务已连接'));
  assert(await page.locator('#boss-internship-only').isVisible());assert(await page.locator('a[href="http://127.0.0.1:8686"]').isVisible());assert(!await page.locator('#boss-connection-status').textContent().then(t=>t.includes('shixi-demo-task')));
  assert.deepEqual(errors,[]);console.log('PASS: Shixiseng platform selection, preview confirmation, city/pages, stop with partial auto-sync, current-run isolation, source link, responsive views, commit-next, environment recovery, preview failure, and stale-response BOSS switch.');
 }catch(error){if(page)error.message+='\nUI: '+await page.locator('#toast').textContent().catch(()=>'(unavailable)');throw error}
 finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r))}fs.rmSync(temp,{recursive:true,force:true})}
}
run().catch(e=>{console.error(e);process.exitCode=1});
