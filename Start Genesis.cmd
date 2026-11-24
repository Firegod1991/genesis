@echo off
rem Start Genesis on this PC: double click this file. The dashboard opens in your browser.
rem The first time, it offers to download a starter library. Needs Python 3.9 or newer.
setlocal
title Genesis
if exist "%~dp0genesis.cmd" (set "GENESIS_CMD=%~dp0genesis.cmd") else (set "GENESIS_CMD=%~dp0genesis\genesis.cmd")
call "%GENESIS_CMD%" start %*
set "GENESIS_RC=%errorlevel%"
echo.
if "%GENESIS_RC%"=="0" (echo Genesis keeps running after this window closes. To stop it, double click Stop Genesis.) else (echo Genesis did not start. The lines above say why.)
echo.
pause
exit /b %GENESIS_RC%
