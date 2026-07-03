import json
import time
import os

from . import config


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
        self.concurrency = 3

    @property
    def token_remaining(self):
        if not self.login_time:
            return config.TOKEN_VALID_SECONDS
        return max(0, int(config.TOKEN_VALID_SECONDS - (time.time() - self.login_time)))

    @property
    def token_expired(self):
        return self.login_time and (time.time() - self.login_time) >= config.TOKEN_VALID_SECONDS

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
                          "concurrency"):
                    if k in data:
                        setattr(s, k, data[k])
            except Exception:
                pass
        return s
