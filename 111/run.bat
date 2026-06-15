@echo off
cd /d "%~dp0"
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "& {python app.py 2>&1 | Tee-Object -FilePath server.log}"
