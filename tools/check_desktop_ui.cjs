/* Browser checks use an isolated temporary SQLite database, never the user's data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.RADAR_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'radar-ui-'));
const shots = process.env.RADAR_UI_SHOTS;
if (shots) fs.mkdirSync(shots, {recursive:true});
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function run(){
 const port=await freePort();
 const python=process.env.RADAR_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.db')},stdio:['ignore','ignore','pipe']});
 let log='';server.stderr.on('data',b=>log+=b);let browser,page;
 try{
  const url=`http://127.0.0.1:${port}`;
  let ready=false;
  for(let n=0;n<60;n++){try{ready=(await fetch(url+'/api/health')).ok}catch{}if(ready)break;await wait(150)}
  assert(ready,'Preview server did not start: '+log);
  browser=await chromium.launch({headless:true});
  page=await browser.newPage({viewport:{width:1320,height:840}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('#home-search-title').waitFor();
  const screenshot=async name=>{if(shots){await page.locator('#toast').waitFor({state:'hidden'});await page.locator('#sidebar').evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:path.join(shots,name+'.png'),animations:'disabled'})}};
  await screenshot('desktop-empty');
  const pages=['overview','capture','jobs','companies','analysis','skills','applications','sources'];
  for(const name of pages){await page.locator(`.nav-item[data-page="${name}"]`).click();await page.locator('.page-head h1').waitFor();assert.equal(await page.locator('.nav-item[aria-current=page]').getAttribute('data-page'),name)}
  await page.locator('.nav-item[data-page=companies]').click();
  await page.locator('.page-head [data-action=add-company]').click();
  await page.locator('#company-form [name=name]').fill('演示企业 · 星图智能');
  await page.locator('#company-form [name=sector]').fill('Agent 应用');
  await page.locator('#company-form [name=city]').fill('北京');
  await page.locator('#company-form button[type=submit]').click();
  await page.locator('#modal').waitFor({state:'hidden'});await page.getByRole('heading',{name:'演示企业 · 星图智能',exact:true}).waitFor();
  await page.locator('#top-add-job').click();await page.locator('[data-advanced-job]').click(); // quick paste form → detailed form
  assert.equal(await page.locator('#job-form [name=company_id] option').first().textContent(),'请选择企业');
  await page.locator('#job-form [name=company_id]').selectOption({label:'演示企业 · 星图智能'});
  await page.locator('#job-form [name=title]').fill('演示 · Agent 应用开发实习生');
  await page.locator('#job-form [name=direction]').selectOption('agent');
  await page.locator('#job-form [name=city]').fill('北京');
  await page.locator('#job-form [name=salary]').fill('200–300 元/天');
  await page.locator('#job-form [name=duration]').fill('3 个月起');
  await page.locator('#job-form [name=onsite_days]').fill('每周 4 天');
  await page.locator('#job-form [name=priority]').selectOption('high');
  await page.locator('#job-form [name=jd_text]').fill('【界面验证演示资料】负责 Agent 工作流与 RAG 应用开发，要求 Python。此记录不是实际招聘信息。');
  await page.locator('#job-form [name=source_url]').fill('https://example.com/demo/agent');
  await page.locator('#job-form [name=skill_names]').fill('Python, Agent, RAG');
  await page.locator('#job-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.keyboard.press('Control+k');assert.equal(await page.locator('#global-search').evaluate(el=>el===document.activeElement),true);
  await page.locator('#global-search').fill('演示');await page.locator('#global-search').press('Enter');await page.locator('button[data-job]').waitFor();
  assert.equal(await page.locator('#breadcrumb').textContent(),'岗位库');
  await page.locator('#filter-q').evaluate(input=>{
   input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));input.value='星';
   input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
   if(document.querySelector('#filter-q')!==input)throw new Error('Chinese composition replaced the input');
   input.value='星图';input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
  });
  assert.equal(await page.locator('#filter-q').inputValue(),'星图');
  assert.equal(await page.locator('button[data-job]').count(),1);
  await page.locator('#filter-direction').selectOption('pm');await page.getByRole('heading',{name:'没有匹配的岗位'}).waitFor();
  await page.locator('[data-action=clear-filters]').first().click();await page.locator('button[data-job]').waitFor();
  await screenshot('desktop-jobs');
  await page.locator('button[data-job]').click();await page.locator('#modal[open]').waitFor();
  assert.equal(await page.locator('#modal').evaluate(el=>el.matches(':modal')),true);
  await screenshot('desktop-detail');
  for(let n=0;n<24;n++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>document.activeElement===document.body || !!document.activeElement.closest('#modal')),'Focus escaped the native dialog')}
  // Detail opens in reading mode; the tabbed editor appears after “编辑”.
  await page.locator('[data-job-reading-edit]').first().click();await page.locator('#job-edit-area').waitFor();
  await page.locator('[data-detail-tab=job]').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('[data-detail-tab=sources]').getAttribute('aria-selected'),'true');
  assert.equal(await page.locator('#source-form').isVisible(),true);
  await page.keyboard.press('ArrowRight');await page.locator('#job-update-form [name=stage]').selectOption('contacted');
  await page.locator('#job-update-form [name=next_action]').fill('准备 Agent 项目演示');
  await page.locator('[data-detail-tab=job]').click();await page.locator('#job-update-form [name=team]').fill('演示应用团队');
  await page.locator('#job-update-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  const detail=await (await fetch(url+'/api/jobs/1')).json();assert.equal(detail.team,'演示应用团队');assert.equal(detail.application.stage,'contacted');
  await page.locator('button[data-job]').click();await page.locator('#modal[open]').waitFor();await page.keyboard.press('Escape');assert.equal(await page.locator('#modal').isVisible(),false);
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.job),'1');
  await page.locator('button[data-job]').click();await page.locator('#modal[open]').waitFor();await page.locator('[data-job-reading-edit]').first().click();await page.locator('[data-detail-tab=sources]').click();
  await page.locator('[data-sourcecheck]').first().click();await page.locator('#source-verify-form').waitFor();await page.keyboard.press('Escape');
  await page.locator('#source-form').waitFor();assert.equal(await page.locator('[data-detail-tab=sources]').getAttribute('aria-selected'),'true');
  await page.locator('[data-skills]').click();await page.locator('#skill-review-form').waitFor();await page.keyboard.press('Escape');await page.locator('#source-form').waitFor();
  await page.locator('[data-sourcecheck]').first().click();await page.locator('#source-verify-form [name=verification]').selectOption('verified');
  await page.locator('#source-verify-form button[type=submit]').click();await page.locator('#source-verify-form').waitFor({state:'detached'});await page.locator('#source-form').waitFor();
  assert.equal(await page.locator('[data-detail-tab=sources]').getAttribute('aria-selected'),'true');assert.equal((await (await fetch(url+'/api/jobs/1')).json()).sources[0].verification,'verified');
  await page.keyboard.press('Escape');await page.locator('#modal').waitFor({state:'hidden'});

  await page.locator('.nav-item[data-page=capture]').click();
  await page.locator('#capture-batch-form [name=label]').fill('演示 · 产品岗位资料');
  await page.locator('#capture-batch-form [name=platform]').selectOption('manual');
  await page.locator('#capture-batch-form button[type=submit]').click();await page.locator('#capture-input-form').waitFor();
  await page.locator('#capture-input-form [name=title]').fill('演示 · AI 产品经理实习生');
  await page.locator('#capture-input-form [name=company]').fill('演示企业 · 星图智能');
  await page.locator('#capture-input-form [name=city]').fill('上海');
  await page.locator('#capture-input-form [name=source_url]').fill('https://example.com/demo/pm');
  await page.locator('#capture-input-form [name=raw_text]').fill('【演示资料】参与 AI 产品需求分析与用户研究，要求撰写 PRD。仅用于界面验证。');
  await page.locator('#capture-input-form button[type=submit]').click();await page.locator('[data-capture-review]').waitFor();
  await screenshot('capture-inbox');
  await page.locator('[data-capture-review]').click();await page.locator('#capture-review-form [name=direction]').selectOption('pm');
  await page.locator('#capture-review-form [name=salary]').fill('演示：220 元/天');
  await page.locator('#capture-review-form [name=eligibility]').fill('演示：在校本科生');
  await page.keyboard.press('Escape');assert(await page.locator('#modal').isVisible(),'Dirty capture review should remain after discard is rejected');
  await page.locator('[data-capture-save-draft]').click();await page.locator('#modal').waitFor({state:'hidden'});
  const draft=(await (await fetch(url+'/api/capture/batches/1/items')).json())[0];
  assert.equal(draft.status,'pending');assert.equal(draft.draft.salary,'演示：220 元/天');
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,1,'Saving a draft must not commit a job');
  await page.locator('[data-capture-review]').click();assert.equal(await page.locator('#capture-review-form [name=salary]').inputValue(),'演示：220 元/天');
  await screenshot('capture-review');
  await page.locator('#capture-review-form button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.locator('.nav-item[data-page=sources]').click();
  await page.locator('#import-file').setInputFiles({name:'demo.csv',mimeType:'text/csv',buffer:Buffer.from('company,title,city,direction,jd_text,source_url\n演示企业 · 星图智能,演示 · AI 解决方案实习生,深圳,fde,演示资料：解决方案设计与技术沟通,https://example.com/demo/fde')});
  await page.locator('[data-action=preview-import]').click();await page.locator('[data-action=confirm-import]').waitFor();
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,2,'Import preview must not write jobs');
  await page.locator('[data-action=confirm-import]').click();await page.getByRole('heading',{name:'导入完成',exact:false}).waitFor();
  assert.equal((await (await fetch(url+'/api/jobs')).json()).length,3);
  const duplicate=await fetch(url+'/api/import/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:false,rows:[{company:'演示企业 · 星图智能',title:'演示 · Agent 应用开发实习生',city:'北京',jd_text:'【演示候选资料】新增 SQL 要求，需人工确认与旧岗位是否相同。',source_url:'https://example.com/demo/agent-second'}]})});
  assert(duplicate.ok);await page.reload();await page.locator('.page-head').waitFor();await page.locator('.nav-item[data-page=capture]').click();await page.locator('[data-workspace-tab][data-page=reviews]').click();
  await page.locator('[data-review-compare]').click();await page.locator('.review-comparison').waitFor();await screenshot('duplicate-review');
  assert(await page.locator('.review-comparison').textContent().then(text=>text.includes('新增 SQL')&&text.includes('RAG')));
  await page.keyboard.press('Escape');
  await page.locator('.nav-item[data-page=overview]').click();await screenshot('desktop-overview');
  for(const size of [{width:1320,height:840},{width:980,height:650},{width:390,height:844}]){
   await page.setViewportSize(size);await page.waitForFunction(width=>document.querySelector('#sidebar').inert===(width<=760),size.width);
   for(const name of pages){
    if(size.width<=760)await page.locator('#nav-toggle').click();
    await page.locator(`.nav-item[data-page="${name}"]`).click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflow on '+name+' at '+size.width);
    assert(await page.locator('.page-head h1').isVisible());
   }
   if(size.width<=760)await page.locator('#nav-toggle').click();
   await page.locator('.nav-item[data-page=overview]').click();
   await screenshot('workspace-'+size.width);
   if(size.width<=760){await page.locator('#nav-toggle').click();await screenshot('mobile-navigation');assert(await page.locator('.main').evaluate(el=>el.inert));}
  }
  assert.deepEqual(errors,[],'Browser runtime errors');
  console.log('PASS: 11 pages at 3 sizes; company/job creation; global and Chinese search; filters; native dialog, keyboard tabs and source/skill return; job/application save; capture draft/review; duplicate JD comparison; CSV preview and import.');
 }catch(error){
  if(page)error.message+='\nLast UI message: '+await page.locator('#toast').textContent().catch(()=>'(unavailable)');
  throw error;
 }finally{
  if(browser)await browser.close();server.kill();await new Promise(r=>server.once('exit',r));fs.rmSync(temp,{recursive:true,force:true});
 }
}
run().catch(e=>{console.error(e);process.exitCode=1});
