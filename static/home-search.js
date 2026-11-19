/* First-page search: the existing capture task owns execution and saved results. */
const HOME_SEARCH_KEY = 'radar.home-search.v1';
const HOME_SEARCH_ACTIVE = new Set(['starting', 'pending', 'running', 'stopping', 'unknown']);
const HOME_SEARCH_LIMITS = {keywords: 9, length: 80, city: 60, cities: 9, combinations: 9, pages: 12, fileBytes: 64 * 1024};
const HOME_SEARCH_COMMON_CITIES = ['北京', '上海', '深圳', '杭州', '广州', '成都', '南京', '武汉', '西安', '苏州'];
// BOSS's supported city names, loaded read-only from the API for suggestions and early checks.
const homeSearchCityTable = {boss: [], loaded: false};
const homeSearch = {
 form: {keywords: '', platform: 'boss', browser_mode: 'managed', cities: ['北京'], max_pages: 1, max_jobs: 30},
 preview: null, previewPayload: null, attempt: null, busy: '', error: '', importNote: '', cityInput: '',
 current: {search_run: null, batch: null, items: [], has_more: false, error: ''},
 checked: false, currentError: '', failures: 0, revision: 0, advanced: false, resultPreview: false, connectionOpen: false, cleanupWait: null,
};
let homeSearchPoll = null, homeSearchRequest = null;

function restoreHomeSearchDraft() {
 try {
  const saved = JSON.parse(localStorage.getItem(HOME_SEARCH_KEY) || 'null');
  if (!saved || typeof saved !== 'object') return;
  const value = saved.form || {};
  if (typeof value.keywords === 'string' && value.keywords.length <= HOME_SEARCH_LIMITS.fileBytes) homeSearch.form.keywords = value.keywords;
  if (['boss', 'shixiseng'].includes(value.platform)) homeSearch.form.platform = value.platform;
  if (['chrome_use', 'managed'].includes(value.browser_mode)) homeSearch.form.browser_mode = value.browser_mode;
  // Drafts saved before multi-city selection stored a single ``city`` string.
  const cities = Array.isArray(value.cities) ? value.cities : typeof value.city === 'string' ? [value.city] : null;
  if (cities && cities.length <= HOME_SEARCH_LIMITS.cities && cities.every(city => typeof city === 'string' && [...city].length <= HOME_SEARCH_LIMITS.city)) {
   homeSearch.form.cities = [...new Set(cities.map(city => city.trim()).filter(Boolean))];
  }
  if (Number.isInteger(value.max_pages) && value.max_pages >= 1 && value.max_pages <= 3) homeSearch.form.max_pages = value.max_pages;
  if (Number.isInteger(value.max_jobs) && value.max_jobs >= 1 && value.max_jobs <= 60) homeSearch.form.max_jobs = value.max_jobs;
  const attempt = saved.attempt;
  if (attempt && /^[a-f0-9-]{36}$/i.test(attempt.id) && typeof attempt.key === 'string' && attempt.key.length < 5000) homeSearch.attempt = attempt;
 } catch { /* A damaged or unavailable local draft does not block search. */ }
}
function saveHomeSearchDraft() {
 try { localStorage.setItem(HOME_SEARCH_KEY, JSON.stringify({form: homeSearch.form, attempt: homeSearch.attempt})); } catch {}
}
restoreHomeSearchDraft();

