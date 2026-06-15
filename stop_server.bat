@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "PID_FILE=%CD%\server.pid"

if not exist "%PID_FILE%" (
  echo server.pid not found. Service is probably not running.
  pause
  exit /b 0
)

set /p PID=<"%PID_FILE%"
if "%PID%"=="" (
  del "%PID_FILE%" >nul 2>nul
  echo Empty PID file cleaned.
  pause
  exit /b 0
)

tasklist /FI "PID eq %PID%" | findstr "%PID%" >nul 2>nul
if errorlevel 1 (
  del "%PID_FILE%" >nul 2>nul
  echo Service is not running. PID file cleaned.
  pause
  exit /b 0
)

taskkill /PID %PID% /F >nul
del "%PID_FILE%" >nul 2>nul
echo Service stopped.
pause
