# -*- coding: utf-8 -*-
"""
店铺智能体训练工作台 - 服务管理面板
"""

import os
import sys
import subprocess
import threading
import time
import requests
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox, ttk
except ImportError:
    print("需要安装 tkinter，请使用: pip install tkinter")
    sys.exit(1)


# 配置
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_BIN = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")
SERVICE_PORT = "5000"
SERVICE_PID_FILE = os.path.join(PROJECT_DIR, "server.pid")
SERVICE_LOG_FILE = os.path.join(PROJECT_DIR, "server.log")
DATA_DIR = os.path.join(PROJECT_DIR, "data")

# 环境变量
ENV_VARS = {
    "QA_PORT": SERVICE_PORT,
    "QA_ADMIN_USERNAME": "shuxing666",
    "QA_ADMIN_PASSWORD": "asdfghjkl",
    "QA_ACCESS_PASSWORD": "asdfghjkl",
    "QA_SECRET_KEY": "qa-workbench-secret-key-2026-06-13-random-string",
    "QA_DATA_DIR": DATA_DIR,
    "FLASK_APP": "wsgi:app",
}

# 全局变量
process = None
is_running = False


class ServiceManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("店铺智能体训练工作台 - 服务管理面板")
        self.root.geometry("700x500")
        self.root.resizable(True, True)

        # 设置样式
        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.create_widgets()
        self.update_status()
        self.root.after(2000, self.auto_update_status)

    def create_widgets(self):
        # 主框架
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_label = ttk.Label(main_frame, text="店铺智能体训练工作台", font=("微软雅黑", 18, "bold"))
        title_label.pack(pady=(0, 10))

        # 状态卡片
        status_frame = ttk.LabelFrame(main_frame, text="服务状态", padding="15")
        status_frame.pack(fill=tk.X, pady=(0, 15))

        self.status_label = ttk.Label(status_frame, text="检查中...", font=("微软雅黑", 14))
        self.status_label.pack()

        self.url_label = ttk.Label(status_frame, text="", font=("微软雅黑", 10))
        self.url_label.pack(pady=(5, 0))

        # 控制按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 15))

        self.start_btn = ttk.Button(btn_frame, text="启动服务", command=self.start_service, width=15)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="停止服务", command=self.stop_service, width=15)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.restart_btn = ttk.Button(btn_frame, text="重启服务", command=self.restart_service, width=15)
        self.restart_btn.pack(side=tk.LEFT, padx=5)

        self.refresh_btn = ttk.Button(btn_frame, text="刷新状态", command=self.update_status, width=15)
        self.refresh_btn.pack(side=tk.LEFT, padx=5)

        # 快捷链接
        link_frame = ttk.LabelFrame(main_frame, text="快捷访问", padding="15")
        link_frame.pack(fill=tk.X, pady=(0, 15))

        link_btn_frame = ttk.Frame(link_frame)
        link_btn_frame.pack()

        ttk.Button(link_btn_frame, text="打开工作台", command=self.open_workbench, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(link_btn_frame, text="管理员后台", command=self.open_admin, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(link_btn_frame, text="登录页面", command=self.open_login, width=15).pack(side=tk.LEFT, padx=5)

        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="服务日志 (实时)", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 底部信息
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X)

        self.info_label = ttk.Label(info_frame, text=f"项目目录: {PROJECT_DIR}", foreground="gray")
        self.info_label.pack(side=tk.LEFT)

        ttk.Label(info_frame, text=f"服务端口: {SERVICE_PORT}", foreground="gray").pack(side=tk.RIGHT)

    def auto_update_status(self):
        """自动更新状态"""
        self.update_status()
        if is_running:
            self.root.after(2000, self.auto_update_status)

    def update_status(self):
        """更新服务状态"""
        global is_running

        try:
            # 尝试访问服务
            response = requests.get(f"http://127.0.0.1:{SERVICE_PORT}", timeout=3)
            if response.status_code == 200:
                is_running = True
                self.status_label.config(text="● 服务运行中", foreground="#039855")
                self.url_label.config(text=f"访问地址: http://127.0.0.1:{SERVICE_PORT} | http://YOUR_IP:{SERVICE_PORT}")
                self.start_btn.config(state="disabled")
                self.stop_btn.config(state="normal")
                self.restart_btn.config(state="normal")
        except requests.exceptions.ConnectionError:
            is_running = False
            self.status_label.config(text="● 服务未运行", foreground="#d92d20")
            self.url_label.config(text="服务未启动")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.restart_btn.config(state="disabled")
        except Exception as e:
            is_running = False
            self.status_label.config(text=f"● 状态异常: {str(e)}", foreground="#dc6803")
            self.url_label.config(text="")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.restart_btn.config(state="disabled")

    def log_message(self, msg):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)

    def start_service(self):
        """启动服务"""
        def start():
            global process, is_running

            self.log_message("正在启动服务...")

            # 构建环境变量
            env = os.environ.copy()
            env.update(ENV_VARS)

            try:
                # 使用 waitress 作为生产服务器
                cmd = [PYTHON_BIN, "-m", "waitress", "--listen=0.0.0.0:" + SERVICE_PORT, "wsgi:app"]
                process = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_DIR,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                # 保存PID
                with open(SERVICE_PID_FILE, "w") as f:
                    f.write(str(process.pid))

                # 等待服务启动
                for i in range(10):
                    time.sleep(1)
                    try:
                        requests.get(f"http://127.0.0.1:{SERVICE_PORT}", timeout=2)
                        self.log_message("服务启动成功!")
                        self.root.after(0, self.update_status)
                        return
                    except:
                        pass

                self.log_message("服务启动中...")

                # 启动日志读取线程
                def read_logs():
                    if process and process.stdout:
                        for line in process.stdout:
                            if line.strip():
                                self.root.after(0, lambda l=line: self.log_message(l.strip()))

                log_thread = threading.Thread(target=read_logs, daemon=True)
                log_thread.start()

            except Exception as e:
                self.log_message(f"启动失败: {str(e)}")
                self.root.after(0, self.update_status)

        self.log_message("=" * 50)
        self.log_message("启动服务")
        self.start_btn.config(state="disabled")
        threading.Thread(target=start, daemon=True).start()

    def stop_service(self):
        """停止服务"""
        def stop():
            global process, is_running

            self.log_message("正在停止服务...")

            # 尝试通过PID文件停止
            try:
                if os.path.exists(SERVICE_PID_FILE):
                    with open(SERVICE_PID_FILE, "r") as f:
                        pid = f.read().strip()
                    if pid:
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                        self.log_message("通过PID停止服务")
            except:
                pass

            # 尝试直接关闭进程
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                    self.log_message("进程已终止")
                except:
                    try:
                        process.kill()
                        self.log_message("进程已强制终止")
                    except:
                        pass

            # 删除PID文件
            try:
                if os.path.exists(SERVICE_PID_FILE):
                    os.remove(SERVICE_PID_FILE)
            except:
                pass

            self.log_message("服务已停止")
            self.root.after(0, self.update_status)

        self.log_message("=" * 50)
        self.log_message("停止服务")
        self.stop_btn.config(state="disabled")
        threading.Thread(target=stop, daemon=True).start()

    def restart_service(self):
        """重启服务"""
        self.log_message("=" * 50)
        self.log_message("重启服务")
        self.stop_service()
        self.root.after(1500, self.start_service)

    def open_workbench(self):
        """打开工作台"""
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}")
        except:
            messagebox.showinfo("提示", f"请手动打开浏览器访问:\nhttp://127.0.0.1:{SERVICE_PORT}")

    def open_admin(self):
        """打开管理员后台"""
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}/admin/users")
        except:
            messagebox.showinfo("提示", f"请手动打开浏览器访问:\nhttp://127.0.0.1:{SERVICE_PORT}/admin/users")

    def open_login(self):
        """打开登录页面"""
        try:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}/login")
        except:
            messagebox.showinfo("提示", f"请手动打开浏览器访问:\nhttp://127.0.0.1:{SERVICE_PORT}/login")


def main():
    # 检查Python环境
    if not os.path.exists(PYTHON_BIN):
        print(f"错误: 虚拟环境未找到")
        print(f"请先运行 deploy_only.bat 部署环境")
        input("按回车键退出...")
        sys.exit(1)

    # 创建并运行应用
    root = tk.Tk()
    app = ServiceManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
