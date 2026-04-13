from __future__ import annotations

from app.models.schemas import SOPArchiveEntryResponse, SOPArchiveResponse, SOPListResponse, SOPPublishResponse, SOPRecord, SOPStatus, SOPUpsertRequest
from app.services.sop_store import SOPSeed, SOPStore


SEED_SOPS: tuple[SOPSeed, ...] = (
    SOPSeed(
        id="history_generic_forensics",
        name="历史故障通用取证",
        topic_key="历史故障通用取证",
        topic_name="历史故障通用取证",
        summary="用于上次/历史/闪断/间歇类问题，优先建议日志、告警、时间线类取证。",
        usage_hint=(
            "这是可选 SOP 档案，不会被系统自动执行。"
            "AI 应先判断是否确属历史性问题，再自主选择其中的命令模板或自行改写为更合适的命令。"
        ),
        trigger_keywords=("上次", "历史", "曾经", "闪断", "抖动", "间歇", "flap", "history", "last", "intermittent"),
        evidence_goals=("日志证据", "告警证据", "时间线证据"),
        key_steps=(
            (1, "建立时间锚点", "先确认设备当前时间，避免历史日志时间轴错位。", ("show clock", "display clock"), ("获得当前时间",)),
            (2, "抓取历史日志", "优先读取日志与告警，确认是否存在可回溯的异常记录。", ("show logging | last 200", "display logbuffer", "display alarm active"), ("定位异常时间", "提取关联日志",)),
        ),
        decision_points=(
            ("日志中存在明确异常时间点", "后续排查应围绕该时间点的接口、协议、告警链继续收窄。"),
            ("日志为空或不可得", "不能直接下根因，需明确标注历史证据不足并转向当前状态取证。"),
        ),
        command_templates=(
            ("generic", ("show clock", "display clock", "show logging | last 200", "display logbuffer", "display alarm active")),
        ),
        expected_findings=("定位异常发生时间", "提取相邻告警或日志", "为后续协议/接口取证建立时间锚点"),
    ),
    SOPSeed(
        id="history_ospf_flap",
        name="OSPF 历史抖动取证",
        topic_key="OSPF 历史抖动取证",
        topic_name="OSPF 历史抖动取证",
        summary="用于 OSPF 闪断/邻接抖动，优先查看协议日志、邻接变化和接口关联证据。",
        usage_hint=(
            "只有当用户问题明确涉及 OSPF 的历史抖动或上次故障时才考虑调用。"
            "AI 需要先判断当前设备厂商，再从模板中挑选最合适的最小命令组。"
        ),
        trigger_keywords=("ospf", "闪断", "抖动", "flap", "history", "last", "上次", "历史"),
        vendor_tags=("huawei", "arista"),
        evidence_goals=("协议事件日志", "邻接变化", "接口关联状态"),
        key_steps=(
            (1, "抓协议历史日志", "先确认 OSPF 是否出现过 down/up、邻接 flap 等记录。", ("display logbuffer | include OSPF|DOWN|UP", "show logging | include OSPF|ADJ|DOWN|UP"), ("出现 OSPF 事件",)),
            (2, "确认当前邻接状态", "再检查当前邻接是否恢复，用于区分历史问题和当前问题。", ("display ospf peer", "show ip ospf neighbor"), ("当前邻接状态",)),
        ),
        decision_points=(
            ("日志有 flap，但当前邻接正常", "更偏向历史抖动已恢复，应补链路或告警时间线证据。"),
            ("日志无 flap 且当前邻接也异常", "说明更可能是当前持续性配置/链路问题。"),
        ),
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
