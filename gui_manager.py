# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import threading
import time
import webbrowser

# 使用系统Python
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk

# 配置
SERVICE_PORT = "5000"

ENV_VARS = {
    "QA_PORT": SERVICE_PORT,
    "QA_ADMIN_USERNAME": "shuxing666",
    "QA_ADMIN_PASSWORD": "asdfghjkl",
    "QA_ACCESS_PASSWORD": "asdfghjkl",
    "QA_SECRET_KEY": "qa-workbench-secret-key-2026-06-13-random-string",
    "QA_DATA_DIR": os.path.join(PROJECT_DIR, "data"),
    "FLASK_APP": "wsgi:app",
}


class ServiceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("服务管理面板")
        self.root.geometry("500x400")

        main_frame = ttk.Frame(root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        ttk.Label(main_frame, text="店铺智能体训练工作台", font=("微软雅黑", 16, "bold")).pack(pady=(0, 10))

        # 状态显示
        self.status_label = ttk.Label(main_frame, text="检查中...", font=("微软雅黑", 14))
        self.status_label.pack(pady=10)

        # 按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=15)

        ttk.Button(btn_frame, text="启动服务", command=self.start_service, width=12).grid(row=0, column=0, padx=8, pady=8)
        ttk.Button(btn_frame, text="停止服务", command=self.stop_service, width=12).grid(row=0, column=1, padx=8, pady=8)
        ttk.Button(btn_frame, text="重启服务", command=self.restart_service, width=12).grid(row=0, column=2, padx=8, pady=8)
        ttk.Button(btn_frame, text="刷新状态", command=self.check_status, width=12).grid(row=1, column=0, padx=8, pady=8)
        ttk.Button(btn_frame, text="打开工作台", command=self.open_workbench, width=12).grid(row=1, column=1, padx=8, pady=8)
        ttk.Button(btn_frame, text="管理后台", command=self.open_admin, width=12).grid(row=1, column=2, padx=8, pady=8)

        # 日志
        ttk.Label(main_frame, text="操作日志:").pack(anchor=tk.W, pady=(10, 5))
        self.log = scrolledtext.ScrolledText(main_frame, height=10, font=("微软雅黑", 9))
        self.log.pack(fill=tk.BOTH, expand=True)

        # 启动时检查状态
        self.check_status()

    def log_msg(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log.see(tk.END)

    def check_status(self):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{SERVICE_PORT}", timeout=2)
            self.status_label.config(text="● 服务运行中", foreground="#008000", font=("微软雅黑", 14, "bold"))
            self.log_msg("状态检查: 服务运行中")
        except:
            self.status_label.config(text="● 服务已停止", foreground="#FF0000", font=("微软雅黑", 14, "bold"))
            self.log_msg("状态检查: 服务已停止")

    def start_service(self):
        self.log_msg("正在启动服务...")

        def do_start():
            try:
                # 先检查是否已在运行
                try:
                    import urllib.request
                    urllib.request.urlopen(f"http://127.0.0.1:{SERVICE_PORT}", timeout=2)
                    self.root.after(0, lambda: self.log_msg("服务已在运行中!"))
                    return
                except:
                    pass

                env = os.environ.copy()
                env.update(ENV_VARS)

                cmd = [VENV_PYTHON, "-m", "flask", "run", "--host=0.0.0.0", "--port=" + SERVICE_PORT]
                subprocess.Popen(cmd, cwd=PROJECT_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # 等待服务启动
                for i in range(15):
                    time.sleep(1)
                    try:
                        import urllib.request
                        urllib.request.urlopen(f"http://127.0.0.1:{SERVICE_PORT}", timeout=2)
                        self.root.after(0, lambda: self.log_msg("服务启动成功!"))
                        self.root.after(0, self.check_status)
                        return
                    except:
                        pass

                self.root.after(0, lambda: self.log_msg("服务启动中，请稍候..."))

            except Exception as e:
                self.root.after(0, lambda: self.log_msg(f"启动失败: {e}"))

        threading.Thread(target=do_start, daemon=True).start()

    def stop_service(self):
        self.log_msg("正在停止服务...")

        def do_stop():
            try:
                # 方法1: 通过netstat找到PID并终止
                result = subprocess.run(
                    f'netstat -ano | findstr ":{SERVICE_PORT}" | findstr "LISTENING"',
                    shell=True, capture_output=True, text=True
                )

                stopped = False
                for line in result.stdout.split('\n'):
                    if 'LISTENING' in line:
                        parts = line.split()
                        if parts:
                            pid = parts[-1].strip()
                            if pid.isdigit():
                                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                                self.root.after(0, lambda: self.log_msg(f"已停止进程 PID: {pid}"))
                                stopped = True

                if not stopped:
                    self.root.after(0, lambda: self.log_msg("未找到运行中的服务"))

                time.sleep(1)
                self.root.after(0, self.check_status)

            except Exception as e:
                self.root.after(0, lambda: self.log_msg(f"停止失败: {e}"))

        threading.Thread(target=do_stop, daemon=True).start()

    def restart_service(self):
        self.log_msg("正在重启服务...")
        self.stop_service()
        self.root.after(2500, self.start_service)

    def open_workbench(self):
        self.log_msg("正在打开工作台...")
        webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}")

    def open_admin(self):
        self.log_msg("正在打开管理后台...")
        webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}/admin/users")


if __name__ == "__main__":
    root = tk.Tk()
    app = ServiceApp(root)
    root.mainloop()
