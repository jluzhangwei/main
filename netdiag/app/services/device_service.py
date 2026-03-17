from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..connectors.smc_shell import SmcShellClient, SmcShellConfig
from ..connectors.ssh_direct import DirectSSHClient
from ..models import DeviceInput
from ..parsers.log_filter import filter_log_with_context
from ..parsers.time_parser import extract_device_profile, parse_device_clock, parse_log_line_timestamp


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _to_naive(dt: datetime) -> datetime:
    # Keep wall-clock fields and drop tzinfo so all comparisons use one timeline.
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _looks_like_cli_error(output: str) -> bool:
    low = output.lower()
    stripped_lines = [ln.strip().lower() for ln in output.splitlines() if ln.strip()]
    return (
        "unrecognized command" in low
        or "invalid command at '^' marker" in low
        or "% invalid command" in low
        or "% invalid input" in low
        or "% incomplete command" in low
        or "wrong parameter found" in low
        or ("^" in stripped_lines and any("invalid" in ln and "command" in ln for ln in stripped_lines))
    )


def _log_commands_for_os(vendor: str, os_family: str) -> list[str]:
    if vendor == "huawei":
        return ["display logbuffer", "dis logbuff"]
    if vendor == "paloalto":
        return [
            "show log system direction equal backward count equal 200",
            "show log system",
        ]
    if os_family == "cisco_iosxr":
        return ["show logging", "show log"]
    if os_family == "cisco_nxos":
        return ["show logging", "show log"]
    if vendor in ("cisco", "arista"):
        return ["show log", "show logging"]
    return ["show log", "show logging", "display logbuffer", "dis logbuff"]


def _extract_log_time_range(raw_log: str, vendor: str, reference_year: int) -> tuple[datetime | None, datetime | None]:
    log_ts_min = None
    log_ts_max = None
    for ln in raw_log.splitlines():
        ts = parse_log_line_timestamp(ln, vendor=vendor, reference_year=reference_year)
        if ts is None:
            continue
        if log_ts_min is None or ts < log_ts_min:
            log_ts_min = ts
        if log_ts_max is None or ts > log_ts_max:
            log_ts_max = ts
    return log_ts_min, log_ts_max


class DebugRecorder:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    async def write(self, line: str) -> None:
        self.sync_write(line)

    def sync_write(self, line: str) -> None:
        if self.path is None:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] {line}\n")


