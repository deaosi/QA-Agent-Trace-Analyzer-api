@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "111\service_panel.py"
) else if exist ".venv\Scripts\python.exe" (
  start "" ".venv\Scripts\python.exe" "111\service_panel.py"
) else (
  start "" python "111\service_panel.py"
)
