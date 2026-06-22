@echo off
title QA Agent Trace Analyzer
cd /d "%~dp0"
chcp 65001 >nul
echo ==================================================
echo   QA Agent Trace Analyzer
echo   http://127.0.0.1:5000
echo ==================================================
if exist "%~dp0..\.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0..\.env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)
if "%QA_DATA_DIR%"=="" set "QA_DATA_DIR=%~dp0..\data"
if not exist "%QA_DATA_DIR%" mkdir "%QA_DATA_DIR%"
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
if exist "%~dp0..\.pydeps" set "PYTHONPATH=%~dp0..\.pydeps;%PYTHONPATH%"
"%PYTHON_CMD%" -c "import flask, requests" >nul 2>nul
if errorlevel 1 (
  echo Missing dependencies. Installing into project .pydeps...
  "%PYTHON_CMD%" -m pip install --target "%~dp0..\.pydeps" -r requirements.txt
  set "PYTHONPATH=%~dp0..\.pydeps;%PYTHONPATH%"
)
"%PYTHON_CMD%" app.py
pause
