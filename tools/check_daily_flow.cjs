/* Browser smoke test for the daily job-hunting loop.
   Expects an already-running server on RADAR_PRIVATE_RC_URL with a disposable database; seeds its own data.
   Works with playwright-core when RADAR_PLAYWRIGHT_MODULE points at it (Chromium path via RADAR_CHROME). */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.RADAR_PLAYWRIGHT_MODULE || 'playwright-core');

const url = process.env.RADAR_PRIVATE_RC_URL || 'http://127.0.0.1:18125';

async function api(route, body, method = 'POST') {
  const response = await fetch(url + '/api' + route, body === undefined ? undefined : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    assert.fail(route + ' -> ' + response.status + ' ' + text);
  }
  return response.json();
}
const iso = d => [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
const day = n => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d); };

async function seed() {
  const company = await api('/companies', { name: '冒烟测试企业', city: '北京' });
  const expired = await api('/jobs', {
    company_id: company.id, title: '过期岗位', jd_text: '过期岗位 JD', deadline: day(-3),
    direction: 'pm', status: 'candidate', priority: 'high', city: '北京',
    source_platform: 'manual', source_url: 'https://example.com/smoke/expired',
  });
  const soon = await api('/jobs', {
    company_id: company.id, title: '临期岗位', jd_text: '临期岗位 JD', deadline: day(2),
    direction: 'agent', status: 'candidate', priority: 'medium', city: '上海',
    source_platform: 'manual', source_url: 'https://example.com/smoke/soon',
  });
  const batch = await api('/capture/batches', { label: '冒烟批次', platform: 'manual' });
  for (const index of [1, 2, 3]) {
    await api('/capture/batches/' + batch.id + '/items', {
      raw_text: '冒烟岗位 ' + index + ' 的 JD 原文', title: '冒烟岗位' + index,
      company: '冒烟企业' + index, city: '北京',
      source_url: 'https://example.com/smoke/capture/' + index,
    });
  }
  return { expired, soon, batch };
}

