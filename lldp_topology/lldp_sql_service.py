#!/usr/bin/env python3
"""LLDP data service for lldp.html.

Features:
- SQL import endpoint: start_hostname -> depth query -> CSV
- CLI import endpoint: SMC jump login -> recursive LLDP collection -> CSV

Run:
  pip install fastapi uvicorn pymysql
  python lldp_sql_service.py

Then open:
  http://127.0.0.1:18080/lldp.html
"""

from __future__ import annotations

import csv
import os
import pty
import re
import select
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp_csv"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def load_dotenv_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def ensure_env_loaded() -> None:
    env_file = get_env("DB_ENV_FILE", str(BASE_DIR / ".env.mysql"))
    load_dotenv_file(env_file)


def connect_db() -> pymysql.connections.Connection:
    ensure_env_loaded()

    host = get_env("DB_HOST", "10.73.255.35")
    port = int(get_env("DB_PORT", "8080"))
    user = get_env("DB_USER", "readonly")
    password = os.getenv("DB_PASSWORD", "")
    database = get_env("DB_NAME", "monitoring")

    if not password:
        raise RuntimeError("Missing DB_PASSWORD. Set it in .env.mysql or environment")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8",
        connect_timeout=10,
        read_timeout=300,
        write_timeout=300,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


LLDP_DEPTH3_SQL = """
SELECT DISTINCT
    1 AS depth,
    L1.localhostname,
    L1.localinterface,
    SUBSTRING_INDEX(L1.remotehostname, '.', 1) AS remotehostname,
    L1.remoteinterface
FROM (
    SELECT t.*
    FROM lldpinformation t
    INNER JOIN (
        SELECT localhostname, MAX(create_time) AS m
        FROM lldpinformation
        GROUP BY localhostname
    ) tm
      ON t.localhostname = tm.localhostname
     AND t.create_time = tm.m
) L1
WHERE L1.localhostname = %s

UNION ALL

SELECT DISTINCT
    2 AS depth,
    L2.localhostname,
    L2.localinterface,
    SUBSTRING_INDEX(L2.remotehostname, '.', 1) AS remotehostname,
    L2.remoteinterface
FROM (
    SELECT DISTINCT SUBSTRING_INDEX(L1.remotehostname, '.', 1) AS next_host
    FROM (
        SELECT t.*
        FROM lldpinformation t
        INNER JOIN (
            SELECT localhostname, MAX(create_time) AS m
            FROM lldpinformation
            GROUP BY localhostname
        ) tm
          ON t.localhostname = tm.localhostname
         AND t.create_time = tm.m
    ) L1
    WHERE L1.localhostname = %s
      AND L1.remotehostname IS NOT NULL AND L1.remotehostname <> ''
) seed2
JOIN (
    SELECT t.*
    FROM lldpinformation t
    INNER JOIN (
        SELECT localhostname, MAX(create_time) AS m
        FROM lldpinformation
        GROUP BY localhostname
    ) tm
      ON t.localhostname = tm.localhostname
     AND t.create_time = tm.m
) L2
  ON seed2.next_host = L2.localhostname

UNION ALL

SELECT DISTINCT
    3 AS depth,
    L3.localhostname,
    L3.localinterface,
    SUBSTRING_INDEX(L3.remotehostname, '.', 1) AS remotehostname,
    L3.remoteinterface
FROM (
    SELECT DISTINCT SUBSTRING_INDEX(L2.remotehostname, '.', 1) AS next_host
    FROM (
        SELECT DISTINCT SUBSTRING_INDEX(L1.remotehostname, '.', 1) AS next_host
        FROM (
            SELECT t.*
            FROM lldpinformation t
            INNER JOIN (
                SELECT localhostname, MAX(create_time) AS m
                FROM lldpinformation
                GROUP BY localhostname
            ) tm
              ON t.localhostname = tm.localhostname
             AND t.create_time = tm.m
        ) L1
        WHERE L1.localhostname = %s
          AND L1.remotehostname IS NOT NULL AND L1.remotehostname <> ''
    ) seed2
    JOIN (
        SELECT t.*
        FROM lldpinformation t
        INNER JOIN (
            SELECT localhostname, MAX(create_time) AS m
            FROM lldpinformation
            GROUP BY localhostname
        ) tm
          ON t.localhostname = tm.localhostname
         AND t.create_time = tm.m
    ) L2
      ON seed2.next_host = L2.localhostname
    WHERE L2.remotehostname IS NOT NULL AND L2.remotehostname <> ''
) seed3
JOIN (
    SELECT t.*
    FROM lldpinformation t
    INNER JOIN (
        SELECT localhostname, MAX(create_time) AS m
        FROM lldpinformation
        GROUP BY localhostname
    ) tm
      ON t.localhostname = tm.localhostname
     AND t.create_time = tm.m
) L3
  ON seed3.next_host = L3.localhostname
"""


