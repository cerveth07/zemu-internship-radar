# P3.4｜整体验收与 Windows EXE 发布清单

本清单用于本地执行与检查。**源码合并不等于已生成新版 EXE，也不代表已通过 Windows 实机验收。**

## 已排除的明确缺陷

- P3 多批 ZIP 的 `manifest.json` 误将 `career` 批次的岗位 ID 当作企业 ID 读取。已改为 `career/jobs` 读取 `job_id`，并增加各类型 manifest 和 55 岗位回归断言。
- GitHub Windows 工作流缺少 `tools/prepare_shixiseng_runtime.py`，但 `desktop.spec` 要求打包前存在 Node sidecar 和 pinned Playwright Core。现显式安装 Node.js 22，准备运行时后才打包。
- Windows 本地构建以前未先跑完整 pytest；现将自动化测试前置，并要求 P0–P3 打包后 API 自检通过。
- 本地构建以前可能误用旧的 `build/desktop-self-test.json`；现每次 EXE 运行前删除**这个测试报告文件**、检查进程返回码和最新报告。不会触碰岗位数据库及个人资料。

## Windows 10/11 x64 构建先决条件

1. 从 GitHub 获取最新 `main` 源码，备份旧的 `%LOCALAPPDATA%\\InternshipRadarPrivateRC\\data\\radar.db` 和截图附件（如果存在）。
2. 安装 **Python 3.12 x64**（确保 `py -3.12` 可用）。
3. 安装 **Node.js 22 或以上**（确保 `node.exe`、`npm.cmd` 在 PATH）；实习僧辅助浏览器需要 pinned Playwright Core，构建脚本会运行 `npm ci`。
4. 需要联网安装 Python/Node 依赖及预下载 RapidOCR 中文模型。目标电脑运行 EXE 需要 WebView2 Runtime。
5. 不需要大模型 API Key；不需要把数据库或简历上传至 GitHub。

## 一键验收和构建

在 Windows 文件资源管理器里双击仓库根目录的 `build-windows-exe.bat`，或从 Windows 命令提示符执行：

```bat
cd /d C:\\your-path\\ai-internship-radar
build-windows-exe.bat
```

流程依次执行：

- 创建/检查 Python 3.12 x64 构建环境，安装桌面依赖与 pytest；
- 运行 `python -m pytest -q` **完整回归测试**，失败立即停止；
- 下载 OCR 模型，核对中文 OCR、生成图标；
- 准备 Node.js/Playwright 运行时，PyInstaller 生成 `dist\\InternshipRadarPrivateRC.exe`；
- 用 `--self-test --require-ocr` 自检**打包后的实际 EXE**，使用临时隔离数据库，验证静态提示词、P0 岗位 JSON、P2 需求统计、P3 求职分析数据包及 JSON 结果导回、Schema 版本和 SQLite 完整性，以及 OCR、实习僧运行时；
- 只有上述步骤全部通过时才生成 `dist\\SHA256SUMS.txt` 并显示 SUCCESS。

查看 `build\\desktop-self-test.json` 中的 `result`、`ocr`、`career_handoff`、`database`、`api` 字段：成功标准分别为 `passed`、`passed`、`true`、`true`、`true`。这是**自动化自检**，不代替打开原生桌面窗口的人工 UI 检查。

CI 免费运行分钟数如已耗尽，请优先采用本机脚本。不要将失败、自检未通过或未验证来源的旧 EXE 冒充本次构建成功工件。

## 人工桌面检查（EXE 自检通过后）

1. 从 **EXE** 启动界面，确认能进入「岗位库」「企业观察库」「ChatGPT 分析工作台」「JD 需求画像」并正常切换。
2. 新建一条临时岗位；分别导出岗位 JSON 和企业 JSON，检查岗位 JD 全文与企业档位没有串字段。
3. 收录 50 条以上测试岗位（用独立测试数据库，避免污染真实库），生成求职分析 ZIP；解包检查 `manifest.json` 的 `kind=career`、每批 `ids`、`count` 和 SHA256。
4. 在分析工作台选择精简版/完整版：精简版不得含 JD 正文，完整版逐字包含 JD；未确认的要求标为未覆盖。
5. 只勾选一条我的能力与经历，确认导出包仅含这一条，不含其他简历、联系人和个人备注。
6. 检查三套 GPT 提示词可下载，不调用 API；使用一批测试 JSON 手动复制 GPT 返回的 findings，预览并保存，确认关联要求 ID、个人经历 ID 严格限制在当前批次。
7. 修改导出后的一条要求或所选个人经历：导回提示应标注旧版本，未经显式确认不得保存；重新预览后确认历史分析仍不修改原始 JD。
8. 选择一条 `learning_task` 分析结论，手动采纳，确认学习计划只新增一条任务且保留关联来源。
9. 关闭再打开 EXE，检查数据持久化及原有数据库未被覆盖。**不要对真实个人数据做破坏性测试。**

## 状态和边界

- P0–P3 的静态代码/测试脚本可在 GitHub 中检查，Python 回归和 EXE 自检仍需 Windows/可运行环境真实执行，并保存日志。
- 本轮不提供无法校验的 EXE 下载地址，也不生成虚假的 SHA-256 或测试成功记录。
- 若完整 pytest 或 Windows EXE 自检失败，应先修复并重新执行，不能将这轮标记为全部验收通过。
