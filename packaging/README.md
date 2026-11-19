# 构建 Windows x64 轻量版

安装程序包含桌面应用、Node.js、Playwright 和三个采集与收藏连接器。OCR 使用独立的 Windows 进程。构建机需要 Windows x64、Python 3.12、Node.js 和 NSIS 3。以下 PowerShell 命令在源码根目录运行。

```powershell
py -3.12 -m venv .build-venv
.\.build-venv\Scripts\Activate.ps1
python -m pip install -c constraints-windows.txt -r requirements-desktop.txt -r requirements-dev.txt
python tools/prepare_shixiseng_runtime.py
python tools/make_icon.py
```

## 使用已发布的 OCR 组件

仓库内的 `packaging/ocr-component.json` 记录 v1.0 已发布组件的字节数和 SHA-256。使用此文件构建的轻量版首次识别截图时下载同一附件。将 v1.0 完整安装目录的 `Licenses` 文件夹路径填到下面的变量中，用于随包分发对应版本的第三方声明。

```powershell
$licenses = Join-Path $env:LOCALAPPDATA 'Programs\Zemu\Licenses'
python -m PyInstaller --noconfirm --workpath build/lite-pyi --distpath build/lite/appdist desktop.spec -- --lite
python tools/prepare_lite_installer.py build/lite $licenses
makensis build/lite/setup.nsi
```

输出文件为 `build/lite/Zemu-v1.0-Windows-x64-Lite-Setup.exe`。普通自检覆盖采集和收藏，在干净的用户环境中不下载 OCR。加上 `--require-ocr` 会实际读取合成测试图，并触发首次下载。

```powershell
$app = 'build/lite/appdist/Zemu/Zemu.exe'
Start-Process $app -ArgumentList '--self-test build/lite-self-test.json' -Wait
Start-Process $app -ArgumentList '--self-test build/lite-ocr-test.json --require-ocr' -Wait
```

## 为后续发行重建 OCR

下列命令生成含识别程序、三份中文模型、许可证和对应 GEOS、Shapely 源码的 ZIP，同时更新本地校验清单。新构建的 ZIP 可以有不同的哈希。发行时先在新版本的 Release 放置对应附件，将 `app/ocr_component.py` 的固定下载地址指向它，再用新的清单构建轻量版。旧版客户端仍需要其原有的附件。

```powershell
rapidocr download_models
python -m PyInstaller --noconfirm --workpath build/ocr-pyi --distpath build/components ocr-worker.spec
python tools/package_ocr_component.py build/components/ZemuOCR $licenses build/Zemu-v1.0-OCR-Windows-x64.zip packaging/ocr-component.json
```
