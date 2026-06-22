"""Service Panel - Start/Stop/Restart Flask App"""
import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(BASE_DIR, "app.py")
LOG_FILE = os.path.join(BASE_DIR, "server.log")
PORT = 5000
HOST = "127.0.0.1"
PID_FILE = os.path.join(BASE_DIR, ".server.pid")


class ServicePanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.log_thread = None
        self.running = False
        self.title("服务控制面板 - QA Agent Trace Analyzer")
        self.geometry("520x480")
        self.resizable(False, False)
        self.configure(bg="#f0f2f5")
        self.build_ui()
        self.check_status()

    def build_ui(self):
        # Title
        title_frame = ttk.Frame(self, padding=16)
        title_frame.pack(fill="x")
        ttk.Label(title_frame, text="店铺智能体训练工作台", font=("Microsoft YaHei", 14, "bold"), foreground="#101828").pack()
        ttk.Label(title_frame, text=f"http://{HOST}:{PORT}", font=("Microsoft YaHei", 9), foreground="#64748b").pack()

        # Status
        status_frame = ttk.LabelFrame(self, text="服务状态", padding=12)
        status_frame.pack(fill="x", padx=16, pady=8)
        self.status_var = tk.StringVar(value="未运行")
        self.status_dot_label = ttk.Label(status_frame, text="●", foreground="#ef4444")
        self.status_dot_label.pack(side="left")
        ttk.Label(status_frame, textvariable=self.status_var, font=("Microsoft YaHei", 10)).pack(side="left", padx=6)

        # Buttons
        btn_frame = ttk.Frame(self, padding=8)
        btn_frame.pack(fill="x", padx=16)

        self.start_btn = ttk.Button(btn_frame, text="▶ 启动服务", command=self.start_service)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=2)

        self.stop_btn = ttk.Button(btn_frame, text="⏹ 停止服务", command=self.stop_service, state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=2)

        self.restart_btn = ttk.Button(btn_frame, text="🔄 重启服务", command=self.restart_service, state="disabled")
        self.restart_btn.pack(side="left", expand=True, fill="x", padx=2)

        # Open URL
        url_btn = ttk.Button(btn_frame, text="🌐 打开网页", command=self.open_url)
        url_btn.pack(side="left", expand=True, fill="x", padx=2)

        # Log area
        log_frame = ttk.LabelFrame(self, text="日志输出", padding=8)
        log_frame.pack(fill="both", expand=True, padx=16, pady=8)

        self.log_text = tk.Text(log_frame, height=16, font=("Consolas", 9), bg="#0f172a", fg="#d1d5db",
                                wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        # Footer
        footer = ttk.Frame(self, padding=4)
        footer.pack(fill="x")
        ttk.Label(footer, text="数据目录: " + BASE_DIR, font=("Microsoft YaHei", 8), foreground="#94a3b8").pack()

    def update_status(self, running, msg=None):
        self.running = running
        if running:
            self.status_var.set(msg or "运行中")
            self.status_dot_label.configure(foreground="#22c55e")
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.restart_btn.configure(state="normal")
        else:
            self.status_var.set(msg or "未运行")
            self.status_dot_label.configure(foreground="#ef4444")
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.restart_btn.configure(state="disabled")

    def append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def start_service(self):
        if self.running:
            messagebox.showwarning("提示", "服务已在运行中")
            return
        self.clear_log()
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 正在启动服务...")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, APP_PY],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            # Save PID
            with open(PID_FILE, "w") as f:
                f.write(str(self.proc.pid))
            # Start log reader thread
            self.log_thread = threading.Thread(target=self.read_stdout, daemon=True)
            self.log_thread.start()
            self.update_status(True, f"运行中 (PID: {self.proc.pid})")
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 服务已启动，PID: {self.proc.pid}")
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 访问 http://{HOST}:{PORT}")
        except Exception as e:
            self.update_status(False, f"启动失败: {e}")
            self.append_log(f"[ERROR] {e}")

    def stop_service(self):
        if not self.running or not self.proc:
            # Try to kill by PID file
            self.kill_by_pid_file()
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.update_status(False, "已停止")
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 服务已停止")
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.update_status(False, "已强制停止")
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 服务已强制停止")

    def restart_service(self):
        self.stop_service()
        self.after(500, self.start_service)

    def open_url(self):
        import webbrowser
        webbrowser.open(f"http://{HOST}:{PORT}")

    def read_stdout(self):
        if not self.proc:
            return
        try:
            for line in iter(self.proc.stdout.readline, ""):
                if line:
                    self.after(0, lambda l=line: self.append_log(l.rstrip()))
        except Exception:
            pass
        finally:
            self.proc.stdout.close()
            self.update_status(False, "已停止")
            self.proc = None

    def kill_by_pid_file(self):
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE, "r") as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 9)
                    self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 已强制终止进程 {pid}")
                except ProcessLookupError:
                    self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 进程 {pid} 不存在")
                except Exception as e:
                    self.append_log(f"[ERROR] 终止失败: {e}")
                finally:
                    os.remove(PID_FILE)
        except Exception as e:
            self.append_log(f"[ERROR] {e}")
        self.update_status(False, "已停止")

    def check_status(self):
        """Check if service is already running"""
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE, "r") as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)  # Check if process exists
                    self.proc = subprocess.Popen(["echo"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.proc.pid = pid
                    self.update_status(True, f"运行中 (PID: {pid})")
                    self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] 检测到已有服务运行 (PID: {pid})")
                    return
                except ProcessLookupError:
                    os.remove(PID_FILE)
        except Exception:
            pass
        self.update_status(False, "未运行")


if __name__ == "__main__":
    app = ServicePanel()
    app.mainloop()
