from __future__ import annotations

from collections import OrderedDict

INTENT_DESCRIPTIONS: dict[str, str] = OrderedDict(
    {
        "clock_check": "Check device clock and timezone.",
        "version_check": "Identify platform model/version and feature family.",
        "system_log_recent": "Collect recent system logs around fault window.",
        "interface_summary": "Collect interface up/down summary.",
        "interface_errors": "Collect interface errors/discards/counters.",
        "cpu_health": "Collect CPU usage and spikes.",
        "memory_health": "Collect memory usage and pressure.",
        "routing_summary": "Collect routing table summary/health.",
        "ospf_neighbor": "Collect OSPF neighbor/session state.",
        "bgp_summary": "Collect BGP session status.",
        "pan_session_stats": "Collect Palo Alto session/firewall dataplane stats.",
    }
)


def normalize_profile(vendor: str, os_family: str) -> str:
    v = str(vendor or "unknown").strip().lower()
    osf = str(os_family or "").strip().lower()
    if osf in {
        "huawei_vrp",
        "arista_eos",
        "cisco_iosxr",
        "cisco_nxos",
        "cisco_iosxe",
        "cisco_ios",
        "paloalto_panos",
    }:
        return osf
    if v == "huawei":
        return "huawei_vrp"
    if v == "arista":
        return "arista_eos"
    if v == "paloalto":
        return "paloalto_panos"
    if v == "cisco":
        return "cisco_iosxe"
    return "unknown"


_INTENT_COMMANDS: dict[str, dict[str, str]] = {
    "clock_check": {
        "default": "show clock",
        "huawei_vrp": "display clock",
        "paloalto_panos": "show clock",
    },
    "version_check": {
        "default": "show version",
        "huawei_vrp": "display version",
        "paloalto_panos": "show system info",
    },
    "system_log_recent": {
        "default": "show logging",
        "cisco_nxos": "show logging logfile",
        "huawei_vrp": "display logbuffer",
        "paloalto_panos": "show log system direction equal backward count equal 200",
    },
    "interface_summary": {
        "default": "show interfaces",
        "cisco_ios": "show ip interface brief",
        "cisco_iosxe": "show ip interface brief",
        "cisco_iosxr": "show interfaces brief",
        "cisco_nxos": "show interface brief",
        "arista_eos": "show interfaces status",
        "huawei_vrp": "display interface brief",
        "paloalto_panos": "show interface all",
    },
    "interface_errors": {
        "default": "show interfaces counters errors",
        "cisco_iosxr": "show interfaces",
        "cisco_nxos": "show interface counters errors",
        "huawei_vrp": "display interface brief",
        "paloalto_panos": "show counter global filter severity warn",
    },
    "cpu_health": {
        "default": "show processes cpu",
        "huawei_vrp": "display cpu-usage",
        "arista_eos": "show processes top once",
        "paloalto_panos": "show running resource-monitor",
    },
    "memory_health": {
        "default": "show processes memory",
        "cisco_iosxr": "show processes memory detail",
        "huawei_vrp": "display memory-usage",
        "paloalto_panos": "show system resources",
    },
    "routing_summary": {
        "default": "show ip route summary",
        "cisco_iosxr": "show route summary",
        "huawei_vrp": "display ip routing-table statistics",
        "paloalto_panos": "show routing route summary",
    },
    "ospf_neighbor": {
        "default": "show ip ospf neighbor",
        "huawei_vrp": "display ospf peer brief",
        "paloalto_panos": "show routing protocol ospf neighbor",
    },
    "bgp_summary": {
        "default": "show ip bgp summary",
        "huawei_vrp": "display bgp peer",
        "paloalto_panos": "show routing protocol bgp peer",
    },
    "pan_session_stats": {
        "default": "show log system",
        "paloalto_panos": "show session info",
    },
}

_PROFILE_DEFAULT_INTENTS: dict[str, list[str]] = {
    "huawei_vrp": ["clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "cpu_health"],
    "arista_eos": ["clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "cpu_health"],
    "cisco_iosxr": ["clock_check", "version_check", "system_log_recent", "interface_summary", "cpu_health", "routing_summary"],
    "cisco_nxos": ["clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "routing_summary"],
    "cisco_iosxe": ["clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "cpu_health"],
    "cisco_ios": ["clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "cpu_health"],
    "paloalto_panos": ["clock_check", "version_check", "system_log_recent", "interface_summary", "pan_session_stats", "cpu_health"],
    "unknown": ["clock_check", "version_check", "system_log_recent", "interface_summary"],
}


def allowed_intents_for_profile(profile: str) -> list[str]:
    p = str(profile or "unknown").strip().lower()
    return list(_PROFILE_DEFAULT_INTENTS.get(p, _PROFILE_DEFAULT_INTENTS["unknown"]))


def command_for_intent(intent: str, profile: str) -> str | None:
    i = str(intent or "").strip()
    p = str(profile or "unknown").strip().lower()
    mapping = _INTENT_COMMANDS.get(i)
    if not mapping:
        return None
    if p in mapping:
        return mapping[p]
    return mapping.get("default")


def default_plan_for_profile(profile: str, max_commands: int = 4) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for intent in allowed_intents_for_profile(profile):
        cmd = command_for_intent(intent, profile)
        if not cmd:
            continue
        out.append(
            {
                "intent": intent,
                "command": cmd,
                "reason": INTENT_DESCRIPTIONS.get(intent, "Collect evidence for diagnosis"),
                "expected_signal": INTENT_DESCRIPTIONS.get(intent, ""),
            }
        )
        if len(out) >= max(1, int(max_commands)):
            break
    return out
