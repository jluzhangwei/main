from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Iterable

from app.models.schemas import (
    SOPArchiveCommandTemplate,
    SOPArchiveEntryResponse,
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
    vendor_tags: tuple[str, ...] = ()
    version_signatures: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    anti_conditions: tuple[str, ...] = ()
    evidence_goals: tuple[str, ...] = ()
    command_templates: tuple[tuple[str, tuple[str, ...]], ...] = ()
    fallback_commands: tuple[str, ...] = ()
    expected_findings: tuple[str, ...] = ()

    def to_record(self) -> SOPRecord:
        return SOPRecord(
            id=self.id,
            version=1,
            status=SOPStatus.published,
            name=self.name,
            summary=self.summary,
            usage_hint=self.usage_hint,
            trigger_keywords=list(self.trigger_keywords),
            vendor_tags=list(self.vendor_tags),
            version_signatures=list(self.version_signatures),
            preconditions=list(self.preconditions),
            anti_conditions=list(self.anti_conditions),
            evidence_goals=list(self.evidence_goals),
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
        home = os.path.expanduser("~")
        if home and home != "~":
            return Path(home) / ".netops-ai-v1" / "sop_snapshot.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_sop_snapshot.json"

    @staticmethod
    def default_wal_path() -> Path:
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
            if existing:
                record = existing.model_copy(deep=True)
            else:
                record = SOPRecord(
                    id=record_id,
                    name=payload.name.strip(),
                    summary=payload.summary.strip(),
                    usage_hint=payload.usage_hint.strip(),
                )
            record.name = payload.name.strip()
            record.summary = payload.summary.strip()
            record.usage_hint = payload.usage_hint.strip()
            record.trigger_keywords = self._normalize_text_list(payload.trigger_keywords)
            record.vendor_tags = self._normalize_text_list(payload.vendor_tags)
            record.version_signatures = self._normalize_text_list(payload.version_signatures)
            record.preconditions = self._normalize_text_list(payload.preconditions)
            record.anti_conditions = self._normalize_text_list(payload.anti_conditions)
            record.evidence_goals = self._normalize_text_list(payload.evidence_goals)
            record.command_templates = [item.model_copy(deep=True) for item in payload.command_templates]
            record.fallback_commands = self._normalize_text_list(payload.fallback_commands)
            record.expected_findings = self._normalize_text_list(payload.expected_findings)
            record.source_run_ids = self._normalize_text_list(payload.source_run_ids)
            record.generated_by_model = (payload.generated_by_model or "").strip() or None
            record.generated_by_prompt_version = (payload.generated_by_prompt_version or "").strip() or None
            record.review_notes = (payload.review_notes or "").strip() or None
            if status is not None:
                record.status = status
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
            record.status = SOPStatus.published
            record.published_at = now_utc()
            record.updated_at = now_utc()
            self._records[record.id] = record
            self._append_wal({"op": "publish", "record_id": record_id, "published_at": record.published_at.isoformat()})
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
            record.updated_at = now_utc()
            self._records[record.id] = record
            self._append_wal({"op": "archive", "record_id": record_id})
            return SOPPublishResponse(
                item=record.to_archive_response(),
                previous_status=previous_status,
                current_status=record.status,
            )

    def delete_record(self, record_id: str) -> bool:
        with self._lock:
            if record_id not in self._records:
                return False
            self._records.pop(record_id, None)
            self._rebuild_source_index()
            self._append_wal({"op": "delete", "record_id": record_id})
            return True

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

    def _matches_record(self, record: SOPRecord, *, problem: str, vendor: str | None, version_signature: str | None) -> bool:
        lowered = str(problem or "").strip().lower()
        if not lowered:
            return False
        if record.trigger_keywords and not any(token.lower() in lowered for token in record.trigger_keywords):
            return False
        normalized_vendor = str(vendor or "").strip().lower()
        if record.vendor_tags and normalized_vendor and not any(token.lower() in normalized_vendor for token in record.vendor_tags):
            return False
        normalized_signature = str(version_signature or "").strip().lower()
        if record.version_signatures and normalized_signature:
            if not any(token.lower() in normalized_signature for token in record.version_signatures):
                return False
        if record.preconditions and not all(token.lower() in lowered for token in record.preconditions):
            return False
        if record.anti_conditions and any(token.lower() in lowered for token in record.anti_conditions):
            return False
        return True

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

    def _apply_seeds(self, seeds: Iterable[SOPSeed]) -> None:
        with self._lock:
            changed = False
            for seed in seeds:
                if seed.id in self._records:
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
            record.status = SOPStatus.published
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

    def _append_wal(self, event: dict) -> None:
        self.wal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.wal_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
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
