from __future__ import annotations

import difflib
import hashlib
import json
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


class NetdiagConfigStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "snapshots": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("snapshots", [])
                return payload
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload or {})
        sid = str(row.get("snapshot_id") or "").strip() or uuid.uuid4().hex[:16]
        file_path = str(row.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("file_path is required")
        p = Path(file_path)
        if not p.exists():
            raise ValueError("file_path not found")
        text = p.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        out = {
            "snapshot_id": sid,
            "device_id": str(row.get("device_id") or "").strip() or "*",
            "session_id": str(row.get("session_id") or "").strip(),
            "profile": str(row.get("profile") or "").strip().lower() or "unknown",
            "command": str(row.get("command") or "").strip(),
            "file_path": p.as_posix(),
            "content_hash": sha,
            "line_count": len(text.splitlines()),
            "size_bytes": len(text.encode("utf-8", errors="replace")),
            "source": str(row.get("source") or "netdiag").strip(),
            "created_at": str(row.get("created_at") or _now_iso()),
            "updated_at": _now_iso(),
            "tags": [str(x).strip() for x in (row.get("tags") or []) if str(x).strip()],
        }
        with self._lock:
            data = self._load()
            items = [x for x in data.get("snapshots", []) if isinstance(x, dict)]
            replaced = False
            for i, old in enumerate(items):
                if str(old.get("snapshot_id") or "") == sid:
                    out["created_at"] = str(old.get("created_at") or out["created_at"])
                    items[i] = {**old, **out}
                    replaced = True
                    break
            if not replaced:
                items.append(out)
            data["snapshots"] = items
            self._save(data)
        return out

    def list_snapshots(self, *, device_id: str = "", session_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        did = str(device_id or "").strip()
        sid = str(session_id or "").strip()
        with self._lock:
            rows = [x for x in self._load().get("snapshots", []) if isinstance(x, dict)]
        out: list[dict[str, Any]] = []
        for row in rows:
            if did and str(row.get("device_id") or "") != did:
                continue
            if sid and str(row.get("session_id") or "") != sid:
                continue
            out.append(row)
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return out[: max(1, min(int(limit), 5000))]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        sid = str(snapshot_id or "").strip()
        if not sid:
            return None
        with self._lock:
            rows = [x for x in self._load().get("snapshots", []) if isinstance(x, dict)]
        for row in rows:
            if str(row.get("snapshot_id") or "") == sid:
                return row
        return None

    def diff_snapshots(self, snapshot_id_a: str, snapshot_id_b: str, *, context: int = 3, max_lines: int = 2000) -> dict[str, Any]:
        a = self.get_snapshot(snapshot_id_a)
        b = self.get_snapshot(snapshot_id_b)
        if not a or not b:
            raise ValueError("snapshot not found")
        pa = Path(str(a.get("file_path") or ""))
        pb = Path(str(b.get("file_path") or ""))
        if not pa.exists() or not pb.exists():
            raise ValueError("snapshot file missing")
        ta = pa.read_text(encoding="utf-8", errors="replace").splitlines()
        tb = pb.read_text(encoding="utf-8", errors="replace").splitlines()
        diff_lines = list(
            difflib.unified_diff(
                ta,
                tb,
                fromfile=f"{a.get('snapshot_id')}:{a.get('device_id')}",
                tofile=f"{b.get('snapshot_id')}:{b.get('device_id')}",
                lineterm="",
                n=max(0, min(int(context), 12)),
            )
        )
        if len(diff_lines) > max_lines:
            diff_lines = diff_lines[: max_lines]
        plus = sum(1 for x in diff_lines if x.startswith("+") and not x.startswith("+++"))
        minus = sum(1 for x in diff_lines if x.startswith("-") and not x.startswith("---"))
        return {
            "snapshot_a": a,
            "snapshot_b": b,
            "changed_lines_add": plus,
            "changed_lines_del": minus,
            "diff_lines": diff_lines,
            "diff_text": "\n".join(diff_lines),
            "truncated": len(diff_lines) >= max_lines,
        }
