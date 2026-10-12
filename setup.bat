@echo off
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem ---- make sure the program files are here (ZIP must be extracted first) ----
if not exist "%~dp0webapp.py" (
  echo.
  echo [X] Program files not found next to this .bat file:
  echo     %~dp0
  echo.
  echo     This usually means the ZIP was opened but NOT extracted.
  echo     1. Close this window
  echo     2. Right-click the ZIP file - choose "Extract All..." - Extract
  echo     3. Open the extracted folder and double-click run_web.bat there
  echo.
  pause
  exit /b 1
)

rem ---- find Python (py launcher first, then python) ----
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo [X] Python not found on this computer.
  echo     1. Install Python 3.12 from https://www.python.org/downloads/
  echo     2. On the first install screen, tick "Add python.exe to PATH"
  echo     3. Run this file again
  echo.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Using: %PY%

echo Installing required libraries...
%PY% -m pip install --disable-pip-version-check flask pypdf pyyaml playwright
if errorlevel 1 (
  echo [X] Library install failed. Check the internet connection and try again.
  pause
  exit /b 1
)
echo Installing browser support...
%PY% -m playwright install chromium
echo.
echo Setup complete. Now double-click run_web.bat
pause
