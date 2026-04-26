from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

CompressionStrategy = str

_TS_PATTERNS = [
    re.compile(r"(?P<y>\d{4})\s+(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
    re.compile(r"(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<y>\d{4})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
    re.compile(r"(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
]
_MON = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_KV_KEYS = (
    "ifName",
    "IfName",
    "PortName",
    "PwName",
    "neighbor",
    "Neighbor",
    "Peer",
    "peer",
    "Vrf",
    "VRF",
    "vrf",
    "InstanceID",
    "PortID",
    "PortIndex",
    "Vlan",
    "VLAN",
    "CID",
    "alarmID",
    "PrefixCount",
    "MaxValue",
    "ThresholdLowerLimit",
    "ThresholdUpperLimit",
)
_IDENTITY_KEYS = (
    "ifName",
    "IfName",
    "PortName",
    "PwName",
    "neighbor",
    "Neighbor",
    "Peer",
    "peer",
    "Vrf",
    "VRF",
    "vrf",
    "InstanceID",
    "PortID",
    "PortIndex",
    "Vlan",
    "VLAN",
)
_IFACE_RE = re.compile(
    r"\b("
    r"Eth-Trunk\d+|GE\d+(?:/\d+){1,3}|XGE\d+(?:/\d+){1,3}|25GE\d+(?:/\d+){1,3}|"
    r"100GE\d+(?:/\d+){1,3}|GigabitEthernet\S+|TenGigE\S+|HundredGigE\S+|"
    r"Bundle-Ether\d+|Port-Channel\d+|Ethernet\d+(?:/\d+){1,3}"
    r")\b"
)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|(?:(?:[0-9a-fA-F]{1,4}:){3,}[0-9a-fA-F:]+)")
_COLLECTOR_PREFIX_RE = re.compile(
    r"^(?P<collector>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[(?P<source>[^\]]+)\]\s+(?P<collector_host>\S+)\s+(?P<rest>.*)$"
)
_SEVERITY_RE = re.compile(r"^(?P<severity><\d+>)(?P<rest>.*)$")
_PAIR_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]{1,40})=([^,)\s;]+)")


def normalize_strategy(raw: Any) -> CompressionStrategy:
    text = str(raw or "").strip().lower()
    if text == "repeat_timeline":
        return "template_vars"
    if text in {"group_repeats", "factor_time", "template_vars", "off"}:
        return text
    if text in {"1", "true", "yes", "on", "checked"}:
        return "group_repeats"
    return "off"


def _vendor_profile(vendor: str = "", os_family: str = "") -> str:
    text = f"{vendor} {os_family}".strip().lower()
    if "huawei" in text or "vrp" in text:
        return "huawei"
    if "nxos" in text or "nx-os" in text:
        return "nxos"
    if "iosxr" in text or "ios xr" in text or "xr" in text:
        return "iosxr"
    if "arista" in text or "eos" in text:
        return "arista"
    if "cisco" in text:
        return "cisco"
    return ""


def _parse_line_dt(line: str, default_year: int) -> datetime | None:
    for p in _TS_PATTERNS:
        m = p.search(line)
        if not m:
            continue
        try:
            year = int(m.groupdict().get("y") or default_year)
            month = _MON.get(str(m.group("m")).lower(), 0)
            if not month:
                continue
            return datetime(
                year,
                month,
                int(m.group("d")),
                int(m.group("h")),
                int(m.group("mi")),
                int(m.group("s")),
            )
        except Exception:
            continue
    return None


def _format_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _extract_nxos_facility(line: str) -> str:
    m = re.search(
        r"^\d{4}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\S+\s+([A-Za-z][A-Za-z0-9_-]{1,40}):\s+",
        line,
    )
    return m.group(1).lower() if m else ""


def _extract_event_code(line: str, vendor_profile: str = "") -> str:
    if vendor_profile == "nxos":
        facility = _extract_nxos_facility(line)
        if facility:
            return facility
    for pat in (
        r"(%%\d+[A-Z]+/\d+/[A-Za-z0-9_]+)",
        r"(%[A-Z0-9_-]+-[A-Z0-9_-]+-\d+-[A-Z0-9_]+)",
        r"\b([A-Za-z][A-Za-z0-9_-]{2,32}):",
    ):
        m = re.search(pat, line)
        if m:
            return m.group(1)
    return ""


