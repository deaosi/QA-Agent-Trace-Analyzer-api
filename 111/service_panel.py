"""Desktop control panel for the QA Agent Trace Analyzer."""

import os
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
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
TUNNEL_PID_FILE = os.path.join(PROJECT_DIR, "cloudflared.pid")
TUNNEL_LOG_FILE = os.path.join(PROJECT_DIR, "cloudflared.log")
TUNNEL_ERR_LOG_FILE = os.path.join(PROJECT_DIR, "cloudflared.log.err")
CLOUDFLARED_EXE = os.path.join(PROJECT_DIR, "cloudflared.exe")
TUNNEL_METRICS_PORT = "9090"
TUNNEL_READY_URL = f"http://127.0.0.1:{TUNNEL_METRICS_PORT}/ready"
DEFAULT_DATA_DIR = os.path.join(PROJECT_DIR, "data")
DEPLOY_SCRIPT = os.path.join(PROJECT_DIR, "deploy_only.bat")
WINDOWS_BACKGROUND_FLAGS = (
    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    if os.name == "nt" else 0
)


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
        "QA_PUBLIC_URL": env_values.get("QA_PUBLIC_URL") or "https://diamondruby.xyz",
        "QA_TUNNEL_ENABLED": env_values.get("QA_TUNNEL_ENABLED") or "0",
        "QA_TUNNEL_TOKEN": env_values.get("QA_TUNNEL_TOKEN") or "",
        "QA_TUNNEL_NAME": env_values.get("QA_TUNNEL_NAME") or "",
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


