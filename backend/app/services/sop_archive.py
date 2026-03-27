from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import SOPArchiveCommandTemplate, SOPArchiveEntryResponse, SOPArchiveResponse


@dataclass(frozen=True)
class SOPArchiveEntry:
    id: str
    name: str
    summary: str
    usage_hint: str
    trigger_keywords: tuple[str, ...] = ()
    vendor_keywords: tuple[str, ...] = ()
    command_templates: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def matches(self, problem: str, vendor: str | None = None) -> bool:
        lowered = str(problem or "").strip().lower()
        if not lowered:
            return False
        if self.trigger_keywords and not any(token in lowered for token in self.trigger_keywords):
            return False
        if self.vendor_keywords:
            normalized_vendor = str(vendor or "").strip().lower()
            if normalized_vendor and not any(token in normalized_vendor for token in self.vendor_keywords):
                return False
        return True

    def to_response(self) -> SOPArchiveEntryResponse:
        return SOPArchiveEntryResponse(
            id=self.id,
            name=self.name,
            summary=self.summary,
            usage_hint=self.usage_hint,
            trigger_keywords=list(self.trigger_keywords),
            command_templates=[
                SOPArchiveCommandTemplate(vendor=vendor, commands=list(commands))
                for vendor, commands in self.command_templates
            ],
        )


class SOPArchive:
    def __init__(self) -> None:
        self._entries: tuple[SOPArchiveEntry, ...] = (
            SOPArchiveEntry(
                id="history_generic_forensics",
                name="历史故障通用取证",
                summary="用于上次/历史/闪断/间歇类问题，优先建议日志、告警、时间线类取证。",
                usage_hint=(
                    "这是可选 SOP 档案，不会被系统自动执行。"
                    "AI 应先判断是否确属历史性问题，再自主选择其中的命令模板或自行改写为更合适的命令。"
                ),
                trigger_keywords=("上次", "历史", "曾经", "闪断", "抖动", "间歇", "flap", "history", "last", "intermittent"),
                command_templates=(
                    ("generic", ("show clock", "display clock", "show logging | last 200", "display logbuffer", "display alarm active")),
                ),
            ),
            SOPArchiveEntry(
                id="history_ospf_flap",
                name="OSPF 历史抖动取证",
                summary="用于 OSPF 闪断/邻接抖动，优先查看协议日志、邻接变化和接口关联证据。",
                usage_hint=(
                    "只有当用户问题明确涉及 OSPF 的历史抖动或上次故障时才考虑调用。"
                    "AI 需要先判断当前设备厂商，再从模板中挑选最合适的最小命令组。"
                ),
                trigger_keywords=("ospf", "闪断", "抖动", "flap", "history", "last", "上次", "历史"),
                command_templates=(
                    ("huawei", ("display logbuffer | include OSPF|DOWN|UP", "display ospf peer", "display alarm active | include OSPF")),
                    ("arista", ("show logging | include OSPF|ADJ|DOWN|UP", "show ip ospf neighbor", "show interfaces status")),
                    ("generic", ("show logging | include OSPF|DOWN|UP", "show ospf neighbor", "display ospf peer")),
                ),
            ),
        )

    def list_entries(self) -> list[SOPArchiveEntryResponse]:
        return [entry.to_response() for entry in self._entries]

    def matched_entries(self, problem: str, vendor: str | None = None) -> list[SOPArchiveEntryResponse]:
        return [entry.to_response() for entry in self._entries if entry.matches(problem, vendor)]

    def response(self, problem: str | None = None, vendor: str | None = None) -> SOPArchiveResponse:
        items = self.list_entries()
        matched = self.matched_entries(problem or "", vendor)
        return SOPArchiveResponse(total=len(items), matched=matched, items=items)

    def prompt_context(self, problem: str, vendor: str | None = None) -> str:
        matched = self.matched_entries(problem, vendor)
        if not matched:
            return ""
        lines = [
            "SOP档案候选（仅供AI按需调用，系统不会自动执行）：",
        ]
        for item in matched:
            lines.append(f"- {item.id}: {item.name}")
            lines.append(f"  summary: {item.summary}")
            lines.append(f"  usage_hint: {item.usage_hint}")
            for template in item.command_templates:
                lines.append(f"  template[{template.vendor}]: {' ; '.join(template.commands)}")
        lines.append("若你决定调用某个SOP档案，请在reason中说明引用了哪个SOP，并自行决定具体命令与执行顺序。")
        return "\n".join(lines)

    def prompt_policy(self) -> str:
        return (
            "运行期 SOP 档案策略："
            "系统可提供历史问题 SOP 档案候选，但不会自动执行；"
            "AI 必须先判断问题是否适合调用 SOP，再自主选择是否使用、使用哪条、以及如何改写为当前设备最合适的命令；"
            "若未调用 SOP，应继续按常规证据链自主规划。"
        )
