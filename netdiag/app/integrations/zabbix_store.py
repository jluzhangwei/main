from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


def _safe_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if not s:
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_int(v: Any, default: int = 30) -> int:
    try:
        return int(v)
    except Exception:
        return default


class NetdiagZabbixStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {
            "base_url": "",
            "username": "",
            "password": "",
            "api_token": "",
            "verify_ssl": True,
            "ca_bundle": "",
            "request_timeout_sec": 30,
        }

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                out = self._default()
                out.update(payload)
                out["verify_ssl"] = _safe_bool(out.get("verify_ssl"), True)
                out["request_timeout_sec"] = max(5, min(120, _safe_int(out.get("request_timeout_sec"), 30)))
                return out
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, masked: bool = False) -> dict[str, Any]:
        with self._lock:
            cfg = self._load()
        if not masked:
            return cfg
        pwd = str(cfg.get("password") or "")
        tok = str(cfg.get("api_token") or "")
        masked_cfg = dict(cfg)
        masked_cfg["password"] = "***" if pwd else ""
        masked_cfg["api_token"] = "***" if tok else ""
        masked_cfg["has_password"] = bool(pwd)
        masked_cfg["has_api_token"] = bool(tok)
        return masked_cfg

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload or {}
        with self._lock:
            cfg = self._load()
            if "base_url" in patch:
                cfg["base_url"] = str(patch.get("base_url") or "").strip()
            if "username" in patch:
                cfg["username"] = str(patch.get("username") or "").strip()
            if "password" in patch:
                cfg["password"] = str(patch.get("password") or "")
            if "api_token" in patch:
                cfg["api_token"] = str(patch.get("api_token") or "").strip()
            if "verify_ssl" in patch:
                cfg["verify_ssl"] = _safe_bool(patch.get("verify_ssl"), True)
            if "ca_bundle" in patch:
                cfg["ca_bundle"] = str(patch.get("ca_bundle") or "").strip()
            if "request_timeout_sec" in patch:
                cfg["request_timeout_sec"] = max(5, min(120, _safe_int(patch.get("request_timeout_sec"), 30)))
            self._save(cfg)
        return self.get(masked=True)
