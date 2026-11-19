# BOSS 岗位检索与详情采集

首页默认使用内置连接器。按[源码启动说明](../README.md#从源码启动)安装依赖后，选择 BOSS 和“独立采集窗口”，即可使用本机 Chrome 或 Edge。检索前请核对[平台接入条件](../LICENSE-STATUS.md#平台接入和账号操作)。两种浏览器模式及结果核对见[首页关键词检索](home-keyword-search.md)，账号收藏的预览、确认及重试见[BOSS 收藏](boss-favorites.md)。

## 可选的 WSL / BossHunter 连接器

下列步骤仅用于单独配置 BossHunter 服务。默认的独立采集窗口使用内置连接器。

1. 在自己的 Windows 设备安装 WSL，记下实际发行版名称（开发者可用 `wsl -l -q` 查看）。择木不会选择或安装发行版。
2. 按 [BossHunter 的安装说明](https://github.com/shengjidaguai-china/BossHunter/blob/main/docs/QUICKSTART.md) 在 WSL 中建立独立环境及 `config.yaml`。择木使用其只读 `collect` 能力，不需要简历或模型 Key。
3. 将择木源码放到 WSL 可访问的位置，使用本仓库的 `tools/start-boss-chain.sh`。该脚本按自身位置确定择木目录；默认在择木的同级 `tools/BossHunter` 查找上游环境。自定义位置可在自己的启动包装脚本中设置 `RADAR_BOSSHUNTER_DIR` 后调用它。
4. 打开桌面版“数据与帮助 → 设置可选 BOSS 连接器”，填入实际 WSL 发行版名称和该启动脚本的绝对路径，保存。
5. 再次打开设置，点击“启动已保存的连接器”。应用先检查 WSL、发行版与脚本，明确报告缺失项。随后到采集收件箱检查连接，在专用 Chrome 完成登录或验证码。

不会在择木启动时自动执行脚本，不包含开发者账户或源码路径。已有兼容服务运行在 `http://127.0.0.1:8686` 时可直接连接。

开发者也可显式设置 `RADAR_BOSSHUNTER_WSL_DISTRO` 和 `RADAR_BOSSHUNTER_WSL_SCRIPT` 后运行 `start-boss-chain-windows.bat`；未配置时该入口只提示设置，不尝试启动。

## 日常使用

在采集收件箱填写关键词和城市，选择 BOSS，预览后确认启动。检索只读取岗位，不发送消息或投递。

- 默认显示本次搜索实际命中的资料，不用关键词匹配历史库猜测本次结果。
- 每次采集都会重新读取岗位详情，已保存过的岗位也会重新打开；读取失败不以旧 JD 代替。本轮内重复命中的同一岗位只读取一次。
- 新任务要求 BossHunter 链路支持重新读取详情；若服务仍是旧版，请用更新后的脚本重启，或使用独立采集窗口。历史快照保留原来的缓存标记，不会被当成本次读取。
- 采集结果先进入待核对队列，可集中核对并批量入库，缺失字段或疑似重复单独处理。
- 断连中的既有任务有限重试。尚未配置且没有任务时，不反复自动重试；可配置后手动检查连接。
- 停止采集会保留已同步资料。恢复备份前必须先停止采集任务。

## 排查

| 提示 | 操作 |
| --- | --- |
| 尚未安装 WSL | 安装可选组件，或先使用粘贴 JD；本地功能仍可用 |
| 发行版或脚本不存在 | 对照实际发行版名称，确认脚本位于该发行版能访问的绝对路径 |
| 已请求启动，但服务仍未连接 | 检查 BossHunter 的独立环境和 config.yaml；查看 `%LOCALAPPDATA%\InternshipRadarPrivateRC\logs\bosshunter-start.log` |
| 服务存在但接口不兼容 | 使用择木的启动链加载 `tools/boss_radar_bridge.py`，核对上游版本与 Radar 接口 |
| 浏览器未连接 / 需要登录 | 使用专用 Chrome 窗口；普通 Chrome 的登录不会自动共享 |

专用 Chrome 配置保存在 `%LOCALAPPDATA%\InternshipRadarPrivateRC\BossHunterChrome`。它包含登录凭据，不属于可分享的诊断或岗位备份。上游尚未同步的采集数据仍在 BossHunter 自身 `data` 目录，Radar 的完整备份只覆盖已进入择木的资料。

上游接口及许可请查阅 [Agent API](https://github.com/shengjidaguai-china/BossHunter/blob/main/docs/AGENT_API.md) 与 [项目许可](https://github.com/shengjidaguai-china/BossHunter/blob/main/LICENSE)。
