from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Iterable

from app.models.schemas import (
    SOPArchiveCommandTemplate,
    SOPArchiveDecisionPoint,
    SOPArchiveEntryResponse,
    SOPArchiveKeyStep,
    SOPListResponse,
    SOPPublishResponse,
    SOPRecord,
    SOPStatus,
    SOPUpsertRequest,
    now_utc,
)


@dataclass(frozen=True)
class SOPSeed:
    id: str
    name: str
    summary: str
    usage_hint: str
    trigger_keywords: tuple[str, ...]
    topic_key: str | None = None
    topic_name: str | None = None
    vendor_tags: tuple[str, ...] = ()
    version_signatures: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    anti_conditions: tuple[str, ...] = ()
    evidence_goals: tuple[str, ...] = ()
    key_steps: tuple[tuple[int, str, str, tuple[str, ...], tuple[str, ...]], ...] = ()
    decision_points: tuple[tuple[str, str], ...] = ()
    command_templates: tuple[tuple[str, tuple[str, ...]], ...] = ()
    fallback_commands: tuple[str, ...] = ()
    expected_findings: tuple[str, ...] = ()

    def to_record(self) -> SOPRecord:
        return SOPRecord(
            id=self.id,
            topic_key=self.topic_key or self.id,
            topic_name=self.topic_name or self.name,
            version=1,
            status=SOPStatus.published,
            is_current_published=True,
            name=self.name,
            summary=self.summary,
            usage_hint=self.usage_hint,
            trigger_keywords=list(self.trigger_keywords),
            vendor_tags=list(self.vendor_tags),
            version_signatures=list(self.version_signatures),
            preconditions=list(self.preconditions),
            anti_conditions=list(self.anti_conditions),
            evidence_goals=list(self.evidence_goals),
            key_steps=[
                SOPArchiveKeyStep(
                    step_no=step_no,
                    title=title,
                    goal=goal,
                    commands=list(commands),
                    expected_signals=list(expected_signals),
                )
                for step_no, title, goal, commands, expected_signals in self.key_steps
            ],
            decision_points=[
                SOPArchiveDecisionPoint(signal=signal, meaning=meaning)
                for signal, meaning in self.decision_points
            ],
            command_templates=[
                SOPArchiveCommandTemplate(vendor=vendor, commands=list(commands))
                for vendor, commands in self.command_templates
            ],
            fallback_commands=list(self.fallback_commands),
            expected_findings=list(self.expected_findings),
            generated_by_model="seed",
            generated_by_prompt_version="seed-v1",
            published_at=now_utc(),
        )


