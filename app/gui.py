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
        self._build_ui()

    def _build_ui(self):
        root = self.root
        root.title(f"EasyFollow自动接单 v{config.APP_VERSION}")
        root.geometry("760x520")
        root.minsize(720, 480)

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
        ttk.Label(ctl, text="轮询(秒):").grid(row=0, column=2, padx=(10,2), pady=2)
        self.poll_var = tk.StringVar(value=str(int(config.DEFAULT_POLL_INTERVAL)))
        ttk.Entry(ctl, textvariable=self.poll_var, width=5).grid(row=0, column=3, padx=2, pady=2)
        ttk.Label(ctl, text="并发:").grid(row=0, column=4, padx=(10,2), pady=2)
        self.concurrency_var = tk.StringVar(value="3")
        ttk.Entry(ctl, textvariable=self.concurrency_var, width=5).grid(row=0, column=5, padx=2, pady=2)

        status = ttk.Frame(run_frame)
        status.pack(fill="x", padx=6, pady=2)
        self.lbl_grab = ttk.Label(status, text="抢单: 0", foreground="#d35400")
        self.lbl_grab.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.lbl_assign = ttk.Label(status, text="派单: 0", foreground="#2980b9")
        self.lbl_assign.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.lbl_socket = ttk.Label(status, text="WS: --", foreground="#888")
        self.lbl_socket.grid(row=0, column=2, sticky="w", padx=4, pady=2)
        self.lbl_online_remain = ttk.Label(status, text="在线剩余: --", foreground="#27ae60")
        self.lbl_online_remain.grid(row=0, column=3, sticky="w", padx=4, pady=2)
        self.lbl_token_remain = ttk.Label(status, text="Token剩余: --", foreground="#c0392b")
        self.lbl_token_remain.grid(row=0, column=4, sticky="w", padx=4, pady=2)

        orders = ttk.Frame(run_frame)
        orders.pack(fill="both", expand=True, padx=6, pady=2)
        cols = ("orderNo", "status", "usdtAmount", "expireTime", "settleRate")
        self.tree = ttk.Treeview(orders, columns=cols, show="headings", height=4)
        headers = {
            "orderNo": ("订单号", 180),
            "status": ("状态", 70),
            "usdtAmount": ("USDT", 70),
            "expireTime": ("过期时间", 130),
            "settleRate": ("结算率", 70),
        }
        for c in cols:
            self.tree.heading(c, text=headers[c][0])
            self.tree.column(c, width=headers[c][1], anchor="center")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(orders, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # ========== 区域三：日志 ==========
        log_frame = ttk.LabelFrame(root, text="日志")
        log_frame.pack(fill="both", expand=True, padx=6, pady=2)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=5, wrap="word",
                                                  font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

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
            self.sess = session.SessionData.load(port)
            self.sess.login_url = self.url_var.get() or config.DEFAULT_LOGIN_URL
            self.url_var.set(self.sess.login_url)
            self.poll_var.set(str(int(self.sess.poll_interval or config.DEFAULT_POLL_INTERVAL)))
            self.concurrency_var.set(str(int(self.sess.concurrency or 3)))
            self.tokenkey_var.set(self.sess.token_key or "auto")
            self.bearer_var.set(self.sess.add_bearer)
            self.token_var.set(self.sess.token or "")
            self.lbl_token_remain.config(text=f"Token剩余: {_fmt(self.sess.token_remaining)}")
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
        self.sess.login_url = self.url_var.get().strip() or config.DEFAULT_LOGIN_URL
        try:
            self.sess.poll_interval = max(0.5, float(self.poll_var.get()))
        except Exception:
            self.sess.poll_interval = config.DEFAULT_POLL_INTERVAL
        try:
            self.sess.concurrency = max(1, int(self.concurrency_var.get()))
        except Exception:
            self.sess.concurrency = 3
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
        url = self.url_var.get().strip() or config.DEFAULT_LOGIN_URL
        try:
            self._log(f"正在打开浏览器 | 端口 {port} | {url}")
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
        self.lbl_socket.config(text="WS: --", foreground="#888")
        self._log("任务已停止，订单表已清空")
        for item in self.tree.get_children():
            self.tree.delete(item)

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
        elif kind == "orders":
            self._update_orders(data)
        elif kind == "expired":
            self._log("Token 已过期，任务终止，请重新登录")
            messagebox.showwarning("Token 过期", "Token 已过期(24小时)，请重新登录后启动")
        elif kind == "finished":
            self._log(f"任务结束: {data}")

    def _update_orders(self, lst):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for o in lst:
            self.tree.insert("", "end", values=(
                o.get("orderNo", ""),
                o.get("status", ""),
                o.get("usdtAmount", ""),
                o.get("expireTime", ""),
                o.get("settleRate", ""),
            ))


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()