async function checkHomepageSearch(page) {
  const pattern = '**/api/job-search/**', requests = [], unexpected = [];
  let current = { search_run: null, batch: null, items: [], has_more: false, error: '' };
  // Intercept every search endpoint; even an unexpected request must never reach a live collector.
  const fixture = async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const method = request.method(), body = request.postDataJSON();
    requests.push({ path, method, body });
    let result = current, status = 200;
    if (path === '/api/job-search/current' && method === 'GET') {
      // Initial discovery is read-only and has no task until the single start click.
    } else if (path === '/api/job-search/preview' && method === 'POST') {
      result = { ready: { ok: true, messages: [] }, warnings: [], preferences: body,
        summary: { keyword_count: body.keywords.length, city: body.cities.join('、'), cities: body.cities,
          platform_label: 'BOSS 直聘', list_pages: body.keywords.length * body.cities.length * body.max_pages,
          max_jobs: body.max_jobs } };
    } else if (path === '/api/job-search/start' && method === 'POST') {
      current = result = { request_id: body.request_id, batch_id: 900002, batch: { id: 900002 },
        items: [], has_more: false, error: '', search_run: {
          id: 900001, batch_id: 900002, platform: 'boss', status: 'running', total: 0, synced_count: 0,
          preferences: { keywords: body.keywords, cities: body.cities, request_id: body.request_id,
            browser_mode: body.browser_mode },
          platform_state: { boss: { browser_mode: body.browser_mode, phase: 'login_required', tabs_opened: 1 } },
        } };
    } else if (path === '/api/job-search/900001/resume' && method === 'POST') {
      current.search_run.platform_state.boss.phase = 'details';
      current.search_run.total = current.search_run.synced_count = 1;
      current.items = [{ id: 900003, title: '首页冒烟岗位', company: '冒烟企业', city: '北京',
        source_url: 'https://example.com/smoke/home-search', jd_available: true, review_status: 'pending' }];
    } else if (path === '/api/job-search/900001/stop' && method === 'POST') {
      current.search_run.status = 'stopped';
      Object.assign(current.search_run.platform_state.boss, { phase: 'stopped', tabs_closed: 1,
        cleanup_status: 'complete', cleanup_message: '本次任务标签已关闭，其他标签保持原样。' });
    } else if (path !== '/api/job-search/900001/refresh' || method !== 'POST') {
      unexpected.push(method + ' ' + path); status = 501;
      result = { detail: 'Unexpected search request in browser fixture' };
    }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(result) });
  };
  await page.route(pattern, fixture);
  try {
    await page.setViewportSize({width:980,height:650});
    await page.waitForSelector('#home-search-keywords');
    assert.equal(await page.locator('.home-search-fields').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),1);
    await page.locator('.home-search-options > summary').click();
    const mode = '#home-search-form [name="browser_mode"]';
    assert.equal(await page.inputValue(mode), 'managed');
    assert.match(await page.locator('#home-search-browser-help').innerText(), /无需安装扩展/);
    await page.selectOption('#home-search-form [name="platform"]', 'shixiseng');
    assert.equal(await page.locator('#home-search-browser-choice').isVisible(), false);
    await page.selectOption('#home-search-form [name="platform"]', 'boss');
    assert.equal(await page.inputValue(mode), 'managed', '切换平台应保留手动选择的检索方式');
    await page.selectOption(mode, 'chrome_use');
    await page.click('#home-search-connection summary');
    assert.equal(await page.locator('#home-search-connection a').first().getAttribute('href'), 'https://chrome-use.leeguoo.com/');
    assert.equal(await page.locator('#home-search-connection a').nth(1).getAttribute('href'),
      'https://chromewebstore.google.com/detail/chrome-use/knfcmbamhjmaonkfnjhldjedeobeafmk');
    await page.fill('#home-search-keywords', '手动输入岗位');
    assert.match(await page.locator('#home-search-scope').innerText(), /1 个关键词/);
    await page.locator('#home-search-file').setInputFiles({ name: 'keywords.csv', mimeType: 'text/csv',
      buffer: Buffer.from('\ufeffkeyword\r\nAI 产品实习\r\nAgent  产品\r\nai 产品实习\r\n', 'utf8') });
    await page.waitForSelector('#home-search-import-note:not([hidden])');
    const keywords = 'AI 产品实习\nAgent  产品';
    assert.equal(await page.inputValue('#home-search-keywords'), keywords);
    assert.match(await page.locator('#home-search-import-note').innerText(), /合并 1 行.*尚未开始/);
    await page.locator('#home-search-file').setInputFiles({ name: 'invalid.csv', mimeType: 'text/csv',
      buffer: Buffer.from('wrong_header\n不应覆盖原输入', 'utf8') });
    await page.waitForSelector('#home-search-error:not([hidden])');
    assert.match(await page.locator('#home-search-error').innerText(), /keyword/);
    assert.equal(await page.inputValue('#home-search-keywords'), keywords, '非法文件必须保留已有关键词');
    assert.equal(requests.filter(r => r.method === 'POST').length, 0, '输入和导入不能启动检索');
    // Several cities: one common city by click, one uncommon city typed and confirmed with Enter.
    if(await page.locator('.home-search-options').evaluate(el=>el.open))await page.locator('.home-search-options > summary').click();
    await page.locator('.home-search-city-select > summary').click();
    await page.click('[data-home-search="toggle-city"][data-city="上海"]');
    await page.fill('#home-search-city-input', '阿里地区');
    await page.press('#home-search-city-input', 'Enter');
    await page.waitForSelector('[data-home-search="remove-city"][data-city="阿里地区"]');
    assert.equal(await page.inputValue('#home-search-city-input'), '');
    assert.match(await page.locator('#home-search-scope').innerText(), /2 个关键词 × 3 个城市 × 每词 1 页 = 6 \/ 12 页/);
    assert.equal(requests.filter(r => r.method === 'POST').length, 0, '回车添加城市不能提交检索');

    // A pending input that does not match one suggestion must also be used on submit.
    await page.fill('#home-search-city-input', '昆明、昆明');
    await page.locator('.home-search-city-select > summary').click();
    await page.locator('#content').evaluate(el=>{el.scrollTop=0});
    const submit=page.locator('#home-search-form button[type="submit"]');
    assert(await submit.evaluate(el=>{
      const rect=el.getBoundingClientRect(),content=document.querySelector('#content');
      const hit=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
      return content.scrollTop===0&&rect.bottom<=innerHeight&&(hit===el||el.contains(hit));
    }),'开始检索在 980 × 650 首屏应无需滚动即可点击');
    await submit.click();
    await page.waitForSelector('#home-search-current [data-home-search="resume"]');
    assert.match(await page.locator('#home-search-progress-title').innerText(), /完成登录/);
    assert.match(await page.locator('#home-search-current').innerText(), /Chrome 中的“择木”任务标签/);
    assert.equal(await page.locator('#home-search-keywords').isDisabled(), true);
    const starts = requests.filter(r => r.path === '/api/job-search/start');
    assert.equal(starts.length, 1, '单击开始只发送一次启动请求');
    assert.equal(requests.filter(r => r.path === '/api/job-search/preview').length, 1);
    assert.equal(requests.find(r => r.path === '/api/job-search/preview').body.browser_mode, 'chrome_use');
    assert.deepEqual(starts[0].body.keywords, keywords.split('\n'));
    assert.deepEqual(starts[0].body.cities, ['北京', '上海', '阿里地区', '昆明']);
    assert.equal(starts[0].body.browser_mode, 'chrome_use');
    assert.equal(starts[0].body.confirmed, true);
    assert.match(starts[0].body.request_id, /^[a-f0-9-]{36}$/i);
    await page.click('#home-search-current [data-home-search="resume"]');
    await page.waitForSelector('#home-search-current .home-search-result-preview');
    await page.click('.home-search-result-preview > summary');
    await page.waitForSelector('#home-search-current .home-search-result');
    assert.match(await page.locator('#home-search-current').innerText(), /首页冒烟岗位/);
    assert.equal(await page.locator('.home-search-saved strong').innerText(), '1');
    await page.click('#home-search-current [data-home-search="stop"]');
    await page.waitForFunction(() => document.querySelector('#home-search-progress-title')?.textContent.includes('已停止'));
    assert.match(await page.locator('#home-search-current').innerText(), /首页冒烟岗位/);
    assert.match(await page.locator('.home-search-cleanup').innerText(), /本次任务标签已关闭/);
    assert.equal(await page.locator('#home-search-keywords').isDisabled(), false);
    assert.equal(await page.locator('#home-search-current [data-home-search="stop"]').count(), 0);
    for (const action of ['resume', 'stop']) {
      const calls = requests.filter(r => r.path === '/api/job-search/900001/' + action);
      assert.equal(calls.length, 1); assert.equal(calls[0].body.confirmed, true);
    }
    assert.equal(requests.filter(r => r.path === '/api/job-search/start').length, 1);
    assert.deepEqual(unexpected, []);
  } finally {
    // The UI is left stopped with its result retained; subsequent flows use the real disposable DB.
    await page.unroute(pattern, fixture);
    await page.setViewportSize({width:1360,height:950});
  }
}

