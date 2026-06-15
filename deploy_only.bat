@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

echo ==================================================
echo   QA Agent Workbench - Deploy Only
echo ==================================================
echo.

if not exist ".env" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$secret=[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'); (Get-Content '.env.example') -replace 'change-this-to-a-long-random-string', $secret | Set-Content -Encoding UTF8 '.env'"
  echo Created .env from .env.example with a random QA_SECRET_KEY
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if not "%%A"=="" set "%%A=%%B"
)

if "%QA_PORT%"=="" set "QA_PORT=5000"
if "%QA_ADMIN_USERNAME%"=="" set "QA_ADMIN_USERNAME=shuxing666"
if "%QA_ADMIN_PASSWORD%"=="" set "QA_ADMIN_PASSWORD=asdfghjkl"
if "%QA_ACCESS_PASSWORD%"=="" set "QA_ACCESS_PASSWORD=%QA_ADMIN_PASSWORD%"
if "%QA_SECRET_KEY%"=="" set "QA_SECRET_KEY=qa-workbench-default-secret-change-me"
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
echo Next step: run start_server.bat
pause
