@echo off
setlocal EnableDelayedExpansion
:: ==========================================================================
::  Automated Tracker - first-run setup for a git clone
::
::  The repository holds the source only. The bundled runtime (Python with
::  PyTorch, COLMAP, FFmpeg, the CoTracker3 weights and the UI backgrounds)
::  is ~5 GB and lives on the GitHub release as zip parts. This script
::  downloads those parts, checks them and unpacks them into this folder.
::  It uses only tools that ship with Windows 10/11 (curl, certutil, tar).
::
::    SETUP.bat            download what is missing and unpack
::    SETUP.bat force      re-download and unpack everything
::
::  Set ATRACK_RUNTIME_URL to fetch the parts from somewhere else, e.g. a
::  local folder:  set ATRACK_RUNTIME_URL=file:///D:/runtime
:: ==========================================================================

pushd "%~dp0" >nul
set "ROOT=%cd%"
popd >nul

set "BASE=%ATRACK_RUNTIME_URL%"
if "%BASE%"=="" set "BASE=https://github.com/capsuleutkarsh-design/automated-tracker/releases/latest/download"
set "DL=%ROOT%\_runtime_download"
set "MODE=%~1"

:: Always the Windows-supplied tools, by full path. Git for Windows and MSYS
:: put their own tar/curl on PATH, and that tar reads C:\... as a network host.
set "SYS=%SystemRoot%\System32"
set "CURL=%SYS%\curl.exe"
set "TAR=%SYS%\tar.exe"

echo.
echo ================================================================
echo   Automated Tracker - runtime setup
echo   folder : %ROOT%
echo   source : %BASE%
echo ================================================================
echo.

if /i not "%MODE%"=="force" (
    if exist "%ROOT%\00 PYTHON\python.exe" if exist "%ROOT%\01 COLMAP\bin" if exist "%ROOT%\03 FFMPEG\bin" if exist "%ROOT%\06 COTRACKER\checkpoints\scaled_offline.pth" (
        echo [setup] The runtime is already in place. Run LAUNCH_UI.bat to start.
        echo         Use "SETUP.bat force" to download it again.
        echo.
        pause
        exit /b 0
    )
)

:: PyTorch's deepest files sit ~200 characters below the root. Windows still
:: caps a path at 260 unless long paths are enabled, and beyond that Python
:: silently fails to import modules. Warn early rather than fail obscurely.
call :strlen "%ROOT%" ROOTLEN
if !ROOTLEN! GTR 60 (
    echo [setup] WARNING: this folder's path is !ROOTLEN! characters long.
    echo         Paths inside the runtime can exceed Windows' 260-character
    echo         limit from here and the app may fail to import PyTorch.
    echo         Move the folder somewhere short such as C:\automated-tracker
    echo         or enable long paths in Windows, then run SETUP.bat again.
    echo.
)

for %%T in ("%CURL%" "%TAR%" "%SYS%\certutil.exe") do (
    if not exist %%T (
        echo [setup] ERROR: %%~nxT not found in %SYS%.
        echo         It ships with Windows 10 1803 and later - update Windows.
        pause
        exit /b 1
    )
)

if not exist "%DL%" mkdir "%DL%"

echo [setup] fetching the part list
"%CURL%" -L --fail --silent --show-error -o "%DL%\runtime.parts.txt" "%BASE%/runtime.parts.txt"
if errorlevel 1 (
    echo.
    echo [setup] ERROR: could not download runtime.parts.txt from
    echo         %BASE%
    echo         Check your connection, or that the release has been published.
    pause
    exit /b 1
)

set "FAILED="
for /f "usebackq tokens=1,2,3" %%A in ("%DL%\runtime.parts.txt") do (
    call :fetch "%%A" "%%B" "%%C"
    if errorlevel 1 set "FAILED=1"
)
if defined FAILED (
    echo.
    echo [setup] ERROR: one or more parts could not be downloaded or verified.
    echo         Run SETUP.bat again - finished parts are kept and skipped.
    pause
    exit /b 1
)

echo.
echo [setup] unpacking into %ROOT%
for /f "usebackq tokens=1" %%A in ("%DL%\runtime.parts.txt") do (
    echo [setup]   %%A
    "%TAR%" -xf "%DL%\%%A" -C "%ROOT%"
    if errorlevel 1 (
        echo [setup] ERROR: could not unpack %%A
        pause
        exit /b 1
    )
)

if not exist "%ROOT%\02 VIDEOS" mkdir "%ROOT%\02 VIDEOS"
if not exist "%ROOT%\04 SCENES" mkdir "%ROOT%\04 SCENES"

echo [setup] removing downloads
rmdir /s /q "%DL%"

echo.
echo ================================================================
echo   SETUP COMPLETE - run LAUNCH_UI.bat to start Automated Tracker
echo ================================================================
echo.
pause
exit /b 0


:: ---- download one part and check its hash -------------------------------
:: %1 name  %2 sha256  %3 size
:fetch
set "NAME=%~1"
set "WANT=%~2"
set "FILE=%DL%\%NAME%"

if exist "%FILE%" (
    call :hash "%FILE%"
    if /i "!HASH!"=="%WANT%" (
        echo [setup] %NAME%  already downloaded
        exit /b 0
    )
)
echo [setup] downloading %NAME%  ^(%~3 bytes^)
"%CURL%" -L --fail -C - --progress-bar -o "%FILE%" "%BASE%/%NAME%"
if errorlevel 1 (
    echo [setup]   download failed
    exit /b 1
)
call :hash "%FILE%"
if /i not "!HASH!"=="%WANT%" (
    echo [setup]   checksum mismatch, deleting the file
    del /q "%FILE%"
    exit /b 1
)
echo [setup]   verified
exit /b 0

:: ---- length of a string -> %2 --------------------------------------------
:strlen
setlocal EnableDelayedExpansion
set "s=%~1"
set "n=0"
:strlen_loop
if defined s (
    set "s=!s:~1!"
    set /a n+=1
    goto :strlen_loop
)
endlocal & set "%~2=%n%"
exit /b 0

:: ---- sha256 of a file via certutil -> HASH ------------------------------
:: certutil is called by bare name on purpose: a quoted full path inside
:: for /f ('...') trips cmd's quote stripping and the command never runs.
:: System32 is always on PATH and nothing else ships a certutil.
:hash
set "HASH="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%~1" SHA256 ^| findstr /v /i "CertUtil"') do (
    if not defined HASH set "HASH=%%H"
)
set "HASH=!HASH: =!"
exit /b 0
