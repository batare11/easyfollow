import json
import os
import threading
import time

import socketio

from . import config

SOCKET_URL = "https://socket-api.easyflow.xin"
SOCKET_NS = "/orderService"
EVENT_ORDER = "order"
EVENT_HEARTBEAT_ACK = "heartbeat_ack"


def _socket_log_file(port):
    return os.path.join(config.app_data_dir(), f"socket_{port}.log")


def save(self, data):
    pass


class SocketOrderClient:

    def __init__(self, token, add_bearer=True, poll_interval=1.0,
                 on_order=None, on_status=None, on_log=None, port=0):
        self.token = token or ""
        self.add_bearer = add_bearer
        self.poll_interval = max(0.5, float(poll_interval or 1.0))
        self.port = port
        self.on_order = on_order or (lambda orders: None)
        self.on_status = on_status or (lambda **kw: None)
        self.on_log = on_log or (lambda msg: None)
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=0.5,
            reconnection_delay_max=2,
            logger=False,
            engineio_logger=False,
        )
        self._connected = False
        self._stop = threading.Event()
        self._thread = None
        self._poll_thread = None
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
            self.on_log("Socket 已连接 /orderService")
            self.on_status(socket_connected=True)
            self._start_polling()

        @sio.event(namespace=SOCKET_NS)
        def connect_error(err):
            self._connected = False
            self._stop_polling()
            self.on_log(f"Socket 连接失败: {err}")
            self.on_status(socket_connected=False)

        @sio.event(namespace=SOCKET_NS)
        def disconnect():
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

    def _start_polling(self):
        self._stop_polling()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _stop_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            pass

    def _poll_loop(self):
        """模拟浏览器行为：每隔 poll_interval 秒主动 emit order 查询，并在 ACK 中获取订单。"""
        while self._connected and not self._stop.is_set():
            try:
                def _ack(data):
                    # data 是服务端对 emit 返回的 ACK 数据
                    if data:
                        self._log_socket_data(data)
                        self._handle_order(data)
                self.sio.emit(EVENT_ORDER, [], namespace=SOCKET_NS, callback=_ack)
            except Exception:
                pass
            self._stop.wait(self.poll_interval)

    def _log_socket_data(self, data):
        """非空 Socket 数据落盘。"""
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(_socket_log_file(self.port), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts, "data": data}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _handle_order(self, data):
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
            if self.sio.connected:
                self._stop.wait(1)
                continue
            try:
                self.sio.connect(
                    SOCKET_URL,
                    transports=["websocket"],
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
                self._connected = False
                self.on_log(f"Socket 连接异常: {e}")
                self.on_status(socket_connected=False)
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
        self._connected = False
        self.on_status(socket_connected=False)
