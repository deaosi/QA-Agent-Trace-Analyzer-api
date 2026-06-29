"""Desktop control panel for the QA Agent Trace Analyzer."""

import os
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
WSGI_PY = os.path.join(PROJECT_DIR, "wsgi.py")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
PID_FILE = os.path.join(PROJECT_DIR, "server.pid")
LOG_FILE = os.path.join(PROJECT_DIR, "server.log")
ERR_LOG_FILE = os.path.join(PROJECT_DIR, "server.log.err")
DEFAULT_DATA_DIR = os.path.join(PROJECT_DIR, "data")
DEPLOY_SCRIPT = os.path.join(PROJECT_DIR, "deploy_only.bat")


def load_env_file(path=ENV_FILE):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def build_runtime_config(env_values=None):
    env_values = dict(env_values or {})
    return {
        "QA_PORT": env_values.get("QA_PORT") or "5000",
        "QA_HOST": env_values.get("QA_HOST") or "0.0.0.0",
        "QA_ADMIN_USERNAME": env_values.get("QA_ADMIN_USERNAME") or "admin",
        "QA_ADMIN_PASSWORD": env_values.get("QA_ADMIN_PASSWORD") or "",
        "QA_ACCESS_PASSWORD": env_values.get("QA_ACCESS_PASSWORD") or env_values.get("QA_ADMIN_PASSWORD") or "",
        "QA_SECRET_KEY": env_values.get("QA_SECRET_KEY") or "",
        "QA_DATA_DIR": env_values.get("QA_DATA_DIR") or DEFAULT_DATA_DIR,
    }


def venv_python():
    python_exe = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")
    return python_exe if os.path.exists(python_exe) else sys.executable


def build_waitress_command(port):
    return [
        venv_python(),
        "-m",
        "waitress",
        f"--listen=0.0.0.0:{port}",
        "wsgi:app",
    ]


def is_process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def read_pid():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


class ServicePanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.log_thread = None
        self.env_values = {}
        self.config = build_runtime_config({})
        self.running = False
        self.title("QA Agent Trace Analyzer - 总控面板")
        self.geometry("760x560")
        self.minsize(720, 520)
        self.configure(bg="#f4f6f8")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.build_ui()
        self.refresh_config()
        self.check_status()

    @property
    def local_url(self):
        return f"http://127.0.0.1:{self.config['QA_PORT']}"

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, padding=(18, 16, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="QA Agent Trace Analyzer 总控面板", font=("Microsoft YaHei", 16, "bold")).grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value="http://127.0.0.1:5000")
        ttk.Label(header, textvariable=self.url_var, foreground="#475467").grid(row=1, column=0, sticky="w", pady=(4, 0))

        status = ttk.LabelFrame(self, text="服务状态", padding=12)
        status.grid(row=1, column=0, sticky="ew", padx=18, pady=8)
        status.columnconfigure(1, weight=1)
        self.status_dot = ttk.Label(status, text="●", foreground="#d92d20", font=("Arial", 14))
        self.status_dot.grid(row=0, column=0, padx=(0, 8))
        self.status_var = tk.StringVar(value="未运行")
        ttk.Label(status, textvariable=self.status_var, font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=1, sticky="w")
        self.config_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.config_var, foreground="#667085").grid(row=1, column=1, sticky="w", pady=(4, 0))

        actions = ttk.Frame(self, padding=(18, 4, 18, 4))
        actions.grid(row=2, column=0, sticky="ew")
        for index in range(6):
            actions.columnconfigure(index, weight=1)

        self.start_btn = ttk.Button(actions, text="启动服务", command=self.start_service)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.stop_btn = ttk.Button(actions, text="停止服务", command=self.stop_service, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.restart_btn = ttk.Button(actions, text="重启服务", command=self.restart_service, state="disabled")
        self.restart_btn.grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="部署环境", command=self.run_deploy).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="刷新状态", command=self.refresh_all).grid(row=0, column=4, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="打开日志", command=self.open_log_file).grid(row=0, column=5, sticky="ew", padx=3, pady=3)

        ttk.Button(actions, text="工作台", command=self.open_workbench).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="管理员后台", command=self.open_admin).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="AI 分析", command=self.open_ai).grid(row=1, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="数据目录", command=self.open_data_dir).grid(row=1, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="编辑 .env", command=self.open_env_file).grid(row=1, column=4, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="项目目录", command=lambda: self.open_path(PROJECT_DIR)).grid(row=1, column=5, sticky="ew", padx=3, pady=3)

        log_frame = ttk.LabelFrame(self, text="运行日志", padding=8)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=18, pady=(8, 14))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=18, bg="#101828", fg="#e4e7ec", insertbackground="#e4e7ec", font=("Consolas", 9), wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def refresh_all(self):
        self.refresh_config()
        self.check_status()
        self.load_log_tail()

    def refresh_config(self):
        self.env_values = load_env_file()
        self.config = build_runtime_config(self.env_values)
        self.url_var.set(self.local_url)
        self.config_var.set(f"端口: {self.config['QA_PORT']}    数据目录: {self.config['QA_DATA_DIR']}")

    def append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def load_log_tail(self):
        if not os.path.exists(LOG_FILE):
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-120:]
        except OSError as exc:
            self.append_log(f"[WARN] 无法读取日志: {exc}")
            return
        self.clear_log()
        for line in lines:
            self.append_log(line.rstrip())

    def update_status(self, running, message=None):
        self.running = running
        self.status_dot.configure(foreground="#12b76a" if running else "#d92d20")
        self.status_var.set(message or ("运行中" if running else "未运行"))
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.restart_btn.configure(state="normal" if running else "disabled")

    def validate_ready_to_start(self):
        missing = []
        if not os.path.exists(ENV_FILE):
            missing.append(".env")
        if not os.path.exists(WSGI_PY):
            missing.append("wsgi.py")
        if not self.config.get("QA_SECRET_KEY"):
            missing.append("QA_SECRET_KEY")
        if not self.config.get("QA_ADMIN_PASSWORD"):
            missing.append("QA_ADMIN_PASSWORD")
        if missing:
            messagebox.showwarning("无法启动", "缺少配置: " + ", ".join(missing) + "\n请先点击『部署环境』或编辑 .env。")
            return False
        return True

    def start_service(self):
        self.refresh_config()
        if self.running:
            messagebox.showinfo("提示", "服务已经在运行。")
            return
        if not self.validate_ready_to_start():
            return
        os.makedirs(self.config["QA_DATA_DIR"], exist_ok=True)
        env = os.environ.copy()
        env.update(self.config)
        command = build_waitress_command(self.config["QA_PORT"])
        try:
            stdout = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            stderr = open(ERR_LOG_FILE, "a", encoding="utf-8", errors="replace")
            stdout.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting service: {' '.join(command)}\n")
            stdout.flush()
            self.proc = subprocess.Popen(command, cwd=PROJECT_DIR, env=env, stdout=stdout, stderr=stderr, text=True)
            with open(PID_FILE, "w", encoding="utf-8") as handle:
                handle.write(str(self.proc.pid))
            self.update_status(True, f"运行中 (PID: {self.proc.pid})")
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 服务已启动: {self.local_url}")
            self.log_thread = threading.Thread(target=self.watch_process, daemon=True)
            self.log_thread.start()
        except Exception as exc:
            self.update_status(False, "启动失败")
            self.append_log(f"[ERROR] 启动失败: {exc}")
            messagebox.showerror("启动失败", str(exc))

    def stop_service(self):
        pid = read_pid()
        if not pid:
            self.update_status(False, "未运行")
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            else:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 已请求停止服务 PID: {pid}")
        except Exception as exc:
            self.append_log(f"[WARN] 停止服务失败: {exc}")
        remove_pid_file()
        self.proc = None
        self.update_status(False, "未运行")

    def restart_service(self):
        self.stop_service()
        self.after(700, self.start_service)

    def run_deploy(self):
        if not os.path.exists(DEPLOY_SCRIPT):
            messagebox.showerror("找不到脚本", DEPLOY_SCRIPT)
            return
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 正在打开部署脚本...")
        subprocess.Popen([DEPLOY_SCRIPT], cwd=PROJECT_DIR, shell=True)

    def watch_process(self):
        if not self.proc:
            return
        self.proc.wait()
        self.after(0, self.check_status)

    def check_status(self):
        pid = read_pid()
        if pid and is_process_running(pid):
            self.update_status(True, f"运行中 (PID: {pid})")
        else:
            if pid:
                remove_pid_file()
            self.update_status(False, "未运行")

    def open_workbench(self):
        webbrowser.open(self.local_url)

    def open_admin(self):
        webbrowser.open(self.local_url + "/admin/users")

    def open_ai(self):
        webbrowser.open(self.local_url + "/?tab=ai")

    def open_env_file(self):
        if not os.path.exists(ENV_FILE):
            messagebox.showwarning("找不到 .env", "请先点击『部署环境』生成 .env。")
            return
        self.open_path(ENV_FILE)

    def open_log_file(self):
        if not os.path.exists(LOG_FILE):
            self.append_log("日志文件还不存在。")
            return
        self.open_path(LOG_FILE)

    def open_data_dir(self):
        os.makedirs(self.config["QA_DATA_DIR"], exist_ok=True)
        self.open_path(self.config["QA_DATA_DIR"])

    def open_path(self, path):
        try:
            os.startfile(path)
        except AttributeError:
            subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))


if __name__ == "__main__":
    app = ServicePanel()
    app.mainloop()
