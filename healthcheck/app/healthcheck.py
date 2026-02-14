#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import getpass
import ipaddress
import csv
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import dataclass
import math
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import paramiko
import yaml

PRINT_LOCK = threading.Lock()
DEBUG_VERBOSE = False
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DEFAULT_COMMAND_MAP_REL = Path("config") / "command_map.yaml"
DEFAULT_REPORT_DIR_REL = Path("output") / "reports"


def safe_print(message: str) -> None:
    """Thread-safe print to avoid interleaved logs."""
    with PRINT_LOCK:
        print(message, flush=True)


def debug_print(message: str) -> None:
    """Print debug details only when debug mode is enabled."""
    if DEBUG_VERBOSE:
        safe_print(message)


@dataclass
class CommandResult:
    """Result of a single command execution."""

    command: str
    output: str
    error: str
    exit_status: int

    @property
    def success(self) -> bool:
        return self.exit_status == 0


class HuaweiSwitchManager:
    """Interactive shell-based network device manager (Huawei/Cisco compatible)."""

    def __init__(
        self,
        username: str,
        password: str,
        port: int = 22,
        connect_timeout: int = 10,
        command_timeout: int = 30,
        command_interval: float = 0.0,
        jump_enabled: bool = False,
        jump_mode: str = "ssh",
        jump_host: str = "",
        jump_port: int = 22,
        jump_username: str = "",
        jump_password: str = "",
        smc_command: str = "smc server toc {jump_host}",
    ):
        self.username = username
        self.password = password
        self.port = port
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.command_interval = command_interval
        self.jump_enabled = bool(jump_enabled and jump_host and jump_username)
        self.jump_mode = jump_mode if jump_mode in {"ssh", "smc"} else "ssh"
        self.jump_host = jump_host
        self.jump_port = int(jump_port or 22)
        self.jump_username = jump_username
        self.jump_password = jump_password
        self.smc_command = smc_command or "smc server toc {jump_host}"
        if self.jump_mode == "smc":
            self.jump_enabled = bool(jump_enabled and jump_host)
        self.client = None
        self.jump_client = None
        self.jump_channel = None
        self.proxy_sock = None
        self.shell = None
        self.smc_proc = None
        self.smc_master_fd = None
        self.smc_mode_active = False

    def connect(self, hostname: str) -> bool:
        """Connect to a device."""
        self.disconnect()

        try:
            sock = None
            if self.jump_enabled and self.jump_mode == "ssh":
                self.jump_client = paramiko.SSHClient()
                self.jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.jump_client.connect(
                    hostname=self.jump_host,
                    username=self.jump_username,
                    password=self.jump_password,
                    port=self.jump_port,
                    timeout=self.connect_timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                transport = self.jump_client.get_transport()
                if not transport:
                    raise RuntimeError("jump host transport is unavailable")
                self.jump_channel = transport.open_channel(
                    "direct-tcpip",
                    (hostname, self.port),
                    ("127.0.0.1", 0),
                )
                sock = self.jump_channel
            elif self.jump_enabled and self.jump_mode == "smc":
                return self._connect_via_smc_shell(hostname)

            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                hostname=hostname,
                username=self.username,
                password=self.password,
                port=self.port,
                timeout=self.connect_timeout,
                look_for_keys=False,
                allow_agent=False,
                sock=sock,
            )

            self.shell = self.client.invoke_shell(width=240, height=4000)
            self._read_until_prompt(timeout=8)

            # Try pager-off commands for different vendors; ignore failures.
            for pager_cmd in (
                "screen-length 0 temporary",
                "screen-length 0",
                "terminal length 0",
                "set cli screen-length 0",
            ):
                self.shell.send(f"{pager_cmd}\n")
                self._read_until_prompt(timeout=1)

            return True
        except paramiko.AuthenticationException:
            print(f"Authentication failed: cannot use username {self.username} on {hostname}")
        except paramiko.SSHException as exc:
            print(f"SSH error ({hostname}): {exc}")
        except Exception as exc:
            print(f"Failed to connect to {hostname}: {exc}")

        self.disconnect()
        return False

    def execute_command(self, command: str) -> CommandResult:
        """Run a command on the device."""
        if self.smc_mode_active:
            return self._execute_command_via_smc(command)
        if not self.shell:
            return CommandResult(command=command, output="", error="device is not connected", exit_status=1)

        try:
            self.shell.send(f"{command}\n")
            raw_output = self._read_until_prompt(timeout=self.command_timeout)
            output = self._clean_shell_output(raw_output, command)

            error = ""
            exit_status = 0
            lowered = output.lower()
            has_caret_error_marker = bool(re.search(r"(?m)^\s*\^\s*$", output))
            if (
                "error:" in lowered
                or "unrecognized command" in lowered
                or "wrong parameter found" in lowered
                or "% invalid input" in lowered
                or "% incomplete command" in lowered
                or has_caret_error_marker
            ):
                error = output
                exit_status = 1

            return CommandResult(command=command, output=output, error=error, exit_status=exit_status)
        except Exception as exc:
            return CommandResult(command=command, output="", error=f"Command execution failed: {exc}", exit_status=1)

    def disconnect(self):
        """Disconnect from the device."""
        self.smc_mode_active = False
        if self.shell:
            self.shell.close()
            self.shell = None
        if self.client:
            self.client.close()
            self.client = None
        if self.jump_channel:
            self.jump_channel.close()
            self.jump_channel = None
        if self.jump_client:
            self.jump_client.close()
            self.jump_client = None
        if self.proxy_sock:
            self.proxy_sock.close()
            self.proxy_sock = None
        if self.smc_master_fd is not None:
            try:
                os.close(self.smc_master_fd)
            except Exception:
                pass
            self.smc_master_fd = None
        if self.smc_proc:
            try:
                if self.smc_proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(self.smc_proc.pid), signal.SIGTERM)
                    except Exception:
                        self.smc_proc.terminate()
                    try:
                        self.smc_proc.wait(timeout=2)
                    except Exception:
                        try:
                            os.killpg(os.getpgid(self.smc_proc.pid), signal.SIGKILL)
                        except Exception:
                            self.smc_proc.kill()
            except Exception:
                pass
            self.smc_proc = None

    def _connect_via_smc_shell(self, hostname: str) -> bool:
        if os.name == "nt":
            raise RuntimeError("SMC mode is currently supported on POSIX systems only")

        cmd = str(self.smc_command).strip() or "smc server toc {jump_host}"
        cmd = cmd.replace("{jump_host}", self.jump_host).replace("{jump_port}", str(self.jump_port))
        if "{jump_host}" not in str(self.smc_command) and self.jump_host and self.jump_host not in cmd:
            cmd = f"{cmd} {self.jump_host}".strip()
        debug_print(f"[SMC] start jump command: {cmd}")

        master_fd, slave_fd = pty.openpty()
        try:
            self.smc_proc = subprocess.Popen(
                ["bash", "-lc", cmd],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid,
            )
        finally:
            try:
                os.close(slave_fd)
            except Exception:
                pass
        self.smc_master_fd = master_fd

        prompt_pattern = re.compile(r"(?m)^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
        jump_prompt_pattern = re.compile(r"(?m)^.*[@].*[$#]\s*$")
        yes_pattern = re.compile(r"\(yes/no(?:/\[fingerprint\])?\)\??", re.IGNORECASE)
        pwd_pattern = re.compile(r"(enter\s+password|password)\s*:\s*$", re.IGNORECASE | re.MULTILINE)
        fail_pattern = re.compile(
            r"(permission denied|connection timed out|could not resolve|connection refused|no route to host|closed by remote host)",
            re.IGNORECASE,
        )
        ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

        # Phase 1: wait jump-host shell ready after `smc ...`
        jump_deadline = time.time() + max(20, self.connect_timeout * 2)
        jump_buffer = ""
        jump_ready = False
        while time.time() < jump_deadline:
            chunk = self._smc_read_for(1.0)
            if not chunk:
                continue
            jump_buffer += chunk
            if len(jump_buffer) > 20000:
                jump_buffer = jump_buffer[-20000:]
            normalized = ansi_pattern.sub("", jump_buffer).replace("\r", "")
            tail = normalized[-800:]
            debug_print(f"[SMC][jump] {tail}")
            if fail_pattern.search(normalized):
                raise RuntimeError("SMC jump command failed before device login")
            if yes_pattern.search(normalized):
                self._smc_send("yes\n")
                continue
            if jump_prompt_pattern.search(tail) or "Last login:" in tail:
                jump_ready = True
                break
        if not jump_ready:
            tail = ansi_pattern.sub("", jump_buffer).replace("\r", "")[-500:]
            raise RuntimeError(f"SMC jump shell timeout. tail={tail}")

        # Phase 2: ssh from jump shell to target device
        self._smc_send(f"ssh -o StrictHostKeyChecking=no {self.username}@{hostname}\n")
        deadline = time.time() + max(20, self.connect_timeout * 3)
        buffer = ""
        password_attempts = 0
        max_password_attempts = 3
        while time.time() < deadline:
            chunk = self._smc_read_for(1.0)
            if not chunk:
                continue
            buffer += chunk
            if len(buffer) > 20000:
                buffer = buffer[-20000:]
            normalized = ansi_pattern.sub("", buffer).replace("\r", "")
            debug_print(f"[SMC][device] {normalized[-800:]}")
            if fail_pattern.search(normalized):
                raise RuntimeError("SMC jump login failed while ssh to target device")
            if yes_pattern.search(normalized):
                self._smc_send("yes\n")
                continue
            if pwd_pattern.search(normalized):
                if password_attempts < max_password_attempts:
                    password_attempts += 1
                    debug_print(f"[SMC] password prompt detected, sending password attempt={password_attempts}")
                    self._smc_send(self.password + "\n")
                    # Clear consumed challenge to avoid repeatedly matching historical prompt text.
                    buffer = ""
                else:
                    raise RuntimeError("SMC auth failed: password prompt repeated too many times")
                continue
            tail = normalized[-4000:]
            if prompt_pattern.search(tail):
                self.smc_mode_active = True
                break

        if not self.smc_mode_active:
            tail = ansi_pattern.sub("", buffer).replace("\r", "")[-500:]
            raise RuntimeError(f"SMC mode timeout: cannot reach target device prompt. tail={tail}")

        for pager_cmd in (
            "screen-length 0 temporary",
            "screen-length 0",
            "terminal length 0",
            "set cli screen-length 0",
        ):
            self._smc_send(f"{pager_cmd}\n")
            self._smc_read_until_prompt_via_smc(timeout=1)
        return True

    def _execute_command_via_smc(self, command: str) -> CommandResult:
        if not self.smc_mode_active or self.smc_master_fd is None:
            return CommandResult(command=command, output="", error="SMC session is not connected", exit_status=1)
        try:
            self._smc_send(f"{command}\n")
            raw_output = self._smc_read_until_prompt_via_smc(timeout=self.command_timeout)
            output = self._clean_shell_output(raw_output, command)
            error = ""
            exit_status = 0
            lowered = output.lower()
            has_caret_error_marker = bool(re.search(r"(?m)^\s*\^\s*$", output))
            if (
                "error:" in lowered
                or "unrecognized command" in lowered
                or "wrong parameter found" in lowered
                or "% invalid input" in lowered
                or "% incomplete command" in lowered
                or has_caret_error_marker
            ):
                error = output
                exit_status = 1
            return CommandResult(command=command, output=output, error=error, exit_status=exit_status)
        except Exception as exc:
            return CommandResult(command=command, output="", error=f"SMC command execution failed: {exc}", exit_status=1)

    def _smc_send(self, text: str) -> None:
        if self.smc_master_fd is None:
            return
        os.write(self.smc_master_fd, text.encode("utf-8", errors="ignore"))

    def _smc_read_for(self, timeout: float) -> str:
        if self.smc_master_fd is None:
            return ""
        data_parts: List[str] = []
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            left = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([self.smc_master_fd], [], [], min(0.2, left))
            if not rlist:
                continue
            try:
                chunk = os.read(self.smc_master_fd, 65535)
            except OSError:
                break
            if not chunk:
                break
            data_parts.append(chunk.decode("utf-8", errors="replace"))
            if len(chunk) < 65535:
                break
        return "".join(data_parts)

    def _smc_read_until_prompt_via_smc(self, timeout: int = 30) -> str:
        prompt_pattern = re.compile(r"(?m)^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
        ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        chunks: List[str] = []
        deadline = time.time() + timeout
        last_recv_ts = time.time()

        while time.time() < deadline:
            piece = self._smc_read_for(0.3)
            if piece:
                chunks.append(piece)
                last_recv_ts = time.time()
                tail = "".join(chunks)[-3000:]
                normalized_tail = ansi_pattern.sub("", tail).replace("\r", "")
                if prompt_pattern.search(normalized_tail) and (time.time() - last_recv_ts) >= 0.1:
                    break
            else:
                if chunks and (time.time() - last_recv_ts) >= 0.2:
                    tail = "".join(chunks)[-3000:]
                    normalized_tail = ansi_pattern.sub("", tail).replace("\r", "")
                    if prompt_pattern.search(normalized_tail):
                        break
                time.sleep(0.05)
        return "".join(chunks)

    def _read_until_prompt(self, timeout: int = 30) -> str:
        """Read from interactive shell until a prompt is detected."""
        if not self.shell:
            return ""

        # Huawei: <HUAWEI> / [HUAWEI]
        # Cisco: Router# / Router> / Router(config)#
        prompt_pattern = re.compile(
            r"(?m)^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$"
        )
        ansi_pattern = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

        chunks: List[str] = []
        deadline = time.time() + timeout
        last_recv_ts = time.time()

        while time.time() < deadline:
            if self.shell.recv_ready():
                data = self.shell.recv(65535).decode("utf-8", errors="replace")
                if data:
                    chunks.append(data)
                    last_recv_ts = time.time()

                    tail = "".join(chunks)[-3000:]
                    normalized_tail = ansi_pattern.sub("", tail).replace("\r", "")
                    if prompt_pattern.search(normalized_tail) and (time.time() - last_recv_ts) >= 0.1:
                        break
            else:
                if chunks and (time.time() - last_recv_ts) >= 0.2:
                    tail = "".join(chunks)[-3000:]
                    normalized_tail = ansi_pattern.sub("", tail).replace("\r", "")
                    if prompt_pattern.search(normalized_tail):
                        break
                time.sleep(0.1)

        return "".join(chunks)

    @staticmethod
    def _clean_shell_output(raw_output: str, command: str) -> str:
        """Clean command echo and prompt lines, keep main output only."""
        lines = [line.rstrip("\r") for line in raw_output.splitlines()]

        while lines and not lines[0].strip():
            lines.pop(0)

        if lines and lines[0].strip() == command.strip():
            lines.pop(0)

        while lines and not lines[-1].strip():
            lines.pop()

        if lines and re.match(r"^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>]+>|\[[^\]]+\])\s*$", lines[-1].strip()):
            lines.pop()

        return "\n".join(lines).strip()


def _load_list_from_file(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip() and not line.strip().startswith("#")]


def _split_inline_items(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]


def _deduplicate(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))


def read_password(prompt: str = "SSH password: ") -> str:
    """
    Support both interactive terminal and piped stdin.
    On Windows subprocess with piped stdin, getpass may block;
    fallback to plain input when stdin is non-tty.
    """
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return getpass.getpass(prompt)
    except Exception:
        pass
    return input(prompt)


def parse_input_items(input_str: str, item_name: str) -> List[str]:
    """
    Parse input:
    - If input is a file path, read one item per line.
    - Otherwise split by comma, semicolon, or newline.
    """
    raw = input_str.strip()
    if not raw:
        return []

    # If user already entered comma/semicolon/newline separated values,
    # treat it as inline list directly instead of probing filesystem path.
    if any(sep in raw for sep in [",", ";", "\n"]):
        return _deduplicate(_split_inline_items(raw))

    def _safe_is_file(path: Path) -> bool:
        try:
            return path.is_file()
        except OSError:
            return False

    raw_path = Path(raw)
    if raw_path.is_absolute():
        candidate = raw_path
    else:
        cwd_candidate = Path.cwd() / raw_path
        project_candidate = PROJECT_ROOT / raw_path
        candidate = cwd_candidate if _safe_is_file(cwd_candidate) else project_candidate

    if _safe_is_file(candidate):
        try:
            return _deduplicate(_load_list_from_file(str(candidate.resolve())))
        except Exception as exc:
            print(f"Failed to read {item_name} file: {exc}")
            return []

    return _deduplicate(_split_inline_items(raw))


def is_valid_device(device: str) -> bool:
    """Validate device address (IPv4/IPv6/hostname)."""
    try:
        ipaddress.ip_address(device)
        return True
    except ValueError:
        pass

    if ".." in device:
        return False

    return bool(re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$", device))


def get_device_list(input_str: str) -> List[str]:
    devices = parse_input_items(input_str, item_name="device")
    valid_devices = [device for device in devices if is_valid_device(device)]

    invalid_devices = [device for device in devices if device not in valid_devices]
    if invalid_devices:
        print(f"Invalid device addresses were skipped: {', '.join(invalid_devices)}")

    return valid_devices


def get_command_list(input_str: str) -> List[str]:
    return parse_input_items(input_str, item_name="command")


def load_command_map(path: str) -> Optional[Dict]:
    """Load command mapping file."""
    raw_path = Path(path)
    map_path = raw_path if raw_path.is_absolute() else (PROJECT_ROOT / raw_path)
    map_path = map_path.resolve()
    if not os.path.isfile(map_path):
        print(f"Command map file not found: {map_path}")
        return None

    try:
        with open(map_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict) or "profiles" not in data:
            print(f"Invalid command map format: {map_path}")
            return None
        return data
    except Exception as exc:
        print(f"Failed to read command map: {exc}")
        return None


def profile_matches(profile: Dict, version_output: str) -> bool:
    """Check whether version output matches a profile rule."""
    match = profile.get("match", {})
    if not isinstance(match, dict):
        return False

    def _ok(rule: Dict) -> bool:
        pattern = rule.get("regex", "")
        if not pattern:
            return False
        return bool(re.search(pattern, version_output))

    if "all" in match:
        rules = match.get("all") or []
        return bool(rules) and all(_ok(rule) for rule in rules if isinstance(rule, dict))

    if "any" in match:
        rules = match.get("any") or []
        return any(_ok(rule) for rule in rules if isinstance(rule, dict))

    return False


def detect_device_profile(manager: HuaweiSwitchManager, command_map: Dict) -> Tuple[str, str]:
    """Detect device profile, return (profile_id, version_raw_output)."""
    probe_outputs: List[str] = []
    for probe_cmd in ("display version", "show version"):
        result = manager.execute_command(probe_cmd)
        if result.output:
            probe_outputs.append(result.output)
        if result.success and result.output:
            break

    version_text = "\n".join(probe_outputs).strip()
    if not version_text:
        return "generic.unknown", ""

    for profile in command_map.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get("id", "")
        if not profile_id:
            continue
        if profile_matches(profile, version_text):
            return profile_id, version_text

    return "generic.unknown", version_text


def find_profile_by_id(command_map: Dict, profile_id: str) -> Optional[Dict]:
    for profile in command_map.get("profiles", []):
        if isinstance(profile, dict) and profile.get("id") == profile_id:
            return profile
    return None


def resolve_intent_commands(command_map: Dict, profile_id: str, intent: str) -> List[str]:
    """Resolve candidate commands by profile and intent (ordered by priority)."""
    candidates: List[str] = []
    visited: set = set()

    vendor_prefix = ""
    if "." in profile_id:
        vendor_prefix = profile_id.split(".", 1)[0]

    search_order: List[str] = [profile_id]
    if vendor_prefix:
        search_order.append(f"{vendor_prefix}.default")
    search_order.append("generic.unknown")

    for pid in search_order:
        profile = find_profile_by_id(command_map, pid)
        if not profile:
            continue
        commands = profile.get("commands", {}).get(intent, [])
        for cmd in commands:
            if cmd and cmd not in visited:
                visited.add(cmd)
                candidates.append(cmd)

    return candidates


def execute_item_with_mapping(
    manager: HuaweiSwitchManager,
    command_map: Optional[Dict],
    profile_id: str,
    raw_item: str,
) -> Tuple[bool, List[CommandResult]]:
    """Execute one input item (raw command or @intent). Return (success, executed results)."""
    raw_item = raw_item.strip()
    if not raw_item:
        return False, []

    if not raw_item.startswith("@"):
        result = manager.execute_command(raw_item)
        return result.success, [result]

    intent = raw_item[1:].strip()
    if not intent:
        return False, [CommandResult(command=raw_item, output="", error="empty intent", exit_status=1)]

    if not command_map:
        return False, [
            CommandResult(
                command=raw_item,
                output="",
                error="command_map.yaml is not loaded, cannot resolve intent",
                exit_status=1,
            )
        ]

    candidates = resolve_intent_commands(command_map, profile_id, intent)
    if not candidates:
        return False, [
            CommandResult(
                command=raw_item,
                output="",
                error=f"No mapping found for intent: {intent} (profile={profile_id})",
                exit_status=1,
            )
        ]

    results: List[CommandResult] = []
    for cmd in candidates:
        result = manager.execute_command(cmd)
        results.append(result)
        if result.success:
            return True, results

    return False, results


def _first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text else ""


def _preview(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def save_structured_report(
    device_reports: List[Dict],
    summary: Dict[str, int],
    report_dir: str = str(DEFAULT_REPORT_DIR_REL),
) -> Tuple[str, str]:
    """Save structured report (JSON + CSV), return both output paths."""
    output_dir = Path(report_dir)
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    ms = f"{now.microsecond // 1000:03d}"
    uid = uuid4().hex[:6]
    report_id = f"{ts}_{ms}_{uid}"
    json_path = output_dir / f"inspection_report_{report_id}.json"
    csv_path = output_dir / f"inspection_report_{report_id}.csv"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "devices": device_reports,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "device",
                "profile_id",
                "item",
                "item_success",
                "item_elapsed_sec",
                "attempt_index",
                "attempt_command",
                "attempt_success",
                "attempt_exit_status",
                "attempt_error_first_line",
                "attempt_output_preview",
                "attempt_output_full",
            ],
        )
        writer.writeheader()
        for device_report in device_reports:
            for item in device_report.get("items", []):
                attempts = item.get("attempts", [])
                if not attempts:
                    writer.writerow(
                        {
                            "device": device_report.get("device", ""),
                            "profile_id": device_report.get("profile_id", ""),
                            "item": item.get("item", ""),
                            "item_success": item.get("success", False),
                            "item_elapsed_sec": item.get("elapsed_sec", 0),
                            "attempt_index": 0,
                            "attempt_command": "",
                            "attempt_success": False,
                            "attempt_exit_status": "",
                            "attempt_error_first_line": "",
                            "attempt_output_preview": "",
                            "attempt_output_full": "",
                        }
                    )
                    continue

                for idx, attempt in enumerate(attempts, start=1):
                    writer.writerow(
                        {
                            "device": device_report.get("device", ""),
                            "profile_id": device_report.get("profile_id", ""),
                            "item": item.get("item", ""),
                            "item_success": item.get("success", False),
                            "item_elapsed_sec": item.get("elapsed_sec", 0),
                            "attempt_index": idx,
                            "attempt_command": attempt.get("command", ""),
                            "attempt_success": attempt.get("success", False),
                            "attempt_exit_status": attempt.get("exit_status", ""),
                            "attempt_error_first_line": attempt.get("error_first_line", ""),
                            "attempt_output_preview": attempt.get("output_preview", ""),
                            "attempt_output_full": attempt.get("output_full", ""),
                        }
                    )

    return str(json_path), str(csv_path)


def process_device(
    device: str,
    username: str,
    password: str,
    commands: List[str],
    command_map: Optional[Dict],
    manager_options: Dict,
) -> Dict:
    """Per-device execution logic (run inside worker thread)."""
    manager = HuaweiSwitchManager(
        username=username,
        password=password,
        port=manager_options.get("port", 22),
        connect_timeout=manager_options.get("connect_timeout", 10),
        command_timeout=manager_options.get("command_timeout", 30),
        command_interval=manager_options.get("command_interval", 0.0),
        jump_enabled=bool(manager_options.get("jump_enabled", False)),
        jump_mode=str(manager_options.get("jump_mode", "ssh") or "ssh"),
        jump_host=str(manager_options.get("jump_host", "") or ""),
        jump_port=int(manager_options.get("jump_port", 22) or 22),
        jump_username=str(manager_options.get("jump_username", "") or ""),
        jump_password=str(manager_options.get("jump_password", "") or ""),
        smc_command=str(manager_options.get("smc_command", "smc server toc {jump_host}") or "smc server toc {jump_host}"),
    )

    device_start = time.time()
    device_report: Dict = {
        "device": device,
        "connected": False,
        "profile_id": "generic.unknown",
        "items": [],
        "stats": {"total_items": 0, "successful_items": 0, "failed_items": 0},
        "elapsed_sec": 0.0,
    }

    retry_limit = int(manager_options.get("connect_retry", 0))
    connected = False
    connect_attempts = 0
    for attempt in range(retry_limit + 1):
        connect_attempts += 1
        if manager.connect(device):
            connected = True
            break
        if attempt < retry_limit:
            time.sleep(0.5)

    device_report["connect_attempts"] = connect_attempts
    if not connected:
        device_report["connect_error"] = "connect_failed"
        device_report["elapsed_sec"] = round(time.time() - device_start, 3)
        debug_print(f"[{device}] connect failed (attempts={connect_attempts})")
        return device_report

    device_report["connected"] = True
    debug_print(f"[{device}] connected")

    try:
        if command_map:
            profile_id, _ = detect_device_profile(manager, command_map)
            device_report["profile_id"] = profile_id
            debug_print(f"[{device}] profile={profile_id}")

        total_cmds = len(commands)
        for cmd_idx, item in enumerate(commands, start=1):
            item_start = time.time()
            ok, results = execute_item_with_mapping(manager, command_map, device_report["profile_id"], item)

            attempts: List[Dict] = []
            for result in results:
                attempts.append(
                    {
                        "command": result.command,
                        "success": result.success,
                        "exit_status": result.exit_status,
                        "error_first_line": _first_line(result.error),
                        "output_preview": _preview(result.output),
                        "output_full": result.output,
                    }
                )

            device_report["items"].append(
                {
                    "item": item,
                    "success": ok,
                    "elapsed_sec": round(time.time() - item_start, 3),
                    "attempt_count": len(results),
                    "attempts": attempts,
                }
            )

            device_report["stats"]["total_items"] += 1
            if ok:
                device_report["stats"]["successful_items"] += 1
            else:
                device_report["stats"]["failed_items"] += 1

            debug_print(
                f"[{device}] item {cmd_idx}/{total_cmds} | "
                f"{'OK' if ok else 'FAIL'} | {item} | "
                f"{device_report['items'][-1]['elapsed_sec']:.2f}s | attempts={len(results)}"
            )

            if manager.command_interval > 0:
                time.sleep(manager.command_interval)
    finally:
        manager.disconnect()
        device_report["elapsed_sec"] = round(time.time() - device_start, 3)

    return device_report


def main():
    """Main entry point."""
    global DEBUG_VERBOSE
    print("Network Device Batch Management Tool")
    print("=" * 50)

    username = input("SSH username: ").strip()
    password = read_password("SSH password: ").strip()

    if not username or not password:
        print("Username and password cannot be empty.")
        return

    map_input = input(f"\nCommand map file (default: {DEFAULT_COMMAND_MAP_REL}): ").strip()
    map_path = map_input or str(DEFAULT_COMMAND_MAP_REL)
    command_map = load_command_map(map_path)
    if command_map:
        resolved_map = (Path(map_path) if Path(map_path).is_absolute() else (PROJECT_ROOT / map_path)).resolve()
        print(f"Loaded command map: {resolved_map}")
    else:
        print("Command map not loaded, raw commands only.")

    device_input = input("\nDevice addresses (comma/semicolon/newline) or file path: ").strip()
    devices = get_device_list(device_input)
    if not devices:
        print("No valid device addresses found.")
        return

    command_input = input(
        "\nCommands or intents (e.g. @version), comma/semicolon/newline, or file path: "
    ).strip()
    commands = get_command_list(command_input)
    if not commands:
        print("No valid commands found.")
        return

    mode_input = input("\nExecution mode serial/parallel/auto (default: auto): ").strip().lower()
    mode = mode_input or "auto"
    if mode not in {"serial", "parallel", "auto"}:
        print("Invalid mode, fallback to auto.")
        mode = "auto"

    workers_input = input("Parallel workers (optional, auto if empty): ").strip()
    try:
        requested_workers = int(workers_input) if workers_input else None
    except ValueError:
        print("Invalid workers input, using auto recommendation.")
        requested_workers = None

    retry_input = input("Connection retry count (default: 0): ").strip()
    try:
        connect_retry = int(retry_input) if retry_input else 0
    except ValueError:
        print("Invalid retry count, using default 0.")
        connect_retry = 0
    connect_retry = max(0, connect_retry)

    jump_mode_input = input("Jump mode direct/ssh/smc (default: direct): ").strip().lower()
    jump_mode = jump_mode_input or "direct"
    if jump_mode not in {"direct", "ssh", "smc"}:
        print("Invalid jump mode, fallback to direct.")
        jump_mode = "direct"
    jump_enabled = jump_mode in {"ssh", "smc"}
    jump_host = ""
    jump_port = 22
    jump_username = ""
    jump_password = ""
    smc_command = "smc server toc {jump_host}"
    if jump_mode == "ssh":
        jump_host = input("Jump host address: ").strip()
        jump_port_input = input("Jump host port (default: 22): ").strip()
        try:
            jump_port = int(jump_port_input) if jump_port_input else 22
        except ValueError:
            print("Invalid jump host port, using default 22.")
            jump_port = 22
        jump_username = input("Jump host username: ").strip()
        jump_password = read_password("Jump host password: ").strip()
        if not jump_host or not jump_username or not jump_password:
            print("Jump host parameters are incomplete, fallback to direct mode.")
            jump_enabled = False
            jump_mode = "direct"
    elif jump_mode == "smc":
        jump_host = input("Jump host address: ").strip()
        smc_cmd_input = input("SMC command template (default: smc server toc {jump_host}): ").strip()
        smc_command = smc_cmd_input or "smc server toc {jump_host}"
        if not jump_host:
            print("Jump host address is empty, fallback to direct mode.")
            jump_enabled = False
            jump_mode = "direct"

    debug_input = input("Enable live debug output? (y/N): ").strip().lower()
    DEBUG_VERBOSE = debug_input in {"y", "yes", "1", "true"}

    recommended_workers = min(10, max(2, math.ceil(len(devices) / 4)))
    if mode == "serial" or (mode == "auto" and len(devices) <= 3):
        max_workers = 1
        effective_mode = "serial"
    else:
        effective_mode = "parallel"
        max_workers = requested_workers if requested_workers else recommended_workers
        max_workers = max(1, min(max_workers, len(devices)))

    print(
        f"Execution config: mode={effective_mode}, workers={max_workers}, "
        f"recommended_workers={recommended_workers}, connect_retry={connect_retry}, "
        f"jump_mode={jump_mode}, jump_enabled={jump_enabled}, debug={DEBUG_VERBOSE}"
    )

    connected_devices = 0
    failed_devices = 0
    total_items = 0
    successful_items = 0
    failed_items = 0
    device_reports: List[Optional[Dict]] = [None] * len(devices)

    manager_options = {
        "port": 22,
        "connect_timeout": 10,
        "command_timeout": 30,
        "command_interval": 0.0,
        "connect_retry": connect_retry,
        "jump_enabled": jump_enabled,
        "jump_mode": jump_mode,
        "jump_host": jump_host,
        "jump_port": jump_port,
        "jump_username": jump_username,
        "jump_password": jump_password,
        "smc_command": smc_command,
    }

    future_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, device in enumerate(devices):
            future = executor.submit(
                process_device,
                device,
                username,
                password,
                commands,
                command_map,
                manager_options,
            )
            future_map[future] = (idx, device)

        done_count = 0
        for future in as_completed(future_map):
            idx, device = future_map[future]
            done_count += 1
            wave = (done_count - 1) // max_workers + 1
            try:
                report = future.result()
            except Exception as exc:
                report = {
                    "device": device,
                    "connected": False,
                    "profile_id": "generic.unknown",
                    "items": [],
                    "stats": {"total_items": 0, "successful_items": 0, "failed_items": 0},
                    "elapsed_sec": 0.0,
                    "connect_error": f"worker_exception: {exc}",
                }

            device_reports[idx] = report

            conn_text = "connected" if report.get("connected") else "connect_failed"
            item_stats = report.get("stats", {})
            print(
                f"{wave}-[{done_count}/{len(devices)}] {device} | {conn_text} | "
                f"item_ok={item_stats.get('successful_items', 0)}/{item_stats.get('total_items', 0)} | "
                f"elapsed {report.get('elapsed_sec', 0):.2f}s"
            )

    finalized_reports: List[Dict] = [r for r in device_reports if isinstance(r, dict)]
    for report in finalized_reports:
        if report.get("connected"):
            connected_devices += 1
        else:
            failed_devices += 1
        stats = report.get("stats", {})
        total_items += int(stats.get("total_items", 0))
        successful_items += int(stats.get("successful_items", 0))
        failed_items += int(stats.get("failed_items", 0))

    print("\n" + "=" * 50)
    print("Execution complete.")
    print(f"Connected devices: {connected_devices}")
    print(f"Failed devices: {failed_devices}")
    print(f"Total items: {total_items}")
    print(f"Successful items: {successful_items}")
    print(f"Failed items: {failed_items}")

    summary = {
        "execution_mode": effective_mode,
        "workers": max_workers,
        "recommended_workers": recommended_workers,
        "connect_retry": connect_retry,
        "jump_mode": jump_mode,
        "jump_enabled": jump_enabled,
        "jump_host": jump_host if jump_enabled else "",
        "jump_port": jump_port if jump_enabled else 22,
        "connected_devices": connected_devices,
        "failed_devices": failed_devices,
        "total_items": total_items,
        "successful_items": successful_items,
        "failed_items": failed_items,
    }
    json_report, csv_report = save_structured_report(finalized_reports, summary)
    print(f"Structured JSON report: {json_report}")
    print(f"Structured CSV report: {csv_report}")


if __name__ == "__main__":
    main()
