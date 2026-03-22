from __future__ import annotations

import re
from typing import Any


def parse_command_output(command: str, output: str) -> tuple[str, dict[str, Any], str]:
    cmd = command.lower().strip()
    cli_error = _extract_cli_error(output)
    if cli_error:
        return (
            "command_error",
            {"cli_error": True, "reason": cli_error},
            f"Command execution failed: {cli_error}",
        )

    if "version" in cmd:
        return _parse_version(output)
    if "running-config interface" in cmd or "current-configuration interface" in cmd:
        return _parse_interface_config(command, output)
    if "interface" in cmd:
        return _parse_interface(output)
    if "route" in cmd:
        return _parse_route(output)
    if cmd.startswith("ping"):
        return _parse_ping(output)

    return (
        "generic",
        {"raw": output.strip()[:2000]},
        "Command executed. Review raw output for details.",
    )


def _parse_version(output: str) -> tuple[str, dict[str, Any], str]:
    device_name = _extract_device_name_from_version(output)
    vendor, platform, software_version = _extract_version_profile(output)
    version_signature = _build_version_signature(vendor, platform, software_version)
    parsed: dict[str, Any] = {
        "device_name": device_name,
        "vendor": vendor,
        "platform": platform,
        "software_version": software_version,
        "version_signature": version_signature,
        "raw": output.strip()[:2000],
    }
    if device_name:
        return (
            "version",
            parsed,
            f"Detected device name from version output: {device_name}",
        )
    return (
        "version",
        parsed,
        "Version information collected; device name not detected from current output.",
    )


def _parse_interface(output: str) -> tuple[str, dict[str, Any], str]:
    down_interfaces: list[str] = []
    admin_down_interfaces: list[str] = []

    for line in output.splitlines():
        lowered = line.lower()
        if not line.strip() or "interface" in lowered:
            continue
        if "down" not in lowered and "*down" not in lowered:
            continue

        name = _extract_interface_name_from_line(line)
        if not name:
            continue

        down_interfaces.append(name)
        if "administratively down" in lowered or "*down" in lowered or "admin down" in lowered:
            admin_down_interfaces.append(name)

    down_interfaces = _unique(down_interfaces)
    admin_down_interfaces = _unique(admin_down_interfaces)

    if admin_down_interfaces:
        return (
            "interface",
            {
                "down_interfaces": down_interfaces,
                "admin_down_interfaces": admin_down_interfaces,
            },
            f"Detected administratively down interfaces: {', '.join(admin_down_interfaces)}",
        )

    if down_interfaces:
        return (
            "interface",
            {
                "down_interfaces": down_interfaces,
                "admin_down_interfaces": [],
            },
            f"Detected down interfaces: {', '.join(down_interfaces)}",
        )

    return (
        "interface",
        {
            "down_interfaces": [],
            "admin_down_interfaces": [],
        },
        "No down interfaces were detected from current output.",
    )


def _parse_interface_config(command: str, output: str) -> tuple[str, dict[str, Any], str]:
    command_lower = command.lower()
    interface_name = ""
    match = re.search(r"interface\s+([a-zA-Z]+(?:\s*)\d+(?:/\d+)+(?:\.\d+)?)", command_lower)
    if match:
        interface_name = _normalize_interface_name(match.group(1))

    has_shutdown_line = bool(re.search(r"(?mi)^\s*shutdown\s*$", output))
    has_undo_shutdown_line = bool(re.search(r"(?mi)^\s*undo shutdown\s*$", output))
    has_admin_down_hint = "administratively down" in output.lower()
    admin_shutdown = (has_shutdown_line and not has_undo_shutdown_line) or has_admin_down_hint

    if admin_shutdown:
        conclusion = (
            f"Interface {interface_name or 'target interface'} is administratively shutdown by configuration."
        )
    else:
        conclusion = (
            f"Interface {interface_name or 'target interface'} config does not show administrative shutdown."
        )

    return (
        "interface",
        {
            "interface": interface_name or None,
            "admin_shutdown": admin_shutdown,
            "has_shutdown_line": has_shutdown_line,
            "has_undo_shutdown_line": has_undo_shutdown_line,
        },
        conclusion,
    )


def _parse_route(output: str) -> tuple[str, dict[str, Any], str]:
    static_routes = len(re.findall(r"\bS\b|static", output, flags=re.IGNORECASE))
    missing_default = "0.0.0.0/0" not in output and "Gateway of last resort" not in output

    if missing_default:
        conclusion = "Default route appears missing; routing may fail for external destinations."
    else:
        conclusion = "Routing table contains default-route indicators."

    return (
        "routing",
        {"static_route_hits": static_routes, "missing_default_route": missing_default},
        conclusion,
    )


def _parse_ping(output: str) -> tuple[str, dict[str, Any], str]:
    success = bool(re.search(r"0% packet loss|Success rate is 100 percent|bytes from", output, flags=re.IGNORECASE))
    loss_100 = bool(re.search(r"100% packet loss|Success rate is 0 percent", output, flags=re.IGNORECASE))

    if success:
        return (
            "connectivity",
            {"success": True, "packet_loss_100": False},
            "Ping succeeded; base connectivity looks healthy.",
        )

    if loss_100:
        return (
            "connectivity",
            {"success": False, "packet_loss_100": True},
            "Ping failed with full packet loss; continue interface and route checks.",
        )

    return (
        "connectivity",
        {"success": False, "packet_loss_100": False},
        "Ping result uncertain; manual verification recommended.",
    )


