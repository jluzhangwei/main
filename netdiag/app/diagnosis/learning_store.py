from __future__ import annotations

import csv
import io
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def _safe_bool(v: Any, default: bool = True) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    if not s:
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _profile_vendor(profile: str) -> str:
    p = str(profile or "unknown").strip().lower()
    if "_" in p:
        return p.split("_", 1)[0]
    return p


class NetdiagLearningStore:
    def __init__(self, path: str, max_events: int = 6000) -> None:
        self.path = Path(path)
        self.max_events = max(500, int(max_events))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "library": [], "events": []}

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("library", [])
                data.setdefault("events", [])
                return data
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_library(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            if enabled_only:
                items = [x for x in items if bool(x.get("enabled", True))]
            return sorted(
                items,
                key=lambda x: (
                    0 if bool(x.get("enabled", True)) else 1,
                    -_safe_int(x.get("priority"), 100),
                    -_safe_float(x.get("score"), 0.0),
                    str(x.get("updated_at") or ""),
                ),
            )

    def upsert_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            rid = str(payload.get("rule_id") or "").strip() or uuid.uuid4().hex[:12]

            command = str(payload.get("command") or "").strip()
            if not command:
                raise ValueError("command is required")
            intent = str(payload.get("intent") or "").strip()
            if not intent:
                raise ValueError("intent is required")

            now = _now_iso()
            row = {
                "rule_id": rid,
                "vendor": str(payload.get("vendor") or "").strip().lower(),
                "os_family": str(payload.get("os_family") or "").strip().lower(),
                "intent": intent,
                "command": command,
                "min_version": str(payload.get("min_version") or "").strip(),
                "max_version": str(payload.get("max_version") or "").strip(),
                "source": str(payload.get("source") or "custom").strip(),
                "enabled": _safe_bool(payload.get("enabled"), True),
                "priority": _safe_int(payload.get("priority"), 100),
                "success": _safe_int(payload.get("success"), 0),
                "fail": _safe_int(payload.get("fail"), 0),
                "score": _safe_float(payload.get("score"), 0.0),
                "last_result": str(payload.get("last_result") or ""),
                "last_error_type": str(payload.get("last_error_type") or ""),
                "updated_at": now,
            }

            replaced = False
            for i, old in enumerate(items):
                if str(old.get("rule_id") or "") == rid:
                    row["created_at"] = str(old.get("created_at") or now)
                    items[i] = {**old, **row}
                    replaced = True
                    break
            if not replaced:
                row["created_at"] = now
                items.append(row)

            data["library"] = items
            self._save(data)
            return row

    def delete_rule(self, rule_id: str) -> bool:
        rid = str(rule_id or "").strip()
        if not rid:
            return False
        with self._lock:
            data = self._load()
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            nxt = [x for x in items if str(x.get("rule_id") or "") != rid]
            changed = len(nxt) != len(items)
            if changed:
                data["library"] = nxt
                self._save(data)
            return changed

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> dict[str, Any] | None:
        rid = str(rule_id or "").strip()
        if not rid:
            return None
        with self._lock:
            data = self._load()
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            for i, row in enumerate(items):
                if str(row.get("rule_id") or "") != rid:
                    continue
                row = dict(row)
                row["enabled"] = bool(enabled)
                row["updated_at"] = _now_iso()
                items[i] = row
                data["library"] = items
                self._save(data)
                return row
            return None

    def import_rows(self, rows: list[dict[str, Any]], source: str = "official", replace_existing: bool = False) -> dict[str, int]:
        with self._lock:
            data = self._load()
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            imported = 0
            updated = 0

            if replace_existing:
                items = []

            index: dict[tuple[str, str, str, str], int] = {}
            for i, row in enumerate(items):
                key = (
                    str(row.get("vendor") or "").strip().lower(),
                    str(row.get("os_family") or "").strip().lower(),
                    str(row.get("intent") or "").strip(),
                    str(row.get("command") or "").strip().lower(),
                )
                index[key] = i

            now = _now_iso()
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                command = str(raw.get("command") or "").strip()
                intent = str(raw.get("intent") or "").strip()
                if not command or not intent:
                    continue
                row = {
                    "rule_id": str(raw.get("rule_id") or uuid.uuid4().hex[:12]).strip(),
                    "vendor": str(raw.get("vendor") or "").strip().lower(),
                    "os_family": str(raw.get("os_family") or "").strip().lower(),
                    "intent": intent,
                    "command": command,
                    "min_version": str(raw.get("min_version") or "").strip(),
                    "max_version": str(raw.get("max_version") or "").strip(),
                    "source": str(raw.get("source") or source).strip(),
                    "enabled": _safe_bool(raw.get("enabled"), True),
                    "priority": _safe_int(raw.get("priority"), 120 if source == "official" else 100),
                    "success": _safe_int(raw.get("success"), 0),
                    "fail": _safe_int(raw.get("fail"), 0),
                    "score": _safe_float(raw.get("score"), 0.0),
                    "last_result": str(raw.get("last_result") or ""),
                    "last_error_type": str(raw.get("last_error_type") or ""),
                    "updated_at": now,
                    "created_at": str(raw.get("created_at") or now),
                }
                key = (row["vendor"], row["os_family"], row["intent"], row["command"].lower())
                if key in index:
                    items[index[key]] = {**items[index[key]], **row}
                    updated += 1
                else:
                    index[key] = len(items)
                    items.append(row)
                    imported += 1

            data["library"] = items
            self._save(data)
            return {"imported": imported, "updated": updated, "total": len(items)}

    def parse_csv_text(self, csv_text: str) -> list[dict[str, Any]]:
        content = str(csv_text or "").strip()
        if not content:
            return []
        reader = csv.DictReader(io.StringIO(content))
        rows: list[dict[str, Any]] = []
        for row in reader:
            rows.append(dict(row))
        return rows

    def resolve_command(self, intent: str, profile: str, version: str = "") -> str | None:
        i = str(intent or "").strip()
        p = str(profile or "unknown").strip().lower()
        v = _profile_vendor(p)
        if not i:
            return None
        _ = version  # reserved for future version-range filtering

        candidates: list[dict[str, Any]] = []
        for row in self.list_library(enabled_only=True):
            if str(row.get("intent") or "").strip() != i:
                continue
            row_os = str(row.get("os_family") or "").strip().lower()
            row_vendor = str(row.get("vendor") or "").strip().lower()
            if row_os and row_os != p:
                continue
            if not row_os and row_vendor and row_vendor != v:
                continue
            candidates.append(row)

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: (
                2 if str(x.get("os_family") or "").strip().lower() == p else 1,
                _safe_int(x.get("priority"), 100),
                _safe_float(x.get("score"), 0.0),
                _safe_int(x.get("success"), 0) - _safe_int(x.get("fail"), 0),
            ),
            reverse=True,
        )
        cmd = str(candidates[0].get("command") or "").strip()
        return cmd or None

    def classify_result(self, status: str, output_text: str, error_text: str = "") -> tuple[str, str]:
        st = str(status or "").strip().lower()
        text = str(output_text or "")
        err = str(error_text or "")
        low = (text + "\n" + err).lower()

        if st == "error_output":
            if "permission" in low or "denied" in low or "authorization" in low:
                return ("error_output", "privilege")
            if "timeout" in low:
                return ("error_output", "timeout")
            return ("error_output", "syntax")
        if st == "empty_output":
            return ("empty_output", "empty")

        if st != "success":
            if "timeout" in low:
                return ("failed", "timeout")
            if "auth" in low or "permission" in low or "denied" in low:
                return ("failed", "privilege")
            return ("failed", "exec_failed")

        if re.search(r"(unrecognized command|invalid command|incomplete command|wrong parameter)", low):
            return ("error_output", "syntax")
        if re.search(r"(permission denied|access denied|authorization failed)", low):
            return ("error_output", "privilege")

        stripped = text.strip()
        if len(stripped) < 4:
            return ("empty_output", "empty")
        return ("valid_output", "ok")

    def record_execution_event(
        self,
        *,
        session_id: str,
        round_no: int,
        device_id: str,
        profile: str,
        intent: str,
        command: str,
        status: str,
        output_text: str,
        error_text: str = "",
    ) -> dict[str, Any]:
        signal, err_type = self.classify_result(status=status, output_text=output_text, error_text=error_text)
        event = {
            "ts": _now_iso(),
            "session_id": str(session_id or ""),
            "round_no": int(round_no),
            "device_id": str(device_id or ""),
            "profile": str(profile or "unknown").strip().lower(),
            "intent": str(intent or "").strip(),
            "command": str(command or "").strip(),
            "status": str(status or ""),
            "signal": signal,
            "error_type": err_type,
        }

        with self._lock:
            data = self._load()
            events = [x for x in data.get("events", []) if isinstance(x, dict)]
            events.append(event)
            if len(events) > self.max_events:
                events = events[-self.max_events :]
            data["events"] = events

            # Online learning: update matching library rule stats if exists.
            items = [x for x in data.get("library", []) if isinstance(x, dict)]
            cmd_norm = event["command"].lower()
            prof = event["profile"]
            vend = _profile_vendor(prof)
            for i, row in enumerate(items):
                if str(row.get("intent") or "").strip() != event["intent"]:
                    continue
                if str(row.get("command") or "").strip().lower() != cmd_norm:
                    continue
                row_os = str(row.get("os_family") or "").strip().lower()
                row_vendor = str(row.get("vendor") or "").strip().lower()
                if row_os and row_os != prof:
                    continue
                if not row_os and row_vendor and row_vendor != vend:
                    continue
                row = dict(row)
                if signal == "valid_output":
                    row["success"] = _safe_int(row.get("success"), 0) + 1
                    row["last_result"] = "success"
                else:
                    row["fail"] = _safe_int(row.get("fail"), 0) + 1
                    row["last_result"] = "fail"
                    row["last_error_type"] = err_type
                s = _safe_int(row.get("success"), 0)
                f = _safe_int(row.get("fail"), 0)
                row["score"] = round((s + 1) / (s + f + 2), 4)
                row["updated_at"] = _now_iso()
                items[i] = row
                break

            data["library"] = items
            self._save(data)
        return event

    def summary(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            events = [x for x in data.get("events", []) if isinstance(x, dict)]
            total = len(events)
            valid = sum(1 for e in events if str(e.get("signal") or "") == "valid_output")
            failed = total - valid

            recent = events[-50:]
            prev = events[-100:-50] if len(events) >= 100 else []
            recent_rate = (sum(1 for e in recent if str(e.get("signal") or "") == "valid_output") / len(recent)) if recent else 0.0
            prev_rate = (sum(1 for e in prev if str(e.get("signal") or "") == "valid_output") / len(prev)) if prev else 0.0

            by_profile: dict[str, dict[str, Any]] = {}
            by_intent: dict[str, dict[str, Any]] = {}
            for e in events:
                p = str(e.get("profile") or "unknown")
                i = str(e.get("intent") or "unknown")
                for bucket, key in ((by_profile, p), (by_intent, i)):
                    row = bucket.setdefault(key, {"key": key, "total": 0, "valid": 0})
                    row["total"] += 1
                    if str(e.get("signal") or "") == "valid_output":
                        row["valid"] += 1

            def _finalize(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
                out = []
                for _, r in rows.items():
                    t = int(r.get("total") or 0)
                    v = int(r.get("valid") or 0)
                    out.append({**r, "rate": round((v / t), 4) if t > 0 else 0.0})
                out.sort(key=lambda x: (-x["total"], -x["rate"], str(x["key"])))
                return out[:50]

            library = [x for x in data.get("library", []) if isinstance(x, dict)]
            enabled_rules = sum(1 for r in library if bool(r.get("enabled", True)))
            official_rules = sum(1 for r in library if str(r.get("source") or "").lower() == "official")

            return {
                "events_total": total,
                "valid_outputs": valid,
                "non_valid_outputs": failed,
                "overall_rate": round((valid / total), 4) if total > 0 else 0.0,
                "recent_rate_50": round(recent_rate, 4),
                "prev_rate_50": round(prev_rate, 4),
                "rate_delta_50": round(recent_rate - prev_rate, 4),
                "by_profile": _finalize(by_profile),
                "by_intent": _finalize(by_intent),
                "library_total": len(library),
                "library_enabled": enabled_rules,
                "library_official": official_rules,
            }
