@echo off
cd /d "%~dp0"
call "deploy_only.bat"
call "start_server.bat"
