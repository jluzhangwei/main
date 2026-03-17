from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..connectors.smc_shell import SmcShellClient, SmcShellConfig
from ..connectors.ssh_direct import DirectSSHClient
from ..diagnosis.policy import is_read_only_command
from ..models import DeviceInput
from ..parsers.time_parser import extract_device_profile
from .device_service import _looks_like_cli_error


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


def _safe_file_token(text: str, max_len: int = 36) -> str:
    token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "").strip())
    token = token.strip("._")
    if not token:
        token = "cmd"
    return token[:max_len]


async def _detect_vendor(exec_func) -> str:
    for cmd in ("show version", "display version", "show system info"):
        try:
            out = await exec_func(cmd, 20)
        except Exception:
            continue
        vendor, _, _, _ = extract_device_profile(out)
        if vendor != "unknown":
            return vendor
    return "unknown"


async def run_read_only_commands(
    device: DeviceInput,
    commands: list[str],
    output_dir: Path,
    timeout_per_command: int = 30,
    debug_mode: bool = True,
) -> list[dict[str, Any]]:
    for cmd in commands:
        if not is_read_only_command(cmd):
            raise RuntimeError(f"command not allowed by policy: {cmd}")

    output_dir.mkdir(parents=True, exist_ok=True)
    debug_path = output_dir / "debug.log" if debug_mode else None
    recorder = DebugRecorder(debug_path)
    results: list[dict[str, Any]] = []

    if device.jump_mode == "smc":
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
                debug=recorder.sync_write,
            )
        )
        try:
            await asyncio.to_thread(cli.connect)

            async def exec_func(cmd: str, timeout: int = 30) -> str:
                return await asyncio.to_thread(cli.exec, cmd, timeout)

            vendor = await _detect_vendor(exec_func)
            try:
                await asyncio.to_thread(cli.try_disable_paging, vendor)
            except Exception:
                pass

            for idx, cmd in enumerate(commands, start=1):
                started = time.monotonic()
                status = "success"
                err = ""
                out = ""
                try:
                    out = await exec_func(cmd, timeout_per_command)
                except Exception as exc:
                    status = "failed"
                    err = str(exc)
                if status == "success" and _looks_like_cli_error(out):
                    status = "error_output"
                    if not err:
                        err = "cli returned syntax/unsupported output"
                duration = round(time.monotonic() - started, 3)
                file_name = f"{idx:02d}_{_safe_file_token(cmd)}.txt"
                out_file = output_dir / file_name
                out_file.write_text(out or "", encoding="utf-8")
                results.append(
                    {
                        "command": cmd,
                        "status": status,
                        "output_file": out_file.as_posix(),
                        "error": err,
                        "duration_sec": duration,
                        "debug_log_path": debug_path.as_posix() if debug_path else None,
                    }
                )
        finally:
            await asyncio.to_thread(cli.close)
        return results

    cli = DirectSSHClient(
        host=device.device_ip,
        port=device.device_port,
        username=device.username,
        password=device.password,
        timeout=20,
        debug=recorder.sync_write,
    )
    try:
        await cli.connect()
        vendor = await _detect_vendor(cli.exec)
        try:
            await cli.try_disable_paging(vendor)
        except Exception:
            pass

        for idx, cmd in enumerate(commands, start=1):
            started = time.monotonic()
            status = "success"
            err = ""
            out = ""
            try:
                out = await cli.exec(cmd, timeout_per_command)
            except Exception as exc:
                status = "failed"
                err = str(exc)
            if status == "success" and _looks_like_cli_error(out):
                status = "error_output"
                if not err:
                    err = "cli returned syntax/unsupported output"
            duration = round(time.monotonic() - started, 3)
            file_name = f"{idx:02d}_{_safe_file_token(cmd)}.txt"
            out_file = output_dir / file_name
            out_file.write_text(out or "", encoding="utf-8")
            results.append(
                {
                    "command": cmd,
                    "status": status,
                    "output_file": out_file.as_posix(),
                    "error": err,
                    "duration_sec": duration,
                    "debug_log_path": debug_path.as_posix() if debug_path else None,
                }
            )
    finally:
        await cli.close()

    return results
