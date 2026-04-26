from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Iterable

from app.models.schemas import (
    CommandCapabilityHistoryItem,
    CommandCapabilityRule,
    CommandCapabilityUpsertRequest,
    DeviceProtocol,
    now_utc,
)

logger = logging.getLogger(__name__)


@dataclass
class CapabilityMatch:
    rule: CommandCapabilityRule
    command_key: str


class CommandCapabilityStore:
    def __init__(
        self,
        *,
        snapshot_path: Path,
        wal_path: Path,
        compact_interval_seconds: int = 60,
        compact_event_threshold: int = 200,
    ) -> None:
        self.snapshot_path = snapshot_path
        self.wal_path = wal_path
        self.compact_interval_seconds = max(5, int(compact_interval_seconds))
        self.compact_event_threshold = max(10, int(compact_event_threshold))

        self._rules_by_key: dict[tuple[str, str], CommandCapabilityRule] = {}
        self._id_to_key: dict[str, tuple[str, str]] = {}
        self._scope_index: dict[str, set[str]] = {}

        self._wal_events_since_snapshot = 0
        self._last_snapshot_monotonic = time.monotonic()
        self._lock = threading.RLock()

        self._load_from_disk()

    @staticmethod
    def default_snapshot_path() -> Path:
        env_path = (os.getenv("NETOPS_COMMAND_CAPABILITY_SNAPSHOT_PATH") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "command_capability_snapshot.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_command_capability_snapshot.json"

    @staticmethod
    def default_wal_path() -> Path:
        env_path = (os.getenv("NETOPS_COMMAND_CAPABILITY_WAL_PATH") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "command_capability.wal"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_command_capability.wal"

    @staticmethod
    def normalize_host(host: str) -> str:
        return str(host or "").strip().lower()

    @staticmethod
    def normalize_version_signature(version_signature: str | None) -> str:
        normalized = str(version_signature or "").strip().lower()
        if not normalized:
            return ""
        # Canonical form: keep only non-empty segments so partial signatures
        # (1 or 2 segments) can match consistently.
        parts = [segment.strip() for segment in normalized.split("|")]
        tokens = [token for token in parts if token]
        return "|".join(tokens)

    @staticmethod
    def normalize_command_key(command: str) -> str:
        compact = " ".join(str(command or "").strip().split())
        return compact.lower()

    @staticmethod
    def normalize_scope_type(scope_type: str | None) -> str:
        normalized = str(scope_type or "").strip().lower()
        if normalized in {"version", "device", "vendor", "global"}:
            return normalized
        return "version"

    @classmethod
    def build_scope_key(
        cls,
        *,
        scope_type: str,
        host: str | None,
        protocol: DeviceProtocol | str,
        device_type: str | None = None,
        vendor: str | None = None,
        version_signature: str | None = None,
    ) -> str:
        protocol_text = str(getattr(protocol, "value", protocol) or "").strip().lower() or "ssh"
        scope_type_text = cls.normalize_scope_type(scope_type)
        version_text = cls.normalize_version_signature(version_signature)
        device_text = str(device_type or "").strip().lower()
        vendor_text = str(vendor or "").strip().lower()
        host_text = cls.normalize_host(host or "")
        if scope_type_text == "version":
            return "|".join([scope_type_text, protocol_text, version_text])
        if scope_type_text == "device":
            return "|".join([scope_type_text, protocol_text, host_text])
        if scope_type_text == "vendor":
            return "|".join([scope_type_text, protocol_text, vendor_text, device_text])
        return "|".join([scope_type_text, protocol_text])

    def list_rules(
        self,
        *,
        host: str | None = None,
        version_signature: str | None = None,
        scope_key: str | None = None,
    ) -> list[CommandCapabilityRule]:
        with self._lock:
            items = list(self._iter_filtered_rules(host=host, version_signature=version_signature, scope_key=scope_key))
            items.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
            return [item.model_copy(deep=True) for item in items]

    def get_rule(self, rule_id: str) -> CommandCapabilityRule | None:
        with self._lock:
            key = self._id_to_key.get(rule_id)
            if not key:
                return None
            item = self._rules_by_key.get(key)
            if item is None:
                return None
            return item.model_copy(deep=True)

    def upsert_rule(self, req: CommandCapabilityUpsertRequest) -> CommandCapabilityRule:
        now = now_utc()
        with self._lock:
            existing = self._resolve_existing(req)
            scope_type = self.normalize_scope_type(req.scope_type)
            normalized_host = self.normalize_host(req.host or "")
            normalized_version_signature = self.normalize_version_signature(req.version_signature)
            target_scope = self.build_scope_key(
                scope_type=scope_type,
                host=normalized_host,
                protocol=req.protocol,
                device_type=req.device_type,
                vendor=req.vendor,
                version_signature=normalized_version_signature,
            )
            command_key = self.normalize_command_key(req.command_key)
            if not command_key:
                raise ValueError("command_key cannot be empty")

            if req.action == "rewrite":
                rewrite_to = str(req.rewrite_to or "").strip()
                if not rewrite_to:
                    raise ValueError("rewrite action requires rewrite_to")
            else:
                rewrite_to = None

            if existing is None:
                create_kwargs = {
                    "scope_type": scope_type,
                    "scope_key": target_scope,
                    "host": normalized_host or None,
                    "protocol": req.protocol,
                    "device_type": (req.device_type or "").strip().lower() or None,
                    "vendor": (req.vendor or "").strip().lower() or None,
                    "version_signature": normalized_version_signature or None,
                    "command_key": command_key,
                    "action": req.action,
                    "rewrite_to": rewrite_to,
                    "reason_code": (req.reason_code or "").strip() or None,
                    "reason_text": (req.reason_text or "").strip() or None,
                    "source": req.source or "manual",
                    "enabled": True if req.enabled is None else bool(req.enabled),
                    "hit_count": 0,
                    "history": [],
                    "created_at": now,
                    "updated_at": now,
                }
                if req.id:
                    create_kwargs["id"] = req.id
                rule = CommandCapabilityRule(**create_kwargs)
            else:
                rule = existing.model_copy(deep=True)
                if (
                    rule.action != req.action
                    or (rule.rewrite_to or "").strip() != (rewrite_to or "").strip()
                    or (rule.reason_code or "") != ((req.reason_code or "").strip() or None)
                    or (rule.reason_text or "") != ((req.reason_text or "").strip() or None)
                ):
                    rule.history.append(
                        CommandCapabilityHistoryItem(
                            changed_at=now,
                            action=rule.action,
                            rewrite_to=rule.rewrite_to,
                            reason_code=rule.reason_code,
                            reason_text=rule.reason_text,
                        )
                    )

                rule.scope_type = scope_type  # type: ignore[assignment]
                rule.scope_key = target_scope
                rule.host = normalized_host or None
                rule.protocol = req.protocol
                rule.device_type = (req.device_type or "").strip().lower() or None
                rule.vendor = (req.vendor or "").strip().lower() or None
                rule.version_signature = normalized_version_signature or None
                rule.command_key = command_key
                rule.action = req.action
                rule.rewrite_to = rewrite_to
                rule.reason_code = (req.reason_code or "").strip() or None
                rule.reason_text = (req.reason_text or "").strip() or None
                if req.source is not None:
                    rule.source = req.source
                if req.enabled is not None:
                    rule.enabled = bool(req.enabled)
                rule.updated_at = now

            self._put_rule(rule)
            self._append_wal_event(
                {
                    "ts": now.isoformat(),
                    "op": "upsert",
                    "rule": rule.model_dump(mode="json"),
                }
            )
            return rule.model_copy(deep=True)

    def set_enabled(self, rule_id: str, enabled: bool) -> CommandCapabilityRule | None:
        with self._lock:
            key = self._id_to_key.get(rule_id)
            if not key:
                return None
            rule = self._rules_by_key.get(key)
            if not rule:
                return None
            now = now_utc()
            rule.enabled = bool(enabled)
            rule.updated_at = now
            self._put_rule(rule)
            self._append_wal_event(
                {
                    "ts": now.isoformat(),
                    "op": "enable",
                    "id": rule.id,
                    "enabled": bool(enabled),
                }
            )
            return rule.model_copy(deep=True)

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            key = self._id_to_key.get(rule_id)
            if not key:
                return False
            self._remove_by_key(key)
            self._append_wal_event({"ts": now_utc().isoformat(), "op": "delete", "id": rule_id})
            return True

    def reset(self, *, host: str | None = None, version_signature: str | None = None) -> tuple[int, int]:
        with self._lock:
            to_delete: list[tuple[str, str]] = []
            normalized_host = self.normalize_host(host) if host else ""
            normalized_signature = self.normalize_version_signature(version_signature)
            for key, rule in self._rules_by_key.items():
                if normalized_host and rule.host != normalized_host:
                    continue
                if normalized_signature and self.normalize_version_signature(rule.version_signature) != normalized_signature:
                    continue
                to_delete.append(key)

            for key in to_delete:
                self._remove_by_key(key)

            self._append_wal_event(
                {
                    "ts": now_utc().isoformat(),
                    "op": "reset",
                    "host": normalized_host or None,
                    "version_signature": normalized_signature or None,
                    "removed": len(to_delete),
                }
            )
            return len(to_delete), len(self._rules_by_key)

    def resolve_match(
        self,
        *,
        host: str,
        protocol: DeviceProtocol | str,
        device_type: str | None,
        vendor: str | None,
        version_signature: str | None,
        command_text: str,
    ) -> CapabilityMatch | None:
        command_key = self.normalize_command_key(command_text)
        if not command_key:
            return None
        normalized_signature = self.normalize_version_signature(version_signature)
        if not normalized_signature:
            return None

        with self._lock:
            for scope in self._candidate_scope_keys(
                host=host,
                protocol=protocol,
                device_type=device_type,
                vendor=vendor,
                version_signature=normalized_signature,
            ):
                key = (scope, command_key)
                rule = self._rules_by_key.get(key)
                if rule and rule.enabled:
                    return CapabilityMatch(rule=rule.model_copy(deep=True), command_key=command_key)
        return None

    def register_hit(self, rule_id: str) -> CommandCapabilityRule | None:
        with self._lock:
            key = self._id_to_key.get(rule_id)
            if not key:
                return None
            rule = self._rules_by_key.get(key)
            if not rule:
                return None
            now = now_utc()
            rule.hit_count = int(rule.hit_count or 0) + 1
            rule.last_hit_at = now
            rule.updated_at = now
            self._put_rule(rule)
            self._append_wal_event(
                {
                    "ts": now.isoformat(),
                    "op": "hit",
                    "id": rule.id,
                    "hit_count": rule.hit_count,
                    "last_hit_at": now.isoformat(),
                }
            )
            return rule.model_copy(deep=True)

    def learn_rewrite(
        self,
        *,
        host: str,
        protocol: DeviceProtocol,
        device_type: str | None,
        vendor: str | None,
        version_signature: str | None,
        failed_command: str,
        rewrite_to: str,
        reason_text: str,
    ) -> CommandCapabilityRule | None:
        normalized_signature = self.normalize_version_signature(version_signature)
        if not normalized_signature:
            return None
        return self.upsert_rule(
            CommandCapabilityUpsertRequest(
                scope_type="device",
                host=host,
                protocol=protocol,
                device_type=device_type,
                vendor=vendor,
                version_signature=normalized_signature,
                command_key=failed_command,
                action="rewrite",
                rewrite_to=rewrite_to,
                reason_code="cli_syntax_retry_success",
                reason_text=reason_text,
                source="learned",
                enabled=True,
            )
        )

    def learn_block(
        self,
        *,
        host: str,
        protocol: DeviceProtocol,
        device_type: str | None,
        vendor: str | None,
        version_signature: str | None,
        failed_command: str,
        reason_text: str,
    ) -> CommandCapabilityRule | None:
        normalized_signature = self.normalize_version_signature(version_signature)
        if not normalized_signature:
            return None
        return self.upsert_rule(
            CommandCapabilityUpsertRequest(
                scope_type="device",
                host=host,
                protocol=protocol,
                device_type=device_type,
                vendor=vendor,
                version_signature=normalized_signature,
                command_key=failed_command,
                action="block",
                reason_code="cli_syntax_error",
                reason_text=reason_text,
                source="learned",
                enabled=True,
            )
        )

    def _resolve_existing(self, req: CommandCapabilityUpsertRequest) -> CommandCapabilityRule | None:
        if req.id:
            key = self._id_to_key.get(req.id)
            if key and key in self._rules_by_key:
                return self._rules_by_key[key]

        scope_key = self.build_scope_key(
            scope_type=self.normalize_scope_type(req.scope_type),
            host=req.host,
            protocol=req.protocol,
            device_type=req.device_type,
            vendor=req.vendor,
            version_signature=req.version_signature,
        )
        command_key = self.normalize_command_key(req.command_key)
        if not command_key:
            return None
        return self._rules_by_key.get((scope_key, command_key))

    def _candidate_scope_keys(
        self,
        *,
        host: str,
        protocol: DeviceProtocol | str,
        device_type: str | None,
        vendor: str | None,
        version_signature: str | None,
    ) -> list[str]:
        normalized_host = self.normalize_host(host)
        protocol_text = str(getattr(protocol, "value", protocol) or "").strip().lower() or "ssh"
        device_text = str(device_type or "").strip().lower()
        vendor_text = str(vendor or "").strip().lower()
        normalized_signature = self.normalize_version_signature(version_signature)
        if not normalized_signature:
            return []
        signature_tokens = [token for token in normalized_signature.split("|") if token]
        if not signature_tokens:
            return []

        candidates: list[str] = []
        if normalized_host:
            candidates.append(
                self.build_scope_key(
                    scope_type="device",
                    host=normalized_host,
                    protocol=protocol_text,
                    device_type=device_text,
                    vendor=vendor_text,
                    version_signature=normalized_signature,
                )
            )
        # Try exact match first, then progressively broader partial signatures.
        # Example with 3 tokens: (a|b|c), (a|b), (a|c), (b|c), (a), (b), (c)
        for size in range(len(signature_tokens), 0, -1):
            for indexes in combinations(range(len(signature_tokens)), size):
                candidate_signature = "|".join(signature_tokens[idx] for idx in indexes)
                candidates.append(
                    self.build_scope_key(
                        scope_type="version",
                        host=normalized_host,
                        protocol=protocol_text,
                        device_type=device_text,
                        vendor=vendor_text,
                        version_signature=candidate_signature,
                    )
                )

        # Keep order, remove duplicates.
        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _iter_filtered_rules(
        self,
        *,
        host: str | None,
        version_signature: str | None,
        scope_key: str | None,
    ) -> Iterable[CommandCapabilityRule]:
        if scope_key:
            ids = list(self._scope_index.get(scope_key, set()))
            for rule_id in ids:
                key = self._id_to_key.get(rule_id)
                if not key:
                    continue
                rule = self._rules_by_key.get(key)
                if rule:
                    yield rule
            return

        if host:
            normalized_host = self.normalize_host(host)
            for rule in self._rules_by_key.values():
                if rule.host == normalized_host:
                    yield rule
            return

        if version_signature:
            normalized_signature = self.normalize_version_signature(version_signature)
            for rule in self._rules_by_key.values():
                if self.normalize_scope_type(rule.scope_type) != "version":
                    continue
                if self.normalize_version_signature(rule.version_signature) == normalized_signature:
                    yield rule
            return

        for rule in self._rules_by_key.values():
            yield rule

    def _put_rule(self, rule: CommandCapabilityRule) -> None:
        if self.normalize_scope_type(rule.scope_type) == "version":
            normalized_signature = self.normalize_version_signature(rule.version_signature)
            rule.version_signature = normalized_signature or None
            rule.scope_key = self.build_scope_key(
                scope_type="version",
                host=rule.host,
                protocol=rule.protocol,
                device_type=rule.device_type,
                vendor=rule.vendor,
                version_signature=normalized_signature,
            )
        rule.command_key = self.normalize_command_key(rule.command_key)
        old_key = self._id_to_key.get(rule.id)
        new_key = (rule.scope_key, rule.command_key)

        if old_key and old_key != new_key:
            self._remove_by_key(old_key)

        replaced = self._rules_by_key.get(new_key)
        if replaced and replaced.id != rule.id:
            self._remove_by_key(new_key)

        self._rules_by_key[new_key] = rule
        self._id_to_key[rule.id] = new_key
        scoped = self._scope_index.get(rule.scope_key)
        if scoped is None:
            scoped = set()
            self._scope_index[rule.scope_key] = scoped
        scoped.add(rule.id)

    def _remove_by_key(self, key: tuple[str, str]) -> None:
        existing = self._rules_by_key.pop(key, None)
        if not existing:
            return

        self._id_to_key.pop(existing.id, None)
        scoped = self._scope_index.get(existing.scope_key)
        if scoped:
            scoped.discard(existing.id)
            if not scoped:
                self._scope_index.pop(existing.scope_key, None)

    def _append_wal_event(self, event: dict) -> None:
        try:
            self.wal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.wal_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(event, ensure_ascii=False))
                fp.write("\n")
                fp.flush()
                os.fsync(fp.fileno())
            self._wal_events_since_snapshot += 1
            self._maybe_compact_snapshot()
        except Exception as exc:  # pragma: no cover - defensive file IO
            logger.warning("command capability wal append failed: %s", exc)

    def _maybe_compact_snapshot(self) -> None:
        elapsed = time.monotonic() - self._last_snapshot_monotonic
        if self._wal_events_since_snapshot < self.compact_event_threshold and elapsed < self.compact_interval_seconds:
            return
        self._write_snapshot_and_truncate_wal()

    def flush_snapshot(self) -> None:
        with self._lock:
            self._write_snapshot_and_truncate_wal()

    def _write_snapshot_and_truncate_wal(self) -> None:
        payload = {
            "version": 1,
            "rules": [item.model_dump(mode="json") for item in self._rules_by_key.values()],
            "generated_at": now_utc().isoformat(),
        }

        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.snapshot_path.with_suffix(f"{self.snapshot_path.suffix}.tmp")
            with tmp_path.open("w", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False))
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_path, self.snapshot_path)

            with self.wal_path.open("w", encoding="utf-8") as fp:
                fp.truncate(0)
                fp.flush()
                os.fsync(fp.fileno())

            self._wal_events_since_snapshot = 0
            self._last_snapshot_monotonic = time.monotonic()
            try:
                os.chmod(self.snapshot_path, 0o600)
            except Exception:
                pass
            try:
                os.chmod(self.wal_path, 0o600)
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover - defensive file IO
            logger.warning("command capability snapshot compact failed: %s", exc)

    def _load_from_disk(self) -> None:
        with self._lock:
            self._rules_by_key.clear()
            self._id_to_key.clear()
            self._scope_index.clear()

            snapshot_exists = self.snapshot_path.exists()
            snapshot_loaded = self._load_snapshot()
            self._replay_wal()
            if snapshot_exists and not snapshot_loaded:
                logger.warning("command capability snapshot unavailable/corrupted, recovered from WAL where possible")

    def _load_snapshot(self) -> bool:
        if not self.snapshot_path.exists():
            return False
        try:
            raw = self.snapshot_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return False
            rows = payload.get("rules", [])
            if not isinstance(rows, list):
                return False
            for row in rows:
                try:
                    rule = CommandCapabilityRule.model_validate(row)
                except Exception:
                    continue
                self._put_rule(rule)
            return True
        except Exception as exc:
            logger.warning("failed to load capability snapshot: %s", exc)
            return False

    def _replay_wal(self) -> None:
        if not self.wal_path.exists():
            return

        bad_lines = 0
        replayed = 0
        try:
            with self.wal_path.open("r", encoding="utf-8") as fp:
                for line_no, line in enumerate(fp, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except Exception:
                        bad_lines += 1
                        logger.warning("skip malformed capability wal line: %s", line_no)
                        continue
                    if not isinstance(event, dict):
                        bad_lines += 1
                        logger.warning("skip malformed capability wal line (not object): %s", line_no)
                        continue
                    try:
                        if self._apply_wal_event(event):
                            replayed += 1
                    except Exception:
                        bad_lines += 1
                        logger.warning("skip invalid capability wal event at line: %s", line_no)
                        continue
        except Exception as exc:
            logger.warning("failed to replay capability wal: %s", exc)

        self._wal_events_since_snapshot = replayed
        self._last_snapshot_monotonic = time.monotonic()
        if bad_lines > 0:
            logger.warning("capability wal replay skipped malformed lines: %s", bad_lines)

    def _apply_wal_event(self, event: dict) -> bool:
        op = str(event.get("op") or "").strip().lower()
        if not op:
            return False

        if op == "upsert":
            row = event.get("rule")
            if not isinstance(row, dict):
                return False
            try:
                rule = CommandCapabilityRule.model_validate(row)
            except Exception:
                return False
            self._put_rule(rule)
            return True

        if op == "delete":
            rule_id = str(event.get("id") or "").strip()
            if not rule_id:
                return False
            key = self._id_to_key.get(rule_id)
            if key:
                self._remove_by_key(key)
            return True

        if op == "enable":
            rule_id = str(event.get("id") or "").strip()
            if not rule_id:
                return False
            key = self._id_to_key.get(rule_id)
            if not key:
                return True
            rule = self._rules_by_key.get(key)
            if not rule:
                return True
            rule.enabled = bool(event.get("enabled", True))
            rule.updated_at = now_utc()
            self._put_rule(rule)
            return True

        if op == "hit":
            rule_id = str(event.get("id") or "").strip()
            if not rule_id:
                return False
            key = self._id_to_key.get(rule_id)
            if not key:
                return True
            rule = self._rules_by_key.get(key)
            if not rule:
                return True
            try:
                rule.hit_count = max(0, int(event.get("hit_count", rule.hit_count or 0)))
            except Exception:
                pass
            last_hit = event.get("last_hit_at")
            if isinstance(last_hit, str) and last_hit.strip():
                try:
                    rule.last_hit_at = datetime.fromisoformat(last_hit)
                except Exception:
                    rule.last_hit_at = now_utc()
            rule.updated_at = now_utc()
            self._put_rule(rule)
            return True

        if op == "reset":
            host = str(event.get("host") or "").strip().lower()
            version_signature = self.normalize_version_signature(event.get("version_signature"))
            to_delete: list[tuple[str, str]] = []
            for key, rule in self._rules_by_key.items():
                if host and rule.host != host:
                    continue
                if version_signature and self.normalize_version_signature(rule.version_signature) != version_signature:
                    continue
                to_delete.append(key)
            for key in to_delete:
                self._remove_by_key(key)
            return True

        return False
