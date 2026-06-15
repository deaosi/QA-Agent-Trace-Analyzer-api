@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "PID_FILE=%CD%\server.pid"
set "PORT=5000"
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="QA_PORT" set "PORT=%%B"
  )
)

:MENU
cls
echo ==================================================
echo   QA Agent Workbench - Service Manager
echo ==================================================
echo.
call :STATUS
echo.
echo  1. Deploy environment only
echo  2. Start service
echo  3. Stop service
echo  4. Restart service
echo  5. Open local workbench
echo  6. Open admin panel
echo  7. View service logs
echo  0. Exit
echo.
set /p CHOICE=Select:

if "%CHOICE%"=="1" call "deploy_only.bat"
if "%CHOICE%"=="2" call "start_server.bat"
if "%CHOICE%"=="3" call "stop_server.bat"
if "%CHOICE%"=="4" (
  call "stop_server.bat"
  call "start_server.bat"
)
if "%CHOICE%"=="5" start "" "http://127.0.0.1:%PORT%"
if "%CHOICE%"=="6" start "" "http://127.0.0.1:%PORT%/admin/users"
if "%CHOICE%"=="7" (
  if exist "server.log" type "server.log"
  if exist "server.log.err" type "server.log.err"
  pause
)
if "%CHOICE%"=="0" exit /b 0
goto MENU

:STATUS
if exist "%PID_FILE%" (
  set /p PID=<"%PID_FILE%"
  tasklist /FI "PID eq %PID%" | findstr "%PID%" >nul 2>nul
  if not errorlevel 1 (
    echo Status: running. PID: %PID%
    echo Local URL: http://127.0.0.1:%PORT%
    echo Admin URL: http://127.0.0.1:%PORT%/admin/users
    exit /b 0
  )
)
echo Status: stopped
exit /b 0