# Keep these patterns aligned with netlog_extractor SMC flow.
PROMPT_PATTERN = re.compile(r"(?m)^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
JUMP_PROMPT_PATTERN = re.compile(r"(?m)^.*[@].*[$#]\s*$")
YES_PATTERN = re.compile(r"\(yes/no(?:/\[fingerprint\])?\)\??", re.IGNORECASE)
TOKEN_RETRY_PATTERN = re.compile(
    r"(try\s+login\s+with\s+the\s+old\s+token.*?\(y/n\).*?(?:default\s*:\s*n|\[default:n\]))",
    re.IGNORECASE | re.DOTALL,
)
PASSWORD_PATTERN = re.compile(r"(enter\s+password|password)\s*:\s*$", re.IGNORECASE | re.MULTILINE)
FAIL_PATTERN = re.compile(
    r"(permission denied|connection timed out|could not resolve|connection refused|no route to host|closed by remote host)",
    re.IGNORECASE,
)
ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
INVALID_CMD_PATTERN = re.compile(
    r"(invalid input|unknown command|unrecognized command|error:\s*invalid|ambiguous command)",
    re.IGNORECASE,
)
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass
class SmcShellConfig:
    smc_command: str
    device_ip: str
    username: str
    password: str
    jump_host: str | None = None
    jump_port: int = 22
    timeout: int = 60
    debug: Callable[[str], None] | None = None


class SmcShellClient:
    def __init__(self, config: SmcShellConfig) -> None:
        self.config = config
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.smc_mode_active = False

    def _dbg(self, message: str) -> None:
        if self.config.debug:
            safe_message = message
            if self.config.password:
                safe_message = safe_message.replace(self.config.password, "***")
            self.config.debug(safe_message)

    def _clean_ansi(self, text: str) -> str:
        return ANSI_PATTERN.sub("", text).replace("\r", "")

    def _smc_send(self, text: str, mask: bool = False) -> None:
        if self.master_fd is None:
            raise RuntimeError("SMC PTY not initialized")
        self._dbg(f"[SEND] {'***' if mask else text.rstrip()}")
        os.write(self.master_fd, text.encode("utf-8", errors="ignore"))

    def _smc_read_for(self, timeout: float) -> str:
        if self.master_fd is None:
            return ""
        data_parts: list[str] = []
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            left = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([self.master_fd], [], [], min(0.2, left))
            if not rlist:
                continue
            try:
                chunk = os.read(self.master_fd, 65535)
            except OSError:
                break
            if not chunk:
                break
            data_parts.append(chunk.decode("utf-8", errors="replace"))
            if len(chunk) < 65535:
                break
        text = "".join(data_parts)
        if text:
            self._dbg(f"[RECV]\n{self._clean_ansi(text)}")
        return text

    def _smc_read_until_prompt_via_smc(self, timeout: int = 30) -> str:
        chunks: list[str] = []
        deadline = time.time() + timeout
        last_recv_ts = time.time()
        while time.time() < deadline:
            piece = self._smc_read_for(0.3)
            if piece:
                chunks.append(piece)
                last_recv_ts = time.time()
                tail = self._clean_ansi("".join(chunks)[-3000:])
                if PROMPT_PATTERN.search(tail) and (time.time() - last_recv_ts) >= 0.1:
                    break
            else:
                if chunks and (time.time() - last_recv_ts) >= 0.2:
                    tail = self._clean_ansi("".join(chunks)[-3000:])
                    if PROMPT_PATTERN.search(tail):
                        break
                time.sleep(0.05)
        return "".join(chunks)

    def _clean_shell_output(self, raw_output: str, command: str) -> str:
        normalized = self._clean_ansi(raw_output)
        lines = normalized.splitlines()
        cleaned: list[str] = []
        command_skipped = False
        for line in lines:
            stripped = line.strip()
            if not command_skipped and stripped == command.strip():
                command_skipped = True
                continue
            if PROMPT_PATTERN.match(stripped):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _connect_via_smc_shell(self) -> None:
        if os.name == "nt":
            raise RuntimeError("SMC mode is currently supported on POSIX systems only")

        cmd = str(self.config.smc_command).strip() or "smc server toc {jump_host}"
        jump_host = self.config.jump_host or ""
        cmd = cmd.replace("{jump_host}", jump_host).replace("{jump_port}", str(self.config.jump_port or 22))
        if "{jump_host}" not in str(self.config.smc_command) and jump_host and jump_host not in cmd:
            cmd = f"{cmd} {jump_host}".strip()
        self._dbg(f"[SMC] start jump command: {cmd}")

        master_fd, slave_fd = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                ["bash", "-lc", cmd],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        finally:
            try:
                os.close(slave_fd)
            except Exception:
                pass
        self.master_fd = master_fd

        jump_deadline = time.time() + max(20, self.config.timeout * 2)
        jump_buffer = ""
        jump_ready = False
        while time.time() < jump_deadline:
            chunk = self._smc_read_for(1.0)
            if not chunk:
                continue
            jump_buffer += chunk
            if len(jump_buffer) > 20000:
                jump_buffer = jump_buffer[-20000:]
            normalized = self._clean_ansi(jump_buffer)
            tail = normalized[-800:]

            if FAIL_PATTERN.search(normalized):
                raise RuntimeError("SMC jump command failed before device login")
            if YES_PATTERN.search(normalized):
                self._smc_send("yes\n")
                continue
            if TOKEN_RETRY_PATTERN.search(normalized):
                self._smc_send("y\n")
                jump_buffer = ""
                continue
            if JUMP_PROMPT_PATTERN.search(tail) or "Last login:" in tail:
                jump_ready = True
                break

        if not jump_ready:
            raise RuntimeError("SMC jump shell timeout")

    def connect(self) -> None:
        self._connect_via_smc_shell()
        self._dbg(f"[LOGIN] ssh to {self.config.username}@{self.config.device_ip}")
        self._smc_send(f"ssh -o StrictHostKeyChecking=no {self.config.username}@{self.config.device_ip}\n")

        deadline = time.time() + max(20, self.config.timeout * 3)
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
            normalized = self._clean_ansi(buffer)

            if FAIL_PATTERN.search(normalized):
                raise RuntimeError("SMC jump login failed while ssh to target device")
            if YES_PATTERN.search(normalized):
                self._smc_send("yes\n")
                continue
            if TOKEN_RETRY_PATTERN.search(normalized):
                self._smc_send("y\n")
                buffer = ""
                continue
            if PASSWORD_PATTERN.search(normalized):
                if password_attempts < max_password_attempts:
                    password_attempts += 1
                    self._dbg(f"[LOGIN] password prompt detected, attempt={password_attempts}")
                    self._smc_send(self.config.password + "\n", mask=True)
                    buffer = ""
                    continue
                raise RuntimeError("SMC auth failed: password prompt repeated too many times")
            tail = normalized[-4000:]
            if PROMPT_PATTERN.search(tail):
                self.smc_mode_active = True
                self._dbg("[LOGIN] target device prompt reached")
                break

        if not self.smc_mode_active:
            raise RuntimeError("SMC mode timeout: cannot reach target device prompt")

        for pager_cmd in (
            "screen-length 0 temporary",
            "screen-length 0",
            "terminal length 0",
            "set cli screen-length 0",
        ):
            try:
                self.exec(pager_cmd, timeout=3)
            except Exception:
                continue

    def _execute_command_via_smc(self, cmd: str, timeout: int = 30) -> str:
        if not self.smc_mode_active:
            raise RuntimeError("SMC mode is not active")
        self._smc_send(cmd + "\n")
        raw_output = self._smc_read_until_prompt_via_smc(timeout=timeout)
        return self._clean_shell_output(raw_output, cmd)

    def exec(self, cmd: str, timeout: int = 30) -> str:
        return self._execute_command_via_smc(cmd, timeout=timeout)

    def close(self) -> None:
        self.smc_mode_active = False
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self.proc:
            try:
                if self.proc.poll() is None:
                    if os.name != "nt":
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    else:
                        self.proc.terminate()
                    try:
                        self.proc.wait(timeout=2)
                    except Exception:
                        if os.name != "nt":
                            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                        else:
                            self.proc.kill()
            except Exception:
                pass
        self.proc = None


@dataclass
class CliRuntimeConfig:
    username: str
    password: str
    smc_command: str
    jump_host: str | None
    jump_port: int
    command_timeout: int
    connect_timeout: int


def get_cli_runtime_config(
    device_username: str | None = None,
    device_password: str | None = None,
    smc_jump_host: str | None = None,
    smc_jump_port: int | None = None,
    smc_command: str | None = None,
    cli_command_timeout: int | None = None,
    cli_connect_timeout: int | None = None,
) -> CliRuntimeConfig:
    ensure_env_loaded()
    username = (
        (device_username or "").strip()
        or get_env("CLI_DEVICE_USERNAME")
        or get_env("DEVICE_SSH_USERNAME")
        or get_env("CLI_USERNAME")
    )
    password = (
        (device_password or "").strip()
        or get_env("CLI_DEVICE_PASSWORD")
        or get_env("DEVICE_SSH_PASSWORD")
        or get_env("CLI_PASSWORD")
    )
    jump_host = (
        (smc_jump_host or "").strip()
        or get_env("SMC_JUMP_HOST")
        or get_env("JUMP_HOST")
        or ""
    )
    jump_port = int(
        smc_jump_port
        if smc_jump_port is not None
        else get_env("SMC_JUMP_PORT", get_env("JUMP_PORT", "22") or "22")
    )
    final_smc_command = (smc_command or "").strip() or get_env("SMC_COMMAND")
    if not final_smc_command and jump_host:
        final_smc_command = f"smc server toc {jump_host}"
    command_timeout = int(
        cli_command_timeout
        if cli_command_timeout is not None
        else get_env("CLI_COMMAND_TIMEOUT", "45")
    )
    connect_timeout = int(
        cli_connect_timeout
        if cli_connect_timeout is not None
        else get_env("CLI_CONNECT_TIMEOUT", "60")
    )

    if not username or not password:
        raise RuntimeError("Missing CLI device credentials. Set CLI_DEVICE_USERNAME/CLI_DEVICE_PASSWORD")
    if not final_smc_command:
        raise RuntimeError("Missing SMC_COMMAND (or set SMC_JUMP_HOST/JUMP_HOST)")

    return CliRuntimeConfig(
        username=username,
        password=password,
        smc_command=final_smc_command,
        jump_host=jump_host or None,
        jump_port=jump_port,
        command_timeout=command_timeout,
        connect_timeout=connect_timeout,
    )


def normalize_device_id(s: str) -> str:
    return (s or "").strip()


def _looks_like_ip(s: str) -> bool:
    if not s:
        return False
    m = IPV4_PATTERN.fullmatch(s.strip())
    if not m:
        return False
    nums = [int(x) for x in s.strip().split(".")]
    return all(0 <= n <= 255 for n in nums)


def run_lldp_commands(cli: SmcShellClient, timeout: int, vendor: str = "huawei") -> str:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        commands = [
            "dis lldp neighbor",
            "display lldp neighbor",
            "dis lldp neighbor verbose",
            "display lldp neighbor verbose",
        ]
    elif v == "cisco":
        commands = [
            "show lldp neighbors detail",
            "show lldp neighbors",
        ]
    elif v == "arista":
        commands = [
            "show lldp neighbors detail",
            "show lldp neighbors",
        ]
    else:
        commands = [
            "dis lldp neighbor",
            "show lldp neighbors detail",
            "display lldp neighbor verbose",
        ]
    best = ""
    for cmd in commands:
        try:
            out = cli.exec(cmd, timeout=timeout)
        except Exception:
            continue
        if not out.strip():
            continue
        if INVALID_CMD_PATTERN.search(out):
            best = out
            continue
        # Accept any output that likely contains LLDP structure.
        low = out.lower()
        if (
            "lldp" in low
            and (
                "system name" in low
                or "management address" in low
                or "local intf" in low
                or "local port" in low
                or "neighbor" in low
                or "neighbour" in low
            )
        ):
            return out
        if len(out) > len(best):
            best = out
    return best


def detect_vendor(cli: SmcShellClient, timeout: int = 15) -> str:
    probes = [
        "display version",
        "dis version",
        "show version",
    ]
    for cmd in probes:
        try:
            out = cli.exec(cmd, timeout=timeout)
        except Exception:
            continue
        low = out.lower()
        if "huawei" in low or "vrp" in low:
            return "huawei"
        if "arista" in low or "eos" in low:
            return "arista"
        if "cisco" in low or "ios" in low or "nx-os" in low:
            return "cisco"
    return "huawei"


def detect_device_name(cli: SmcShellClient, vendor: str, timeout: int = 15) -> str:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        cmds = [
            "display current-configuration | include ^sysname",
            "dis current-configuration | include ^sysname",
        ]
        for cmd in cmds:
            try:
                out = cli.exec(cmd, timeout=timeout)
            except Exception:
                continue
            m = re.search(r"(?im)^\\s*sysname\\s+(.+?)\\s*$", out)
            if m:
                return m.group(1).strip()
    elif v == "cisco":
        try:
            out = cli.exec("show running-config | include ^hostname", timeout=timeout)
            m = re.search(r"(?im)^\\s*hostname\\s+(.+?)\\s*$", out)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    elif v == "arista":
        try:
            out = cli.exec("show running-config | include ^hostname", timeout=timeout)
            m = re.search(r"(?im)^\\s*hostname\\s+(.+?)\\s*$", out)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    return ""


def parse_lldp_neighbors_huawei(output: str) -> list[dict[str, str]]:
    """Huawei-focused LLDP parser."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    records: list[dict[str, str]] = []
    cur: dict[str, str] = {"local_if": "", "remote_host": "", "remote_ip": "", "remote_if": ""}
    wait_mgmt_value = False

    block_re = re.compile(r"^([A-Za-z0-9/-]+)\s+has\s+\d+\s+neighbor\(s\)\s*:\s*$", re.I)
    rules: list[tuple[str, re.Pattern[str], bool]] = [
        ("local_if", re.compile(r"^(?:local\s+intf|local\s+port|local\s+interface|local\s+port\s+id|本地(?:接口|端口))\s*:\s*(.+)$", re.I), True),
        ("remote_host", re.compile(r"^(?:system\s+name|sysname|neighbor\s+name|neighbour\s+name|邻居(?:系统)?名称)\s*:\s*(.+)$", re.I), False),
        ("remote_ip", re.compile(r"^(?:management\s+address(?:es)?|management\s+ip|management\s+address\s+\(ipv4\)|管理地址|邻居管理地址)\s*:\s*(.+)$", re.I), False),
        ("remote_if", re.compile(r"^(?:port\s+description|neighbor\s+interface|neighbour\s+interface|neighbor\s+port|neighbour\s+port|邻居(?:接口|端口)|port\s+id)\s*:\s*(.+)$", re.I), False),
    ]

    def clean_value(v: str) -> str:
        x = (v or "").strip()
        return "" if x in {"--", ":"} else x

    def flush_current() -> None:
        rec = {
            "local_if": clean_value(cur["local_if"]) or "unknown",
            "remote_host": clean_value(cur["remote_host"]),
            "remote_ip": clean_value(cur["remote_ip"]),
            "remote_if": clean_value(cur["remote_if"]),
        }
        if not (rec["remote_host"] or rec["remote_ip"] or rec["remote_if"]):
            return
        records.append(rec)

    for line in lines:
        m_block = block_re.search(line)
        if m_block:
            flush_current()
            cur = {"local_if": m_block.group(1).strip(), "remote_host": "", "remote_ip": "", "remote_if": ""}
            wait_mgmt_value = False
            continue

        if wait_mgmt_value:
            ip_match = IPV4_PATTERN.search(line)
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
            wait_mgmt_value = False

        matched = False
        for field, pattern, starts_new in rules:
            m = pattern.search(line)
            if not m:
                continue
            if starts_new:
                flush_current()
                cur = {"local_if": "", "remote_host": "", "remote_ip": "", "remote_if": ""}
            value = m.group(1).strip()
            if field == "remote_ip":
                ip_match = IPV4_PATTERN.search(value)
                if ip_match:
                    cur[field] = ip_match.group(0)
                elif clean_value(value):
                    cur[field] = clean_value(value)
                else:
                    wait_mgmt_value = True
            elif field == "remote_if":
                # Prefer first useful remote interface value.
                if not cur[field] or cur[field] in {"--", ":"}:
                    cur[field] = value
            else:
                cur[field] = value
            matched = True
            break
        if matched:
            continue

        if not cur["remote_ip"]:
            ip_match = IPV4_PATTERN.search(line)
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)

        if not cur["local_if"]:
            first = line.split()[0] if line.split() else ""
            if re.match(r"^(?:Eth|Ethernet|GE|XGE|Gig|Gi|Te|Ten|Po|Port-Channel|Vlan|MEth|Mgmt|100GE|25GE)", first, re.I):
                cur["local_if"] = first

    flush_current()

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in records:
        k = "||".join([(r.get("local_if") or "").lower(), (r.get("remote_host") or "").lower(), (r.get("remote_ip") or "").lower(), (r.get("remote_if") or "").lower()])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def parse_lldp_neighbors_generic(output: str) -> list[dict[str, str]]:
    """Generic fallback parser for non-Huawei vendors."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    out: list[dict[str, str]] = []
    cur = {"local_if": "", "remote_host": "", "remote_ip": "", "remote_if": ""}

    def flush() -> None:
        if not (cur["remote_host"] or cur["remote_ip"]):
            return
        out.append(
            {
                "local_if": (cur["local_if"] or "unknown").strip(),
                "remote_host": (cur["remote_host"] or "").strip(),
                "remote_ip": (cur["remote_ip"] or "").strip(),
                "remote_if": (cur["remote_if"] or "").strip(),
            }
        )

    for line in lines:
        m_local = re.search(r"^(?:local\s+intf|local\s+interface|local\s+port)\s*:\s*(.+)$", line, re.I)
        if m_local:
            flush()
            cur = {"local_if": m_local.group(1).strip(), "remote_host": "", "remote_ip": "", "remote_if": ""}
            continue
        m_host = re.search(r"^(?:system\s+name|neighbor\s+name|neighbour\s+name)\s*:\s*(.+)$", line, re.I)
        if m_host:
            cur["remote_host"] = m_host.group(1).strip()
            continue
        m_if = re.search(r"^(?:port\s+id|port\s+description|neighbor\s+port|neighbour\s+port)\s*:\s*(.+)$", line, re.I)
        if m_if and not cur["remote_if"]:
            cur["remote_if"] = m_if.group(1).strip()
            continue
        if not cur["remote_ip"]:
            ip_match = IPV4_PATTERN.search(line)
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
    flush()

    seen: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in out:
        k = "||".join([(r.get("local_if") or "").lower(), (r.get("remote_host") or "").lower(), (r.get("remote_ip") or "").lower(), (r.get("remote_if") or "").lower()])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def parse_lldp_neighbors(output: str, vendor: str = "huawei") -> list[dict[str, str]]:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        return parse_lldp_neighbors_huawei(output)
    return parse_lldp_neighbors_generic(output)


def collect_device_lldp_once(source: str, depth: int, cfg: CliRuntimeConfig) -> dict[str, Any]:
    transcript: list[str] = []

    def dbg(msg: str) -> None:
        if len(transcript) < 600:
            transcript.append(msg)

    cli = SmcShellClient(
        SmcShellConfig(
            smc_command=cfg.smc_command,
            jump_host=cfg.jump_host,
            jump_port=cfg.jump_port,
            device_ip=source,
            username=cfg.username,
            password=cfg.password,
            timeout=cfg.connect_timeout,
            debug=dbg,
        )
    )

    vendor = "unknown"
    device_name = ""
    parsed_rows: list[dict[str, Any]] = []
    next_ips: set[str] = set()
    error = ""

    try:
        cli.connect()
        vendor = detect_vendor(cli, timeout=min(cfg.command_timeout, 20))
        device_name = detect_device_name(cli, vendor, timeout=min(cfg.command_timeout, 20))
        output = run_lldp_commands(cli, timeout=cfg.command_timeout, vendor=vendor)
        parsed = parse_lldp_neighbors(output, vendor=vendor)
        for rec in parsed:
            remote_host = (rec.get("remote_host") or "").strip()
            remote_ip = (rec.get("remote_ip") or "").strip()
            remote_candidate = remote_host or remote_ip
            if not remote_candidate:
                continue
            row = {
                "depth": depth,
                "localhostname": source,
                "localinterface": (rec.get("local_if") or "").strip(),
                "remotehostname": remote_candidate,
                "remoteinterface": (rec.get("remote_if") or "").strip(),
                "remotevendor": "",
                "remoteip": remote_ip,
            }
            parsed_rows.append(row)
            if remote_ip and _looks_like_ip(remote_ip):
                next_ips.add(remote_ip)
    except Exception as exc:
        error = str(exc)
    finally:
        cli.close()

    return {
        "source": source,
        "depth": depth,
        "vendor": vendor,
        "device_name": device_name,
        "rows": parsed_rows,
        "next_ips": sorted(next_ips),
        "error": error,
        "debug_entry": {
            "device": source,
            "depth": depth,
            "status": "failed" if error else "ok",
            "vendor": vendor,
            "device_name": device_name,
            "neighbor_count": len(parsed_rows),
            "error": error,
            "transcript": transcript[:],
        },
    }


def build_cli_lldp_rows(
    start_device: str,
    max_depth: int,
    *,
    cli_max_workers: int | None = None,
    device_username: str | None = None,
    device_password: str | None = None,
    smc_jump_host: str | None = None,
    smc_jump_port: int | None = None,
    smc_command: str | None = None,
    cli_command_timeout: int | None = None,
    cli_connect_timeout: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = get_cli_runtime_config(
        device_username=device_username,
        device_password=device_password,
        smc_jump_host=smc_jump_host,
        smc_jump_port=smc_jump_port,
        smc_command=smc_command,
        cli_command_timeout=cli_command_timeout,
        cli_connect_timeout=cli_connect_timeout,
    )

    visited_sources: set[str] = set()
    failed: list[dict[str, str]] = []
    debug_entries: list[dict[str, Any]] = []
    detected_vendors: dict[str, str] = {}
    detected_device_names: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    edge_seen: set[str] = set()
    env_workers = int(get_env("CLI_MAX_WORKERS", "4") or "4")
    workers = int(cli_max_workers if cli_max_workers is not None else env_workers)
    workers = max(1, min(16, workers))

    current_layer: list[str] = [normalize_device_id(start_device)]
    for depth in range(1, max_depth + 1):
        targets = [t for t in current_layer if t and t not in visited_sources]
        if not targets:
            break
        visited_sources.update(targets)

        next_layer: set[str] = set()
        with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
            future_map = {
                pool.submit(collect_device_lldp_once, source, depth, cfg): source
                for source in targets
            }
            for fut in as_completed(future_map):
                source = future_map.get(fut, "")
                try:
                    res = fut.result()
                except Exception as exc:
                    failed.append({"device": str(source), "error": f"worker_exception: {exc}"})
                    debug_entries.append(
                        {
                            "device": source,
                            "depth": depth,
                            "status": "failed",
                            "vendor": "unknown",
                            "device_name": "",
                            "neighbor_count": 0,
                            "error": f"worker_exception: {exc}",
                            "transcript": [],
                        }
                    )
                    continue
                source = str(res.get("source", "") or "")
                vendor = str(res.get("vendor", "unknown") or "unknown")
                device_name = str(res.get("device_name", "") or "")
                error = str(res.get("error", "") or "")
                debug_entries.append(res.get("debug_entry", {}))

                if vendor and vendor != "unknown":
                    detected_vendors[source] = vendor
                if device_name:
                    detected_device_names[source] = device_name
                if error:
                    failed.append({"device": source, "error": error})
                    continue

                for row in res.get("rows", []):
                    edge_key = "||".join(
                        [
                            str(row.get("depth", "")),
                            str(row.get("localhostname", "")).lower(),
                            str(row.get("localinterface", "")).lower(),
                            str(row.get("remotehostname", "")).lower(),
                            str(row.get("remoteinterface", "")).lower(),
                        ]
                    )
                    if edge_key in edge_seen:
                        continue
                    edge_seen.add(edge_key)
                    rows.append(row)

                if depth < max_depth:
                    for ip in res.get("next_ips", []):
                        if ip and ip not in visited_sources:
                            next_layer.add(ip)

        current_layer = sorted(next_layer)

    meta = {
        "queried_devices": sorted(visited_sources),
        "failed_devices": failed,
        "debug_entries": debug_entries,
        "detected_vendors": detected_vendors,
        "detected_device_names": detected_device_names,
        "cli_max_workers": workers,
    }
    # Pass 1: fill destination name from same-IP rows where name exists.
    ip_to_name: dict[str, str] = {}
    for r in rows:
        ip = (r.get("remoteip") or "").strip()
        name = (r.get("remotehostname") or "").strip()
        if ip and name and not _looks_like_ip(name):
            ip_to_name.setdefault(ip, name)
    # Pass 2: merge names detected from queried source devices.
    for ip, name in detected_device_names.items():
        if ip and name:
            ip_to_name.setdefault(ip, name)
    # Normalize source device display name to avoid duplicate nodes (IP + hostname).
    for r in rows:
        src = str(r.get("localhostname", "") or "").strip()
        if not src:
            continue
        if src in detected_device_names and detected_device_names[src]:
            r["localhostname"] = detected_device_names[src]
        elif _looks_like_ip(src) and src in ip_to_name:
            r["localhostname"] = ip_to_name[src]
    # Second pass: fill destination vendor from detected device map when possible.
    for r in rows:
        dst_ip = (r.get("remoteip") or "").strip()
        if dst_ip and (not r.get("remotehostname") or _looks_like_ip(str(r.get("remotehostname", "")))):
            if dst_ip in ip_to_name:
                r["remotehostname"] = ip_to_name[dst_ip]
        if dst_ip and dst_ip in detected_vendors:
            r["remotevendor"] = detected_vendors[dst_ip]
    return rows, meta


class QueryRequest(BaseModel):
    start_hostname: str


class CliQueryRequest(BaseModel):
    device_address: str
    max_depth: int = Field(default=3, ge=1, le=5)
    cli_max_workers: int | None = Field(default=None, ge=1, le=16)
    device_username: str | None = None
    device_password: str | None = None
    smc_jump_host: str | None = None
    smc_jump_port: int | None = None
    smc_command: str | None = None
    cli_connect_timeout: int | None = None
    cli_command_timeout: int | None = None


app = FastAPI(title="LLDP SQL Service", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def write_rows_to_csv(file_prefix: str, rows: list[dict[str, Any]]) -> tuple[str, Path, str]:
    filename = f"{file_prefix}.csv"
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[:140]
    out_path = TMP_DIR / safe_name

    fieldnames = [
        "depth",
        "localhostname",
        "localinterface",
        "remotehostname",
        "remoteinterface",
        "目的设备Vendor",
        "目的设备IP",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "depth": r.get("depth", ""),
                    "localhostname": r.get("localhostname", ""),
                    "localinterface": r.get("localinterface", ""),
                    "remotehostname": r.get("remotehostname", ""),
                    "remoteinterface": r.get("remoteinterface", ""),
                    "目的设备Vendor": r.get("remotevendor", ""),
                    "目的设备IP": r.get("remoteip", ""),
                }
            )

    csv_text = out_path.read_text(encoding="utf-8")
    return safe_name, out_path, csv_text


def write_cli_debug_text(file_prefix: str, meta: dict[str, Any]) -> tuple[str, Path]:
    filename = f"{file_prefix}.txt"
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[:140]
    out_path = TMP_DIR / safe_name

    lines: list[str] = []
    lines.append("LLDP CLI Recursive Collection Debug")
    lines.append(f"queried_count={len(meta.get('queried_devices', []))}")
    lines.append(f"failed_count={len(meta.get('failed_devices', []))}")
    lines.append("")
    for entry in meta.get("debug_entries", []):
        lines.append("=" * 80)
        lines.append(
            f"device={entry.get('device')} depth={entry.get('depth')} "
            f"status={entry.get('status')} neighbor_count={entry.get('neighbor_count', 0)}"
        )
        err = entry.get("error")
        if err:
            lines.append(f"error={err}")
        lines.append("-" * 80)
        transcript = entry.get("transcript") or []
        if transcript:
            lines.extend(str(x) for x in transcript)
        else:
            lines.append("(no transcript)")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return safe_name, out_path


@app.post("/api/sql/lldp-csv")
def query_lldp_csv(payload: QueryRequest) -> dict[str, Any]:
    start_hostname = payload.start_hostname.strip()
    if not start_hostname:
        raise HTTPException(status_code=400, detail="start_hostname is required")

    try:
        conn = connect_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB connect failed: {exc}") from exc

    try:
        with conn.cursor() as cur:
            cur.execute(LLDP_DEPTH3_SQL, (start_hostname, start_hostname, start_hostname))
            rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {exc}") from exc
    finally:
        conn.close()

    for r in rows:
        rh = str(r.get("remotehostname", "") or "").strip()
        r["remotevendor"] = ""
        r["remoteip"] = rh if _looks_like_ip(rh) else ""

    safe_name, out_path, csv_text = write_rows_to_csv(
        f"lldp_{start_hostname.replace('/', '_').replace(' ', '_')}",
        rows,
    )

    return {
        "ok": True,
        "mode": "sql",
        "start_hostname": start_hostname,
        "row_count": len(rows),
        "temp_file": str(out_path),
        "download_url": f"/api/lldp-csv/file/{safe_name}",
        "csv_text": csv_text,
    }


@app.post("/api/cli/lldp-csv")
def query_lldp_csv_via_cli(payload: CliQueryRequest) -> dict[str, Any]:
    device_address = normalize_device_id(payload.device_address)
    max_depth = int(payload.max_depth)

    if not device_address:
        raise HTTPException(status_code=400, detail="device_address is required")

    try:
        rows, meta = build_cli_lldp_rows(
            device_address,
            max_depth=max_depth,
            cli_max_workers=payload.cli_max_workers,
            device_username=payload.device_username,
            device_password=payload.device_password,
            smc_jump_host=payload.smc_jump_host,
            smc_jump_port=payload.smc_jump_port,
            smc_command=payload.smc_command,
            cli_command_timeout=payload.cli_command_timeout,
            cli_connect_timeout=payload.cli_connect_timeout,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CLI LLDP collection failed: {exc}") from exc

    safe_name, out_path, csv_text = write_rows_to_csv(
        f"lldp_cli_{device_address.replace('/', '_').replace(' ', '_')}_d{max_depth}",
        rows,
    )
    debug_safe_name, debug_path = write_cli_debug_text(
        f"lldp_cli_debug_{device_address.replace('/', '_').replace(' ', '_')}_d{max_depth}",
        meta,
    )

    return {
        "ok": True,
        "mode": "cli",
        "device_address": device_address,
        "max_depth": max_depth,
        "cli_max_workers": meta.get("cli_max_workers"),
        "vendor_mode": "auto-detect-by-version",
        "detected_vendors": meta.get("detected_vendors", {}),
        "row_count": len(rows),
        "queried_count": len(meta.get("queried_devices", [])),
        "queried_devices": meta.get("queried_devices", []),
        "failed_devices": meta.get("failed_devices", []),
        "temp_file": str(out_path),
        "download_url": f"/api/lldp-csv/file/{safe_name}",
        "debug_file": str(debug_path),
        "debug_download_url": f"/api/lldp-debug/file/{debug_safe_name}",
        "csv_text": csv_text,
    }


@app.get("/api/lldp-csv/file/{filename}")
def download_csv(filename: str):
    safe_name = Path(filename).name
    path = TMP_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="text/csv", filename=safe_name)


@app.get("/api/lldp-debug/file/{filename}")
def download_debug_file(filename: str):
    safe_name = Path(filename).name
    path = TMP_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="text/plain", filename=safe_name)


app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("lldp_sql_service:app", host="127.0.0.1", port=18080, reload=False)
