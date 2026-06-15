@echo off
chcp 65001 >nul
title 店铺智能体训练工作台 - 服务管理面板
cd /d "%~dp0"
echo 正在启动服务管理面板...
.venv\Scripts\python.exe service_manager.py
pause
