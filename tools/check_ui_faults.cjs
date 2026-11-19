/* Fault-path browser checks use only a temporary SQLite database and loopback server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.RADAR_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const gate = () => {let release;const promise=new Promise(resolve=>release=resolve);return {promise,release}};
const csv = (name, text) => ({name, mimeType:'text/csv', buffer:Buffer.from(text)});
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port}

async function run(){
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-ui-faults-'));
 let server,browser,page,scenario='startup',url,log='',passed=false;
 const errors=[];
 try{
  const port=await freePort();url=`http://127.0.0.1:${port}`;
  const python=process.env.RADAR_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.db')},stdio:['ignore','ignore','pipe']});
  server.stderr.on('data',data=>log+=data);server.on('error',error=>log+=error.message);
  let ready=false;
  for(let n=0;n<60;n++){try{ready=(await fetch(url+'/api/health')).ok}catch{}if(ready)break;await wait(150)}
  assert(ready,'Backend did not start: '+log);
  async function api(route,body,method='POST'){
   const response=await fetch(url+'/api'+route,body===undefined?undefined:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   assert(response.ok,route+' returned '+response.status+': '+(response.ok?'':await response.text()));return response.json();
  }
  await api('/companies',{name:'Fault review company'});
  await api('/jobs',{company_id:1,title:'Fault review intern',jd_text:'Python and RAG required',source_platform:'manual',source_url:'https://example.com/fault-review'});
  browser=await chromium.launch({headless:true});
  async function open(viewport={width:1320,height:840},load=true){
   page=await browser.newPage({viewport});page.setDefaultTimeout(10000);await page.addInitScript(()=>{try{localStorage.setItem('radar-advanced-nav','true')}catch{}});page.on('pageerror',error=>errors.push(error.message));
   if(load){await page.goto(url);await page.locator('.page-head h1').waitFor()}
   return page;
  }
  async function detail(){await page.locator('.nav-item[data-page=jobs]').click();await page.locator('button[data-job="1"]').click();await page.locator('[data-job-reading-edit]').first().click();await page.locator('#job-update-form').waitFor()}
  async function saveDetail(){await page.locator('#job-update-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'})}

  scenario='ordinary job edits preserve application state';
  await open();await detail();await page.locator('[name=team]').fill('Changed team');await saveDetail();
  assert.equal((await api('/jobs/1')).application,null,'Ordinary job save created a blank application');
  const application={stage:'contacted',resume_version:'CV v2',recruiter:'Reviewer',last_contact:'2026-10-08',next_action:'Prepare demo',notes:'Keep private communication notes'};
  await api('/jobs/1/application',application,'PUT');
  await detail();await page.locator('[name=jd_text]').fill('Python and RAG required; corrected JD');await saveDetail();
  let saved=(await api('/jobs/1')).application;
  for(const [key,value] of Object.entries(application))assert.equal(saved[key],value,'Job edit erased application '+key);
  await detail();await page.locator('[name=team]').fill('Atomic team');await page.locator('[data-detail-tab=application]').click();await page.locator('[name=next_action]').fill('New application action');await saveDetail();
  const job=await api('/jobs/1');assert.equal(job.team,'Atomic team');assert.equal(job.application.next_action,'New application action');
  assert.equal(job.application.last_contact,application.last_contact);assert.equal(job.application.notes,application.notes);
  await page.close();

  for(const kind of ['skill','source']){
   scenario=kind+' child modal guards unsaved parent draft';
   await open();await detail();await page.locator('[name=team]').fill('Unsaved team '+kind);await page.locator('[data-detail-tab=application]').click();await page.locator('[name=next_action]').fill('Unsaved action '+kind);await page.locator('[data-detail-tab=sources]').click();
   let prompts=0;page.on('dialog',async dialog=>{prompts++;await dialog.dismiss()});
   await page.locator(kind==='skill'?'[data-skills]':'[data-sourcecheck]').click();
   assert.equal(prompts,1,'Leaving an edited job must ask before discarding');
   assert.equal(await page.locator('#job-update-form').count(),1,'Cancelled discard replaced the job editor');
   assert.equal(await page.locator('[name=team]').inputValue(),'Unsaved team '+kind);
   assert.equal(await page.locator('[name=next_action]').inputValue(),'Unsaved action '+kind);
   await page.close();
  }

  scenario='pending save prevents closing or replacing editor';
  await open();const saveGate=gate(),saveSeen=gate();let posts=0;
  await page.route('**/api/companies',async route=>{if(route.request().method()==='POST'){posts++;saveSeen.release();await saveGate.promise}await route.continue()});
  await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-action=add-company]').click();await page.locator('#company-form [name=name]').fill('Pending company');await page.locator('#company-form button[type=submit]').click();await saveSeen.promise;
  try{
   await page.keyboard.press('Escape');assert(await page.locator('#modal').evaluate(element=>element.open),'Escape closed pending save');
   assert(await page.locator('#company-form').evaluate(element=>element.inert),'Pending form still accepts edits');
   assert(await page.locator('#company-form button[type=submit]').isDisabled());
   await page.locator('#modal-close').click();assert(await page.locator('#modal').evaluate(element=>element.open),'Close button closed pending save');
   assert.equal(posts,1);
  }finally{saveGate.release()}
  await page.locator('#modal').waitFor({state:'hidden'});await page.locator('[data-action=add-company]').click();await page.locator('#company-form [name=name]').fill('Next unsaved company');
  assert(await page.locator('#modal').evaluate(element=>element.open));await page.close();

  scenario='saved job with failed reload offers reload without retrying POST';
  await open();await page.locator('#top-add-job').click();await page.locator('[data-advanced-job]').click();await page.locator('#job-form [name=company_id]').selectOption({index:1});await page.locator('#job-form [name=title]').fill('Saved once despite refresh error');
  await page.route('**/api/dashboard',route=>route.fulfill({status:500,contentType:'application/json',body:'{"detail":"simulated refresh failure"}'}));
  await page.locator('#job-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('#toast').filter({hasText:'列表刷新失败'}).waitFor();
  assert.equal((await api('/jobs')).filter(item=>item.title==='Saved once despite refresh error').length,1);
  await page.unroute('**/api/dashboard');await page.locator('[data-action=reload-data]').click();await page.locator('.page-error').waitFor({state:'detached'});
  assert.equal((await api('/jobs')).filter(item=>item.title==='Saved once despite refresh error').length,1);await page.close();

  scenario='hidden required title reveals job tab and receives focus';
  await open();await detail();await page.locator('[name=title]').fill('');await page.locator('[data-detail-tab=application]').click();await page.locator('[name=next_action]').fill('Must not save invalid job');await page.locator('#job-update-form button[type=submit]').click();
  assert.equal(await page.locator('[data-detail-tab=job]').getAttribute('aria-selected'),'true');
  assert(await page.locator('[name=title]').evaluate(element=>element.validity.valueMissing&&element===document.activeElement));
  assert.equal((await api('/jobs/1')).application.next_action,'New application action');await page.close();

  scenario='mobile drawer Ctrl+K closes drawer and focuses search';
  await open({width:390,height:844});await page.locator('#nav-toggle').click();await page.keyboard.press('Control+k');
  assert.equal(await page.locator('body').evaluate(element=>element.classList.contains('sidebar-open')),false);
  assert.equal(await page.locator('.main').evaluate(element=>element.inert),false);
  assert(await page.locator('#global-search').evaluate(element=>element===document.activeElement));
  await page.locator('#nav-toggle').click();await page.keyboard.press('Escape');assert(await page.locator('#nav-toggle').evaluate(element=>element===document.activeElement));await page.close();

  scenario='CSV commit failure shows feedback and allows a safe retry';
  await open();await page.locator('.nav-item[data-page=sources]').click();await page.locator('#import-file').setInputFiles(csv('valid.csv','company,title\nCsv company,Csv role'));await page.locator('[data-action=preview-import]').click();await page.locator('[data-action=confirm-import]').waitFor();
  await page.route('**/api/import/jobs',async route=>{if(!route.request().postDataJSON().dry_run)await route.fulfill({status:500,contentType:'application/json',body:'{"detail":"simulated CSV commit failure"}'});else await route.continue()});
  await page.locator('[data-action=confirm-import]').click();await page.locator('#toast').filter({hasText:'导入失败：simulated CSV commit failure'}).waitFor();
  assert.equal((await api('/jobs')).filter(item=>item.title==='Csv role').length,0);assert(await page.locator('[data-action=confirm-import]').isEnabled());
  await page.unroute('**/api/import/jobs');await page.locator('[data-action=confirm-import]').click();await page.getByRole('heading',{name:'导入完成',exact:false}).waitFor();
  assert.equal((await api('/jobs')).filter(item=>item.title==='Csv role').length,1);await page.close();

  scenario='CSV selection and parse error invalidate old confirmation';
  await open();await page.locator('.nav-item[data-page=sources]').click();await page.locator('#import-file').setInputFiles(csv('old.csv','company,title\nOld company,Must not import old CSV'));await page.locator('[data-action=preview-import]').click();await page.locator('[data-action=confirm-import]').waitFor();
  await page.locator('#import-file').setInputFiles(csv('bad.csv','company,title\n"Unclosed quote,Bad role'));
  assert.equal(await page.locator('[data-action=confirm-import]').count(),0,'New file kept old confirm active');
  await page.locator('[data-action=preview-import]').click();await page.locator('#toast').filter({hasText:'未正确闭合'}).waitFor();
  assert.equal(await page.locator('[data-action=confirm-import]').count(),0);assert.equal((await api('/jobs')).filter(item=>item.title==='Must not import old CSV').length,0);await page.close();

  scenario='typing during analysis preview invalidates response and saves only re-previewed JSON';
  const pkg=await api('/analysis/packages',{save_snapshot:true});await open();page.on('dialog',dialog=>dialog.accept());await page.locator('.nav-item[data-page=analysis]').click();await page.locator('[data-analysis-flow=advanced]').click();
  const payload=title=>JSON.stringify({package_id:pkg.package.package_id,findings:[{kind:'other',title,content:title+' body',job_ids:[]}]});
  const analysisGate=gate(),analysisSeen=gate();let delay=true;
  await page.route('**/api/analysis/import',async route=>{if(delay&&route.request().postDataJSON().dry_run){analysisSeen.release();await analysisGate.promise}await route.continue()});
  await page.locator('#analysis-import-json').fill(payload('Stale preview A'));await page.locator('[data-action=preview-analysis-import]').click();await analysisSeen.promise;
  try{await page.locator('#analysis-import-json').fill(payload('Visible result B'));assert(await page.locator('[data-action=commit-analysis-import]').isDisabled())}finally{analysisGate.release();delay=false}
  await page.locator('#toast').filter({hasText:'内容已修改，请重新预览'}).waitFor();assert(await page.locator('[data-action=commit-analysis-import]').isDisabled());assert.equal(JSON.parse(await page.locator('#analysis-import-json').inputValue()).findings[0].title,'Visible result B');
  await page.locator('[data-action=preview-analysis-import]').click();await page.locator('[data-action=commit-analysis-import]:enabled').waitFor();await page.locator('[data-action=commit-analysis-import]').click();await page.locator('.analysis-finding').waitFor();
  assert.deepEqual((await api('/analysis/findings')).map(item=>item.title),['Visible result B']);await page.close();

  scenario='initial read failure stays explicit until retry succeeds';
  await open(undefined,false);await page.route('**/api/skills',route=>route.fulfill({status:500,contentType:'application/json',body:'{"detail":"simulated initial read failure"}'}));await page.goto(url);await page.getByRole('heading',{name:'本地数据尚未加载'}).waitFor();
  await page.locator('.nav-item[data-page=jobs]').click();assert(await page.getByRole('heading',{name:'本地数据尚未加载'}).isVisible());assert.equal(await page.getByRole('heading',{name:'岗位库还没有记录'}).count(),0);assert(await page.locator('#top-add-job').isDisabled());
  await page.unroute('**/api/skills');await page.locator('[data-action=reload-data]').click();await page.locator('button[data-job="1"]').waitFor();assert(await page.locator('#top-add-job').isEnabled());
  assert.deepEqual(errors,[],'Browser runtime errors');passed=true;
 }catch(error){const message='Scenario: '+scenario+'\n'+error.message+'\nLast UI message: '+(page?await page.locator('#toast').textContent().catch(()=>'(unavailable)'):'(unavailable)');throw new Error(message,{cause:error})}
 finally{
  try{if(browser)await browser.close()}finally{
   if(server&&server.exitCode===null&&server.signalCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');const force=setTimeout(()=>server.kill('SIGKILL'),3000);await stopped;clearTimeout(force)}
   fs.rmSync(temp,{recursive:true,force:true});assert(!fs.existsSync(temp),'Temporary database cleanup failed');
   if(url){let live=false;try{live=(await fetch(url+'/api/health')).ok}catch{}assert(!live,'Temporary backend is still responding')}
   if(!passed)console.log('CLEANUP: temporary backend stopped and database removed.');
  }
 }
 if(passed)console.log('PASS: UI fault paths, modal draft/save guards, CSV and analysis import races, error recovery, mobile shortcut, and application preservation; temporary backend stopped and database removed.');
}
run().catch(error=>{console.error(error);process.exitCode=1});
