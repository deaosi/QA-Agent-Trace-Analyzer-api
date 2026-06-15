@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==================================================
echo   店铺智能体训练工作台 - Windows 云服务器启动
echo ==================================================

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo 已生成 .env，请先编辑 .env 设置 QA_ACCESS_PASSWORD 和 QA_SECRET_KEY。
  echo.
  echo 示例：
  echo QA_PORT=5000
  echo QA_ACCESS_PASSWORD=你的访问密码
  echo QA_SECRET_KEY=一段足够长的随机字符串
  echo.
  pause
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if not "%%A"=="" set "%%A=%%B"
)

if "%QA_PORT%"=="" set "QA_PORT=5000"
if "%QA_DATA_DIR%"=="" set "QA_DATA_DIR=%CD%\data"

if "%QA_ACCESS_PASSWORD%"=="" (
  echo 错误：请在 .env 里设置 QA_ACCESS_PASSWORD，公网部署必须设置访问密码。
  pause
  exit /b 1
)

if "%QA_ACCESS_PASSWORD%"=="change-this-password" (
  echo 错误：请把 .env 里的 QA_ACCESS_PASSWORD 改成你自己的密码。
  pause
  exit /b 1
)

if "%QA_SECRET_KEY%"=="" (
  echo 错误：请在 .env 里设置 QA_SECRET_KEY。
  pause
  exit /b 1
)

if "%QA_SECRET_KEY%"=="change-this-to-a-long-random-string" (
  echo 错误：请把 .env 里的 QA_SECRET_KEY 改成一段随机字符串。
  pause
  exit /b 1
)

if not exist "%QA_DATA_DIR%" mkdir "%QA_DATA_DIR%"

if not exist ".venv\Scripts\python.exe" (
  echo 正在创建 Python 虚拟环境...
  where py >nul 2>nul
  if %errorlevel%==0 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
)

echo 正在安装/更新依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "111\requirements.txt"

echo.
echo ==================================================
echo   服务已启动
echo   本机访问：http://127.0.0.1:%QA_PORT%
echo   外部访问：http://你的服务器公网IP:%QA_PORT%
echo   数据目录：%QA_DATA_DIR%
echo ==================================================
echo.

".venv\Scripts\python.exe" -m waitress --listen=0.0.0.0:%QA_PORT% wsgi:app

pause
