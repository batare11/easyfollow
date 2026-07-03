import os
import sys

APP_NAME = "EasyFollow"
APP_VERSION = "1.0.0"

API_BASE = "https://core-api.easyflow.xin"

ENDPOINT_MY_ORDERS = "/app/platform/orderAssignment/myOrders"
ENDPOINT_ACCEPT = "/app/platform/orderAssignment/accept"
ENDPOINT_ONLINE = "/app/platform/trader/listenOrderSwitch"
ENDPOINT_LOAD_TTL = "/app/platform/user/loadListenTTL"

DEFAULT_LOGIN_URL = "https://mix.easyflow.finance/#/pages/user/login"
DOMAIN_HINT = "easyflow"

LANGUAGE = "zh-tw"

TOKEN_VALID_SECONDS = 24 * 3600
ONLINE_MAX_SECONDS = 3600

DEFAULT_POLL_INTERVAL = 2.0
BASE_PORT = 9222
DEFAULT_TTL_CHECK_INTERVAL = 10.0
DEFAULT_TOKEN_REFRESH_INTERVAL = 60.0
RE_ONLINE_THRESHOLD = 60

REQUEST_TIMEOUT = 15

CHROME_PATH_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def app_data_dir():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base, "data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def chrome_user_dir(port):
    return os.path.join(app_data_dir(), f"chrome_profile_{port}")


def session_file(port):
    return os.path.join(app_data_dir(), f"account_{port}.json")


PORT_FILE = None


def _port_file():
    global PORT_FILE
    if PORT_FILE is None:
        PORT_FILE = os.path.join(app_data_dir(), "last_port.txt")
    return PORT_FILE


def load_last_port(default=BASE_PORT):
    try:
        with open(_port_file(), "r", encoding="utf-8") as f:
            v = (f.read() or "").strip()
            if v:
                return int(v)
    except Exception:
        pass
    return default


def save_last_port(port):
    try:
        with open(_port_file(), "w", encoding="utf-8") as f:
            f.write(str(port))
    except Exception:
        pass


def _used_ports():
    """扫描已保存的 session 文件，返回已被占用的端口集合。"""
    used = set()
    try:
        d = app_data_dir()
        for f in os.listdir(d):
            if f.startswith("account_") and f.endswith(".json"):
                try:
                    p = int(f[len("account_"):-len(".json")])
                    used.add(p)
                except Exception:
                    pass
    except Exception:
        pass
    return used


def recommended_port():
    """单开返回基础端口；多开依次递增（按已有 session 数）。"""
    used = _used_ports()
    port = BASE_PORT
    while port in used:
        port += 1
    return port


def find_chrome():
    for p in CHROME_PATH_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None
