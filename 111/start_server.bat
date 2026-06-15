@echo off
cd /d "%~dp0"
python app.py > server.log 2>&1
