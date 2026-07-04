import queue
import threading
import time
import traceback
import json
from concurrent.futures import ThreadPoolExecutor

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
        msg_queue.put(("log", f"[{time.strftime('%H:%M:%S')}] {msg}"))

    def status(**kw):
        msg_queue.put(("status", kw))

    def orders(list_data):
        msg_queue.put(("orders", list_data))

    poll = max(0.5, float(getattr(sess, "poll_interval", config.DEFAULT_POLL_INTERVAL) or config.DEFAULT_POLL_INTERVAL))
    conc = max(1, int(getattr(sess, "concurrency", 10) or 10))
    log(f"任务已启动 | 端口 {sess.port} | 轮询 {poll}s | 并发 {conc}")

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

    # 已接单去重集合（进程内）
    accepted = set()
    accepted_lock = threading.Lock()
    stats = {"grab": int(getattr(sess, "grab_count", 0) or 0),
             "assign": int(getattr(sess, "assign_count", 0) or 0)}
    status(order_stats=(stats["grab"], stats["assign"]))
    # 持久线程池，避免重复创建开销
    accept_pool = ThreadPoolExecutor(max_workers=conc, thread_name_prefix="accept")

    def do_accept(order_obj, source):
        """在 accept_pool 中异步执行接单。"""
        order_no = (order_obj or {}).get("orderNo", "")
        if not order_no:
            return
        with accepted_lock:
            if order_no in accepted:
                return
            accepted.add(order_no)
        st = (order_obj or {}).get("status") or ""
        kind = "派单" if st == "assigned" else "抢单"
        def _run():
            try:
                ar = client.accept(order_no)
                ok = ar.get("code") == 1000
                if ok:
                    with accepted_lock:
                        if st == "assigned":
                            stats["assign"] += 1
                        else:
                            stats["grab"] += 1
                    sess.grab_count = stats["grab"]
                    sess.assign_count = stats["assign"]
                    sess.save()
                    status(order_stats=(stats["grab"], stats["assign"]))
                    log(f"[{source}][{kind}] 接单成功: {order_no} | USDT {order_obj.get('usdtAmount')} | 率 {order_obj.get('settleRate')}")
                else:
                    log(f"[{source}][{kind}] 接单失败: {order_no} | {ar.get('message')}")
            except Exception as e:
                log(f"[{source}][{kind}] 接单异常: {order_no} | {e}")
        accept_pool.submit(_run)

    def accept_list(lst, source):
        if not lst:
            return
        _save_orders(lst)
        for o in lst:
            do_accept(o, source)
        log(f"[{source}] 收到 {len(lst)} 个 → 已提交")

    def _save_orders(lst):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        for o in lst:
            line = json.dumps({"ts": ts, "order": o}, ensure_ascii=False) + "\n"
            try:
                with open(config.orders_log_file(sess.port), "a", encoding="utf-8") as f:
                    f.write(line)
                st = (o or {}).get("status", "")
                if st != "assigned":
                    with open(config.grab_log_file(sess.port), "a", encoding="utf-8") as f:
                        f.write(line)
            except Exception:
                pass

    # Socket 实时通道（主）
    sock = None
    def _on_order(lst):
        if lst:
            accept_list(lst, "Socket")
    try:
        sock = SocketOrderClient(
            token=sess.token,
            add_bearer=sess.add_bearer,
            poll_interval=sess.poll_interval,
            port=sess.port,
            on_order=_on_order,
            on_status=(lambda **kw: status(**kw)),
            on_log=(lambda m: log(m)),
        )
        sock.connect_async()
    except Exception as e:
        log(f"Socket 初始化异常: {e}")

    # HTTP 兜底轮询间隔放宽（Socket 已是主通道）

    last_poll = 0.0
    last_ttl = 0.0
    last_token_refresh = 0.0
    last_home = 0.0

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
                    log("Token 已从浏览器刷新")
                    if sock:
                        try:
                            sock.stop()
                            sock.token = value
                            sock.connect_async()
                        except Exception:
                            pass
                if value:
                    status(token_found=True)
            except Exception:
                pass

        # HTTP 兜底查询订单（与 Socket 同频）
        if now - last_poll >= poll:
            last_poll = now
            try:
                resp = client.my_orders()
                code = resp.get("code")
                if code == 1000:
                    data = resp.get("data") or {}
                    lst = data.get("list") or []
                    if lst:
                        accept_list(lst, "HTTP")
                    else:
                        log("[HTTP] 收到 0 个")
                else:
                    log(f"查询订单失败: code={code} msg={resp.get('message')}")
            except Exception as e:
                log(f"查询订单异常: {e}")

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

        # 在线保持
        if now - last_ttl >= config.DEFAULT_TTL_CHECK_INTERVAL:
            last_ttl = now
            try:
                tr = client.load_ttl()
                if tr.get("code") == 1000:
                    remain = tr.get("data")
                    status(online_remaining=remain, online_active=True)
                    if remain is None or (isinstance(remain, (int, float)) and remain <= config.RE_ONLINE_THRESHOLD):
                        ar = client.online()
                        if ar.get("code") == 1000:
                            log("在线倒计时结束，已自动重新上线")
                            status(online_remaining=config.ONLINE_MAX_SECONDS, online_active=True)
                        else:
                            log(f"自动上线失败: {ar.get('message')}")
                else:
                    log(f"查询在线剩余失败: {tr.get('message')}")
            except Exception as e:
                log(f"查询在线剩余异常: {e}")

        # 更新 token 剩余展示
        status(token_remaining=sess.token_remaining)

        if _sleep(stop_event, min(poll, 1.0)):
            break

    if sock:
        try:
            sock.stop()
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
