@echo off
rem Stop what Start Genesis started on this PC.
setlocal
title Genesis
if exist "%~dp0genesis.cmd" (set "GENESIS_CMD=%~dp0genesis.cmd") else (set "GENESIS_CMD=%~dp0genesis\genesis.cmd")
call "%GENESIS_CMD%" stop
echo.
pause
