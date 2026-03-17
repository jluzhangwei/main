from __future__ import annotations

import re
from typing import Any


def _profile_vendor(profile: str) -> str:
    p = str(profile or "").strip().lower()
    if "_" in p:
        return p.split("_", 1)[0]
    return p or "unknown"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp_weight(v: float, floor: float = 0.01, cap: float = 0.35) -> float:
    return max(floor, min(cap, float(v)))


def _looks_like_cli_error(output: str) -> bool:
    low = str(output or "").lower()
    lines = [ln.strip().lower() for ln in str(output or "").splitlines() if ln.strip()]
    return (
        "unrecognized command" in low
        or "invalid command at '^' marker" in low
        or "% invalid command" in low
        or "% invalid input" in low
        or "% incomplete command" in low
        or "wrong parameter found" in low
        or ("^" in lines and any("invalid" in ln and "command" in ln for ln in lines))
    )


def _add_signal(
    out: list[dict[str, Any]],
    *,
    device_id: str,
    vendor: str,
    command: str,
    domain: str,
    polarity: str,
    signal: str,
    weight: float,
    detail: str,
) -> None:
    out.append(
        {
            "device_id": device_id,
            "vendor": vendor,
            "command": command,
            "domain": domain,
            "polarity": "positive" if str(polarity).lower() != "negative" else "negative",
            "signal": signal,
            "weight": round(_clamp_weight(weight), 4),
            "detail": str(detail or "").strip()[:280],
        }
    )


def _parse_interface_brief_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    text = str(output_text or "")
    low = text.lower()
    out: list[dict[str, Any]] = []

    if vendor == "huawei":
        line_re = re.compile(
            r"^\s*(\S+)\s+([*^]?(?:up|down))\s+([*^]?(?:up|down))\s+\S+\s+\S+\s+(\d+)\s+(\d+)\s*$",
            re.I,
        )
        down_phy = 0
        err_if = 0
        admin_down_ports: list[str] = []
        for ln in text.splitlines():
            m = line_re.match(ln)
            if not m:
                continue
            if_name = str(m.group(1) or "").strip()
            phy_raw = (m.group(2) or "").lower()
            phy = phy_raw.lstrip("*^")
            in_err = _safe_int(m.group(4), 0)
            out_err = _safe_int(m.group(5), 0)
            if phy == "down":
                down_phy += 1
            if phy_raw.startswith("*") and if_name and "." not in if_name:
                admin_down_ports.append(if_name)
            if in_err > 0 or out_err > 0:
                err_if += 1
        if err_if > 0:
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="positive",
                signal="huawei_interface_error_counter_nonzero",
                weight=min(0.30, 0.08 + 0.05 * err_if),
                detail=f"Interfaces with non-zero in/out errors: {err_if}",
            )
        if down_phy > 0 and err_if <= 0:
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="negative",
                signal="huawei_down_ports_without_errors",
                weight=min(0.15, 0.03 + 0.003 * down_phy),
                detail=f"Down PHY ports observed without error growth: {down_phy}",
            )
        if admin_down_ports:
            uniq_admin = sorted(set(admin_down_ports))
            shown = ", ".join(uniq_admin[:5])
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="positive",
                signal="interface_admin_down_present",
                weight=min(0.24, 0.08 + 0.02 * len(uniq_admin)),
                detail=f"Interfaces administratively down: {shown}",
            )
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="positive",
                signal="huawei_interface_admin_down",
                weight=min(0.28, 0.09 + 0.03 * len(uniq_admin)),
                detail=f"Administratively down interfaces: {shown}",
            )
        return out

    # Cisco/Arista/NXOS style signals in interface brief output.
    no_members = len(re.findall(r"no operational members", low))
    link_not_connected = len(re.findall(r"link not connected", low))
    xcvr_missing = len(re.findall(r"xcvr not inserted", low))
    line_proto_down = len(re.findall(r"line protocol down", low))
    errdisabled = len(re.findall(r"err-?disabled|errdisabled", low))

    if no_members > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="bundle_no_operational_members",
            weight=min(0.26, 0.05 + 0.015 * no_members),
            detail=f"Port-channel without members: {no_members}",
        )
    if line_proto_down > 0 or errdisabled > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="line_protocol_or_errdisabled_down",
            weight=min(0.24, 0.08 + 0.04 * (line_proto_down + errdisabled)),
            detail=f"line-protocol-down={line_proto_down}, errdisabled={errdisabled}",
        )
    if xcvr_missing > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="negative",
            signal="unused_ports_xcvr_missing",
            weight=min(0.16, 0.02 + 0.004 * xcvr_missing),
            detail=f"Likely unused ports (XCVR not inserted): {xcvr_missing}",
        )
    if link_not_connected > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="negative",
            signal="not_connected_ports_present",
            weight=min(0.14, 0.015 + 0.004 * link_not_connected),
            detail=f"Ports link-not-connected: {link_not_connected}",
        )
    return out