async function run() {
  const seeded = await seed();
  const browser = await chromium.launch({ headless: true, executablePath: process.env.RADAR_CHROME });
  const page = await browser.newPage({ viewport: { width: 1360, height: 950 } });
  const dialogs = [];
  const errors = [];
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.accept() });
  page.on('pageerror', error => errors.push(String(error)));
  const openModal = async () => page.waitForFunction(() => document.querySelector('#modal')?.open);
  const closedModal = async () => page.waitForFunction(() => !document.querySelector('#modal')?.open);
  const editorOpen = async () => page.waitForFunction(() => {
    const area = document.querySelector('#job-edit-area');
    return Boolean(area) && !area.hidden;
  });

  await page.goto(url);
  await page.waitForSelector('button[data-page="jobs"]');
  const beforeHomeDialogs = dialogs.length;
  await checkHomepageSearch(page);
  assert.equal(dialogs.length, beforeHomeDialogs, '首页单击开始不应要求额外确认');

  // 1. deadline column shows urgency in the list
  await page.click('button[data-page="jobs"]');
  await page.waitForSelector('table.table tbody tr');
  assert.match(await page.locator('tr', { hasText: '过期岗位' }).first().innerText(), /已过期/);
  assert.match(await page.locator('tr', { hasText: '临期岗位' }).first().innerText(), /即将截止/);

  // 2. still-open deadlines precede historical expired rows
  if(!await page.locator('#job-more-filters').evaluate(el=>el.open))await page.locator('#job-more-filters>summary').click();await page.selectOption('#filter-sort', 'deadline');
  await page.waitForTimeout(300);
  const firstRow = await page.locator('table.table tbody tr').first().innerText();
  assert.ok(firstRow.includes('临期岗位'), '按截止排序后第一行应为临期岗位，实际：' + firstRow);

  // 3. "只看 7 天内截止" narrows the list
  if(!await page.locator('#job-more-filters').evaluate(el=>el.open))await page.locator('#job-more-filters>summary').click();await page.check('#filter-soon');
  await page.waitForTimeout(300);
  const visibleRows = await page.locator('table.table tbody tr').count();
  assert.equal(visibleRows, 1, '7 天内截止应只剩未过期的一条，实际 ' + visibleRows);
  await page.uncheck('#filter-soon');

  // 4. add-job form does not preselect a company
  await page.click('#top-add-job');
  await openModal();
  await page.waitForSelector('#quick-job-form');
  assert.equal(await page.locator('#quick-job-form [required]').count(), 3);
  await page.click('[data-advanced-job]');
  assert.equal(await page.inputValue('#job-form [name="company_id"]'), '');
  assert.equal(await page.inputValue('#job-form [name="direction"]'), 'other');
  await page.click('#modal-close');
  await closedModal();

  // 5. reading view shows the deadline once in the info grid and offers "记录投递"
  await page.click('button[data-job="' + seeded.expired.id + '"]');
  await openModal();
  assert.equal(await page.locator('.info-item small', { hasText: '截止日期' }).count(), 1,
    '截止日期只在信息网格出现一次');
  assert.equal(/截止/.test(await page.locator('.job-read-meta').innerText()), false,
    '元信息行不应重复截止日期');
  assert.ok(await page.locator('.job-deadline-notice').count(), '过期岗位应给出警示');
  await page.click('button[data-job-reading-apply]');
  await editorOpen();
  assert.equal(await page.getAttribute('[data-detail-tab="application"]', 'aria-selected'), 'true');
  await page.click('#modal-close');
  await closedModal();

  // 6. tracking page can pick a job and save a stage
  await page.click('button[data-page="applications"]');
  await page.click('.empty button[data-action="choose-apply-job"]');
  await openModal();
  await page.fill('#apply-job-search', '冒烟测试企业 临期');
  assert.equal(await page.locator('[data-apply-job]').count(), 1);
  const beforeSearchClose = dialogs.length;
  await page.click('#modal-close');
  await closedModal();
  assert.equal(dialogs.length, beforeSearchClose, '搜索不应触发放弃修改确认');
  await page.click('.empty button[data-action="choose-apply-job"]');
  await page.fill('#apply-job-search', '临期');
  await page.click('button[data-apply-job="' + seeded.soon.id + '"]');
  await editorOpen();
  // A different window changes the JD after this form opened.
  await api('/jobs/' + seeded.soon.id, { jd_text: '另一窗口更新的 JD' }, 'PATCH');
  await page.selectOption('#job-update-form [name="stage"]', 'resume_sent');
  await page.click('#job-update-form button[type="submit"]');
  await closedModal();
  await page.waitForFunction(() => document.body.innerText.includes('已发简历'));
  assert.equal((await api('/jobs/' + seeded.soon.id)).jd_text, '另一窗口更新的 JD');

  // Conflicts retain the user's typed text instead of silently overwriting the latest record.
  await page.click('button[data-job="' + seeded.soon.id + '"]');
  await page.click('[data-job-reading-apply]');
  await page.fill('#job-update-form [name="next_action"]', '我的面试计划');
  await api('/jobs/' + seeded.soon.id, { application: { stage: 'interview', next_action: '其他窗口的计划' } }, 'PATCH');
  await page.click('#job-update-form button[type="submit"]');
  await page.waitForFunction(() => document.querySelector('#job-update-form #form-error')?.textContent.includes('其他窗口更新'));
  assert.equal(await page.inputValue('#job-update-form [name="next_action"]'), '我的面试计划');
  assert.equal((await api('/jobs/' + seeded.soon.id)).application.next_action, '其他窗口的计划');
  await page.click('#modal-close');
  await closedModal();

  // 7. intake keeps other ticks when one row is corrected
  await page.click('button[data-page="capture"]');
  await page.waitForSelector('button[data-capture-select="' + seeded.batch.id + '"]');
  await page.click('button[data-capture-select="' + seeded.batch.id + '"]');
  await page.click('button[data-capture-bulk-review]');
  await page.waitForSelector('#capture-intake-form');
  const boxes = page.locator('#capture-intake-form [data-intake-select]:not(:disabled)');
  assert.equal(await boxes.count(), 3);
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await boxes.nth(2).check();
  const changedId = Number(await boxes.nth(1).getAttribute('data-intake-select'));
  const before = dialogs.length;
  await page.locator('#capture-intake-form [data-intake-edit]').first().click();
  await page.waitForSelector('#capture-review-form');
  assert.equal(dialogs.length, before, '进入单条核对不应弹确认框');
  await page.fill('#capture-review-form [name="company"]', '冒烟企业甲');
  await page.fill('#capture-review-form [name="title"]', '冒烟岗位甲');
  await page.fill('#capture-review-form [name="jd_text"]', '冒烟岗位甲的 JD 原文');
  await api('/capture/items/' + changedId, { jd_text: '其他窗口修改后的原文，需重新核对' }, 'PATCH');
  await page.click('[data-capture-save-draft]');
  await page.waitForSelector('#capture-intake-form');
  assert.equal(await boxes.count(), 3, '保存草稿后仍保留编辑行');
  assert.equal(await page.locator('[data-intake-select]:checked').count(), 1, '只恢复未变更的第三行');
  assert.match(await page.locator('#capture-intake-return-notice').innerText(), /1 条.*已变化/);
  assert.equal(await page.locator('[data-intake-select="' + changedId + '"]').isChecked(), false);
  // Closing a single review returns to the table and preserves remaining reviewed rows.
  await page.locator('[data-intake-edit]').first().click();
  await page.waitForSelector('#capture-review-form');
  await page.click('#modal-close');
  await page.waitForSelector('#capture-intake-form');
  assert.equal(await page.locator('[data-intake-select]:checked').count(), 1);
  await page.locator('[data-intake-edit]').first().click();
  await page.waitForSelector('#capture-review-form');
  await page.click('#capture-review-form button[type="submit"]');
  await page.waitForSelector('#capture-intake-form', { timeout: 20000 });
  await page.waitForFunction(() => /已核对 1 条/.test(document.querySelector('#capture-intake-count')?.textContent || ''));
  assert.equal(await page.locator('#capture-intake-form [data-intake-select]:checked').count(), 1);
  await page.click('#modal [data-action="close"]');
  await closedModal();

  // First-use entry creates the company together with the job and opens reading view.
  await page.click('#top-add-job');
  await page.fill('#quick-job-form [name=company]', '新人企业');
  await page.fill('#quick-job-form [name=title]', '第一条实习岗位');
  await page.fill('#quick-job-form [name=jd_text]', '需要熟悉 Python，每周到岗三天。');
  await page.click('#quick-job-form [type=submit]');
  await page.waitForSelector('#job-read-view');
  assert.match(await page.locator('.detail-title').innerText(), /第一条实习岗位/);
  await page.click('#modal-close');
  await closedModal();
  await page.click('[data-page=sources]');
  assert.equal(await page.locator('[data-restore-backup]').isDisabled(), true, '浏览器不会在线替换数据库');
  // Legacy connector controls belong to the Data and Help page.
  assert.equal(await page.locator('[data-boss-settings]').isVisible(), false, '历史连接器默认收起');
  await page.click('#support-advanced > summary');
  await page.click('[data-boss-settings]');
  await page.waitForSelector('[data-quick-job]');
  assert.match(await page.locator('#modal-body').innerText(), /浏览器模式/);
  await page.click('#modal-close');
  await closedModal();
  await page.click('#support-advanced > summary');
  assert.equal(await page.locator('[data-boss-settings]').isVisible(), false);

  // 8. first JD-extraction run keeps four steps and hides batch review
  await page.click('button[data-page="analysis"]');
  await page.waitForSelector('button[data-analysis-flow="extract"]');
  await page.click('button[data-analysis-flow="extract"]');
  await page.waitForSelector('section.workflow-step');
  const extractText = await page.locator('#content').innerText();
  for (const step of ['第一阶段', '第二阶段：交给 AI 助手处理', '第三阶段：导回结果', '第四阶段：人工核对候选要求']) {
    assert.ok(extractText.includes(step), '缺少步骤：' + step);
  }
  assert.equal(await page.locator('#ai-review-panel').count(), 0, '没有候选时不应出现批量复核');

  // Check the current navigation and quick-entry dialog at desktop and narrow widths.
  for (const width of [1320, 980, 390]) {
    await page.setViewportSize({width, height: 844});
    await page.waitForFunction(w => document.querySelector('#sidebar').inert === (w <= 760), width);
    for (const name of ['overview', 'capture', 'jobs', 'companies', 'analysis', 'skills', 'applications', 'sources']) {
      if (width <= 760) await page.click('#nav-toggle');
      await page.click(`.nav-item[data-page="${name}"]`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name} overflows at ${width}`);
    }
    await page.click('#top-add-job');
    assert.ok(await page.locator('#quick-job-form').isVisible());
    assert.ok(await page.evaluate(() => document.querySelector('#modal').getBoundingClientRect().right <= innerWidth), `Quick entry overflows at ${width}`);
    await page.keyboard.press('Escape');
  }
  await page.setViewportSize({width:1320, height:844});
  await page.waitForFunction(() => !document.querySelector('#sidebar').inert);

  // Grouped routes keep their sidebar parent selected.
  await page.locator('.nav-item[data-page=skills]').click();
  await page.locator('[data-workspace-tab][data-page=evidence]').click();
  assert.equal(await page.locator('.nav-item[aria-current=page]').getAttribute('data-page'),'skills');
  await page.locator('[data-workspace-tab][data-page=learning]').click();
  assert(await page.getByRole('heading',{name:'准备任务与成果'}).isVisible());
  await page.locator('.nav-item[data-page=overview]').click();
  await page.locator('#home-search [data-page=planner]').click();
  await page.locator('[data-planner-tab=library]').waitFor();
  await page.locator('[data-planner-tab=plan]').click();
  assert(await page.getByRole('heading',{name:'已保存的计划'}).isVisible());
  await page.locator('.nav-item[data-page=sources]').click();
  await page.locator('#import-file').setInputFiles({name:'duplicate.csv',mimeType:'text/csv',
    buffer:Buffer.from('company,title,city,jd_text,source_url\n冒烟测试企业,过期岗位,北京,候选岗位资料,https://example.com/smoke/duplicate\n','utf8')});
  await page.locator('[data-action=preview-import]').click();
  await page.getByRole('button',{name:'提交重复审核'}).click();
  await page.locator('[data-workspace-tab][data-page=reviews][aria-current=page]').waitFor();
  assert.equal(await page.locator('.nav-item[aria-current=page]').getAttribute('data-page'),'capture');
  assert.equal(await page.locator('[data-review-compare]').count(),1);
  await page.locator('[data-workspace-tab][data-page=capture]').click();
  assert(await page.locator('#capture-batch-form').isVisible());

  // Native bridge contract: actual offline replacement is covered by Python/controller tests.
  await page.evaluate(() => {
    window.nativeCalls=[];
    window.pywebview={api:{
      boss_settings:async()=>({wsl_available:false,configured:false,distro:'',script:''}),
      save_boss_settings:async(distro,script)=>{window.nativeCalls.push(['boss',distro,script]);return {ok:true}},
      choose_restore_backup:async()=>({name:'test.zip',jobs:2,companies:1,applications:0,token:'preview-token'}),
      confirm_restore:async token=>{window.nativeCalls.push(['restore',token]);return {ok:true}},
    }};
  });
  await page.click('[data-page=sources]');
  await page.click('#support-advanced > summary');
  await page.click('[data-boss-settings]');
  assert.match(await page.locator('#modal-body').innerText(), /尚未安装 WSL/);
  await page.fill('#boss-settings-form [name=distro]', 'My-Linux');
  await page.fill('#boss-settings-form [name=script]', '/home/me/radar/tools/start-boss-chain.sh');
  await page.click('#boss-settings-form [type=submit]');
  await closedModal();
  await page.click('[data-restore-backup]');
  await page.waitForSelector('#restore-confirm-form');
  assert.match(await page.locator('#modal-body').innerText(), /2 个岗位/);
  await page.click('#restore-confirm-form [type=submit]');
  assert.equal(await page.evaluate(()=>window.nativeCalls.some(x=>x[0]==='restore')), false);
  await page.check('#restore-confirm-form [name=confirmed]');
  await page.click('#restore-confirm-form [type=submit]');
  await page.waitForFunction(()=>window.nativeCalls.some(x=>x[0]==='restore'));
  assert.equal(await page.evaluate(()=>window.nativeCalls.find(x=>x[0]==='restore')[1]), 'preview-token');

  assert.deepEqual(errors, [], '页面报错：' + errors.join(' | '));
  await browser.close();
  console.log('daily-flow smoke passed');
}

run().catch(error => { console.error(error); process.exit(1) });
