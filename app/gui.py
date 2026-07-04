import os
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from . import config, cdp, api, session, worker


def _fmt(seconds):
    if seconds is None:
        return "--"
    try:
        seconds = int(seconds)
    except Exception:
        return "--"
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_time(ts):
    if not ts:
        return "--"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class App:
    def __init__(self, root):
        self.root = root
        self.sess = None
        self.worker = None
        self.chrome_proc = None
        self._locked_port = None
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        root = self.root
        root.title(f"EasyFlow自动接单 v{config.APP_VERSION}")
        root.geometry("660x580")
        root.minsize(600, 450)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        flow = ttk.Frame(root)
        flow.pack(fill="x", padx=8, pady=(4,2))
        ttk.Label(flow,
                  text="方式一：打开浏览器 → 登录账号 → 启动（Token自动回填）    方式二：粘贴Token → 设置Token → 启动",
                  font=("Microsoft YaHei", 8), foreground="#888").pack(anchor="w")

        # ========== 区域一：Token 管理 ==========
        token_frame = ttk.LabelFrame(root, text="Token 管理")
        token_frame.pack(fill="x", padx=8, pady=4)

        tkw = token_frame
        ttk.Label(tkw, text="端口:").grid(row=0, column=0, sticky="w", padx=(6,2), pady=4)
        init_port = config.recommended_port()
        self.port_var = tk.StringVar(value=str(init_port))
        ttk.Entry(tkw, textvariable=self.port_var, width=8).grid(row=0, column=1, padx=2, pady=4)
        self.port_var.trace_add("write", self._on_port_change)

        self.url_var = tk.StringVar(value=config.DEFAULT_LOGIN_URL)
        self.bearer_var = tk.BooleanVar(value=False)
        self.tokenkey_var = tk.StringVar(value="auto")

        self.btn_open = ttk.Button(tkw, text="打开浏览器", width=10, command=self.on_open_browser)
        self.btn_open.grid(row=0, column=2, padx=3, pady=4)
        self.btn_refresh_token = ttk.Button(tkw, text="刷新Token", width=10, command=self.on_refresh_token)
        self.btn_refresh_token.grid(row=0, column=3, padx=3, pady=4)
        self.btn_online = ttk.Button(tkw, text="手动上线", width=10, command=self.on_manual_online)
        self.btn_online.grid(row=0, column=4, padx=3, pady=4)

        ttk.Label(tkw, text="Token:").grid(row=1, column=0, sticky="w", padx=(6,2), pady=4)
        self.token_var = tk.StringVar()
        ttk.Entry(tkw, textvariable=self.token_var).grid(
            row=1, column=1, columnspan=3, padx=2, pady=4, sticky="we")
        self.btn_set_token = ttk.Button(tkw, text="设置Token", width=10, command=self.on_set_token)
        self.btn_set_token.grid(row=1, column=4, padx=3, pady=4, sticky="e")

        # ========== 区域二：运行控制 ==========
        run_frame = ttk.LabelFrame(root, text="运行控制")
        run_frame.pack(fill="both", expand=True, padx=8, pady=4)

        ctl = ttk.Frame(run_frame)
        ctl.pack(fill="x", padx=6, pady=2)
        self.btn_start = ttk.Button(ctl, text="启动", width=10, command=self.on_start)
        self.btn_start.grid(row=0, column=0, padx=3, pady=2)
        self.btn_stop = ttk.Button(ctl, text="停止", width=10, command=self.on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=3, pady=2)
        ttk.Label(ctl, text="轮询(ms):").grid(row=0, column=2, padx=(10,2), pady=2)
        self.poll_var = tk.StringVar(value=str(int(config.DEFAULT_POLL_INTERVAL * 1000)))
        ttk.Entry(ctl, textvariable=self.poll_var, width=5).grid(row=0, column=3, padx=2, pady=2)
        ttk.Label(ctl, text="并发:").grid(row=0, column=4, padx=(10,2), pady=2)
        self.concurrency_var = tk.StringVar(value="10")
        ttk.Entry(ctl, textvariable=self.concurrency_var, width=5).grid(row=0, column=5, padx=2, pady=2)
        self.btn_apply = ttk.Button(ctl, text="应用", width=6, command=self.on_apply_params)
        self.btn_apply.grid(row=0, column=6, padx=5, pady=2)

        status = ttk.Frame(run_frame)
        status.pack(fill="x", padx=6, pady=2)
        self.lbl_grab = ttk.Label(status, text="抢单: 0", foreground="#d35400")
        self.lbl_grab.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.lbl_assign = ttk.Label(status, text="派单: 0", foreground="#2980b9")
        self.lbl_assign.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.lbl_total = ttk.Label(status, text="总单: --", foreground="#8e44ad")
        self.lbl_total.grid(row=0, column=2, sticky="w", padx=4, pady=2)
        self.lbl_socket = ttk.Label(status, text="WS: --", foreground="#888")
        self.lbl_socket.grid(row=0, column=3, sticky="w", padx=4, pady=2)
        self.lbl_online_remain = ttk.Label(status, text="在线剩余: --", foreground="#27ae60")
        self.lbl_online_remain.grid(row=0, column=4, sticky="w", padx=4, pady=2)
        self.lbl_token_remain = ttk.Label(status, text="Token剩余: --", foreground="#c0392b")
        self.lbl_token_remain.grid(row=0, column=5, sticky="w", padx=4, pady=2)

        # 日志区域
        pane = ttk.PanedWindow(run_frame, orient="vertical")
        pane.pack(fill="both", expand=True, padx=6, pady=2)

        log_frame = ttk.Frame(pane)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="word",
                                                  font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        pane.add(log_frame, weight=1)

        self._on_port_change()
        self._update_buttons()

    # ---------- helpers ----------
    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_port_change(self, *a):
        port = self._port_value()
        if port:
            # 释放旧端口锁
            if self._locked_port and self._locked_port != port:
                config.unlock_port(self._locked_port)
                try:
                    os.remove(config.session_file(self._locked_port))
                except Exception:
                    pass
            config.lock_port(port)
            self._locked_port = port
            self.sess = session.SessionData.load(port)
            self.sess.login_url = config.DEFAULT_LOGIN_URL
            self.poll_var.set(str(int((self.sess.poll_interval or config.DEFAULT_POLL_INTERVAL) * 1000)))
            self.concurrency_var.set(str(int(self.sess.concurrency or 3)))
            self.tokenkey_var.set(self.sess.token_key or "auto")
            self.bearer_var.set(self.sess.add_bearer)
            self.token_var.set(self.sess.token or "")
            self.lbl_token_remain.config(text=f"Token剩余: {_fmt(self.sess.token_remaining)}")
            self.sess.save()
        else:
            self.sess = None

    def _port_value(self):
        v = (self.port_var.get() or "").strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _sync_sess_from_ui(self):
        if not self.sess:
            return False
        self.sess.login_url = config.DEFAULT_LOGIN_URL
        try:
            self.sess.poll_interval = max(0.5, float(self.poll_var.get()) / 1000)  # ms→秒
        except Exception:
            self.sess.poll_interval = config.DEFAULT_POLL_INTERVAL
        try:
            self.sess.concurrency = max(1, int(self.concurrency_var.get()))
        except Exception:
            self.sess.concurrency = 10
        self.sess.token_key = self.tokenkey_var.get().strip() or "auto"
        self.sess.add_bearer = bool(self.bearer_var.get())
        manual = self.token_var.get().strip()
        if manual and manual != self.sess.token:
            self.sess.token = manual
            if not self.sess.login_time:
                self.sess.login_time = time.time()
            self.sess.save()
        elif not manual and not self.sess.token:
            self.sess.token = ""
        return True

    def _update_buttons(self):
        running = self.worker is not None and self.worker.is_alive()
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")
        st = "disabled" if running else "normal"
        for b in (self.btn_open, self.btn_refresh_token, self.btn_online, self.btn_set_token):
            if b is not None:
                b.config(state=st)

    # ---------- actions ----------
    def on_open_browser(self):
        port = self._port_value()
        if not port:
            messagebox.showwarning("提示", "请输入有效的端口号")
            return
        if not self.sess:
            self.sess = session.SessionData.load(port)
        self._sync_sess_from_ui()
        url = config.DEFAULT_LOGIN_URL
        # 已运行则新建标签页；未运行则启动 Chrome
        if cdp.wait_for_devtools(port, timeout=1):
            try:
                self._log(f"浏览器已运行（端口 {port}），打开新标签页...")
                cdp.open_url_in_existing(port, url)
                self._log("已打开登录页面")
            except Exception as e:
                self._log(f"打开标签失败: {e}，尝试重启浏览器...")
                self.chrome_proc = cdp.launch_chrome(port, url)
        else:
            try:
                self._log(f"正在启动浏览器 | 端口 {port}")
                self.chrome_proc = cdp.launch_chrome(port, url)
                if not cdp.wait_for_devtools(port, 20):
                    self._log("警告: 调试端口未就绪，请确认 Chrome 已启动")
                else:
                    self._log("浏览器已启动，请在浏览器中完成登录")
            except Exception as e:
                messagebox.showerror("错误", f"打开浏览器失败:\n{e}")
                self._log(f"打开浏览器失败: {e}")

    def on_start(self):
        port = self._port_value()
        if not port:
            messagebox.showwarning("提示", "请输入有效的端口号")
            return
        if not self.sess:
            self.sess = session.SessionData.load(port)
        manual = self.token_var.get().strip()
        if not manual and not self.sess.token:
            messagebox.showwarning("提示", "尚无 Token，请先：\n  打开浏览器登录（自动回填Token），或  粘贴Token → 设置Token")
            return
        self._sync_sess_from_ui()
        if manual:
            self.sess.token = manual
            if not self.sess.login_time:
                self.sess.login_time = time.time()
        self.sess.save()
        self.worker = worker.Worker(self.sess)
        self.worker.start()
        self._update_buttons()
        self._drain_queue()

    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self._log("正在停止任务...")
            # 后台等待真正退出，再清状态
            def _wait():
                if self.worker:
                    self.worker.join(timeout=5)
                self.root.after(0, self._on_worker_stopped)
            threading.Thread(target=_wait, daemon=True).start()
        self._update_buttons()

    def _on_worker_stopped(self):
        self._update_buttons()
        self._log("任务已停止")

    def on_close(self):
        # 停止 worker
        if self.worker and self.worker.is_alive():
            self._log("正在关闭...")
            self.worker.stop()
            self.worker.join(timeout=3)
        # 杀掉关联的 Chrome 进程
        if self.chrome_proc:
            try:
                import subprocess
                subprocess.run(["taskkill", "/PID", str(self.chrome_proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
            except Exception:
                pass
        # 释放端口锁 + 清理 session 文件
        port = self._locked_port or self._port_value()
        if port:
            config.unlock_port(port)
            try:
                os.remove(config.session_file(port))
            except Exception:
                pass
        self.root.destroy()

    def on_apply_params(self):
        if not self.sess:
            return
        try:
            self.sess.poll_interval = max(0.5, float(self.poll_var.get()) / 1000)  # ms→秒
        except Exception:
            pass
        try:
            self.sess.concurrency = max(1, int(self.concurrency_var.get()))
        except Exception:
            self.sess.concurrency = 10
        self.sess.save()
        self._log(f"参数已保存 | 轮询 {self.sess.poll_interval}s | 并发 {self.sess.concurrency}")
        if self.worker and self.worker.is_alive():
            self.worker.msg_queue.put(("params_updated", None))

    def on_set_token(self):
        if not self.sess:
            port = self._port_value()
            if not port:
                messagebox.showwarning("提示", "请先输入端口")
                return
            self.sess = session.SessionData.load(port)
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("提示", "请先粘贴 Token")
            return
        if not self._sync_sess_from_ui():
            return
        self.sess.token = token
        if not self.sess.login_time:
            self.sess.login_time = time.time()
        self.sess.save()
        self._log("Token 已设置，可点击启动")

    def on_refresh_token(self):
        port = self._port_value()
        if not port:
            messagebox.showwarning("提示", "请先输入端口")
            return
        if not self.sess:
            self.sess = session.SessionData.load(port)
        self._sync_sess_from_ui()
        self.btn_refresh_token.config(state="disabled", text="刷新中...")
        def _run():
            try:
                key, value, source = cdp.read_token(
                    port, token_key=self.sess.token_key, url=self.sess.login_url)
                if not value:
                    self._log("刷新失败: 未在浏览器中检测到 Token，请先在该端口浏览器登录")
                    return
                self.token_var.set(value)
                self.sess.token = value
                self.sess.login_time = time.time()
                self.sess.token_source = source or ""
                if key and key != "auto":
                    self.sess.token_key = key
                self.sess.save()
                self.lbl_token_remain.config(text=f"Token剩余: {_fmt(self.sess.token_remaining)}")
                self._log(f"已从端口 {port} 浏览器刷新 Token（来源 {source}）")
            except Exception as e:
                self._log(f"刷新Token异常: {e}")
            finally:
                self.root.after(0, lambda: self.btn_refresh_token.config(state="normal", text="刷新Token"))
        threading.Thread(target=_run, daemon=True).start()

    def on_manual_online(self):
        if not self.sess or not self.sess.token:
            messagebox.showwarning("提示", "尚无 Token，无法上线")
            return
        self.btn_online.config(state="disabled", text="上线中...")
        def _run():
            try:
                client = api.ApiClient(self.sess.token, add_bearer=self.sess.add_bearer)
                r = client.online()
                if r.get("code") == 1000:
                    self._log("手动上线成功")
                else:
                    self._log(f"手动上线失败: {r.get('message')}")
            except Exception as e:
                self._log(f"手动上线异常: {e}")
            finally:
                self.root.after(0, lambda: self.btn_online.config(state="normal", text="手动上线"))
        threading.Thread(target=_run, daemon=True).start()

    # ---------- queue draining ----------
    def _drain_queue(self):
        try:
            if self.worker:
                while True:
                    try:
                        kind, data = self.worker.msg_queue.get_nowait()
                    except Exception:
                        break
                    self._handle_msg(kind, data)
        finally:
            if self.worker and self.worker.is_alive():
                self.root.after(120, self._drain_queue)
            else:
                self._update_buttons()

    def _handle_msg(self, kind, data):
        if kind == "log":
            self._log(data)
        elif kind == "status":
            kw = data
            if "token_found" in kw:
                # 浏览器登录方式：自动回填 token 到输入框，无需手动确认
                tv = kw.get("token_value")
                if tv and not self.token_var.get().strip():
                    self.token_var.set(tv)
            if "token_remaining" in kw:
                self.lbl_token_remain.config(text=f"Token剩余: {_fmt(kw['token_remaining'])}")
            if "online_remaining" in kw:
                self.lbl_online_remain.config(text=f"在线剩余: {_fmt(kw['online_remaining'])}")
            if "online_active" in kw:
                self.lbl_online_remain.config(
                    foreground="#27ae60" if kw["online_active"] else "#c0392b")
            if "socket_connected" in kw:
                ok = kw["socket_connected"]
                self.lbl_socket.config(
                    text=f"WS: {'已连接' if ok else '未连接'}",
                    foreground="#27ae60" if ok else "#c0392b")
            if "order_stats" in kw:
                grab, assign = kw["order_stats"]
                self.lbl_grab.config(text=f"抢单: {grab}")
                self.lbl_assign.config(text=f"派单: {assign}")
            if "home_total" in kw:
                self.lbl_total.config(text=f"总单: {kw['home_total']}")
        elif kind == "expired":
            self._log("Token 已过期，任务终止，请重新登录")
            messagebox.showwarning("Token 过期", "Token 已过期(24小时)，请重新登录后启动")
        elif kind == "finished":
            self._log(f"任务结束: {data}")


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()
