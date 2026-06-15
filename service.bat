@echo off
chcp 65001 >nul
title QA Service Manager

:menu
cls
echo ================================================
echo     QA Service Manager
echo ================================================
echo.
echo  1. Check Status
echo  2. Start Service
echo  3. Stop Service
echo  4. Restart Service
echo  5. Open Workbench
echo  6. Open Admin Panel
echo  0. Exit
echo.
echo ================================================

set /p choice=Select (0-6):

if "%choice%"=="1" goto :status
if "%choice%"=="2" goto :start
if "%choice%"=="3" goto :stop
if "%choice%"=="4" goto :restart
if "%choice%"=="5" goto :open_wb
if "%choice%"=="6" goto :open_admin
if "%choice%"=="0" goto :exit

goto :menu

:status
cls
echo Checking service status...
curl -s -o nul -w "Status Code: %%{http_code}\n" http://127.0.0.1:5000
if %errorlevel%==0 (
    echo Service: RUNNING
    echo URL: http://127.0.0.1:5000
) else (
    echo Service: STOPPED
)
echo.
pause
goto :menu

:start
cls
echo Starting service...
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo Service is already running!
    pause
    goto :menu
)

cd /d "%~dp0"
start "QA Service" cmd /k "set QA_PORT=5000 && set QA_ADMIN_USERNAME=shuxing666 && set QA_ADMIN_PASSWORD=asdfghjkl && set QA_ACCESS_PASSWORD=asdfghjkl && set QA_SECRET_KEY=qa-workbench-secret-key-2026-06-13-random-string && set QA_DATA_DIR=%~dp0data && set FLASK_APP=wsgi:app && .venv\Scripts\python.exe -m flask run --host=0.0.0.0 --port=5000"
echo Service starting...
timeout /t 3 /nobreak >nul
echo Done! Service should be running.
pause
goto :menu

:stop
cls
echo Stopping service...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo Stopping PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)
echo Service stopped.
pause
goto :menu

:restart
cls
echo Restarting service...
goto :stop
timeout /t 2 /nobreak >nul
goto :start

:open_wb
start http://127.0.0.1:5000
goto :menu

:open_admin
start http://127.0.0.1:5000/admin/users
goto :menu

:exit
exit
