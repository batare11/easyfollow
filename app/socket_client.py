import threading
import time
import json

import socketio

from . import config


SOCKET_URL = "https://socket-api.easyflow.xin"
SOCKET_NS = "/orderService"
EVENT_ORDER = "order"
EVENT_HEARTBEAT_ACK = "heartbeat_ack"


class SocketOrderClient:
    """实时订单通道：连 /orderService 监听 order 事件。"""

    def __init__(self, token, add_bearer=True, on_order=None, on_status=None, on_log=None):
        self.token = token or ""
        self.add_bearer = add_bearer
        self.on_order = on_order or (lambda orders: None)
        self.on_status = on_status or (lambda **kw: None)
        self.on_log = on_log or (lambda msg: None)
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=0,
                                   reconnection_delay=2, reconnection_delay_max=10,
                                   logger=False, engineio_logger=False)
        self._lock = threading.Lock()
        self._connected = False
        self._stop = threading.Event()
        self._thread = None
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
            self._connected = True
            self.on_log(f"Socket 已连接 /orderService")
            self.on_status(socket_connected=True)

        @sio.event(namespace=SOCKET_NS)
        def connect_error(err):
            self._connected = False
            self.on_log(f"Socket 连接失败: {err}")
            self.on_status(socket_connected=False)

        @sio.event(namespace=SOCKET_NS)
        def disconnect():
            self._connected = False
            self.on_log("Socket 已断开，等待自动重连...")
            self.on_status(socket_connected=False)

        @sio.on(EVENT_ORDER, namespace=SOCKET_NS)
        def on_order(data):
            try:
                self._handle_order(data)
            except Exception as e:
                self.on_log(f"处理 socket 订单事件异常: {e}")

        @sio.on(EVENT_HEARTBEAT_ACK, namespace=SOCKET_NS)
        def on_hb(*a):
            self.on_status(socket_last_hb=time.time())

    def _handle_order(self, data):
        normalized = []
        if isinstance(data, list):
            normalized = data
        elif isinstance(data, dict):
            if "list" in data:
                normalized = data["list"]
            elif "orderNo" in data:
                normalized = [data]
            else:
                normalized = [data]
        self.on_status(socket_last_order=time.time(), socket_order_count=len(normalized))
        self.on_order(normalized)

    def connect_async(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                if self._connected:
                    time.sleep(0.5)
                    continue
                self.sio.connect(
                    SOCKET_URL,
                    transports=["websocket"],
                    namespaces=[SOCKET_NS],
                    auth=self._auth_payload(),
                    socketio_path="/socket.io",
                    headers={"Origin": "https://mix.easyflow.finance",
                             "User-Agent": "Mozilla/5.0 EasyFollow"},
                    wait_timeout=8,
                )
            except Exception as e:
                self._connected = False
                self.on_log(f"Socket 连接异常: {e}")
                self._stop.wait(3)
            while not self._stop.is_set() and self._connected:
                self._stop.wait(1)
            if self._stop.is_set():
                break
            self._stop.wait(3)

    def stop(self):
        self._stop.set()
        try:
            self.sio.disconnect()
        except Exception:
            pass
        self._connected = False
        self.on_status(socket_connected=False)