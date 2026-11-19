# 择木 · 实习工作台

**v1.0** · Windows · 本地保存

找实习时，你可能在好几个网页之间来回翻。这份工作每周要去几天？上周投过的是哪一家？择木把每个岗位整理成一张可以回头看的卡片。招聘原文、原始链接和你的投递进度都放在一起。

## 下载并安装 Windows 版

**[下载择木 v1.0 安装程序（Windows x64）](https://github.com/cerveth07/zemu-internship-radar/releases/download/v1.0/Zemu-v1.0-Windows-x64-Setup.exe)**

1. 下载并双击 `Zemu-v1.0-Windows-x64-Setup.exe`，按提示安装。安装程序会为当前 Windows 用户创建开始菜单快捷方式。
2. 安装完成后，在开始菜单搜索 **择木** 并打开。桌面版已包含 Python、Node.js 和中文截图识别组件。
3. 第一次打开，可以直接点击 **「体验示例（加入 3 个示例岗位）」**，先用虚构资料熟悉工作台。

安装程序尚未做代码签名。需要核对文件时，可将下载文件的 SHA-256 与 [v1.0 发布页](https://github.com/cerveth07/zemu-internship-radar/releases/tag/v1.0) 的值比较。

适用于 64 位 Windows 10 和 11。如果打开时提示缺少 WebView2 或 Visual C++ 运行库，请安装对应的微软官方组件，再打开择木。[WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) · [Visual C++ x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)

## 第一次打开，点哪里？

先用虚构岗位走一遍。这个练习在本机完成，无需招聘平台账号。

1. 在「工作台」点击 **「体验示例（加入 3 个示例岗位）」**。择木会打开第一份岗位档案。这三个岗位都来自虚构的「山间文创」。
2. 打开左侧 **「岗位库」**，找到北京的「内容运营实习」，看看招聘原文。以后读到一份真正想投的工作，就可以用右上角的 **「添加岗位」** 保存它。界面里的 **JD** 就是招聘方写的岗位说明。
3. 投完简历，去 **「投递追踪」** 记下进度。下次打开择木，就能查看哪些岗位已经投过、哪些还要跟进。

如果只想试用，回到「工作台」点击 **「结束体验并移除示例」**。自己另外添加的岗位会保留。示例入口只在岗位库为空时出现。

## 想把招聘网站上的岗位收进来？

「采集收件箱」和「岗位库」就像桌上的两个文件夹。**收件箱**放刚找到、还要看一眼的资料。**岗位库**放你核对后决定留下的岗位。

要连接 BOSS 直聘或实习僧，准备好 Chrome 或 Edge。安装程序已包含连接器。仅源码版需要另装 Node.js 22 或以上版本，并在源码文件夹的终端运行下面这条命令。

```powershell
npm.cmd ci --prefix tools/shixiseng-node --ignore-scripts
```

然后回到「工作台」，输入一个岗位名，例如「产品运营实习」，选城市和招聘平台，点 **「开始检索」**。取得结果后点 **「核对本次结果」**，查看原文再收录。需要登录时，在平台窗口由你自己完成。连接真实平台前，请核对适用的接入条件。[首页检索说明](docs/home-keyword-search.md) · [BOSS 直聘](docs/boss-collection.md) · [实习僧](docs/shixiseng-collection.md) · [平台接入说明](LICENSE-STATUS.md#平台接入和账号操作)

**想收藏到自己的 BOSS 账号？** 在工作台点「按岗位清单查找 / BOSS 收藏」，粘贴岗位名称或原始链接。先核对匹配结果，再点「收藏所选岗位到 BOSS」。这一步会操作你登录的账号。平时在本地收录岗位，只需用岗位库。[BOSS 收藏操作说明](docs/boss-favorites.md)

## 我的记录放在哪里？

桌面版把岗位和投递记录保存在 `%LOCALAPPDATA%\InternshipRadarPrivateRC\data`。从 Windows「设置 → 应用」卸载择木会保留这里的资料。源码版默认保存在源码文件夹的 `data` 目录。换电脑或升级前，先在 **「数据与帮助」** 下载完整备份，收在自己能找到的地方。备份可能包含岗位截图和你填写的个人经历，请私下保存。[备份与恢复](docs/independent-desktop-use.md)

## 用熟以后

- 已经有岗位表？在「数据与帮助」下载示例 CSV，照着填两行，导入时先预览再确认。CSV 是 Excel 等表格软件可以打开的一种文件。
- 想比较岗位要求？在「AI 分析」选好要分析的岗位，再把导出的材料交给你选的 AI 助手。拿到结果后，可以回到择木预览并导入。发出前看一眼材料里包含哪些个人信息。[AI 分析使用说明](docs/agent-handoff.md)
- 只有招聘截图？桌面版自带中文文字识别（OCR），可直接从图片读取文字。使用源码版时，可先运行启动脚本，再在源码目录的终端执行下面两行，完成后重启择木。

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints-windows.txt -r requirements-ocr.txt
.\.venv\Scripts\rapidocr.exe download_models
```

## 从源码启动

1. 在 Windows 上安装 **Python 3.12**，安装时保留 Python 启动器（`py`）。如果电脑已经装好了，可以跳过这一步。
2. 把源码 ZIP **全部解压**到一个准备长期使用的文件夹，例如「文档」里的「择木」。打开里面有 `README.md` 的那一层，双击 **`start-windows.bat`**。
3. 第一次运行会联网下载需要的组件。等黑色窗口出现 `Uvicorn running`，在浏览器地址栏输入 `http://127.0.0.1:8785`。用的时候保持黑色窗口开着，用完在窗口里按 `Ctrl+C` 停止。下次仍然双击同一个文件。

如果窗口一闪就关，在这个文件夹的空白处右击，选择「在终端中打开」，输入 `.\start-windows.bat`。这样可以看清报错。如果提示找不到 `py` 或 Python 3.12，先完成第 1 步。

开发者可查看[本地接口与 MCP](docs/agent-api.md)、[数据库导出工具](tools/export_collected_data.py)或[贡献指南](CONTRIBUTING.md)。BOSS 城市映射收录 336 项，含「全国」。[城市覆盖和来源](docs/boss-city-mapping.md)

项目自有源码、文档、品牌图形和合成样例采用 MIT 许可证，署名 **cerveth07**。依赖和第三方材料各自适用随附许可。[MIT 许可证](LICENSE) · [第三方材料说明](LICENSE-STATUS.md) · [社区行为准则](CODE_OF_CONDUCT.md)
