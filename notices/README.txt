第三方依赖的许可与来源

distributions.json 记录完整私有版 Windows 测试、构建环境的 Python 包和 wheel 附带的许可文本。它也包括仅在开发时使用的工具。CPython-LICENSE.txt 记录测试所用解释器的许可。平台连接器随桌面包使用 Node 和 Playwright Core，对应的许可和第三方通知保存在 node 和 playwright-core 子目录。OCR 模型权重的授权需与识别引擎的代码许可分别核对。

Microsoft.Web.WebView2-1.0.3856.49 目录含该版本官方 NuGet 包的 LICENSE.txt、NOTICE.txt，以及比对 pywebview 所带五个 SDK 文件的哈希和包内路径。核对来源为 https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49 。

proxy_tools 0.1.0 在 wheel 元数据中标记 MIT，模块头部标记 BSD。已将该模块与上游提交 db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6 逐字比较并随附上游 BSD 原文。核对来源为 https://github.com/jtushman/proxy_tools/tree/db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6 。公开二进制前请记录这项差异的处理结论。

本次仅提供源码。今后如分发 EXE，还需按其实际内含组件核对 VC 运行库、CPython 自带的原生库、Pillow 图像库、CLR 桥、PyInstaller 引导程序及随附的间接组件。项目自身的许可证及源码材料的范围见根目录 LICENSE 和 LICENSE-STATUS.md。
