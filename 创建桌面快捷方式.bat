@echo off
chcp 65001 >nul

:: 获取桌面路径
set DESKTOP=
for /f "usebackq tokens=*" %%i in (`powershell -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%i"

if "%DESKTOP%"=="" (
    echo 获取桌面路径失败!
    pause
    exit /b 1
)

:: 复制文件到桌面
copy "%~dp0服务管理.bat" "%DESKTOP%\" >nul 2>&1

if exist "%DESKTOP%\服务管理.bat" (
    echo.
    echo ================================================
    echo   成功! 已在桌面创建快捷方式
    echo ================================================
    echo.
    echo 文件: %DESKTOP%\服务管理.bat
    echo.
    echo 双击桌面上的"服务管理.bat"即可打开管理面板
    echo.
) else (
    echo.
    echo 创建失败，请手动复制"服务管理.bat"到桌面
    echo.
)

pause