def _parse_routing_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    low = str(output_text or "").lower()
    out: list[dict[str, Any]] = []
    down_cnt = 0
    up_cnt = 0

    for p in ("bgp down", "neighbor down", "idle", "active", "route withdraw", "adjacency down", "ospf down"):
        down_cnt += len(re.findall(re.escape(p), low))
    for p in ("established", "full", "adjacency full", "neighbor up"):
        up_cnt += len(re.findall(re.escape(p), low))

    if down_cnt > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="routing",
            polarity="positive",
            signal="routing_session_unstable",
            weight=min(0.30, 0.09 + 0.04 * down_cnt),
            detail=f"Routing down/idle indicators: {down_cnt}",
        )
    if up_cnt > 0 and down_cnt <= 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="routing",
            polarity="negative",
            signal="routing_sessions_established",
            weight=min(0.22, 0.08 + 0.03 * up_cnt),
            detail=f"Established/full indicators: {up_cnt}",
        )
    return out


def _parse_clock_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    low = str(output_text or "").lower()
    out: list[dict[str, Any]] = []
    if any(x in low for x in ("unsynchronized", "unsynced", "not synchronized", "clock is unsynchronized")):
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="clock",
            polarity="positive",
            signal="clock_unsynchronized",
            weight=0.25,
            detail="Clock/NTP unsynchronized indication found",
        )
    elif any(x in low for x in ("time source is ntp", "clock synchronized", "ntp synchronized", "time zone")):
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="clock",
            polarity="negative",
            signal="clock_synchronized_or_stable",
            weight=0.11,
            detail="Clock/NTP appears synchronized or stable",
        )
    return out


def _parse_interface_detail_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    low = str(output_text or "").lower()
    out: list[dict[str, Any]] = []
    if "administratively down" in low or "the interface is shut down" in low:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="interface_admin_shutdown_detail",
            weight=0.24,
            detail="Interface detail confirms administratively down / shut down",
        )
    if "line protocol current state : down" in low or "protocol : down" in low:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="interface_protocol_down_detail",
            weight=0.12,
            detail="Interface detail indicates protocol down",
        )
    return out


def _parse_resource_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    low = str(output_text or "").lower()
    out: list[dict[str, Any]] = []

    peaks: list[int] = []
    for m in re.finditer(r"(cpu|memory)[^%\n]{0,36}?(\d{1,3})\s*%", low):
        pct = _safe_int(m.group(2), -1)
        if 0 <= pct <= 100:
            peaks.append(pct)
    if peaks:
        max_pct = max(peaks)
        if max_pct >= 85:
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="resource",
                polarity="positive",
                signal="high_cpu_or_memory_utilization",
                weight=min(0.30, 0.12 + (max_pct - 85) * 0.01),
                detail=f"Detected utilization peak={max_pct}%",
            )
        elif max_pct <= 45:
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="resource",
                polarity="negative",
                signal="resource_utilization_healthy",
                weight=0.1,
                detail=f"Resource utilization peak={max_pct}%",
            )
    return out


