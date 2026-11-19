const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.RADAR_PLAYWRIGHT_MODULE);
const url=process.env.RADAR_PRIVATE_RC_URL;
const output=process.env.RADAR_SCREENSHOT_DIR;
const shot=async(page,name)=>{if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:require('node:path').join(output,name)});}};
async function api(path,body,method='POST'){
 const r=await fetch(url+'/api'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 assert.ok(r.ok,path+' '+r.status+' '+await r.clone().text());return r.json();
}
async function closed(page){await page.locator('#modal').waitFor({state:'hidden'});}
async function personal(page){await page.locator('.nav-item[data-page=skills]').click();await page.locator('[data-workspace-tab][data-page=evidence]').click();}
async function learning(page){await page.locator('.nav-item[data-page=skills]').click();await page.locator('[data-workspace-tab][data-page=learning]').click();}
(async()=>{
 const b=await chromium.launch({headless:true,executablePath:process.env.RADAR_CHROME});
 const page=await b.newPage({viewport:{width:1366,height:900}});
 const errors=[],dialogs=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss();});
 try{
  await page.goto(url);await page.locator('.first-run-intro').waitFor();
  assert.equal(await page.locator('[data-install-examples]').isVisible(),true);
  await page.locator('.first-run-intro [data-material-import]').click();
  await page.locator('#workspace-import-direct [name=name]').fill('运营经历原稿');
  await page.locator('#workspace-import-direct [name=skills]').fill('Excel, 活动运营');
  await page.locator('#workspace-import-direct [name=text]').fill('校园活动项目\n用 Excel 整理报名数据，完成参与转化分析。');
  await page.locator('#workspace-import-direct [type=submit]').click();await closed(page);
  let [evidence]=await api('/evidence');assert.deepEqual(evidence.skills,['Excel','活动运营']);
  await personal(page);
  await page.locator('[data-evidence-edit="'+evidence.id+'"]').click();
  await page.locator('#workspace-evidence-form [name=description]').fill('用 Excel 整理校园活动报名数据，补充了真实统计口径与局限。');
  await page.locator('#workspace-evidence-form [type=submit]').click();await closed(page);
  assert.equal((await api('/evidence'))[0].id,evidence.id);
  const company=await api('/companies',{name:'流程验收示例企业'}),jobs=[],requirements=[];
  for(const title of ['内容运营实习','用户运营实习','活动运营实习']){
   const job=await api('/jobs',{company_id:company.id,title,direction:'产品运营',priority:'high',jd_text:'熟悉 Excel，能整理活动数据并完成复盘。'});jobs.push(job.id);
   const pkg=await api('/analysis/packages',{job_ids:[job.id]});
   await api('/requirements/import',{package_id:pkg.package.package_id,dry_run:false,requirements:[{job_id:job.id,requirement_type:'product_skill',name:'Excel',strength:'must',evidence:'熟悉 Excel',source_field:'jd_text',option_logic:'single',alternatives:[]}]});
   const row=(await api('/requirements?status=pending')).items.find(r=>r.job_id===job.id);requirements.push(row.id);
   await api('/requirements/'+row.id+'/review',{action:'confirm'});
  }
  const pkg=await api('/career-analysis/export',{job_ids:jobs,evidence_ids:[evidence.id],goal:'gap',mode:'compact'});
  const result={package_id:pkg.package_id,findings:[{kind:'learning_task',title:'补充一份活动复盘',content:'整理已有校园活动中的数据口径、真实结果与局限。',job_ids:jobs,related_skill:'Excel',priority:'high',
   assessments:requirements.map(id=>({requirement_id:id,personal_evidence_ids:[evidence.id],judgment:'transferable',explanation:'已有数据整理经历，业务活动场景仍需核对。',next_action:'完成一页真实活动复盘，标注统计口径和局限。'}))}]};
  await page.locator('#gpt-json-file').setInputFiles({name:'analysis.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(result))});
  await page.locator('[data-gpt-commit]:not([disabled])').waitFor();await page.locator('[data-gpt-commit]').click();await closed(page);
  let [finding]=await api('/analysis/findings');
  await page.locator('.nav-item[data-page=jobs]').click();await page.locator('#content [data-job="'+jobs[0]+'"]').first().click();
  await page.locator('[data-dossier-tab=analysis]').click();
  await page.locator('[data-finding-review="'+finding.id+'"]').click();
  await page.locator('.assessment-editor').first().locator('[data-judgment]').selectOption('direct');
  await page.locator('.assessment-editor').first().locator('[data-explanation]').fill('人工核对：作品中确有报名数据整理，范围与局限已经说明。');
  await page.locator('#workspace-finding-form [name=note]').fill('依据实际作品修订第一项判断。');
  await page.locator('#workspace-finding-form [type=submit]').click();
  await page.locator('#job-read-view:not([hidden]) [data-dossier-tab="analysis"][aria-selected="true"]').waitFor();
  await page.locator('#modal-close').click();await closed(page);
  [finding]=await api('/analysis/findings');assert.equal(finding.assessments[0].judgment,'direct');assert.equal(finding.original_assessments[0].judgment,'transferable');
  await page.locator('#content [data-job="'+jobs[0]+'"]').first().click();await page.locator('[data-dossier-tab=analysis]').click();
  await page.locator('[data-dossier-adopt="'+finding.id+'"]').click();
  await page.locator('#dossier-tasks:not([hidden]) [data-prep-complete]').waitFor();
  await page.locator('#modal-close').click();await learning(page);
  assert.match(await page.locator('#content').innerText(),/覆盖 3 个岗位/);
  await shot(page,'v0.14-准备覆盖.png');
  const task=(await api('/learning'))[0];
  await page.locator('#content [data-prep-complete="'+task.id+'"]').click();
  await page.locator('#complete-register-evidence').check();
  await page.locator('#workspace-complete-form [name=skills]').fill('Excel, 活动复盘');
  await page.locator('#workspace-complete-form [name=description]').fill('实际完成活动复盘报告，包含数据定义、处理过程和局限说明。');
  await page.locator('#workspace-complete-form [name=source_url]').fill('https://example.com/portfolio/review');
  await shot(page,'v0.14-登记成果.png');
  await page.locator('#workspace-complete-form [type=submit]').click();await closed(page);
  const newEvidence=(await api('/evidence')).find(e=>e.id!==evidence.id);assert.ok(newEvidence);assert.equal(newEvidence.review_status,'documented');
  const fresh=await api('/career-analysis/export',{job_ids:jobs,evidence_ids:[newEvidence.id],goal:'gap',mode:'compact'});assert.ok(fresh.package_id);
  await personal(page);await page.locator('[data-material-create]').first().click();
  await page.locator('#workspace-material-form [name=name]').fill('运营简历');
  await page.locator('#workspace-material-form [name=text]').fill('实际投递的运营简历 v1\n校园活动数据整理与复盘。');
  await page.locator('#workspace-material-form [type=submit]').click();await closed(page);
  const material=(await api('/materials')).find(m=>m.name==='运营简历');assert.ok(material);
  await page.locator('.nav-item[data-page=jobs]').click();await page.locator('#content [data-job="'+jobs[0]+'"]').first().click();
  await page.locator('[data-job-reading-apply]').click();
  await page.locator('#job-update-form [name=stage]').selectOption('resume_sent');
  await page.locator('#job-update-form [name=material_version_id]').selectOption(String(material.id));
  await page.locator('#job-update-form [name=next_action]').fill('下次联系时补发活动复盘作品');
  await shot(page,'v0.14-投递材料版本.png');
  await page.locator('#job-update-form [type=submit]').click();await closed(page);
  await api('/materials/versions',{name:'运营简历',material_id:material.material_id,text:'后续新版简历，保留旧投递原件'});
  const application=(await api('/jobs/'+jobs[0])).application;assert.equal(application.material_version_id,material.id);
  await page.locator('.nav-item[data-page=applications]').click();
  assert.match(await page.locator('#content').innerText(),/1 条未安排跟进日期/);
  await page.locator('#application-search-form [name=q]').fill('内容运营');await page.locator('#application-search-form [type=submit]').click();
  await page.locator('#application-stage-filter').selectOption('resume_sent');await page.locator('#application-followup-filter').selectOption('unscheduled');
  assert.equal(await page.locator('#content tbody tr').count(),1);
  await page.locator('.nav-item[data-page=skills]').click();
  await page.locator('[data-gap-expand="excel"]').first().click();
  await page.locator('[data-gap-add-evidence="Excel"]').click();
  assert.equal(await page.locator('#workspace-evidence-form [name=skills]').inputValue(),'Excel');
  assert.equal(await page.locator('#workspace-evidence-form [name=review_status]').count(),0);
  await page.locator('#modal-close').click();
  await page.locator('.nav-item[data-page=sources]').click();await page.locator('[data-agent-access]').first().click();
  assert.doesNotMatch(await page.locator('.agent-access-snippet').innerText(),/<源码目录>/);
  await page.locator('[data-mcp-check]').click();await page.getByText('MCP 握手、工具列表及本机接口访问均已通过',{exact:true}).waitFor();await page.locator('#modal-close').click();
  await page.locator('.nav-item[data-page=overview]').click();await page.setViewportSize({width:980,height:650});
  const box=await page.locator('#home-search-form [type=submit]').boundingBox();assert.ok(box&&box.y+box.height<=650,JSON.stringify(box));
  await page.locator('.nav-item[data-page=jobs]').click();await page.locator('#content [data-job="'+jobs[0]+'"]').first().click();await page.locator('[data-dossier-tab=analysis]').click();
  await page.setViewportSize({width:390,height:650});assert.equal(await page.locator('#modal-body').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  assert.deepEqual(errors,[]);assert.deepEqual(dialogs,[]);
  const report={passed:true,version:(await api('/health')).version,browser:'Browser plugin not available; Playwright / local Chrome',viewports:['1366x900','980x650','390x650'],
   flow:['paste existing experience','save self-described abilities directly','edit without replacing ID','import analysis for 3 jobs','revise judgment while preserving original','shared task shows 3 jobs','complete and explicitly register outcome','new outcome can enter next analysis','application binds immutable material version','keyword/stage/unscheduled filters','MCP handshake/tools/API check'],errors,dialogs};
  if(output)fs.writeFileSync(require('node:path').join(output,'v0.14-browser-check.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1);});
