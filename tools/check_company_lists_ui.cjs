const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const base=process.env.RADAR_PRIVATE_RC_URL,output=process.env.RADAR_SCREENSHOT_DIR;
const compact=process.env.RADAR_LIST_VIEWPORT==='compact';
async function api(route,body,method='POST'){
 const response=await fetch(base+'/api'+route,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 assert.ok(response.ok,route+' '+await response.clone().text());return response.json();
}
async function rows(page,count){await page.waitForFunction(n=>document.querySelectorAll('#job-results tbody tr').length===n,count);}
async function size(page,bound,value){if(!await page.locator('#job-more-filters').isVisible()||!await page.locator('#job-more-filters').evaluate(el=>el.open))await page.locator('#job-more-filters>summary').click();const input=page.locator('[data-size-scope=job-filter] [data-size-number='+bound+']');await input.fill(value);await input.press('Tab');await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function screenshot(page,name){if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'v0.16.0-'+(compact?'最小窗口-':'常用窗口-')+name+'.png')});}}
async function selectList(page,id){await page.locator('#company-list-picker').selectOption(String(id));await page.waitForFunction(id=>document.querySelector('[data-company-list-jobs]')?.dataset.companyListJobs===String(id),id);await page.locator('.company-list-entry').first().waitFor();}
async function preview(page){
 const [response]=await Promise.all([
  page.waitForResponse(r=>r.url()===base+'/api/company-lists/import/preview' && r.request().method()==='POST'),
  page.locator('[data-company-list-preview]').click()
 ]);
 assert.ok(response.ok(),await response.text());
 await page.locator('[data-list-import-preview] [role=status]').waitFor();
}
async function commit(page){await page.locator('#company-list-import-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('.company-list-heading').waitFor();}
async function manage(page,selector){const button=page.locator(selector);if(!await button.isVisible())await page.locator('.list-manage>summary').click();await button.click();}
async function sources(page){if(!await page.locator('a[href="/api/company-lists/builtin.csv"]').isVisible())await page.locator('.company-list-source>summary').click();}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.RADAR_CHROME});
 const context=await browser.newContext({viewport:compact?{width:980,height:650}:{width:1366,height:900},acceptDownloads:true});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const page=await context.newPage(),errors=[],dialogs=[];let expectedImportFailure=false;
 page.on('pageerror',error=>errors.push(error.message));page.on('console',m=>{if(m.type()==='error'&&!(expectedImportFailure&&m.location().url.endsWith('/api/company-lists/import/commit')&&m.text().includes('500')))errors.push(m.text());});
 page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.accept();});
 try{
  await page.goto(base);assert.match(await page.title(),/择木/);await page.locator('[data-company-list-example]').waitFor();
  assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  await page.locator('[data-company-list-example]').click();await page.locator('.company-list-entry').first().waitFor();
  assert.match(await page.locator('#content').innerText(),/合成企业示例名单/);
  await sources(page);assert.match(await page.locator('#content').innerText(),/虚构/);
  assert.match(await page.locator('.company-list-entry').first().innerText(),/合成样例一号有限公司/);
  assert.equal((await api('/companies')).length,0);assert.equal((await api('/jobs')).length,0);
  await sources(page);for(const route of ['builtin','template']){
   const pending=page.waitForEvent('download');await page.locator('a[href="/api/company-lists/'+route+'.csv"]').click();
   const download=await pending,bytes=fs.readFileSync(await download.path());assert.equal(bytes.subarray(0,3).toString('hex'),'efbbbf');
   assert.equal(bytes.toString('utf8').trim().split(/\r?\n/).length,route==='builtin'?101:1);
  }
  await screenshot(page,'内置名单');
  for(let i=2;i<=5;i++){await page.locator('[data-company-list-page="'+i+'"]').click();await page.waitForFunction(i=>document.querySelector('.job-list-pagination')?.textContent.includes(i+' / 5'),i);}
  assert.match(await page.locator('.company-list-entry').last().innerText(),/合成样例百号有限公司/);
  for(let i=4;i>=1;i--){await page.locator('[data-company-list-page="'+i+'"]').click();await page.waitForFunction(i=>document.querySelector('.job-list-pagination')?.textContent.includes(i+' / 5'),i);}
  await manage(page,'[data-company-list-new]');await page.locator('#company-list-import-form [name=name]').fill('我的目标企业');
  const names='合成星桥公司\n虚构青穹数据有限公司\n合成星桥公司',input=page.locator('#company-list-import-form [name=content]');
  await input.fill(names);await preview(page);assert.match(await page.locator('[data-list-import-preview]').innerText(),/重复跳过 1 家/);
  await input.fill(names+'\n新加的一行');assert.equal(await page.locator('#company-list-import-form [type=submit]').isDisabled(),true);
  assert.equal(await page.locator('[data-list-import-preview]').innerText(),'');await input.fill(names);await preview(page);
  expectedImportFailure=true;await page.route('**/api/company-lists/import/commit',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'模拟保存失败，请重试'})}));
  await page.locator('#company-list-import-form [type=submit]').click();await page.locator('[data-list-import-error]').filter({hasText:'模拟保存失败'}).waitFor();
  assert.equal(await input.inputValue(),names);assert.equal(await page.locator('#company-list-import-form [type=submit]').innerText(),'确认导入');
  await page.unroute('**/api/company-lists/import/commit');await commit(page);expectedImportFailure=false;
  const custom=(await api('/company-lists')).lists.find(x=>x.name==='我的目标企业');assert.equal(custom.entry_count,2);
  assert.equal((await api('/companies')).length,0);
  const meituan=(await api('/companies',{name:'合成星桥公司',headcount_text:'500人'})).id;
  const tencent=(await api('/companies',{name:'青穹',headcount_text:'100人'})).id;
  const outsourced=(await api('/companies',{name:'外包服务有限公司',headcount_text:'60人'})).id;
  for(const [company_id,title,city,jd_text] of [[meituan,'产品运营实习','杭州','协助业务运营'],[tencent,'财务运营实习','上海','协助报表整理'],[outsourced,'外包项目实习','北京','服务虚构青穹数据有限公司的青穹实验室大厂项目']])await api('/jobs',{company_id,title,city,jd_text,direction:'产品运营'});
  await manage(page,'[data-company-list-refresh]');await page.waitForFunction(()=>document.querySelector('.company-list-stats .stat-card:nth-child(2) .stat-number')?.textContent==='1');
  let detail=await api('/company-lists/'+custom.id),entry=detail.entries[1];const card=()=>page.locator('[data-list-entry="'+entry.id+'"]');
  assert.match(await card().innerText(),/待选择/);await card().locator('details>summary').click();assert.match(await card().innerText(),/青穹/);assert.doesNotMatch(await card().innerText(),/外包服务有限公司/);
  await card().locator('[data-company-list-match]').click();await page.locator('#company-list-match-form [name=company_ids][value="'+tencent+'"]').check();
  await page.locator('#company-list-match-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('.company-list-stats .stat-card:nth-child(2) .stat-number')?.textContent==='2');
  await page.reload();await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-page=companyLists]').click();await page.locator('.company-list-entry').first().waitFor();await selectList(page,custom.id);
  assert.match(await card().innerText(),/沿用已保存的对应关系/);
  await api('/jobs',{company_id:tencent,title:'后来收录的北京实习',city:'北京',direction:'产品运营'});
  await manage(page,'[data-company-list-refresh]');await page.waitForFunction(()=>document.querySelector('.company-list-stats .stat-card:nth-child(3) .stat-number')?.textContent==='3');
  await card().scrollIntoViewIfNeeded();await screenshot(page,'名称对应');
  await page.locator('.company-list-heading [data-company-list-jobs]').click();await rows(page,3);assert.equal(await page.locator('#filter-company-list').inputValue(),String(custom.id));
  assert.doesNotMatch(await page.locator('#job-results').innerText(),/外包项目实习/);
  await page.locator('#filter-city').selectOption('北京');await page.locator('#filter-direction').selectOption('产品运营');await rows(page,1);
  await size(page,'max','200');await size(page,'min','50');await rows(page,1);await page.locator('#job-results').scrollIntoViewIfNeeded();await screenshot(page,'组合筛选');
  await size(page,'max','99');await page.locator('#job-results .empty').waitFor();
  await page.locator('[data-clear-company-list]').click();await rows(page,1);assert.match(await page.locator('#job-results').innerText(),/外包项目实习/);
  await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-page=companyLists]').click();await page.locator('.company-list-entry').first().waitFor();await selectList(page,custom.id);
  await card().locator('[data-company-list-match]').click();await page.locator('[data-list-match-clear]').click();await page.locator('#company-list-match-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('.company-list-stats .stat-card:nth-child(2) .stat-number')?.textContent==='1');
  await card().locator('[data-company-list-match]').click();for(const id of [meituan,tencent])await page.locator('#company-list-match-form [name=company_ids][value="'+id+'"]').check();
  await page.locator('#company-list-match-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  detail=await api('/company-lists/'+custom.id);assert.equal(detail.job_count,3);assert.equal(detail.entries[1].matches.length,2);
  await manage(page,'[data-company-list-new]');await page.locator('#company-list-import-form [name=name]').fill('CSV 名单');await page.locator('#company-list-import-form [name=mode]').selectOption('file');
  const file=page.locator('#company-list-import-form [name=file]');
  await file.setInputFiles({name:'my-list.csv',mimeType:'text/csv',buffer:Buffer.from('\ufeff企业名称,别名,地区\r\n合成星桥公司,合成星桥,北京\r\nCSV 新企业,,上海')});await preview(page);
  await file.setInputFiles({name:'bad.csv',mimeType:'text/csv',buffer:Buffer.from('\ufeff企业名称,地区\n有效公司,北京\n,上海')});assert.equal(await page.locator('#company-list-import-form [type=submit]').isDisabled(),true);
  await preview(page);assert.match(await page.locator('[data-list-import-preview]').innerText(),/不会部分保存/);assert.equal(await page.locator('#company-list-import-form [type=submit]').isDisabled(),true);
  await file.setInputFiles({name:'my-list.csv',mimeType:'text/csv',buffer:Buffer.from('\ufeff企业名称,别名,地区\r\n合成星桥公司,合成星桥,北京\r\nCSV 新企业,,上海')});await preview(page);await commit(page);
  const csvList=(await api('/company-lists')).lists.find(x=>x.name==='CSV 名单');assert.equal(csvList.entry_count,2);
  await manage(page,'[data-company-list-new]');await page.locator('#company-list-import-form [name=name]').fill('TXT 名单');await page.locator('#company-list-import-form [name=mode]').selectOption('file');
  await page.locator('#company-list-import-form [name=file]').setInputFiles({name:'names.txt',mimeType:'text/plain',buffer:Buffer.from('TXT 企业甲\nTXT 企业乙')});await preview(page);await commit(page);
  await manage(page,'[data-company-list-rename]');await page.locator('#company-list-rename-form [name=name]').fill('已重命名的名单');await page.locator('#company-list-rename-form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});
  await page.locator('.company-list-title').filter({hasText:'已重命名的名单'}).waitFor();
  // Hold the write in flight: appending must never target the list being deleted.
  const listUrl='**/api/company-lists/'+await page.locator('#company-list-picker').inputValue();
  let releaseDelete;
  const deletionHeld=new Promise(resolve=>releaseDelete=resolve);
  await page.route(listUrl,async route=>{
   if(route.request().method()==='DELETE')await deletionHeld;
   await route.continue();
  });
  await manage(page,'[data-company-list-delete]');
  try{
   await page.getByRole('status').filter({hasText:'正在删除名单'}).waitFor();
   assert.equal(await page.locator('[data-company-list-append]').count(),0);
  }finally{releaseDelete();}
  await page.getByRole('heading',{name:'合成企业示例名单',exact:true}).waitFor();
  await page.unroute(listUrl);
  await page.locator('[data-company-list-append]').click();
  const appended=page.locator('#company-list-import-form [name=content]');
  await appended.fill('自行添加企业');await preview(page);
  await appended.dispatchEvent('change');
  assert.equal(await page.locator('#company-list-import-form [type=submit]').isEnabled(),true);
  await commit(page);
  assert.match(await page.locator('#content').innerText(),/已自定义/);
  await page.locator('#company-list-filter [name=query]').fill('自行添加企业');await page.locator('#company-list-filter [type=submit]').click();assert.match(await page.locator('.company-list-entry').innerText(),/自行添加/);
  await page.locator('[data-company-list-remove]').click();await page.waitForFunction(()=>document.querySelector('.company-list-stats .stat-number')?.textContent==='100');assert.equal((await api('/company-lists/1')).entry_count,100);
  await manage(page,'[data-company-list-delete]');await page.locator('.list-manage').waitFor();
  await page.reload();await page.locator('.nav-item[data-page=companies]').click();await page.locator('[data-page=companyLists]').click();await page.locator('.list-manage').waitFor();
  assert.equal((await api('/company-lists')).builtin_id,null);
  await manage(page,'[data-company-list-restore]');await page.locator('.company-list-title').filter({hasText:'合成企业示例名单'}).waitFor();
  assert.doesNotMatch(await page.locator('#content').innerText(),/已自定义/);assert.equal((await api('/company-lists')).lists.find(x=>x.builtin).entry_count,100);
  assert.equal((await api('/companies')).length,3);assert.equal((await api('/jobs')).length,4);
  assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  assert.deepEqual(errors,[]);assert.ok(dialogs.every(x=>x.startsWith('删除「')||x.startsWith('移除「')));
  const result={passed:true,browser:'Browser plugin not available; Playwright / Chromium',viewport:compact?'980x650':'1366x900',externalNetwork:'blocked',checks:['complete offline builtin without phantom companies or jobs','BOM original and blank CSV downloads','pasted Excel column, text, CSV and TXT','duplicates and atomic format errors','text and file changes invalidate previews','failed save retains input and retries','exact employer match and brand candidates','manual cancel, correction, multiple employers and reload persistence','new jobs update matches','list, city, direction and headcount compose; zero results recover','rename, append, remove, delete and restore preserve company and job data'],errors,dialogs};
  if(output)fs.writeFileSync(path.join(output,'v0.16.0-company-lists-'+(compact?'compact':'desktop')+'.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){await screenshot(page,'名单检查失败');throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
