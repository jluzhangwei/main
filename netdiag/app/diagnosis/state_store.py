from __future__ import annotations

import json
import statistics
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(float(x) for x in values)
    if len(arr) == 1:
        return arr[0]
    pp = max(0.0, min(100.0, float(p)))
    k = (len(arr) - 1) * (pp / 100.0)
    low = int(k)
    high = min(low + 1, len(arr) - 1)
    if low == high:
        return arr[low]
    ratio = k - low
    return arr[low] * (1 - ratio) + arr[high] * ratio


class NetdiagStateStore:
    def __init__(self, path: str, max_points: int = 200000) -> None:
        self.path = Path(path)
        self.max_points = max(1000, int(max_points))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "points": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("points", [])
                return payload
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_points(self, points: list[dict[str, Any]]) -> dict[str, int]:
        rows = [x for x in (points or []) if isinstance(x, dict)]
        if not rows:
            return {"added": 0, "total": len(self.list_points(limit=1))}

        normalized: list[dict[str, Any]] = []
        for raw in rows:
            ts = _safe_int(raw.get("ts"), 0)
            if ts <= 0:
                ts = int(datetime.now(timezone.utc).timestamp())
            did = str(raw.get("device_id") or "").strip() or "*"
            domain = str(raw.get("domain") or "").strip().lower() or "global"
            key = str(raw.get("key") or "").strip().lower() or "value"
            value = _safe_float(raw.get("value"), 0.0)
            normalized.append(
                {
                    "point_id": str(raw.get("point_id") or uuid.uuid4().hex[:16]),
                    "ts": ts,
                    "time": str(raw.get("time") or _iso_from_ts(ts)),
                    "device_id": did,
                    "session_id": str(raw.get("session_id") or "").strip(),
                    "round_no": _safe_int(raw.get("round_no"), 0),
                    "domain": domain,
                    "key": key,
                    "value": value,
                    "unit": str(raw.get("unit") or "").strip(),
                    "source": str(raw.get("source") or "netdiag").strip(),
                    "tags": [str(x).strip() for x in (raw.get("tags") or []) if str(x).strip()],
                    "updated_at": _now_iso(),
                }
            )

        with self._lock:
            data = self._load()
            points_data = [x for x in data.get("points", []) if isinstance(x, dict)]
            points_data.extend(normalized)
            if len(points_data) > self.max_points:
                points_data = sorted(points_data, key=lambda x: int(x.get("ts") or 0))[-self.max_points :]
            data["points"] = points_data
            self._save(data)
            return {"added": len(normalized), "total": len(points_data)}

    def list_points(self, *, limit: int = 200, newest_first: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            points = [x for x in self._load().get("points", []) if isinstance(x, dict)]
        points.sort(key=lambda x: int(x.get("ts") or 0), reverse=bool(newest_first))
        return points[: max(1, min(int(limit), 5000))]

    def query_points(
        self,
        *,
        device_id: str = "",
        domain: str = "",
        key: str = "",
        ts_from: int | None = None,
        ts_till: int | None = None,
        limit: int = 500,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        did = str(device_id or "").strip()
        dom = str(domain or "").strip().lower()
        kk = str(key or "").strip().lower()
        lo = _safe_int(ts_from, 0) if ts_from is not None else None
        hi = _safe_int(ts_till, 0) if ts_till is not None else None

        with self._lock:
            items = [x for x in self._load().get("points", []) if isinstance(x, dict)]
        out: list[dict[str, Any]] = []
        for row in items:
            if did and str(row.get("device_id") or "") != did:
                continue
            if dom and str(row.get("domain") or "").lower() != dom:
                continue
            if kk and str(row.get("key") or "").lower() != kk:
                continue
            ts = _safe_int(row.get("ts"), 0)
            if lo is not None and ts < lo:
                continue
            if hi is not None and ts > hi:
                continue
            out.append(row)
        out.sort(key=lambda x: _safe_int(x.get("ts"), 0), reverse=bool(newest_first))
        return out[: max(1, min(int(limit), 10000))]

    def baseline_compare(
        self,
        *,
        device_id: str,
        domain: str,
        key: str,
        current_value: float,
        history_limit: int = 120,
    ) -> dict[str, Any]:
        rows = self.query_points(
            device_id=device_id,
            domain=domain,
            key=key,
            limit=max(1, min(int(history_limit), 5000)),
            newest_first=True,
        )
        values = [float(x.get("value") or 0.0) for x in rows]
        if not values:
            return {
                "device_id": device_id,
                "domain": domain,
                "key": key,
                "current": round(float(current_value), 6),
                "history_count": 0,
                "baseline_mean": 0.0,
                "baseline_median": 0.0,
                "baseline_p95": 0.0,
                "delta_vs_median": round(float(current_value), 6),
                "delta_ratio_vs_median": None,
                "is_significant": False,
            }
        med = statistics.median(values)
        mean = statistics.fmean(values)
        p95 = _percentile(values, 95)
        cur = float(current_value)
        delta = cur - med
        ratio = (delta / abs(med)) if abs(med) > 1e-9 else None
        # Significance threshold: absolute deviation >= 20% of p95 band or relative >= 50%.
        sig_abs = abs(delta) >= max(0.05, abs(p95) * 0.2)
        sig_ratio = (abs(ratio) >= 0.5) if ratio is not None else (abs(delta) >= 1.0)
        return {
            "device_id": device_id,
            "domain": domain,
            "key": key,
            "current": round(cur, 6),
            "history_count": len(values),
            "baseline_mean": round(mean, 6),
            "baseline_median": round(med, 6),
            "baseline_p95": round(p95, 6),
            "delta_vs_median": round(delta, 6),
            "delta_ratio_vs_median": (round(ratio, 6) if ratio is not None else None),
            "is_significant": bool(sig_abs or sig_ratio),
        }
