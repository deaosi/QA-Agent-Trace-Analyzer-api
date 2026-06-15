@echo off
chcp 65001 >nul
title 服务管理面板
python "%~dp0gui_manager.py"
pause
