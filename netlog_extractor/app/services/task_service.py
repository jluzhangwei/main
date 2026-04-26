from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from ..models import DeviceInput, TaskCreatePayload


def _looks_like_ipv4(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    parts = raw.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def parse_time_or_raise(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("time value is empty")

    normalized = raw.replace("T", " ")
    if normalized.endswith("Z"):
        normalized = normalized[:-1]

    for candidate in (normalized, normalized.split(".")[0]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
        try:
            return datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    raise ValueError(f"Unsupported time format: {raw}")


def default_smc_command(mode: str, jump_host: str | None) -> str | None:
    normalized = str(mode or "").strip().lower()
    if normalized == "smc":
        return f"smc server toc {str(jump_host or '').strip()}".strip()
    if normalized == "smc_pam_nd":
        return "smc pam nd ssh {device_ip}"
    return None


def parse_devices_from_text(
    raw_text: str,
    default_username: str | None,
    default_password: str | None,
    default_jump_mode: str,
    sql_only_mode: bool,
    jump_host: str | None,
    jump_port: int,
    smc_command: str | None,
    vendor_hint: str | None,
) -> list[DeviceInput]:
    devices: list[DeviceInput] = []
    require_credentials = (not sql_only_mode) and str(default_jump_mode or "").strip().lower() != "smc_pam_nd"
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",")]
        ip_port = parts[0]
        if ":" in ip_port:
            ip, p = ip_port.split(":", 1)
            device_port = int(p)
        else:
            ip = ip_port
            device_port = 22

        device_name = parts[1] if len(parts) > 1 and parts[1] else None
        if not device_name and sql_only_mode and not _looks_like_ipv4(ip):
            device_name = ip
        local_vendor = parts[2] if len(parts) > 2 and parts[2] else vendor_hint

        if require_credentials and (not default_username or not default_password):
            raise ValueError("Batch text mode requires global username/password")

        devices.append(
            DeviceInput(
                device_ip=ip,
                device_port=device_port,
                device_name=device_name,
                username=default_username or "",
                password=default_password or "",
                vendor_hint=local_vendor,
                jump_mode=default_jump_mode,
                jump_host=jump_host,
                jump_port=jump_port,
                smc_command=smc_command,
            )
        )
    return devices


def parse_devices_from_csv(
    csv_bytes: bytes,
    default_username: str | None,
    default_password: str | None,
    default_jump_mode: str,
    jump_host: str | None,
    jump_port: int,
    smc_command: str | None,
) -> list[DeviceInput]:
    content = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    devices: list[DeviceInput] = []

    for row in reader:
        device_ip = (row.get("device_ip") or "").strip()
        if not device_ip:
            continue

        mode = (row.get("jump_mode") or "").strip() or default_jump_mode
        require_credentials = str(mode or "").strip().lower() != "smc_pam_nd"
        username = (row.get("username") or "").strip() or (default_username or "")
        password = (row.get("password") or "").strip() or (default_password or "")
        if require_credentials and (not username or not password):
            raise ValueError(f"CSV row for {device_ip} missing username/password")

        local_jump_host = (row.get("jump_host") or "").strip() or jump_host
        local_jump_port_str = (row.get("jump_port") or "").strip()
        local_jump_port = int(local_jump_port_str) if local_jump_port_str else jump_port

        local_smc_cmd = (row.get("smc_command") or "").strip() or smc_command
        if mode in {"smc", "smc_pam_nd"} and not local_smc_cmd:
            local_smc_cmd = default_smc_command(mode, local_jump_host)

        devices.append(
            DeviceInput(
                device_ip=device_ip,
                device_port=int((row.get("device_port") or "22").strip() or "22"),
                device_name=(row.get("device_name") or "").strip() or None,
                username=username,
                password=password,
                vendor_hint=(row.get("vendor_hint") or "").strip() or None,
                jump_mode=mode,
                jump_host=local_jump_host,
                jump_port=local_jump_port,
                smc_command=local_smc_cmd,
            )
        )
    return devices


def build_payload(form: dict[str, Any], devices: list[DeviceInput]) -> TaskCreatePayload:
    sql_only_mode = bool(form.get("sql_only_mode", False))
    payload = TaskCreatePayload(
        start_time=parse_time_or_raise(form["start_time"]),
        end_time=parse_time_or_raise(form["end_time"]),
        context_lines=int(form.get("context_lines", 3)),
        concurrency=max(1, int(form.get("concurrency", 10))),
        per_device_timeout=max(10, int(form.get("per_device_timeout", 60))),
        default_username=form.get("default_username") or None,
        default_password=form.get("default_password") or None,
        default_jump_mode=form.get("jump_mode", "direct"),
        jump_host=form.get("jump_host") or None,
        jump_port=int(form.get("jump_port") or 22),
        smc_command=form.get("smc_command") or default_smc_command(form.get("jump_mode", "direct"), form.get("jump_host")) or None,
        debug_mode=bool(form.get("debug_mode", False)),
        sql_query_mode=bool(form.get("sql_query_mode", False)) or sql_only_mode,
        sql_only_mode=sql_only_mode,
        db_host=form.get("db_host") or None,
        db_port=int(form.get("db_port") or 0) or None,
        db_user=form.get("db_user") or None,
        db_password=form.get("db_password") or None,
        db_name=form.get("db_name") or None,
        devices=devices,
    )
    if payload.end_time < payload.start_time:
        raise ValueError("end_time must be >= start_time")
    return payload
