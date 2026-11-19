/* All collector/run HTTP is mocked; review writes use only temporary demo data. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-boss-ui-'));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function run(){
 const port=await freePort(),origin=`http://127.0.0.1:${port}`,python=process.env.RADAR_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.db')},stdio:['ignore','ignore','pipe']});
 let log='',browser,page;server.stderr.on('data',b=>log+=b);
 try{
  let ready=false;for(let n=0;n<60;n++){try{ready=(await fetch(origin+'/api/health')).ok}catch{}if(ready)break;await wait(150)}assert(ready,log);
  browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1320,height:900}});page.setDefaultTimeout(10000);
  const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
  const preferences={keywords:['AI 产品 实习','Agent 产品实习'],cities:['北京','上海','苏州'],platform_order:['boss'],allow_internship:true,recruitment_type:'both',max_pages:{boss:2}};
  let searchRun=null,offline=false,previewError=false,batchId,runItems=[];
  const json=async(route,body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  await page.route('**/api/capture/connectors/bosshunter/**',async route=>{
   const req=route.request(),u=new URL(req.url()),endpoint=u.pathname.split('/').pop(),payload=req.method()==='POST'?req.postDataJSON():null;
   calls.push({endpoint,payload,query:u.search});
   if(endpoint==='status')return json(route,{batch_id:Number(u.searchParams.get('batch_id')),connected:!offline,capabilities:{version:1,matched_results:true},readiness:{ok:true,checks:[{status:'pass',message:'演示浏览器已就绪'}]},preferences,task:searchRun?.status==='running'?{id:searchRun.task_id,status:'running'}:null,search_run:searchRun});
   if(endpoint==='search-preview')return previewError?json(route,{detail:'演示检查：浏览器连接失败，请开启远程调试'},503):json(route,{preferences,requires_confirmation:true,internship_only:true});
   if(endpoint==='start-collect'){searchRun={id:101,batch_id:batchId,task_id:'demo-task-1',status:'running',preferences,internship_only:true,total:0,sync_offset:0,synced_count:0};return json(route,{started:true,search_run:searchRun})}
   await route.abort();throw Error('Unexpected connector '+endpoint);
  });
  await page.route('**/api/capture/search-runs/**',async route=>{
   const req=route.request(),endpoint=new URL(req.url()).pathname.split('/').pop(),payload=req.method()==='POST'?req.postDataJSON():null;calls.push({endpoint,payload});
   if(endpoint==='sync'){searchRun={...searchRun,sync_offset:2,synced_count:2};return json(route,{created:2,scanned:2,next_offset:2,has_more:false,search_run:searchRun})}
   if(endpoint==='stop'){searchRun={...searchRun,status:'stopped',total:2};return json(route,{search_run:searchRun})}
   if(endpoint==='resolve-start'){searchRun={...searchRun,status:'interrupted'};return json(route,{search_run:searchRun})}
   return json(route,searchRun);
  });
  await page.route(url=>url.pathname.startsWith('/api/capture/batches'),async route=>{
   const req=route.request(),u=new URL(req.url());if(req.method()!=='GET')return route.continue();
   if(/\/items$/.test(u.pathname)){
    calls.push({endpoint:'items',query:u.search});const response=await route.fetch({url:origin+u.pathname});let items=await response.json();
    if(u.searchParams.get('run_id'))items=items.filter(i=>runItems.includes(i.id));
    items=items.map(i=>({...i,reused_snapshot:i.id===runItems[0],observed_at:'演示观察时间',draft:{...i.draft,salary:i.id===runItems[0]?'200 元/天':'300 元/天'}}));return json(route,items);
   }
   const response=await route.fetch();const rows=await response.json();return json(route,rows.map(b=>({...b,latest_search_run_id:searchRun?.batch_id===b.id?searchRun.id:null})));
  });
  await page.route('http://127.0.0.1:8686/**',route=>route.abort());
  const backend=async(url,data,method='POST')=>{const response=await fetch(origin+url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert(response.ok,await response.clone().text());return response.json()};
  await page.goto(origin);await page.locator('#home-search-title').waitFor();await page.locator('[data-page=capture]').first().click();
  assert.match(await page.locator('#capture-batch-form').textContent(),/城市\s*可多选，可添加其他城市/);assert.match(await page.locator('#capture-batch-form').textContent(),/每行一个，或用逗号分隔/);
  await page.locator('#capture-batch-form [name=label]').fill('演示 · BOSS 本轮检索检查（全部模拟资料）');
  await page.locator('#capture-batch-form [name=platform]').selectOption('boss');
  await page.locator('#capture-batch-form [name=keyword]').fill('AI 产品 实习\nAgent 产品实习');
  assert.equal(await page.locator('[data-capture-city=北京]').getAttribute('aria-pressed'),'true');
  await page.locator('#capture-city-input').fill('苏州');await page.locator('#capture-city-input').press('Enter');assert.equal(await page.locator('#capture-city-input').inputValue(),'');
  await page.locator('[data-capture-city=上海]').click();assert.equal(await page.locator('[data-capture-city=上海]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#capture-batch-form [name=city]').inputValue(),'北京，苏州，上海');
  await page.locator('#capture-city-input').fill('杭州');
  await page.locator('#capture-batch-form button[type=submit]').click();await page.locator('#boss-max-pages').waitFor();
  const created=(await (await fetch(origin+'/api/capture/batches')).json())[0];
  batchId=created.id;assert.equal(created.city,'北京，苏州，上海，杭州');
  assert.equal(await page.locator('#capture-input-form').evaluate(e=>e.closest('details').open),false);
  assert(await page.locator('#boss-max-pages').evaluate(e=>!!e.closest('section')));
  assert(await page.locator('#boss-internship-only').isChecked());assert.match(await page.locator('section').filter({has:page.locator('#boss-internship-only')}).textContent(),/仅实习岗位/);
  await page.locator('#boss-max-pages').selectOption('2');await page.locator('[data-bh-status]').click();await page.waitForFunction(()=>!document.querySelector('[data-bh-collect]').disabled);
  await page.locator('[data-bh-collect]').click();await page.locator('#boss-collect-form').waitFor();
  assert.deepEqual(calls.find(c=>c.endpoint==='search-preview').payload,{batch_id:batchId,max_pages:2,internship_only:true});
  assert(calls.some(c=>c.endpoint==='status'&&c.query.includes('preflight=true')));
  assert.match(await page.locator('.search-confirmation').textContent(),/仅实习岗位/);assert.match(await page.locator('.search-confirmation').textContent(),/最多 12 页/);
  await page.locator('#boss-collect-form [data-action=close]').click();await page.locator('#modal').waitFor({state:'hidden'});assert(!calls.some(c=>c.endpoint==='start-collect'));
  await page.locator('[data-bh-collect]').click();await page.locator('#boss-collect-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('[data-bh-collect]').disabled);
  assert.deepEqual(calls.find(c=>c.endpoint==='start-collect').payload,{confirmed:true,batch_id:batchId,max_pages:2,internship_only:true});
  await page.waitForFunction(()=>/采集中.*demo-task-1/.test(document.querySelector('#boss-connection-status').textContent));
  assert(await page.locator('[data-bh-stop]').isEnabled());
  // Real temporary inbox records permit meaningful defer/draft/commit checks.
  for(const [title,city] of [['演示历史岗位（不属于本轮）','北京'],['演示本轮产品实习','北京'],['演示本轮 Agent 实习','上海']]){
   const item=await backend(`/api/capture/batches/${batchId}/items`,{title,company:title,city,raw_text:'【演示资料】完整 JD：参与产品研发，要求用户研究，每周3天，至少6个月。',source_url:'https://www.zhipin.com/job_detail/demo-'+encodeURIComponent(title)+'.html'});if(!title.includes('历史'))runItems.push(item.id);
  }
  searchRun={...searchRun,status:'completed_with_shortage',total:2};await page.locator('[data-bh-status]').click();await page.locator('.capture-result-row').first().waitFor();
  assert.equal(await page.locator('.capture-result-row').count(),2);assert(!await page.locator('#capture-results').textContent().then(t=>t.includes('演示历史岗位')));
  assert(calls.some(c=>c.endpoint==='sync'&&c.payload.confirmed&&c.payload.limit===200));assert(calls.some(c=>c.endpoint==='items'&&c.query.includes('run_id=101')));
  await page.locator(`[data-capture-detail="${runItems[0]}"]`).click();assert.match(await page.locator('.capture-result-detail').textContent(),/200 元\/天/);assert.match(await page.locator('.capture-result-detail').textContent(),/本地已有快照/);
  if(process.env.RADAR_BOSS_SCREENSHOTS){
   fs.mkdirSync(process.env.RADAR_BOSS_SCREENSHOTS,{recursive:true});
   for(const [name,width,height] of [['desktop',1320,840],['compact',980,650],['narrow',430,932]]){
    await page.setViewportSize({width,height});await page.locator('#capture-results').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(process.env.RADAR_BOSS_SCREENSHOTS,'boss-search-demo-'+name+'.png'),animations:'disabled'});
    assert(await page.locator('.capture-result-detail').isVisible());
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal page overflow at '+width);
   }
   await page.setViewportSize({width:1320,height:840});
  }
  await page.locator('[data-capture-filter=salary]').fill('300');assert.equal(await page.locator('.capture-result-row').count(),1);await page.locator('[data-capture-filter=salary]').fill('');
  await page.locator('[data-capture-filter=city]').selectOption('上海');assert.equal(await page.locator('.capture-result-row').count(),1);await page.locator('[data-capture-filter=city]').selectOption('');
  for(const action of ['defer','dismiss']){
   await page.locator('.capture-result-detail [data-capture-disposition='+action+']').click();await page.waitForFunction(()=>document.querySelectorAll('.capture-result-row').length===1);
   await page.locator('[data-capture-filter=status]').selectOption(action==='defer'?'deferred':'dismissed');await page.locator('.capture-result-detail [data-capture-disposition=reopen]').click();
   await page.locator('[data-capture-filter=status]').selectOption('pending');await page.waitForFunction(()=>document.querySelectorAll('.capture-result-row').length===2);
  }
  await page.locator(`[data-capture-detail="${runItems[0]}"]`).click();await page.locator('.capture-result-detail [data-capture-open-review]').click();await page.locator('#capture-review-form [name=duration]').fill('至少6个月');await page.locator('#capture-review-form [name=onsite_days]').fill('每周3天');
  await page.locator('[data-capture-save-draft]').click();await page.locator('#modal').waitFor({state:'hidden'});
  const stored=(await (await fetch(origin+`/api/capture/batches/${batchId}/items`)).json()).find(i=>i.id===runItems[0]);assert.equal(stored.draft.duration,'至少6个月');assert.equal(stored.draft.onsite_days,'每周3天');
  await page.locator('.capture-result-detail [data-capture-open-review]').click();await page.locator('[data-capture-review-next]').click();await page.waitForFunction(id=>document.querySelector('#capture-review-form')?.dataset.captureItem===String(id),runItems[1]);
  await page.keyboard.press('Escape');await page.locator('#modal').waitFor({state:'hidden'});
  await page.reload();await page.locator('[data-page=capture]').first().click();await page.locator('.capture-result-row').waitFor();await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('结果不足'));assert.match(await page.locator('#boss-connection-status').textContent(),/结果不足/);
  offline=true;await page.locator('[data-bh-status]').click();await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('尚未安装或未启动'));assert(await page.locator('[data-bh-collect]').isDisabled());
  offline=false;await page.locator('[data-bh-status]').click();await page.waitForFunction(()=>!document.querySelector('[data-bh-collect]').disabled);
  searchRun={...searchRun,status:'running'};await page.locator('[data-bh-status]').click();await page.locator('[data-bh-stop]').click();await page.waitForFunction(()=>document.querySelector('#boss-connection-status').textContent.includes('已停止'));assert.deepEqual(calls.find(c=>c.endpoint==='stop').payload,{confirmed:true});
  searchRun={...searchRun,status:'unknown',task_id:null};await page.locator('[data-bh-status]').click();await page.locator('[data-bh-resolve]').click();await page.waitForFunction(()=>document.querySelector('[data-bh-resolve]').hidden);assert.deepEqual(calls.find(c=>c.endpoint==='resolve-start').payload,{confirmed:true});
  previewError=true;await page.locator('[data-bh-collect]').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('远程调试'));assert.equal(await page.locator('#modal').isVisible(),false);assert.equal(calls.filter(c=>c.endpoint==='start-collect').length,1);
  previewError=false;
  await page.locator('#capture-batch-form [name=label]').fill('');
  await page.locator('#capture-batch-form [name=keyword]').fill('AI 产品 实习');
  for(let i=0;i<20;i++){const pressed=page.locator('#capture-city-picker button.city-chip[aria-pressed=true], #capture-city-picker [data-capture-city-remove]');if(!await pressed.count())break;await pressed.first().click();}
  await page.locator('[data-capture-city=杭州]').click();assert.equal(await page.locator('#capture-batch-form [name=city]').inputValue(),'杭州');
  await page.locator('[data-capture-search-new]').click();await page.locator('#boss-collect-form').waitFor();
  const freshBatches=await (await fetch(origin+'/api/capture/batches')).json();
  assert.equal(freshBatches.length,2);assert.match(freshBatches[0].label,/AI 产品 实习.*杭州/);
  assert.equal(calls.filter(c=>c.endpoint==='start-collect').length,1,'Creating and previewing must await collect confirmation');
  await page.locator('#boss-collect-form [data-action=close]').click();await page.locator('#modal').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);console.log('PASS: mocked run search/confirmation, city/custom selection, internship switch, current-run auto sync, preserved salary/cache notice, filters, defer/dismiss/reopen, durable duration/attendance drafts, commit-next, refresh recovery, offline retry, stop/resolve, preview errors.');
 }catch(error){if(page)error.message+='\nUI: '+await page.locator('#toast').textContent().catch(()=>'(unavailable)');throw error}
 finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r))}fs.rmSync(temp,{recursive:true,force:true})}
}
run().catch(e=>{console.error(e);process.exitCode=1});
