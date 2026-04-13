from __future__ import annotations

from app.models.schemas import SOPArchiveEntryResponse, SOPArchiveResponse, SOPListResponse, SOPPublishResponse, SOPRecord, SOPStatus, SOPUpsertRequest
from app.services.sop_store import SOPStore


class SOPArchive:
    def __init__(self) -> None:
        self.store = SOPStore()

    def list_entries(self, status: SOPStatus | None = SOPStatus.published) -> list[SOPArchiveEntryResponse]:
        return self.store.list_records(status=status).items

    def list_records(self, status: SOPStatus | None = None) -> SOPListResponse:
        return self.store.list_records(status=status)

    def get_record(self, record_id: str) -> SOPRecord:
        return self.store.get_record(record_id)

    def upsert_record(
        self,
        record_id: str,
        payload: SOPUpsertRequest,
        *,
        status: SOPStatus | None = None,
        version: int | None = None,
    ) -> SOPRecord:
        return self.store.upsert_record(record_id, payload, status=status, version=version)

    def publish_record(self, record_id: str) -> SOPPublishResponse:
        return self.store.publish_record(record_id)

    def archive_record(self, record_id: str) -> SOPPublishResponse:
        return self.store.archive_record(record_id)

    def delete_record(self, record_id: str) -> bool:
        return self.store.delete_record(record_id)

    def has_source_run(self, source_run_id: str) -> bool:
        return self.store.has_source_run(source_run_id)

    def source_run_counts(self, source_run_id: str) -> tuple[bool, int, int]:
        return self.store.source_run_counts(source_run_id)

    def referenced_entries(
        self,
        text: str | None = None,
        *,
        refs: list[str] | None = None,
        run_key: str | None = None,
    ) -> list[SOPArchiveEntryResponse]:
        return self.store.referenced_entries(refs=refs or [], text=text, run_key=run_key)

    def matched_entries(
        self,
        problem: str,
        vendor: str | None = None,
        *,
        version_signature: str | None = None,
        run_key: str | None = None,
    ) -> list[SOPArchiveEntryResponse]:
        return self.store.matched_entries(
            problem=problem,
            vendor=vendor,
            version_signature=version_signature,
            run_key=run_key,
        )

    def response(
        self,
        problem: str | None = None,
        vendor: str | None = None,
        *,
        version_signature: str | None = None,
    ) -> SOPArchiveResponse:
        items = self.list_entries(status=SOPStatus.published)
        matched = self.matched_entries(problem or "", vendor, version_signature=version_signature)
        return SOPArchiveResponse(total=len(items), matched=matched, items=items)

    def prompt_context(
        self,
        problem: str,
        vendor: str | None = None,
        *,
        version_signature: str | None = None,
        run_key: str | None = None,
    ) -> str:
        matched = self.store.dedupe_entries_for_runtime(self.matched_entries(
            problem,
            vendor,
            version_signature=version_signature,
            run_key=run_key,
        ))
        if not matched:
            return ""
        lines = [
            "SOP档案候选（仅供AI按需调用，系统不会自动执行）：",
        ]
        for item in matched:
            lines.append(f"- {item.id}: {item.name} (v{item.version})")
            lines.append(f"  topic: {item.topic_name} [{item.topic_key}]")
            lines.append(f"  summary: {item.summary}")
            lines.append(f"  usage_hint: {item.usage_hint}")
            if item.preconditions:
                lines.append(f"  preconditions: {' ; '.join(item.preconditions)}")
            if item.anti_conditions:
                lines.append(f"  anti_conditions: {' ; '.join(item.anti_conditions)}")
            if item.evidence_goals:
                lines.append(f"  evidence_goals: {' ; '.join(item.evidence_goals)}")
            for step in item.key_steps:
                lines.append(f"  key_step[{step.step_no}]: {step.title} -> {step.goal}")
                if step.commands:
                    lines.append(f"    commands: {' ; '.join(step.commands)}")
                if step.expected_signals:
                    lines.append(f"    expected_signals: {' ; '.join(step.expected_signals)}")
            for decision in item.decision_points:
                lines.append(f"  decision_point: {decision.signal} => {decision.meaning}")
            for template in item.command_templates:
                lines.append(f"  template[{template.vendor}]: {' ; '.join(template.commands)}")
            if item.fallback_commands:
                lines.append(f"  fallback_commands: {' ; '.join(item.fallback_commands)}")
        lines.append("若你决定调用某个SOP档案，请在sop_refs中返回引用的SOP id，并在reason中说明原因，再自行决定真正执行的命令。")
        return "\n".join(lines)

    def prompt_policy(self) -> str:
        return (
            "运行期 SOP 档案策略："
            "系统可提供已发布 SOP 档案候选，但不会自动执行；"
            "AI 必须先判断问题是否适合调用 SOP，再自主选择是否使用、使用哪条、以及如何改写为当前设备最合适的命令；"
            "若调用 SOP，优先在sop_refs中结构化返回引用的SOP id；"
            "若未调用 SOP，应继续按常规证据链自主规划。"
        )

    def record_outcome(self, run_key: str, *, success: bool) -> bool:
        return self.store.record_outcome(run_key, success=success)
