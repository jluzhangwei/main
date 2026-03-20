from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class PlaybookStep:
    title: str
    command: str


def infer_intents(message: str) -> list[str]:
    lowered = message.lower()
    intents: list[str] = []

    if any(token in lowered for token in ["ping", "不通", "丢包", "connect", "reachable"]):
        intents.append("connectivity")
    if any(token in lowered for token in ["接口", "interface", "port", "链路", "端口", "ethernet", "gigabitethernet"]):
        intents.append("interface")
    if any(token in lowered for token in ["路由", "route", "bgp", "ospf", "next-hop"]):
        intents.append("routing")

    if not intents:
        intents = ["connectivity", "interface", "routing"]

    return list(dict.fromkeys(intents))


def build_playbook(message: str, vendor: str | None = None) -> list[PlaybookStep]:
    intents = infer_intents(message)
    steps: list[PlaybookStep] = []
    target_interface = extract_interface_target(message)

    if "connectivity" in intents:
        steps.extend(
            [
                PlaybookStep("连通性检查", "ping 8.8.8.8"),
                PlaybookStep("ARP 检查", "show arp"),
            ]
        )

    if "interface" in intents:
        steps.extend(
            [
                PlaybookStep("接口状态", "show ip interface brief"),
                PlaybookStep("接口详情", "show interface status"),
            ]
        )
        if target_interface:
            steps.append(PlaybookStep("接口配置检查", f"show running-config interface {target_interface}"))

    if "routing" in intents:
        steps.extend(
            [
                PlaybookStep("路由表概览", "show ip route"),
                PlaybookStep("默认路由检查", "show ip route 0.0.0.0"),
            ]
        )

    lowered = message.lower()
    no_fix = any(
        token in lowered
        for token in [
            "不要修复",
            "不要执行修复",
            "仅诊断",
            "只诊断",
            "不要变更",
            "no fix",
            "read only",
        ]
    )
    if any(token in lowered for token in ["修复", "fix", "自动处理", "apply"]) and not no_fix:
        steps.append(PlaybookStep("建议修复动作", build_repair_command(vendor)))

    return steps


def build_repair_command(vendor: str | None = None) -> str:
    normalized = (vendor or "").strip().lower()

    if "huawei" in normalized:
        return "system-view ; interface GigabitEthernet0/0/1 ; undo shutdown"

    return "configure terminal ; interface GigabitEthernet0/0/1 ; no shutdown"


def extract_interface_target(message: str) -> str | None:
    patterns = (
        r"\b(ethernet|eth)\s*(\d+(?:/\d+)+(?:\.\d+)?)\b",
        r"\b(gigabitethernet)\s*(\d+(?:/\d+)+(?:\.\d+)?)\b",
        r"\b(xgigabitethernet)\s*(\d+(?:/\d+)+(?:\.\d+)?)\b",
        r"\b(ge)\s*(\d+(?:/\d+)+(?:\.\d+)?)\b",
    )
    lowered = message.lower()

    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        family = match.group(1).lower()
        suffix = match.group(2)
        if family in {"ethernet", "eth"}:
            return f"Ethernet{suffix}"
        if family == "gigabitethernet":
            return f"GigabitEthernet{suffix}"
        if family == "xgigabitethernet":
            return f"XGigabitEthernet{suffix}"
        if family == "ge":
            return f"GE{suffix}"

    return None