async def run_device_collection(
    device: DeviceInput,
    output_dir: Path,
    user_start: datetime,
    user_end: datetime,
    context_lines: int,
    per_device_timeout: int,
    debug_mode: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_path = output_dir / "debug.log" if debug_mode else None
    recorder = DebugRecorder(debug_path)

    try:
        if device.jump_mode == "smc":
            return await asyncio.wait_for(
                _run_device_via_smc(device, output_dir, user_start, user_end, context_lines, recorder),
                timeout=per_device_timeout,
            )
        return await asyncio.wait_for(
            _run_device_via_direct(device, output_dir, user_start, user_end, context_lines, recorder),
            timeout=per_device_timeout,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Device collection timeout after {int(per_device_timeout)}s") from exc


async def _run_device_via_direct(
    device: DeviceInput,
    output_dir: Path,
    user_start: datetime,
    user_end: datetime,
    context_lines: int,
    debug: DebugRecorder,
) -> dict[str, Any]:
    await debug.write(f"[DEVICE] {device.device_ip}:{device.device_port} mode=direct")
    cli = DirectSSHClient(
        host=device.device_ip,
        port=device.device_port,
        username=device.username,
        password=device.password,
        timeout=20,
        debug=debug.sync_write,
    )
    try:
        await cli.connect()
        return await _collect_using_exec(
            exec_func=lambda cmd, timeout=30: cli.exec(cmd, timeout),
            paging_func=lambda vendor: cli.try_disable_paging(vendor),
            output_dir=output_dir,
            vendor_hint=device.vendor_hint,
            user_start=user_start,
            user_end=user_end,
            context_lines=context_lines,
            debug=debug,
            paging_preconfigured=False,
        )
    finally:
        await cli.close()


async def _run_device_via_smc(
    device: DeviceInput,
    output_dir: Path,
    user_start: datetime,
    user_end: datetime,
    context_lines: int,
    debug: DebugRecorder,
) -> dict[str, Any]:
    await debug.write(f"[DEVICE] {device.device_ip}:{device.device_port} mode=smc")
    smc_cmd = device.smc_command or f"smc server toc {device.jump_host}"
    cli = SmcShellClient(
        SmcShellConfig(
            smc_command=smc_cmd,
            device_ip=device.device_ip,
            username=device.username,
            password=device.password,
            jump_host=device.jump_host,
            jump_port=int(device.jump_port or 22),
            timeout=30,
            debug=debug.sync_write,
        )
    )
    try:
        await asyncio.to_thread(cli.connect)

        async def exec_func(cmd: str, timeout: int = 30) -> str:
            return await asyncio.to_thread(cli.exec, cmd, timeout)

        async def paging_func(vendor: str) -> None:
            await asyncio.to_thread(cli.try_disable_paging, vendor)

        return await _collect_using_exec(
            exec_func=exec_func,
            paging_func=paging_func,
            output_dir=output_dir,
            vendor_hint=device.vendor_hint,
            user_start=user_start,
            user_end=user_end,
            context_lines=context_lines,
            debug=debug,
            paging_preconfigured=True,
        )
    finally:
        await asyncio.to_thread(cli.close)


async def _collect_using_exec(
    exec_func,
    paging_func,
    output_dir: Path,
    vendor_hint: str | None,
    user_start: datetime,
    user_end: datetime,
    context_lines: int,
    debug: DebugRecorder,
    paging_preconfigured: bool = False,
) -> dict[str, Any]:
    await debug.write("[FLOW] start collecting device information")
    raw_version = ""
    vendor = "unknown"
    os_family = vendor

    # Always detect vendor from version first, then run vendor-specific commands.
    version_cmd_candidates = ["show version", "display version", "show system info"]
    for ver_cmd in version_cmd_candidates:
        try:
            out = await exec_func(ver_cmd, 20)
        except Exception as exc:
            await debug.write(f"[FLOW] version cmd failed: {ver_cmd} err={exc}")
            continue
        await debug.write(f"[FLOW] version cmd tried: {ver_cmd}")
        if _looks_like_cli_error(out):
            await debug.write(f"[FLOW] version cmd rejected: {ver_cmd}")
            continue
        raw_version = out
        break

    detected_vendor, detected_os_family, model, version = extract_device_profile(raw_version)
    if detected_vendor != "unknown":
        vendor = detected_vendor
        os_family = detected_os_family
    elif vendor_hint:
        vendor = vendor_hint
        os_family = f"{vendor_hint}_hint"
    else:
        os_family = "unknown"

    await debug.write(
        f"[FLOW] vendor={vendor} os_family={os_family} model={model or '-'} version={version or '-'}"
    )
    if not paging_preconfigured:
        await paging_func(vendor)
    else:
        await debug.write("[FLOW] paging already preconfigured in connector, skip duplicate paging setup")

    if vendor == "huawei":
        clock_cmds = ["display clock", "show clock"]
    elif vendor == "paloalto":
        clock_cmds = ["show clock", "display clock"]
    elif vendor in ("cisco", "arista"):
        clock_cmds = ["show clock", "display clock"]
    else:
        clock_cmds = ["show clock", "display clock"]

    device_time = None
    clock_out = ""
    for clock_cmd in clock_cmds:
        try:
            out = await exec_func(clock_cmd, 20)
        except Exception as exc:
            await debug.write(f"[FLOW] clock cmd failed: {clock_cmd} err={exc}")
            continue
        await debug.write(f"[FLOW] clock cmd tried: {clock_cmd}")
        if _looks_like_cli_error(out):
            await debug.write(f"[FLOW] clock cmd rejected: {clock_cmd}")
            clock_out = out
            continue
        parsed = parse_device_clock(out, vendor=vendor)
        if parsed is not None:
            device_time = parsed
            clock_out = out
            break
        clock_out = out

    if device_time is None:
        clock_tail = clock_out.replace("\r", "\n").strip()[-400:]
        await debug.write(f"[ERROR] clock parse failed, raw_tail={clock_tail}")
        raise RuntimeError(f"Unable to parse device clock output: {clock_tail}")

    user_start = _to_naive(user_start)
    user_end = _to_naive(user_end)
    reference_time = datetime.now()
    offset = device_time - reference_time
    device_start = user_start + offset
    device_end = user_end + offset
    await debug.write(
        f"[FLOW] device_time={_iso(device_time)} reference_time={_iso(reference_time)} offset_seconds={offset.total_seconds()}"
    )

    raw_log = ""
    log_ts_min = None
    log_ts_max = None

    if os_family == "cisco_nxos":
        # NX-OS logs can be huge; progressively expand window to avoid long/full dumps.
        nxos_last_candidates = [1000, 3000, 8000, 16000]
        for n in nxos_last_candidates:
            cmd = f"show logging last {n}"
            try:
                out = await exec_func(cmd, 120)
            except Exception as exc:
                await debug.write(f"[FLOW] nxos log cmd failed: {cmd} err={exc}")
                continue
            if _looks_like_cli_error(out):
                await debug.write(f"[FLOW] nxos log cmd rejected: {cmd}")
                continue
            cand_min, cand_max = _extract_log_time_range(out, vendor=vendor, reference_year=device_time.year)
            await debug.write(
                f"[FLOW] nxos log candidate: {cmd} range=[{_iso(cand_min) if cand_min else None} ~ "
                f"{_iso(cand_max) if cand_max else None}]"
            )
            raw_log = out
            log_ts_min, log_ts_max = cand_min, cand_max
            if cand_min and cand_max and cand_min <= device_start and cand_max >= device_end:
                await debug.write(f"[FLOW] nxos log window covered with {cmd}")
                break

        if not raw_log:
            # Fallback if `last N` not supported.
            for cmd in ("show logging", "show log"):
                try:
                    out = await exec_func(cmd, 180)
                except Exception as exc:
                    await debug.write(f"[FLOW] nxos fallback cmd failed: {cmd} err={exc}")
                    continue
                if _looks_like_cli_error(out):
                    await debug.write(f"[FLOW] nxos fallback cmd rejected: {cmd}")
                    continue
                raw_log = out
                log_ts_min, log_ts_max = _extract_log_time_range(raw_log, vendor=vendor, reference_year=device_time.year)
                await debug.write(f"[FLOW] nxos fallback selected: {cmd}")
                break
    else:
        log_cmds = _log_commands_for_os(vendor, os_family)
        for cmd in log_cmds:
            try:
                out = await exec_func(cmd, 60)
            except Exception as exc:
                await debug.write(f"[FLOW] log cmd failed: {cmd} err={exc}")
                continue
            if _looks_like_cli_error(out):
                await debug.write(f"[FLOW] log cmd rejected: {cmd}")
                continue
            raw_log = out
            log_ts_min, log_ts_max = _extract_log_time_range(raw_log, vendor=vendor, reference_year=device_time.year)
            await debug.write(f"[FLOW] log cmd selected: {cmd}")
            break
        if not raw_log:
            raise RuntimeError(f"Unable to collect buffer log using commands: {', '.join(log_cmds)}")

    if not raw_log:
        raise RuntimeError("Unable to collect buffer log from device")

    # Safety cap for very large logs.
    max_log_lines = 40000
    lines = raw_log.splitlines()
    if len(lines) > max_log_lines:
        raw_log = "\n".join(lines[-max_log_lines:])
        await debug.write(f"[FLOW] raw log truncated to last {max_log_lines} lines")
        log_ts_min, log_ts_max = _extract_log_time_range(raw_log, vendor=vendor, reference_year=device_time.year)

    filter_result = filter_log_with_context(
        raw_log_text=raw_log,
        device_start=device_start,
        device_end=device_end,
        context_lines=context_lines,
        vendor=vendor,
        reference_year=device_time.year,
    )

    if log_ts_min is None and log_ts_max is None:
        log_ts_min, log_ts_max = _extract_log_time_range(raw_log, vendor=vendor, reference_year=device_time.year)

    raw_path = output_dir / "raw.log"
    filtered_path = output_dir / "filtered.log"
    meta_path = output_dir / "meta.json"

    raw_path.write_text(raw_log, encoding="utf-8")
    filtered_path.write_text(filter_result.text, encoding="utf-8")

    meta = {
        "vendor": vendor,
        "os_family": os_family,
        "model": model,
        "version": version,
        "reference_time": _iso(reference_time),
        "device_time": _iso(device_time),
        "offset_seconds": offset.total_seconds(),
        "device_start": _iso(device_start),
        "device_end": _iso(device_end),
        "log_time_min": _iso(log_ts_min) if log_ts_min else None,
        "log_time_max": _iso(log_ts_max) if log_ts_max else None,
        "hits_count": filter_result.hits_count,
        "blocks_count": filter_result.blocks_count,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    await debug.write(
        f"[FLOW] done hits={filter_result.hits_count} blocks={filter_result.blocks_count} "
        f"device_range=[{meta['device_start']} ~ {meta['device_end']}] "
        f"log_range=[{meta['log_time_min']} ~ {meta['log_time_max']}] "
        f"raw={raw_path.name} filtered={filtered_path.name}"
    )

    return {
        "vendor": vendor,
        "os_family": os_family,
        "model": model,
        "version": version,
        "reference_time": meta["reference_time"],
        "device_time": meta["device_time"],
        "offset_seconds": meta["offset_seconds"],
        "hits_count": filter_result.hits_count,
        "blocks_count": filter_result.blocks_count,
        "raw_log_path": str(raw_path),
        "filtered_log_path": str(filtered_path),
        "meta_path": str(meta_path),
        "debug_log_path": str(debug.path) if debug.path else None,
    }
