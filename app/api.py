import requests

from . import config


class ApiClient:
    def __init__(self, token, add_bearer=False):
        self.token = token or ""
        self.add_bearer = add_bearer
        self.session = requests.Session()

    def _auth(self):
        t = self.token
        if self.add_bearer and t and not t.startswith("Bearer "):
            t = "Bearer " + t
        return t

    def _headers(self):
        return {
            "Authorization": self._auth(),
            "Content-Type": "application/json",
            "language": "en",
            "Referer": "https://mix.easyflow.finance/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }

    def _post(self, path, body=None):
        url = config.API_BASE + path
        resp = self.session.post(url, headers=self._headers(), json=body or {}, timeout=config.REQUEST_TIMEOUT)
        try:
            return resp.json()
        except Exception:
            return {"code": resp.status_code, "message": resp.text}

    def my_orders(self):
        return self._post(config.ENDPOINT_MY_ORDERS, {})

    def accept(self, order_no):
        return self._post(config.ENDPOINT_ACCEPT, {"orderNo": order_no})

    def online(self):
        return self._post(config.ENDPOINT_ONLINE, {"lStatus": True})

    def load_ttl(self):
        return self._post(config.ENDPOINT_LOAD_TTL, {})
