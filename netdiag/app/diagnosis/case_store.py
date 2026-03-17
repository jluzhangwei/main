from __future__ import annotations

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
    s = str(v or "").strip().lower()
    if not s:
        return default
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_/-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    return {w.strip() for w in words if w.strip()}


def _to_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    text = str(v or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;|]+", text)
    return [p.strip() for p in parts if p.strip()]


class NetdiagCaseStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "cases": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("cases", [])
                return payload
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_cases(self, enabled_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = [x for x in self._load().get("cases", []) if isinstance(x, dict)]
        if enabled_only:
            rows = [x for x in rows if bool(x.get("enabled", True))]
        rows.sort(
            key=lambda x: (
                0 if bool(x.get("enabled", True)) else 1,
                -_safe_int(x.get("priority"), 100),
                str(x.get("updated_at") or ""),
            )
        )
        return rows[: max(1, min(int(limit), 5000))]

    def upsert_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = payload or {}
        title = str(row.get("title") or "").strip()
        root_cause = str(row.get("root_cause") or "").strip()
        if not title:
            raise ValueError("title is required")
        if not root_cause:
            raise ValueError("root_cause is required")
        cid = str(row.get("case_id") or "").strip() or uuid.uuid4().hex[:14]
        now = _now_iso()
        out = {
            "case_id": cid,
            "title": title,
            "question": str(row.get("question") or "").strip(),
            "focus_goals": _to_list(row.get("focus_goals")),
            "vendor_profiles": _to_list(row.get("vendor_profiles")),
            "domains": _to_list(row.get("domains")),
            "root_cause": root_cause,
            "resolution_steps": _to_list(row.get("resolution_steps")),
            "verify_commands": _to_list(row.get("verify_commands")),
            "evidence_signals": _to_list(row.get("evidence_signals")),
            "source_session_id": str(row.get("source_session_id") or "").strip(),
            "source_round_no": _safe_int(row.get("source_round_no"), 0),
            "confidence": round(max(0.0, min(1.0, _safe_float(row.get("confidence"), 0.7))), 4),
            "priority": _safe_int(row.get("priority"), 100),
            "tags": _to_list(row.get("tags")),
            "enabled": _safe_bool(row.get("enabled"), True),
            "updated_at": now,
        }
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("cases", []) if isinstance(x, dict)]
            replaced = False
            for i, old in enumerate(rows):
                if str(old.get("case_id") or "") == cid:
                    out["created_at"] = str(old.get("created_at") or now)
                    rows[i] = {**old, **out}
                    replaced = True
                    break
            if not replaced:
                out["created_at"] = now
                rows.append(out)
            data["cases"] = rows
            self._save(data)
        return out

    def delete_case(self, case_id: str) -> bool:
        cid = str(case_id or "").strip()
        if not cid:
            return False
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("cases", []) if isinstance(x, dict)]
            nxt = [x for x in rows if str(x.get("case_id") or "") != cid]
            changed = len(nxt) != len(rows)
            if changed:
                data["cases"] = nxt
                self._save(data)
            return changed

    def set_case_enabled(self, case_id: str, enabled: bool) -> dict[str, Any] | None:
        cid = str(case_id or "").strip()
        if not cid:
            return None
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("cases", []) if isinstance(x, dict)]
            for idx, row in enumerate(rows):
                if str(row.get("case_id") or "") != cid:
                    continue
                item = dict(row)
                item["enabled"] = bool(enabled)
                item["updated_at"] = _now_iso()
                rows[idx] = item
                data["cases"] = rows
                self._save(data)
                return item
            return None

    def search(
        self,
        *,
        query_text: str,
        profiles: list[str] | None = None,
        domains: list[str] | None = None,
        evidence_text: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        q_tokens = _tokenize(query_text)
        pset = {str(x).strip().lower() for x in (profiles or []) if str(x).strip()}
        dset = {str(x).strip().lower() for x in (domains or []) if str(x).strip()}
        e_low = str(evidence_text or "").lower()
        hits: list[dict[str, Any]] = []
        for row in self.list_cases(enabled_only=True, limit=5000):
            score = 0.0
            reasons: list[str] = []
            row_profiles = {str(x).strip().lower() for x in row.get("vendor_profiles", []) if str(x).strip()}
            row_domains = {str(x).strip().lower() for x in row.get("domains", []) if str(x).strip()}
            if pset and row_profiles:
                inter = sorted(pset.intersection(row_profiles))
                if inter:
                    score += 2.2
                    reasons.append("profiles=" + ",".join(inter))
            if dset and row_domains:
                inter = sorted(dset.intersection(row_domains))
                if inter:
                    score += 1.6
                    reasons.append("domains=" + ",".join(inter))

            base_text = " ".join(
                [
                    str(row.get("title") or ""),
                    str(row.get("question") or ""),
                    str(row.get("root_cause") or ""),
                    " ".join(str(x) for x in row.get("focus_goals", [])),
                    " ".join(str(x) for x in row.get("tags", [])),
                    " ".join(str(x) for x in row.get("evidence_signals", [])),
                ]
            )
            overlap = sorted(q_tokens.intersection(_tokenize(base_text)))
            if overlap:
                score += min(3.0, 0.40 * len(overlap))
                reasons.append("query_terms=" + ",".join(overlap[:6]))

            signal_hits = [s for s in row.get("evidence_signals", []) if str(s).strip() and str(s).lower() in e_low]
            if signal_hits:
                score += min(2.0, 0.5 * len(signal_hits))
                reasons.append("signal_hits=" + ",".join(signal_hits[:4]))

            score += 0.01 * _safe_int(row.get("priority"), 100)
            if score <= 0.0:
                continue
            hit = dict(row)
            hit["score"] = round(score, 4)
            hit["match_reasons"] = reasons
            hit["explain"] = "; ".join(reasons[:4]) if reasons else "contextual weak match"
            hits.append(hit)
        hits.sort(key=lambda x: (-_safe_float(x.get("score"), 0.0), -_safe_int(x.get("priority"), 0), str(x.get("case_id") or "")))
        return hits[: max(1, min(int(limit), 50))]
