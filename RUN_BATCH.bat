@echo off
setlocal
:: 100% Portable Root Launcher for Batch Reconstruction
pushd "%~dp0" >nul
set "TOP=%cd%"
popd >nul

call "%TOP%\05 SCRIPT\batch_reconstruct.bat"