def _extract_interface_name_from_line(line: str) -> str | None:
    match = re.search(
        r"\b(ethernet|eth|gigabitethernet|xgigabitethernet|ge)\s*(\d+(?:/\d+)+(?:\.\d+)?)\b",
        line,
        flags=re.IGNORECASE,
    )
    if match:
        family = match.group(1).lower()
        suffix = match.group(2)
        if family in {"ethernet", "eth"}:
            return f"Ethernet{suffix}"
        if family == "gigabitethernet":
            return f"GigabitEthernet{suffix}"
        if family == "xgigabitethernet":
            return f"XGigabitEthernet{suffix}"
        return f"GE{suffix}"

    first_token = line.strip().split()[0]
    if "/" not in first_token:
        return None
    return _normalize_interface_name(first_token)


def _normalize_interface_name(name: str) -> str:
    compact = re.sub(r"\s+", "", name.strip())
    if not compact:
        return compact
    return compact[0].upper() + compact[1:]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _extract_cli_error(output: str) -> str | None:
    lowered = output.lower()
    markers = [
        "error: unrecognized command",
        "error: wrong parameter",
        "error: incomplete command",
        "unknown command",
        "invalid input detected",
        "% invalid input",
        "invalid input",
    ]
    if not any(marker in lowered for marker in markers):
        return None

    for line in output.splitlines():
        normalized = line.lower()
        if "error:" in normalized or "invalid input" in normalized or "unknown command" in normalized:
            return line.strip()
    return "CLI command parsing failed"


def _extract_device_name_from_version(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    # Prompt-style name, e.g. <NE40E-1> or [R1]
    for line in lines:
        match = re.match(r"^[<\[]([A-Za-z0-9._-]{1,64})[>\]]$", line)
        if match:
            return match.group(1)

    # Common prompt forms, e.g. "Device-102#" / "Device-102(config)#show version"
    for line in lines:
        match = re.match(r"^([A-Za-z0-9._-]{1,64})(?:\([^)]+\))?[>#]\s*(?:.*)?$", line)
        if match:
            return match.group(1)

    # Typical hostname line: "R1 uptime is ..."
    for line in lines:
        match = re.match(r"^([A-Za-z0-9._-]{1,64})\s+(?:system\s+)?uptime\s+is\b", line, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    # Explicit naming fields
    patterns = [
        r"(?i)\bhostname\s*[:=]\s*([A-Za-z0-9._-]{1,64})\b",
        r"(?i)\bsysname\s*[:=]\s*([A-Za-z0-9._-]{1,64})\b",
        r"(?i)\bsystem\s+name\s*[:=]\s*([A-Za-z0-9._-]{1,64})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)

    return None


def _extract_version_profile(output: str) -> tuple[str | None, str | None, str | None]:
    text = output or ""
    lowered = text.lower()

    vendor: str | None = None
    if "huawei" in lowered or "vrp" in lowered:
        vendor = "huawei"
    elif "arista" in lowered or "eos" in lowered:
        vendor = "arista"
    elif "cisco" in lowered or "ios xe" in lowered or "ios xr" in lowered:
        vendor = "cisco"

    platform: str | None = None
    software_version: str | None = None

    # Huawei examples:
    # VRP (R) software, Version 8.180 (NE40E V800R011C00SPC607B607)
    # HUAWEI NE40E uptime is ...
    huawei_version = re.search(r"(?i)\bversion\s+([A-Za-z0-9._-]+)\s*\(([^)]+)\)", text)
    if huawei_version:
        software_version = huawei_version.group(1).strip()
        detail = huawei_version.group(2).strip()
        platform_match = re.search(r"\b([A-Za-z]+[0-9][A-Za-z0-9-]*)\b", detail)
        if platform_match:
            platform = platform_match.group(1)
    if not platform:
        huawei_platform = re.search(r"(?im)^\s*huawei\s+([A-Za-z0-9._-]+)\s+uptime\s+is", text)
        if huawei_platform:
            platform = huawei_platform.group(1).strip()

    # Arista examples:
    # Arista vEOS-lab
    # Software image version: 4.29.2F
    if not platform:
        arista_platform = re.search(r"(?im)^\s*arista\s+([A-Za-z0-9._-]+)\s*$", text)
        if arista_platform:
            platform = arista_platform.group(1).strip()
    if not software_version:
        arista_version = re.search(r"(?im)^\s*software image version:\s*([A-Za-z0-9._-]+)\s*$", text)
        if arista_version:
            software_version = arista_version.group(1).strip()

    # Cisco-like examples:
    # Cisco IOS Software, ... Version 15.2(4)M7, ...
    if not software_version:
        cisco_version = re.search(r"(?i)\bversion\s+([A-Za-z0-9()._-]+)", text)
        if cisco_version:
            software_version = cisco_version.group(1).strip().rstrip(",")
    if not platform:
        cisco_platform = re.search(r"(?i)\bcisco\s+([A-Za-z0-9._-]+)\s+\(", text)
        if cisco_platform:
            platform = cisco_platform.group(1).strip()

    return (vendor, platform, software_version)


def _build_version_signature(vendor: str | None, platform: str | None, software_version: str | None) -> str | None:
    vendor_text = (vendor or "").strip().lower()
    platform_text = (platform or "").strip().lower()
    version_text = (software_version or "").strip().lower()
    if not vendor_text and not platform_text and not version_text:
        return None
    return "|".join([vendor_text, platform_text, version_text])