def _parse_log_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    low = str(output_text or "").lower()
    out: list[dict[str, Any]] = []

    link_events = len(re.findall(r"(updown|if_down|line protocol down|link down|link failure)", low))
    route_events = len(re.findall(r"(bgp.*(down|idle|reset)|ospf.*down|neighbor.*down)", low))
    fw_events = len(re.findall(r"(policy deny|security policy|session exhausted|threat|flow[_ -]?deny|nat deny)", low))
    if vendor == "paloalto":
        fw_events += len(re.findall(r"\b(drop|deny)\b", low))

    if link_events > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="log_link_flap_events",
            weight=min(0.24, 0.08 + 0.03 * link_events),
            detail=f"Link-related log events: {link_events}",
        )
    if route_events > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="routing",
            polarity="positive",
            signal="log_routing_session_events",
            weight=min(0.24, 0.08 + 0.03 * route_events),
            detail=f"Routing-related log events: {route_events}",
        )
    if fw_events > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="firewall",
            polarity="positive",
            signal="log_policy_or_drop_events",
            weight=min(0.20, 0.07 + 0.02 * fw_events),
            detail=f"Firewall/policy drop keywords: {fw_events}",
        )
    generic_shutdown_hits = len(
        re.findall(
            r"(administratively down|interface is shut down|shutdown by|configured down|adminstatus=down)",
            low,
        )
    )
    if generic_shutdown_hits > 0:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="interface_admin_shutdown_event",
            weight=min(0.28, 0.10 + 0.03 * generic_shutdown_hits),
            detail=f"Generic shutdown/admin-down indicators in log: {generic_shutdown_hits}",
        )
    if vendor == "huawei":
        shutdown_if = sorted(
            {
                str(m.group(1) or "").strip()
                for m in re.finditer(
                    r"ifname=([a-z]+[\d/.]+),\s*adminstatus=down,\s*operstatus=down,\s*reason=the interface is shut down",
                    low,
                )
                if str(m.group(1) or "").strip()
            }
        )
        if shutdown_if:
            shown = ", ".join(shutdown_if[:5])
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="positive",
                signal="huawei_interface_shutdown_event",
                weight=min(0.35, 0.18 + 0.04 * len(shutdown_if)),
                detail=f"Admin shutdown log events: {shown}",
            )
        recover_if = sorted(
            {
                str(m.group(1) or "").strip()
                for m in re.finditer(
                    r"ifname=([a-z]+[\d/.]+),\s*adminstatus=up,\s*operstatus=up,\s*reason=interface physical link is up",
                    low,
                )
                if str(m.group(1) or "").strip()
            }
        )
        if shutdown_if and recover_if:
            overlap = sorted(set(shutdown_if) & set(recover_if))
            if overlap:
                shown = ", ".join(overlap[:5])
                _add_signal(
                    out,
                    device_id=device_id,
                    vendor=vendor,
                    command=command,
                    domain="link",
                    polarity="positive",
                    signal="huawei_shutdown_recovery_flap_pattern",
                    weight=min(0.30, 0.16 + 0.03 * len(overlap)),
                    detail=f"Shutdown/up recovery oscillation in logs: {shown}",
                )
    return out


def _parse_output_signals(
    *,
    device_id: str,
    vendor: str,
    command: str,
    output_text: str,
) -> list[dict[str, Any]]:
    cmd = str(command or "").strip().lower()
    text = str(output_text or "")
    low = text.lower()
    out: list[dict[str, Any]] = []

    if any(x in cmd for x in ("interface brief", "interfaces brief")):
        out.extend(_parse_interface_brief_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))
    elif "interface " in cmd:
        out.extend(_parse_interface_detail_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))
    if "counter" in cmd and "error" in cmd:
        # Non-zero numeric counters are meaningful even when tabular format varies.
        nums = [_safe_int(x, 0) for x in re.findall(r"\b\d+\b", low)]
        non_zero = sum(1 for n in nums if n > 0)
        if non_zero > 0:
            _add_signal(
                out,
                device_id=device_id,
                vendor=vendor,
                command=command,
                domain="link",
                polarity="positive",
                signal="interface_error_counters_nonzero",
                weight=min(0.32, 0.10 + 0.02 * min(non_zero, 8)),
                detail=f"Detected non-zero counter values count={non_zero}",
            )
    if any(x in cmd for x in ("bgp", "ospf", "route", "routing")):
        out.extend(_parse_routing_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))
    if any(x in cmd for x in ("clock", "ntp", "time")):
        out.extend(_parse_clock_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))
    if any(x in cmd for x in ("cpu", "memory", "resource")):
        out.extend(_parse_resource_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))
    if "log" in cmd:
        out.extend(_parse_log_signals(device_id=device_id, vendor=vendor, command=command, output_text=text))

    # Generic fallback for obvious high-signal text.
    if "crc" in low and "0" not in low:
        _add_signal(
            out,
            device_id=device_id,
            vendor=vendor,
            command=command,
            domain="link",
            polarity="positive",
            signal="crc_related_indicator",
            weight=0.14,
            detail="CRC indicator appeared in output",
        )
    return out


