/* Browser plugin not available: existing Playwright, isolated local fixture,
 * all BOSS traffic mocked; never operates a real account. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'radar-favorite-ui-'));
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
async function main(){
 const port=await freePort(),origin='http://127.0.0.1:'+port;
 const server=spawn(process.env.RADAR_PYTHON||path.join(root,'.venv','Scripts','python.exe'),['tools/boss_favorites_ui_fixture.py',String(port)],
  {cwd:root,env:{...process.env,PYTHONPATH:root,RADAR_PRIVATE_RC_DB_PATH:path.join(temp,'radar.db')},stdio:['ignore','ignore','pipe']});
 let log='',browser,page;server.stderr.on('data',data=>log+=data);
 try{
  let ready=false;for(let n=0;n<60;n++){try{ready=(await fetch(origin+'/api/health')).ok}catch{}if(ready)break;await wait(150)}assert(ready,log);
  browser=await chromium.launch({headless:true,...(process.env.RADAR_BROWSER_CHANNEL?{channel:process.env.RADAR_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1400,height:900}});page.setDefaultTimeout(12000);
  const errors=[],external=[],posts=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('status of 400'))errors.push(message.text())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==origin){external.push(url.href);return route.abort()}return route.continue()});
  page.on('request',request=>{if(request.method()==='POST')posts.push(request.url())});
  await page.goto(origin);await page.locator('#home-search-title').waitFor();assert.equal(await page.title(),'择木 · 实习工作台');
  await page.locator('[data-boss-favorite=open]').click();await page.locator('#boss-favorite-import-form').waitFor();
  await page.locator('[name=text]').fill('运营实习\n运营实习');await page.locator('#boss-favorite-import-form [type=submit]').click();
  await page.getByText('识别 1 条 · 重复 1 条 · 格式问题 0 处',{exact:true}).waitFor();
  await page.locator('[name=text]').fill('修改后内容');assert(await page.locator('[data-boss-favorite=create]').isDisabled());
  const copied='企业\t岗位\t城市\t链接\n样例企业\t运营实习\t北京\t\n样例企业\t已关闭岗位\t北京\t';
  await page.locator('[name=text]').fill(copied);await page.locator('#boss-favorite-import-form [type=submit]').click();
  await page.getByText('识别 2 条 · 重复 0 条 · 格式问题 0 处',{exact:true}).waitFor();
  await page.locator('[data-boss-favorite=create]').click();await page.locator('.boss-favorite-target').first().waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-boss-favorite=favorite]')&&!document.querySelector('[data-boss-favorite=favorite]').disabled);
  assert.equal(await page.locator('.boss-favorite-target').count(),2);
  assert.equal(await page.locator('[data-boss-favorite-choice]:checked').count(),1);
  assert.equal(await page.locator('[data-boss-favorite-choice]:disabled').count(),1);
  assert.equal(posts.filter(url=>url.includes('/from-job/')).length,0);
  await page.locator('[data-boss-favorite=clear]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-boss-favorite-choice]:checked').length===0);
  await page.reload();await page.locator('[data-boss-favorite=open]').click();await page.locator('.boss-favorite-target').first().waitFor();
  assert.equal(await page.locator('[data-boss-favorite-choice]:checked').count(),0,'Cleared choices must survive reopening');
  await page.locator('[data-boss-favorite-choice]:not(:disabled)').check();
  await page.waitForFunction(()=>!document.querySelector('[data-boss-favorite=favorite]').disabled);
  await page.locator('[data-boss-favorite=favorite]').click();await page.getByText(/本次操作已收藏/).waitFor();
  assert.equal(await page.locator('[data-boss-favorite-choice]:checked').count(),0);
  for(const [name,width,height] of [['desktop',1400,900],['compact',980,650]]){
   await page.setViewportSize({width,height});await page.locator('.boss-favorite-actions').scrollIntoViewIfNeeded();
   assert(await page.locator('[data-boss-favorite=favorite]').isVisible());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Overflow '+name);
   if(process.env.RADAR_FAVORITE_SCREENSHOTS){fs.mkdirSync(process.env.RADAR_FAVORITE_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.RADAR_FAVORITE_SCREENSHOTS,'boss-favorites-'+name+'.png'),animations:'disabled'})}
  }
  await page.locator('[data-boss-favorite=new]').click();await page.locator('[name=text]').fill('岗位,城市\n运营实习,未知城市');
  await page.locator('#boss-favorite-import-form [type=submit]').click();await page.getByText(/第 2 行：城市无法识别/).waitFor();
  assert(await page.locator('[data-boss-favorite=create]').isDisabled());assert((await page.locator('[name=text]').inputValue()).includes('未知城市'));
  // UTF-8 BOM file import and name-only disambiguation.
  await page.locator('#boss-favorite-file').setInputFiles({name:'titles.csv',mimeType:'text/csv',buffer:Buffer.from('\ufeff岗位\r\n财务分析实习\r\n')});
  await page.locator('#boss-favorite-import-form [type=submit]').click();await page.getByText('识别 1 条 · 重复 0 条 · 格式问题 0 处',{exact:true}).waitFor();
  await page.locator('[data-boss-favorite=create]').click();await page.locator('.boss-favorite-target').first().waitFor();
  await page.getByText('请选择对应职位',{exact:true}).waitFor();assert.equal(await page.locator('[data-boss-favorite-choice]:checked').count(),0);
  assert.equal(await page.locator('.boss-favorite-candidate').count(),2);
  assert.equal((await (await fetch(origin+'/api/jobs')).json()).length,0,'Import/matching must not fabricate jobs');
  // A late batch response must not change the visible list or its action owner.
  const batches=await (await fetch(origin+'/api/boss-favorites/batches')).json();
  let release,entered;const held=new Promise(resolve=>entered=resolve);
  const oldURL=origin+'/api/boss-favorites/batches/'+batches[1].id;
  await page.route(oldURL,async route=>{entered();await new Promise(resolve=>release=resolve);await route.continue()});
  await page.locator('#boss-favorite-batch').selectOption(String(batches[1].id));await held;
  await page.locator('#boss-favorite-batch').selectOption(String(batches[0].id));
  await page.waitForFunction(id=>bossFavorites.current?.batch.id===id,batches[0].id);release();await page.unroute(oldURL);
  await page.waitForFunction(()=>document.querySelectorAll('.boss-favorite-candidate').length===2);
  assert.equal(await page.evaluate(()=>bossFavorites.current.batch.id),batches[0].id);
  // The existing job dossier has the same action and a working return path.
  const post=async(url,body)=>{const response=await fetch(origin+'/api'+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert(response.ok,await response.clone().text());return response.json()};
  const company=await post('/companies',{name:'界面测试本地企业'});
  const job=await post('/jobs',{company_id:company.id,title:'档案收藏测试实习',city:'北京',jd_text:'测试用岗位描述',source_platform:'boss',source_url:'https://www.zhipin.com/job_detail/dossierfixture.html'});
  await page.reload();await page.locator('nav .nav-item[data-page=jobs]').click();await page.locator('[data-job="'+job.id+'"]').first().click();
  await page.locator('[data-boss-favorite=job]').click();await page.getByText(/本次操作已收藏/).waitFor();
  await page.locator('#modal').waitFor({state:'hidden'});
  await page.locator('[data-boss-favorite=back]').click();await page.locator('#modal').waitFor();
  assert.match(await page.locator('#modal').textContent(),/档案收藏测试实习/);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  // Exercise the real DOM click guard on a fully intercepted BOSS-shaped page.
  const {clickFavorite}=await import(require('node:url').pathToFileURL(path.join(root,'tools/boss_favorites_runner.mjs')).href);
  const localBoss=await browser.newPage();let requests=0;
  await localBoss.route('**/*',route=>{requests++;return route.fulfill({contentType:'text/html; charset=utf-8',body:`<h1>测试岗位</h1><a class="btn-interest" href="javascript:;" onclick="this.textContent='取消感兴趣';this.classList.add('active')">感兴趣</a><a id="forbidden" href="/web/geek/chat" onclick="window.hrContacted=true">立即沟通</a>`})});
  const fixtureURL='https://www.zhipin.com/job_detail/localfixture.html';await localBoss.goto(fixtureURL);await clickFavorite(localBoss,fixtureURL);
  assert.equal(await localBoss.locator('.btn-interest').textContent(),'取消感兴趣');assert.equal(await localBoss.evaluate(()=>Boolean(window.hrContacted)),false);assert.equal(requests,1);
  await assert.rejects(()=>clickFavorite(localBoss,fixtureURL),/未找到唯一/);await localBoss.close();
  console.log('PASS: import/preview invalidation/Excel paste/BOM, persistent choices, closed and ambiguous jobs, explicit favorites, compact layout, console, and real DOM click guard; zero external traffic or HR actions.');
 }catch(error){if(page)console.error('UI state:',(await page.locator('body').innerText().catch(()=>'' )).slice(-5500));throw error}
 finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(resolve=>server.once('exit',resolve))}fs.rmSync(temp,{recursive:true,force:true})}
}
main().catch(error=>{console.error(error);process.exitCode=1});
