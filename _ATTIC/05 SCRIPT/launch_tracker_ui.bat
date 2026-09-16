@echo off
setlocal

:: ---------- Resolve Root Directory (100% Relative) -----
pushd "%~dp0\.." >nul
set "TOP=%cd%"
popd >nul

echo Starting Automated_Tracker_V001.1 Fallback UI...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TOP%\05 SCRIPT\tracker_ui.ps1"
