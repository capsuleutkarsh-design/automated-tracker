@echo off
:: ================================================================
::  AUTOMATED CAMERA TRACKER V001.1 - BATCH RECONSTRUCTION
:: ================================================================
::  USAGE
::    • Double-click this .bat or run it from a command prompt.  
::    • Frames are extracted, features matched with loop closure,
::      and a 3D trajectory + collision mesh are solved.  
::
::  PURPOSE
::    Automated photogrammetry tracking engine for turning
::    videos/sequences into camera tracks with COLMAP SfM,
::    high-accuracy BA refinement, and multi-format exports.
::
::  FOLDER LAYOUT (all folders sit side-by-side):
::    00 PYTHON   – Embedded Python environment
::    01 COLMAP   – COLMAP binaries and DLLs
::    02 VIDEOS   – Place input video files (.mp4, .mov, etc.) here
::    03 FFMPEG   – FFmpeg binaries
::    04 SCENES   – Auto-generated output scene tracks
::    05 SCRIPT   – Scripts & tools
:: ================================================================

:: ---------- Resolve top-level folder (one up from this .bat) -----
pushd "%~dp0\.." >nul
set "TOP=%cd%"
popd >nul

:: ---------- Key paths -------------------------------------------
set "COLMAP_DIR=%TOP%\01 COLMAP"
set "VIDEOS_DIR=%TOP%\02 VIDEOS"
set "FFMPEG_DIR=%TOP%\03 FFMPEG"
set "SCENES_DIR=%TOP%\04 SCENES"
set "PYTHON_EXE=%TOP%\00 PYTHON\python.exe"

:: ---------- Locate ffmpeg.exe -----------------------------------
if exist "%FFMPEG_DIR%\ffmpeg.exe" (
    set "FFMPEG=%FFMPEG_DIR%\ffmpeg.exe"
) else if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" (
    set "FFMPEG=%FFMPEG_DIR%\bin\ffmpeg.exe"
) else (
    echo [ERROR] ffmpeg.exe not found inside "%FFMPEG_DIR%".
    pause & goto :eof
)

:: ---------- Locate colmap.exe (skip the .bat) --------------------
if exist "%COLMAP_DIR%\colmap.exe" (
    set "COLMAP=%COLMAP_DIR%\colmap.exe"
) else if exist "%COLMAP_DIR%\bin\colmap.exe" (
    set "COLMAP=%COLMAP_DIR%\bin\colmap.exe"
) else (
    echo [ERROR] colmap.exe not found inside "%COLMAP_DIR%".
    pause & goto :eof
)

:: ---------- Locate Vocab Tree for Offline Loop Detection --------
set "VOCAB_TREE="
if exist "%COLMAP_DIR%\vocab_tree_faiss_flickr100K_words256K.bin" (
    set "VOCAB_TREE=%COLMAP_DIR%\vocab_tree_faiss_flickr100K_words256K.bin"
) else (
    for %%F in ("%COLMAP_DIR%\*vocab*.bin") do (
        if not defined VOCAB_TREE set "VOCAB_TREE=%%~fF"
    )
)

:: ---------- Put COLMAP’s dll folder(s) on PATH -------------------
set "PATH=%COLMAP_DIR%;%COLMAP_DIR%\bin;%FFMPEG_DIR%;%FFMPEG_DIR%\bin;%PATH%"
set "QT_PLUGIN_PATH=%COLMAP_DIR%\plugins;%QT_PLUGIN_PATH%"

:: ---------- Ensure required folders exist ------------------------
if not exist "%VIDEOS_DIR%" (
    echo [ERROR] Input folder "%VIDEOS_DIR%" missing.
    pause & goto :eof
)
if not exist "%SCENES_DIR%" mkdir "%SCENES_DIR%"

:: ---------- Count videos for progress bar ------------------------
set "TOTAL=0"
for %%E in (mp4 mov avi mkv m4v wmv flv webm exr) do (
    for /f %%C in ('dir /b /a-d "%VIDEOS_DIR%\*.%%E" 2^>nul ^| find /c /v ""') do (
        set /a TOTAL+=%%C
    )
)

if "%TOTAL%"=="0" (
    echo [INFO] No compatible video files found in "%VIDEOS_DIR%".
    echo [INFO] Please place video files (.mp4, .mov, .avi, etc.) into "%VIDEOS_DIR%" and run again.
    pause
    goto :eof
)

echo ==============================================================
echo  Automated_Tracker_V001.1 Batch Processing %TOTAL% file(s)
echo ==============================================================

setlocal EnableDelayedExpansion
set /a IDX=0

