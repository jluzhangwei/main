from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


class NetdiagConnectionStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {
            "smc_jump_mode": "smc",
            "smc_jump_host": "",
            "smc_jump_port": 22,
            "smc_command": "smc server toc {jump_host}",
            "zabbix_default_host": "",
            "zabbix_default_item_key": "",
            "zabbix_default_timezone": "Asia/Singapore",
            "zabbix_default_limit": 600,
            "zabbix_default_use_trend": "auto",  # auto / true / false
        }

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        out = self._default()
        out.update(raw or {})
        mode = str(out.get("smc_jump_mode") or "smc").strip().lower()
        out["smc_jump_mode"] = "smc" if mode == "smc" else "direct"
        out["smc_jump_host"] = str(out.get("smc_jump_host") or "").strip()
        out["smc_jump_port"] = max(1, min(65535, _safe_int(out.get("smc_jump_port"), 22)))
        out["smc_command"] = str(out.get("smc_command") or "").strip() or "smc server toc {jump_host}"
        out["zabbix_default_host"] = str(out.get("zabbix_default_host") or "").strip()
        out["zabbix_default_item_key"] = str(out.get("zabbix_default_item_key") or "").strip()
        out["zabbix_default_timezone"] = str(out.get("zabbix_default_timezone") or "Asia/Singapore").strip() or "Asia/Singapore"
        out["zabbix_default_limit"] = max(10, min(5000, _safe_int(out.get("zabbix_default_limit"), 600)))
        use_trend = str(out.get("zabbix_default_use_trend") or "auto").strip().lower()
        out["zabbix_default_use_trend"] = use_trend if use_trend in {"auto", "true", "false"} else "auto"
        return out

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return self._normalize(payload)
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        patch = payload or {}
        with self._lock:
            cur = self._load()
            for key in (
                "smc_jump_mode",
                "smc_jump_host",
                "smc_jump_port",
                "smc_command",
                "zabbix_default_host",
                "zabbix_default_item_key",
                "zabbix_default_timezone",
                "zabbix_default_limit",
                "zabbix_default_use_trend",
            ):
                if key in patch:
                    cur[key] = patch.get(key)
            cur = self._normalize(cur)
            self._save(cur)
            return cur

