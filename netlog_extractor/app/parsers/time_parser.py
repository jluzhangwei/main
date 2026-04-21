from __future__ import annotations

import re
from datetime import datetime

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def parse_device_clock(text: str, vendor: str) -> datetime | None:
    text = text.strip()
    # Huawei/common ISO style: 2026-02-19 15:23:44, 2026-02-19 15:23:44+08:00, with optional .ms
    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2})?)", text)
    if m:
        raw = m.group(1)
        fixed = raw
        # normalize +0800 -> +08:00
        if re.search(r"[+-]\d{4}$", fixed):
            fixed = fixed[:-5] + fixed[-5:-2] + ":" + fixed[-2:]
        for candidate in (fixed, fixed.split(".")[0]):
            try:
                if re.search(r"[+-]\d{2}:\d{2}$", candidate):
                    return _naive(datetime.fromisoformat(candidate))
                return datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

    # Cisco/Arista: *15:23:44.123 UTC Thu Feb 19 2026
    m = re.search(
        r"\*?(\d{2}:\d{2}:\d{2})(?:\.\d+)?\s+\S+\s+\w+\s+(\w{3})\s+(\d{1,2})\s+(\d{4})",
        text,
    )
    if m:
        hhmmss, mon, day, year = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(int(year), month, int(day), h, mi, s)

    # Cisco/Arista variant: Thu Feb 19 15:23:44 UTC 2026
    m = re.search(
        r"\w+\s+(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?\s+\S+\s+(\d{4})",
        text,
    )
    if m:
        mon, day, hhmmss, year = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(int(year), month, int(day), h, mi, s)

    # Another variant: Feb 19 2026 15:23:44
    m = re.search(r"(\w{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})", text)
    if m:
        mon, day, year, hhmmss = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(int(year), month, int(day), h, mi, s)

    # Fallback: standard datetime patterns
    for fmt in ["%Y-%m-%d %H:%M:%S", "%b %d %Y %H:%M:%S"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_log_line_timestamp(line: str, vendor: str, reference_year: int | None = None) -> datetime | None:
    line = line.strip()
    vendor_norm = (vendor or "unknown").strip().lower()

    if vendor_norm in ("cisco", "arista"):
        return _parse_cisco_arista_log_ts(line, reference_year)
    if vendor_norm == "huawei":
        return _parse_huawei_log_ts(line)
    return _parse_generic_log_ts(line, reference_year)


def _parse_cisco_arista_log_ts(line: str, reference_year: int | None = None) -> datetime | None:
    # Cisco/NX-OS style: 2026 Feb  7 09:05:27.741 ...
    m = re.match(r"(\d{4})\s+(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?", line)
    if m:
        year, mon, day, hhmmss = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(int(year), month, int(day), h, mi, s)

    # Cisco/Arista common: Feb 19 15:20:01
    m = re.match(r"(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})", line)
    if m and reference_year:
        mon, day, hhmmss = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(reference_year, month, int(day), h, mi, s)

    # Cisco ASR/XR style: RP/0/RSP0/CPU0:Feb 19 00:10:47.775 ...
    m = re.match(r"(?:[^:]+:)?(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?", line)
    if m and reference_year:
        mon, day, hhmmss = m.groups()
        month = MONTHS.get(mon)
        if month:
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(reference_year, month, int(day), h, mi, s)
    return None


def _parse_huawei_log_ts(line: str) -> datetime | None:
    # Huawei: Feb  3 2026 23:09:25+08:00 / Feb 3 2026 23:09:25
    m = re.match(
        r"(\w{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:([+-]\d{2}:?\d{2}))?",
        line,
    )
    if m:
        mon, day, year, hhmmss, tz = m.groups()
        month = MONTHS.get(mon)
        if month:
            if tz:
                tz_norm = tz
                if re.fullmatch(r"[+-]\d{4}", tz):
                    tz_norm = tz[:-2] + ":" + tz[-2:]
                try:
                    dt = datetime.fromisoformat(f"{year}-{month:02d}-{int(day):02d} {hhmmss}{tz_norm}")
                    return _naive(dt)
                except ValueError:
                    pass
            h, mi, s = [int(x) for x in hhmmss.split(":")]
            return datetime(int(year), month, int(day), h, mi, s)

    # Huawei variant in logbuffer body
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", line)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


def _parse_generic_log_ts(line: str, reference_year: int | None = None) -> datetime | None:
    # ISO style
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", line)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # Fall back to major vendor parsers
    ts = _parse_huawei_log_ts(line)
    if ts is not None:
        return ts
    return _parse_cisco_arista_log_ts(line, reference_year)


def extract_model_and_version(version_text: str) -> tuple[str | None, str | None, str | None]:
    vendor, _, model, version = extract_device_profile(version_text)
    return vendor, model, version


def extract_device_profile(version_text: str) -> tuple[str, str, str | None, str | None]:
    low = version_text.lower()
    vendor = "unknown"
    os_family = "unknown"

    if "cisco" in low:
        vendor = "cisco"
        if "ios xr" in low or "ios-xr" in low or "cisco ios xr" in low or "xr software" in low:
            os_family = "cisco_iosxr"
        elif "nx-os" in low or "nexus operating system" in low or "nexus" in low:
            os_family = "cisco_nxos"
        elif "ios xe" in low or "ios-xe" in low:
            os_family = "cisco_iosxe"
        elif "cisco ios software" in low or "ios software" in low:
            os_family = "cisco_ios"
        else:
            os_family = "cisco_unknown"
    elif "arista" in low:
        vendor = "arista"
        os_family = "arista_eos" if "eos" in low or "arista" in low else "arista_unknown"
    elif "huawei" in low:
        vendor = "huawei"
        os_family = "huawei_vrp" if "vrp" in low or "versatile routing platform" in low else "huawei_unknown"

    model = None
    version = None

    mm = re.search(r"(?:Model|cisco\s+)(?:\s*number\s*:?\s*|\s+)([A-Za-z0-9\-_/]+)", version_text, re.IGNORECASE)
    if mm:
        model = mm.group(1)

    # Prefer explicit "... Software, Version X" / "Version X"
    mv = re.search(r"(?:Software,\s*Version|Version)\s*[:\s]+([^\s,\n]+)", version_text, re.IGNORECASE)
    if mv:
        version = mv.group(1).strip()
    else:
        # Huawei variant fallback
        mv2 = re.search(r"VRP\s*\(R\)\s*software,\s*Version\s*([^\s,\n]+)", version_text, re.IGNORECASE)
        if mv2:
            version = mv2.group(1).strip()

    return vendor, os_family, model, version


def parse_device_name(text: str, vendor: str) -> str | None:
    vendor_norm = (vendor or "unknown").strip().lower()
    if not text:
        return None

    patterns: list[re.Pattern[str]] = []
    if vendor_norm == "huawei":
        patterns = [
            re.compile(r"^\s*sysname\s+([A-Za-z0-9_.-]+)\s*$", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*<([A-Za-z0-9_.-]+)>\s*$", re.MULTILINE),
        ]
    else:
        patterns = [
            re.compile(r"^\s*hostname\s+([A-Za-z0-9_.-]+)\s*$", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\([^)]+\))?[>#]\s*$", re.MULTILINE),
        ]

    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()

    return extract_device_name_from_version(text, vendor)


def extract_device_name_from_prompt(prompt_text: str) -> str | None:
    text = (prompt_text or "").strip()
    if not text:
        return None

    m = re.search(r"<([A-Za-z0-9_.-]+)>", text)
    if m:
        return m.group(1).strip()

    m = re.search(r"([A-Za-z0-9_.-]+)(?:\([^)]+\))?[>#]\s*$", text)
    if m:
        return m.group(1).strip()

    return None


def extract_device_name_from_version(version_text: str, vendor: str | None = None) -> str | None:
    text = version_text or ""
    if not text:
        return None

    vendor_norm = (vendor or "unknown").strip().lower()
    patterns: list[re.Pattern[str]] = []
    if vendor_norm == "huawei":
        patterns = [
            re.compile(r"^\s*HUAWEI\s+([A-Za-z0-9_.-]+)\s+uptime is", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*([A-Za-z0-9_.-]+)\s+uptime is", re.IGNORECASE | re.MULTILINE),
        ]
    else:
        patterns = [
            re.compile(r"^\s*([A-Za-z0-9_.-]+)\s+uptime is", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*[A-Za-z0-9_.-]+\s+([A-Za-z0-9_.-]+)\s+uptime is", re.IGNORECASE | re.MULTILINE),
        ]

    for pattern in patterns:
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip()
            if candidate.lower() not in {"huawei", "cisco"}:
                return candidate

    return None
