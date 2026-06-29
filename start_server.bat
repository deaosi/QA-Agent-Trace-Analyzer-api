@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "PID_FILE=%CD%\server.pid"
set "LOG_FILE=%CD%\server.log"

if not exist ".env" (
  echo .env not found. Please run deploy_only.bat first.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment not found. Please run deploy_only.bat first.
  pause
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if not "%%A"=="" set "%%A=%%B"
)

if "%QA_PORT%"=="" set "QA_PORT=5000"
if "%QA_ADMIN_USERNAME%"=="" set "QA_ADMIN_USERNAME=admin"
if "%QA_ADMIN_PASSWORD%"=="" (
  echo QA_ADMIN_PASSWORD is missing. Please edit .env or run deploy_only.bat again.
  pause
  exit /b 1
)
if "%QA_ACCESS_PASSWORD%"=="" set "QA_ACCESS_PASSWORD=%QA_ADMIN_PASSWORD%"
if "%QA_SECRET_KEY%"=="" (
  echo QA_SECRET_KEY is missing. Please edit .env or run deploy_only.bat again.
  pause
  exit /b 1
)
if "%QA_DATA_DIR%"=="" set "QA_DATA_DIR=%CD%\data"

if not exist "%QA_DATA_DIR%" mkdir "%QA_DATA_DIR%"

if exist "%PID_FILE%" (
  set /p OLD_PID=<"%PID_FILE%"
  tasklist /FI "PID eq %OLD_PID%" | findstr "%OLD_PID%" >nul 2>nul
  if not errorlevel 1 (
    echo Service is already running. PID: %OLD_PID%
    goto SHOW_INFO
  )
)

echo Starting service in background...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:QA_PORT='%QA_PORT%'; $env:QA_ADMIN_USERNAME='%QA_ADMIN_USERNAME%'; $env:QA_ADMIN_PASSWORD='%QA_ADMIN_PASSWORD%'; $env:QA_ACCESS_PASSWORD='%QA_ACCESS_PASSWORD%'; $env:QA_SECRET_KEY='%QA_SECRET_KEY%'; $env:QA_DATA_DIR='%QA_DATA_DIR%'; $p=Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList '-m','waitress','--listen=0.0.0.0:%QA_PORT%','wsgi:app' -WorkingDirectory '%CD%' -RedirectStandardOutput '%LOG_FILE%' -RedirectStandardError '%LOG_FILE%.err' -WindowStyle Hidden -PassThru; Set-Content -Path '%PID_FILE%' -Value $p.Id"

:SHOW_INFO
echo.
echo ==================================================
echo   Local URL: http://127.0.0.1:%QA_PORT%
echo   Public URL: http://YOUR_SERVER_PUBLIC_IP:%QA_PORT%
echo   Admin URL: http://YOUR_SERVER_PUBLIC_IP:%QA_PORT%/admin/users
echo   Admin username: %QA_ADMIN_USERNAME%
echo   Admin password: saved in .env
echo   Data dir: %QA_DATA_DIR%
echo ==================================================
echo.
pause