def _extract_pairs(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in _PAIR_RE.findall(line):
        if key not in out:
            out[key] = value
    return out


def _extract_key_fields(line: str, vendor_profile: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    pairs = _extract_pairs(line)
    for key in _KV_KEYS:
        if key in pairs:
            item = f"{key}={pairs[key]}"
            if item not in seen:
                seen.add(item)
                out.append(item)
    for m in re.findall(r"\b(?:neighbor|peer|from|to)\s+([^\s,;()]+)", line, flags=re.IGNORECASE):
        item = f"peer={m}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    for m in _IFACE_RE.findall(line):
        item = f"iface={m}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    for m in _IP_RE.findall(line):
        item = f"ip={m}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    if vendor_profile in {"iosxr", "nxos", "cisco"}:
        for pat, name in (
            (r"\bVRF:\s*([^\s;,)]+)", "vrf"),
            (r"\bVrf:\s*\"?([^\s\";,)]+)", "vrf"),
            (r"\bneighbor\s+([^\s,;()]+)", "neighbor"),
            (r"\bfrom\s+([^\s,;()]+)", "neighbor"),
        ):
            for m in re.findall(pat, line, flags=re.IGNORECASE):
                item = f"{name}={m}"
                if item not in seen:
                    seen.add(item)
                    out.append(item)
    return out[:6]


def _extract_identity_fields(line: str, vendor_profile: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    pairs = _extract_pairs(line)
    for key in _IDENTITY_KEYS:
        if key in pairs:
            item = f"{key}={pairs[key]}"
            if item not in seen:
                seen.add(item)
                out.append(item)
    for m in re.findall(r"\b(?:neighbor|peer|from|to)\s+([^\s,;()]+)", line, flags=re.IGNORECASE):
        item = f"neighbor={m}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    for m in _IFACE_RE.findall(line):
        item = f"iface={m}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    if vendor_profile in {"iosxr", "nxos", "cisco"}:
        for pat in (r"\bVRF:\s*([^\s;,)]+)", r"\bVrf:\s*\"?([^\s\";,)]+)"):
            for m in re.findall(pat, line, flags=re.IGNORECASE):
                item = f"vrf={m}"
                if item not in seen:
                    seen.add(item)
                    out.append(item)
    return out[:4]


def _normalize_line_for_fallback(line: str) -> str:
    text = line.strip()
    for pat in _TS_PATTERNS:
        m = pat.search(text)
        if m:
            text = text[m.end() :].strip()
            break
    text = re.sub(r"\s+", " ", text)
    return text[:220]


def _normalize_message_stem(line: str, vendor_profile: str = "") -> str:
    text = _normalize_line_for_fallback(line)
    text = re.sub(r"\(message repeated \d+ time[s]?\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "<url>", text)
    if vendor_profile == "nxos":
        facility = _extract_nxos_facility(line)
        if facility and f"{facility}:" in text:
            text = text.split(f"{facility}:", 1)[1].strip()
    if vendor_profile == "iosxr":
        m = re.search(r"%[A-Z0-9_-]+-[A-Z0-9_-]+-\d+-[A-Z0-9_]+\s*:\s*(.*)$", text)
        if m:
            text = m.group(1).strip()
    if vendor_profile == "huawei":
        text = re.sub(r"CID=[^;]+", "CID=<value>", text)
        text = re.sub(r"alarmID=[^;]+", "alarmID=<value>", text)
    text = re.sub(r"\b\d+\b", "<num>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def _build_signature(line: str, vendor_profile: str = "") -> str:
    event_code = _extract_event_code(line, vendor_profile)
    fields = _extract_identity_fields(line, vendor_profile)
    stem = _normalize_message_stem(line, vendor_profile)
    if event_code and fields:
        return event_code + "|" + "|".join(fields) + "|" + stem
    if event_code:
        return event_code + "|" + stem
    return stem


def _sample_context(lines: list[str], line_no: int, radius: int = 1) -> str:
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    return "\n".join(lines[start - 1 : end])


def _should_group_repeats(total_lines: int, groups: list[dict[str, Any]]) -> bool:
    if total_lines <= 120:
        return False
    repeated_groups = [g for g in groups if int(g.get("count", 0) or 0) >= 3]
    if not repeated_groups:
        return False
    if any(int(g.get("count", 0) or 0) >= 4 for g in repeated_groups):
        return True
    repeated_lines = sum(int(g.get("count", 0) or 0) for g in repeated_groups)
    return repeated_lines >= max(12, int(total_lines * 0.18))


def _split_body_and_fields(body: str, vendor_profile: str = "") -> tuple[str, dict[str, str]]:
    pairs = _extract_pairs(body)
    if ";" in body:
        message = body.split(";", 1)[1].strip()
    else:
        message = body.strip()
    event_code = _extract_event_code(message, vendor_profile)
    if event_code:
        idx = message.find(event_code)
        if idx >= 0:
            message = message[idx + len(event_code) :].lstrip(" :;-")
    if vendor_profile == "nxos":
        facility = _extract_nxos_facility(body)
        if facility and message.lower().startswith(facility.lower() + ":"):
            message = message.split(":", 1)[1].strip()
    for key, value in pairs.items():
        message = re.sub(rf"\b{re.escape(key)}={re.escape(value)}\b", f"{key}=<value>", message)
    message = re.sub(r"\(message repeated \d+ time[s]?\)", "(message repeated <count> times)", message, flags=re.IGNORECASE)
    message = re.sub(r"https?://\S+", "<url>", message)
    if vendor_profile in {"iosxr", "nxos", "cisco"}:
        message = re.sub(r"\b\d+\b", "<num>", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message, pairs


def _extract_time_factored_occurrence(line: str, line_no: int, default_year: int, vendor_profile: str = "") -> dict[str, Any]:
    collector_time = None
    collector_source = None
    collector_host = None
    rest = line.strip()
    m = _COLLECTOR_PREFIX_RE.match(rest)
    if m:
        collector_time = m.group("collector")
        collector_source = m.group("source")
        collector_host = m.group("collector_host")
        rest = m.group("rest").strip()

    severity = None
    m = _SEVERITY_RE.match(rest)
    if m:
        severity = m.group("severity")
        rest = m.group("rest").strip()

    device_time_dt = _parse_line_dt(rest, default_year)
    device_time = _format_dt(device_time_dt)
    if device_time_dt is not None:
        for pat in _TS_PATTERNS:
            mm = pat.search(rest)
            if mm:
                rest = rest[mm.end() :].strip()
                break

    device_host = None
    parts = rest.split(None, 1)
    if parts and len(parts[0]) > 2 and ":" not in parts[0] and not parts[0].startswith("%%") and not parts[0].startswith("%"):
        if any(ch.isalpha() for ch in parts[0]):
            device_host = parts[0]
            rest = parts[1].strip() if len(parts) > 1 else ""

    event_code = _extract_event_code(rest, vendor_profile) or _extract_event_code(line, vendor_profile) or "raw_line"
    message_body, fields = _split_body_and_fields(rest, vendor_profile)
    template_text = message_body or _normalize_line_for_fallback(rest)
    return {
        "line_no": line_no,
        "collector_time": collector_time,
        "device_time": device_time,
        "collector_source": collector_source,
        "collector_host": collector_host,
        "device_host": device_host,
        "severity": severity,
        "event_code": event_code,
        "template_text": template_text,
        "fields": fields,
        "raw_body": rest,
        "raw_line": line,
    }


def _build_group_repeats_package(
    raw_lines: list[str],
    non_empty: list[tuple[int, str]],
    source_name: str,
    device_id: str,
    default_year: int,
    vendor_profile: str,
) -> dict[str, Any]:
    groups_by_sig: dict[str, dict[str, Any]] = {}
    group_order: list[str] = []
    for line_no, line in non_empty:
        sig = _build_signature(line, vendor_profile)
        if sig not in groups_by_sig:
            groups_by_sig[sig] = {
                "event_id": f"evt-{len(group_order)+1:03d}",
                "signature": sig,
                "event_code": _extract_event_code(line, vendor_profile) or "raw_line",
                "count": 0,
                "first_seen": None,
                "last_seen": None,
                "timeline": [],
                "line_numbers": [],
                "sample_lines": [],
                "sample_contexts": [],
                "key_fields": _extract_key_fields(line, vendor_profile),
            }
            group_order.append(sig)
        g = groups_by_sig[sig]
        g["count"] += 1
        g["line_numbers"].append(line_no)
        dt = _parse_line_dt(line, default_year)
        if dt is not None:
            stamp = _format_dt(dt)
            if not g["first_seen"]:
                g["first_seen"] = stamp
            g["last_seen"] = stamp
            g["timeline"].append(stamp)
        if len(g["sample_lines"]) < 2:
            g["sample_lines"].append(line)
            g["sample_contexts"].append(_sample_context(raw_lines, line_no))
        else:
            g["sample_lines"][-1] = line
            g["sample_contexts"][-1] = _sample_context(raw_lines, line_no)

    groups = [groups_by_sig[sig] for sig in group_order]
    used = _should_group_repeats(len(non_empty), groups)
    index = {
        "strategy": "group_repeats",
        "device_id": device_id,
        "source_name": source_name,
        "used": used,
        "line_count": len(non_empty),
        "group_count": len(groups),
        "groups": groups,
    }
    if not used:
        return {
            "used": False,
            "index": index,
            "markdown": "",
            "stats": {
                "strategy": "group_repeats",
                "line_count": len(non_empty),
                "group_count": len(groups),
                "repeated_group_count": sum(1 for g in groups if int(g.get("count", 0) or 0) >= 3),
            },
        }

    md: list[str] = []
    md.append("# Conservative Semantic Compression View")
    md.append("")
    md.append(f"- Strategy: `group_repeats`")
    md.append(f"- Source: `{source_name}`")
    if device_id:
        md.append(f"- Device: `{device_id}`")
    md.append(f"- Raw non-empty lines: `{len(non_empty)}`")
    md.append(f"- Event groups: `{len(groups)}`")
    md.append("- Note: this view only folds repeated events. Original evidence remains in the source log.")
    md.append("")
    md.append("## Event Groups")
    for g in groups:
        md.append(f"### {g['event_id']} | {g['event_code']} | count={g['count']}")
        if g.get("key_fields"):
            md.append(f"- Key fields: `{', '.join(g['key_fields'])}`")
        if g.get("first_seen") or g.get("last_seen"):
            md.append(f"- First seen: `{g.get('first_seen') or '-'}`")
            md.append(f"- Last seen: `{g.get('last_seen') or '-'}`")
        if g.get("timeline"):
            md.append("- Timeline:")
            for ts in g["timeline"]:
                md.append(f"  - `{ts}`")
        md.append(f"- Evidence lines: `{json.dumps(g.get('line_numbers', []), ensure_ascii=False)}`")
        if g.get("sample_lines"):
            md.append("- Sample lines:")
            for sample in g["sample_lines"]:
                md.append("```text")
                md.append(sample)
                md.append("```")
        if g.get("sample_contexts"):
            md.append("- Sample contexts:")
            for ctx in g["sample_contexts"]:
                md.append("```text")
                md.append(ctx)
                md.append("```")
        md.append("")
    return {
        "used": True,
        "index": index,
        "markdown": "\n".join(md).strip() + "\n",
        "stats": {
            "strategy": "group_repeats",
            "line_count": len(non_empty),
            "group_count": len(groups),
            "repeated_group_count": sum(1 for g in groups if int(g.get("count", 0) or 0) >= 3),
        },
    }


def _build_factor_time_package(
    raw_lines: list[str],
    non_empty: list[tuple[int, str]],
    source_name: str,
    device_id: str,
    default_year: int,
    vendor_profile: str,
) -> dict[str, Any]:
    groups_by_sig: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line_no, line in non_empty:
        occ = _extract_time_factored_occurrence(line, line_no, default_year, vendor_profile)
        identity_fields = _extract_identity_fields(line, vendor_profile)
        sig = occ["event_code"] + "|" + occ["template_text"] + ("|" + "|".join(identity_fields) if identity_fields else "")
        if sig not in groups_by_sig:
            groups_by_sig[sig] = {
                "event_id": f"evt-{len(order)+1:03d}",
                "signature": sig,
                "event_code": occ["event_code"],
                "template_text": occ["template_text"],
                "occurrences": [],
                "sample_lines": [],
                "static_fields": {},
                "varying_field_names": [],
                "collector_sources": set(),
                "collector_hosts": set(),
                "device_hosts": set(),
                "severities": set(),
            }
            order.append(sig)
        g = groups_by_sig[sig]
        g["occurrences"].append(
            {
                "line_no": occ["line_no"],
                "collector_time": occ["collector_time"],
                "device_time": occ["device_time"],
                "fields": occ["fields"],
            }
        )
        if len(g["sample_lines"]) < 2:
            g["sample_lines"].append(occ["raw_line"])
        else:
            g["sample_lines"][-1] = occ["raw_line"]
        if occ["collector_source"]:
            g["collector_sources"].add(occ["collector_source"])
        if occ["collector_host"]:
            g["collector_hosts"].add(occ["collector_host"])
        if occ["device_host"]:
            g["device_hosts"].add(occ["device_host"])
        if occ["severity"]:
            g["severities"].add(occ["severity"])

    groups: list[dict[str, Any]] = []
    for sig in order:
        g = groups_by_sig[sig]
        all_field_names = sorted({k for occ in g["occurrences"] for k in occ["fields"].keys()})
        static_fields: dict[str, str] = {}
        varying_names: list[str] = []
        for name in all_field_names:
            values = {str(occ["fields"].get(name, "")) for occ in g["occurrences"]}
            if len(values) == 1:
                static_fields[name] = next(iter(values))
            else:
                varying_names.append(name)
        normalized_occ: list[dict[str, Any]] = []
        for occ in g["occurrences"]:
            varying_fields = {k: v for k, v in occ["fields"].items() if k in varying_names}
            normalized_occ.append(
                {
                    "line_no": occ["line_no"],
                    "collector_time": occ["collector_time"],
                    "device_time": occ["device_time"],
                    "varying_fields": varying_fields,
                }
            )
        groups.append(
            {
                "event_id": g["event_id"],
                "signature": g["signature"],
                "event_code": g["event_code"],
                "template_text": g["template_text"],
                "occurrence_count": len(g["occurrences"]),
                "static_fields": static_fields,
                "varying_field_names": varying_names,
                "occurrences": normalized_occ,
                "sample_lines": g["sample_lines"],
                "collector_sources": sorted(g["collector_sources"]),
                "collector_hosts": sorted(g["collector_hosts"]),
                "device_hosts": sorted(g["device_hosts"]),
                "severities": sorted(g["severities"]),
            }
        )

    index = {
        "strategy": "factor_time",
        "device_id": device_id,
        "source_name": source_name,
        "used": bool(groups),
        "line_count": len(non_empty),
        "template_count": len(groups),
        "groups": groups,
    }
    if not groups:
        return {
            "used": False,
            "index": index,
            "markdown": "",
            "stats": {
                "strategy": "factor_time",
                "line_count": len(non_empty),
                "template_count": 0,
            },
        }

    md: list[str] = []
    md.append("# Time-Factored Compression View")
    md.append("")
    md.append(f"- Strategy: `factor_time`")
    md.append(f"- Source: `{source_name}`")
    if device_id:
        md.append(f"- Device: `{device_id}`")
    md.append(f"- Raw non-empty lines: `{len(non_empty)}`")
    md.append(f"- Event templates: `{len(groups)}`")
    md.append("- Note: timestamps and occurrences are separated from the template body. Original evidence remains in the source log.")
    global_sources = sorted({src for g in groups for src in g["collector_sources"]})
    global_collector_hosts = sorted({host for g in groups for host in g["collector_hosts"]})
    global_device_hosts = sorted({host for g in groups for host in g["device_hosts"]})
    global_severities = sorted({sev for g in groups for sev in g["severities"]})
    if global_sources:
        md.append(f"- Collector sources: `{', '.join(global_sources)}`")
    if global_collector_hosts:
        md.append(f"- Collector hosts: `{', '.join(global_collector_hosts)}`")
    if global_device_hosts:
        md.append(f"- Device hosts: `{', '.join(global_device_hosts)}`")
    if global_severities:
        md.append(f"- Severities: `{', '.join(global_severities)}`")
    md.append("")
    md.append("## Event Templates")

    def _compact_pairs_text(pairs: dict[str, str], limit: int = 10) -> str:
        items = [f"{k}={v}" for k, v in sorted(pairs.items()) if str(v or "").strip()]
        if len(items) > limit:
            items = items[:limit] + [f"...(+{len(items) - limit})"]
        return "; ".join(items)

    def _occurrence_label(occ: dict[str, Any], varying_names: list[str]) -> str:
        parts = [f"L{occ.get('line_no', '')}"]
        if occ.get("collector_time"):
            parts.append(f"C={occ.get('collector_time')}")
        if occ.get("device_time"):
            parts.append(f"D={occ.get('device_time')}")
        varying = occ.get("varying_fields", {}) or {}
        for name in varying_names:
            value = str(varying.get(name, "") or "").strip()
            if value:
                parts.append(f"{name}={value}")
        return " | ".join(parts)

    for g in groups:
        md.append(f"### {g['event_id']} | {g['event_code']} | occurrences={g['occurrence_count']}")
        md.append(f"- Template text: `{g['template_text']}`")
        if g["collector_sources"] and sorted(g["collector_sources"]) != global_sources:
            md.append(f"- Collector sources: `{', '.join(g['collector_sources'])}`")
        if g["collector_hosts"] and sorted(g["collector_hosts"]) != global_collector_hosts:
            md.append(f"- Collector hosts: `{', '.join(g['collector_hosts'])}`")
        if g["device_hosts"] and sorted(g["device_hosts"]) != global_device_hosts:
            md.append(f"- Device hosts: `{', '.join(g['device_hosts'])}`")
        if g["severities"] and sorted(g["severities"]) != global_severities:
            md.append(f"- Severities: `{', '.join(g['severities'])}`")
        if g["static_fields"]:
            md.append(f"- Static fields: `{_compact_pairs_text(g['static_fields'])}`")
        if g["occurrences"]:
            if g["varying_field_names"]:
                md.append("- Occurrences:")
                for occ in g["occurrences"]:
                    md.append(f"  - {_occurrence_label(occ, list(g['varying_field_names']))}")
            else:
                compact_timeline = "; ".join(_occurrence_label(occ, []) for occ in g["occurrences"])
                md.append(f"- Timeline: `{compact_timeline}`")
        md.append("")
    return {
        "used": True,
        "index": index,
        "markdown": "\n".join(md).strip() + "\n",
        "stats": {
            "strategy": "factor_time",
            "line_count": len(non_empty),
            "template_count": len(groups),
        },
    }


def _build_template_vars_package(
    raw_lines: list[str],
    non_empty: list[tuple[int, str]],
    source_name: str,
    device_id: str,
    default_year: int,
    vendor_profile: str,
) -> dict[str, Any]:
    groups_by_sig: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line_no, line in non_empty:
        occ = _extract_time_factored_occurrence(line, line_no, default_year, vendor_profile)
        sig = occ["event_code"] + "|" + occ["template_text"]
        if sig not in groups_by_sig:
            groups_by_sig[sig] = {
                "event_id": f"evt-{len(order)+1:03d}",
                "signature": sig,
                "event_code": occ["event_code"],
                "template_text": occ["template_text"],
                "representative_line": occ["raw_line"],
                "occurrences": [],
            }
            order.append(sig)
        groups_by_sig[sig]["occurrences"].append(
            {
                "line_no": occ["line_no"],
                "collector_time": occ["collector_time"],
                "device_time": occ["device_time"],
                "fields": occ["fields"],
            }
        )

    groups: list[dict[str, Any]] = []
    for sig in order:
        g = groups_by_sig[sig]
        all_field_names = sorted({k for occ in g["occurrences"] for k in occ["fields"].keys()})
        static_fields: dict[str, str] = {}
        varying_names: list[str] = []
        for name in all_field_names:
            values = {str(occ["fields"].get(name, "")) for occ in g["occurrences"]}
            if len(values) == 1:
                static_fields[name] = next(iter(values))
            else:
                varying_names.append(name)
        normalized_occurrences: list[dict[str, Any]] = []
        for occ in g["occurrences"]:
            normalized_occurrences.append(
                {
                    "line_no": occ["line_no"],
                    "collector_time": occ["collector_time"],
                    "device_time": occ["device_time"],
                    "varying_fields": {k: v for k, v in occ["fields"].items() if k in varying_names},
                }
            )
        groups.append(
            {
                "event_id": g["event_id"],
                "signature": g["signature"],
                "event_code": g["event_code"],
                "template_text": g["template_text"],
                "representative_line": g["representative_line"],
                "occurrence_count": len(g["occurrences"]),
                "static_fields": static_fields,
                "varying_field_names": varying_names,
                "occurrences": normalized_occurrences,
            }
        )

    groups.sort(key=lambda g: (-int(g["occurrence_count"]), str(g["event_code"]), str(g["template_text"])))
    index = {
        "strategy": "template_vars",
        "device_id": device_id,
        "source_name": source_name,
        "used": bool(groups),
        "line_count": len(non_empty),
        "group_count": len(groups),
        "groups": groups,
    }
    if not groups:
        return {
            "used": False,
            "index": index,
            "markdown": "",
            "stats": {
                "strategy": "template_vars",
                "line_count": len(non_empty),
                "group_count": 0,
            },
        }

    def _compact_pairs_text(pairs: dict[str, str], limit: int = 10) -> str:
        items = [f"{k}={v}" for k, v in sorted(pairs.items()) if str(v or "").strip()]
        if len(items) > limit:
            items = items[:limit] + [f"...(+{len(items) - limit})"]
        return "; ".join(items)

    md: list[str] = []
    md.append("# Template Variables View")
    md.append("")
    md.append("- Strategy: `template_vars`")
    md.append(f"- Source: `{source_name}`")
    if device_id:
        md.append(f"- Device: `{device_id}`")
    md.append(f"- Raw non-empty lines: `{len(non_empty)}`")
    md.append(f"- Templates: `{len(groups)}`")
    md.append("- Rule: logs are grouped by constant template. Timestamps are separated, and only parsed variable fields are listed per occurrence.")
    md.append("")
    md.append("## Templates")
    for g in groups:
        md.append(f"### {g['event_id']} | {g['event_code']} | occurrences={g['occurrence_count']}")
        md.append(f"- Template: `{g['template_text']}`")
        md.append(f"- Representative line: `{g['representative_line']}`")
        if g["static_fields"]:
            md.append(f"- Static fields: `{_compact_pairs_text(g['static_fields'])}`")
        if g["varying_field_names"]:
            md.append(f"- Varying fields: `{', '.join(g['varying_field_names'])}`")
        md.append("- Occurrences:")
        for occ in g["occurrences"]:
            bits = [f"L{occ.get('line_no', '')}"]
            if occ.get("collector_time"):
                bits.append(f"C={occ['collector_time']}")
            if occ.get("device_time"):
                bits.append(f"D={occ['device_time']}")
            varying = occ.get("varying_fields", {}) or {}
            for name in g["varying_field_names"]:
                value = str(varying.get(name, "") or "").strip()
                if value:
                    bits.append(f"{name}={value}")
            md.append(f"  - {' | '.join(bits)}")
        md.append("")
    return {
        "used": True,
        "index": index,
        "markdown": "\n".join(md).strip() + "\n",
        "stats": {
            "strategy": "template_vars",
            "line_count": len(non_empty),
            "group_count": len(groups),
            "collapsed_lines": max(0, len(non_empty) - len(groups)),
        },
    }


def build_semantic_package(
    log_text: str,
    *,
    source_name: str = "filtered.log",
    device_id: str = "",
    default_year: int | None = None,
    strategy: CompressionStrategy = "group_repeats",
    vendor: str = "",
    os_family: str = "",
) -> dict[str, Any]:
    strategy = normalize_strategy(strategy)
    raw_lines = log_text.splitlines()
    non_empty = [(i + 1, line) for i, line in enumerate(raw_lines) if line.strip()]
    year = default_year or datetime.now().year
    vendor_profile = _vendor_profile(vendor, os_family)

    if strategy == "off":
        return {
            "used": False,
            "index": {
                "strategy": "off",
                "device_id": device_id,
                "source_name": source_name,
                "used": False,
                "line_count": len(non_empty),
                "groups": [],
            },
            "markdown": "",
            "stats": {"strategy": "off", "line_count": len(non_empty)},
        }
    if strategy == "factor_time":
        return _build_factor_time_package(raw_lines, non_empty, source_name, device_id, year, vendor_profile)
    if strategy == "template_vars":
        return _build_template_vars_package(raw_lines, non_empty, source_name, device_id, year, vendor_profile)
    return _build_group_repeats_package(raw_lines, non_empty, source_name, device_id, year, vendor_profile)