for %%V in ("%VIDEOS_DIR%\*.*") do (
    set "EXT=%%~xV"
    set "VALID="
    for %%G in (.mp4 .mov .avi .mkv .m4v .wmv .flv .webm .MP4 .MOV .AVI .MKV) do (
        if /i "!EXT!"=="%%G" set "VALID=1"
    )
    if defined VALID (
        set /a IDX+=1
        call :PROCESS_VIDEO "%%~fV" "!IDX!" "%TOTAL%"
    )
)

echo --------------------------------------------------------------
echo  All batch jobs finished – results are in "%SCENES_DIR%".
echo --------------------------------------------------------------
pause
goto :eof


:PROCESS_VIDEO
:: ----------------------------------------------------------------
::  %1 = full path to video   %2 = current index   %3 = total
:: ----------------------------------------------------------------
setlocal
set "VIDEO=%~1"
set "NUM=%~2"
set "TOT=%~3"

for %%I in ("%VIDEO%") do (
    set "BASE=%%~nI"
    set "EXT=%%~xI"
)

echo.
echo [!NUM!/!TOT!] === Processing "!BASE!!EXT!" ===

:: -------- Directory layout for this scene (Shot, Date & Time Wise) ---------
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "DT=%%I"
if not defined DT (
    set "TIMESTAMP=%date:~10,4%-%date:~4,2%-%date:~7,2%_%time:~0,2%-%time:~3,2%-%time:~6,2%"
    set "TIMESTAMP=!TIMESTAMP: =0!"
) else (
    set "TIMESTAMP=!DT:~0,4!-!DT:~4,2!-!DT:~6,2!_!DT:~8,2!-!DT:~10,2!-!DT:~12,2!"
)

set "SHOT_DIR=%SCENES_DIR%\!BASE!"
set "IMG_DIR=!SHOT_DIR!\images"
set "TRACK_DIR=!SHOT_DIR!\3D_CAMERA_TRACK\!TIMESTAMP!"
set "SPARSE_DIR=!TRACK_DIR!\sparse"
set "DB_PATH=!TRACK_DIR!\database.db"

:: Create directories --------------------------------------------
if not exist "!IMG_DIR!" mkdir "!IMG_DIR!" >nul
mkdir "!SPARSE_DIR!" >nul

:: -------- 1) Extract every frame (if not already extracted) -----
dir /b "!IMG_DIR!\*.jpg" >nul 2>&1 || (
    echo        [1/4] Extracting frames with FFmpeg …
    "%FFMPEG%" -loglevel error -stats -i "!VIDEO!" -qscale:v 2 ^
        "!IMG_DIR!\frame_%%06d.jpg"
    if errorlevel 1 (
        echo        ✖ FFmpeg failed – skipping "!BASE!".
        goto :END
    )
)

:: Check at least one frame exists
dir /b "!IMG_DIR!\*.jpg" >nul 2>&1 || (
    echo        ✖ No frames extracted – skipping "!BASE!".
    goto :END
)

:: -------- 2) Feature extraction ---------------------------------
echo        [2/4] Extracting SIFT features …
"%COLMAP%" feature_extractor ^
    --database_path "!DB_PATH!" ^
    --image_path    "!IMG_DIR!" ^
    --ImageReader.camera_model SIMPLE_RADIAL ^
    --ImageReader.single_camera 1 ^
    --SiftExtraction.use_gpu 1 ^
    --SiftExtraction.max_image_size 4096
if errorlevel 1 (
    echo        ✖ feature_extractor failed – skipping "!BASE!".
    goto :END
)

:: -------- 3) Sequential matching (with Loop Detection) ----------
echo        [3/4] Sequential feature matching …
if defined VOCAB_TREE (
    echo        ✔ Using Vocabulary Tree for offline loop closure: "!VOCAB_TREE!"
    "%COLMAP%" sequential_matcher ^
        --database_path "!DB_PATH!" ^
        --SequentialMatching.overlap 35 ^
        --SequentialMatching.vocab_tree_path "!VOCAB_TREE!" ^
        --SequentialMatching.loop_detection 1
) else (
    "%COLMAP%" sequential_matcher ^
        --database_path "!DB_PATH!" ^
        --SequentialMatching.overlap 35 ^
        --SequentialMatching.loop_detection 0
)

if errorlevel 1 (
    echo        ✖ sequential_matcher failed – skipping "!BASE!".
    goto :END
)

:: -------- 4) Structure-from-Motion (Parallel GPU Refined) --------------
echo        [4/4] Solving 3D Camera Track (Parallel GPU BA Refined) …
"%COLMAP%" hierarchical_mapper ^
    --database_path "!DB_PATH!" ^
    --image_path    "!IMG_DIR!" ^
    --output_path   "!SPARSE_DIR!" ^
    --Mapper.ba_use_gpu 1 ^
    --Mapper.num_threads %NUMBER_OF_PROCESSORS% >nul 2>&1

