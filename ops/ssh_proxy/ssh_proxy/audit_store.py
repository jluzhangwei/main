from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AUDIT_DIR = "~/.ssh_proxy/audit"


@dataclass(frozen=True)
class AuditSession:
    session_id: str
    session_dir: Path
    metadata: dict[str, Any]


def audit_base(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("SSH_PROXY_AUDIT_DIR") or DEFAULT_AUDIT_DIR).expanduser()


def resolve_session(base: Path, session_id: str | None) -> AuditSession:
    sid = session_id or latest_session_id(base)
    if not sid:
        raise FileNotFoundError(f"no audit sessions found under {base}")
    matches = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(sid)]
    if not matches:
        raise FileNotFoundError(f"session not found: {sid}")
    if len(matches) > 1 and not any(p.name == sid for p in matches):
        found = ", ".join(p.name for p in matches[:8])
        raise ValueError(f"ambiguous session id prefix {sid!r}: {found}")
    session_dir = next((p for p in matches if p.name == sid), matches[0])
    metadata = read_json(session_dir / "metadata.json")
    return AuditSession(session_id=session_dir.name, session_dir=session_dir, metadata=metadata)


def latest_session_id(base: Path) -> str | None:
    latest = base / "latest"
    if latest.exists():
        text = latest.read_text(encoding="utf-8").strip()
        if text:
            return text
    sessions = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions[0].name if sessions else None


def list_sessions(base: Path, limit: int = 20) -> list[AuditSession]:
    sessions: list[AuditSession] = []
    for session_dir in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        metadata_path = session_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        sessions.append(AuditSession(session_id=session_dir.name, session_dir=session_dir, metadata=read_json(metadata_path)))
        if len(sessions) >= limit:
            break
    return sessions


def read_events(session: AuditSession, event_type: str | None = None) -> list[dict[str, Any]]:
    rows = list(read_jsonl(session.session_dir / "events.jsonl"))
    if event_type:
        rows = [row for row in rows if row.get("event") == event_type]
    return rows


def read_replay(session: AuditSession) -> list[dict[str, Any]]:
    return list(read_jsonl(session.session_dir / "replay.jsonl"))


def read_commands(session: AuditSession) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for row in read_events(session, "command"):
        payload = row.get("payload") or {}
        decision = payload.get("decision") or {}
        commands.append(
            {
                "ts": row.get("ts"),
                "command": payload.get("command", ""),
                "allowed": bool(decision.get("allowed")),
                "rule": decision.get("rule", ""),
                "reason": decision.get("reason", ""),
            }
        )
    return commands


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            yield json.loads(text)
