const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const url=process.env.RADAR_PRIVATE_RC_URL;
const screenshots=process.env.RADAR_SCREENSHOT_DIR;
async function api(route,body,method='POST'){
 const response=await fetch(url+'/api'+route,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 assert.ok(response.ok,route+' '+response.status+' '+await response.clone().text());return response.json();
}
async function shot(page,name){if(screenshots){fs.mkdirSync(screenshots,{recursive:true});await page.screenshot({path:path.join(screenshots,name)});}}
async function close(page){await page.locator('#modal-close').click();await page.locator('#modal').waitFor({state:'hidden'});}
async function personal(page){await page.locator('.nav-item[data-page=skills]').click();await page.locator('[data-workspace-tab][data-page=evidence]').click();}
async function submit(page,id){await page.locator('#'+id+' [type=submit]').click();}
async function openJob(page,id,tab='analysis'){
 await page.locator('.nav-item[data-page=jobs]').click();await page.locator('#content [data-job="'+id+'"]').first().click();
 await page.locator('[data-dossier-tab="'+tab+'"]').click();
}
async function returned(page,tab){await page.locator('#job-read-view:not([hidden]) [data-dossier-tab="'+tab+'"][aria-selected=true]').waitFor();}

async function saveAndReturn(page,dialogs){
 await page.goto(url);await page.locator('.first-run-intro').waitFor();
 await page.locator('.first-run-intro [data-material-import]').click();
 await page.locator('#workspace-import-direct [name=text]').fill('我组织过一次活动。\n\n我整理过项目资料。');
 await submit(page,'workspace-import-direct');await page.locator('#modal').waitFor({state:'hidden'});
 await page.getByRole('button',{name:'查看我的经历',exact:true}).click();
 await page.getByRole('heading',{name:'我的能力与经历',exact:true}).waitFor();
 assert.match(await page.locator('#content').innerText(),/我组织过一次活动/);
 assert.match(await page.locator('#content').innerText(),/我整理过项目资料/);
 const [a,b]=await api('/evidence');assert.equal(a.skill,'');assert.equal(b.skill,'');
 // File and text must survive together, including a new version based on an older snapshot.
 await page.locator('#content [data-material-create]').first().click();
 const pdf=Buffer.from('%PDF-1.4\nresume bytes\n%%EOF');
 await page.locator('#workspace-material-form [name=name]').fill('运营简历');
 await page.locator('#workspace-material-form [name=text]').fill('这版突出产品策划经历');
 await page.locator('#workspace-material-form [name=attachment]').setInputFiles({name:'resume.pdf',mimeType:'application/pdf',buffer:pdf});
 await submit(page,'workspace-material-form');await page.locator('#modal').waitFor({state:'hidden'});
 let version=(await api('/materials')).find(m=>m.name==='运营简历');
 assert.equal((await api('/materials/versions/'+version.id)).text_snapshot,'这版突出产品策划经历');
 const bytes=await fetch(url+'/api/materials/versions/'+version.id+'/download');assert.deepEqual(Buffer.from(await bytes.arrayBuffer()),pdf);
 await api('/materials/versions',{name:'运营简历',material_id:version.material_id,text:'另一版，不能误带这段文字'});
 await page.locator('[data-personal-pane=materials]').click();
 // Reload once to include the newer version while selecting the original row explicitly.
 await page.reload();await page.locator('#home-search-form').waitFor();await personal(page);
 await page.locator('[data-personal-pane=materials]').click();
 await page.locator('[data-material-version="'+version.id+'"]').click();
 await page.waitForFunction(()=>document.querySelector('#workspace-material-form [name=text]')?.value==='这版突出产品策划经历');
 await page.locator('#workspace-material-form [name=text]').fill('这版突出产品策划经历，并补充一项运营项目');
 await submit(page,'workspace-material-form');await page.locator('#modal').waitFor({state:'hidden'});
 const newer=(await api('/materials')).filter(m=>m.material_id===version.material_id)[0];
 assert.equal(newer.filename,'resume.pdf');
 assert.equal((await api('/materials/versions/'+version.id)).text_snapshot,'这版突出产品策划经历');
 const company=await api('/companies',{name:'流程修整测试企业'});
 const job=await api('/jobs',{company_id:company.id,title:'产品运营实习',jd_text:'熟悉 Excel，能整理活动数据。',priority:'high'});
 const rejected=await api('/jobs',{company_id:company.id,title:'已结束的岗位',jd_text:'资料'});
 const extraction=await api('/analysis/packages',{job_ids:[job.id]});
 await api('/requirements/import',{package_id:extraction.package.package_id,dry_run:false,requirements:[{job_id:job.id,requirement_type:'product_skill',name:'Excel 数据整理',strength:'must',evidence:'熟悉 Excel',source_field:'jd_text',option_logic:'single',alternatives:[]}]});
 const req=(await api('/requirements?status=pending')).items.find(r=>r.job_id===job.id);
 await api('/requirements/'+req.id+'/review',{action:'confirm'});
 const pkg=await api('/career-analysis/export',{job_ids:[job.id],evidence_ids:[a.id],goal:'gap',mode:'compact'});
 await api('/evidence/'+a.id,{skill:'',description:'我补充了新的经历说明',expected_revision:a.revision},'PATCH');
 const payload={package_id:pkg.package_id,findings:[{kind:'job_assessment',title:'先整理已有经历',content:'一项准备建议',job_ids:[job.id],assessments:[{requirement_id:req.id,personal_evidence_ids:[a.id],judgment:'direct',explanation:'依据用户提供的能力与经历',next_action:'整理一页活动复盘'}]}]};
 await page.locator('#gpt-json-file').setInputFiles({name:'result.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(payload))});
 await page.locator('[data-gpt-commit]:not([disabled])').waitFor();
 assert.match(await page.locator('#modal-body').innerText(),/个人经历已更新/);
 assert.doesNotMatch(await page.locator('#modal-body').innerText(),/须重新导出/);
 await page.locator('[data-gpt-commit]').click();await page.locator('#modal').waitFor({state:'hidden'});
 // The same result can also be saved from the analysis page with no extra dialogs.
 await page.locator('#analysis-import-json').fill(JSON.stringify(payload));
 await page.locator('[data-action=preview-analysis-import]').click();
 await page.locator('[data-action=commit-analysis-import]:not([disabled])').waitFor();
 await page.locator('[data-action=commit-analysis-import]').click();
 await page.waitForFunction(()=>!document.querySelector('[data-action=commit-analysis-import]')||document.querySelector('[data-action=commit-analysis-import]').disabled);
 assert.equal((await api('/analysis/findings')).length,1);
 let [finding]=await api('/analysis/findings');
 await openJob(page,job.id);
 const older=page.locator('#dossier-analysis .historical-findings');
 if(await older.count())await older.locator(':scope > summary').click();
 const action=page.locator('#dossier-analysis [data-finding-action]').first();
 const originalText=await action.innerText();
 let fail=true;
 const target='**/api/analysis/findings/'+finding.id+'/assessments/'+req.id+'/task';
 await page.route(target,async route=>{if(fail){fail=false;await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'模拟保存失败'})});}else await route.continue();});
 await action.click();await page.getByText('模拟保存失败',{exact:true}).waitFor();
 assert.equal(await action.innerText(),originalText);assert.equal(await action.isEnabled(),true);
 assert.equal((await api('/learning')).length,0);
 await action.click();await returned(page,'tasks');
 assert.equal((await api('/learning')).length,1);
 await page.unroute(target);
 await page.locator('[data-dossier-tab=analysis]').click();
 if(await older.count())await older.locator(':scope > summary').click();
 await page.locator('[data-finding-review="'+finding.id+'"]').click();
 assert.match(await page.locator('.assessment-editor').innerText(),/Excel 数据整理/);
 assert.match(await page.locator('.assessment-editor').innerText(),/熟悉 Excel/);
 const choices=page.locator('[data-evidence-ids]');
 await choices.selectOption([String(a.id),String(b.id)]);
 dialogs.next='dismiss';await page.locator('#modal-close').click();
 assert.equal(dialogs.unsaved,1);assert.equal(await choices.isVisible(),true);
 assert.equal(await choices.locator('option:checked').count(),2);
 dialogs.next='accept';await page.locator('#modal-close').click();await returned(page,'analysis');
 assert.deepEqual((await api('/analysis/findings'))[0].assessments[0].personal_evidence_ids,[a.id]);
 await page.locator('[data-finding-review="'+finding.id+'"]').click();
 await page.locator('[data-next-action]').fill('更新后的准备行动');await submit(page,'workspace-finding-form');await returned(page,'analysis');
 assert.match(await page.locator('#dossier-analysis').innerText(),/更新后的准备行动/);
 await page.locator('[data-dossier-tab=tasks]').click();
 await page.locator('#dossier-tasks [data-prep-complete]').click();
 await page.locator('#workspace-complete-form [data-action=close]').click();await returned(page,'tasks');
 assert.equal((await api('/learning'))[0].status,'todo');
 await page.locator('#dossier-tasks [data-prep-complete]').click();
 await page.locator('#complete-register-evidence').check();
 await page.locator('#workspace-complete-form [name=skills]').fill('');
 await page.locator('#workspace-complete-form [name=description]').fill('我完成了复盘，能力标签以后补');
 await submit(page,'workspace-complete-form');await returned(page,'tasks');
 assert.equal((await api('/evidence'))[0].skill,'');
 await shot(page,'v0.14.1-返回岗位准备任务.png');
 await close(page);
 await api('/jobs/'+job.id+'/application',{stage:'resume_sent',material_version_id:version.id,resume_version:'旧文字备注'} ,'PUT');
 await api('/jobs/'+rejected.id+'/application',{stage:'rejected'},'PUT');
 await page.reload();await page.locator('#home-search-form').waitFor();await page.locator('.nav-item[data-page=applications]').click();
 assert.match(await page.locator('#content').innerText(),/进行中的投递有 1 条未安排/);
 await page.locator('[data-unplanned-applications]').click();assert.equal(await page.locator('#content tbody tr').count(),1);
 const materials=page.locator('#content [data-material-open="'+version.id+'"]');
 assert.equal(await materials.innerText(),version.title);assert.match(await page.locator('#content').innerText(),/旧文字备注/);
 await materials.click();await page.locator('.material-snapshot').waitFor();assert.match(await page.locator('#modal-body').innerText(),/这版突出产品策划经历/);await close(page);
 await page.locator('#application-search-form [name=q]').fill('不存在的关键词');await submit(page,'application-search-form');
 assert.match(await page.locator('#content').innerText(),/没有符合筛选的记录/);assert.doesNotMatch(await page.locator('#content').innerText(),/暂未记录申请进度/);
 await page.locator('[data-clear-application-filters]').click();assert.equal(await page.locator('#content tbody tr').count(),2);
 await shot(page,'v0.14.1-投递实际材料.png');
 assert.equal(dialogs.unexpected.length,0,JSON.stringify(dialogs.unexpected));
}