def tunnel_enabled(config):
    value = str(config.get("QA_TUNNEL_ENABLED", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def build_cloudflared_command(config):
    token = str(config.get("QA_TUNNEL_TOKEN", "")).strip()
    tunnel_name = str(config.get("QA_TUNNEL_NAME", "")).strip()
    if not token and not tunnel_name:
        return []
    command = [
        CLOUDFLARED_EXE,
        "tunnel",
        "--no-autoupdate",
        "--edge-ip-version",
        "4",
        "--protocol",
        "http2",
        "--loglevel",
        "info",
        "--metrics",
        f"127.0.0.1:{TUNNEL_METRICS_PORT}",
        "run",
        "--dns-resolver-addrs",
        "1.1.1.1:53",
    ]
    if token:
        command.extend(["--token", token])
    else:
        command.append(tunnel_name)
    return [
        *command,
    ]


def build_child_env(config, base_env=None):
    base_env = dict(base_env or os.environ)
    if os.name != "nt":
        env = dict(base_env)
        env.update(config)
        return env

    env = {}
    seen = {}
    for key, value in base_env.items():
        folded = key.upper()
        if folded in seen:
            existing_key = seen[folded]
            if key == "Path" and existing_key != "Path":
                env.pop(existing_key, None)
                env[key] = value
                seen[folded] = key
            continue
        env[key] = value
        seen[folded] = key
    env.update(config)
    return env


def is_process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def parse_netstat_listening_pids(output, port):
    pids = set()
    port = str(port)
    for raw_line in str(output or "").splitlines():
        parts = raw_line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid = parts[-1]
        if state == "LISTENING" and local_address.endswith(f":{port}") and pid.isdigit():
            pids.add(int(pid))
    return sorted(pids)


def listening_pids_on_port(port):
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_netstat_listening_pids(result.stdout, port)


def tunnel_ready(timeout=2):
    try:
        with urllib.request.urlopen(TUNNEL_READY_URL, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def stop_process_tree(pid):
    if not pid:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        return True
    except Exception:
        return False


def read_pid(path=PID_FILE):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def remove_pid_file(path=PID_FILE):
    try:
        os.remove(path)
    except OSError:
        pass


class ServicePanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.tunnel_proc = None
        self.log_thread = None
        self.env_values = {}
        self.config = build_runtime_config({})
        self.running = False
        self.title("QA Agent Trace Analyzer - 总控面板")
        self.geometry("760x560")
        self.minsize(720, 520)
        self.configure(bg="#f4f6f8")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
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
        tunnel_text = "已启用" if tunnel_enabled(self.config) else "未启用"
        self.config_var.set(
            f"端口: {self.config['QA_PORT']}    数据目录: {self.config['QA_DATA_DIR']}    内网穿透: {tunnel_text}"
        )

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
        running_pids = listening_pids_on_port(self.config["QA_PORT"])
        if self.running or running_pids:
            if running_pids:
                self.update_status(True, f"运行中 (PID: {running_pids[0]})")
                self.start_tunnel()
            messagebox.showinfo("提示", "服务已经在运行。")
            return
        if not self.validate_ready_to_start():
            return
        os.makedirs(self.config["QA_DATA_DIR"], exist_ok=True)
        env = build_child_env(self.config)
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
            self.start_tunnel()
            self.log_thread = threading.Thread(target=self.watch_process, daemon=True)
            self.log_thread.start()
        except Exception as exc:
            self.update_status(False, "启动失败")
            self.append_log(f"[ERROR] 启动失败: {exc}")
            messagebox.showerror("启动失败", str(exc))

    def start_tunnel(self):
        if not tunnel_enabled(self.config):
            return
        if not os.path.exists(CLOUDFLARED_EXE):
            self.append_log("[WARN] 已启用内网穿透，但找不到 cloudflared.exe")
            return
        command = build_cloudflared_command(self.config)
        if not command:
            self.append_log("[WARN] 已启用内网穿透，但 .env 缺少 QA_TUNNEL_TOKEN 或 QA_TUNNEL_NAME")
            return

        pid = read_pid(TUNNEL_PID_FILE)
        metrics_pids = listening_pids_on_port(TUNNEL_METRICS_PORT)
        if tunnel_ready():
            running_pid = pid or (metrics_pids[0] if metrics_pids else None)
            if running_pid and (not pid or pid != running_pid):
                with open(TUNNEL_PID_FILE, "w", encoding="utf-8") as handle:
                    handle.write(str(running_pid))
            suffix = f" PID: {running_pid}" if running_pid else ""
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 内网穿透已在线{suffix}")
            return
        if pid and is_process_running(pid):
            self.append_log(f"[WARN] 内网穿透进程存在但未就绪，正在重启 PID: {pid}")
            stop_process_tree(pid)
            remove_pid_file(TUNNEL_PID_FILE)

        env = build_child_env(self.config)
        try:
            stdout = open(TUNNEL_LOG_FILE, "a", encoding="utf-8", errors="replace")
            stderr = open(TUNNEL_ERR_LOG_FILE, "a", encoding="utf-8", errors="replace")
            stdout.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting tunnel for {self.config['QA_PUBLIC_URL']}\n")
            stdout.flush()
            self.tunnel_proc = subprocess.Popen(
                command,
                cwd=PROJECT_DIR,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                creationflags=WINDOWS_BACKGROUND_FLAGS,
            )
            with open(TUNNEL_PID_FILE, "w", encoding="utf-8") as handle:
                handle.write(str(self.tunnel_proc.pid))
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 内网穿透已启动: {self.config['QA_PUBLIC_URL']} (PID: {self.tunnel_proc.pid})")
        except Exception as exc:
            self.append_log(f"[WARN] 启动内网穿透失败: {exc}")

    def stop_tunnel(self):
        pids = set()
        pid = read_pid(TUNNEL_PID_FILE)
        if pid:
            pids.add(pid)
        if self.tunnel_proc and self.tunnel_proc.pid:
            pids.add(self.tunnel_proc.pid)
        for metrics_pid in listening_pids_on_port(TUNNEL_METRICS_PORT):
            pids.add(metrics_pid)
        if not pids:
            remove_pid_file(TUNNEL_PID_FILE)
            self.tunnel_proc = None
            return

        stopped = []
        failed = []
        for target_pid in sorted(pids):
            if stop_process_tree(target_pid):
                stopped.append(target_pid)
            else:
                failed.append(target_pid)
        if stopped:
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 内网穿透已停止 PID: {', '.join(map(str, stopped))}")
        if failed:
            self.append_log(f"[WARN] 内网穿透停止失败 PID: {', '.join(map(str, failed))}")
        remove_pid_file(TUNNEL_PID_FILE)
        self.tunnel_proc = None
        if tunnel_ready():
            self.append_log("[WARN] 已请求停止，但内网穿透仍然在线，请检查是否还有其他 cloudflared 实例")

    def stop_service(self):
        self.refresh_config()
        self.stop_tunnel()
        pids = set()
        pid = read_pid()
        if pid:
            pids.add(pid)
        if self.proc and self.proc.pid:
            pids.add(self.proc.pid)
        for port_pid in listening_pids_on_port(self.config["QA_PORT"]):
            pids.add(port_pid)

        if not pids:
            remove_pid_file()
            self.proc = None
            self.update_status(False, "未运行")
            return

        stopped = []
        failed = []
        for target_pid in sorted(pids):
            if stop_process_tree(target_pid):
                stopped.append(target_pid)
            else:
                failed.append(target_pid)

        time.sleep(0.6)
        remaining = listening_pids_on_port(self.config["QA_PORT"])
        if remaining:
            self.append_log(f"[WARN] 已请求停止，但端口 {self.config['QA_PORT']} 仍被 PID 占用: {', '.join(map(str, remaining))}")
            self.update_status(True, f"运行中 (PID: {remaining[0]})")
            if failed:
                self.append_log(f"[WARN] 停止失败 PID: {', '.join(map(str, failed))}")
            return

        if stopped:
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 服务已停止 PID: {', '.join(map(str, stopped))}")
        remove_pid_file()
        self.proc = None
        self.update_status(False, "未运行")

    def on_close(self):
        self.stop_service()
        self.destroy()

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
        port_pids = listening_pids_on_port(self.config["QA_PORT"])
        if port_pids:
            self.update_status(True, f"运行中 (PID: {port_pids[0]})")
            self.start_tunnel()
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
