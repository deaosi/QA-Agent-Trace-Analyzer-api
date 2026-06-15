@echo off
chcp 65001 >nul
title QA Trace Analyzer
cd /d "%~dp0111"
echo ==================================================
echo   QA Agent Trace Analyzer
echo ==================================================
echo.
echo Starting server...
set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py"
if not defined PYTHON_CMD where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PYTHON_CMD (
  echo Python not found. Please install Python 3 and run:
  echo   pip install -r requirements.txt
  pause
  exit /b 1
)
if exist "%~dp0.pydeps" set "PYTHONPATH=%~dp0.pydeps;%PYTHONPATH%"
"%PYTHON_CMD%" -c "import flask, requests" >nul 2>nul
if errorlevel 1 (
  echo Missing dependencies. Installing into project .pydeps...
  "%PYTHON_CMD%" -m pip install --target "%~dp0.pydeps" -r requirements.txt
  set "PYTHONPATH=%~dp0.pydeps;%PYTHONPATH%"
)
start "QA Trace Analyzer Server" /b "%PYTHON_CMD%" app.py
echo.
echo Waiting for server...
:wait
timeout /t 2 /nobreak >nul
curl -s -o nul http://127.0.0.1:5000/api/overview 2>nul
if errorlevel 1 goto wait
echo Server ready!
echo.
echo Opening Chrome...
set "APP_URL=http://127.0.0.1:5000"
set "CHROME_EXE="
where chrome.exe >nul 2>nul && set "CHROME_EXE=chrome.exe"
if defined CHROME_EXE goto open_chrome
if not defined CHROME_EXE if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if defined CHROME_EXE goto open_chrome
call set "CHROME_X86=%%ProgramFiles(x86)%%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME_X86%" set "CHROME_EXE=%CHROME_X86%"
if defined CHROME_EXE goto open_chrome
echo.
echo ==================================================
echo   未找到Chrome浏览器，请手动复制跳转
echo.
echo   %APP_URL%
echo ==================================================
goto after_chrome

:open_chrome
start "" "%CHROME_EXE%" %APP_URL%

:after_chrome
echo.
echo Press any key to stop server...
pause >nul
taskkill /f /im python.exe 2>nul
