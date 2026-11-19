# 前端结构与设计值约定

这份文档记录 `static/` 的分层与视觉 token，避免再出现「83 个色值、14 种字号、单行 4000 字符」的散乱状态。

## 脚本分层

`static/index.html` 按顺序加载，顺序有意义（先定义、后装配）：

1. 功能模块：`export-manager.js`、`career-prompts.js`、`ai-review.js`、`analysis-workflows.js`、`skill-gap.js`、`listing-pagination.js`
2. 领域视图：`job-library.js`（岗位阅读、投递入口、新增岗位）、`home-search.js`（首页关键词导入、检索与进度）、`analysis-view.js`（ChatGPT 分析工作台、JD 要求审核、求职分析导出）
3. 核心：`app.js`（state、api、渲染与路由、模态框、事件装配）
4. 页面模块：`desktop-support.js`（首次建档、可选连接器与恢复入口）、`capture.js`、`planner.js`、`gpt-import.js`、`job-bulk.js`、`capture-bulk.js`

约定：

- 新增页面视图放进上面对应的文件，不要把 200 行以上的模板塞回 `app.js`。
- 事件装配只留在 `app.js`/各自文件的底部，渲染函数保持「传 state、返回 HTML 字符串」，方便在 node 里直接调用。
- `home-search.js` 的事件通过 `initHomeSearch()` 在 `app.js` 启动时绑定。进度轮询只刷新状态区域，保留检索输入与键盘焦点。
- 单行超过 1200 字符的模板视为技术债：`tests/test_frontend_behaviour.py::test_long_single_line_templates_do_not_grow` 会拦截增长，拆分后请把该测试里的基线调低。

## 设计值 token

`style.css` 的 `:root` 是唯一的取值来源：

| 类别 | token |
| --- | --- |
| 表面 | `--bg` `--surface` `--surface-2` `--surface-3` |
| 描边 | `--line` `--line-strong` `--line-accent` |
| 文本 | `--text` `--text-2` `--text-muted` `--muted` `--muted-2` |
| 主色 | `--accent` `--accent-hover` `--accent-strong` `--accent-soft` `--accent-tint` `--accent-bar` |
| 语义 | `--success` `--warn` `--danger` `--info`（各带 `-soft` / `-border` / `-bar`） |
| 侧栏 | `--sidebar` `--sidebar-hover` `--sidebar-active` `--sidebar-txt` `--sidebar-dim` `--sidebar-icon` |
| 字号 | `--fs-xs` 12 / `--fs-sm` 13 / `--fs-base` 14 / `--fs-lg` 16 / `--fs-xl` 20 / `--fs-2xl` 24 |
| 节奏 | `--space-1..6`（4/8/12/16/24/32）、`--radius-sm` 4 / `--radius-md` 8 / `--radius-pill` |
| 卷宗风 | `--ink` `--rule`（墨线）、`--field`（输入框底）、`--link`（黛蓝链接）、`--on-accent`（朱砂上的文字）、`--serif`（宋体标题与数字）、`--fs-3xl` 30 |
| 控件 | `--control-h`（输入框、下拉框与按钮同高）、`--chevron`（下拉箭头）、`--shadow-xs`（极浅投影） |
| 阅读宽度 | `--measure` 46em（长文本栏宽） |

规则：

- 颜色、圆角、字号只允许出现在 `:root`；其余位置一律 `var(...)`。`tests/test_daily_ux_flow.py` 会检查这一点。
- 布局不要写 inline style；用 `.mt-3/4/5`、`.field-inline`、`.toolbar-split`、`.is-top`、`.is-wrap`、`.is-grow`、`.pick-row`、`.scroll-list-sm/md` 这些工具类，或新增语义类。
- 间距历史遗留的 7/10/14/15/20/28px 还没收敛，改造时优先顺手替换成最近的 token。
- 页内视图切换复用 `.planner-tabs` / `.workflow-nav` 的页签样式，选中态用 `aria-pressed` 或 `.workflow-active` 表达，不再借用 `btn-primary`。
- 朱砂 `--accent` 只用于印章、页面主操作、重点关注、当前导航竖条与焦点；新组件的选中、勾选、次要链接用 `--ink` 或 `--link`。
- 页面一级区块之间的间距由 `.page-content > .panel + .panel` 等规则统一给出，新页面不必再逐个加 `mt-5`。

## 测试策略

- 渲染行为用 node 跑真实代码：`tests/fe/harness.cjs` 把 `index.html` 里的脚本按顺序装进 vm，`tests/test_frontend_behaviour.py` 直接调用页面渲染函数。移动函数、改文件名不会让测试失效。
- 完整交互用浏览器冒烟：`tools/check_daily_flow.cjs`（需要 Playwright），`tests/test_ui_smoke.py` 会起临时服务跑它，缺少 Playwright 时自动跳过。
- 只有「跨文件调用契约」这类确实无法在渲染层表达的东西，才退回 `fe_helpers.frontend_source()` 做文本检查，不要再按单个文件名去读 `app.js`。
