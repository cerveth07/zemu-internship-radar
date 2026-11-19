@echo off
setlocal
cd /d "%~dp0"

echo [Zemu] Build Windows portable desktop EXE
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.12 x64 is required to build the EXE.
    echo Install it from https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
if not exist ".build-venv\Scripts\python.exe" (
    echo [1/7] Create build environment...
    py -3.12 -m venv .build-venv
    if errorlevel 1 goto failed
)

".build-venv\Scripts\python.exe" -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8, 'Build venv must use 64-bit Python 3.12'"
if errorlevel 1 goto failed

echo [2/7] Install build and test dependencies...
".build-venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m pip install -c constraints-windows.txt -r requirements-desktop.txt -r requirements-dev.txt
if errorlevel 1 goto failed

echo [3/7] Run P0-P3 backend regression tests...
".build-venv\Scripts\python.exe" tools\prepare_shixiseng_runtime.py
if errorlevel 1 goto failed
node tools\shixiseng-node\node_modules\playwright-core\cli.js install chromium
if errorlevel 1 goto failed
set "RADAR_PLAYWRIGHT_MODULE=%CD%\tools\shixiseng-node\node_modules\playwright-core"
set "RADAR_PYTHON=%CD%\.build-venv\Scripts\python.exe"
set "RADAR_REQUIRE_BROWSER=1"
node --test tools\test_boss_runner.mjs tools\test_boss_favorites_runner.mjs tools\test_shixiseng_runner.mjs tools\test_chrome_use_browser.mjs
if errorlevel 1 goto failed
for %%T in (check_boss_search_ui check_shixiseng_ui check_boss_favorites_ui) do (
    node tools\%%T.cjs
    if errorlevel 1 goto failed
)
node --check static\listing-pagination.js
if errorlevel 1 exit /b 1
node --check static\app.js
if errorlevel 1 goto failed
node --check static\export-manager.js
if errorlevel 1 goto failed
node --check static\analysis-workflows.js
if errorlevel 1 goto failed
node --check static\ai-review.js
if errorlevel 1 goto failed
node --check static\career-prompts.js
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed

echo [4/7] Download OCR models and build icon...
".build-venv\Scripts\rapidocr.exe" download_models
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -c "from app.ocr_adapter import smoke_check; from pathlib import Path; print(smoke_check(Path('assets/ocr-smoke.b64')))"
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" tools\make_icon.py
if errorlevel 1 goto failed

echo [5/7] Package standalone EXE...
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean desktop.spec
if errorlevel 1 goto failed
if not exist "dist\InternshipRadarPrivateRC.exe" goto failed

echo [6/7] Test the packaged EXE...
if not exist build mkdir build
if exist "build\desktop-self-test.json" del /q "build\desktop-self-test.json"
start "" /wait "dist\InternshipRadarPrivateRC.exe" --self-test "%CD%\build\desktop-self-test.json" --require-ocr
if errorlevel 1 goto failed
if not exist "build\desktop-self-test.json" goto failed
".build-venv\Scripts\python.exe" -c "import json,pathlib; p=pathlib.Path('build/desktop-self-test.json'); r=json.loads(p.read_text(encoding='utf-8')); print(r); assert r['result']=='passed' and r['ocr']=='passed' and r['career_handoff'] is True and r['database'] is True"
if errorlevel 1 goto failed

echo [7/7] Write SHA256 checksum...
powershell -NoProfile -Command "$sha=(Get-FileHash 'dist\InternshipRadarPrivateRC.exe' -Algorithm SHA256).Hash.ToLower(); ($sha + '  InternshipRadarPrivateRC.exe') | Set-Content -Encoding ASCII 'dist\SHA256SUMS.txt'"
if errorlevel 1 goto failed

echo.
echo [SUCCESS] dist\InternshipRadarPrivateRC.exe
explorer "dist"
pause
exit /b 0

:failed
echo [FAILED] Regression, packaging or EXE self-test failed. Nothing should be distributed.
pause
exit /b 1
