@echo off
setlocal EnableDelayedExpansion
:: ==========================================================================
::  Automated Tracker - one-click build
::
::    step 1  Python  : freeze 05 SCRIPT\tracker_gui.py into dist\Automated_Tracker
::    step 2  Inno    : package that plus COLMAP / FFmpeg / CoTracker weights
::                      into build\Output\AutomatedTracker_Setup_<version>.exe
::                      plus AutomatedTracker_Setup_<version>-1.bin, -2.bin ...
::                      (the payload is over 2 GB compressed, so Inno has to
::                      split it; ship the exe and every .bin together)
::
::  Usage:
::    BUILD.bat              full build
::    BUILD.bat clean        wipe dist\ and work\ first
::    BUILD.bat exe          stop after the executable, skip the installer
::    BUILD.bat installer    skip freezing, just re-run Inno on the last build
::    BUILD.bat check        only verify prerequisites
:: ==========================================================================

pushd "%~dp0" >nul
set "BUILD_DIR=%cd%"
popd >nul
pushd "%BUILD_DIR%\.." >nul
set "ROOT=%cd%"
popd >nul

set "PY=%ROOT%\00 PYTHON\python.exe"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=all"

echo.
echo ================================================================
echo   Automated Tracker build
echo   root : %ROOT%
echo   mode : %MODE%
echo ================================================================
echo.

:: ---------- locate a Python ------------------------------------------------
if not exist "%PY%" (
    echo [build] bundled interpreter not found, trying system python...
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [build] ERROR: no Python found.
        echo         Expected: %ROOT%\00 PYTHON\python.exe
        echo         or python on PATH.
        echo.
        pause
        exit /b 1
    )
    set "PY=python"
)

:: ---------- locate Inno Setup ----------------------------------------------
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P if not defined ISCC set "ISCC=%%~P"
)

if "%MODE%"=="check" (
    "%PY%" "%BUILD_DIR%\build_app.py" --check
    if defined ISCC (echo [build]   ok       Inno Setup: !ISCC!) else (echo [build]   MISSING  Inno Setup)
    echo.
    pause
    exit /b 0
)

:: ---------- step 1 : freeze -------------------------------------------------
if /i not "%MODE%"=="installer" (
    echo [build] ---- step 1 of 2 : building the executable ----
    if /i "%MODE%"=="clean" (
        "%PY%" "%BUILD_DIR%\build_app.py" --clean
    ) else (
        "%PY%" "%BUILD_DIR%\build_app.py"
    )
    if errorlevel 1 (
        echo.
        echo [build] ERROR: the executable step failed. Stopping.
        echo.
        pause
        exit /b 1
    )
)

if /i "%MODE%"=="exe" (
    echo.
    echo [build] Executable only - skipping the installer.
    echo [build] Result: %BUILD_DIR%\dist\Automated_Tracker\Automated_Tracker.exe
    echo.
    pause
    exit /b 0
)

:: ---------- step 2 : installer ---------------------------------------------
echo.
echo [build] ---- step 2 of 2 : building the installer ----

if not defined ISCC (
    echo.
    echo [build] ERROR: Inno Setup compiler ^(ISCC.exe^) not found.
    echo         Looked in Program Files for "Inno Setup 6" and "Inno Setup 5".
    echo         Install it from https://jrsoftware.org/isdl.php
    echo         The executable is still available at:
    echo           %BUILD_DIR%\dist\Automated_Tracker\Automated_Tracker.exe
    echo.
    pause
    exit /b 1
)

if not exist "%BUILD_DIR%\dist\Automated_Tracker\Automated_Tracker.exe" (
    echo.
    echo [build] ERROR: no built executable to package.
    echo         Run BUILD.bat without "installer" first.
    echo.
    pause
    exit /b 1
)

if exist "%BUILD_DIR%\Output\AutomatedTracker_Setup_*" (
    echo [build] clearing previous installer output
    del /q "%BUILD_DIR%\Output\AutomatedTracker_Setup_*" >nul 2>&1
)
echo [build] using %ISCC%
"%ISCC%" /Q "%BUILD_DIR%\installer.iss"
if errorlevel 1 (
    echo.
    echo [build] ERROR: Inno Setup failed.
    echo.
    pause
    exit /b 1
)

:: Inno splits the payload into slices. Make sure they actually exist - a
:: compile that "succeeds" but leaves only the exe would install nothing.
dir /b "%BUILD_DIR%\Output\*.bin" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [build] ERROR: Inno Setup wrote no .bin slices next to the installer.
    echo         Check that DiskSpanning=yes is still set in installer.iss.
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   BUILD COMPLETE
echo.
echo   Executable : %BUILD_DIR%\dist\Automated_Tracker\Automated_Tracker.exe
echo   Installer  : %BUILD_DIR%\Output\
echo.
echo   The installer is the setup exe PLUS its .bin slices. Ship them
echo   together in the same folder - the exe alone will not install.
echo ================================================================
echo.
dir /b "%BUILD_DIR%\Output\*.exe" "%BUILD_DIR%\Output\*.bin" 2>nul
echo.
pause
exit /b 0
