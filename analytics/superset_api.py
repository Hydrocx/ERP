"""Client tối giản cho REST API của Apache Superset (chỉ dùng thư viện chuẩn).

Dùng bởi lệnh `python manage.py provision_superset`.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


class SupersetError(RuntimeError):
    pass


class SupersetClient:
    def __init__(self, base_url, username, password, timeout=120):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.token = None
        self.csrf = None
        self._login(username, password)

    # ----------------------------------------------------------------- nội bộ
    def _request(self, method, path, payload=None, headers=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if self.csrf:
            req.add_header("X-CSRFToken", self.csrf)
            req.add_header("Referer", self.base)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1500]
            raise SupersetError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SupersetError(f"Không kết nối được Superset tại {self.base}: {exc.reason}") from exc
        return json.loads(body) if body else {}

    def _login(self, username, password):
        result = self._request("POST", "/api/v1/security/login", {
            "username": username, "password": password, "provider": "db", "refresh": True,
        })
        self.token = result["access_token"]
        self.csrf = self._request("GET", "/api/v1/security/csrf_token/")["result"]

    # ------------------------------------------------------------------ chung
    def get(self, path):
        return self._request("GET", path)

    def post(self, path, payload):
        return self._request("POST", path, payload)

    def put(self, path, payload):
        return self._request("PUT", path, payload)

    def find(self, resource, column, value):
        """Tìm bản ghi đầu tiên khớp filter; trả về None nếu không có."""
        query = json.dumps({"filters": [{"col": column, "opr": "eq", "value": value}]})
        result = self.get(f"/api/v1/{resource}/?q={urllib.parse.quote(query)}")
        ids = result.get("ids") or []
        return ids[0] if ids else None
