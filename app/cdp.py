import json
import os
import subprocess
import time
import urllib.request
import websocket

from . import config


def launch_chrome(port, url, chrome_path=None):
    if chrome_path is None:
        chrome_path = config.find_chrome()
    if not chrome_path:
        raise FileNotFoundError("未找到 Chrome / Edge 可执行文件，请安装 Google Chrome。")
    user_dir = config.chrome_user_dir(port)
    os.makedirs(user_dir, exist_ok=True)
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--remote-allow-origins=*",
    ]
    if url:
        args.append(url)
    proc = subprocess.Popen(args)
    return proc


def _http_get_json(url):
    with urllib.request.urlopen(url, timeout=config.REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_targets(port):
    try:
        return _http_get_json(f"http://127.0.0.1:{port}/json")
    except Exception:
        return []


def get_page_targets(port):
    return [t for t in get_targets(port) if t.get("type") == "page"]


def get_browser_ws_url(port):
    try:
        info = _http_get_json(f"http://127.0.0.1:{port}/json/version")
        return info.get("webSocketDebuggerUrl")
    except Exception:
        return None


def wait_for_devtools(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            _http_get_json(f"http://127.0.0.1:{port}/json/version")
            return True
        except Exception:
            time.sleep(0.5)
    return False


class CDPClient:
    def __init__(self, ws_url, timeout=config.REQUEST_TIMEOUT):
        if not ws_url:
            raise RuntimeError("无可用 WebSocket 调试地址")
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        deadline = time.time() + config.REQUEST_TIMEOUT
        while time.time() < deadline:
            raw = self.ws.recv()
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError(f"CDP 错误({method}): {data['error']}")
                return data.get("result")
        raise TimeoutError(f"CDP 命令超时: {method}")

    def evaluate(self, expression):
        res = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if not res:
            return None
        result = res.get("result") or {}
        exc = res.get("exceptionDetails")
        if exc:
            return None
        return result.get("value")

    def get_local_storage(self):
        value = self.evaluate("JSON.stringify(localStorage)")
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    def get_cookies(self):
        try:
            res = self.send("Storage.getCookies")
            return (res or {}).get("cookies", [])
        except Exception:
            try:
                res = self.send("Network.getCookies")
                return (res or {}).get("cookies", [])
            except Exception:
                return []

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def ensure_domain_target(port, url):
    pages = get_page_targets(port)
    for t in pages:
        if config.DOMAIN_HINT in t.get("url", ""):
            return t
    ws_url = get_browser_ws_url(port)
    if ws_url:
        try:
            client = CDPClient(ws_url)
            try:
                client.send("Target.createTarget", {"url": url or config.DEFAULT_LOGIN_URL})
            finally:
                client.close()
            time.sleep(1.5)
        except Exception:
            pass
    pages = get_page_targets(port)
    for t in pages:
        if config.DOMAIN_HINT in t.get("url", ""):
            return t
    return pages[0] if pages else None


_TOKEN_KEY_HINTS = ("token", "auth", "authorization", "accesstoken", "access_token", "bearertoken")


def _looks_like_token(value):
    if not isinstance(value, str):
        return False
    if len(value) < 16:
        return False
    return value.startswith("eyJ") or len(value) >= 32


def _detect_in_dict(d, source):
    candidates = []
    for k, v in d.items():
        if not isinstance(v, str):
            continue
        kl = k.lower()
        if any(h in kl for h in _TOKEN_KEY_HINTS):
            candidates.append((k, v, source))
        elif _looks_like_token(v):
            candidates.append((k, v, source))
        else:
            try:
                inner = json.loads(v)
                if isinstance(inner, dict):
                    for ik, iv in inner.items():
                        if isinstance(iv, str) and any(h in ik.lower() for h in _TOKEN_KEY_HINTS):
                            candidates.append((f"{k}.{ik}", iv, source))
                        elif isinstance(iv, str) and _looks_like_token(iv):
                            candidates.append((f"{k}.{ik}", iv, source))
            except Exception:
                pass
    return candidates


def _score(c):
    k, v, _ = c
    s = 0
    kl = k.lower()
    if kl == "token":
        s += 200
    elif kl in ("authorization", "authtoken", "accesstoken", "access_token"):
        s += 150
    elif "token" in kl:
        s += 100
    elif "auth" in kl:
        s += 80
    if v.startswith("eyJ"):
        s += 60
    s += min(len(v), 50)
    return s


def read_token(port, token_key="auto", url=None):
    target = ensure_domain_target(port, url)
    if not target:
        return None, "未找到浏览器页面，请先打开浏览器", None
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "页面缺少调试地址", None
    try:
        client = CDPClient(ws_url)
    except Exception as e:
        return None, f"连接调试端口失败: {e}", None
    try:
        ls = client.get_local_storage()
        cookies = client.get_cookies()
    except Exception as e:
        return None, f"读取存储失败: {e}", None
    finally:
        client.close()

    if token_key and token_key != "auto":
        if token_key in ls:
            return token_key, ls[token_key], "localStorage"
        for c in cookies:
            if c.get("name") == token_key:
                return token_key, c.get("value", ""), "cookie"

    candidates = _detect_in_dict(ls, "localStorage")
    for c in cookies:
        n = c.get("name", "")
        v = c.get("value", "")
        if any(h in n.lower() for h in _TOKEN_KEY_HINTS) or _looks_like_token(v):
            candidates.append((n, v, "cookie"))

    if not candidates:
        return None, "未检测到 token，请在浏览器中完成登录", None

    candidates.sort(key=_score, reverse=True)
    best = candidates[0]
    return best[0], best[1], best[2]