async function examples(page,dialogs){
 await page.goto(url);await page.locator('[data-install-examples]').click();
 await page.locator('#dossier-analysis:not([hidden]) [data-dossier-adopt]').click();
 await returned(page,'tasks');await close(page);
 assert.equal((await api('/learning')).length,1);
 // Start ordinary use while the example is present, including a name with the
 // same prefix to prove removal is based on ownership, not displayed names.
 await page.locator('#top-add-job').click();
 await page.locator('#quick-job-form [name=company]').fill('【示例】用户自己输入的公司');
 await page.locator('#quick-job-form [name=title]').fill('我自己新增的正式岗位');
 await page.locator('#quick-job-form [name=jd_text]').fill('用户自己的岗位资料');
 await submit(page,'quick-job-form');await page.locator('#job-read-view').waitFor();await close(page);
 await personal(page);await page.locator('#content [data-material-import]').first().click();
 await page.locator('#workspace-import-direct [name=text]').fill('我自己新增的一段经历');
 await submit(page,'workspace-import-direct');await page.locator('#modal').waitFor({state:'hidden'});
 await page.locator('[data-exit-examples]').click();
 await page.locator('[data-exit-examples]').waitFor({state:'hidden'});
 assert.equal((await api('/jobs')).length,1);assert.equal((await api('/evidence')).length,1);assert.equal((await api('/learning')).length,0);
 assert.equal((await api('/materials')).length,1);assert.match(await page.locator('#content').innerText(),/我自己新增的一段经历/);
 await page.locator('.nav-item[data-page=jobs]').click();assert.match(await page.locator('#content').innerText(),/我自己新增的正式岗位/);
 await page.reload();await page.locator('#home-search-form').waitFor();assert.equal(await page.locator('[data-exit-examples]').count(),0);
 await shot(page,'v0.14.1-结束示例保留个人内容.png');
 assert.deepEqual(dialogs.unexpected,[]);
}

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.RADAR_CHROME?{executablePath:process.env.RADAR_CHROME}:{})});
 const page=await browser.newPage({viewport:{width:1366,height:900}});
 const errors=[],dialogs={next:null,unsaved:0,unexpected:[]};
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('500 (Internal Server Error)'))errors.push(message.text());});
 page.on('dialog',async dialog=>{
  if(dialogs.next&&dialog.message().includes('尚未保存')){dialogs.unsaved++;const action=dialogs.next;dialogs.next=null;await dialog[action]();}
  else{dialogs.unexpected.push(dialog.message());await dialog.dismiss();}
 });
 const scenario=process.env.RADAR_SCENARIO||'save-return';
 try{
  await (scenario==='examples'?examples(page,dialogs):saveAndReturn(page,dialogs));
  assert.deepEqual(errors,[]);
  const result={passed:true,scenario,unexpectedDialogs:dialogs.unexpected,unsavedPrompts:dialogs.unsaved,errors,
   browser:'Browser plugin not available; Playwright / Chromium',viewports:['1366x900']};
  if(screenshots)fs.writeFileSync(path.join(screenshots,'v0.14.1-'+scenario+'-check.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }catch(error){await shot(page,'v0.14.1-'+scenario+'-failure.png');throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
