from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass
class ZabbixConfig:
    base_url: str
    username: str = ""
    password: str = ""
    api_token: str = ""
    verify_ssl: bool = True
    ca_bundle: str = ""
    request_timeout_sec: int = 30

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ZabbixConfig":
        row = payload or {}
        return cls(
            base_url=str(row.get("base_url") or "").strip(),
            username=str(row.get("username") or "").strip(),
            password=str(row.get("password") or ""),
            api_token=str(row.get("api_token") or "").strip(),
            verify_ssl=bool(row.get("verify_ssl", True)),
            ca_bundle=str(row.get("ca_bundle") or "").strip(),
            request_timeout_sec=max(5, min(120, int(row.get("request_timeout_sec") or 30))),
        )

    def api_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.lower().endswith("/api_jsonrpc.php"):
            return base
        return base + "/api_jsonrpc.php"

    def validate(self) -> None:
        if not self.base_url:
            raise ValueError("zabbix base_url is required")
        if not (self.api_token or (self.username and self.password)):
            raise ValueError("zabbix credentials required: api_token or username+password")


class ZabbixClient:
    def __init__(self, config: ZabbixConfig) -> None:
        self.cfg = config
        self._id = 1
        self._session_auth: str | None = None

    def _next_id(self) -> int:
        i = self._id
        self._id += 1
        return i

    def _ssl_context(self) -> ssl.SSLContext:
        if not self.cfg.verify_ssl:
            return ssl._create_unverified_context()  # nosec B323
        if self.cfg.ca_bundle:
            return ssl.create_default_context(cafile=self.cfg.ca_bundle)
        return ssl.create_default_context()

    def _post_json(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        req = urlrequest.Request(
            self.cfg.api_url(),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json-rpc", **(headers or {})},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.cfg.request_timeout_sec, context=self._ssl_context()) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail[:400]}") from exc
        except Exception as exc:
            raise RuntimeError(f"request failed: {exc}") from exc
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise RuntimeError("invalid response payload")
            return payload
        except Exception as exc:
            raise RuntimeError(f"response parse failed: {exc}") from exc

    def _rpc(self, method: str, params: dict[str, Any] | list[Any] | None = None, auth: str | None = None) -> Any:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._next_id(),
        }
        headers: dict[str, str] = {}
        if self.cfg.api_token:
            headers["Authorization"] = f"Bearer {self.cfg.api_token}"
        elif auth:
            body["auth"] = auth
        payload = self._post_json(body, headers=headers)
        if "error" in payload:
            err = payload.get("error")
            if isinstance(err, dict):
                code = err.get("code")
                msg = err.get("message")
                data = err.get("data")
                raise RuntimeError(f"zabbix rpc error code={code} message={msg} data={data}")
            raise RuntimeError(f"zabbix rpc error: {err}")
        return payload.get("result")

    def _auth(self) -> str | None:
        if self.cfg.api_token:
            return None
        if self._session_auth:
            return self._session_auth
        auth = self._rpc("user.login", {"username": self.cfg.username, "password": self.cfg.password}, auth=None)
        token = str(auth or "").strip()
        if not token:
            raise RuntimeError("zabbix login returned empty auth token")
        self._session_auth = token
        return token

    def api_version(self) -> str:
        result = self._rpc("apiinfo.version", {}, auth=None)
        return str(result or "")

    def ping(self) -> dict[str, Any]:
        self.cfg.validate()
        version = self.api_version()
        auth = self._auth()
        hosts = self._rpc(
            "host.get",
            {
                "output": ["hostid", "host", "name", "status"],
                "limit": 1,
                "sortfield": "hostid",
            },
            auth=auth,
        )
        count = len(hosts) if isinstance(hosts, list) else 0
        return {"version": version, "sample_hosts": count}

    def host_get(self, *, keyword: str = "", limit: int = 20, include_disabled: bool = True) -> list[dict[str, Any]]:
        auth = self._auth()
        params: dict[str, Any] = {
            "output": ["hostid", "host", "name", "status"],
            "limit": max(1, min(int(limit), 200)),
            "sortfield": "host",
        }
        if keyword:
            params["search"] = {"host": keyword, "name": keyword}
            params["searchByAny"] = True
            params["searchWildcardsEnabled"] = True
        if not include_disabled:
            params["filter"] = {"status": 0}
        result = self._rpc("host.get", params, auth=auth)
        return [x for x in (result or []) if isinstance(x, dict)]

    def item_get(
        self,
        *,
        hostids: list[str],
        key_filter: str = "",
        name_filter: str = "",
        value_type: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        auth = self._auth()
        params: dict[str, Any] = {
            "output": ["itemid", "hostid", "name", "key_", "value_type", "status", "state"],
            "hostids": [str(x).strip() for x in hostids if str(x).strip()],
            "limit": max(1, min(int(limit), 500)),
            "sortfield": "name",
        }
        if value_type is not None:
            params["filter"] = {"value_type": int(value_type)}
        if key_filter or name_filter:
            search: dict[str, str] = {}
            if key_filter:
                search["key_"] = key_filter
            if name_filter:
                search["name"] = name_filter
            params["search"] = search
            params["searchByAny"] = True
            params["searchWildcardsEnabled"] = True
        result = self._rpc("item.get", params, auth=auth)
        return [x for x in (result or []) if isinstance(x, dict)]

    def history_get(
        self,
        *,
        itemids: list[str],
        time_from: int,
        time_till: int,
        value_type: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        auth = self._auth()
        params = {
            "output": "extend",
            "history": int(value_type),
            "itemids": [str(x).strip() for x in itemids if str(x).strip()],
            "time_from": int(time_from),
            "time_till": int(time_till),
            "sortfield": "clock",
            "sortorder": "ASC",
            "limit": max(1, min(int(limit), 5000)),
        }
        result = self._rpc("history.get", params, auth=auth)
        return [x for x in (result or []) if isinstance(x, dict)]

    def trend_get(
        self,
        *,
        itemids: list[str],
        time_from: int,
        time_till: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        auth = self._auth()
        params = {
            "output": "extend",
            "itemids": [str(x).strip() for x in itemids if str(x).strip()],
            "time_from": int(time_from),
            "time_till": int(time_till),
            "sortfield": "clock",
            "sortorder": "ASC",
            "limit": max(1, min(int(limit), 5000)),
        }
        result = self._rpc("trend.get", params, auth=auth)
        return [x for x in (result or []) if isinstance(x, dict)]
