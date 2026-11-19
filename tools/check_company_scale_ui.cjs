const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const base=process.env.RADAR_PRIVATE_RC_URL,output=process.env.RADAR_SCREENSHOT_DIR;
async function api(route,body,method='POST'){const response=await fetch(base+'/api'+route,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok,route+' '+await response.clone().text());return response.json();}
async function count(page,selector,expected){await page.waitForFunction(({selector,expected})=>document.querySelectorAll(selector).length===expected,{selector,expected});}
async function number(page,scope,bound,value){const input=page.locator('[data-size-scope='+scope+'] [data-size-number='+bound+']');if(scope==='job-filter'&&!await page.locator('#job-more-filters').evaluate(el=>el.open))await page.locator('#job-more-filters>summary').click();await input.fill(value);await input.press('Tab');}
async function shot(page,name){if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,name)});}}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.RADAR_CHROME});
 const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],dialogs=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss();});
 try{
  const data=[['零至二十人企业','0-20人'],['跨范围企业','100-499人'],['未提供人数企业',''],['只知下限企业','2000人以上'],['五百人企业','500人']];
  const ids=[];
  for(const [name,headcount_text] of data){const co=await api('/companies',{name,sector:'制造业',headcount_text});ids.push(co.id);await api('/jobs',{company_id:co.id,title:name+' 财务实习',jd_text:'熟悉 Excel',priority:'high'});}
  await page.goto(base);await page.locator('#home-search-form').waitFor();
  assert.match(await page.title(),/择木/);
  await page.locator('.nav-item[data-page=jobs]').click();await count(page,'#job-results tbody tr',5);
  assert.doesNotMatch(await page.locator('#content').innerText(),/大厂|中厂|小厂|企业档位|分档/);
  assert.match(await page.locator('#content').innerText(),/企业规模/);assert.match(await page.locator('#job-results').innerText(),/待录入/);
  // Drag the real thumb through several input events. Results must update
  // without replacing the control or breaking the pointer gesture.
  await page.locator('#job-more-filters>summary').click();const slider=page.getByRole('slider',{name:'企业规模人数上限',exact:true}),handle=await slider.elementHandle(),box=await slider.boundingBox();
  await page.mouse.move(box.x+box.width-8,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.65,box.y+box.height/2,{steps:8});
  assert.equal(await handle.evaluate(el=>el.isConnected),true);assert.ok(Number(await slider.inputValue())<1001);
  await page.mouse.up();
  await page.locator('[data-action=clear-filters]').click();await count(page,'#job-results tbody tr',5);
  await number(page,'job-filter','max','500');await number(page,'job-filter','min','100');
  const upper=page.locator('[data-size-scope=job-filter] [data-size-number=max]'),lower=page.locator('[data-size-scope=job-filter] [data-size-number=min]'),range=page.locator('[data-size-scope=job-filter] output');
  await upper.fill('');
  for(const digit of '1000'){
   await upper.press(digit);
   assert.equal(await lower.inputValue(),'100');assert.equal(await range.innerText(),'100–500 人');
  }
  await upper.press('Enter');assert.equal(await lower.inputValue(),'100');assert.equal(await range.innerText(),'100–1,000 人');
  await upper.press('Tab');assert.equal(await lower.inputValue(),'100');
  await upper.fill('50');await upper.press('Tab');
  assert.equal(await lower.inputValue(),'100');assert.equal(await range.innerText(),'100–1,000 人');assert.match(await page.locator('[data-size-error]:visible').innerText(),/尚未应用/);
  await number(page,'job-filter','max','500');await number(page,'job-filter','min','200');await count(page,'#job-results tbody tr',3);
  assert.equal(await page.locator('[data-size-error]:visible').count(),0);
  await page.locator('[data-size-scope=job-filter] [data-size-unknown]').uncheck();await count(page,'#job-results tbody tr',2);
  const jobText=await page.locator('#job-results tbody').innerText();assert.match(jobText,/跨范围企业/);assert.match(jobText,/五百人企业/);assert.doesNotMatch(jobText,/未提供人数企业/);
  await shot(page,'v0.15.1-岗位人数筛选.png');
  await page.locator('.nav-item[data-page=companies]').click();await count(page,'.company-card',5);
  await number(page,'company-filter','max','1000');await number(page,'company-filter','min','500');await count(page,'.company-card',2);
  await page.locator('[data-size-scope=company-filter] [data-size-unknown]').uncheck();await count(page,'.company-card',1);
  assert.match(await page.locator('#company-results').innerText(),/五百人企业/);
  await page.locator('[data-action=clear-company-filters]').click();await count(page,'.company-card',5);
  await page.locator('button[data-company="'+ids[2]+'"]').click();await page.locator('#company-edit-form').waitFor();
  assert.equal(await page.locator('#company-edit-form [name=size_band],#company-edit-form [name=tier_reason]').count(),0);
  await page.locator('#company-edit-form [name=headcount_text]').fill('150人');await page.locator('#company-edit-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  const saved=(await api('/companies')).find(c=>c.id===ids[2]);assert.equal(saved.headcount_min,150);assert.equal(saved.headcount_source_url,'');
  await page.locator('button[data-company="'+ids[2]+'"]').click();await page.locator('#company-edit-form').waitFor();
  await page.locator('#company-edit-form [name=headcount_text]').fill('1000人');await page.locator('#company-edit-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  const updated=(await api('/companies')).find(c=>c.id===ids[2]);assert.equal(updated.headcount_min,1000);assert.equal(updated.headcount_max,1000);
  await number(page,'company-filter','max','1200');await number(page,'company-filter','min','800');await count(page,'.company-card',1);
  assert.match(await page.locator('#company-results').innerText(),/未提供人数企业/);assert.match(await page.locator('#company-results').innerText(),/1000人/);
  await shot(page,'v0.15.1-企业规模.png');
  const stats=await api('/requirements/stats?headcount_min=200&headcount_max=500&include_unknown_size=false');assert.equal(stats.selected_jobs,2);assert.ok(stats.comparisons.company_scale);assert.equal(stats.comparisons.tier,undefined);
  const exported=await api('/exports/companies',{ids:[ids[1]],filters:{headcount_min:'200',headcount_max:'500'}});assert.equal(exported.companies[0].headcount_text,'100-499人');assert.doesNotMatch(JSON.stringify(exported),/company_tier|size_band|tier_reason/);
  await page.setViewportSize({width:980,height:650});assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  await page.locator('.nav-item[data-page=overview]').click();const start=await page.locator('#home-search-form [type=submit]').boundingBox();assert.ok(start&&start.y+start.height<=650);
  await page.locator('.nav-item[data-page=skills]').click();await page.locator('[data-size-scope=gap-filter]').waitFor();assert.doesNotMatch(await page.locator('#content').innerText(),/企业档位|大厂|中厂|小厂/);
  await page.locator('.gap-metrics').waitFor();
  await number(page,'gap-filter','max','500');await number(page,'gap-filter','min','200');
  const gapResponse=page.waitForResponse(r=>r.url().includes('/api/skill-gap?')&&r.url().includes('include_unknown_size=false'));
  await page.locator('[data-size-scope=gap-filter] [data-size-unknown]').uncheck();assert.equal((await (await gapResponse).json()).selected_jobs,2);
  await page.waitForFunction(()=>document.querySelector('.gap-metrics .stat-number')?.textContent==='2');
  await number(page,'gap-filter','min','50');await number(page,'gap-filter','max','99');
  await page.locator('[data-gap-action=clearSize]').waitFor();assert.match(await page.locator('#content').innerText(),/没有符合人数范围的岗位/);
  assert.equal(await page.locator('[data-gap-action=extract]').count(),0);await page.locator('[data-gap-action=clearSize]').scrollIntoViewIfNeeded();await shot(page,'v0.15.1-人数筛选空态.png');
  await page.locator('[data-gap-action=clearSize]').click();await page.waitForFunction(()=>document.querySelector('.gap-metrics .stat-number')?.textContent==='5');
  await page.locator('[data-gap-action=extract]').waitFor();assert.equal(await page.locator('[data-size-scope=gap-filter] [data-size-number=min]').inputValue(),'');
  assert.equal(await page.locator('[data-size-scope=gap-filter] [data-size-unknown]').isChecked(),true);
  // Let the initial response arrive during an unfinished edit, deterministically.
  let releaseStats,firstStats=true;
  const statsGate=new Promise(resolve=>{releaseStats=resolve;});
  await page.route('**/api/requirements/stats?*',async route=>{if(firstStats){firstStats=false;await statsGate;}await route.continue();});
  await page.locator('[data-gap-action=advanced]').click();await page.locator('[data-size-scope=demand-filter]').waitFor();
  const draftUpper=page.locator('[data-size-scope=demand-filter] [data-size-number=max]');await draftUpper.fill('5');
  const initialStats=page.waitForResponse(r=>r.url().includes('/api/requirements/stats?'));releaseStats();await initialStats;
  await page.waitForFunction(()=>document.querySelector('#content .stats .stat-number')?.textContent==='5');
  assert.equal(await draftUpper.inputValue(),'5');assert.equal(await draftUpper.evaluate(el=>document.activeElement===el),true);
  await draftUpper.press('0');await draftUpper.press('0');await draftUpper.press('Tab');await number(page,'demand-filter','min','200');
  const demandResponse=page.waitForResponse(r=>r.url().includes('/api/requirements/stats?')&&r.url().includes('include_unknown_size=false'));
  await page.locator('[data-size-scope=demand-filter] [data-size-unknown]').uncheck();assert.equal((await (await demandResponse).json()).selected_jobs,2);
  await page.waitForFunction(()=>document.querySelector('#content .stats .stat-number')?.textContent==='2');
  assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);
  const result={passed:true,browser:'Browser plugin not available; Playwright / Chromium',viewports:['1366x900','980x650'],checks:['no retired classifications','continuous native range drag','number drafts preserve the other bound','Enter and blur commit complete values','async statistics refresh preserves unfinished numbers and focus','invalid numeric range keeps applied filters','overlapping ranges','include missing toggle','independent company and job filters','edited scale text synchronizes numeric bounds','exports and statistics','zero jobs offers clear size filter; jobs without requirements offers JD extraction','minimum-window search action'],errors,dialogs};
  if(output)fs.writeFileSync(path.join(output,'v0.15.1-scale-browser-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await shot(page,'v0.15.1-scale-failure.png');throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
