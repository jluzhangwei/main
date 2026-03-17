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
    p = str(profile or "").strip().lower()
    if "_" in p:
        return p.split("_", 1)[0]
    return p


def _split_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    text = str(v or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;|]+", text)
    return [p.strip() for p in parts if p.strip()]


def _version_tokens(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in nums[:6])


def _version_in_range(version: str, min_v: str, max_v: str) -> bool:
    cur = _version_tokens(version)
    lo = _version_tokens(min_v)
    hi = _version_tokens(max_v)
    if cur and lo and cur < lo:
        return False
    if cur and hi and cur > hi:
        return False
    return True


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_/-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    return {w.strip() for w in words if w.strip()}


def _format_version_scope(min_v: str, max_v: str) -> str:
    lo = str(min_v or "").strip()
    hi = str(max_v or "").strip()
    if lo and hi:
        return f"[{lo} ~ {hi}]"
    if lo:
        return f"[>= {lo}]"
    if hi:
        return f"[<= {hi}]"
    return "[any]"


class NetdiagKnownIssueStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "issues": []}

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("issues", [])
                return data
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_issues(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            items = [x for x in self._load().get("issues", []) if isinstance(x, dict)]
            if enabled_only:
                items = [x for x in items if bool(x.get("enabled", True))]
            items.sort(
                key=lambda x: (
                    0 if bool(x.get("enabled", True)) else 1,
                    -_safe_int(x.get("priority"), 100),
                    str(x.get("updated_at") or ""),
                )
            )
            return items

    def upsert_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            items = [x for x in data.get("issues", []) if isinstance(x, dict)]
            iid = str(payload.get("issue_id") or "").strip() or uuid.uuid4().hex[:12]
            title = str(payload.get("title") or "").strip()
            root_cause = str(payload.get("root_cause") or "").strip()
            if not title:
                raise ValueError("title is required")
            if not root_cause:
                raise ValueError("root_cause is required")

            now = _now_iso()
            row = {
                "issue_id": iid,
                "title": title,
                "vendor": str(payload.get("vendor") or "").strip().lower(),
                "os_family": str(payload.get("os_family") or "").strip().lower(),
                "min_version": str(payload.get("min_version") or "").strip(),
                "max_version": str(payload.get("max_version") or "").strip(),
                "symptoms": _split_list(payload.get("symptoms")),
                "evidence_patterns": _split_list(payload.get("evidence_patterns")),
                "diag_intents": _split_list(payload.get("diag_intents")),
                "diagnostic_commands": _split_list(payload.get("diagnostic_commands")),
                "root_cause": root_cause,
                "fix_actions": str(payload.get("fix_actions") or "").strip(),
                "verify_commands": _split_list(payload.get("verify_commands")),
                "severity": str(payload.get("severity") or "medium").strip().lower(),
                "domain": str(payload.get("domain") or "").strip().lower(),
                "source": str(payload.get("source") or "noc").strip(),
                "priority": _safe_int(payload.get("priority"), 100),
                "enabled": _safe_bool(payload.get("enabled"), True),
                "updated_at": now,
            }

            replaced = False
            for i, old in enumerate(items):
                if str(old.get("issue_id") or "") == iid:
                    row["created_at"] = str(old.get("created_at") or now)
                    items[i] = {**old, **row}
                    replaced = True
                    break
            if not replaced:
                row["created_at"] = now
                items.append(row)

            data["issues"] = items
            self._save(data)
            return row

    def delete_issue(self, issue_id: str) -> bool:
        iid = str(issue_id or "").strip()
        if not iid:
            return False
        with self._lock:
            data = self._load()
            items = [x for x in data.get("issues", []) if isinstance(x, dict)]
            nxt = [x for x in items if str(x.get("issue_id") or "") != iid]
            changed = len(nxt) != len(items)
            if changed:
                data["issues"] = nxt
                self._save(data)
            return changed

    def set_issue_enabled(self, issue_id: str, enabled: bool) -> dict[str, Any] | None:
        iid = str(issue_id or "").strip()
        if not iid:
            return None
        with self._lock:
            data = self._load()
            items = [x for x in data.get("issues", []) if isinstance(x, dict)]
            for idx, row in enumerate(items):
                if str(row.get("issue_id") or "") != iid:
                    continue
                row = dict(row)
                row["enabled"] = bool(enabled)
                row["updated_at"] = _now_iso()
                items[idx] = row
                data["issues"] = items
                self._save(data)
                return row
            return None

    def parse_csv_text(self, csv_text: str) -> list[dict[str, Any]]:
        text = str(csv_text or "").strip()
        if not text:
            return []
        reader = csv.DictReader(io.StringIO(text))
        return [dict(r) for r in reader]

    def import_rows(self, rows: list[dict[str, Any]], source: str = "noc", replace_existing: bool = False) -> dict[str, int]:
        with self._lock:
            data = self._load()
            items = [] if replace_existing else [x for x in data.get("issues", []) if isinstance(x, dict)]
            imported = 0
            updated = 0

            index: dict[tuple[str, str], int] = {}
            for i, row in enumerate(items):
                key = (str(row.get("issue_id") or "").strip(), str(row.get("title") or "").strip().lower())
                index[key] = i

            now = _now_iso()
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                iid = str(raw.get("issue_id") or "").strip() or uuid.uuid4().hex[:12]
                title = str(raw.get("title") or "").strip()
                root_cause = str(raw.get("root_cause") or "").strip()
                if not title or not root_cause:
                    continue
                row = {
                    "issue_id": iid,
                    "title": title,
                    "vendor": str(raw.get("vendor") or "").strip().lower(),
                    "os_family": str(raw.get("os_family") or "").strip().lower(),
                    "min_version": str(raw.get("min_version") or "").strip(),
                    "max_version": str(raw.get("max_version") or "").strip(),
                    "symptoms": _split_list(raw.get("symptoms")),
                    "evidence_patterns": _split_list(raw.get("evidence_patterns")),
                    "diag_intents": _split_list(raw.get("diag_intents")),
                    "diagnostic_commands": _split_list(raw.get("diagnostic_commands")),
                    "root_cause": root_cause,
                    "fix_actions": str(raw.get("fix_actions") or "").strip(),
                    "verify_commands": _split_list(raw.get("verify_commands")),
                    "severity": str(raw.get("severity") or "medium").strip().lower(),
                    "domain": str(raw.get("domain") or "").strip().lower(),
                    "source": str(raw.get("source") or source).strip(),
                    "priority": _safe_int(raw.get("priority"), 100),
                    "enabled": _safe_bool(raw.get("enabled"), True),
                    "updated_at": now,
                    "created_at": str(raw.get("created_at") or now),
                }
                key = (row["issue_id"], row["title"].lower())
                if key in index:
                    items[index[key]] = {**items[index[key]], **row}
                    updated += 1
                else:
                    index[key] = len(items)
                    items.append(row)
                    imported += 1

            data["issues"] = items
            self._save(data)
            return {"imported": imported, "updated": updated, "total": len(items)}

    def search(
        self,
        *,
        profile: str,
        version: str = "",
        query_text: str = "",
        evidence_text: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        p = str(profile or "").strip().lower()
        vendor = _profile_vendor(p)
        q_tokens = _tokenize(query_text)
        e_low = str(evidence_text or "").lower()

        hits: list[dict[str, Any]] = []
        for row in self.list_issues(enabled_only=True):
            osf = str(row.get("os_family") or "").strip().lower()
            vend = str(row.get("vendor") or "").strip().lower()
            if osf and osf != p:
                continue
            if not osf and vend and vend != vendor:
                continue
            if not _version_in_range(version, str(row.get("min_version") or ""), str(row.get("max_version") or "")):
                continue

            score = 0.0
            if osf and osf == p:
                score += 3.0
            elif vend and vend == vendor:
                score += 1.5
            score += 0.01 * _safe_float(row.get("priority"), 100.0)

            symptoms = [str(x).lower() for x in row.get("symptoms", []) if str(x).strip()]
            symptom_tokens = _tokenize(" ".join(symptoms))
            overlap = sorted(q_tokens.intersection(symptom_tokens))
            score += min(3.0, 0.45 * len(overlap))

            patterns = [str(x).strip() for x in row.get("evidence_patterns", []) if str(x).strip()]
            matched_patterns: list[str] = []
            for ptn in patterns:
                try:
                    if re.search(ptn, e_low, flags=re.I):
                        matched_patterns.append(ptn)
                except re.error:
                    if ptn.lower() in e_low:
                        matched_patterns.append(ptn)
            score += min(4.0, 0.9 * len(matched_patterns))

            if score <= 0.0:
                continue
            reasons: list[str] = []
            if osf and osf == p:
                reasons.append(f"profile_exact={p}")
            elif vend and vend == vendor:
                reasons.append(f"vendor_match={vend}")
            if str(version or "").strip():
                reasons.append(
                    f"version_scope={_format_version_scope(str(row.get('min_version') or ''), str(row.get('max_version') or ''))}, observed={version}"
                )
            if overlap:
                reasons.append("symptom_terms=" + ",".join(overlap[:6]))
            if matched_patterns:
                reasons.append("evidence_patterns=" + " | ".join(matched_patterns[:4]))
            diag_intents = [str(x).strip() for x in row.get("diag_intents", []) if str(x).strip()]
            if diag_intents:
                reasons.append("diag_intents=" + ",".join(diag_intents[:4]))

            hit = dict(row)
            hit["score"] = round(score, 4)
            hit["matched_terms"] = overlap[:10]
            hit["matched_patterns"] = matched_patterns[:10]
            hit["match_reasons"] = reasons
            hit["match_confidence"] = round(min(0.99, 0.42 + (score / 12.0)), 4)
            hit["explain"] = "; ".join(reasons[:4]) if reasons else "contextual weak match"
            hits.append(hit)

        hits.sort(
            key=lambda x: (
                -_safe_float(x.get("score"), 0.0),
                -_safe_int(x.get("priority"), 0),
                str(x.get("issue_id") or ""),
            )
        )
        return hits[: max(1, min(int(limit), 20))]
