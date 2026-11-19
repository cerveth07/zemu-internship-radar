@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -c constraints-windows.txt -r requirements.txt
if errorlevel 1 exit /b 1
echo http://127.0.0.1:8785
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8785
exit /b %errorlevel%
