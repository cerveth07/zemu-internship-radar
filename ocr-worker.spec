from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
data, libs, imports = collect_all('rapidocr')
analysis = Analysis([str(root / 'tools' / 'ocr_worker.py')], pathex=[str(root)],
                    datas=data, binaries=libs, hiddenimports=imports,
                    excludes=['webview', 'pytest', 'IPython', 'matplotlib'])
analysis.binaries = [
    item for item in analysis.binaries
    if not Path(item[0]).name.lower().startswith(('opencv_videoio_ffmpeg', 'api-ms-win-', 'ext-ms-win-'))
    and Path(item[0]).name.lower() not in {'ucrtbase.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll'}
]
exe = EXE(PYZ(analysis.pure), analysis.scripts, [], name='ZemuOCR', exclude_binaries=True,
          console=True, upx=False, icon=str(root / 'assets' / 'radar.ico'))
COLLECT(exe, analysis.binaries, analysis.datas, name='ZemuOCR', upx=False)
