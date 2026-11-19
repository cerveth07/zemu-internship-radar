# 实习僧检索与桌面连接器（v0.8.0）

## 使用

在桌面应用进入「采集收件箱」，来源选「实习僧」，填写关键词（每行一个或用逗号分隔），点选常用城市或输入其他城市后回车添加（可多选），点击「检索实习僧岗位」。预览确认后，应用会打开专用 Chrome / Edge，并读取列表与逐条详情。

- 每个词、城市可以检索 1–3 页，最多 9 个组合、12 总页；每次最多读取 60 条详情，达到上限会明确提示。
- 本次任务独立保存结果，完成后自动同步到待核对队列。来源链接、完整 JD、薪资、出勤、时长与学历只保留页面实际提供的信息。
- 每次采集都会重新打开并读取详情，不复用历史 JD；失败时保留失败状态。一次任务中同一岗位重复命中只读一次，下一次采集仍重新读取。
- 页面明确下线的岗位单独列为「来源提示已关闭」，展示本次读取时间；不使用旧 JD 代替。实际城市不符合选择的岗位会筛除，城市缺失不会猜成所选城市。
- 「停止采集，保留结果」在当前请求结束后停止，已保存的结果仍可同步、核对。停止标记和任务快照跨桌面重启保留。
- 遇到登录墙、验证码或访问限制时停止。在专用浏览器中人工处理后，再发起新的检索。乱码 JD 保留原文并标为不完整；乱码标题或公司名不入库。
- 收件箱结果必须逐条人工核对后才进入正式岗位库；不会发送消息、填写投递表单或投递简历。

## 复用来源

提取和列表／详情合并复用 [Internship Workbench](https://github.com/Dongnb66/internship-workbench)；HTML、实体、日期处理辅助函数改编自 [Offer Harvester](https://github.com/JiemsLBJ/offer-harvester)。固定提交、改编范围与 MIT 许可证见 [vendor/README.md](../vendor/README.md)。未安装这两个项目的完整工作台或 Agent Skill。

Radar 补充城市/页码参数、任务独立归属、原子快照、停止标记、断连恢复和桌面打包。未采用上游助手的「读取最近几个输出文件」方式，防止把历史结果混入本次任务。

## 运行与数据

Windows EXE 内置 Node.js 和固定版本的 Playwright Core；需要本机已安装 Chrome 或 Edge。实习僧连接器直接在 Windows 运行，不依赖 WSL。BOSS 连接器仍沿用既有 WSL BossHunter。

- Windows 数据：`%LOCALAPPDATA%\InternshipRadarPrivateRC\data\radar.db`。
- 原始任务快照与日志：数据库同级 `shixiseng-runs\<任务ID>\result.json`、`runner.log`。
- 独立浏览器档案：`%LOCALAPPDATA%\InternshipRadarPrivateRC\ShixisengChrome`，调试端口 9334；如果该端口属于其他浏览器会话，启动会拒绝连接。
- 已同步的待核对资料和任务关联随 Radar 完整备份导出。未同步快照留在 `shixiseng-runs`，搬迁时一并保留。

源码运行先安装 Windows Node.js 22+，然后在 WSL 项目目录执行 `npm ci --ignore-scripts --prefix tools/shixiseng-node`。默认使用本机 `C:\Program Files\nodejs\node.exe`；可通过 `RADAR_SHIXISENG_NODE` 指定其他 Node 路径。

Windows 构建脚本会准备并打包 Node 与 Playwright Core，不需要最终使用者安装 Python 或 Node。

## 验证

2026-10-09 使用隔离的临时数据库实际验证「北京 / AI 产品 / 1 页 / 4 条详情」：4 条均有公司、城市、来源链接和 JD，正文长度 95–679 字。独立打开其中一条原页面核对了标题、日薪和正文；不以长度保证每个站点页面的完整性。

同一真实快照通过 Radar API 同步新增 4 条，全部保持待核对，重复同步新增 0 条。验证资料未写入用户岗位数据库或 Git 仓库。自动化另覆盖任务隔离、分页、停止/进程中断、快照校验、缺失与乱码字段、迁移以及模拟 UI 的检索到人工入库流程。
