@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m playwright install chromium
echo.
echo Setup complete. Edit config.yaml before first use.
pause
