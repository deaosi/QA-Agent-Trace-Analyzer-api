# -*- coding: utf-8 -*-
"""创建桌面快捷方式"""

import os
import sys
from pathlib import Path
import win32com.client

def create_shortcut():
    # 获取桌面路径
    desktop = Path(os.path.expanduser("~/Desktop"))

    # 获取脚本所在目录
    script_dir = Path(__file__).parent.absolute()

    # 快捷方式配置
    shortcut_name = "店铺智能体训练工作台.lnk"
    bat_file = script_dir / "服务管理面板.bat"
    description = "店铺智能体训练工作台服务管理面板"

    # 创建快捷方式
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(desktop / shortcut_name))
        shortcut.TargetPath = str(bat_file)
        shortcut.WorkingDirectory = str(script_dir)
        shortcut.Description = description
        shortcut.Save()

        print(f"✓ 成功在桌面创建快捷方式: {shortcut_name}")
        print(f"  位置: {desktop / shortcut_name}")
        print(f"\n双击快捷方式即可打开服务管理面板!")
        input("\n按回车键退出...")
    except Exception as e:
        print(f"✗ 创建快捷方式失败: {e}")
        print(f"\n请手动双击运行: {bat_file}")
        input("\n按回车键退出...")

if __name__ == "__main__":
    try:
        import win32com.client
        create_shortcut()
    except ImportError:
        print("需要安装 pywin32 库")
        print("请运行: .venv\\Scripts\\pip.exe install pywin32")
        input("\n按回车键退出...")
