import base64
import json
import os
import queue
import threading
import time

import socketio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import os as _os

from . import config

SOCKET_URL = "https://socket-api.easyflow.xin"
SOCKET_NS = "/orderService"
EVENT_ORDER = "order"
EVENT_HEARTBEAT_ACK = "heartbeat_ack"
EVENT_SUBSCRIBE_RES = "updateSubcribeRes"


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_subscribe_info():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    auth = _os.urandom(16)
    return {
        "endpoint": "https://fcm.googleapis.com/fcm/send/easyflow",
        "p256dh": _b64url(pub_bytes),
        "auth": _b64url(auth),
        "lang": "zh-tw",
    }


def _socket_log_file(port):
    return os.path.join(config.app_data_dir(), f"socket_{port}.log")


def save(self, data):
    pass


class SocketOrderClient:

    def __init__(self, token, add_bearer=True, poll_interval=1.0,
                 on_order=None, on_status=None, on_log=None, port=0,
                 poll_offset=0):
        self.token = token or ""
        self.add_bearer = add_bearer
        self.poll_interval = max(0.5, float(poll_interval or 1.0))
        self.poll_offset = poll_offset
        self.port = port
        self.on_order = on_order or (lambda orders: None)
        self.on_status = on_status or (lambda **kw: None)
        self.on_log = on_log or (lambda msg: None)
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=0.3,
            reconnection_delay_max=1,
            logger=False,
            engineio_logger=False,
        )
        self._connected = False
        self._conn_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._poll_thread = None
        self._subscribe_info = None
        self._retry_delay = 0.5
        self._last_data_ts = time.time()
        self._register_handlers()

    @property
    def connected(self):
        return self._connected

    def _auth_payload(self):
        if self.add_bearer and self.token and not self.token.startswith("Bearer "):
            return {"authorization": "Bearer " + self.token}
        return {"token": self.token}

    def _register_handlers(self):
        sio = self.sio

        @sio.event(namespace=SOCKET_NS)
        def connect():
            with self._conn_lock:
                self._connected = True
            self._retry_delay = 0.2
            self.on_log("Socket 已连接 /orderService")
            self.on_status(socket_connected=True)
            self._start_polling()
            try:
                if self._subscribe_info is None:
                    self._subscribe_info = _make_subscribe_info()
                self.sio.emit(
                    "updateSubcribeInfo",
                    self._subscribe_info,
                    namespace=SOCKET_NS,
                )
            except Exception:
                pass

        @sio.event(namespace=SOCKET_NS)
        def connect_error(err):
            with self._conn_lock:
                self._connected = False
            self._stop_polling()
            self.on_status(socket_connected=False)

        @sio.event(namespace=SOCKET_NS)
        def disconnect():
            with self._conn_lock:
                self._connected = False
            self._stop_polling()
            self.on_log("Socket 已断开")
            self.on_status(socket_connected=False)

        @sio.on(EVENT_ORDER, namespace=SOCKET_NS)
        def on_order(data):
            try:
                self._handle_order(data)
            except Exception as e:
                self.on_log(f"Socket 订单事件异常: {e}")

        @sio.on(EVENT_HEARTBEAT_ACK, namespace=SOCKET_NS)
        def on_hb(*a):
            self.on_status(socket_last_hb=time.time())

        @sio.on(EVENT_SUBSCRIBE_RES, namespace=SOCKET_NS)
        def on_sub_res(data):
            if data:
                self.on_log(f"推送注册: {data}")

    def _start_polling(self):
        self._stop_polling()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _stop_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            pass

    def _poll_loop(self):
        """模拟浏览器行为：每隔 poll_interval 秒主动 emit order 查询，并在 ACK 中获取订单。"""
        if self.poll_offset:
            self._stop.wait(self.poll_offset)
        last_heartbeat = time.time()
        while self._connected and not self._stop.is_set():
            now = time.time()
            try:
                def _ack(data):
                    self._last_data_ts = time.time()
                    if data:
                        self._handle_order(data)
                self.sio.emit(EVENT_ORDER, [], namespace=SOCKET_NS, callback=_ack)
            except Exception:
                pass
            # 每10秒发送心跳保持连接，防止服务端闲置断开
            if now - last_heartbeat >= 10:
                last_heartbeat = now
                try:
                    self.sio.emit("heartbeat", {"type": "ping"}, namespace=SOCKET_NS)
                except Exception:
                    pass
            self._stop.wait(config.SOCKET_EMIT_INTERVAL)

    def _handle_order(self, data):
        self._last_data_ts = time.time()
        if not data:
            self.on_status(socket_last_order=time.time(), socket_order_count=0)
            self.on_order([])
            return
        if isinstance(data, list):
            lst = data
        elif isinstance(data, dict):
            if "list" in data:
                lst = data["list"]
            elif "orderNo" in data:
                lst = [data]
            else:
                lst = [data]
        else:
            lst = []
        self.on_status(socket_last_order=time.time(), socket_order_count=len(lst))
        self.on_order(lst)

    def connect_async(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            with self._conn_lock:
                connected = self._connected
            if connected:
                poll_alive = self._poll_thread and self._poll_thread.is_alive()
                if not poll_alive:
                    self.on_log("Socket poll线程已死，强制重置")
                    with self._conn_lock:
                        self._connected = False
                    try:
                        self.sio.disconnect()
                    except Exception:
                        pass
                    continue
                if time.time() - self._last_data_ts > 15:
                    self.on_log("Socket 假在线，强制重置")
                    with self._conn_lock:
                        self._connected = False
                    self._stop_polling()
                    try:
                        self.sio.disconnect()
                    except Exception:
                        pass
                    self._last_data_ts = time.time()
                    continue
                self._stop.wait(1)
                continue
            try:
                self.sio.connect(
                    SOCKET_URL,
                    transports=["websocket", "polling"],
                    namespaces=[SOCKET_NS],
                    auth=self._auth_payload(),
                    socketio_path="/socket.io",
                    headers={
                        "Origin": "https://mix.easyflow.finance",
                        "User-Agent": "Mozilla/5.0 EasyFlow",
                    },
                    wait_timeout=8,
                )
            except Exception as e:
                err_msg = str(e)
                if "Already connected" in err_msg:
                    with self._conn_lock:
                        self._connected = True
                    self._start_polling()
                    self.on_status(socket_connected=True)
                    continue
                with self._conn_lock:
                    self._connected = False
                self.on_log(f"Socket 重连失败 ({self._retry_delay:.1f}s): {err_msg}")
                self.on_status(socket_connected=False)
                self._stop.wait(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, 30)
                continue
            if self._stop.is_set():
                break
            self._stop.wait(0.5)

    def stop(self):
        self._stop.set()
        self._stop_polling()
        try:
            self.sio.disconnect()
        except Exception:
            pass
        with self._conn_lock:
            self._connected = False
        self.on_status(socket_connected=False)
