import json
import time
import os
import base64

from . import config


def _decode_jwt_exp(token):
    """从JWT中解析exp字段，失败返回None。"""
    try:
        parts = (token or "").split(".")
        if len(parts) < 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("exp")
    except Exception:
        return None


class SessionData:
    def __init__(self, port):
        self.port = port
        self.token = ""
        self.login_time = 0.0
        self.token_key = "auto"
        self.login_url = config.DEFAULT_LOGIN_URL
        self.poll_interval = config.DEFAULT_POLL_INTERVAL
        self.add_bearer = False
        self.token_source = ""
        self.account = ""
        self.concurrency = 10
        self.grab_count = 0
        self.assign_count = 0

    @property
    def token_remaining(self):
        if self.token:
            exp = _decode_jwt_exp(self.token)
            if exp:
                return max(0, int(exp - time.time()))
        if self.login_time:
            return max(0, int(config.TOKEN_VALID_SECONDS - (time.time() - self.login_time)))
        return config.TOKEN_VALID_SECONDS

    @property
    def token_expired(self):
        return self.token_remaining <= 0

    def to_dict(self):
        return {
            "port": self.port,
            "token": self.token,
            "login_time": self.login_time,
            "token_key": self.token_key,
            "login_url": self.login_url,
            "poll_interval": self.poll_interval,
            "add_bearer": self.add_bearer,
            "token_source": self.token_source,
            "account": self.account,
            "concurrency": self.concurrency,
            "grab_count": self.grab_count,
            "assign_count": self.assign_count,
        }

    def save(self):
        path = config.session_file(self.port)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, port):
        s = cls(port)
        path = config.session_file(port)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k in ("token", "login_time", "token_key", "login_url",
                          "poll_interval", "add_bearer", "token_source", "account",
                          "concurrency", "grab_count", "assign_count"):
                    if k in data:
                        setattr(s, k, data[k])
            except Exception:
                pass
        return s
