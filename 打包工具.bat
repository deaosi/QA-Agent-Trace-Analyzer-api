@echo off
chcp 65001 >nul
title 打包服务管理面板

echo 正在安装 PyInstaller...
.venv\Scripts\pip.exe install pyinstaller

echo.
echo 正在打包，请稍候...
echo.

.venv\Scripts\pyinstaller --onefile --windowed --name "服务管理面板" --distpath . gui_manager.py

echo.
echo 打包完成！
echo.
echo EXE 文件位于: %~dp0服务管理面板.exe
echo.
pause
