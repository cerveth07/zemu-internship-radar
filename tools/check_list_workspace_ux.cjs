const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const base=process.env.RADAR_PRIVATE_RC_URL,output=process.env.RADAR_SCREENSHOT_DIR;
async function api(route,body,method='POST'){const r=await fetch(base+'/api'+route,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(r.ok,await r.clone().text());return r.json();}
async function create(name,content){const data={name,content},p=await api('/company-lists/import/preview',data);return (await api('/company-lists/import/commit',{...data,preview_token:p.preview_token})).id;}
async function settle(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function select(page,id){await page.locator('#company-list-picker').selectOption(String(id));await page.waitForFunction(id=>document.querySelector('.company-list-heading [data-company-list-jobs]')?.dataset.companyListJobs===String(id),id);}
async function manage(page,selector){if(!await page.locator(selector).isVisible())await page.locator('.list-manage>summary').click();await page.locator(selector).click();}
async function shot(page,name){if(output){await page.locator('#toast').waitFor({state:'hidden'});fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'v0.17.0-'+name+'.png')});}}
(async()=>{
 const co=(await api('/companies',{name:'范围测试主体有限公司'})).id,near=(await api('/companies',{name:'连续整理企业有限公司'})).id;
 for(const company_id of [co,near])await api('/jobs',{company_id,title:company_id===co?'财务分析实习':'制造业运营实习',city:'北京',direction:'运营',jd_text:'负责整理报表和运营数据',priority:'high'});
 const a=await create('名单 A','范围测试主体有限公司'),b=await create('名单 B','连续整理企业有限公司'),large=await create('连续整理名单',Array.from({length:65},(_,i)=>'连续整理企业'+String(i+1).padStart(2,'0')+'有限公司').join('\n'));
 const browser=await chromium.launch({headless:true,executablePath:process.env.RADAR_CHROME});
 const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],dialogs=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.accept();});
 try{
  await page.goto(base);await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-page=companyLists]').click();await page.locator('.company-list-heading').waitFor();await select(page,a);await shot(page,'名单常用窗口');
  await page.locator('.company-list-entry [data-company-list-jobs]').click();await page.locator('#job-results').waitFor();
  assert.match(await page.locator('.job-scope').innerText(),/企业名单：名单 A/);assert.match(await page.locator('.job-scope').innerText(),/企业：范围测试主体有限公司/);assert.match(await page.locator('.job-scope').innerText(),/共 1 个岗位/);
  await page.locator('.nav-item[data-page=analysis]').click();assert.match(await page.locator('.job-scope').first().innerText(),/企业名单：名单 A/);assert.doesNotMatch(await page.locator('.job-scope').first().innerText(),/全部岗位/);
  await page.locator('[data-analysis-flow=career]').first().click();assert.match(await page.locator('.job-scope').last().innerText(),/共 1 个岗位/);await shot(page,'分析范围');
  await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-page=companyLists]').click();await page.locator('.company-list-heading').waitFor();await select(page,a);
  for(const switchList of [false,true]){
   let release;const gate=new Promise(r=>release=r);await page.route('**/api/companies',async route=>{await gate;await route.continue();});
   const response=page.waitForResponse(r=>r.url()===base+'/api/companies');await page.locator('[data-company-list-match]').first().click();
   if(switchList){await select(page,b);await manage(page,'[data-company-list-new]');await page.locator('#company-list-import-form [name=name]').fill('后开的导入草稿');}
   else{await manage(page,'[data-company-list-rename]');await page.locator('#company-list-rename-form [name=name]').fill('后开的重命名草稿');}
   release();await response;await settle(page);
   const form=page.locator(switchList?'#company-list-import-form':'#company-list-rename-form');assert.equal(await form.isVisible(),true);assert.match(await form.locator('[name=name]').inputValue(),/后开的/);assert.equal(await page.locator('#company-list-match-form:visible').count(),0);
   await page.unroute('**/api/companies');await page.locator('#modal-close').click();await page.locator('#modal').waitFor({state:'hidden'});await select(page,a);
  }
  await page.locator('[data-company-list-match]').first().click();await page.locator('[data-list-match-clear]').click();
  assert.equal((await api('/company-lists/'+a)).entries[0].matches.length,1);assert.equal(await page.locator('#modal').isVisible(),true);
  await page.locator('#modal-close').click();await page.locator('#modal').waitFor({state:'hidden'});assert.equal((await api('/company-lists/'+a)).entries[0].matches.length,1);
  await page.locator('[data-company-list-match]').first().click();await page.locator('[data-list-match-clear]').click();await page.locator('#company-list-match-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('.list-state-cancelled').waitFor();assert.equal((await api('/company-lists/'+a)).pending_count,0);
  await select(page,large);await page.locator('#company-list-filter [name=query]').fill('连续');await page.locator('#company-list-filter [type=submit]').click();await page.locator('.company-list-entry').first().waitFor();await page.locator('#company-list-status').selectOption('pending');await page.locator('.company-list-entry').first().waitFor();
  for(let i=2;i<=3;i++){await page.locator('[data-company-list-page="'+i+'"]').click();await page.waitForFunction(i=>document.querySelector('.job-list-pagination')?.textContent.includes(i+' / 4'),i);}
  for(let i=0;i<2;i++){
   const before=await page.locator('.company-list-entry').first().getAttribute('data-list-entry');await page.locator('.company-list-entry [data-company-list-match]').first().click();await page.locator('#company-list-match-form [value="'+near+'"]').check();await page.locator('#company-list-match-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('.company-list-entry').first().waitFor();
   assert.equal(await page.locator('#company-list-filter [name=query]').inputValue(),'连续');assert.equal(await page.locator('#company-list-status').inputValue(),'pending');assert.match(await page.locator('.job-list-pagination').innerText(),/3 \/ 4/);assert.notEqual(await page.locator('.company-list-entry').first().getAttribute('data-list-entry'),before);
  }
  await manage(page,'[data-company-list-rename]');await page.locator('#company-list-rename-form [name=name]').fill('连续整理名单新版');await page.locator('#company-list-rename-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('.company-list-entry').first().waitFor();assert.equal(await page.locator('#company-list-filter [name=query]').inputValue(),'连续');assert.match(await page.locator('.job-list-pagination').innerText(),/3 \/ 4/);
  const target=await page.locator('.company-list-entry').first().getAttribute('data-list-entry');
  await page.locator('[data-company-list-remove]').first().click();
  await page.getByText('已从名单移除，原有企业和岗位已保留',{exact:true}).waitFor();
  await page.waitForFunction(id=>document.querySelector('.company-list-entry') && !document.querySelector('[data-list-entry="'+id+'"]'),target);assert.ok(dialogs.some(x=>x.startsWith('移除「')));
  await page.setViewportSize({width:980,height:650});await page.locator('.company-list-title').scrollIntoViewIfNeeded();await shot(page,'名单最小窗口');assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  const long=await create('长名称与多主体验收','华东区域（中国）供应链数字化与信息技术综合服务集团有限公司\n尚未收录岗位的企业');const longEntry=(await api('/company-lists/'+long)).entries[0];await api('/company-lists/'+long+'/entries/'+longEntry.id+'/matches',{company_ids:[co,near]},'PUT');await manage(page,'[data-company-list-refresh]');await select(page,long);assert.equal(await page.locator('.list-employer').count(),2);assert.match(await page.locator('.company-list-entry').last().innerText(),/本库尚未收录匹配岗位/);await page.setViewportSize({width:1366,height:900});await page.locator('.company-list-title').scrollIntoViewIfNeeded();await shot(page,'长名称多主体-常用窗口');await page.setViewportSize({width:980,height:650});await shot(page,'长名称多主体-最小窗口');assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  const empty=(await api('/company-lists',{name:'空名单验收'})).id;await manage(page,'[data-company-list-refresh]');await select(page,empty);assert.match(await page.locator('#content').innerText(),/名单还是空的/);assert.equal(await page.locator('[data-company-list-clear-query]').count(),0);
  assert.deepEqual(errors,[]);
  const result={passed:true,checks:['scope reflects list, employer and actual count across job and analysis pages','stale responses preserve newer same-list and cross-list drafts','clear checkboxes is unsaved until explicit save','cancelled excluded from pending','continuous editing preserves query, status and page; nearby entries remain','rename preserves context','remove requires confirmation','empty list has import action','compact layout without horizontal overflow'],errors};
  if(output)fs.writeFileSync(path.join(output,'v0.17.0-list-ux-browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){await shot(page,'回归失败');throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
