import queue
import threading
import time
import traceback

from .accept_engine import AcceptEngine
from . import config, cdp, api
from .socket_client import SocketOrderClient


def _sleep(stop_event, secs):
    end = time.time() + secs
    while time.time() < end:
        if stop_event.is_set():
            return True
        time.sleep(0.2)
    return False


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


def worker_loop(sess, stop_event, msg_queue):
    def log(msg):
        now = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(now))
        ms = int((now % 1) * 1000)
        msg_queue.put(("log", f"[{ts}.{ms:03d}] {msg}"))

    def status(**kw):
        msg_queue.put(("status", kw))

    def orders(list_data):
        msg_queue.put(("orders", list_data))

    # HTTP兜底轮询间隔（派单不着急，固定1.5s）
    poll = config.DEFAULT_POLL_INTERVAL
    conc = max(1, int(getattr(sess, "concurrency", 80) or 80))
    log(f"任务已启动 | 端口 {sess.port} | 并发 {conc}")

    # 阶段一：登录检测
    if sess.token_expired or not sess.token:
        if sess.token_expired:
            log("Token 已过期(24h)，进入重新登录检测...")
            sess.token = ""
        while not stop_event.is_set():
            try:
                key, value, source = cdp.read_token(
                    sess.port, token_key=sess.token_key, url=sess.login_url
                )
                if value:
                    sess.token = value
                    sess.token_source = source or ""
                    sess.login_time = time.time()
                    sess.token_key = key if key and key != "auto" else sess.token_key
                    sess.save()
                    log(f"检测到登录成功 | token来源: {source} / 键: {key}")
                    status(token_found=True, login_time=sess.login_time,
                           token_source=sess.token_source, token_value=value)
                    break
                else:
                    log("未检测到 token，请在浏览器中完成登录后等待自动检测...")
            except Exception as e:
                log(f"读取 token 异常: {e}")
            if _sleep(stop_event, 3):
                break
        else:
            log("已停止")
            msg_queue.put(("finished", "stopped"))
            return

    if stop_event.is_set():
        log("已停止")
        msg_queue.put(("finished", "stopped"))
        return

    # 阶段二：上线 + 主循环
    client = api.ApiClient(sess.token, add_bearer=sess.add_bearer)
    try:
        # 先查询剩余在线时间，不到阈值才上线
        tr = client.load_ttl()
        remain = tr.get("data") if tr.get("code") == 1000 else None
        if remain is None or (isinstance(remain, (int, float)) and remain <= config.RE_ONLINE_THRESHOLD):
            r = client.online()
            if r.get("code") == 1000:
                log("已主动上线")
                status(online_active=True, online_remaining=config.ONLINE_MAX_SECONDS)
            else:
                log(f"上线返回: {r.get('message')}")
        else:
            log(f"在线剩余 {int(remain) if remain else '?'}s，无需重复上线")
            status(online_active=True, online_remaining=remain)
    except Exception as e:
        log(f"初始化在线检查异常: {e}")

    stats = {"grab": 0, "assign": 0}
    sess.grab_count = 0
    sess.assign_count = 0
    try:
        sess.save()
    except Exception:
        pass
    _stats_lock = threading.Lock()
    _sess_dirty = [False]
    _seen_orders = set()
    status(order_stats=(stats["grab"], stats["assign"]))

    # aiohttp 异步接单引擎（单线程事件循环，并发上限由 semaphore 控制）
    accept_engine = AcceptEngine(
        token=sess.token,
        add_bearer=sess.add_bearer,
        concurrency=conc,
        on_error=lambda msg: log(msg),
    )
    accept_engine.start()

    def _on_accept_ok(_no, _obj, _src, elapsed):
        is_assigned = (_obj or {}).get("status") == "assigned"
        with _stats_lock:
            if is_assigned:
                stats["assign"] += 1
            else:
                stats["grab"] += 1
        if not _sess_dirty[0]:
            _sess_dirty[0] = True
        status(order_stats=(stats["grab"], stats["assign"]))
        if _src == "Socket":
            kind = "派单" if is_assigned else "抢单"
            log(f"[{_src}][{kind}] ✅ {_no} | "
                f"USDT {_obj.get('usdtAmount')} | 率 {_obj.get('settleRate')} | {elapsed}ms")

    def _on_accept_fail(_no, _obj, _src, msg, elapsed):
        if _src == "Socket":
            kind = "派单" if (_obj or {}).get("status") == "assigned" else "抢单"
            log(f"[{_src}][{kind}] ❌ {_no} | {msg} | {elapsed}ms")

    def accept_list(lst, source):
        if not lst:
            if source == "Socket":
                log("[Socket] 收到 0 个")
            return
        tasks = []
        for o in lst:
            order_no = (o or {}).get("orderNo", "")
            if not order_no:
                continue
            if order_no in _seen_orders:
                continue
            _seen_orders.add(order_no)
            if len(_seen_orders) > 10000:
                _seen_orders.clear()
            tasks.append(o)

        if tasks:
            accept_engine.enqueue_batch(tasks, source, _on_accept_ok, _on_accept_fail)
        if source == "Socket":
            log(f"[{source}] ✅ 收到 {len(lst)} 个 → 已提交")

    # Socket 实时通道（双连接冗余，断一条另一条继续收单）
    sock1 = None
    sock2 = None
    _sock_state = {"connected": False, "count": 0}
    _force_reonline = False
    _zero_log_state = {"ts": 0.0}

    def _on_order(lst):
        if lst:
            accept_list(lst, "Socket")
        else:
            now = time.time()
            if now - _zero_log_state["ts"] >= 3:
                _zero_log_state["ts"] = now
                log("[Socket] 收到 0 个")

    def _on_sock_status(**kw):
        if "socket_connected" in kw:
            if kw["socket_connected"]:
                _sock_state["count"] += 1
            else:
                _sock_state["count"] = max(0, _sock_state["count"] - 1)
            was_connected = _sock_state["connected"]
            _sock_state["connected"] = _sock_state["count"] > 0
            if was_connected and not _sock_state["connected"]:
                nonlocal _force_reonline
                _force_reonline = True
        status(**kw)

    def _make_socket(offset=0):
        return SocketOrderClient(
            token=sess.token,
            add_bearer=sess.add_bearer,
            port=sess.port,
            on_order=_on_order,
            on_status=_on_sock_status,
            on_log=(lambda m: log(m)),
            poll_offset=offset,
        )

    try:
        sock1 = _make_socket()
        sock1.connect_async()
        sock2 = _make_socket(offset=0.5)
        sock2.connect_async()
    except Exception as e:
        log(f"Socket 初始化异常: {e}")

    # HTTP兜底轮询独立线程（不阻塞主循环）
    def _http_poller():
        while not stop_event.is_set():
            http_interval = poll / 4 if not _sock_state["connected"] else poll
            http_interval = max(http_interval, 0.2)
            try:
                resp = client.my_orders()
                code = resp.get("code")
                if code == 1000:
                    data = resp.get("data") or {}
                    lst = data.get("list") or []
                    if lst:
                        accept_list(lst, "HTTP")
                elif code not in (-1,):
                    log(f"查询订单失败: code={code} msg={resp.get('message')}")
            except Exception as e:
                log(f"查询订单异常: {e}")
            _sleep(stop_event, http_interval)

    _http_thread = threading.Thread(target=_http_poller, daemon=True)
    _http_thread.start()

    last_ttl = 0.0
    last_reonline = 0.0
    last_token_refresh = 0.0
    last_home = 0.0
    last_balance = 0.0

    while not stop_event.is_set():
        now = time.time()

        # token 倒计时
        token_remaining = sess.token_remaining
        status(token_remaining=token_remaining)
        if sess.token_expired:
            log("Token 已过期(24h)，请重新登录")
            msg_queue.put(("expired", "token"))
            break

        # 定时刷新 token（前端可能续期）
        if now - last_token_refresh >= config.DEFAULT_TOKEN_REFRESH_INTERVAL:
            last_token_refresh = now
            try:
                key, value, source = cdp.read_token(
                    sess.port, token_key=sess.token_key, url=sess.login_url
                )
                if value and value != sess.token:
                    sess.token = value
                    sess.save()
                    client.token = value
                    accept_engine.token = value
                    log("Token 已从浏览器刷新")
                    for s in (sock1, sock2):
                        if s:
                            try:
                                s.stop()
                                s.token = value
                                s.connect_async()
                            except Exception:
                                pass
                if value:
                    status(token_found=True)
            except Exception:
                pass

        # 总单查询
        if now - last_home >= 5:
            last_home = now
            try:
                hr = client.home_data()
                if hr.get("code") == 1000:
                    hd = hr.get("data") or {}
                    status(home_total=hd.get("progressTotal", 0))
            except Exception:
                pass

        # 余额查询
        if now - last_balance >= config.BALANCE_CHECK_INTERVAL:
            last_balance = now
            try:
                br = client.balance()
                if br.get("code") == 1000:
                    bd = br.get("data") or {}
                    bal = float(bd.get("usdtBalance", 0) or 0)
                    if bal < config.BALANCE_WARN_THRESHOLD:
                        msg_queue.put(("red_log",
                            f"余额不足 USDT {bal:.4f}，低于 {config.BALANCE_WARN_THRESHOLD}，请及时充值"))
            except Exception:
                pass

        # 在线保持
        if _force_reonline or (now - last_ttl >= config.DEFAULT_TTL_CHECK_INTERVAL):
            _force_reonline = False
            last_ttl = now
            # 批量落盘统计计数（避免每次接单都写文件）
            if _sess_dirty[0]:
                _sess_dirty[0] = False
                sess.grab_count = stats["grab"]
                sess.assign_count = stats["assign"]
                try:
                    sess.save()
                except Exception:
                    pass
            try:
                tr = client.load_ttl()
                need_reonline = False
                if tr.get("code") == 1000:
                    remain = tr.get("data")
                    status(online_remaining=remain, online_active=True)
                    if remain is None or (isinstance(remain, (int, float)) and remain <= config.RE_ONLINE_THRESHOLD):
                        need_reonline = True
                else:
                    log(f"查询在线剩余失败: {tr.get('message')}")
                    remain = None
                    need_reonline = True
                if need_reonline:
                    is_low = isinstance(remain, (int, float)) and remain <= 10
                    cooldown = 30 if is_low else 300
                    if now - last_reonline >= cooldown:
                        last_reonline = now
                        ar = client.online()
                        if ar.get("code") == 1000:
                            log("在线倒计时结束，已自动重新上线")
                            status(online_remaining=config.ONLINE_MAX_SECONDS, online_active=True)
                        else:
                            log(f"自动上线失败: {ar.get('message')}")
            except Exception as e:
                log(f"查询在线剩余异常: {e}")

        # 更新 token 剩余展示
        status(token_remaining=sess.token_remaining)

        if _sleep(stop_event, min(poll, 1.0)):
            break

    for s in (sock1, sock2):
        if s:
            try:
                s.stop()
            except Exception:
                pass
    try:
        accept_engine.stop()
    except Exception:
        pass
    if _sess_dirty[0]:
        sess.grab_count = stats["grab"]
        sess.assign_count = stats["assign"]
        try:
            sess.save()
        except Exception:
            pass
    log("任务已停止")
    msg_queue.put(("finished", "stopped"))


class Worker(threading.Thread):
    def __init__(self, sess):
        super().__init__(daemon=True)
        self.sess = sess
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()

    def run(self):
        try:
            worker_loop(self.sess, self.stop_event, self.msg_queue)
        except Exception:
            self.msg_queue.put(("log", f"[严重错误] {traceback.format_exc()}"))
            self.msg_queue.put(("finished", "error"))

    def stop(self):
        self.stop_event.set()
