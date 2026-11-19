@echo off
if not defined RADAR_BOSSHUNTER_WSL_DISTRO goto setup
if not defined RADAR_BOSSHUNTER_WSL_SCRIPT goto setup
wsl.exe -d "%RADAR_BOSSHUNTER_WSL_DISTRO%" -- bash "%RADAR_BOSSHUNTER_WSL_SCRIPT%"
if errorlevel 1 pause
exit /b
:setup
echo Configure the optional BOSS connector in the desktop app: Data and Help.
echo For this developer launcher, set RADAR_BOSSHUNTER_WSL_DISTRO and RADAR_BOSSHUNTER_WSL_SCRIPT first.
pause
exit /b 1
