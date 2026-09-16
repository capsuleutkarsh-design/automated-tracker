@echo off
setlocal
:: 100% Portable Local Python & PySide6 UI Launcher
pushd "%~dp0" >nul
set "TOP=%cd%"
popd >nul

set "LOCAL_PY=%TOP%\00 PYTHON\python.exe"
set "SCRIPT_PY=%TOP%\05 SCRIPT\tracker_gui.py"
set "COLMAP_DIR=%TOP%\01 COLMAP"
set "FFMPEG_DIR=%TOP%\03 FFMPEG"

:: Add local binaries and embedded python to PATH
set "PATH=%COLMAP_DIR%\bin;%FFMPEG_DIR%\bin;%COLMAP_DIR%;%FFMPEG_DIR%;%TOP%\00 PYTHON;%TOP%\00 PYTHON\Scripts;%PATH%"
set "QT_PLUGIN_PATH=%COLMAP_DIR%\plugins;%QT_PLUGIN_PATH%"

if exist "%LOCAL_PY%" (
    echo Starting Automated_Tracker_V001.1 GUI...
    "%LOCAL_PY%" "%SCRIPT_PY%"
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Tracker GUI exited with code %errorlevel%.
        pause
    )
    goto :eof
)

:: Fallback if 00 PYTHON is missing
where python >nul 2>&1
if %errorlevel% equ 0 (
    python "%SCRIPT_PY%"
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Tracker GUI exited with code %errorlevel%.
        pause
    )
    goto :eof
)

echo.
echo [ERROR] No Python runtime found.
echo.
echo   The bundled interpreter is missing:
echo     the "00 PYTHON" folder inside this tracker directory
echo.
echo   Either restore the "00 PYTHON" folder, or install Python 3.11+
echo   and make sure it is on your PATH, then run this again.
echo.
pause
goto :eof
