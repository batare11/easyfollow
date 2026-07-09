import os
import sys

APP_NAME = "EasyFlow"
APP_VERSION = "1.0.0"

API_BASE = "https://core-api.easyflow.xin"

ENDPOINT_MY_ORDERS = "/app/platform/orderAssignment/myOrders"
ENDPOINT_ACCEPT = "/app/platform/orderAssignment/accept"
ENDPOINT_GRAB = "/app/platform/orders/grab"
ENDPOINT_ONLINE = "/app/platform/trader/listenOrderSwitch"
ENDPOINT_LOAD_TTL = "/app/platform/user/loadListenTTL"
ENDPOINT_HOME_DATA = "/app/platform/home-page/homeData"
ENDPOINT_BALANCE = "/app/platform/user/balance"

DEFAULT_LOGIN_URL = "https://mix.easyflow.finance/#/pages/user/login"
DOMAIN_HINT = "easyflow"

LANGUAGE = "zh-tw"

TOKEN_VALID_SECONDS = 24 * 3600
ONLINE_MAX_SECONDS = 3600

DEFAULT_POLL_INTERVAL = 1.0
SOCKET_EMIT_INTERVAL = 1.0
BASE_PORT = 9222
DEFAULT_TTL_CHECK_INTERVAL = 10.0
BALANCE_CHECK_INTERVAL = 30.0
BALANCE_WARN_THRESHOLD = 500
DEFAULT_TOKEN_REFRESH_INTERVAL = 60.0
RE_ONLINE_THRESHOLD = 60

REQUEST_TIMEOUT = 15
ACCEPT_TIMEOUT = 1

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


def orders_log_file(port):
    return os.path.join(app_data_dir(), f"orders_{port}.log")


def grab_log_file(port):
    return os.path.join(app_data_dir(), f"grab_{port}.log")


def app_log_file(port):
    return os.path.join(app_data_dir(), f"app_{port}.log")


def accept_log_file(port):
    return os.path.join(app_data_dir(), f"accept_{port}.log")


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


def port_lock_file(port):
    return os.path.join(app_data_dir(), f"port_{port}.lock")


def _used_ports():
    """扫描 port_*.lock 文件，返回已锁定的端口集合。"""
    used = set()
    try:
        d = app_data_dir()
        for f in os.listdir(d):
            if f.startswith("port_") and f.endswith(".lock"):
                try:
                    used.add(int(f[len("port_"):-len(".lock")]))
                except Exception:
                    pass
    except Exception:
        pass
    return used


def lock_port(port):
    try:
        os.makedirs(app_data_dir(), exist_ok=True)
        with open(port_lock_file(port), "w") as f:
            f.write(str(port))
    except Exception:
        pass


def unlock_port(port):
    try:
        os.remove(port_lock_file(port))
    except Exception:
        pass


def recommended_port():
    """单开返回基础端口；多开依次递增。自动清理僵尸锁文件。"""
    import urllib.request
    used = _used_ports()
    for port in sorted(used):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        except Exception:
            unlock_port(port)
            used.discard(port)
    port = BASE_PORT
    while port in used:
        port += 1
    return port


def _chrome_from_registry():
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                    path, _ = winreg.QueryValueEx(key, "")
                    if path and os.path.isfile(path):
                        return path
            except OSError:
                pass
    except Exception:
        pass
    return None


def find_chrome():
    reg = _chrome_from_registry()
    if reg:
        return reg
    for p in CHROME_PATH_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None