function normalizeHomeKeywords(lines) {
 const keywords = [], seen = new Set(), errors = [];
 let duplicates = 0, blanks = 0;
 for (const [index, raw] of lines.entries()) {
  const value = String(raw).trim();
  if (!value) { blanks++; continue; }
  if ([...value].length > HOME_SEARCH_LIMITS.length || /[\u0000-\u001f\u007f]/.test(value)) {
   errors.push(index + 1); continue;
  }
  const key = value.toLowerCase();
  if (seen.has(key)) { duplicates++; continue; }
  seen.add(key); keywords.push(value);
 }
 return {keywords, duplicates, blanks, errors};
}
// Keywords and cities may be separated by new lines, commas, 顿号 or semicolons.
function splitHomeEntries(text) {
 return String(text ?? '').split(/\r\n|\n|\r|[,，;；、]/);
}
function homeSearchKeywordList() {
 return normalizeHomeKeywords(splitHomeEntries(homeSearch.form.keywords)).keywords;
}
// Every keyword is searched in every city: at most 9 keyword × city pairs and 12 list pages.
function homeSearchBudget(form = homeSearch.form) {
 const keywords = normalizeHomeKeywords(splitHomeEntries(form.keywords)).keywords.length;
 const cities = form.cities.length, pages = Number(form.max_pages) || 1;
 const perKeyword = Math.max(1, cities) * pages;
 const maxKeywords = Math.max(0, Math.min(HOME_SEARCH_LIMITS.keywords,
  Math.floor(HOME_SEARCH_LIMITS.combinations / Math.max(1, cities)), Math.floor(HOME_SEARCH_LIMITS.pages / perKeyword)));
 const combinations = keywords * cities, listPages = combinations * pages;
 let over = '';
 if (cities > HOME_SEARCH_LIMITS.cities) over = `已选 ${cities} 个城市，每次最多 9 个。`;
 else if (combinations > HOME_SEARCH_LIMITS.combinations) over = `关键词 × 城市共 ${combinations} 组，每次最多 9 组。请减少关键词或城市。`;
 else if (listPages > HOME_SEARCH_LIMITS.pages) over = `共需 ${listPages} 页列表，每次最多 12 页。请减少关键词、城市或每词页数。`;
 return {keywords, cities, pages, combinations, listPages, maxKeywords, over};
}
function homeSearchCountText() {
 const budget = homeSearchBudget();
 const limit = budget.cities ? `按当前 ${budget.cities} 个城市、每词 ${budget.pages} 页，最多 ${budget.maxKeywords} 个` : '先选择城市';
 return `已填 ${budget.keywords} 个 · ${limit}`;
}
function normalizeHomeCity(value) {
 const city = String(value ?? '').trim();
 if (!city) return '';
 if ([...city].length > HOME_SEARCH_LIMITS.city || /[\u0000-\u001f\u007f]/.test(city)) {
  throw new Error('城市名称最多 60 字，不能包含制表符或控制字符。');
 }
 return city;
}
function homeSearchUnsupportedCities(cities = homeSearch.form.cities) {
 if (homeSearch.form.platform !== 'boss' || !homeSearchCityTable.loaded || !homeSearchCityTable.boss.length) return [];
 const known = new Set(homeSearchCityTable.boss);
 return cities.filter(city => !known.has(city));
}
function parseHomeKeywordFile(text, name) {
 if (typeof text !== 'string' || text.length > HOME_SEARCH_LIMITS.fileBytes) throw new Error('关键词文件过大，请使用不超过 64 KB 的文件。');
 const kind = String(name).toLowerCase().split('.').pop();
 if (!['csv', 'txt'].includes(kind)) throw new Error('请选择 CSV 模板或每行一个关键词的 TXT 文件。');
 let lines = text.replace(/^\ufeff/, '').split(/\r\n|\n|\r/);
 // One field per physical row: quoted commas and escaped quotes are supported.
 // A keyword cannot itself contain a line break or extra CSV columns.
 if (kind === 'csv') {
  lines = lines.map((line, index) => {
   const value = line.trim();
   if (!value) return '';
   if (value.startsWith('"')) {
    if (!/^"(?:[^"]|"")*"$/.test(value)) throw new Error(`CSV 第 ${index + 1} 行格式无效：每行只填写一列 keyword。`);
    return value.slice(1, -1).replaceAll('""', '"');
   }
   if (/[,\"]/.test(value)) throw new Error(`CSV 第 ${index + 1} 行有多列或引号错误，请按模板每行填写一个关键词。`);
   return value;
  });
  if (lines[0]?.toLowerCase() !== 'keyword') throw new Error('CSV 第一行必须是 keyword，下面每行填写一个关键词。可先下载模板。');
  lines = lines.slice(1);
 }
 const parsed = normalizeHomeKeywords(lines);
 if (parsed.errors.length) {
  const rows = parsed.errors.slice(0, 5).map(number => number + (kind === 'csv' ? 1 : 0));
  throw new Error(`有 ${parsed.errors.length} 行关键词无效（第 ${rows.join('、')} 行）：每项最多 80 字，不能包含制表符或控制字符。原输入已保留。`);
 }
 if (!parsed.keywords.length) throw new Error('文件里没有可用关键词，原输入已保留。');
 if (parsed.keywords.length > HOME_SEARCH_LIMITS.keywords) throw new Error(`去重后有 ${parsed.keywords.length} 个关键词；每次最多 9 个，请分批导入。原输入已保留。`);
 return parsed;
}
function homeSearchPayload() {
 const form = homeSearch.form;
 const parsed = normalizeHomeKeywords(splitHomeEntries(form.keywords));
 if (parsed.errors.length) throw new Error(`第 ${parsed.errors.slice(0, 5).join('、')} 项关键词无效；每项最多 80 字，不能包含制表符或控制字符。`);
 if (!parsed.keywords.length) throw new Error('请先输入至少一个岗位关键词，每行一个，或用逗号分隔。');
 if (parsed.keywords.length > HOME_SEARCH_LIMITS.keywords) throw new Error('每次最多检索 9 个不同关键词，请分批处理。');
 const cities = [...new Set(form.cities.map(normalizeHomeCity).filter(Boolean))];
 const pages = Number(form.max_pages), jobs = Number(form.max_jobs);
 if (!cities.length) throw new Error('请至少选择或填写一个城市，例如北京。');
 if (!['boss', 'shixiseng'].includes(form.platform)) throw new Error('请选择 BOSS 直聘或实习僧。');
 const unsupported = homeSearchUnsupportedCities(cities);
 if (unsupported.length) throw new Error(`BOSS 暂不支持「${unsupported.join('、')}」，请从输入提示中选择城市名称。`);
 if (!Number.isInteger(pages) || pages < 1 || pages > 3) throw new Error('每个关键词请选择 1 至 3 页。');
 const budget = homeSearchBudget({...form, cities});
 if (budget.over) throw new Error(budget.over);
 if (!Number.isInteger(jobs) || jobs < 1 || jobs > 60) throw new Error('岗位详情上限须为 1 至 60 的整数。');
 const payload = {platform: form.platform, keywords: parsed.keywords, cities, max_pages: pages, max_jobs: jobs};
 if (form.platform === 'boss') {
  if (!['chrome_use', 'managed'].includes(form.browser_mode)) throw new Error('请选择使用当前 Chrome 或独立采集窗口。');
  payload.browser_mode = form.browser_mode;
 }
 return payload;
}
function homeSearchIsActive(run = homeSearch.current.search_run) {
 return Boolean(run && HOME_SEARCH_ACTIVE.has(run.status));
}
function homeSearchScopeText() {
 const budget = homeSearchBudget(), form = homeSearch.form;
 const platform = form.platform === 'boss' ? 'BOSS 直聘' : '实习僧';
 if (!budget.keywords) return '每个关键词都会在每个所选城市各检索一遍；每次最多 9 组关键词 × 城市、12 页列表。';
 if (budget.over) return budget.over;
 const cities = form.cities.length ? form.cities.join('、') : '城市未选择';
 return `${platform} · ${cities} · ${budget.keywords} 个关键词 × ${budget.cities} 个城市 × 每词 ${budget.pages} 页 = ${budget.listPages} / 12 页列表 · 最多读取 ${form.max_jobs} 条详情`;
}
function homeSearchCityChips() {
 const selected = new Set(homeSearch.form.cities);
 const common = HOME_SEARCH_COMMON_CITIES.map(city => `<button type="button" class="city-chip" data-home-search="toggle-city" data-city="${esc(city)}" aria-pressed="${selected.has(city)}">${esc(city)}</button>`).join('');
 const others = homeSearch.form.cities.filter(city => !HOME_SEARCH_COMMON_CITIES.includes(city));
 const unsupported = new Set(homeSearchUnsupportedCities());
 const custom = others.map(city => `<span class="city-chip is-custom${unsupported.has(city) ? ' is-invalid' : ''}" aria-pressed="true">${esc(city)}<button type="button" class="city-chip-remove" data-home-search="remove-city" data-city="${esc(city)}" aria-label="移除城市 ${esc(city)}">×</button></span>`).join('');
 const status = !homeSearch.form.cities.length ? '尚未选择城市。'
  : unsupported.size ? `BOSS 暂不支持：${[...unsupported].join('、')}。请按输入提示选择城市名称。`
  : `已选 ${homeSearch.form.cities.length} 个城市：${homeSearch.form.cities.join('、')}`;
 return `<div class="city-chips" role="group" aria-label="常用城市">${common}</div>
  ${custom ? `<div class="city-chips city-chips-custom" aria-label="其他城市">${custom}</div>` : ''}
  <p class="tip${unsupported.size ? ' error' : ''}" id="home-search-city-status" role="status">${esc(status)}</p>`;
}
function homeSearchCityOptions() {
 const names = homeSearchCityTable.boss.length ? homeSearchCityTable.boss : HOME_SEARCH_COMMON_CITIES;
 return names.map(city => `<option value="${esc(city)}"></option>`).join('');
}
function homeSearchBrowserHelp() {
 if (homeSearch.form.platform !== 'boss') return '<p class="tip">实习僧会打开采集窗口；需要登录或验证码时，这里会提示你处理。</p>';
 if (homeSearch.form.browser_mode === 'managed') return '<p class="tip">使用本机的 Chrome 或 Edge 打开独立采集窗口，无需安装扩展；首次在该窗口登录 BOSS。</p>';
 return `<p class="tip">复用当前 Chrome 的登录状态，在后台任务标签中检索；结束时只关闭本次任务打开的标签。</p>
  <details id="home-search-connection" class="home-search-connection" ${homeSearch.connectionOpen ? 'open' : ''}>
   <summary>首次使用：连接说明</summary>
   <ol><li>按<a href="https://chrome-use.leeguoo.com/" target="_blank" rel="noopener noreferrer">官方 Windows 安装说明</a>安装 Chrome Use。</li>
    <li>在平时使用的 Chrome 中添加<a href="https://chromewebstore.google.com/detail/chrome-use/knfcmbamhjmaonkfnjhldjedeobeafmk" target="_blank" rel="noopener noreferrer">Chrome Use 扩展</a>，按说明完成一次本机连接。</li>
    <li>保持 Chrome 开启。连接完成后回到首页，点“开始检索”会自动重新检查连接。</li></ol>
   <p class="tip">登录或验证码需要你切到 Chrome 的“择木”任务标签处理，再回这里继续检索。</p>
  </details>`;
}

function homeSearchConfirmation() {
 const preview = homeSearch.preview;
 if (!preview || preview.ready?.ok === true) return '';
 const payload = homeSearch.previewPayload;
 const summary = preview.summary || {};
 const keywords = preview.preferences?.keywords || payload.keywords;
 const messages = [...(preview.ready?.messages || []), ...(preview.warnings || [])];
 if (!messages.length) messages.push('暂时无法确认浏览器是否可用，请重新检查。');
 return `<section class="home-search-confirm" aria-labelledby="home-search-confirm-title">
  <h3 id="home-search-confirm-title" tabindex="-1">检索环境需要处理</h3>
  <p><strong>${esc(summary.platform_label || (payload.platform === 'boss' ? 'BOSS 直聘' : '实习僧'))}</strong>
   · ${esc(summary.city || payload.cities.join('、'))} · 仅实习岗位 · ${Number(summary.list_pages || payload.keywords.length * payload.cities.length * payload.max_pages)} 页列表
   · 最多读取 ${Number(summary.max_jobs || payload.max_jobs)} 条详情</p>
  <div class="home-search-keywords">${keywords.map(keyword => `<span class="tag">${esc(keyword)}</span>`).join('')}</div>
  ${messages.length ? `<ul class="home-search-messages">${messages.map(message => `<li>${esc(message)}</li>`).join('')}</ul>` : ''}
  <div class="form-actions">
   <button type="button" class="btn btn-primary" data-home-search="start" ${homeSearch.busy || homeSearchIsActive() ? 'disabled' : ''}>重新检查并检索</button>
   ${payload.platform === 'boss' && payload.browser_mode === 'chrome_use' ? '<button type="button" class="btn" data-home-search="connection">查看连接说明</button>' : ''}
   <button type="button" class="btn" data-quick-job>先粘贴岗位 JD</button>
   <button type="button" class="btn btn-ghost" data-home-search="edit" ${homeSearch.busy ? 'disabled' : ''}>修改条件</button>
  </div>
 </section>`;
}
function homeSearchProgress() {
 const current = homeSearch.current, run = current.search_run;
 const failure = homeSearch.currentError || current.error || '';
 if (!run) return failure ? `<div class="notice" role="alert">${esc(failure)}
  <button type="button" class="btn btn-small" data-home-search="refresh">重新检查检索状态</button></div>` : '';
 const progress = run.platform_state || {}, active = homeSearchIsActive(run), prefs = run.preferences || {};
 const platformProgress = progress[run.platform] || progress, phase = platformProgress.phase || progress.phase;
 const chromeUse = run.platform === 'boss' && (platformProgress.browser_mode || prefs.browser_mode) === 'chrome_use';
 const needsLogin = active && phase === 'login_required', needsVerification = active && phase === 'verification_required';
 const labels = {starting: chromeUse ? '正在连接当前 Chrome' : '正在打开采集窗口', pending: '正在准备检索', running: '正在检索岗位',
  stopping: '正在停止，保留已取得的结果', unknown: '正在确认检索状态', completed: '检索完成',
  completed_with_shortage: '本轮检索结束', completed_with_errors: '检索结束，部分岗位未读取',
  stopped: '已停止，已保存的结果仍可核对', failed: '检索未完成'};
 const heading = needsLogin ? (chromeUse ? '请到 Chrome 的任务标签完成登录' : '请在浏览器中完成登录')
  : needsVerification ? (chromeUse ? '请到 Chrome 的任务标签完成验证' : '请在浏览器中完成验证') : labels[run.status] || '检索状态待确认';
 const items = Array.isArray(current.items) ? current.items : [];
 const notes = [...new Set([platformProgress.message || progress.message, run.error].filter(Boolean))];
 let cleanup = chromeUse && (!active || run.status === 'stopping') ? platformProgress.cleanup_message || {
  pending: '正在关闭本次任务标签…', complete: '本次任务标签已关闭。',
  incomplete: '部分任务标签未能确认关闭，请在 Chrome 的“择木”标签组中检查。',
 }[platformProgress.cleanup_status] || '' : '';
 if (chromeUse && !active && platformProgress.cleanup_status === 'pending' && !homeSearchAwaitingCleanup(run)) {
  cleanup = '未能确认本次任务标签已关闭，请在 Chrome 的“择木”标签组中检查。';
 }
 const rowState = {imported: '已入库', accepted: '已入库', deferred: '暂时跳过', dismissed: '不感兴趣',
  pending: '待核对', duplicate: '已有同一来源', review: '待重复审核'};
 const results = items.map(item => {
  const link = secureLink(item.source_url || '');
  const label = item.jd_available === false ? '原文待补全' : rowState[item.review_status] || '待核对';
  return `<div class="home-search-result">
   <div><strong>${esc(item.title || '岗位名称待核对')}</strong><small>${esc(item.company || '企业待核对')} · ${esc(item.city || '城市待核对')}</small></div>
   <span class="home-search-result-state">${esc(label)}</span>
   ${link === '#' ? '' : `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">原始岗位 ↗</a>`}
  </div>`;
 }).join('');
 const scope=esc([run.platform==='shixiseng'?'实习僧':'BOSS 直聘',
  (prefs.keywords||[]).join('、'),(prefs.cities||[]).join('、')].filter(Boolean).join(' · '));
 const resultButton=current.batch?.id||run.batch_id?'<button type="button" class="btn btn-small" data-home-search="results">核对本次结果</button>':'';
 const refreshButton=failure||run.status==='unknown'?'<button type="button" class="btn btn-small" data-home-search="refresh">刷新状态</button>':'';
 const closedJobs=Array.isArray(platformProgress.closed_jobs)?platformProgress.closed_jobs:[];
 const closedList=closedJobs.length?`<details class="home-search-result-preview"><summary>来源提示已关闭（${closedJobs.length} 条）</summary>
  <p class="tip">以下岗位在本次读取时已关闭；旧资料仍保留。</p>
  <div class="home-search-results">${closedJobs.map(job=>`<div class="home-search-result"><div><strong>${esc(job.title||'岗位名称未写明')}</strong><small>${esc(job.company||'企业未写明')}${job.checked_at?' · 读取于 '+esc(job.checked_at):''}</small></div><span class="home-search-result-state">已关闭</span><a href="${esc(secureLink(job.url||''))}" target="_blank" rel="noopener noreferrer">原始岗位 ↗</a></div>`).join('')}</div></details>`:'';
 const messages=`${notes.map(note=>`<p class="tip">${esc(note)}</p>`).join('')}
  ${failure?`<p class="error" role="alert">${esc(failure)}；已保存的结果仍可查看。</p>`:''}
  ${cleanup?`<p class="tip home-search-cleanup" role="status">${esc(cleanup)}</p>`:''}`;
 const resultList=results?`<div class="home-search-results">${results}</div>
  ${current.has_more?`<p class="tip">这里显示前 ${items.length} 条，可到采集收件箱查看全部。</p>`:''}`:'';
 const details=(label,body)=>`<details class="home-search-result-preview" ${homeSearch.resultPreview?'open':''}><summary>${label}</summary>${body}</details>`;
 return `<section class="home-search-progress${active?'':' home-search-history'}${needsLogin||needsVerification?' home-search-attention':''}" aria-labelledby="home-search-progress-title">
  <div class="home-search-progress-head"><div role="status" aria-live="polite">
   <h3 id="home-search-progress-title" tabindex="-1">${esc(heading)}</h3>
   ${active?`<p>${scope}</p>`:''}
  </div><span class="home-search-saved">已保存 <strong>${Number(run.synced_count||0)}</strong> 条结果${closedJobs.length?' · 已关闭 '+closedJobs.length+' 条':''}</span>${active?'':resultButton}</div>
  ${closedList}
  ${active?`
   ${chromeUse&&(needsLogin||needsVerification)?`<p class="notice">请切到 Chrome 中的“择木”任务标签，完成${needsLogin?'登录':'验证码'}后返回这里，点击下方继续检索。</p>`:''}
   ${messages}
   <div class="form-actions">
    ${needsLogin||needsVerification?`<button type="button" class="btn btn-primary" data-home-search="resume" ${homeSearch.busy?'disabled':''}>
     ${homeSearch.busy==='resume'?'正在继续…':needsLogin?'我已完成登录，继续检索':'我已完成验证，继续检索'}</button>`:''}
    ${resultButton}
    <button type="button" class="btn" data-home-search="stop" ${homeSearch.busy||run.status==='stopping'?'disabled':''}>
     ${homeSearch.busy==='stop'||run.status==='stopping'?'正在停止…':'停止检索，保留结果'}</button>
    ${refreshButton}
   </div>
   ${resultList?details('预览已取得的岗位',resultList):'<p class="tip">取得的岗位会自动保存到采集收件箱。</p>'}
  `:details('查看检索详情',`<p class="tip">${scope}</p>${messages}
   ${resultList||'<p class="tip">本轮没有取得可核对的岗位。</p>'}${refreshButton}`)}
 </section>`;
}

function homeSearchCitySummary(){
 const cities=homeSearch.form.cities;
 return cities.length?cities.slice(0,2).join('、')+' · 已选 '+cities.length+' 个':'选择城市';
}
function homeSearchPanel() {
 const form=homeSearch.form, active=homeSearchIsActive(), disabled=Boolean(homeSearch.busy)||active;
 return `<section id="home-search" class="panel home-search" aria-labelledby="home-search-title">
  <div class="home-search-heading">
   <div><h2 id="home-search-title">检索岗位</h2><p>可输入多个关键词</p></div>
   <button type="button" class="btn btn-small" data-page="planner">词库与计划</button>
  </div>
  <form id="home-search-form" ${active?'hidden':''}>
   <fieldset id="home-search-fields" ${disabled?'disabled':''}>
    <div class="home-search-fields">
     <div class="home-search-keywords-col">
      <label class="field home-search-keyword-field"><span class="home-search-label-row"><span>岗位关键词 <small>每行一个，或用逗号分隔</small></span><small id="home-search-keyword-count" class="home-search-count">${esc(homeSearchCountText())}</small></span><textarea name="keywords" id="home-search-keywords" rows="3" required
       maxlength="65536" placeholder="产品运营实习&#10;财务分析实习&#10;机械设计实习" aria-describedby="home-search-scope">${esc(form.keywords)}</textarea></label>
      <div class="home-search-file-tools">
       <button type="button" class="btn btn-small btn-ghost" data-home-search="library">从词库选</button>
       <button type="button" class="btn btn-small btn-ghost" data-home-search="import" title="CSV / TXT · UTF-8 · 每行一个关键词">导入关键词文件</button>
       <button type="button" class="btn btn-small btn-ghost" data-boss-favorite="open">按岗位清单查找 / BOSS 收藏</button>
       <input type="file" id="home-search-file" accept=".csv,.txt,text/csv,text/plain" hidden aria-label="选择岗位关键词文件">
      </div>
     </div>
     <div class="home-search-location">
      <label class="field">招聘平台<select name="platform">${opt({boss:'BOSS 直聘',shixiseng:'实习僧'},form.platform)}</select></label>
      <fieldset class="field home-search-city-field"><legend>城市</legend>
       <details class="home-search-city-select">
        <summary><span id="home-search-city-summary">${esc(homeSearchCitySummary())}</span><svg class="icon" aria-hidden="true"><use href="#i-chevron"/></svg></summary>
        <div class="home-search-city-popover">
         <div id="home-search-city-picker">${homeSearchCityChips()}</div>
         <div class="home-search-city-add">
          <input id="home-search-city-input" list="home-search-cities" maxlength="${HOME_SEARCH_LIMITS.city}" autocomplete="off" value="${esc(homeSearch.cityInput)}" placeholder="其他城市，输入后回车添加" aria-label="添加其他城市" aria-describedby="home-search-city-status">
          <button type="button" class="btn btn-small" data-home-search="add-city">添加</button>
         </div>
         <div class="home-search-city-actions"><button type="button" class="btn btn-small" data-home-search="close-cities">完成</button></div>
        </div>
       </details>
       <datalist id="home-search-cities">${homeSearchCityOptions()}</datalist>
      </fieldset>
     </div>
    </div>
    <div class="home-search-submit">
     <details class="home-search-options" ${homeSearch.advanced?'open':''}><summary>更多设置</summary>
      <div class="home-search-option-fields">
       <label id="home-search-browser-choice" class="field home-search-browser-choice" ${form.platform==='boss'?'':'hidden'}>检索方式<select name="browser_mode">${opt({managed:'独立采集窗口',chrome_use:'使用当前 Chrome'},form.browser_mode)}</select></label>
       <label class="field">每个关键词<select name="max_pages">${[1,2,3].map(value=>`<option value="${value}" ${Number(form.max_pages)===value?'selected':''}>${value} 页</option>`).join('')}</select></label>
       <label class="field">最多读取岗位详情<input name="max_jobs" type="number" min="1" max="60" step="1" value="${esc(form.max_jobs)}" required></label>
      </div>
      <div id="home-search-browser-help" class="home-search-browser-help">${homeSearchBrowserHelp()}</div>
      <button type="button" class="btn btn-small btn-ghost" data-home-search="template">下载关键词 CSV 模板</button>
     </details>
     <button type="submit" class="btn btn-primary">${homeSearch.busy==='preview'?'正在检查环境…':homeSearch.busy==='start'?'正在启动…':'开始检索'}</button>
     <p id="home-search-scope" class="home-search-scope ${homeSearchBudget().over?'is-over':''}">${esc(homeSearchScopeText())}</p>
    </div>
   </fieldset>
  </form>
  <p id="home-search-import-note" class="tip" role="status" ${homeSearch.importNote?'':'hidden'}>${esc(homeSearch.importNote)}</p>
  <p id="home-search-error" class="error" role="alert" ${homeSearch.error?'':'hidden'}>${esc(homeSearch.error)}</p>
  <div id="home-search-confirmation">${homeSearchConfirmation()}</div>
  <div id="home-search-current">${homeSearchProgress()}</div>
 </section>`;
}
function paintHomeSearch() {
 if (state.page !== 'overview') return;
 const panel = document.querySelector('#home-search');
 if (panel) panel.outerHTML = homeSearchPanel();
}
function paintHomeSearchProgress() {
 if (state.page !== 'overview') return;
 const panel = document.querySelector('#home-search-current');
 if (panel) {
  const focused = document.activeElement;
  const action = focused && panel.contains(focused) ? focused.dataset.homeSearch : '';
  panel.innerHTML = homeSearchProgress();
  if (action) panel.querySelector('[data-home-search="' + CSS.escape(action) + '"]')?.focus({preventScroll: true});
 }
 const error = document.querySelector('#home-search-error');
 if (error) { error.textContent = homeSearch.error; error.hidden = !homeSearch.error; }
 const active=homeSearchIsActive();
 const form=document.querySelector('#home-search-form');
 if(form)form.hidden=active;
 const fields=document.querySelector('#home-search-fields');
 if(fields)fields.disabled=Boolean(homeSearch.busy)||active;
 const confirmation = document.querySelector('#home-search-confirmation');
 if (confirmation) confirmation.innerHTML = homeSearchConfirmation();
}
function changeHomeSearchField(input) {
 if (!Object.hasOwn(homeSearch.form, input.name)) return;
 homeSearch.form[input.name] = ['max_pages', 'max_jobs'].includes(input.name) ? Number(input.value) : input.value;
 homeSearch.preview = null; homeSearch.previewPayload = null; homeSearch.error = ''; homeSearch.importNote = '';
 saveHomeSearchDraft();
 paintHomeSearchBudget();
 if (input.name === 'platform') paintHomeSearchCities();
 if (['platform', 'browser_mode'].includes(input.name)) {
  const choice = document.querySelector('#home-search-browser-choice');
  if (choice) choice.hidden = homeSearch.form.platform !== 'boss';
  const help = document.querySelector('#home-search-browser-help');
  if (help) help.innerHTML = homeSearchBrowserHelp();
 }
 for (const id of ['#home-search-error', '#home-search-import-note']) { const item = document.querySelector(id); if (item) item.hidden = true; }
 const confirmation = document.querySelector('#home-search-confirmation');
 if (confirmation) confirmation.innerHTML = '';
}

function paintHomeSearchBudget() {
 const scope = document.querySelector('#home-search-scope');
 if (scope) { scope.textContent = homeSearchScopeText(); scope.classList.toggle('is-over', Boolean(homeSearchBudget().over)); }
 const count = document.querySelector('#home-search-keyword-count');
 if (count) count.textContent = homeSearchCountText();
}
function paintHomeSearchCities() {
 const picker = document.querySelector('#home-search-city-picker');
 if(picker){
  const focused=document.activeElement;
  const city=focused?.dataset?.city&&picker.contains(focused)?focused.dataset.city:'';
  picker.innerHTML=homeSearchCityChips();
  if(city)picker.querySelector('[data-city="'+CSS.escape(city)+'"]')?.focus({preventScroll:true});
 }
 const summary=document.querySelector('#home-search-city-summary');
 if(summary)summary.textContent=homeSearchCitySummary();
 paintHomeSearchBudget();
}
function setHomeSearchCities(cities) {
 homeSearch.form.cities = [...new Set(cities)];
 homeSearch.preview = null; homeSearch.previewPayload = null; homeSearch.error = '';
 saveHomeSearchDraft();
 const error = document.querySelector('#home-search-error');
 if (error) error.hidden = true;
 const confirmation = document.querySelector('#home-search-confirmation');
 if (confirmation) confirmation.innerHTML = '';
 paintHomeSearchCities();
}
// Adds one or more typed cities; pasted lists such as “北京，上海” are split.
function addHomeSearchCities(text) {
 const added = splitHomeEntries(text).map(normalizeHomeCity).filter(Boolean);
 if (!added.length) return 0;
 const next = [...new Set([...homeSearch.form.cities, ...added])];
 if (next.length > HOME_SEARCH_LIMITS.cities) throw new Error('每次最多选择 9 个城市。');
 setHomeSearchCities(next);
 return added.length;
}
function toggleHomeSearchCity(city) {
 const selected = homeSearch.form.cities.includes(city);
 if (!selected && homeSearch.form.cities.length >= HOME_SEARCH_LIMITS.cities) throw new Error('每次最多选择 9 个城市。');
 setHomeSearchCities(selected ? homeSearch.form.cities.filter(item => item !== city) : [...homeSearch.form.cities, city]);
}
function commitHomeSearchCityInput() {
 const input = document.querySelector('#home-search-city-input');
 if (!input || !input.value.trim()) return;
 addHomeSearchCities(input.value);
 homeSearch.cityInput = '';
 input.value = '';
 input.focus();
}
async function loadHomeSearchCities() {
 if (homeSearchCityTable.loaded) return;
 try {
  const response = await fetch('/api/job-search/cities');
  if (!response.ok) return;
  const data = await response.json();
  if (Array.isArray(data?.boss) && data.boss.every(city => typeof city === 'string')) homeSearchCityTable.boss = data.boss;
  homeSearchCityTable.loaded = homeSearchCityTable.boss.length > 0;
  const list = document.querySelector('#home-search-cities');
  if (list) list.innerHTML = homeSearchCityOptions();
  paintHomeSearchCities();
 } catch { /* Suggestions are optional; the server still validates cities. */ }
}

// ---- Keyword groups from the planner's family library ----
let homeKeywordLibrary = null;
async function loadHomeKeywordLibrary() {
 const response = await fetch('/api/planner/library');
 const data = response.ok ? await response.json() : null;
 let families = Array.isArray(data?.families) ? data.families.filter(family => family && family.enabled !== false) : [];
 let source = '你在“词库与计划”中维护的岗位词库';
 if (!families.length) {
  // Read-only fallback: the bundled template is not imported into the database here.
  const template = await fetch('/static/templates/ai-keywords-v1.json');
  if (!template.ok) throw new Error('岗位词库暂不可用，请在“词库与计划”中导入词库。');
  const value = await template.json();
  families = Array.isArray(value?.families) ? value.families : [];
  source = '内置 AI 方向示例词库（可在“词库与计划”中导入并编辑）';
 }
 families = families.filter(family => typeof family?.representative === 'string' && family.representative.trim());
 if (!families.length) throw new Error('岗位词库里没有可用的代表词。');
 return {families, source};
}
function homeKeywordLibraryWords(ids, withAliases, library = homeKeywordLibrary) {
 const chosen = library.families.filter(family => ids.includes(String(family.id)));
 return chosen.flatMap(family => [family.representative, ...(withAliases && Array.isArray(family.aliases) ? family.aliases : [])]);
}
function homeKeywordLibraryMarkup(library = homeKeywordLibrary) {
 const routes = new Map();
 for (const family of library.families) {
  const route = family.route || '其他';
  if (!routes.has(route)) routes.set(route, []);
  routes.get(route).push(family);
 }
 const groups = [...routes].map(([route, families]) => `<fieldset class="keyword-library-group"><legend>${esc(route)}</legend>
  ${families.map(family => `<label class="keyword-library-family"><input type="checkbox" data-modal-transient data-library-family value="${esc(family.id)}">
   <span><strong>${esc(family.representative)}</strong><small>${esc(family.name || '')}${family.priority ? ' · ' + esc(family.priority) : ''}${family.aliases?.length ? ` · 别名 ${family.aliases.length} 个` : ''}</small></span></label>`).join('')}
 </fieldset>`).join('');
 return `<p class="tip">来源：${esc(library.source)}。勾选岗位族会填入它的代表词；勾选“同时加入别名”会把别名也作为关键词。</p>
  <div class="keyword-library">${groups}</div>
  <div id="form-error" class="error" role="alert"></div>
  <div class="form-actions keyword-library-actions">
   <label class="filter-check form-actions-lead"><input type="checkbox" data-modal-transient id="keyword-library-aliases"> 同时加入别名</label>
   <span class="tip" id="keyword-library-count" role="status"></span>
   <button type="button" class="btn" data-action="close">取消</button>
   <button type="button" class="btn btn-primary" data-home-search="library-apply">填入关键词</button>
  </div>`;
}
function homeKeywordLibraryPreview() {
 const ids = [...document.querySelectorAll('[data-library-family]:checked')].map(input => input.value);
 const withAliases = Boolean(document.querySelector('#keyword-library-aliases')?.checked);
 const words = normalizeHomeKeywords([...homeSearchKeywordList(), ...homeKeywordLibraryWords(ids, withAliases)]).keywords;
 const limit = homeSearchBudget().maxKeywords;
 return {ids, withAliases, words, limit};
}
function paintHomeKeywordLibraryCount() {
 const target = document.querySelector('#keyword-library-count');
 if (!target) return;
 const {words, limit} = homeKeywordLibraryPreview();
 target.textContent = `填入后共 ${words.length} 个关键词（当前城市与页数下最多 ${limit} 个）`;
 target.classList.toggle('error', words.length > limit);
}
async function openHomeKeywordLibrary() {
 homeKeywordLibrary = homeKeywordLibrary || await loadHomeKeywordLibrary();
 modal('从岗位词库选择关键词', homeKeywordLibraryMarkup(), true);
 paintHomeKeywordLibraryCount();
}
async function applyHomeKeywordLibrary() {
 const {ids, words, limit} = homeKeywordLibraryPreview();
 const error = document.querySelector('#modal #form-error');
 if (!ids.length) { if (error) error.textContent = '请至少勾选一个岗位族。'; return; }
 if (words.length > limit) {
  if (error) error.textContent = `填入后共 ${words.length} 个关键词，超过当前上限 ${limit} 个。请少选几个岗位族、不加别名，或减少城市和每词页数。`;
  return;
 }
 homeSearch.form.keywords = words.join('\n');
 homeSearch.preview = null; homeSearch.previewPayload = null; homeSearch.error = '';
 homeSearch.importNote = `已从岗位词库填入，共 ${words.length} 个关键词。尚未开始检索。`;
 saveHomeSearchDraft();
 await closeModal(true);
 paintHomeSearch();
}
// The planner hands one prepared action to the homepage instead of the capture inbox.
function useHomeSearchConditions({keywords = [], cities = [], platform, max_pages, max_jobs} = {}) {
 if (Number.isInteger(max_pages)) homeSearch.form.max_pages = Math.min(3, Math.max(1, max_pages));
 if (Number.isInteger(max_jobs)) homeSearch.form.max_jobs = Math.min(60, Math.max(1, max_jobs));
 const words = normalizeHomeKeywords(keywords).keywords;
 if (words.length) homeSearch.form.keywords = words.join('\n');
 const places = [...new Set(cities.map(city => String(city).trim()).filter(Boolean))];
 if (places.length) homeSearch.form.cities = places.slice(0, HOME_SEARCH_LIMITS.cities);
 if (['boss', 'shixiseng'].includes(platform)) homeSearch.form.platform = platform;
 homeSearch.preview = null; homeSearch.previewPayload = null; homeSearch.error = '';
 homeSearch.importNote = '已带入规划中的检索条件，确认后点击“开始检索”。';
 saveHomeSearchDraft();
}

async function importHomeKeywordFile(file) {
 if (!file || homeSearch.busy || homeSearchIsActive()) return;
 homeSearch.busy = 'import'; homeSearch.error = ''; paintHomeSearch();
 try {
  if (file.size > HOME_SEARCH_LIMITS.fileBytes) throw new Error('关键词文件不能超过 64 KB，原输入已保留。');
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); }
  catch { throw new Error('无法按 UTF-8 读取文件，请另存为“CSV UTF-8”或 UTF-8 文本后重试。原输入已保留。'); }
  const parsed = parseHomeKeywordFile(text, file.name);
  homeSearch.form.keywords = parsed.keywords.join('\n');
  homeSearch.preview = null; homeSearch.previewPayload = null;
  homeSearch.importNote = `已填入 ${parsed.keywords.length} 个关键词${parsed.duplicates ? `，合并 ${parsed.duplicates} 行重复项` : ''}${parsed.blanks ? `，忽略 ${parsed.blanks} 个空行` : ''}。尚未开始检索。`;
  saveHomeSearchDraft();
 } catch (error) { homeSearch.error = error.message; }
 finally { homeSearch.busy = ''; paintHomeSearch(); }
}
async function downloadHomeKeywordTemplate() {
 const response = await fetch('/static/templates/job-keywords.csv');
 if (!response.ok) throw new Error('关键词模板暂不可用，请稍后重试。');
 const text = '\ufeff' + (await response.text()).replace(/^\ufeff/, '');
 await saveExportText('job-keywords.csv', text, 'text/csv;charset=utf-8');
}
function homeSearchRequestId(payload) {
 const key = JSON.stringify(payload);
 if (homeSearch.attempt?.key !== key) {
  if (typeof globalThis.crypto?.randomUUID !== 'function') throw new Error('当前浏览器版本过旧，请更新 Chrome 或 Edge 后重试。');
  homeSearch.attempt = {key, id: globalThis.crypto.randomUUID()};
  saveHomeSearchDraft();
 }
 return homeSearch.attempt.id;
}
function adoptHomeSearchCurrent(value) {
 if (!value || typeof value !== 'object') throw new Error('检索状态暂时无法读取，请刷新重试。');
 const previousRun=homeSearch.current.search_run?.id;
 homeSearch.current = value;
 homeSearch.checked = true; homeSearch.currentError = '';
 const run = value.search_run, progress = run?.platform_state?.boss || run?.platform_state;
 if(run?.id!==previousRun)homeSearch.resultPreview=false;
 if (run && !homeSearchIsActive(run) && progress?.cleanup_status === 'pending') {
  if (homeSearch.cleanupWait?.id !== run.id) homeSearch.cleanupWait = {id: run.id, deadline: Date.now() + 90000};
 } else homeSearch.cleanupWait = null;
 const requestId = value.request_id || value.search_run?.preferences?.request_id;
 if (requestId && requestId === homeSearch.attempt?.id) {
  homeSearch.attempt = null; homeSearch.error = ''; saveHomeSearchDraft();
 }
}
async function startHomeSearch() {
 if (homeSearch.busy || homeSearchIsActive()) return;
 homeSearch.error = ''; homeSearch.preview = null; homeSearch.previewPayload = null;
 let requested = false;
 try {
  commitHomeSearchCityInput();
  const payload = homeSearchPayload();
  homeSearch.busy = 'preview'; paintHomeSearch();
  homeSearch.preview = await api('/job-search/preview', {method: 'POST', body: JSON.stringify(payload)});
  homeSearch.previewPayload = payload;
  if (homeSearch.preview.ready?.ok !== true) return;
  const requestId = homeSearchRequestId(payload);
  homeSearch.busy = 'start'; paintHomeSearch();
  requested = true;
  const started = await api('/job-search/start', {method: 'POST', body: JSON.stringify({...payload, confirmed: true, request_id: requestId})});
  homeSearch.revision++;
  adoptHomeSearchCurrent({batch: {id: started.batch_id}, items: [], has_more: false, error: '', ...started});
  homeSearch.attempt = null; homeSearch.preview = null; homeSearch.previewPayload = null;
  saveHomeSearchDraft();
 } catch (error) { homeSearch.error = error.message + (requested ? '。输入与本次请求已保留，可重试。' : ''); }
 finally {
  homeSearch.busy = ''; paintHomeSearch();
  const focus=homeSearchIsActive()?'#home-search-progress-title':'#home-search-confirm-title';
  document.querySelector(focus)?.focus({preventScroll:true});
 }
 if (homeSearchNeedsRefresh()) refreshHomeSearchCurrent(false);
 else if (requested && homeSearch.attempt) refreshHomeSearchCurrent();
}
function homeSearchNeedsRefresh() {
 const value = homeSearch.current, run = value.search_run;
 return Boolean(run && (homeSearchIsActive(run) || homeSearchHasPendingResults(run) || homeSearchAwaitingCleanup(run) || value.error || homeSearch.currentError));
}
function homeSearchHasPendingResults(run) {
 return Number(run.sync_offset ?? run.synced_count ?? 0) < Number(run.total || 0);
}
function homeSearchAwaitingCleanup(run) {
 const progress = run.platform_state?.boss || run.platform_state;
 if (homeSearchIsActive(run) || progress?.cleanup_status !== 'pending') return false;
 return homeSearch.cleanupWait?.id !== run.id || Date.now() < homeSearch.cleanupWait.deadline;
}
function scheduleHomeSearchPoll() {
 clearTimeout(homeSearchPoll);
 if (state.page !== 'overview' || document.visibilityState === 'hidden' || !homeSearchNeedsRefresh() || homeSearch.failures > 5) return;
 homeSearchPoll = setTimeout(() => refreshHomeSearchCurrent(false), homeSearch.failures ? Math.min(30000, 2000 * 2 ** homeSearch.failures) : 2000);
}
async function refreshHomeSearchCurrent(discover = true) {
 if (homeSearchRequest) return homeSearchRequest;
 const revision = homeSearch.revision;
 const task = (async () => {
  try {
   let value = discover || !homeSearch.current.search_run ? await api('/job-search/current') : homeSearch.current;
   const run = value.search_run;
   if (run && (HOME_SEARCH_ACTIVE.has(run.status) || homeSearchHasPendingResults(run) || homeSearchAwaitingCleanup(run) || value.error || homeSearch.currentError)) {
    value = await api(`/job-search/${run.id}/refresh`, {method: 'POST', body: JSON.stringify({confirmed: true})});
   }
   if (revision !== homeSearch.revision) return;
   adoptHomeSearchCurrent(value);
   homeSearch.failures = value.error ? homeSearch.failures + 1 : 0;
  } catch (error) {
   if (revision !== homeSearch.revision) return;
   homeSearch.checked = true; homeSearch.currentError = error.message; homeSearch.failures++;
  } finally { paintHomeSearchProgress(); }
 })();
 homeSearchRequest = task;
 try { await task; } finally { if (homeSearchRequest === task) homeSearchRequest = null; scheduleHomeSearchPoll(); }
}
function resumeHomeSearch() {
 clearTimeout(homeSearchPoll);
 if (state.loaded && state.page === 'overview') { homeSearch.failures = 0; refreshHomeSearchCurrent(); }
}
async function controlHomeSearch(action) {
 const run = homeSearch.current.search_run;
 if (homeSearch.busy || !run || !['stop', 'resume'].includes(action)) return;
 homeSearch.busy = action; homeSearch.error = ''; homeSearch.revision++; paintHomeSearch();
 try {
  const value = await api(`/job-search/${run.id}/${action}`, {method: 'POST', body: JSON.stringify({confirmed: true})});
  adoptHomeSearchCurrent(value); homeSearch.failures = value.error ? homeSearch.failures + 1 : 0;
 } catch (error) { homeSearch.currentError = error.message; homeSearch.failures++; }
 finally { homeSearch.busy = ''; paintHomeSearch(); scheduleHomeSearchPoll(); }
}
async function openHomeSearchResults() {
 const current = homeSearch.current, id = Number(current.batch?.id || current.search_run?.batch_id);
 if (!Number.isInteger(id) || id < 1) return;
 state.captureSelected = id; state.captureHistory = false; state.bossConnector = null;
 state.bossConnectionError = ''; state.bossSyncError = '';
 await refreshCapture(false);
 navigate('capture');
}

function initHomeSearch() {
 loadHomeSearchCities();
 document.addEventListener('input', event => {
  if (event.target.id === 'home-search-city-input') homeSearch.cityInput = event.target.value;
  else if (event.target.closest('#home-search-form')) changeHomeSearchField(event.target);
 });
 document.addEventListener('keydown', event => {
  if(event.key==='Escape'&&event.target.closest?.('.home-search-city-select[open]')){
   const picker=event.target.closest('.home-search-city-select');
   picker.open=false;
   picker.querySelector('summary').focus();
   return;
  }
  if (event.target.id !== 'home-search-city-input' || event.key !== 'Enter' || event.isComposing) return;
  event.preventDefault();
  try { commitHomeSearchCityInput(); } catch (error) { homeSearch.error = error.message; paintHomeSearchProgress(); }
 });
 document.addEventListener('change', event => {
  if (event.target.id === 'home-search-file') { importHomeKeywordFile(event.target.files?.[0]); return; }
  if (event.target.matches?.('[data-library-family], #keyword-library-aliases')) { paintHomeKeywordLibraryCount(); return; }
  if (event.target.id === 'home-search-city-input') {
   // A suggestion picked from the list is committed right away.
   if (homeSearchCityTable.boss.includes(event.target.value.trim())) {
    try { commitHomeSearchCityInput(); } catch (error) { homeSearch.error = error.message; paintHomeSearchProgress(); }
   }
   return;
  }
  if (event.target.closest('#home-search-form')) changeHomeSearchField(event.target);
 });
 document.addEventListener('toggle', event => {
  if (event.target.matches?.('.home-search-options')) homeSearch.advanced = event.target.open;
  if (event.target.matches?.('.home-search-result-preview')) homeSearch.resultPreview = event.target.open;
  if (event.target.id === 'home-search-connection') homeSearch.connectionOpen = event.target.open;
 }, true);
 document.addEventListener('invalid',event=>{
  const settings=event.target.closest?.('.home-search-options');
  if(settings)settings.open=true;
 },true);
 document.addEventListener('submit', event => {
  if (event.target.id === 'home-search-form') { event.preventDefault(); startHomeSearch(); }
 });
 document.addEventListener('click', async event => {
  const cityPicker=document.querySelector('.home-search-city-select[open]');
  if(cityPicker&&!cityPicker.contains(event.target))cityPicker.open=false;
  const button = event.target.closest('[data-home-search]');
  if (!button || button.disabled) return;
  event.preventDefault();
  try {
   const action = button.dataset.homeSearch;
   if (action === 'import') document.querySelector('#home-search-file')?.click();
   else if (action === 'library') await openHomeKeywordLibrary();
   else if (action === 'library-apply') await applyHomeKeywordLibrary();
   else if (action === 'toggle-city') toggleHomeSearchCity(button.dataset.city);
   else if (action === 'remove-city') { setHomeSearchCities(homeSearch.form.cities.filter(city => city !== button.dataset.city)); document.querySelector('#home-search-city-input')?.focus(); }
   else if (action === 'add-city') commitHomeSearchCityInput();
   else if (action === 'close-cities') {
    commitHomeSearchCityInput();
    cityPicker.open=false;
    cityPicker.querySelector('summary').focus();
   }
   else if (action === 'template') await downloadHomeKeywordTemplate();
   else if (action === 'start') await startHomeSearch();
   else if (action === 'results') await openHomeSearchResults();
   else if (action === 'stop' || action === 'resume') await controlHomeSearch(action);
   else if (action === 'refresh') { homeSearch.failures = 0; await refreshHomeSearchCurrent(); }
   else if (action === 'connection') {
    homeSearch.connectionOpen = true;
    const help = document.querySelector('#home-search-connection');
    if (help) { help.open = true; help.querySelector('summary')?.focus(); }
   }
   else if (action === 'edit') {
    homeSearch.preview = null; homeSearch.previewPayload = null; paintHomeSearch();
    document.querySelector('#home-search-keywords')?.focus();
   }
  } catch (error) { homeSearch.error = error.message; paintHomeSearch(); }
 });
 document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') clearTimeout(homeSearchPoll);
  else resumeHomeSearch();
 });
}
