@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

echo ==================================================
echo   QA Agent Workbench - Deploy Only
echo ==================================================
echo.

if not exist ".env" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$secret=[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'); $pwd=[guid]::NewGuid().ToString('N').Substring(0,16); (Get-Content '.env.example') -replace 'change-this-to-a-long-random-string', $secret -replace 'change-this-admin-password', $pwd | Set-Content -Encoding UTF8 '.env'"
  echo Created .env from .env.example with random QA_SECRET_KEY and QA_ADMIN_PASSWORD
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if not "%%A"=="" set "%%A=%%B"
)

if "%QA_PORT%"=="" set "QA_PORT=5000"
if "%QA_ADMIN_USERNAME%"=="" set "QA_ADMIN_USERNAME=admin"
if "%QA_ADMIN_PASSWORD%"=="" (
  echo QA_ADMIN_PASSWORD is missing. Please edit .env first.
  pause
  exit /b 1
)
if "%QA_ACCESS_PASSWORD%"=="" set "QA_ACCESS_PASSWORD=%QA_ADMIN_PASSWORD%"
if "%QA_SECRET_KEY%"=="" (
  echo QA_SECRET_KEY is missing. Please edit .env first.
  pause
  exit /b 1
)
if "%QA_DATA_DIR%"=="" set "QA_DATA_DIR=%CD%\data"

if not exist "%QA_DATA_DIR%" mkdir "%QA_DATA_DIR%"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating Python virtual environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
) else (
  echo [1/3] Python virtual environment exists.
)

echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "111\requirements.txt"

echo [3/3] Deploy finished.
echo.
echo Initial admin username: %QA_ADMIN_USERNAME%
echo Initial admin password: %QA_ADMIN_PASSWORD%
echo.
echo Next step: run start_server.bat
pause
