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
    parsed: dict[str, Any] = {
        "device_name": device_name,
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
    ]
    if not any(marker in lowered for marker in markers):
        return None

    for line in output.splitlines():
        if "error:" in line.lower() or "invalid input" in line.lower() or "unknown command" in line.lower():
            return line.strip()
    return "CLI command parsing failed"


def _extract_device_name_from_version(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]

    # Prompt-style name, e.g. <NE40E-1> or [R1]
    for line in lines:
        match = re.match(r"^[<\[]([A-Za-z0-9._-]{1,64})[>\]]$", line)
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