:: Reorganize output to sparse/0 if needed
if exist "!SPARSE_DIR!\cameras.bin" (
    if not exist "!SPARSE_DIR!\0" mkdir "!SPARSE_DIR!\0" >nul
    move /y "!SPARSE_DIR!\cameras.*" "!SPARSE_DIR!\0\" >nul 2>&1
    move /y "!SPARSE_DIR!\images.*" "!SPARSE_DIR!\0\" >nul 2>&1
    move /y "!SPARSE_DIR!\points3D.*" "!SPARSE_DIR!\0\" >nul 2>&1
)

:: -------- Fallback: Incremental Mapper if fast solve didn't produce a model -----
if not exist "!SPARSE_DIR!\0" (
    echo        ↻ Running Incremental Mapper (High-Accuracy BA) …
    "%COLMAP%" mapper ^
        --database_path "!DB_PATH!" ^
        --image_path    "!IMG_DIR!" ^
        --output_path   "!SPARSE_DIR!" ^
        --Mapper.init_min_tri_angle 2.5 ^
        --Mapper.init_min_num_inliers 40 ^
        --Mapper.abs_pose_min_num_inliers 20 ^
        --Mapper.init_max_forward_motion 1.0 ^
        --Mapper.init_num_trials 500 ^
        --Mapper.ba_refine_focal_length 1 ^
        --Mapper.ba_refine_extra_params 1 ^
        --Mapper.ba_refine_principal_point 0 ^
        --Mapper.ba_use_gpu 1 ^
        --Mapper.num_threads %NUMBER_OF_PROCESSORS%
)

:: -------- Smart Auto-Retry on Low Parallax / Subtle Movement Shots ---
if not exist "!SPARSE_DIR!\0" (
    echo        ↻ Initial solve had low parallax – executing Smart Auto-Retry pass with relaxed angles...
    "%COLMAP%" mapper ^
        --database_path "!DB_PATH!" ^
        --image_path    "!IMG_DIR!" ^
        --output_path   "!SPARSE_DIR!" ^
        --Mapper.init_min_tri_angle 0.5 ^
        --Mapper.init_min_num_inliers 15 ^
        --Mapper.abs_pose_min_num_inliers 10 ^
        --Mapper.init_max_forward_motion 1.0 ^
        --Mapper.init_num_trials 1000 ^
        --Mapper.filter_min_tri_angle 0.5 ^
        --Mapper.ba_refine_focal_length 1 ^
        --Mapper.ba_refine_extra_params 1 ^
        --Mapper.ba_use_gpu 1 ^
        --Mapper.num_threads %NUMBER_OF_PROCESSORS%
)

:: -------- Export best model to TXT, Mesh, and Multi-Format Exports -----
if exist "!SPARSE_DIR!\0" (
    "%COLMAP%" model_converter ^
        --input_path  "!SPARSE_DIR!\0" ^
        --output_path "!SPARSE_DIR!" ^
        --output_type TXT >nul

    :: Surface Environment Mesh Reconstruction
    "%COLMAP%" delaunay_mesher ^
        --input_type sparse ^
        --input_path "!SPARSE_DIR!\0" ^
        --output_path "!TRACK_DIR!\environment_mesh.ply" >nul 2>&1

    if not exist "!TRACK_DIR!\environment_mesh.ply" (
        "%COLMAP%" poisson_mesher ^
            --input_path "!SPARSE_DIR!\0" ^
            --output_path "!TRACK_DIR!\environment_mesh.ply" >nul 2>&1
    )

    if exist "%PYTHON_EXE%" (
        "%PYTHON_EXE%" "%TOP%\05 SCRIPT\export_tools.py" "!TRACK_DIR!" >nul 2>&1
    )

    :: Sync to _latest for instant 1-click access
    if not exist "!SHOT_DIR!\3D_CAMERA_TRACK\_latest" mkdir "!SHOT_DIR!\3D_CAMERA_TRACK\_latest" >nul
    xcopy /e /y /q "!TRACK_DIR!\*" "!SHOT_DIR!\3D_CAMERA_TRACK\_latest\" >nul 2>&1

    echo        ✔ Finished "!BASE!" - Results in "04 SCENES\!BASE!\3D_CAMERA_TRACK\!TIMESTAMP!\".
) else (
    echo        ✖ Mapper completed but could not reconstruct camera poses for "!BASE!". Check if footage has enough texture and parallax.
)

:END
endlocal & goto :eof
