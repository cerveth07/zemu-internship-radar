/* Integration regression: isolated temporary DB; no live data or external sites. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os');
const path=require('node:path'),net=require('node:net');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const python=process.env.RADAR_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function run(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-integration-'));
 const env={...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.sqlite')};
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
 const port=socket.address().port;await new Promise(r=>socket.close(r));
 const url=`http://127.0.0.1:${port}`;
 const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env,stdio:['ignore','ignore','pipe']});
 let log='',browser;server.stderr.on('data',b=>log+=b);
 try{
  let ready=false;
  for(let n=0;n<100;n++){try{ready=(await fetch(url+'/api/health')).ok}catch{}if(ready)break;await sleep(100)}
  assert(ready,log);
  const post=async(route,data)=>{const r=await fetch(url+route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert(r.ok,await r.clone().text());return r.json()};
  let cid;
  for(let n=1;n<=27;n++){const c=await post('/api/companies',{name:'分页企业 '+String(n).padStart(2,'0'),headcount_text:'1000-9999人'});cid=c.id}
  for(let n=1;n<=30;n++)await post('/api/jobs',{company_id:cid,title:'分页岗位 '+n,priority:'high',jd_text:'熟悉 RAG 者优先'});
  const seed=spawnSync(python,['-c',`from app.db import connect
with connect() as c:
 c.execute("CREATE TABLE radar_skill_cleanup_links(batch_id TEXT,job_id INTEGER,skill_name TEXT,category TEXT,necessity TEXT,evidence_json TEXT,old_skill_ids_json TEXT,live_check TEXT,review_status TEXT)")
 c.execute("INSERT INTO radar_skill_cleanup_links VALUES('demo',1,'RAG','technical','bonus',?,'[]','candidate_supported_by_current_jd','candidate')", ('["熟悉 RAG 者优先"]',))
`],{cwd:root,env,encoding:'utf8'});assert.equal(seed.status,0,seed.stderr);
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1320,height:840}}),errors=[];
  // Companies and evidence are advanced pages; enable them as a returning user would.
  await page.addInitScript(()=>{try{localStorage.setItem('radar-advanced-nav','true')}catch{}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('.nav-item[data-page=companies]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.company-card').length===12);
  await page.locator('[data-listing-page="3"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.company-card').length===3);
  const companyIds=await page.locator('[data-company]').evaluateAll(xs=>xs.map(x=>x.dataset.company));
  assert.equal(new Set(companyIds).size,3);
  await page.locator('[data-company]').first().click();await page.locator('#company-edit-form').waitFor();
  await page.keyboard.press('Escape');
  await page.locator('#company-filter-q').fill('分页企业 01');
  await page.waitForFunction(()=>document.querySelectorAll('.company-card').length===1);
  await page.locator('[data-action=clear-company-filters]').click();
  await page.locator('#listing-size-companies').selectOption('24');
  await page.waitForFunction(()=>document.querySelectorAll('.company-card').length===24);
  assert.equal(await page.evaluate(()=>selectedCompanies().length),27);
  const shots=process.env.RADAR_UI_SHOTS;
  if(shots){fs.mkdirSync(shots,{recursive:true});await page.screenshot({path:path.join(shots,'integration-companies-desktop.png')})}
  await page.locator('.nav-item[data-page=jobs]').click();
  await page.locator('[data-job-select]').first().check();
  await page.locator('[data-job-page="2"]').click();
  await page.locator('[data-job-select]').first().check();
  assert.equal(await page.evaluate(()=>chosenJobs().length),2);
  assert.equal(await page.locator('[data-job-select]').count(),5);
  await page.locator('.nav-item[data-page=skills]').click();
  await page.locator('.gap-expand[data-gap-expand="rag"]').click();
  await page.getByText('清洗候选 1 岗',{exact:true}).waitFor();
  await page.locator('[data-gap-filter=includePending]').uncheck();
  await page.getByText('清洗候选 1 岗',{exact:true}).waitFor({state:'hidden'});
  await page.locator('[data-gap-filter=includePending]').check();
  await page.locator('.gap-expand[data-gap-expand="rag"]').click();
  await page.getByText('清洗候选 1 岗',{exact:true}).waitFor();
  if(shots)await page.screenshot({path:path.join(shots,'integration-skills-desktop.png')});
  // An explicit manual review appears in the same workbench with its own origin.
  const reviewed=await fetch(url+'/api/jobs/2/skills',{method:'PUT',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({confirmed:true,skills:[{name:'RAG',necessity:'bonus',evidence:'熟悉 RAG 者优先'}]})});
  assert(reviewed.ok);await page.evaluate(()=>refreshSkillGap());
  await page.getByText('人工技能确认 1 岗',{exact:true}).waitFor();
  // The unified JSON importer preserves the export identity and blocks old decisions.
  const exported=await post('/api/exports/jobs',{ids:[2]});
  const priorityFile={name:'priority.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({
   package_id:exported.package_id,priorities:[{job_id:2,priority:'medium',reason:'测试分析'}]}))};
  await page.locator('#gpt-json-file').setInputFiles(priorityFile);
  await page.locator('[data-gpt-commit]:not([disabled])').waitFor();
  await page.locator('[data-gpt-commit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  assert.equal((await (await fetch(url+'/api/jobs/2')).json()).priority,'medium');
  await fetch(url+'/api/jobs/2',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({priority:'skip'})});
  await page.locator('#gpt-json-file').setInputFiles(priorityFile);
  await page.getByText(/岗位资料、优先级或分析依据已变化/).waitFor();
  assert(await page.locator('[data-gpt-commit]').isDisabled());
  await page.locator('[data-gpt-cancel]').click();
  // Captured dates are visible, editable and survive the actual review submit.
  const batch=await post('/api/capture/batches',{platform:'manual',label:'日期回归'});
  const item=await post(`/api/capture/batches/${batch.id}/items`,{raw_text:'完整测试 JD',company:'日期企业',title:'日期岗位'});
  await fetch(url+`/api/capture/items/${item.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({posted_at:'2026-10-19',deadline:'2026-10-21'})});
  await page.reload();await page.locator('.nav-item[data-page=capture]').click();
  await page.locator(`[data-capture-select="${batch.id}"]`).click();
  await page.locator(`[data-capture-open-review="${item.id}"]`).click();
  assert.equal(await page.locator('#capture-review-form [name=posted_at]').inputValue(),'2026-10-19');
  await page.locator('#capture-review-form [name=deadline]').fill('2026-10-22');
  if(shots)await page.screenshot({path:path.join(shots,'consistency-capture-dates.png')});
  await page.locator('#capture-review-form button[type=submit]').click();
  await page.locator('#modal').waitFor({state:'hidden'});
  const dated=(await (await fetch(url+'/api/jobs')).json()).find(j=>j.title==='日期岗位');
  assert.equal(dated.deadline,'2026-10-22');assert.equal(dated.posted_at,'2026-10-19');
  await page.locator('.nav-item[data-page=companies]').click();
  await page.emulateMedia({reducedMotion:'reduce'});
  for(const width of [980,390]){
   await page.setViewportSize({width,height:840});
   await page.locator('.listing-pagination').scrollIntoViewIfNeeded();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Overflow at ${width}`);
   assert(await page.locator('.listing-pagination').evaluate(el=>el.getBoundingClientRect().right<=innerWidth),`Pager overflow at ${width}`);
   if(shots)await page.screenshot({path:path.join(shots,`integration-companies-${width}.png`),animations:'disabled'});
  }
  assert.deepEqual(errors,[]);console.log('PASS: company paging, filtering, edit ID, full export scope; cross-page job selection; candidate toggle; manual skills; versioned priority import; captured dates; desktop and narrow layouts.');
 }finally{
  if(browser)await browser.close();
  const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;
  fs.rmSync(temp,{recursive:true,force:true});
 }
}
run().catch(e=>{console.error(e);process.exitCode=1});
