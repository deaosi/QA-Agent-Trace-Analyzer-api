@echo off
chcp 65001 >nul
title 店铺智能体训练工作台 - 服务管理

set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"
set "PORT=5000"

:menu
cls
echo ================================================
echo     店铺智能体训练工作台 - 服务管理
echo ================================================
echo.
echo  1. 查看服务状态
echo  2. 启动服务
echo  3. 停止服务
echo  4. 重启服务
echo  5. 打开工作台
echo  6. 打开管理员后台
echo  7. 查看服务日志
echo  0. 退出
echo.
echo ================================================

set /p choice=请选择操作 (0-7):

if "%choice%"=="1" goto status
if "%choice%"=="2" goto start
if "%choice%"=="3" goto stop
if "%choice%"=="4" goto restart
if "%choice%"=="5" goto open_workbench
if "%choice%"=="6" goto open_admin
if "%choice%"=="7" goto view_logs
if "%choice%"=="0" goto end
goto menu

:status
cls
echo ================================================
echo              查看服务状态
echo ================================================
echo.
curl -s -o nul -w "状态码: %%{http_code}\n" http://127.0.0.1:%PORT%
if %errorlevel%==0 (
    echo 服务状态: 运行中
    echo 访问地址: http://127.0.0.1:%PORT%
) else (
    echo 服务状态: 未运行
)
echo.
echo 按任意键返回菜单...
pause >nul
goto menu

:start
cls
echo ================================================
echo              启动服务
echo ================================================
echo.

:: 检查端口是否已被占用
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo 服务已在运行中!
    echo 按任意键返回菜单...
    pause >nul
    goto menu
)

echo 正在启动服务...
cd /d "%PROJECT_DIR%"
start "QA Service" cmd /c "title 店铺智能体训练工作台 && set QA_PORT=%PORT% && set QA_ADMIN_USERNAME=shuxing666 && set QA_ADMIN_PASSWORD=asdfghjkl && set QA_ACCESS_PASSWORD=asdfghjkl && set QA_SECRET_KEY=qa-workbench-secret-key-2026-06-13-random-string && set QA_DATA_DIR=%PROJECT_DIR%data && set FLASK_APP=wsgi:app && .venv\Scripts\python.exe -m flask run --host=0.0.0.0 --port=%PORT%"

timeout /t 3 /nobreak >nul
echo 服务启动中，请稍候...
timeout /t 2 /nobreak >nul

:: 检查服务是否成功启动
curl -s -o nul -w "" http://127.0.0.1:%PORT%
if %errorlevel%==0 (
    echo.
    echo [OK] 服务启动成功!
    echo 访问地址: http://127.0.0.1:%PORT%
) else (
    echo.
    echo [!] 服务启动中，请在浏览器中访问测试
)

echo.
echo 按任意键返回菜单...
pause >nul
goto menu

:stop
cls
echo ================================================
echo              停止服务
echo ================================================
echo.

:: 查找并关闭占用端口的进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo 正在停止进程 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo [OK] 服务已停止
echo.
echo 按任意键返回菜单...
pause >nul
goto menu

:restart
cls
echo ================================================
echo              重启服务
echo ================================================
echo.
echo 第一步：停止服务...
goto stop

:restart_start
echo.
echo 第二步：启动服务...
goto start

:open_workbench
cls
echo 正在打开工作台...
start http://127.0.0.1:%PORT%
goto menu

:open_admin
cls
echo 正在打开管理员后台...
start http://127.0.0.1:%PORT%/admin/users
goto menu

:view_logs
cls
echo ================================================
echo              查看服务日志
echo ================================================
echo.
echo [提示] 日志窗口已打开
echo.
echo 按 Q 键退出日志查看
echo.
start cmd /c "title 服务日志 - 按 Q 退出 && powershell Get-Content -Wait server.log"
goto menu

:end
cls
echo.
echo 感谢使用！
echo.
timeout /t 2 /nobreak >nul
exit
