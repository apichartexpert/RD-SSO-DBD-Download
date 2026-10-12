@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python taxdocs.py sort "%USERPROFILE%\Downloads"
pause
