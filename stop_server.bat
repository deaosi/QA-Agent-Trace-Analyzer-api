@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul

set "PID_FILE=%CD%\server.pid"
set "PORT=5000"
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="QA_PORT" set "PORT=%%B"
  )
)

set "STOPPED=0"

if exist "%PID_FILE%" (
  set /p PID=<"%PID_FILE%"
  if not "%PID%"=="" (
    tasklist /FI "PID eq %PID%" | findstr "%PID%" >nul 2>nul
    if not errorlevel 1 (
      taskkill /PID %PID% /T /F >nul 2>nul
      set "STOPPED=1"
    )
  )
)

for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  taskkill /PID %%P /T /F >nul 2>nul
  set "STOPPED=1"
)

del "%PID_FILE%" >nul 2>nul
if "!STOPPED!"=="1" (
  echo Service stopped.
) else (
  echo Service was not running.
)
pause
