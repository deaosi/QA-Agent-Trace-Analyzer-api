# -*- coding: utf-8 -*-
"""创建桌面快捷方式"""

import os
import shutil
from pathlib import Path

def main():
    # 获取桌面路径
    desktop = Path(os.path.expanduser("~/Desktop"))

    # 获取脚本所在目录
    script_dir = Path(__file__).parent.absolute()

    # 源文件
    bat_file = script_dir / "服务管理.bat"
    target_file = desktop / "服务管理.bat"

    # 复制文件
    try:
        shutil.copy2(bat_file, target_file)
        print("\n" + "="*50)
        print("  成功! 已在桌面创建快捷方式")
        print("="*50)
        print(f"\n文件: {target_file}")
        print("\n双击桌面上的'服务管理.bat'即可打开管理面板!")
        print("\n功能包括:")
        print("  1. 查看服务状态")
        print("  2. 启动服务")
        print("  3. 停止服务")
        print("  4. 重启服务")
        print("  5. 打开工作台")
        print("  6. 打开管理员后台")
        print("  7. 查看服务日志")
        input("\n按回车键退出...")
    except Exception as e:
        print(f"\n创建失败: {e}")
        print(f"\n请手动复制以下文件到桌面:")
        print(f"  {bat_file}")
        input("\n按回车键退出...")

if __name__ == "__main__":
    main()
