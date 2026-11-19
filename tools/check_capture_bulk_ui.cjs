/* Real-browser bulk intake regression against an isolated, disposable database. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os');
const path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),python=process.env.RADAR_PYTHON||'python';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function run(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-capture-bulk-'));
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
 const port=socket.address().port;await new Promise(r=>socket.close(r));
 const url=`http://127.0.0.1:${port}`;
 const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],
  {cwd:root,env:{...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.sqlite')},stdio:['ignore','ignore','pipe']});
 let log='',browser;server.stderr.on('data',b=>log+=b);
 try{
  let ready=false;for(let n=0;n<100;n++){try{ready=(await fetch(url+'/api/health')).ok}catch{}if(ready)break;await sleep(100)}assert(ready,log);
  const request=async(route,data,method='POST')=>{const r=await fetch(url+route,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert(r.ok,await r.clone().text());return r.json()};
  const batch=await request('/api/capture/batches',{platform:'manual',label:'集中核对验证'});
  const add=async(title,company='星海智能')=>request(`/api/capture/batches/${batch.id}/items`,{
   title,company,city:'北京',raw_text:'负责 AI 产品设计与用户研究。\n熟悉 Python；每周到岗 3 天，实习至少 3 个月。\n欢迎在校学生申请。',source_url:'https://example.com/'+encodeURIComponent(title)});
  const one=await add('AI 产品实习生'),two=await add('智能体研发实习生'),missing=await add('资料缺失岗位','');
  await request('/api/capture/items/'+one.id,{salary:'200–250 元/天',duration:'3 个月以上',onsite_days:'每周 3 天',deadline:'2026-11-01',posted_at:'2026-10-19',direction:'pm'},'PATCH');
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1320,height:840}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto(url);await page.waitForFunction(()=>state.loaded);
  await page.locator('.nav-item[data-page=capture]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-capture-detail]').length===3);
  // Honor the current filter; do not silently include hidden candidates.
  await page.locator('[data-capture-filter=q]').fill('智能体');
  await page.waitForFunction(()=>document.querySelectorAll('[data-capture-detail]').length===1);
  await page.locator('[data-capture-bulk-review]').click();
  await page.locator('#capture-intake-form').waitFor();assert.equal(await page.locator('[data-intake-row]').count(),1);
  await page.keyboard.press('Escape');await page.locator('[data-capture-filter=q]').fill('');
  await page.locator('[data-capture-bulk-review]').click();await page.locator('#capture-intake-form').waitFor();
  assert.equal(await page.locator('[data-intake-row]').count(),3);
  assert(await page.locator(`[data-intake-select="${missing.id}"]`).isDisabled());
  assert(await page.locator('#capture-intake-form [type=submit]').isDisabled());
  const evidence=page.locator(`[data-intake-row="${one.id}"] details`);await evidence.locator('summary').click();
  assert((await evidence.innerText()).includes('2026-11-01'));
  assert((await evidence.innerText()).includes('欢迎在校学生申请'));
  await page.locator('[data-intake-all]').check();assert.equal(await page.locator('[data-intake-select]:checked').count(),2);
  const shots=process.env.RADAR_UI_SHOTS;if(shots)fs.mkdirSync(shots,{recursive:true});
  for(const [width,height] of [[1320,840],[980,650],[390,844]]){
   await page.setViewportSize({width,height});
   await page.locator('#capture-intake-form').waitFor();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   assert(await page.evaluate(()=>{const b=document.querySelector('#modal-body');return b.scrollWidth<=b.clientWidth+1}));
   await page.locator('#capture-intake-form [type=submit]').scrollIntoViewIfNeeded();
   if(shots)await page.screenshot({path:path.join(shots,`capture-bulk-${width}.png`)});
  }
  await page.setViewportSize({width:1320,height:840});
  // Change a checked record after preview: atomically reject both, retain selection.
  await request('/api/capture/items/'+two.id,{salary:'300 元/天'},'PATCH');
  await page.locator('#capture-intake-form [type=submit]').click();
  await page.locator('#capture-intake-form #form-error').filter({hasText:'已变化'}).waitFor();
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,0);
  assert.equal(await page.locator('[data-intake-select]:checked').count(),2);
  await page.locator('[data-intake-reload]').click();await page.locator('#capture-intake-form').waitFor();
  assert.equal(await page.locator('[data-intake-select]:checked').count(),0);
  await page.locator('[data-intake-all]').check();
  // A lost HTTP response is retried without duplicating jobs or history.
  let first=true;
  await page.route('**/api/capture/items/commit-bulk',async route=>{
   if(first){first=false;await route.fetch();await route.abort('failed')}else await route.continue();
  });
  await page.locator('#capture-intake-form [type=submit]').click();
  await page.locator('#capture-intake-form #form-error').filter({hasText:/./}).waitFor();
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,2);
  await page.locator('#capture-intake-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,2);
  await page.locator(`[data-capture-detail="${missing.id}"]`).waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('[data-capture-detail]').length===1);
  // Invalid rows keep a working individual correction path.
  await page.locator('[data-capture-bulk-review]').click();await page.locator('#capture-intake-form').waitFor();
  await page.locator(`[data-intake-edit="${missing.id}"]`).click();await page.locator('#capture-review-form').waitFor();
  await page.locator('#capture-review-form [name=company]').fill('补充企业');
  await page.locator(`[data-capture-save-draft="${missing.id}"]`).click();
  await page.locator('#capture-intake-form').waitFor();
  assert.equal(await page.locator('[data-intake-select]:checked').count(),0);
  assert(await page.locator(`[data-intake-select="${missing.id}"]`).isEnabled());
  await page.locator('[data-intake-all]').check();await page.locator('#capture-intake-form [type=submit]').click();
  await page.locator('#modal').waitFor({state:'hidden'});
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,3);
  assert.deepEqual(errors,[]);
  console.log('PASS: filtered review, exception correction, explicit selection, stale atomic rejection, lost-response retry, dates/JD, 1320/980/390 layouts');
 }finally{
  if(browser)await browser.close();
  const stopped=new Promise(r=>server.once('exit',r));server.kill();await stopped;
  fs.rmSync(temp,{recursive:true,force:true});
 }
}
run().catch(error=>{console.error(error);process.exitCode=1});