def extract_round_evidence(
    *,
    executions: list[dict[str, Any]],
    profile_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    profile_map = profile_map or {}
    signals: list[dict[str, Any]] = []
    health = {"total": 0, "success": 0, "failed": 0, "error_output": 0, "valid_output": 0}

    for row in executions or []:
        if not isinstance(row, dict):
            continue
        did = str(row.get("device_id") or "*").strip() or "*"
        profile = str(profile_map.get(did) or "unknown").strip().lower()
        vendor = _profile_vendor(profile)
        command = str(row.get("command") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        output = str(row.get("output_text") or "")
        err = str(row.get("error") or "")
        combined = f"{output}\n{err}".strip()

        health["total"] += 1
        if status == "success":
            health["success"] += 1
        else:
            health["failed"] += 1

        if _looks_like_cli_error(combined):
            health["error_output"] += 1
            _add_signal(
                signals,
                device_id=did,
                vendor=vendor,
                command=command,
                domain="global",
                polarity="negative",
                signal="command_syntax_or_unsupported",
                weight=0.12,
                detail=f"Command output indicates syntax/unsupported: {command}",
            )
            continue

        if status == "success" and len(output.strip()) >= 4:
            health["valid_output"] += 1
        else:
            _add_signal(
                signals,
                device_id=did,
                vendor=vendor,
                command=command,
                domain="global",
                polarity="negative",
                signal="command_no_valid_output",
                weight=0.1,
                detail=f"Command did not produce valid output: status={status}",
            )
            continue

        signals.extend(
            _parse_output_signals(
                device_id=did,
                vendor=vendor,
                command=command,
                output_text=output,
            )
        )

    deduped: list[dict[str, Any]] = []
    seen_sig: set[tuple[str, str, str, str, str, str]] = set()
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        key = (
            str(sig.get("device_id") or ""),
            str(sig.get("command") or ""),
            str(sig.get("domain") or ""),
            str(sig.get("polarity") or ""),
            str(sig.get("signal") or ""),
            str(sig.get("detail") or ""),
        )
        if key in seen_sig:
            continue
        seen_sig.add(key)
        deduped.append(sig)
    signals = deduped

    domain_delta: dict[str, float] = {}
    for sig in signals:
        domain = str(sig.get("domain") or "global").strip().lower()
        pol = str(sig.get("polarity") or "positive").strip().lower()
        w = _safe_float(sig.get("weight"), 0.0)
        domain_delta[domain] = domain_delta.get(domain, 0.0) + (w if pol == "positive" else -w)
    domain_delta = {k: round(v, 4) for k, v in domain_delta.items()}

    rank_keys = sorted(domain_delta.keys(), key=lambda d: -abs(domain_delta.get(d, 0.0)))
    summary_lines: list[str] = []
    for d in rank_keys[:5]:
        summary_lines.append(f"{d}: delta={domain_delta.get(d, 0.0):+.3f}")
    if health["total"] > 0:
        valid_rate = health["valid_output"] / max(1, health["total"])
        summary_lines.append(
            "command_health: "
            f"valid={health['valid_output']}/{health['total']} ({valid_rate:.2%}), "
            f"error_output={health['error_output']}, failed={health['failed']}"
        )

    return {
        "signals": signals,
        "domain_delta": domain_delta,
        "command_health": health,
        "summary_lines": summary_lines,
    }


def format_evidence_brief(parsed: dict[str, Any], max_signals: int = 10) -> str:
    if not isinstance(parsed, dict):
        return ""
    lines = list(parsed.get("summary_lines") or [])
    sigs = [x for x in (parsed.get("signals") or []) if isinstance(x, dict)]
    for s in sigs[: max(1, min(int(max_signals), 20))]:
        pol = "+" if str(s.get("polarity") or "").lower() == "positive" else "-"
        lines.append(
            f"{pol} [{s.get('domain')}] {s.get('signal')} "
            f"w={_safe_float(s.get('weight'), 0.0):.2f} "
            f"dev={s.get('device_id')} cmd={s.get('command')} "
            f"detail={s.get('detail')}"
        )
    return "\n".join(str(x) for x in lines if str(x).strip())
