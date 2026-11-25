@echo off
rem Genesis on Windows. In a terminal opened in this folder the commands are the same as everywhere:
rem     genesis start        genesis fetch --tier seed        genesis stop
rem Needs Python 3.9 or newer: the one in the python folder of the Windows download, or one installed
rem from the Microsoft Store or from https://www.python.org/downloads/
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "GENESIS_PY="
if exist "%~dp0python\python.exe" set "GENESIS_PY="%~dp0python\python.exe""
if not defined GENESIS_PY py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && set "GENESIS_PY=py -3"
if not defined GENESIS_PY python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && set "GENESIS_PY=python"
if not defined GENESIS_PY (
  echo Genesis needs Python 3.9 or newer, and this PC has none that answers.
  echo Get it from the Microsoft Store, or from https://www.python.org/downloads/ and run this again.
  exit /b 1
)
%GENESIS_PY% -m genesis %*
