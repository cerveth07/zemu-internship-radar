const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const base=process.env.RADAR_PRIVATE_RC_URL,output=process.env.RADAR_SCREENSHOT_DIR;
async function api(route,body){const response=await fetch(base+'/api'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok,route+' '+await response.clone().text());return response.json();}
(async()=>{
 const {readCompanyDOM}=await import(pathToFileURL(path.join(__dirname,'company_profile_dom.mjs')).href);
 const browser=await chromium.launch({headless:true,executablePath:process.env.RADAR_CHROME});
 const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
 try{
  await page.setContent('<div class="sider-company"><div class="company-info"><a href="/gongsi/acme.html">甲公司</a></div><p>公司规模：0–20人</p></div><div class="recommendations">10000人以上</div>');
  let facts=await page.evaluate(readCompanyDOM,{platform:'boss'});
  assert.equal(facts.profile_company,'甲公司');assert.equal(facts.company_headcount,'公司规模：0–20人');
  await page.setContent('<div class="company-banner"><div class="info-primary"><h1 class="name">小米<i></i><div>收藏</div></h1><p>已上市<em></em>10000人以上<em></em><a class="industry-link">互联网</a></p></div></div>');
  facts=await page.evaluate(readCompanyDOM,{platform:'boss',mode:'company'});
  assert.equal(facts.profile_company,'小米');assert.equal(facts.company_headcount,'10000人以上');
  await page.setContent('<h1 class="new_job_name">财务实习</h1><div class="job-about"><a class="com-name">甲公司</a><div class="com-detail"><div>\ue123-\ue124人</div></div></div>');
  await page.evaluate(()=>window.__NUXT__={jobs:[{cname:'甲公司',iname:'财务实习',info:'职责',scale:'15-50人',cuuid:'com_acme',industry:'制造业'},
    {cname:'其他公司',iname:'财务实习',info:'推荐岗位',scale:'10000人以上',cuuid:'com_other'}]});
  facts=await page.evaluate(readCompanyDOM,{platform:'shixiseng',jobId:'inn_current'});
  assert.equal(facts.company_headcount,'15-50人');assert.equal(facts.company_profile_url,'https://www.shixiseng.com/com/com_acme');
  await page.setContent('<div class="recommendation">甲公司 10000人以上</div>');
  assert.deepEqual(await page.evaluate(readCompanyDOM,{platform:'boss'}),{});
  // Render the inbox using a captured-facts fixture; persistence is separately
  // covered by the real adapter -> capture -> company API regression test.
  const batch=await api('/capture/batches',{label:'企业规模展示验证',platform:'shixiseng'});
  await api('/capture/batches/'+batch.id+'/items',{company:'甲公司',title:'财务实习',city:'北京',raw_text:'整理财务报表',source_url:'https://www.shixiseng.com/intern/inn_demo'});
  const items=await api('/capture/batches/'+batch.id+'/items');
  const profile='https://www.shixiseng.com/com/com_acme';
  items[0].draft.company_evidence={company:'甲公司',sector:'制造业',sector_source_url:profile,sector_observed_on:'2026-11-11',
    headcount_text:'15-50人',headcount_min:15,headcount_max:50,headcount_source_url:profile,headcount_observed_on:'2026-11-11'};
  await page.route('**/api/capture/batches/'+batch.id+'/items*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(items)}));
  await page.goto(base);await page.locator('#home-search-form').waitFor();
  assert.match(await page.title(),/择木/);
  await page.locator('.nav-item[data-page=capture]').click();
  await page.locator('.capture-result-detail .company-facts').waitFor();
  const details=page.locator('.capture-result-detail');assert.match(await details.innerText(),/15-50人/);
  assert.equal(await details.getByRole('link',{name:'规模来源 ↗'}).getAttribute('href'),profile);
  await page.locator('.capture-result-detail .company-facts').scrollIntoViewIfNeeded();
  if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'v0.14.2-采集公司规模.png')});}
  await page.setViewportSize({width:980,height:650});
  assert.equal(await page.locator('#content').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  assert.deepEqual(errors,[]);
  const result={passed:true,browser:'Browser plugin not available; Playwright / Chromium',viewports:['1366x900','980x650'],
    checks:['BOSS detail fallback without icons','BOSS company banner','Shixiseng readable state for font labels','ignore other company recommendations','inbox range/date/source display'],errors};
  if(output)fs.writeFileSync(path.join(output,'v0.14.2-company-browser-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