class SOPStore:
    def __init__(
        self,
        *,
        snapshot_path: Path | None = None,
        wal_path: Path | None = None,
        seeds: Iterable[SOPSeed] = (),
    ) -> None:
        self.snapshot_path = snapshot_path or self.default_snapshot_path()
        self.wal_path = wal_path or self.default_wal_path()
        self._records: dict[str, SOPRecord] = {}
        self._source_run_index: dict[str, set[str]] = defaultdict(set)
        self._active_refs: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.RLock()
        self._load()
        self._apply_seeds(seeds)

    @staticmethod
    def default_snapshot_path() -> Path:
        explicit = str(os.getenv("NETOPS_SOP_SNAPSHOT_PATH", "")).strip()
        if explicit:
            return Path(explicit).expanduser()
        home = os.path.expanduser("~")
        if home and home != "~":
            return Path(home) / ".netops-ai-v1" / "sop_snapshot.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_sop_snapshot.json"

    @staticmethod
    def default_wal_path() -> Path:
        explicit = str(os.getenv("NETOPS_SOP_WAL_PATH", "")).strip()
        if explicit:
            return Path(explicit).expanduser()
        home = os.path.expanduser("~")
        if home and home != "~":
            return Path(home) / ".netops-ai-v1" / "sop.wal"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_sop.wal"

    def list_records(self, status: SOPStatus | None = None) -> SOPListResponse:
        with self._lock:
            items = [
                record.to_archive_response()
                for record in self._sorted_records()
                if status is None or record.status == status
            ]
            return SOPListResponse(total=len(items), items=items)

    def get_record(self, record_id: str) -> SOPRecord:
        with self._lock:
            return self._records[record_id].model_copy(deep=True)

    def has_source_run(self, source_run_id: str) -> bool:
        with self._lock:
            return bool(self._source_run_index.get(source_run_id))

    def source_run_counts(self, source_run_id: str) -> tuple[bool, int, int]:
        with self._lock:
            record_ids = self._source_run_index.get(source_run_id, set())
            draft_count = 0
            published_count = 0
            for record_id in record_ids:
                record = self._records.get(record_id)
                if not record:
                    continue
                if record.status == SOPStatus.draft:
                    draft_count += 1
                elif record.status == SOPStatus.published:
                    published_count += 1
            return bool(record_ids), draft_count, published_count

    def primary_record_id_for_source_run(self, source_run_id: str) -> str | None:
        with self._lock:
            record_ids = self._source_run_index.get(source_run_id, set())
            if not record_ids:
                return None
            records = [self._records[item_id] for item_id in record_ids if item_id in self._records]
            if not records:
                return None
            published = [item for item in records if item.status == SOPStatus.published]
            target = published or records
            target.sort(
                key=lambda item: (
                    0 if item.status == SOPStatus.published else 1,
                    -(item.published_at.timestamp() if item.published_at else 0),
                    -(item.updated_at.timestamp() if item.updated_at else 0),
                    -item.version,
                )
            )
            return target[0].id

    def upsert_record(
        self,
        record_id: str,
        payload: SOPUpsertRequest,
        *,
        status: SOPStatus | None = None,
        version: int | None = None,
    ) -> SOPRecord:
        with self._lock:
            existing = self._records.get(record_id)
            now = now_utc()
            normalized_topic_name = self._normalize_topic_name(payload.topic_name, fallback=payload.name)
            normalized_topic_key = self._normalize_topic_key(payload.topic_key, fallback=normalized_topic_name)
            if existing:
                record = existing.model_copy(deep=True)
            else:
                record = SOPRecord(
                    id=record_id,
                    topic_key=normalized_topic_key,
                    topic_name=normalized_topic_name,
                    name=payload.name.strip(),
                    summary=payload.summary.strip(),
                    usage_hint=payload.usage_hint.strip(),
                )
            record.topic_key = normalized_topic_key
            record.topic_name = normalized_topic_name
            record.parent_version_id = str(payload.parent_version_id or "").strip() or record.parent_version_id
            record.name = payload.name.strip()
            record.summary = payload.summary.strip()
            record.usage_hint = payload.usage_hint.strip()
            record.trigger_keywords = self._normalize_text_list(payload.trigger_keywords)
            record.vendor_tags = self._normalize_text_list(payload.vendor_tags)
            record.version_signatures = self._normalize_text_list(payload.version_signatures)
            record.preconditions = self._normalize_text_list(payload.preconditions)
            record.anti_conditions = self._normalize_text_list(payload.anti_conditions)
            record.evidence_goals = self._normalize_text_list(payload.evidence_goals)
            record.key_steps = [item.model_copy(deep=True) for item in payload.key_steps]
            record.decision_points = [item.model_copy(deep=True) for item in payload.decision_points]
            record.command_templates = [item.model_copy(deep=True) for item in payload.command_templates]
            record.fallback_commands = self._normalize_text_list(payload.fallback_commands)
            record.expected_findings = self._normalize_text_list(payload.expected_findings)
            record.source_run_ids = self._normalize_text_list(payload.source_run_ids)
            record.generated_by_model = (payload.generated_by_model or "").strip() or None
            record.generated_by_prompt_version = (payload.generated_by_prompt_version or "").strip() or None
            record.review_notes = (payload.review_notes or "").strip() or None
            if status is not None:
                record.status = status
            if record.status != SOPStatus.published:
                record.is_current_published = False
            if version is not None:
                record.version = version
            if existing is None:
                record.created_at = now
            record.updated_at = now
            self._records[record.id] = record
            self._rebuild_source_index()
            self._append_wal({"op": "upsert", "record": record.model_dump(mode="json")})
            return record.model_copy(deep=True)

    def publish_record(self, record_id: str) -> SOPPublishResponse:
        with self._lock:
            record = self._records[record_id].model_copy(deep=True)
            previous_status = record.status
            archived_ids: list[str] = []
            for item in list(self._records.values()):
                if item.topic_key != record.topic_key or item.id == record.id or item.status != SOPStatus.published:
                    continue
                previous = item.model_copy(deep=True)
                previous.status = SOPStatus.archived
                previous.is_current_published = False
                previous.updated_at = now_utc()
                self._records[previous.id] = previous
                archived_ids.append(previous.id)
            record.status = SOPStatus.published
            record.is_current_published = True
            record.published_at = now_utc()
            record.updated_at = now_utc()
            self._records[record.id] = record
            self._append_wal(
                {
                    "op": "publish",
                    "record_id": record_id,
                    "published_at": record.published_at.isoformat(),
                    "archived_record_ids": archived_ids,
                }
            )
            return SOPPublishResponse(
                item=record.to_archive_response(),
                previous_status=previous_status,
                current_status=record.status,
            )

    def archive_record(self, record_id: str) -> SOPPublishResponse:
        with self._lock:
            record = self._records[record_id].model_copy(deep=True)
            previous_status = record.status
            record.status = SOPStatus.archived
            record.is_current_published = False
            record.updated_at = now_utc()
            self._records[record.id] = record
            self._append_wal({"op": "archive", "record_id": record_id})
            return SOPPublishResponse(
                item=record.to_archive_response(),
                previous_status=previous_status,
                current_status=record.status,
            )

    def next_version_for_topic(self, topic_key: str) -> int:
        normalized = self._normalize_topic_key(topic_key)
        with self._lock:
            versions = [item.version for item in self._records.values() if item.topic_key == normalized]
            return (max(versions) if versions else 0) + 1

    def delete_record(self, record_id: str) -> bool:
        with self._lock:
            if record_id not in self._records:
                return False
            self._records.pop(record_id, None)
            self._rebuild_source_index()
            self._append_wal({"op": "delete", "record_id": record_id})
            return True

    def cleanup_historical_records(self) -> dict[str, int]:
        with self._lock:
            if not self._records:
                return {"before": 0, "after": 0, "deleted": 0, "topics_merged": 0}

            before = len(self._records)
            touched_topics: set[str] = set()

            # Normalize topic naming first so later dedupe works across old variants like "-v2".
            for record_id, record in list(self._records.items()):
                updated = record.model_copy(deep=True)
                canonical_name = self._canonical_topic_name(updated.topic_name or updated.name)
                canonical_key = self._canonical_topic_key(updated.topic_key or canonical_name)
                if updated.topic_name != canonical_name or updated.topic_key != canonical_key:
                    updated.topic_name = canonical_name
                    updated.topic_key = canonical_key
                    self._records[record_id] = updated
                    touched_topics.add(canonical_key)

            grouped: dict[str, list[SOPRecord]] = defaultdict(list)
            for record in self._records.values():
                grouped[record.topic_key].append(record)

            records_to_delete: set[str] = set()
            for topic_key, rows in grouped.items():
                canonical_name = self._canonical_topic_name(rows[0].topic_name or rows[0].name)
                fingerprint_groups: dict[tuple[object, ...], list[SOPRecord]] = defaultdict(list)
                for row in rows:
                    fingerprint_groups[self._semantic_fingerprint(row)].append(row)

                deduped_rows: list[SOPRecord] = []
                for group_rows in fingerprint_groups.values():
                    primary = self._choose_primary_record(group_rows)
                    merged = primary.model_copy(deep=True)
                    merged.topic_key = topic_key
                    merged.topic_name = canonical_name
                    merged.source_run_ids = self._normalize_text_list(
                        source_id
                        for item in group_rows
                        for source_id in (item.source_run_ids or [])
                    )
                    merged.matched_count = sum(int(item.matched_count or 0) for item in group_rows)
                    merged.referenced_count = sum(int(item.referenced_count or 0) for item in group_rows)
                    merged.success_count = sum(int(item.success_count or 0) for item in group_rows)
                    merged.last_matched_at = self._max_datetime(item.last_matched_at for item in group_rows)
                    merged.last_referenced_at = self._max_datetime(item.last_referenced_at for item in group_rows)
                    merged.last_success_at = self._max_datetime(item.last_success_at for item in group_rows)
                    merged.updated_at = self._max_datetime(item.updated_at for item in group_rows) or merged.updated_at
                    self._records[merged.id] = merged
                    deduped_rows.append(merged)
                    for item in group_rows:
                        if item.id != merged.id:
                            records_to_delete.add(item.id)

                published = [item for item in deduped_rows if item.status == SOPStatus.published]
                published_primary = self._choose_primary_record(published) if published else None
                for item in deduped_rows:
                    current = self._records.get(item.id)
                    if not current:
                        continue
                    changed = False
                    if published_primary and current.status == SOPStatus.published and current.id != published_primary.id:
                        current.status = SOPStatus.archived
                        current.is_current_published = False
                        changed = True
                    elif published_primary and current.id == published_primary.id:
                        if not current.is_current_published:
                            current.is_current_published = True
                            changed = True
                    elif current.is_current_published:
                        current.is_current_published = False
                        changed = True
                    if current.topic_name != canonical_name:
                        current.topic_name = canonical_name
                        changed = True
                    if changed:
                        current.updated_at = now_utc()
                        self._records[current.id] = current

            for record_id in records_to_delete:
                self._records.pop(record_id, None)

            self._rebuild_source_index()
            self._append_wal(
                {
                    "op": "cleanup_history",
                    "deleted_record_ids": sorted(records_to_delete),
                    "ts": now_utc().isoformat(),
                }
            )
            after = len(self._records)
            return {
                "before": before,
                "after": after,
                "deleted": before - after,
                "topics_merged": len(touched_topics),
            }

    def matched_entries(
        self,
        *,
        problem: str,
        vendor: str | None = None,
        version_signature: str | None = None,
        run_key: str | None = None,
    ) -> list[SOPArchiveEntryResponse]:
        with self._lock:
            matched: list[SOPRecord] = []
            for record in self._sorted_records():
                if record.status != SOPStatus.published:
                    continue
                if self._matches_record(record, problem=problem, vendor=vendor, version_signature=version_signature):
                    matched.append(record)
            for record in matched:
                record.matched_count += 1
                record.last_matched_at = now_utc()
                self._records[record.id] = record
            if matched:
                self._append_wal(
                    {
                        "op": "stats",
                        "kind": "matched",
                        "record_ids": [item.id for item in matched],
                        "run_key": run_key,
                        "ts": now_utc().isoformat(),
                    }
                )
            return [item.to_archive_response() for item in matched]

    def referenced_entries(self, *, refs: list[str], text: str | None = None, run_key: str | None = None) -> list[SOPArchiveEntryResponse]:
        lowered_text = str(text or "").strip().lower()
        normalized_refs = [str(item or "").strip().lower() for item in refs if str(item or "").strip()]
        with self._lock:
            matched: list[SOPRecord] = []
            for record in self._records.values():
                if record.status != SOPStatus.published:
                    continue
                lowered_name = record.name.strip().lower()
                lowered_id = record.id.strip().lower()
                if lowered_id in normalized_refs or lowered_name in normalized_refs:
                    matched.append(record)
                    continue
                if lowered_text and ((lowered_id and lowered_id in lowered_text) or (lowered_name and lowered_name in lowered_text)):
                    matched.append(record)
            seen: set[str] = set()
            deduped: list[SOPRecord] = []
            for record in matched:
                if record.id in seen:
                    continue
                seen.add(record.id)
                deduped.append(record)
            for record in deduped:
                record.referenced_count += 1
                record.last_referenced_at = now_utc()
                self._records[record.id] = record
                if run_key:
                    self._active_refs[run_key].add(record.id)
            if deduped:
                self._append_wal(
                    {
                        "op": "stats",
                        "kind": "referenced",
                        "record_ids": [item.id for item in deduped],
                        "run_key": run_key,
                        "ts": now_utc().isoformat(),
                    }
                )
            return [item.to_archive_response() for item in deduped]

    def record_outcome(self, run_key: str, *, success: bool) -> bool:
        with self._lock:
            record_ids = list(self._active_refs.pop(run_key, set()))
            if not record_ids:
                return False
            if success:
                for record_id in record_ids:
                    record = self._records.get(record_id)
                    if not record:
                        continue
                    record.success_count += 1
                    record.last_success_at = now_utc()
                    self._records[record.id] = record
                self._append_wal(
                    {
                        "op": "stats",
                        "kind": "success",
                        "record_ids": record_ids,
                        "run_key": run_key,
                        "ts": now_utc().isoformat(),
                    }
                )
            return True

    def _sorted_records(self) -> list[SOPRecord]:
        return sorted(
            self._records.values(),
            key=lambda item: (
                item.status.value,
                -(item.published_at.timestamp() if item.published_at else 0),
                -(item.updated_at.timestamp() if item.updated_at else 0),
                item.name.lower(),
            ),
        )

    def dedupe_entries_for_runtime(self, entries: Iterable[SOPArchiveEntryResponse]) -> list[SOPArchiveEntryResponse]:
        grouped: dict[tuple[object, ...], SOPArchiveEntryResponse] = {}
        for entry in entries:
            key = (
                entry.topic_key.strip().lower() or entry.name.strip().lower(),
                tuple(sorted(item.strip().lower() for item in entry.vendor_tags)),
                tuple(sorted(item.strip().lower() for item in entry.version_signatures)),
                tuple(sorted(item.strip().lower() for item in entry.trigger_keywords)),
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = entry
                continue
            existing_ts = existing.published_at or existing.updated_at or existing.created_at
            current_ts = entry.published_at or entry.updated_at or entry.created_at
            if current_ts and (existing_ts is None or current_ts > existing_ts):
                grouped[key] = entry
        return list(grouped.values())

    def _matches_record(self, record: SOPRecord, *, problem: str, vendor: str | None, version_signature: str | None) -> bool:
        lowered = str(problem or "").strip().lower()
        if not lowered:
            return False
        if record.trigger_keywords and not any(self._text_matches_token(lowered, token) for token in record.trigger_keywords):
            return False
        normalized_vendor = str(vendor or "").strip().lower()
        if record.vendor_tags and normalized_vendor and not any(token.lower() in normalized_vendor for token in record.vendor_tags):
            return False
        normalized_signature = str(version_signature or "").strip().lower()
        if record.version_signatures and normalized_signature:
            if not any(token.lower() in normalized_signature for token in record.version_signatures):
                return False
        if record.preconditions and not all(self._text_matches_token(lowered, token) for token in record.preconditions):
            return False
        if record.anti_conditions and any(self._text_matches_token(lowered, token) for token in record.anti_conditions):
            return False
        return True

    @staticmethod
    def _text_matches_token(text: str, token: str) -> bool:
        lowered_text = str(text or "").strip().lower()
        lowered_token = str(token or "").strip().lower()
        if not lowered_text or not lowered_token:
            return False
        if lowered_token in lowered_text:
            return True
        for alias in SOPStore._token_aliases(lowered_token):
            if alias and alias in lowered_text:
                return True
        return False

    @staticmethod
    def _token_aliases(token: str) -> tuple[str, ...]:
        alias_groups: tuple[tuple[str, ...], ...] = (
            ("接口", "端口", "interface", "port", "ethernet", "gigabitethernet", "et"),
            ("shutdown", "admin shutdown", "admin down", "administratively down", "disabled", "disable"),
            ("物理断链", "断链", "链路断开", "link down", "physical down", "los"),
            ("ospf", "邻接", "neighbor", "adjacency"),
        )
        for group in alias_groups:
            lowered_group = tuple(item.lower() for item in group)
            if token in lowered_group:
                return lowered_group
        return (token,)

    def _normalize_text_list(self, values: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(text)
        return items

    def _canonical_topic_name(self, value: str | None) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[\s_-]*v\d+$", "", text, flags=re.IGNORECASE).strip()
        return text or "未命名主题"

    def _canonical_topic_key(self, value: str | None) -> str:
        return self._normalize_topic_key(None, fallback=self._canonical_topic_name(value))

    def _semantic_fingerprint(self, record: SOPRecord) -> tuple[object, ...]:
        return (
            record.summary.strip().lower(),
            record.usage_hint.strip().lower(),
            tuple(item.strip().lower() for item in record.trigger_keywords),
            tuple(item.strip().lower() for item in record.vendor_tags),
            tuple(item.strip().lower() for item in record.version_signatures),
            tuple(item.strip().lower() for item in record.preconditions),
            tuple(item.strip().lower() for item in record.anti_conditions),
            tuple(item.strip().lower() for item in record.evidence_goals),
            tuple(
                (
                    int(step.step_no or 0),
                    step.title.strip().lower(),
                    step.goal.strip().lower(),
                    tuple(command.strip().lower() for command in step.commands),
                    tuple(signal.strip().lower() for signal in step.expected_signals),
                )
                for step in record.key_steps
            ),
            tuple(
                (
                    point.signal.strip().lower(),
                    point.meaning.strip().lower(),
                )
                for point in record.decision_points
            ),
            tuple(
                (
                    item.vendor.strip().lower(),
                    tuple(command.strip().lower() for command in item.commands),
                )
                for item in record.command_templates
            ),
            tuple(item.strip().lower() for item in record.fallback_commands),
            tuple(item.strip().lower() for item in record.expected_findings),
        )

    def _choose_primary_record(self, rows: Iterable[SOPRecord]) -> SOPRecord:
        return sorted(
            list(rows),
            key=lambda item: (
                0 if item.is_current_published else 1,
                0 if item.status == SOPStatus.published else 1 if item.status == SOPStatus.draft else 2,
                -int(item.version or 0),
                -(item.updated_at.timestamp() if item.updated_at else 0),
                -(item.created_at.timestamp() if item.created_at else 0),
            ),
        )[0]

    def _max_datetime(self, values: Iterable[datetime | None]) -> datetime | None:
        cleaned = [item for item in values if item is not None]
        return max(cleaned) if cleaned else None

    def _normalize_topic_name(self, value: str | None, *, fallback: str | None = None) -> str:
        text = str(value or "").strip()
        if text:
            return text
        return str(fallback or "").strip() or "未命名主题"

    def _normalize_topic_key(self, value: str | None, *, fallback: str | None = None) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            raw = str(fallback or "").strip().lower()
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", raw).strip("-")
        return normalized or "sop-topic"

    def _apply_seeds(self, seeds: Iterable[SOPSeed]) -> None:
        with self._lock:
            changed = False
            for seed in seeds:
                existing = self._records.get(seed.id)
                if existing is not None:
                    if existing.generated_by_model == "seed" and existing.status != SOPStatus.published:
                        restored = seed.to_record()
                        restored.matched_count = existing.matched_count
                        restored.referenced_count = existing.referenced_count
                        restored.success_count = existing.success_count
                        restored.last_matched_at = existing.last_matched_at
                        restored.last_referenced_at = existing.last_referenced_at
                        restored.last_success_at = existing.last_success_at
                        restored.created_at = existing.created_at
                        restored.updated_at = now_utc()
                        self._records[seed.id] = restored
                        changed = True
                    continue
                self._records[seed.id] = seed.to_record()
                changed = True
            if changed:
                self._rebuild_source_index()
                self._write_snapshot()

    def _rebuild_source_index(self) -> None:
        self._source_run_index = defaultdict(set)
        for record in self._records.values():
            for source_run_id in record.source_run_ids:
                self._source_run_index[source_run_id].add(record.id)

    def _load(self) -> None:
        self._load_snapshot()
        self._replay_wal()
        self._migrate_loaded_records()
        self._remove_seed_records()
        self._rebuild_source_index()

    def _load_snapshot(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            records = data.get("records", []) if isinstance(data, dict) else []
            self._records = {
                item["id"]: SOPRecord.model_validate(item)
                for item in records
                if isinstance(item, dict) and item.get("id")
            }
        except Exception:
            self._records = {}

    def _replay_wal(self) -> None:
        if not self.wal_path.exists():
            return
        try:
            with self.wal_path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    self._apply_wal_event(event)
        except Exception:
            return

    def _apply_wal_event(self, event: dict) -> None:
        op = str(event.get("op") or "").strip()
        if op == "upsert":
            record_data = event.get("record")
            if isinstance(record_data, dict) and record_data.get("id"):
                record = SOPRecord.model_validate(record_data)
                self._records[record.id] = record
            return
        if op == "publish":
            record_id = str(event.get("record_id") or "").strip()
            record = self._records.get(record_id)
            if not record:
                return
            for archived_id in event.get("archived_record_ids", []) or []:
                archived = self._records.get(str(archived_id))
                if not archived:
                    continue
                archived.status = SOPStatus.archived
                archived.is_current_published = False
                self._records[archived.id] = archived
            record.status = SOPStatus.published
            record.is_current_published = True
            published_at = str(event.get("published_at") or "").strip()
            record.published_at = datetime.fromisoformat(published_at) if published_at else now_utc()
            self._records[record.id] = record
            return
        if op == "archive":
            record_id = str(event.get("record_id") or "").strip()
            record = self._records.get(record_id)
            if not record:
                return
            record.status = SOPStatus.archived
            record.is_current_published = False
            self._records[record.id] = record
            return
        if op == "delete":
            record_id = str(event.get("record_id") or "").strip()
            self._records.pop(record_id, None)
            return
        if op == "stats":
            kind = str(event.get("kind") or "").strip()
            for record_id in event.get("record_ids", []):
                record = self._records.get(str(record_id))
                if not record:
                    continue
                if kind == "matched":
                    record.matched_count += 1
                elif kind == "referenced":
                    record.referenced_count += 1
                elif kind == "success":
                    record.success_count += 1
                self._records[record.id] = record
            return
        if op == "cleanup_history":
            for record_id in event.get("deleted_record_ids", []) or []:
                self._records.pop(str(record_id), None)

    def _append_wal(self, event: dict) -> None:
        self.wal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.wal_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._write_snapshot()

    def _migrate_loaded_records(self) -> None:
        changed = False
        for record_id, record in list(self._records.items()):
            updated = record.model_copy(deep=True)
            if not updated.topic_name:
                updated.topic_name = updated.name
                changed = True
            if not updated.topic_key:
                updated.topic_key = self._normalize_topic_key(None, fallback=updated.topic_name or updated.name)
                changed = True
            if updated.status != SOPStatus.published and updated.is_current_published:
                updated.is_current_published = False
                changed = True
            self._records[record_id] = updated
        if changed:
            self._write_snapshot()

    def _remove_seed_records(self) -> None:
        seed_ids = {"history_generic_forensics", "history_ospf_flap"}
        removable = [
            record_id
            for record_id, record in self._records.items()
            if record_id in seed_ids
            or record.generated_by_model == "seed"
            or record.generated_by_prompt_version == "seed-v1"
        ]
        if not removable:
            return
        for record_id in removable:
            self._records.pop(record_id, None)
        self._write_snapshot()

    def _write_snapshot(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.snapshot_path.with_suffix(f"{self.snapshot_path.suffix}.tmp")
        payload = {
            "records": [record.model_dump(mode="json") for record in self._records.values()],
        }
        with tmp_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.snapshot_path)
