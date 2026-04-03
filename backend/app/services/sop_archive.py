from __future__ import annotations

from app.models.schemas import SOPArchiveEntryResponse, SOPArchiveResponse, SOPListResponse, SOPPublishResponse, SOPRecord, SOPStatus, SOPUpsertRequest
from app.services.sop_store import SOPSeed, SOPStore


SEED_SOPS: tuple[SOPSeed, ...] = (
    SOPSeed(
        id="history_generic_forensics",
        name="历史故障通用取证",
        summary="用于上次/历史/闪断/间歇类问题，优先建议日志、告警、时间线类取证。",
        usage_hint=(
            "这是可选 SOP 档案，不会被系统自动执行。"
            "AI 应先判断是否确属历史性问题，再自主选择其中的命令模板或自行改写为更合适的命令。"
        ),
        trigger_keywords=("上次", "历史", "曾经", "闪断", "抖动", "间歇", "flap", "history", "last", "intermittent"),
        evidence_goals=("日志证据", "告警证据", "时间线证据"),
        command_templates=(
            ("generic", ("show clock", "display clock", "show logging | last 200", "display logbuffer", "display alarm active")),
        ),
        expected_findings=("定位异常发生时间", "提取相邻告警或日志", "为后续协议/接口取证建立时间锚点"),
    ),
    SOPSeed(
        id="history_ospf_flap",
        name="OSPF 历史抖动取证",
        summary="用于 OSPF 闪断/邻接抖动，优先查看协议日志、邻接变化和接口关联证据。",
        usage_hint=(
            "只有当用户问题明确涉及 OSPF 的历史抖动或上次故障时才考虑调用。"
            "AI 需要先判断当前设备厂商，再从模板中挑选最合适的最小命令组。"
        ),
        trigger_keywords=("ospf", "闪断", "抖动", "flap", "history", "last", "上次", "历史"),
        vendor_tags=("huawei", "arista"),
        evidence_goals=("协议事件日志", "邻接变化", "接口关联状态"),
        command_templates=(
            ("huawei", ("display logbuffer | include OSPF|DOWN|UP", "display ospf peer", "display alarm active | include OSPF")),
            ("arista", ("show logging | include OSPF|ADJ|DOWN|UP", "show ip ospf neighbor", "show interfaces status")),
            ("generic", ("show logging | include OSPF|DOWN|UP", "show ospf neighbor", "display ospf peer")),
        ),
        expected_findings=("确认历史 flap 发生时间", "确认邻接是否反复 up/down", "提取接口或告警关联线索"),
    ),
)


class SOPArchive:
    def __init__(self) -> None:
        self.store = SOPStore(seeds=SEED_SOPS)

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
            lines.append(f"  summary: {item.summary}")
            lines.append(f"  usage_hint: {item.usage_hint}")
            if item.preconditions:
                lines.append(f"  preconditions: {' ; '.join(item.preconditions)}")
            if item.anti_conditions:
                lines.append(f"  anti_conditions: {' ; '.join(item.anti_conditions)}")
            if item.evidence_goals:
                lines.append(f"  evidence_goals: {' ; '.join(item.evidence_goals)}")
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
