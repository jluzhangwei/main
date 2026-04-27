from __future__ import annotations

import getpass
import json
import os
import re
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


@dataclass(frozen=True)
class AuditPaths:
    session_dir: Path
    events_path: Path
    replay_path: Path
    metadata_path: Path


class AuditWriter:
    def __init__(self, audit_dir: Path | str | None, login_command: list[str]) -> None:
        base = Path(audit_dir or os.environ.get("SSH_PROXY_AUDIT_DIR") or "~/.ssh_proxy/audit").expanduser()
        self.session_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.paths = AuditPaths(
            session_dir=base / self.session_id,
            events_path=base / self.session_id / "events.jsonl",
            replay_path=base / self.session_id / "replay.jsonl",
            metadata_path=base / self.session_id / "metadata.json",
        )
        self.paths.session_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = base / "sessions.jsonl"
        self.latest_path = base / "latest"
        self._events = self.paths.events_path.open("a", encoding="utf-8")
        self._replay = self.paths.replay_path.open("a", encoding="utf-8")
        self._metadata: dict[str, Any] = {
            "session_id": self.session_id,
            "user": getpass.getuser(),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "login_command": list(login_command),
            "target_hint": _target_hint(login_command),
            "started_at": _iso(self.started_at),
            "started_at_epoch": self.started_at,
            "ended_at": None,
            "ended_at_epoch": None,
            "exit_code": None,
        }
        self._write_metadata()
        self._write_latest()
        self.event("session_start", self._metadata)

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "ts": _iso(time.time()),
            "ts_epoch": time.time(),
            "session_id": self.session_id,
            "event": event_type,
            "payload": _redact(payload),
        }
        self._events.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._events.flush()

    def replay(self, stream: str, data: bytes | str) -> None:
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        row = {
            "ts": _iso(time.time()),
            "ts_epoch": time.time(),
            "session_id": self.session_id,
            "stream": stream,
            "data": _redact_text(text),
        }
        self._replay.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._replay.flush()

    def command(self, command: str, decision: Any) -> None:
        payload = {
            "command": command,
            "decision": asdict(decision) if hasattr(decision, "__dataclass_fields__") else decision,
        }
        self.event("command", payload)

    def close(self, exit_code: int | None) -> None:
        ended = time.time()
        self._metadata["ended_at"] = _iso(ended)
        self._metadata["ended_at_epoch"] = ended
        self._metadata["exit_code"] = exit_code
        self._write_metadata()
        self.event("session_end", {"exit_code": exit_code})
        self._append_index()
        self._write_latest()
        self._events.close()
        self._replay.close()

    def _write_metadata(self) -> None:
        self.paths.metadata_path.write_text(
            json.dumps(_redact(self._metadata), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _append_index(self) -> None:
        row = dict(_redact(self._metadata))
        row["session_dir"] = str(self.paths.session_dir)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _write_latest(self) -> None:
        self.latest_path.write_text(self.session_id + "\n", encoding="utf-8")


def _target_hint(argv: list[str]) -> str | None:
    for token in reversed(argv):
        text = str(token)
        if "@" in text or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", text) or "." in text:
            return text
    return argv[-1] if argv else None


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if _looks_secret_key(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    out = str(text)
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + "***" if m.groups() else "***", out)
    return out


def _looks_secret_key(key: str) -> bool:
    low = str(key or "").lower()
    return any(x in low for x in ("password", "passwd", "token", "secret", "api_key", "apikey"))
