# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()
node_runtime = ROOT / 'tools' / 'shixiseng-node'
if not (node_runtime / 'node.exe').is_file() or not (node_runtime / 'node_modules' / 'playwright-core').is_dir():
    raise SystemExit('Run python tools/prepare_shixiseng_runtime.py before packaging')
web_datas, web_binaries, web_imports = collect_all('webview')
ocr_datas, ocr_binaries, ocr_imports = collect_all('rapidocr')
uvicorn_imports = collect_submodules('uvicorn')

a = Analysis(
    [str(ROOT / 'desktop.py')],
    pathex=[str(ROOT)],
    binaries=web_binaries + ocr_binaries,
    datas=web_datas + ocr_datas + [(str(ROOT / 'notices'), 'notices'), (str(ROOT / 'static'), 'static'), (str(ROOT / 'assets' / 'ocr-smoke.b64'), 'assets'),
          (str(ROOT / 'assets' / 'radar.ico'), 'assets'),
          (str(ROOT / 'vendor'), 'vendor'),
          (str(node_runtime / 'node_modules'), 'tools/shixiseng-node/node_modules'),
          (str(node_runtime / 'node.exe'), 'tools/shixiseng-node'),
          (str(ROOT / 'tools' / 'shixiseng_runner.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'boss_runner.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'boss_favorites_runner.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'radar_mcp.py'), 'tools'),
          (str(ROOT / 'tools' / 'chrome_use_browser.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'boss-cities.json'), 'tools'),
          (str(ROOT / 'tools' / 'collector_runtime.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'company_profile_dom.mjs'), 'tools'),
          (str(ROOT / 'tools' / 'start-collection-browser.ps1'), 'tools')],
    hiddenimports=web_imports + ocr_imports + uvicorn_imports + ['clr', 'pythonnet'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='InternshipRadarPrivateRC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / 'assets' / 'radar.ico'),
)
