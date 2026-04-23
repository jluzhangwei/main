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
import json
import os
import pty
import re
import select
import signal
import ssl
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed, wait
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
SHARED_DIR = BASE_DIR.parent / "service_hub" / "shared"
LOCAL_SHARED_DIR = BASE_DIR / "shared"
TMP_DIR = BASE_DIR / "tmp_csv"
TMP_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = BASE_DIR / "state_snapshots"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_LIMIT = int(os.getenv("LLDP_STATE_LIMIT", "15") or "15")
ZABBIX_CONFIG_FILE = BASE_DIR / "zabbix_config.json"
LINK_UTIL_CACHE_FILE = TMP_DIR / "link_util_cache.csv"
LINK_UTIL_CACHE_FIELDS = [
    "util_key",
    "data_source",
    "time_mode",
    "time_from",
    "time_till",
    "source_device",
    "source_interface",
    "vendor",
    "status",
    "tx_pct",
    "rx_pct",
    "used_pct",
    "tx_bps",
    "rx_bps",
    "bw_bps",
    "error",
    "updated_at",
]
LINK_UTIL_JOBS: dict[str, dict[str, Any]] = {}
LINK_UTIL_JOB_LOCK = threading.Lock()
LINK_UTIL_JOB_TTL_SEC = int(os.getenv("LINK_UTIL_JOB_TTL_SEC", "21600") or "21600")
SQL_QUERY_JOBS: dict[str, dict[str, Any]] = {}
SQL_QUERY_JOB_LOCK = threading.Lock()
SQL_QUERY_JOB_TTL_SEC = int(os.getenv("SQL_QUERY_JOB_TTL_SEC", "21600") or "21600")
CLI_QUERY_JOBS: dict[str, dict[str, Any]] = {}
CLI_QUERY_JOB_LOCK = threading.Lock()
CLI_QUERY_JOB_TTL_SEC = int(os.getenv("CLI_QUERY_JOB_TTL_SEC", "21600") or "21600")
ZABBIX_URL_DEFAULT = ""
ZABBIX_API_TOKEN_DEFAULT = ""
NDMP_API_URL_DEFAULT = "https://space.shopee.io/apis/ndmp/v2/device_interface/query"
NDMP_DEVICE_API_URL_DEFAULT = "https://space.shopee.io/apis/ndmp/v2/device/query"


class ZabbixConfigSaveRequest(BaseModel):
    url: str = ""
    api_token: str = ""
    verify_ssl: bool = False


def _read_saved_zabbix_config() -> dict[str, Any]:
    if not ZABBIX_CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(ZABBIX_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "url": str(raw.get("url", "") or "").strip(),
        "api_token": str(raw.get("api_token", "") or "").strip(),
        "verify_ssl": bool(raw.get("verify_ssl", False)),
    }


def _effective_zabbix_config() -> dict[str, Any]:
    saved = _read_saved_zabbix_config()
    url = str(saved.get("url") or get_env("ZABBIX_URL", ZABBIX_URL_DEFAULT)).strip()
    api_token = str(saved.get("api_token") or get_env("ZABBIX_API_TOKEN", ZABBIX_API_TOKEN_DEFAULT)).strip()
    verify_ssl = saved.get("verify_ssl")
    if verify_ssl is None:
        verify_ssl = zabbix_verify_ssl(None)
    return {
        "url": url,
        "api_token": api_token,
        "verify_ssl": bool(verify_ssl),
        "source": "file" if saved else "env",
    }


def _save_zabbix_config(payload: ZabbixConfigSaveRequest) -> dict[str, Any]:
    item = {
        "url": str(payload.url or "").strip(),
        "api_token": str(payload.api_token or "").strip(),
        "verify_ssl": bool(payload.verify_ssl),
    }
    ZABBIX_CONFIG_FILE.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


def _delete_zabbix_config() -> None:
    try:
        ZABBIX_CONFIG_FILE.unlink()
    except FileNotFoundError:
        return


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


def zabbix_api_url(url_override: str | None = None) -> str:
    base = str(url_override or _effective_zabbix_config().get("url") or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/api_jsonrpc.php"):
        return base
    return f"{base}/api_jsonrpc.php"


def zabbix_api_url_candidates(url_override: str | None = None) -> list[str]:
    raw = str(url_override or _effective_zabbix_config().get("url") or "").strip()
    if not raw:
        return []
    base = raw.rstrip("/")
    if base.endswith("/api_jsonrpc.php"):
        return [base]

    parsed = urllib.parse.urlsplit(base)
    root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    path = (parsed.path or "").rstrip("/")
    candidates: list[str] = []

    def add(url: str) -> None:
        val = str(url or "").strip().rstrip("/")
        if not val:
            return
        if not val.endswith("/api_jsonrpc.php"):
            val = f"{val}/api_jsonrpc.php"
        if val not in candidates:
            candidates.append(val)

    add(base)
    if root:
        if not path:
            add(f"{root}/zabbix")
            add(root)
        elif path != "/zabbix":
            add(f"{root}/zabbix")
            add(root)
    return candidates


def zabbix_api_token(token_override: str | None = None) -> str:
    return str(token_override or _effective_zabbix_config().get("api_token") or "").strip()


def zabbix_verify_ssl(verify_ssl_override: bool | None = None) -> bool:
    if isinstance(verify_ssl_override, bool):
        return verify_ssl_override
    saved = _read_saved_zabbix_config()
    if "verify_ssl" in saved:
        return bool(saved.get("verify_ssl"))
    raw = get_env("ZABBIX_VERIFY_SSL", "false").lower()
    return raw in {"1", "true", "yes", "on"}


def ndmp_api_url(url_override: str | None = None) -> str:
    return str(url_override or get_env("NDMP_API_URL", NDMP_API_URL_DEFAULT)).strip()


def ndmp_device_api_url(url_override: str | None = None) -> str:
    raw = str(url_override or get_env("NDMP_DEVICE_API_URL", "")).strip()
    if raw:
        return raw
    interface_url = ndmp_api_url()
    if interface_url and "/device_interface/query" in interface_url:
        return interface_url.replace("/device_interface/query", "/device/query")
    return NDMP_DEVICE_API_URL_DEFAULT


def ndmp_verify_ssl(verify_ssl_override: bool | None = None) -> bool:
    if isinstance(verify_ssl_override, bool):
        return verify_ssl_override
    raw = get_env("NDMP_VERIFY_SSL", "false").lower()
    return raw in {"1", "true", "yes", "on"}


def ensure_env_loaded() -> None:
    env_file = get_env("DB_ENV_FILE", str(BASE_DIR / ".env.mysql"))
    load_dotenv_file(env_file)


def current_db_config_defaults() -> dict[str, Any]:
    ensure_env_loaded()
    return {
        "db_host": get_env("DB_HOST", "10.73.255.35"),
        "db_port": int(get_env("DB_PORT", "8080")),
        "db_user": get_env("DB_USER", "readonly"),
        "db_password": os.getenv("DB_PASSWORD", ""),
        "db_name": get_env("DB_NAME", "monitoring"),
    }


def connect_db(
    *,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
) -> pymysql.connections.Connection:
    cfg = current_db_config_defaults()
    host = str(db_host or cfg["db_host"]).strip()
    port = int(db_port or cfg["db_port"])
    user = str(db_user or cfg["db_user"]).strip()
    password = str(db_password if db_password is not None else cfg["db_password"])
    database = str(db_name or cfg["db_name"]).strip()

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
/* MySQL 5.6-compatible targeted depth expansion (start-host scoped). */
SELECT DISTINCT
    1 AS depth,
    L1.localhostname,
    L1.ipaddr AS sourceip,
    L1.localinterface,
    SUBSTRING_INDEX(L1.remotehostname, '.', 1) AS remotehostname,
    L1.remoteinterface,
    R1.ipaddr AS remoteip
FROM lldpinformation L1
LEFT JOIN lldpinformation R1
  ON R1.localhostname = SUBSTRING_INDEX(L1.remotehostname, '.', 1)
 AND R1.create_time = (
      SELECT MAX(rr.create_time)
      FROM lldpinformation rr
      WHERE rr.localhostname = R1.localhostname
 )
WHERE L1.localhostname = %s
  AND L1.create_time = (
      SELECT MAX(x1.create_time)
      FROM lldpinformation x1
      WHERE x1.localhostname = L1.localhostname
  )

UNION ALL

SELECT DISTINCT
    2 AS depth,
    L2.localhostname,
    L2.ipaddr AS sourceip,
    L2.localinterface,
    SUBSTRING_INDEX(L2.remotehostname, '.', 1) AS remotehostname,
    L2.remoteinterface,
    R2.ipaddr AS remoteip
FROM lldpinformation L1
JOIN lldpinformation L2
  ON SUBSTRING_INDEX(L1.remotehostname, '.', 1) = L2.localhostname
 AND L2.create_time = (
      SELECT MAX(x2.create_time)
      FROM lldpinformation x2
      WHERE x2.localhostname = L2.localhostname
 )
LEFT JOIN lldpinformation R2
  ON R2.localhostname = SUBSTRING_INDEX(L2.remotehostname, '.', 1)
 AND R2.create_time = (
      SELECT MAX(rr2.create_time)
      FROM lldpinformation rr2
      WHERE rr2.localhostname = R2.localhostname
 )
WHERE L1.localhostname = %s
  AND L1.create_time = (
      SELECT MAX(x1.create_time)
      FROM lldpinformation x1
      WHERE x1.localhostname = L1.localhostname
  )
  AND L1.remotehostname IS NOT NULL AND L1.remotehostname <> ''

UNION ALL

SELECT DISTINCT
    3 AS depth,
    L3.localhostname,
    L3.ipaddr AS sourceip,
    L3.localinterface,
    SUBSTRING_INDEX(L3.remotehostname, '.', 1) AS remotehostname,
    L3.remoteinterface,
    R3.ipaddr AS remoteip
FROM lldpinformation L1
JOIN lldpinformation L2
  ON SUBSTRING_INDEX(L1.remotehostname, '.', 1) = L2.localhostname
 AND L2.create_time = (
      SELECT MAX(x2.create_time)
      FROM lldpinformation x2
      WHERE x2.localhostname = L2.localhostname
 )
JOIN lldpinformation L3
  ON SUBSTRING_INDEX(L2.remotehostname, '.', 1) = L3.localhostname
 AND L3.create_time = (
      SELECT MAX(x3.create_time)
      FROM lldpinformation x3
      WHERE x3.localhostname = L3.localhostname
 )
LEFT JOIN lldpinformation R3
  ON R3.localhostname = SUBSTRING_INDEX(L3.remotehostname, '.', 1)
 AND R3.create_time = (
      SELECT MAX(rr3.create_time)
      FROM lldpinformation rr3
      WHERE rr3.localhostname = R3.localhostname
 )
WHERE L1.localhostname = %s
  AND L1.create_time = (
      SELECT MAX(x1.create_time)
      FROM lldpinformation x1
      WHERE x1.localhostname = L1.localhostname
  )
  AND L1.remotehostname IS NOT NULL AND L1.remotehostname <> ''
  AND L2.remotehostname IS NOT NULL AND L2.remotehostname <> ''
"""


# Keep these patterns aligned with netlog_extractor SMC flow.
# Device prompts include Huawei (<host>), NX-OS/Arista (host#), and IOS-XR
# forms like "RP/0/RSP1/CPU0:HOST#". Keep this broad but line-anchored.
PROMPT_PATTERN = re.compile(r"(?m)^([\w./:-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
JUMP_PROMPT_PATTERN = re.compile(r"(?m)^.*[@].*[$#]\s*$")
YES_PATTERN = re.compile(r"\(yes/no(?:/\[fingerprint\])?\)\??", re.IGNORECASE)
TOKEN_RETRY_PATTERN = re.compile(
    r"(try\s+login\s+with\s+the\s+old\s+token.*?\(y/n\).*?(?:default\s*:\s*n|\[default:n\]))",
    re.IGNORECASE | re.DOTALL,
)
OLD_TOKEN_PATTERN = re.compile(r"old\s+token.*?\(y/n\)", re.IGNORECASE | re.DOTALL)
PASSWORD_PATTERN = re.compile(r"(enter\s+password|password)\s*:\s*$", re.IGNORECASE | re.MULTILINE)
FAIL_PATTERN = re.compile(
    r"(permission denied|invalid credentials|too many authentication failures|connection timed out|could not resolve|connection refused|no route to host|closed by remote host)",
    re.IGNORECASE,
)
ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
MORE_PROMPT_PATTERN = re.compile(r"(?:----\s*More\s*----|--\s*More\s*--|\bMore\b)", re.IGNORECASE)
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
    ping_precheck: bool = False
    debug: Callable[[str], None] | None = None


class SmcShellClient:
    def __init__(self, config: SmcShellConfig) -> None:
        self.config = config
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.smc_mode_active = False
        self.last_prompt = ""

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
                # Auto-advance paged output (Huawei/H3C/Cisco variants).
                if MORE_PROMPT_PATTERN.search(tail):
                    self._smc_send(" ")
                    continue
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
            if MORE_PROMPT_PATTERN.search(stripped):
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

        jump_deadline = time.time() + max(15, self.config.timeout)
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
                jump_buffer = ""
                continue
            if TOKEN_RETRY_PATTERN.search(normalized) or OLD_TOKEN_PATTERN.search(normalized):
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
        if self.config.ping_precheck:
            ping_ok, _ = self.ping_target(count=2, timeout=min(4, max(2, self.config.timeout)))
            if not ping_ok:
                raise RuntimeError("ping_unreachable")
        self._dbg(f"[LOGIN] ssh to {self.config.username}@{self.config.device_ip}")
        ssh_connect_timeout = max(5, min(int(self.config.timeout or 10), 12))
        ssh_cmd = (
            "ssh "
            "-o StrictHostKeyChecking=no "
            f"-o ConnectTimeout={ssh_connect_timeout} "
            "-o ConnectionAttempts=1 "
            f"{self.config.username}@{self.config.device_ip}"
        )
        self._smc_send(ssh_cmd + "\n")

        deadline = time.time() + max(15, ssh_connect_timeout + 8)
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
                reason = classify_ssh_failure(normalized)
                raise RuntimeError(f"SMC jump login failed while ssh to target device: {reason}")
            if YES_PATTERN.search(normalized):
                self._smc_send("yes\n")
                buffer = ""
                continue
            if TOKEN_RETRY_PATTERN.search(normalized) or OLD_TOKEN_PATTERN.search(normalized):
                self._smc_send("y\n")
                buffer = ""
                continue
            if PASSWORD_PATTERN.search(normalized):
                low = normalized.lower()
                if "invalid credentials" in low or "too many authentication failures" in low:
                    raise RuntimeError("SMC jump login failed while ssh to target device: password_error")
                if password_attempts < max_password_attempts:
                    password_attempts += 1
                    self._dbg(f"[LOGIN] password prompt detected, attempt={password_attempts}")
                    self._smc_send(self.config.password + "\n", mask=True)
                    buffer = ""
                    continue
                raise RuntimeError("SMC auth failed: password prompt repeated too many times")
            tail = normalized[-4000:]
            if PROMPT_PATTERN.search(tail):
                matches = list(PROMPT_PATTERN.finditer(tail))
                if matches:
                    self.last_prompt = (matches[-1].group(1) or "").strip()
                self.smc_mode_active = True
                self._dbg("[LOGIN] target device prompt reached")
                break

        if not self.smc_mode_active:
            raise RuntimeError("SMC mode timeout: cannot reach target device prompt")

    def _execute_command_via_smc(self, cmd: str, timeout: int = 30) -> str:
        if not self.smc_mode_active:
            raise RuntimeError("SMC mode is not active")
        self._smc_send(cmd + "\n")
        raw_output = self._smc_read_until_prompt_via_smc(timeout=timeout)
        return self._clean_shell_output(raw_output, cmd)

    def _execute_jump_command(self, cmd: str, timeout: int = 10) -> str:
        if self.master_fd is None:
            raise RuntimeError("SMC jump shell is not active")
        self._smc_send(cmd + "\n")
        deadline = time.time() + max(2, int(timeout))
        buffer = ""
        while time.time() < deadline:
            chunk = self._smc_read_for(1.0)
            if not chunk:
                continue
            buffer += chunk
            if len(buffer) > 20000:
                buffer = buffer[-20000:]
            normalized = self._clean_ansi(buffer)
            if JUMP_PROMPT_PATTERN.search(normalized[-2000:]):
                return self._clean_shell_output(normalized, cmd)
        raise RuntimeError("jump_shell_command_timeout")

    def ping_target(self, count: int = 2, timeout: int = 4) -> tuple[bool, str]:
        if not self.master_fd:
            raise RuntimeError("SMC jump shell is not active")
        per_packet = 1
        cmd = f"ping -c {max(1, int(count))} -W {max(1, int(per_packet))} {self.config.device_ip}"
        self._dbg(f"[PING] {cmd}")
        try:
            out = self._execute_jump_command(cmd, timeout=max(2, int(timeout)))
        except Exception as exc:
            self._dbg(f"[PING] failed: {exc}")
            return False, str(exc)
        low = out.lower()
        ok = (
            "0% packet loss" in low
            or re.search(r"\b[12]\s+received\b", low) is not None
            or re.search(r"\b[12]\s+packets received\b", low) is not None
        )
        if not ok and (
            "100% packet loss" in low
            or "0 received" in low
            or "100% loss" in low
        ):
            return False, out
        return ok, out

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
    ping_precheck: bool


def get_cli_runtime_config(
    device_username: str | None = None,
    device_password: str | None = None,
    smc_jump_host: str | None = None,
    smc_jump_port: int | None = None,
    smc_command: str | None = None,
    cli_command_timeout: int | None = None,
    cli_connect_timeout: int | None = None,
    ping_precheck: bool | None = None,
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
    final_ping_precheck = bool(
        ping_precheck
        if ping_precheck is not None
        else str(get_env("CLI_PING_PRECHECK", "false")).strip().lower() in {"1", "true", "yes", "on"}
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
        ping_precheck=final_ping_precheck,
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


_INVALID_DEVICE_NAME_TOKENS = {"", "none", "null", "n/a", "na", "unknown", "-", "--"}


def _clean_device_name_token(raw: str | None) -> str:
    name = str(raw or "").strip()
    if not name:
        return ""
    if name.lower() in _INVALID_DEVICE_NAME_TOKENS:
        return ""
    return name


def _name_lookup_variants(raw: str | None) -> list[str]:
    base = _clean_device_name_token(raw)
    if not base:
        return []
    vals: list[str] = []

    def add(value: str) -> None:
        v = _clean_device_name_token(value)
        if not v:
            return
        key = v.lower()
        if key not in vals:
            vals.append(key)

    add(base)
    if "." in base:
        add(base.split(".", 1)[0])
    compact = re.sub(r"\s+", "", base)
    if compact and compact != base:
        add(compact)
        if "." in compact:
            add(compact.split(".", 1)[0])
    return vals


def classify_ssh_failure(text: str) -> str:
    low = (text or "").lower()
    if "invalid credentials" in low or "too many authentication failures" in low:
        return "password_error"
    if "connection timed out" in low:
        return "ssh_timeout"
    if "permission denied" in low:
        return "ssh_auth_failed"
    if "could not resolve" in low:
        return "ssh_dns_failed"
    if "connection refused" in low:
        return "ssh_connection_refused"
    if "no route to host" in low:
        return "ssh_no_route"
    if "closed by remote host" in low:
        return "ssh_closed_by_remote"
    return "ssh_login_failed"


def run_lldp_commands(cli: SmcShellClient, timeout: int, vendor: str = "huawei") -> str:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        commands = [
            "dis lldp neighbor",
            "display lldp neighbor",
            "dis lldp neighbor brief",
            "display lldp neighbor brief",
        ]
    elif v == "cisco":
        commands = [
            "show lldp neighbors detail",
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
            "display lldp neighbor",
        ]
    per_cmd_timeout = timeout if v != "unknown" else min(timeout, 8)
    best = ""
    for cmd in commands:
        try:
            out = cli.exec(cmd, timeout=per_cmd_timeout)
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


def disable_paging(cli: SmcShellClient, vendor: str, timeout: int = 3) -> None:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        # Different Huawei platforms accept different variants.
        candidates = ["screen-length 0 temporary", "screen-length 0"]
    elif v in {"cisco", "arista"}:
        candidates = ["terminal length 0"]
    else:
        # Unknown vendor: try Huawei then Cisco-style, first success stops.
        candidates = ["screen-length 0 temporary", "terminal length 0"]

    for cmd in candidates:
        try:
            out = cli.exec(cmd, timeout=timeout)
        except Exception:
            continue
        if out and INVALID_CMD_PATTERN.search(out):
            continue
        return


def detect_vendor(cli: SmcShellClient, timeout: int = 8) -> str:
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
    return "unknown"


def extract_hostname_from_prompt(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    if not text:
        return ""
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    elif text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    elif text.endswith("#") or text.endswith(">"):
        text = text[:-1].strip()
    if ":" in text:
        text = text.split(":")[-1].strip()
    text = text.strip()
    if not text:
        return ""
    if _looks_like_ip(text):
        return ""
    return text


def detect_device_name(cli: SmcShellClient, vendor: str, timeout: int = 5) -> str:
    prompt_name = extract_hostname_from_prompt(getattr(cli, "last_prompt", ""))
    if prompt_name:
        return prompt_name
    v = (vendor or "").strip().lower()
    if v == "huawei":
        cmds = [
            "display current-configuration | include  sysname",
            "dis current-configuration | include  sysname",
        ]
        for cmd in cmds:
            try:
                out = cli.exec(cmd, timeout=timeout)
            except Exception:
                continue
            m = re.search(r"(?im)^\s*sysname\s+(.+?)\s*$", out)
            if m:
                return m.group(1).strip()
    elif v == "cisco":
        cmds = [
            "show hostname",
            "show version",
            "show ver",
        ]
        for cmd in cmds:
            try:
                out = cli.exec(cmd, timeout=timeout)
            except Exception:
                continue
            m = re.search(r"(?im)^\s*hostname\s+(.+?)\s*$", out)
            if m:
                return m.group(1).strip()
            m = re.search(r"(?im)^\s*Device name\s*:\s*(.+?)\s*$", out)
            if m:
                return m.group(1).strip()
    elif v == "arista":
        cmds = [
            "show hostname",
            "show version",
        ]
        for cmd in cmds:
            try:
                out = cli.exec(cmd, timeout=timeout)
            except Exception:
                continue
            m = re.search(r"(?im)^\s*hostname\s+(.+?)\s*$", out)
            if m:
                return m.group(1).strip()
            m = re.search(r"(?im)^\s*System MAC address:\s*(.+?)\s*$", out)
            if m:
                break
    return ""


def parse_lldp_neighbors_huawei(output: str) -> list[dict[str, str]]:
    """Huawei-focused LLDP parser."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    records: list[dict[str, str]] = []
    cur: dict[str, str] = {"local_if": "", "remote_host": "", "remote_ip": "", "remote_if": ""}
    wait_mgmt_value = False
    brief_header_seen = False
    brief_sep_seen = False

    block_re = re.compile(r"^([A-Za-z0-9/-]+)\s+has\s+\d+\s+neighbor\(s\)\s*:\s*$", re.I)
    brief_row_re = re.compile(
        r"^(?P<local>\S+)\s+(?P<remote_host>\S+)\s+(?P<remote_if>\S+)\s+(?P<expiry>\d+)\s*$",
        re.I,
    )
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
        low = line.lower()
        if "local intf" in low and "neighbor dev" in low and "neighbor intf" in low:
            brief_header_seen = True
            brief_sep_seen = False
            continue
        if brief_header_seen and set(line) <= {"-"}:
            brief_sep_seen = True
            continue
        if brief_header_seen and brief_sep_seen:
            m_brief = brief_row_re.match(line)
            if m_brief:
                local_if = clean_value(m_brief.group("local"))
                remote_host = clean_value(m_brief.group("remote_host"))
                remote_if = clean_value(m_brief.group("remote_if"))
                if remote_host and remote_host.lower() != "neighbor":
                    records.append(
                        {
                            "local_if": local_if or "unknown",
                            "remote_host": remote_host,
                            "remote_ip": "",
                            "remote_if": remote_if,
                        }
                    )
                    continue

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
                    # Prefer IPv4 for recursion/query. Do not overwrite an existing IPv4
                    # with IPv6 or other non-IPv4 tokens.
                    if not cur[field]:
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


def parse_lldp_neighbors_cisco(output: str) -> list[dict[str, str]]:
    """Cisco NX-OS + IOS-XR style LLDP detail parser."""
    text = (output or "").replace("\r", "")
    if not text.strip():
        return []

    lines = text.splitlines()
    records: list[dict[str, str]] = []
    cur: dict[str, str] = {
        "local_if": "",
        "remote_if": "",
        "remote_host": "",
        "remote_ip": "",
        "chassis": "",
    }

    def _empty_like(v: str) -> bool:
        return (v or "").strip().lower() in {"", "null", "not advertised", "--"}

    def flush() -> None:
        local_if = "" if _empty_like(cur["local_if"]) else cur["local_if"].strip()
        remote_if = "" if _empty_like(cur["remote_if"]) else cur["remote_if"].strip()
        remote_host = "" if _empty_like(cur["remote_host"]) else cur["remote_host"].strip()
        remote_ip = "" if _empty_like(cur["remote_ip"]) else cur["remote_ip"].strip()

        if not remote_ip:
            ip_match = IPV4_PATTERN.search(cur.get("remote_ip", ""))
            if ip_match:
                remote_ip = ip_match.group(0)

        if not (remote_host or remote_ip):
            return

        records.append(
            {
                "local_if": local_if or "unknown",
                "remote_host": remote_host,
                "remote_ip": remote_ip,
                "remote_if": remote_if,
            }
        )

    wait_mgmt_value = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # IOS-XR section separator
        if re.fullmatch(r"-{20,}", line):
            if cur["chassis"] or cur["local_if"] or cur["remote_host"] or cur["remote_ip"]:
                flush()
                cur = {"local_if": "", "remote_if": "", "remote_host": "", "remote_ip": "", "chassis": ""}
            continue

        m = re.match(r"(?i)^chassis id\s*:\s*(.+)$", line)
        if m:
            # NX-OS uses consecutive chassis blocks without separators.
            if cur["chassis"] or cur["remote_host"] or cur["remote_ip"]:
                flush()
                cur = {"local_if": "", "remote_if": "", "remote_host": "", "remote_ip": "", "chassis": ""}
            cur["chassis"] = m.group(1).strip()
            continue

        m = re.match(r"(?i)^local\s+(?:interface|intf)\s*:\s*(.+)$", line)
        if m:
            cur["local_if"] = m.group(1).strip()
            continue

        m = re.match(r"(?i)^local\s+port\s+id\s*:\s*(.+)$", line)
        if m and not cur["local_if"]:
            cur["local_if"] = m.group(1).strip()
            continue

        m = re.match(r"(?i)^port\s+id\s*:\s*(.+)$", line)
        if m:
            cur["remote_if"] = m.group(1).strip()
            continue

        m = re.match(r"(?i)^system\s+name\s*:\s*(.+)$", line)
        if m:
            cur["remote_host"] = m.group(1).strip()
            continue

        if wait_mgmt_value:
            ip_match = IPV4_PATTERN.search(line)
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
            wait_mgmt_value = False
            continue

        # NX-OS single-line
        m = re.match(r"(?i)^management\s+address\s*:\s*(.+)$", line)
        if m:
            ip_match = IPV4_PATTERN.search(m.group(1))
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
            continue

        # NX-OS / IOS-style multi-line header
        if re.match(r"(?i)^management\s+addresses\s*:\s*$", line):
            wait_mgmt_value = True
            continue

        # IOS-XR multi-line section
        m = re.match(r"(?i)^ipv4\s+address\s*:\s*(.+)$", line)
        if m:
            ip_match = IPV4_PATTERN.search(m.group(1))
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
            continue

        if re.match(r"(?i)^management\s+addresses\s*-\s*not\s+advertised$", line):
            if not cur["remote_ip"]:
                cur["remote_ip"] = ""
            continue

    flush()

    seen: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in records:
        k = "||".join(
            [
                (r.get("local_if") or "").lower(),
                (r.get("remote_host") or "").lower(),
                (r.get("remote_ip") or "").lower(),
                (r.get("remote_if") or "").lower(),
            ]
        )
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def parse_lldp_neighbors_arista(output: str) -> list[dict[str, str]]:
    """Arista EOS LLDP detail parser."""
    text = (output or "").replace("\r", "")
    if not text.strip():
        return []

    lines = text.splitlines()
    records: list[dict[str, str]] = []
    current_local_if = ""
    cur: dict[str, str] | None = None

    def _empty_like(v: str) -> bool:
        return (v or "").strip().strip('"').lower() in {"", "null", "not advertised", "--"}

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        local_if = (cur.get("local_if") or "").strip().strip('"')
        remote_if = (cur.get("remote_if") or "").strip().strip('"')
        remote_host = (cur.get("remote_host") or "").strip().strip('"')
        remote_ip = (cur.get("remote_ip") or "").strip().strip('"')

        if _empty_like(local_if):
            local_if = ""
        if _empty_like(remote_if):
            remote_if = ""
        if _empty_like(remote_host):
            remote_host = ""
        if _empty_like(remote_ip):
            remote_ip = ""

        if remote_ip:
            ip_match = IPV4_PATTERN.search(remote_ip)
            remote_ip = ip_match.group(0) if ip_match else ""

        if not (remote_host or remote_ip):
            cur = None
            return

        records.append(
            {
                "local_if": local_if or "unknown",
                "remote_host": remote_host,
                "remote_ip": remote_ip,
                "remote_if": remote_if,
            }
        )
        cur = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        m_if = re.match(r"(?i)^Interface\s+(\S+)\s+detected\s+(\d+)\s+LLDP\s+neighbors?\s*:\s*$", line)
        if m_if:
            flush()
            current_local_if = m_if.group(1).strip().strip('"')
            continue

        if re.match(r"(?i)^Neighbor\s+", line):
            flush()
            cur = {
                "local_if": current_local_if,
                "remote_if": "",
                "remote_host": "",
                "remote_ip": "",
            }
            continue

        if cur is None:
            continue

        m = re.match(r"(?i)^-\s*System\s+Name\s*:\s*(.+?)\s*$", line)
        if m:
            cur["remote_host"] = m.group(1).strip()
            continue

        m = re.match(r"(?i)^-\s*Management\s+Address\s+Subtype\s*:\s*IPv4\b", line)
        if m:
            # marker line; actual address is usually on following line.
            continue

        m = re.match(r"(?i)^Management\s+Address\s*:\s*(.+?)\s*$", line)
        if m:
            ip_match = IPV4_PATTERN.search(m.group(1))
            if ip_match:
                cur["remote_ip"] = ip_match.group(0)
            continue

        # Common indentation variant in EOS output.
        m = re.match(r"(?i)^-\s*Port\s+ID\s*:\s*(.+?)\s*$", line)
        if m and not cur.get("remote_if"):
            val = m.group(1).strip().strip('"')
            if val and val != "0":
                cur["remote_if"] = val
            continue

        m = re.match(r"(?i)^Port\s+ID\s*:\s*(.+?)\s*$", line)
        if m and not cur.get("remote_if"):
            val = m.group(1).strip().strip('"')
            if val and val != "0":
                cur["remote_if"] = val
            continue

    flush()

    seen: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in records:
        k = "||".join(
            [
                (r.get("local_if") or "").lower(),
                (r.get("remote_host") or "").lower(),
                (r.get("remote_ip") or "").lower(),
                (r.get("remote_if") or "").lower(),
            ]
        )
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def parse_lldp_neighbors(output: str, vendor: str = "huawei") -> list[dict[str, str]]:
    v = (vendor or "").strip().lower()
    if v == "huawei":
        return parse_lldp_neighbors_huawei(output)
    if v == "cisco":
        return parse_lldp_neighbors_cisco(output)
    if v == "arista":
        return parse_lldp_neighbors_arista(output)
    return parse_lldp_neighbors_generic(output)


def collect_device_lldp_once(source: str, depth: int, cfg: CliRuntimeConfig) -> dict[str, Any]:
    started_at = time.time()
    started_perf = time.perf_counter()
    transcript: list[str] = []

    def dbg(msg: str) -> None:
        transcript.append(msg)
        if len(transcript) > 1200:
            del transcript[: len(transcript) - 1200]

    cli = SmcShellClient(
        SmcShellConfig(
            smc_command=cfg.smc_command,
            jump_host=cfg.jump_host,
            jump_port=cfg.jump_port,
            device_ip=source,
            username=cfg.username,
            password=cfg.password,
            timeout=cfg.connect_timeout,
            ping_precheck=cfg.ping_precheck,
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
        disable_paging(cli, "unknown", timeout=2)
        vendor = detect_vendor(cli, timeout=min(cfg.command_timeout, 8))
        disable_paging(cli, vendor, timeout=3)
        device_name = detect_device_name(cli, vendor, timeout=min(cfg.command_timeout, 5))
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
                "sourceip": source if _looks_like_ip(source) else "",
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
    finished_at = time.time()
    duration_sec = max(0.0, finished_at - started_at)

    return {
        "source": source,
        "depth": depth,
        "vendor": vendor,
        "device_name": device_name,
        "rows": parsed_rows,
        "next_ips": sorted(next_ips),
        "error": error,
        "duration_sec": duration_sec,
        "debug_entry": {
            "device": source,
            "depth": depth,
            "status": "failed" if error else "ok",
            "vendor": vendor,
            "device_name": device_name,
            "neighbor_count": len(parsed_rows),
            "error": error,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_sec": duration_sec,
            "transcript": transcript[:],
        },
    }


def build_cli_lldp_rows(
    start_device: str,
    max_depth: int,
    *,
    cli_max_workers: int | None = None,
    recursive_only_172: bool = False,
    ping_precheck: bool = False,
    device_username: str | None = None,
    device_password: str | None = None,
    smc_jump_host: str | None = None,
    smc_jump_port: int | None = None,
    smc_command: str | None = None,
    cli_command_timeout: int | None = None,
    cli_connect_timeout: int | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    total_start = time.perf_counter()
    cfg = get_cli_runtime_config(
        device_username=device_username,
        device_password=device_password,
        smc_jump_host=smc_jump_host,
        smc_jump_port=smc_jump_port,
        smc_command=smc_command,
        cli_command_timeout=cli_command_timeout,
        cli_connect_timeout=cli_connect_timeout,
        ping_precheck=ping_precheck,
    )

    visited_sources: set[str] = set()
    failed: list[dict[str, str]] = []
    debug_entries: list[dict[str, Any]] = []
    device_timings: list[dict[str, Any]] = []
    depth_summaries: list[dict[str, Any]] = []
    detected_vendors: dict[str, str] = {}
    detected_device_names: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    edge_seen: set[str] = set()
    env_workers = int(get_env("CLI_MAX_WORKERS", "4") or "4")
    workers = int(cli_max_workers if cli_max_workers is not None else env_workers)
    workers = max(1, min(16, workers))
    finished_devices = 0
    total_devices = len({normalize_device_id(start_device)}) if normalize_device_id(start_device) else 0
    current_device = ""
    current_depth = 0
    active_workers = 0
    pending_devices = 0
    layer_total_devices = 0
    layer_finished_devices = 0

    def emit_progress(status: str) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(
                {
                    "status": status,
                    "finished_devices": finished_devices,
                    "total_devices": total_devices,
                    "current_device": current_device,
                    "current_depth": current_depth,
                    "active_workers": active_workers,
                    "pending_devices": pending_devices,
                    "layer_total_devices": layer_total_devices,
                    "layer_finished_devices": layer_finished_devices,
                    "configured_workers": workers,
                    "elapsed_seconds": max(0.0, time.perf_counter() - total_start),
                }
            )
        except Exception:
            pass

    current_layer: list[str] = [normalize_device_id(start_device)]
    emit_progress("running")
    for depth in range(1, max_depth + 1):
        current_depth = depth
        if cancel_event and cancel_event.is_set():
            break
        targets = [t for t in current_layer if t and t not in visited_sources]
        if not targets:
            break
        depth_started = time.perf_counter()
        visited_sources.update(targets)
        total_devices = max(total_devices, len(visited_sources))

        next_layer: set[str] = set()
        depth_completed = 0
        layer_total_devices = len(targets)
        layer_finished_devices = 0
        with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
            future_map = {
                pool.submit(collect_device_lldp_once, source, depth, cfg): source
                for source in targets
            }
            pending = dict(future_map)
            pending_devices = len(pending)
            active_workers = min(len(pending), workers)
            while pending:
                if cancel_event and cancel_event.is_set():
                    for fut in pending:
                        try:
                            fut.cancel()
                        except Exception:
                            pass
                    pending_devices = len(pending)
                    active_workers = min(len(pending), workers)
                    emit_progress("cancelled")
                    break
                done, _ = wait(list(pending.keys()), timeout=1.0, return_when=FIRST_COMPLETED)
                if not done:
                    pending_devices = len(pending)
                    active_workers = min(len(pending), workers)
                    emit_progress("running")
                    continue
                for fut in done:
                    source = pending.pop(fut, "")
                    pending_devices = len(pending)
                    active_workers = min(len(pending), workers)
                    current_device = str(source or "")
                    emit_progress("running")
                    try:
                        res = fut.result()
                    except Exception as exc:
                        failed.append({"device": str(source), "error": f"worker_exception: {exc}"})
                        device_timings.append(
                            {
                                "device": str(source),
                                "depth": depth,
                                "status": "failed",
                                "vendor": "unknown",
                                "neighbor_count": 0,
                                "duration_sec": 0.0,
                                "error": f"worker_exception: {exc}",
                            }
                        )
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
                        finished_devices += 1
                        depth_completed += 1
                        layer_finished_devices = depth_completed
                        emit_progress("running")
                        continue
                    source = str(res.get("source", "") or "")
                    vendor = str(res.get("vendor", "unknown") or "unknown")
                    device_name = str(res.get("device_name", "") or "")
                    error = str(res.get("error", "") or "")
                    debug_entries.append(res.get("debug_entry", {}))
                    device_timings.append(
                        {
                            "device": source,
                            "depth": depth,
                            "status": "failed" if error else "ok",
                            "vendor": vendor,
                            "neighbor_count": len(res.get("rows", []) or []),
                            "duration_sec": float(res.get("duration_sec", 0.0) or 0.0),
                            "error": error,
                        }
                    )

                    if vendor and vendor != "unknown":
                        detected_vendors[source] = vendor
                    if device_name:
                        detected_device_names[source] = device_name
                    if error:
                        failed.append({"device": source, "error": error})
                        finished_devices += 1
                        depth_completed += 1
                        layer_finished_devices = depth_completed
                        emit_progress("running")
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
                            if not ip or ip in visited_sources:
                                continue
                            if recursive_only_172 and not str(ip).startswith("172."):
                                continue
                            if ip and ip not in visited_sources:
                                next_layer.add(ip)
                    total_devices = max(total_devices, len(visited_sources) + len(next_layer) + len(pending))
                    finished_devices += 1
                    depth_completed += 1
                    layer_finished_devices = depth_completed
                    emit_progress("running")
            if cancel_event and cancel_event.is_set():
                current_layer = []
                break
            active_workers = 0
            pending_devices = 0

        current_layer = sorted(next_layer)
        depth_summaries.append(
            {
                "depth": depth,
                "submitted": len(targets),
                "completed": depth_completed,
                "discovered_next": len(current_layer),
                "elapsed_seconds": max(0.0, time.perf_counter() - depth_started),
            }
        )
        total_devices = max(total_devices, len(visited_sources) + len(current_layer))
        emit_progress("running")

    meta = {
        "queried_devices": sorted(visited_sources),
        "failed_devices": failed,
        "debug_entries": debug_entries,
        "device_timings": device_timings,
        "depth_summaries": depth_summaries,
        "detected_vendors": detected_vendors,
        "detected_device_names": detected_device_names,
        "cli_max_workers": workers,
        "recursive_only_172": bool(recursive_only_172),
        "total_elapsed_seconds": max(0.0, time.perf_counter() - total_start),
        "finished_devices": finished_devices,
        "total_devices": total_devices,
        "configured_workers": workers,
        "cancelled": bool(cancel_event and cancel_event.is_set()),
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
    name_to_ip: dict[str, str] = {}
    for ip, name in detected_device_names.items():
        if ip and name and _looks_like_ip(ip):
            name_to_ip.setdefault(name, ip)
    # Normalize source device display name to avoid duplicate nodes (IP + hostname).
    for r in rows:
        src = str(r.get("localhostname", "") or "").strip()
        src_ip = str(r.get("sourceip", "") or "").strip()
        if not src:
            continue
        if not _looks_like_ip(src_ip):
            if _looks_like_ip(src):
                r["sourceip"] = src
            elif src in name_to_ip:
                r["sourceip"] = name_to_ip[src]
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
    emit_progress("cancelled" if (cancel_event and cancel_event.is_set()) else "completed")
    return rows, meta


class QueryRequest(BaseModel):
    start_hostname: str
    max_depth: int = Field(default=3, ge=1, le=3)
    db_host: str | None = None
    db_port: int | None = Field(default=None, ge=1, le=65535)
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None


class CliQueryRequest(BaseModel):
    device_address: str
    max_depth: int = Field(default=3, ge=1, le=5)
    cli_max_workers: int | None = Field(default=None, ge=1, le=16)
    recursive_only_172: bool = False
    ping_precheck: bool = False
    device_username: str | None = None
    device_password: str | None = None
    smc_jump_host: str | None = None
    smc_jump_port: int | None = None
    smc_command: str | None = None
    cli_connect_timeout: int | None = None
    cli_command_timeout: int | None = None


class NdmpQueryRequest(BaseModel):
    device_hostname: str | None = None
    device_ip: str | None = None
    query_mode: str = Field(default="auto")
    max_depth: int = Field(default=1, ge=1, le=3)
    only_link_up: bool = True
    timeout_seconds: int = Field(default=20, ge=3, le=120)
    ndmp_url: str | None = None
    verify_ssl: bool | None = None


class DeviceDetailTarget(BaseModel):
    hostname: str | None = None
    ip: str | None = None
    node_id: str | None = None
    label: str | None = None


class DeviceDetailsRequest(BaseModel):
    devices: list[DeviceDetailTarget]
    timeout_seconds: int = Field(default=20, ge=3, le=120)
    max_workers: int = Field(default=8, ge=1, le=16)
    ndmp_device_url: str | None = None
    verify_ssl: bool | None = None


class LinkUtilTarget(BaseModel):
    source_device: str
    source_interface: str
    util_key: str | None = None
    source_name: str | None = None
    peer_device: str | None = None
    peer_interface: str | None = None
    peer_name: str | None = None


class LinkUtilRequest(BaseModel):
    targets: list[LinkUtilTarget]
    metric: str = Field(default="tx")
    cli_max_workers: int | None = Field(default=None, ge=1, le=16)
    device_username: str | None = None
    device_password: str | None = None
    smc_jump_host: str | None = None
    smc_jump_port: int | None = None
    smc_command: str | None = None
    cli_connect_timeout: int | None = None
    cli_command_timeout: int | None = None
    debug_enabled: bool = False


class ZabbixLinkUtilRequest(BaseModel):
    targets: list[LinkUtilTarget]
    metric: str = Field(default="max")
    time_mode: str = Field(default="current")
    time_from: int | None = None
    time_till: int | None = None
    zabbix_url: str | None = None
    zabbix_api_token: str | None = None
    zabbix_verify_ssl: bool | None = None


class StateSnapshotSaveRequest(BaseModel):
    snapshot: dict[str, Any]
    name: str | None = None


app = FastAPI(title="LLDP SQL Service", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def write_rows_to_csv(file_prefix: str, rows: list[dict[str, Any]], *, include_csv_text: bool = True) -> tuple[str, Path, str]:
    filename = f"{file_prefix}.csv"
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[:140]
    out_path = TMP_DIR / safe_name

    fieldnames = [
        "depth",
        "localhostname",
        "ipaddr",
        "源设备IP",
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
                    "ipaddr": r.get("sourceip", ""),
                    "源设备IP": r.get("sourceip", ""),
                    "localinterface": r.get("localinterface", ""),
                    "remotehostname": r.get("remotehostname", ""),
                    "remoteinterface": r.get("remoteinterface", ""),
                    "目的设备Vendor": r.get("remotevendor", ""),
                    "目的设备IP": r.get("remoteip", ""),
                }
            )

    csv_text = out_path.read_text(encoding="utf-8") if include_csv_text else ""
    return safe_name, out_path, csv_text


def _chunked(values: list[str], size: int = 200) -> list[list[str]]:
    if size <= 0:
        return [values]
    return [values[i : i + size] for i in range(0, len(values), size)]


def _fetch_latest_edges_for_hosts(cur: Any, hosts: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not hosts:
        return out
    uniq_hosts = sorted({h.strip() for h in hosts if h and h.strip()})
    if not uniq_hosts:
        return out

    for part in _chunked(uniq_hosts, 180):
        marks = ",".join(["%s"] * len(part))
        sql = f"""
SELECT
    t.localhostname,
    t.ipaddr AS sourceip,
    t.localinterface,
    SUBSTRING_INDEX(t.remotehostname, '.', 1) AS remotehostname,
    t.remoteinterface
FROM lldpinformation t
INNER JOIN (
    SELECT localhostname, MAX(create_time) AS m
    FROM lldpinformation
    WHERE localhostname IN ({marks})
    GROUP BY localhostname
) tm
  ON t.localhostname = tm.localhostname
 AND t.create_time = tm.m
WHERE t.localhostname IN ({marks})
  AND t.remotehostname IS NOT NULL
  AND t.remotehostname <> ''
"""
        params = tuple(part + part)
        cur.execute(sql, params)
        out.extend(cur.fetchall() or [])
    return out


def _fetch_latest_ip_map_for_hosts(cur: Any, hosts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not hosts:
        return out
    uniq_hosts = sorted({h.strip() for h in hosts if h and h.strip()})
    if not uniq_hosts:
        return out

    for part in _chunked(uniq_hosts, 180):
        marks = ",".join(["%s"] * len(part))
        sql = f"""
SELECT
    t.localhostname,
    t.ipaddr
FROM lldpinformation t
INNER JOIN (
    SELECT localhostname, MAX(create_time) AS m
    FROM lldpinformation
    WHERE localhostname IN ({marks})
    GROUP BY localhostname
) tm
  ON t.localhostname = tm.localhostname
 AND t.create_time = tm.m
WHERE t.localhostname IN ({marks})
"""
        params = tuple(part + part)
        cur.execute(sql, params)
        for r in cur.fetchall() or []:
            h = str(r.get("localhostname", "") or "").strip()
            ip = str(r.get("ipaddr", "") or "").strip()
            if h and _looks_like_ip(ip):
                out[h] = ip
    return out


def _fetch_latest_hostnames_by_ips(cur: Any, ips: list[str]) -> list[str]:
    out: list[str] = []
    if not ips:
        return out
    uniq_ips = sorted({str(ip or "").strip() for ip in ips if _looks_like_ip(str(ip or "").strip())})
    if not uniq_ips:
        return out
    for part in _chunked(uniq_ips, 180):
        marks = ",".join(["%s"] * len(part))
        sql = f"""
SELECT
    t.localhostname
FROM lldpinformation t
INNER JOIN (
    SELECT localhostname, MAX(create_time) AS m
    FROM lldpinformation
    GROUP BY localhostname
) tm
  ON t.localhostname = tm.localhostname
 AND t.create_time = tm.m
WHERE t.ipaddr IN ({marks})
"""
        cur.execute(sql, tuple(part))
        for r in cur.fetchall() or []:
            h = str(r.get("localhostname", "") or "").strip()
            if h:
                out.append(h)
    return sorted({h for h in out if h})


def _resolve_sql_seed_hosts(cur: Any, start_hostname: str) -> list[str]:
    seed = str(start_hostname or "").strip()
    if not seed:
        return []
    hosts: list[str] = [seed]
    ips: list[str] = []
    if _looks_like_ip(seed):
        ips = [seed]
    else:
        ip_map = _fetch_latest_ip_map_for_hosts(cur, [seed])
        ip = str(ip_map.get(seed, "") or "").strip()
        if _looks_like_ip(ip):
            ips = [ip]
    if ips:
        aliases = _fetch_latest_hostnames_by_ips(cur, ips)
        for h in aliases:
            if h not in hosts:
                hosts.append(h)
    return hosts


def _query_lldp_depth_rows(cur: Any, start_hostname: Any, max_depth: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(start_hostname, (list, tuple, set)):
        frontier = [str(x or "").strip() for x in start_hostname if str(x or "").strip()]
    else:
        frontier = [str(start_hostname or "").strip()]
    expanded: set[str] = set()

    for depth in range(1, max_depth + 1):
        current_hosts = sorted({h for h in frontier if h and h not in expanded})
        if not current_hosts:
            break
        batch = _fetch_latest_edges_for_hosts(cur, current_hosts)
        if not batch:
            expanded.update(current_hosts)
            frontier = []
            continue
        for r in batch:
            r["depth"] = depth
            rows.append(r)
        expanded.update(current_hosts)
        next_hosts = []
        for r in batch:
            nh = str(r.get("remotehostname", "") or "").strip()
            if nh and nh not in expanded:
                next_hosts.append(nh)
        frontier = next_hosts

    if not rows:
        return rows

    remote_hosts = sorted(
        {
            str(r.get("remotehostname", "") or "").strip()
            for r in rows
            if str(r.get("remotehostname", "") or "").strip()
        }
    )
    ip_map = _fetch_latest_ip_map_for_hosts(cur, remote_hosts)
    for r in rows:
        rh = str(r.get("remotehostname", "") or "").strip()
        if _looks_like_ip(rh):
            r["remoteip"] = rh
        else:
            r["remoteip"] = ip_map.get(rh, "")
    return rows


def dedupe_lldp_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows or []:
        depth = str(r.get("depth", "") or "").strip()
        lh = str(r.get("localhostname", "") or "").strip()
        li = str(r.get("localinterface", "") or "").strip() or "N/A"
        rh = str(r.get("remotehostname", "") or "").strip()
        ri = str(r.get("remoteinterface", "") or "").strip() or "N/A"
        if not (lh and rh):
            continue
        key = "||".join([depth.lower(), lh.lower(), li.lower(), rh.lower(), ri.lower()])
        if key in seen:
            continue
        seen.add(key)
        r["localinterface"] = li
        r["remoteinterface"] = ri
        out.append(r)
    return out


def write_cli_debug_text(file_prefix: str, meta: dict[str, Any]) -> tuple[str, Path]:
    filename = f"{file_prefix}.txt"
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[:140]
    out_path = TMP_DIR / safe_name

    lines: list[str] = []
    lines.append("LLDP CLI Recursive Collection Debug")
    lines.append(f"queried_count={len(meta.get('queried_devices', []))}")
    lines.append(f"failed_count={len(meta.get('failed_devices', []))}")
    lines.append(f"total_elapsed_seconds={float(meta.get('total_elapsed_seconds', 0.0)):.3f}")
    lines.append("")
    lines.append("[Per-Depth Summary]")
    for item in meta.get("depth_summaries", []):
        lines.append(
            f"depth={item.get('depth')} submitted={item.get('submitted')} "
            f"completed={item.get('completed')} discovered_next={item.get('discovered_next')} "
            f"elapsed_sec={float(item.get('elapsed_seconds', 0.0) or 0.0):.3f}"
        )
    lines.append("")
    lines.append("[Per-Device Timing]")
    for item in meta.get("device_timings", []):
        lines.append(
            f"device={item.get('device')} depth={item.get('depth')} status={item.get('status')} "
            f"vendor={item.get('vendor', 'unknown')} neighbor_count={item.get('neighbor_count', 0)} "
            f"duration_sec={float(item.get('duration_sec', 0.0) or 0.0):.3f} "
            f"error={item.get('error', '')}"
        )
    lines.append("")
    for entry in meta.get("debug_entries", []):
        lines.append("=" * 80)
        duration = float(entry.get("duration_sec", 0.0) or 0.0)
        lines.append(
            f"device={entry.get('device')} depth={entry.get('depth')} "
            f"status={entry.get('status')} neighbor_count={entry.get('neighbor_count', 0)} "
            f"duration_sec={duration:.3f}"
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


def write_link_util_debug_text(file_prefix: str, meta: dict[str, Any]) -> tuple[str, Path]:
    filename = f"{file_prefix}.txt"
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-")[:140]
    out_path = TMP_DIR / safe_name

    results = meta.get("results") or []
    ok_count = len([x for x in results if x.get("status") == "ok"])
    fail_count = len([x for x in results if x.get("status") == "failed"])
    no_data_count = len([x for x in results if x.get("status") == "no_data"])

    lines: list[str] = []
    lines.append("LLDP Link Utilization Debug")
    lines.append(f"metric={meta.get('metric', 'tx')}")
    lines.append(f"result_count={len(results)}")
    lines.append(f"ok_count={ok_count}")
    lines.append(f"failed_count={fail_count}")
    lines.append(f"no_data_count={no_data_count}")
    lines.append(f"queried_devices={len(meta.get('queried_devices', []))}")
    lines.append(f"cli_max_workers={meta.get('cli_max_workers')}")
    lines.append(f"total_elapsed_seconds={float(meta.get('total_elapsed_seconds', 0.0)):.3f}")
    lines.append("")

    lines.append("[Per-Port Results]")
    for r in results:
        lines.append(
            f"{r.get('source_device')} {r.get('source_interface')} status={r.get('status')} "
            f"vendor={r.get('vendor')} tx={r.get('tx_pct')} rx={r.get('rx_pct')} "
            f"used={r.get('used_pct')} err={r.get('error', '')}"
        )
    lines.append("")

    lines.append("[Per-Device Debug]")
    for entry in meta.get("debug_entries", []):
        lines.append("=" * 80)
        duration = float(entry.get("duration_sec", 0.0) or 0.0)
        lines.append(
            f"device={entry.get('device')} status={entry.get('status')} vendor={entry.get('vendor', 'unknown')} "
            f"duration_sec={duration:.3f}"
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


def _num_or_empty(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(float(v))
    except Exception:
        return ""


def _parse_num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _zabbix_rpc(
    method: str,
    params: dict[str, Any] | list[Any],
    *,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> Any:
    endpoints = zabbix_api_url_candidates(url_override)
    if not endpoints:
        raise RuntimeError("Missing ZABBIX_URL")
    token = zabbix_api_token(token_override)
    if not token:
        raise RuntimeError("Missing ZABBIX_API_TOKEN")
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": int(time.time() * 1000) % 1000000}
    ).encode("utf-8")
    ssl_ctx = ssl.create_default_context() if zabbix_verify_ssl(verify_ssl_override) else ssl._create_unverified_context()
    last_exc: Exception | None = None
    for endpoint in endpoints:
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("error"):
                err = body["error"]
                raise RuntimeError(f"Zabbix API error {err.get('code')}: {err.get('data') or err.get('message')}")
            return body.get("result")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 404:
                last_exc = RuntimeError(f"Zabbix HTTP {exc.code} on {endpoint}: {detail}")
                continue
            raise RuntimeError(f"Zabbix HTTP {exc.code} on {endpoint}: {detail}") from exc
        except Exception as exc:
            last_exc = exc
            if "404" in str(exc):
                continue
            raise RuntimeError(f"Zabbix request failed on {endpoint}: {exc}") from exc
    if last_exc:
        raise RuntimeError(str(last_exc))
    raise RuntimeError("Zabbix request failed: no valid API endpoint candidate")


def _ndmp_pick(record: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        cur: Any = record
        ok = True
        for seg in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(seg)
            else:
                ok = False
                break
        if not ok or cur is None:
            continue
        txt = str(cur).strip()
        if txt:
            return txt
    return ""


def _extract_ndmp_interfaces(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("device_interfaces", "interfaces", "rows", "items", "list", "result"):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]

    for key in ("data", "result", "payload"):
        if key in payload:
            out = _extract_ndmp_interfaces(payload.get(key))
            if out:
                return out
    return []


def _is_link_status_up(status: str) -> bool:
    norm = re.sub(r"[\s_\-]+", "", str(status or "").strip().lower())
    return norm in {"up", "true", "1", "connected", "linkup", "operup", "upup"}


def _extract_first_ipv4(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if _looks_like_ip(s):
        return s
    m = re.search(r"((?:\d{1,3}\.){3}\d{1,3})", s)
    if not m:
        return ""
    candidate = m.group(1)
    return candidate if _looks_like_ip(candidate) else ""


def _is_mgmt_interface_name(iface: str) -> bool:
    name = re.sub(r"\s+", "", str(iface or "").strip().lower())
    if not name:
        return False
    if any(
        name.startswith(prefix)
        for prefix in (
            "meth",
            "mgmt",
            "management",
            "managementethernet",
            "fxp",
            "oob",
            "vme",
        )
    ):
        return True
    return ("mgmt" in name) or ("management" in name)


def _normalize_ndmp_source_ip_per_host(rows: list[dict[str, Any]], query_value: str) -> None:
    if not rows:
        return
    query_ip = _extract_first_ipv4(query_value)
    host_scores: dict[str, dict[str, tuple[int, int]]] = {}
    host_original_case: dict[str, str] = {}
    for row in rows:
        host = str(row.get("localhostname", "") or "").strip()
        sip = _extract_first_ipv4(str(row.get("sourceip", "") or ""))
        if not host or not sip:
            continue
        host_key = host.lower()
        host_original_case.setdefault(host_key, host)
        iface = str(row.get("localinterface", "") or "").strip()
        score = 1
        if _is_mgmt_interface_name(iface):
            score += 120
        if query_ip and sip == query_ip:
            score += 80
        prev_score, prev_count = host_scores.setdefault(host_key, {}).get(sip, (0, 0))
        host_scores[host_key][sip] = (prev_score + score, prev_count + 1)

    canonical_by_host: dict[str, str] = {}
    for host_key, candidates in host_scores.items():
        if not candidates:
            continue
        best_ip = sorted(
            candidates.items(),
            key=lambda item: (
                -item[1][0],  # weighted score
                -item[1][1],  # occurrence count
                item[0],      # stable tie-break
            ),
        )[0][0]
        canonical_by_host[host_key] = best_ip

    if not canonical_by_host:
        return
    for row in rows:
        host = str(row.get("localhostname", "") or "").strip().lower()
        if not host:
            continue
        canonical_ip = canonical_by_host.get(host)
        if canonical_ip:
            row["sourceip"] = canonical_ip


def _is_ndmp_not_found_error(err: str) -> bool:
    low = str(err or "").strip().lower()
    return ("not found" in low) and ("device " in low or " host " in low or "hostname" in low)


def _build_ndmp_lldp_rows(
    *,
    response_body: Any,
    query_value: str,
    only_link_up: bool,
    depth: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    interfaces = _extract_ndmp_interfaces(response_body)
    rows: list[dict[str, Any]] = []
    skipped_by_status = 0
    skipped_no_neighbor = 0
    skipped_no_endpoint = 0

    for item in interfaces:
        if not isinstance(item, dict):
            continue

        link_status = _ndmp_pick(
            item,
            [
                "link_status",
                "oper_status",
                "status",
                "protocol_status",
                "neighbor.link_status",
                "neighbor.status",
            ],
        )
        if only_link_up and link_status and not _is_link_status_up(link_status):
            skipped_by_status += 1
            continue

        local_name = _ndmp_pick(
            item,
            [
                "device_hostname",
                "local_device_hostname",
                "localhostname",
                "hostname",
                "device_name",
                "neighbor.local_device_hostname",
            ],
        )
        local_ip = _ndmp_pick(
            item,
            [
                "device_ip",
                "local_device_ip",
                "ipaddr",
                "sourceip",
                "management_ip",
                "mgmt_ip",
                "interface_ip",
                "local_mgmt_address",
                "management_address",
                "device_management_ip",
                "neighbor.local_device_ip",
            ],
        )
        local_if = _ndmp_pick(
            item,
            [
                "interface_name",
                "local_interface_name",
                "localinterface",
                "port_name",
                "ifname",
                "name",
                "neighbor.local_interface_name",
            ],
        )

        remote_name = _ndmp_pick(
            item,
            [
                "neighbor_device_hostname",
                "remote_device_hostname",
                "lldp_neighbor_hostname",
                "neighbor_hostname",
                "peer_device_hostname",
                "remotehostname",
                "neighbor_hostname",
                "neighbor.device_hostname",
                "neighbor.hostname",
                "peer.hostname",
            ],
        )
        remote_ip = _ndmp_pick(
            item,
            [
                "neighbor_device_ip",
                "remote_device_ip",
                "lldp_neighbor_ip",
                "neighbor_ip",
                "peer_device_ip",
                "remoteip",
                "neighbor_mgmt_address",
                "neighbor_management_ip",
                "management_address",
                "neighbor.device_ip",
                "neighbor.management_address",
                "peer.device_ip",
            ],
        )
        remote_if = _ndmp_pick(
            item,
            [
                "neighbor_interface_name",
                "remote_interface_name",
                "lldp_neighbor_interface_name",
                "lldp_neighbor_port",
                "neighbor_interface",
                "neighbor_interface",
                "peer_interface_name",
                "remoteinterface",
                "neighbor.interface_name",
                "peer.interface_name",
            ],
        )
        remote_vendor = _ndmp_pick(
            item,
            [
                "neighbor_vendor",
                "neighbor_device_vendor",
                "remote_vendor",
                "peer_vendor",
                "neighbor.vendor",
            ],
        )

        # Some NDMP payloads omit per-row local hostname but include interface_ip.
        # Keep local node identity stable to queried target first, and store IP in sourceip.
        local_node = local_name or query_value or local_ip
        remote_node = remote_name or remote_ip

        if not (remote_name or remote_ip or remote_if):
            skipped_no_neighbor += 1
            continue
        if not (local_node and remote_node):
            skipped_no_endpoint += 1
            continue

        src_ip = _extract_first_ipv4(local_ip) or (local_node if _looks_like_ip(local_node) else "")
        dst_ip = _extract_first_ipv4(remote_ip) or (remote_node if _looks_like_ip(remote_node) else "")
        rows.append(
            {
                "depth": int(depth),
                "localhostname": local_node,
                "sourceip": src_ip,
                "localinterface": local_if or "N/A",
                "remotehostname": remote_node,
                "remoteinterface": remote_if or "N/A",
                "remotevendor": remote_vendor,
                "remoteip": dst_ip,
            }
        )

    return dedupe_lldp_rows(rows), {
        "interface_count": len(interfaces),
        "neighbor_interface_count": len(rows),
        "skipped_by_status_count": skipped_by_status,
        "skipped_no_neighbor_count": skipped_no_neighbor,
        "skipped_no_endpoint_count": skipped_no_endpoint,
    }


def _ndmp_query_json(
    *,
    ndmp_url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    ssl_ctx: ssl.SSLContext,
) -> Any:
    req = urllib.request.Request(
        ndmp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(3, int(timeout_seconds)), context=ssl_ctx) as resp:
            raw_text = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"NDMP HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"NDMP request failed: {exc}") from exc

    if not raw_text.strip():
        raise RuntimeError("NDMP 返回为空")
    try:
        return json.loads(raw_text)
    except Exception as exc:
        raise RuntimeError(f"NDMP 返回非 JSON：{exc}") from exc


def _strip_domain_hostname(value: str) -> str:
    text = normalize_device_id(value)
    if not text or _looks_like_ip(text):
        return text
    return text.split(".", 1)[0].strip()


def _safe_ndmp_hostname_regex(hostname: str) -> str:
    host = normalize_device_id(hostname)
    return f"^{re.escape(host)}$" if host else ""


def _extract_ndmp_devices(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("devices", "rows", "items", "list", "result"):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    for key in ("data", "payload", "result"):
        val = payload.get(key)
        if isinstance(val, (dict, list)):
            out = _extract_ndmp_devices(val)
            if out:
                return out
    return []


def _pick_ndmp_device(devices: list[dict[str, Any]], hostname: str, ip: str) -> dict[str, Any] | None:
    if not devices:
        return None
    host = normalize_device_id(hostname).lower()
    short = _strip_domain_hostname(hostname).lower()
    target_ip = normalize_device_id(ip)

    def score(dev: dict[str, Any]) -> tuple[int, int]:
        dev_host = normalize_device_id(str(dev.get("hostname") or "")).lower()
        dev_short = _strip_domain_hostname(dev_host).lower()
        dev_ip = normalize_device_id(str(dev.get("management_ip") or ""))
        value = 0
        if target_ip and dev_ip == target_ip:
            value += 100
        if host and dev_host == host:
            value += 80
        if short and dev_short == short:
            value += 60
        if host and dev_host and (dev_host.startswith(host) or host.startswith(dev_host)):
            value += 10
        updated = 0
        try:
            updated = int(str(dev.get("updated_at") or "0").strip() or "0")
        except Exception:
            updated = 0
        return (value, updated)

    return sorted(devices, key=score, reverse=True)[0]


def _flatten_ndmp_device_detail(
    dev: dict[str, Any],
    *,
    target: DeviceDetailTarget,
    query_payload: dict[str, Any],
) -> dict[str, Any]:
    system_info = dev.get("system_info") if isinstance(dev.get("system_info"), dict) else {}

    def pick(*keys: str) -> str:
        for key in keys:
            val = dev.get(key)
            if val not in (None, ""):
                return str(val).strip()
            val = system_info.get(key)
            if val not in (None, ""):
                return str(val).strip()
        return ""

    hostname = pick("hostname", "HOSTNAME")
    management_ip = pick("management_ip", "MGMT_IPADDR")
    if "/" in management_ip:
        management_ip = management_ip.split("/", 1)[0].strip()
    return {
        "node_id": normalize_device_id(target.node_id or ""),
        "input_label": normalize_device_id(target.label or ""),
        "input_hostname": normalize_device_id(target.hostname or ""),
        "input_ip": normalize_device_id(target.ip or ""),
        "query_payload": query_payload,
        "source": "NDMP device/query",
        "queried_at": int(time.time()),
        "device_id": pick("id"),
        "hostname": hostname,
        "management_ip": management_ip,
        "sn": pick("sn", "SERIAL_NUMBER", "SERIAL"),
        "manufacture": pick("manufacture"),
        "model": pick("model", "MODEL"),
        "platform": pick("platform"),
        "version": pick("version", "SOFTWARE_VERSION", "VRP_VERSION"),
        "role": pick("role"),
        "business": pick("business"),
        "service": pick("service"),
        "idc": pick("idc"),
        "hall": pick("hall"),
        "rack": pick("rack"),
        "area": pick("area"),
        "location": pick("location"),
        "az": pick("az"),
        "status": pick("status"),
        "is_mlag": bool(dev.get("is_mlag", False)),
        "mlag_peer": pick("mlag_peer"),
        "system_description": pick("system_description"),
        "software": pick("SOFTWARE"),
        "software_version": pick("SOFTWARE_VERSION", "version"),
        "boot_image": pick("BOOT_IMAGE"),
        "mgmt_interface": pick("MGMT_INTF"),
        "mgmt_ipaddr": pick("MGMT_IPADDR"),
        "uptime": pick("UPTIME"),
        "asn": pick("asn"),
        "local_asn": pick("local_asn"),
        "created_at": pick("created_at"),
        "updated_at": pick("updated_at"),
    }


def _query_ndmp_device_detail_one(
    target: DeviceDetailTarget,
    *,
    ndmp_url: str,
    timeout_seconds: int,
    ssl_ctx: ssl.SSLContext,
) -> dict[str, Any]:
    hostname = normalize_device_id(target.hostname or target.label or "")
    ip = normalize_device_id(target.ip or "")
    if _looks_like_ip(hostname) and not ip:
        ip = hostname
        hostname = ""
    hostname_candidates: list[str] = []
    for cand in (hostname, _strip_domain_hostname(hostname)):
        cand = normalize_device_id(cand)
        if cand and not _looks_like_ip(cand) and cand not in hostname_candidates:
            hostname_candidates.append(cand)

    attempts: list[dict[str, Any]] = []
    for host in hostname_candidates:
        regex = _safe_ndmp_hostname_regex(host)
        if regex:
            attempts.append({"hostname": regex})
    if _looks_like_ip(ip):
        attempts.append({"management_ip": ip})
    if not attempts:
        return {
            "status": "failed",
            "node_id": normalize_device_id(target.node_id or ""),
            "input_label": normalize_device_id(target.label or ""),
            "input_hostname": normalize_device_id(target.hostname or ""),
            "input_ip": normalize_device_id(target.ip or ""),
            "error": "missing hostname or management_ip",
        }

    errors: list[str] = []
    last_payload: dict[str, Any] = {}
    for payload in attempts:
        last_payload = payload
        try:
            body = _ndmp_query_json(
                ndmp_url=ndmp_url,
                payload=payload,
                timeout_seconds=timeout_seconds,
                ssl_ctx=ssl_ctx,
            )
        except Exception as exc:
            errors.append(f"{payload}: {exc}")
            continue
        devices = _extract_ndmp_devices(body)
        if not devices:
            continue
        picked = _pick_ndmp_device(devices, hostname, ip)
        if not picked:
            continue
        detail = _flatten_ndmp_device_detail(picked, target=target, query_payload=payload)
        detail["status"] = "ok"
        return detail

    return {
        "status": "not_found" if not errors else "failed",
        "node_id": normalize_device_id(target.node_id or ""),
        "input_label": normalize_device_id(target.label or ""),
        "input_hostname": normalize_device_id(target.hostname or ""),
        "input_ip": normalize_device_id(target.ip or ""),
        "query_payload": last_payload,
        "source": "NDMP device/query",
        "queried_at": int(time.time()),
        "error": "; ".join(errors) if errors else "device not found",
    }


def query_ndmp_device_details(payload: DeviceDetailsRequest) -> dict[str, Any]:
    raw_targets = payload.devices or []
    targets: list[DeviceDetailTarget] = []
    seen: set[str] = set()
    for item in raw_targets:
        hostname = normalize_device_id(item.hostname or item.label or "")
        ip = normalize_device_id(item.ip or "")
        node_id = normalize_device_id(item.node_id or "")
        key = (hostname.lower(), ip, node_id)
        key_text = "||".join(key)
        if not hostname and not ip:
            continue
        if key_text in seen:
            continue
        seen.add(key_text)
        targets.append(item)
    if not targets:
        raise RuntimeError("devices is required")

    ndmp_url = ndmp_device_api_url(payload.ndmp_device_url)
    if not ndmp_url:
        raise RuntimeError("Missing NDMP device query URL")
    ssl_ctx = ssl.create_default_context() if ndmp_verify_ssl(payload.verify_ssl) else ssl._create_unverified_context()
    workers = min(max(1, int(payload.max_workers or 8)), 16, len(targets))
    started = time.perf_counter()
    details: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(
                _query_ndmp_device_detail_one,
                target,
                ndmp_url=ndmp_url,
                timeout_seconds=int(payload.timeout_seconds or 20),
                ssl_ctx=ssl_ctx,
            )
            for target in targets
        ]
        for fut in as_completed(futs):
            details.append(fut.result())

    success = sum(1 for item in details if item.get("status") == "ok")
    not_found = sum(1 for item in details if item.get("status") == "not_found")
    failed = sum(1 for item in details if item.get("status") == "failed")
    return {
        "ok": True,
        "mode": "ndmp_device_details",
        "queried": len(targets),
        "success": success,
        "not_found": not_found,
        "failed": failed,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "details": details,
        "errors": [item for item in details if item.get("status") in {"failed", "not_found"}],
    }


def _run_ndmp_lldp_query(
    *,
    query_value: str,
    query_mode: str,
    max_depth: int,
    only_link_up: bool,
    timeout_seconds: int,
    ndmp_url_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> dict[str, Any]:
    ndmp_url = ndmp_api_url(ndmp_url_override)
    if not ndmp_url:
        raise RuntimeError("Missing NDMP_API_URL")

    mode = str(query_mode or "").strip().lower()
    if mode not in {"auto", "device_hostname", "device_ip"}:
        mode = "auto"
    if mode == "auto":
        mode = "device_ip" if _looks_like_ip(query_value) else "device_hostname"
    depth_limit = min(3, max(1, int(max_depth or 1)))
    ssl_ctx = ssl.create_default_context() if ndmp_verify_ssl(verify_ssl_override) else ssl._create_unverified_context()
    started = time.perf_counter()

    frontier: list[str] = [query_value]
    visited_targets: set[str] = set()
    all_rows: list[dict[str, Any]] = []
    queried_devices: list[dict[str, Any]] = []
    failed_queries: list[dict[str, Any]] = []
    depth_summaries: list[dict[str, Any]] = []
    total_interface_count = 0
    total_neighbor_interface_count = 0
    total_skipped_by_status_count = 0
    total_skipped_no_neighbor_count = 0
    total_skipped_no_endpoint_count = 0

    for depth in range(1, depth_limit + 1):
        current_targets: list[str] = []
        current_keys: set[str] = set()
        for target in frontier:
            normalized = normalize_device_id(target)
            if not normalized:
                continue
            key = normalized.lower()
            if key in visited_targets or key in current_keys:
                continue
            current_keys.add(key)
            current_targets.append(normalized)

        if not current_targets:
            break

        next_frontier: list[str] = []
        next_keys: set[str] = set()
        depth_submitted = len(current_targets)
        depth_succeeded = 0
        depth_failed = 0
        depth_interface_count = 0
        depth_neighbor_rows = 0

        for target in current_targets:
            target_key = target.lower()
            visited_targets.add(target_key)
            payload_mode = mode if depth == 1 else ("device_ip" if _looks_like_ip(target) else "device_hostname")
            payload: dict[str, Any] = {"device_ip": target} if payload_mode == "device_ip" else {"device_hostname": target}
            try:
                body = _ndmp_query_json(
                    ndmp_url=ndmp_url,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                    ssl_ctx=ssl_ctx,
                )
            except Exception as exc:
                depth_failed += 1
                failed_queries.append(
                    {
                        "depth": depth,
                        "target": target,
                        "query_mode": payload_mode,
                        "error": str(exc),
                    }
                )
                continue

            depth_succeeded += 1
            queried_devices.append({"depth": depth, "target": target, "query_mode": payload_mode})
            rows, stats = _build_ndmp_lldp_rows(
                response_body=body,
                query_value=target,
                only_link_up=bool(only_link_up),
                depth=depth,
            )
            all_rows.extend(rows)

            depth_interface_count += int(stats.get("interface_count", 0) or 0)
            depth_neighbor_rows += int(stats.get("neighbor_interface_count", 0) or 0)
            total_interface_count += int(stats.get("interface_count", 0) or 0)
            total_neighbor_interface_count += int(stats.get("neighbor_interface_count", 0) or 0)
            total_skipped_by_status_count += int(stats.get("skipped_by_status_count", 0) or 0)
            total_skipped_no_neighbor_count += int(stats.get("skipped_no_neighbor_count", 0) or 0)
            total_skipped_no_endpoint_count += int(stats.get("skipped_no_endpoint_count", 0) or 0)

            if depth >= depth_limit:
                continue

            for row in rows:
                candidate = str(row.get("remoteip", "") or "").strip()
                if not _looks_like_ip(candidate):
                    candidate = str(row.get("remotehostname", "") or "").strip()
                candidate = normalize_device_id(candidate)
                if not candidate:
                    continue
                ckey = candidate.lower()
                if ckey in visited_targets or ckey in next_keys:
                    continue
                next_keys.add(ckey)
                next_frontier.append(candidate)

        depth_summaries.append(
            {
                "depth": depth,
                "submitted": depth_submitted,
                "succeeded": depth_succeeded,
                "failed": depth_failed,
                "next_targets": len(next_frontier),
                "interface_count": depth_interface_count,
                "neighbor_interface_count": depth_neighbor_rows,
            }
        )
        frontier = next_frontier
        if not frontier:
            break

    start_not_found = False
    start_not_found_targets: list[str] = []
    if not queried_devices and failed_queries:
        only_not_found = all(_is_ndmp_not_found_error(str(item.get("error") or "")) for item in failed_queries)
        if not only_not_found:
            first = failed_queries[0]
            raise RuntimeError(str(first.get("error") or "all NDMP queries failed"))
        start_not_found = True
        start_not_found_targets = sorted(
            {
                str(item.get("target") or "").strip()
                for item in failed_queries
                if str(item.get("target") or "").strip()
            }
        )

    rows = dedupe_lldp_rows(all_rows)
    # NDMP payload often has only neighbor_mgmt_address; backfill source IP by hostname mapping.
    host_to_ip: dict[str, str] = {}
    for r in rows:
        rh = str(r.get("remotehostname", "") or "").strip()
        rip = _extract_first_ipv4(str(r.get("remoteip", "") or ""))
        if not (rh and rip):
            continue
        host_to_ip.setdefault(rh.lower(), rip)
        host_to_ip.setdefault(rh.split(".", 1)[0].lower(), rip)
    for r in rows:
        sip = _extract_first_ipv4(str(r.get("sourceip", "") or ""))
        if sip:
            r["sourceip"] = sip
            continue
        lh = str(r.get("localhostname", "") or "").strip()
        if not lh:
            r["sourceip"] = ""
            continue
        mapped = host_to_ip.get(lh.lower()) or host_to_ip.get(lh.split(".", 1)[0].lower()) or ""
        r["sourceip"] = mapped
    # NDMP may return per-interface local IP (e.g. 10.254.*) instead of a device management IP.
    # Normalize each device's sourceip to one canonical value to avoid polluting node IP identity.
    _normalize_ndmp_source_ip_per_host(rows, query_value)

    safe_name, out_path, csv_text = write_rows_to_csv(
        f"lldp_ndmp_{query_value.replace('/', '_').replace(' ', '_')}",
        rows,
    )
    return {
        "ok": True,
        "mode": "ndmp",
        "query_mode": mode,
        "query_value": query_value,
        "max_depth": depth_limit,
        "only_link_up": bool(only_link_up),
        "timeout_seconds": int(timeout_seconds),
        "elapsed_seconds": max(0.0, time.perf_counter() - started),
        "row_count": len(rows),
        "queried_count": len(queried_devices),
        "failed_count": len(failed_queries),
        "queried_devices": queried_devices,
        "failed_queries": failed_queries,
        "depth_summaries": depth_summaries,
        "start_not_found": start_not_found,
        "start_not_found_targets": start_not_found_targets,
        "temp_file": str(out_path),
        "download_url": f"/api/lldp-csv/file/{safe_name}",
        "csv_text": csv_text,
        "interface_count": total_interface_count,
        "neighbor_interface_count": total_neighbor_interface_count,
        "skipped_by_status_count": total_skipped_by_status_count,
        "skipped_no_neighbor_count": total_skipped_no_neighbor_count,
        "skipped_no_endpoint_count": total_skipped_no_endpoint_count,
    }


def _normalize_units_multiplier(units: str, item_key: str = "") -> float:
    u = str(units or "").strip().lower()
    k = str(item_key or "").strip().lower()
    if u in {"bps", "b/s", "bit/s"}:
        return 1.0
    if u in {"Bps".lower(), "byte/s", "bytes/s"}:
        return 8.0
    if u in {"kbps", "kbit/s"}:
        return 1_000.0
    if u in {"mbps", "mbit/s"}:
        return 1_000_000.0
    if u in {"gbps", "gbit/s"}:
        return 1_000_000_000.0
    # In this environment ifHC* items are already preprocessed to bps.
    if "ifhc" in k or "traffic" in k:
        return 1.0
    return 1.0


def _zabbix_item_last_bps(item: dict[str, Any]) -> float | None:
    val = _parse_num(item.get("lastvalue"))
    if val is None:
        return None
    return val * _normalize_units_multiplier(str(item.get("units", "")), str(item.get("key_", "")))


def _expand_iface_aliases(iface: str) -> list[str]:
    raw = str(iface or "").strip()
    if not raw:
        return []
    aliases: list[str] = []

    def add(val: str) -> None:
        s = str(val or "").strip()
        if s and s not in aliases:
            aliases.append(s)

    add(raw)
    add(raw.replace(" ", ""))

    prefix_rules = [
        (r"^Eth(?=\d)", ["Ethernet"]),
        (r"^Ethernet(?=\d)", ["Eth"]),
        (r"^Gi(?=\d)", ["GigabitEthernet"]),
        (r"^GigabitEthernet(?=\d)", ["Gi"]),
        (r"^Fa(?=\d)", ["FastEthernet"]),
        (r"^FastEthernet(?=\d)", ["Fa"]),
        (r"^Te(?=\d)", ["TenGigE", "TenGigabitEthernet"]),
        (r"^TenGigE(?=\d)", ["Te", "TenGigabitEthernet"]),
        (r"^TenGigabitEthernet(?=\d)", ["Te", "TenGigE"]),
        (r"^Hu(?=\d)", ["HundredGigE", "HundredGigabitEthernet"]),
        (r"^HundredGigE(?=\d)", ["Hu", "HundredGigabitEthernet"]),
        (r"^HundredGigabitEthernet(?=\d)", ["Hu", "HundredGigE"]),
        (r"^BE(?=\d)", ["Bundle-Ether"]),
        (r"^Bundle-Ether(?=\d)", ["BE"]),
        (r"^Po(?=\d)", ["Port-Channel"]),
        (r"^Port-Channel(?=\d)", ["Po"]),
        (r"^Lo(?=\d)", ["Loopback"]),
        (r"^Loopback(?=\d)", ["Lo"]),
        (r"^MEth(?=\d)", ["MgmtEth", "ManagementEthernet"]),
        (r"^MgmtEth(?=\d)", ["MEth", "ManagementEthernet"]),
        (r"^ManagementEthernet(?=\d)", ["MEth", "MgmtEth"]),
    ]
    for pattern, repls in prefix_rules:
        if re.search(pattern, raw, re.IGNORECASE):
            suffix = re.sub(pattern, "", raw, count=1, flags=re.IGNORECASE)
            for repl in repls:
                add(f"{repl}{suffix}")
    return aliases


def _normalize_iface_name(iface: str) -> str:
    raw = str(iface or "").strip()
    if not raw:
        return ""
    s = raw.replace('"', "").replace("'", "").replace(" ", "")
    s = re.sub(r"^Eth(?=\d)", "ethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^Ethernet(?=\d)", "ethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^Gi(?=\d)", "gigabitethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^GigabitEthernet(?=\d)", "gigabitethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^Fa(?=\d)", "fastethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^FastEthernet(?=\d)", "fastethernet", s, flags=re.IGNORECASE)
    s = re.sub(r"^Te(?=\d)", "tengige", s, flags=re.IGNORECASE)
    s = re.sub(r"^TenGigE(?=\d)", "tengige", s, flags=re.IGNORECASE)
    s = re.sub(r"^TenGigabitEthernet(?=\d)", "tengige", s, flags=re.IGNORECASE)
    s = re.sub(r"^Hu(?=\d)", "hundredgige", s, flags=re.IGNORECASE)
    s = re.sub(r"^HundredGigE(?=\d)", "hundredgige", s, flags=re.IGNORECASE)
    s = re.sub(r"^HundredGigabitEthernet(?=\d)", "hundredgige", s, flags=re.IGNORECASE)
    s = re.sub(r"^BE(?=\d)", "bundle-ether", s, flags=re.IGNORECASE)
    s = re.sub(r"^Bundle-Ether(?=\d)", "bundle-ether", s, flags=re.IGNORECASE)
    s = re.sub(r"^Po(?=\d)", "port-channel", s, flags=re.IGNORECASE)
    s = re.sub(r"^Port-Channel(?=\d)", "port-channel", s, flags=re.IGNORECASE)
    s = re.sub(r"^Lo(?=\d)", "loopback", s, flags=re.IGNORECASE)
    s = re.sub(r"^Loopback(?=\d)", "loopback", s, flags=re.IGNORECASE)
    s = re.sub(r"^MEth(?=\d)", "meth", s, flags=re.IGNORECASE)
    s = re.sub(r"^MgmtEth(?=\d)", "meth", s, flags=re.IGNORECASE)
    s = re.sub(r"^ManagementEthernet(?=\d)", "meth", s, flags=re.IGNORECASE)
    return s.lower()


def _extract_item_iface_tokens(item: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    key = str(item.get("key_", "") or "")
    name = str(item.get("name", "") or "")
    for m in re.finditer(r"\[([^\]]+)\]", key):
        inner = str(m.group(1) or "").split(",", 1)[0].strip().strip('"').strip("'")
        norm = _normalize_iface_name(inner)
        if norm:
            tokens.add(norm)
    for m in re.finditer(r"interface\s+([A-Za-z][A-Za-z0-9./:-]*)", name, re.IGNORECASE):
        norm = _normalize_iface_name(m.group(1))
        if norm:
            tokens.add(norm)
    return tokens


def _zabbix_iface_match_score(item: dict[str, Any], iface: str) -> int:
    aliases = _expand_iface_aliases(iface)
    if not aliases:
        return -10
    norms = {_normalize_iface_name(x) for x in aliases if _normalize_iface_name(x)}
    name = str(item.get("name", "") or "").lower()
    key = str(item.get("key_", "") or "").lower()
    score = 0
    item_tokens = _extract_item_iface_tokens(item)
    if norms & item_tokens:
        score += 120
    for alias in aliases:
        alias_l = alias.lower()
        if alias_l in key:
            score += 40
        if alias_l in name:
            score += 30
        if f"[{alias_l}]" in key:
            score += 40
        if f"interface {alias_l}" in name:
            score += 20
    return score


def _zabbix_item_kind(item: dict[str, Any]) -> str:
    name = str(item.get("name", "") or "").lower()
    key = str(item.get("key_", "") or "").lower()
    if (
        "ifhighspeed[" in key
        or "ifspeed[" in key
        or "speed of interface" in name
        or "interface speed" in name
        or "bandwidth of interface" in name
        or "interface bandwidth" in name
    ):
        return "speed"
    if (
        "ifhcoutoctets[" in key
        or "ifoutoctets[" in key
        or "outgoing traffic" in name
        or "outbound traffic" in name
        or "bits sent" in name
        or "bits transmit" in name
        or "tx traffic" in name
    ):
        return "tx"
    if (
        "ifhcinoctets[" in key
        or "ifinoctets[" in key
        or "incoming traffic" in name
        or "inbound traffic" in name
        or "bits received" in name
        or "bits receive" in name
        or "rx traffic" in name
    ):
        return "rx"
    return ""


def _pick_best_zabbix_item(items: list[dict[str, Any]], iface: str, kind: str) -> dict[str, Any] | None:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        if _zabbix_item_kind(item) != kind:
            continue
        score = _zabbix_iface_match_score(item, iface)
        if score < 0:
            continue
        candidates.append((score, item))
    if not candidates:
        return None
    candidates.sort(
        key=lambda x: (
            -x[0],
            str(x[1].get("name", "")),
            str(x[1].get("key_", "")),
        )
    )
    return candidates[0][1]


def _zabbix_item_completeness_score(
    tx_item: dict[str, Any] | None,
    rx_item: dict[str, Any] | None,
    speed_item: dict[str, Any] | None,
) -> tuple[int, int]:
    primary = 0
    if speed_item:
        primary += 4
    if tx_item:
        primary += 2
    if rx_item:
        primary += 2
    secondary = 0
    if tx_item and rx_item:
        secondary += 2
    if speed_item and (tx_item or rx_item):
        secondary += 1
    return primary, secondary


def _pick_best_zabbix_host_for_target(
    src_ip: str,
    source_name: str,
    iface: str,
    *,
    host_map_by_ip: dict[str, dict[str, str]],
    host_map_by_name: dict[str, dict[str, str]],
    item_cache: dict[tuple[str, str], list[dict[str, Any]]],
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> tuple[dict[str, str] | None, list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    ip_host = host_map_by_ip.get(src_ip)
    name_host = None
    for name_key in _name_lookup_variants(source_name):
        name_host = host_map_by_name.get(name_key)
        if name_host:
            break

    candidates: list[tuple[str, dict[str, str], bool]] = []
    seen_hostids: set[str] = set()
    for host, prefer_ip in ((ip_host, True), (name_host, False)):
        if not host:
            continue
        hostid = str(host.get("hostid", "")).strip()
        if not hostid or hostid in seen_hostids:
            continue
        seen_hostids.add(hostid)
        candidates.append((hostid, host, prefer_ip))

    if not candidates:
        return None, [], None, None, None

    best: tuple[tuple[int, int, int, int, int], dict[str, str], list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None] | None = None
    iface_key = str(iface or "").strip().lower()
    for hostid, host, prefer_ip in candidates:
        cache_key = (hostid, iface_key)
        items = item_cache.get(cache_key)
        if items is None:
            items = _zabbix_get_items_for_interface(
                hostid,
                iface,
                url_override=url_override,
                token_override=token_override,
                verify_ssl_override=verify_ssl_override,
            )
            item_cache[cache_key] = items
        tx_item = _pick_best_zabbix_item(items, iface, "tx")
        rx_item = _pick_best_zabbix_item(items, iface, "rx")
        speed_item = _pick_best_zabbix_item(items, iface, "speed")
        score_a, score_b = _zabbix_item_completeness_score(tx_item, rx_item, speed_item)
        sort_key = (
            score_a,
            score_b,
            1 if prefer_ip else 0,
            len(items),
            1 if speed_item else 0,
        )
        if best is None or sort_key > best[0]:
            best = (sort_key, host, items, tx_item, rx_item, speed_item)

    if best is None:
        return None, [], None, None, None
    return best[1], best[2], best[3], best[4], best[5]


def _zabbix_get_host_map_by_ip(
    device_ips: list[str],
    *,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> dict[str, dict[str, str]]:
    ips = sorted({str(ip or "").strip() for ip in device_ips if _looks_like_ip(str(ip or "").strip())})
    if not ips:
        return {}
    interfaces = _zabbix_rpc(
        "hostinterface.get",
        {"output": ["hostid", "ip", "interfaceid"], "filter": {"ip": ips}},
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    ) or []
    ip_to_hostid: dict[str, str] = {}
    for row in interfaces:
        ip = str(row.get("ip", "")).strip()
        hostid = str(row.get("hostid", "")).strip()
        if ip and hostid and ip not in ip_to_hostid:
            ip_to_hostid[ip] = hostid
    unresolved_ips = [ip for ip in ips if ip not in ip_to_hostid]
    if unresolved_ips:
        unresolved_set = set(unresolved_ips)
        extra_hosts = _zabbix_rpc(
            "host.get",
            {
                "output": ["hostid", "host", "name"],
                "search": {"host": unresolved_ips, "name": unresolved_ips},
                "searchByAny": True,
                "limit": max(50, len(unresolved_ips) * 6),
            },
            url_override=url_override,
            token_override=token_override,
            verify_ssl_override=verify_ssl_override,
        ) or []
        for row in extra_hosts:
            hostid = str(row.get("hostid", "")).strip()
            host = str(row.get("host", "")).strip()
            name = str(row.get("name", "")).strip()
            if not hostid:
                continue
            if host in unresolved_set and host not in ip_to_hostid:
                ip_to_hostid[host] = hostid
            if name in unresolved_set and name not in ip_to_hostid:
                ip_to_hostid[name] = hostid
    if not ip_to_hostid:
        return {}
    hosts = _zabbix_rpc(
        "host.get",
        {"output": ["hostid", "host", "name"], "hostids": sorted(set(ip_to_hostid.values()))},
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    ) or []
    host_by_id = {
        str(h.get("hostid", "")).strip(): {
            "hostid": str(h.get("hostid", "")).strip(),
            "host": str(h.get("host", "")).strip(),
            "name": str(h.get("name", "")).strip(),
        }
        for h in hosts
    }
    return {
        ip: host_by_id[hostid]
        for ip, hostid in ip_to_hostid.items()
        if hostid in host_by_id
    }


def _zabbix_get_host_map_by_name(
    names: list[str],
    *,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> dict[str, dict[str, str]]:
    wanted_raw = [_clean_device_name_token(str(n or "").strip()) for n in names]
    wanted_raw = [n for n in wanted_raw if n]
    if not wanted_raw:
        return {}
    search_terms = sorted({term for n in wanted_raw for term in _name_lookup_variants(n)})
    wanted_l = set(search_terms)
    if not search_terms:
        return {}
    rows = _zabbix_rpc(
        "host.get",
        {
            "output": ["hostid", "host", "name"],
            "search": {"host": search_terms, "name": search_terms},
            "searchByAny": True,
            "limit": max(50, len(search_terms) * 6),
        },
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    ) or []
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        host = str(row.get("host", "")).strip()
        name = str(row.get("name", "")).strip()
        keys: list[str] = []
        for candidate in (host, name):
            for variant in _name_lookup_variants(candidate):
                if variant in wanted_l and variant not in keys:
                    keys.append(variant)
        for key in keys:
            out.setdefault(
                key,
                {
                    "hostid": str(row.get("hostid", "")).strip(),
                    "host": host,
                    "name": name,
                },
            )
    return out


def _zabbix_get_items_for_interface(
    hostid: str,
    iface: str,
    *,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> list[dict[str, Any]]:
    iface_raw = str(iface or "").strip()
    if not hostid or not iface_raw:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for alias in _expand_iface_aliases(iface_raw):
        rows = _zabbix_rpc(
            "item.get",
            {
                "output": ["itemid", "name", "key_", "units", "value_type", "lastvalue"],
                "hostids": [str(hostid)],
                "search": {"name": alias, "key_": alias},
                "searchByAny": True,
                "sortfield": ["name"],
                "limit": 120,
            },
            url_override=url_override,
            token_override=token_override,
            verify_ssl_override=verify_ssl_override,
        ) or []
        for item in rows:
            itemid = str(item.get("itemid", "")).strip()
            if itemid:
                merged[itemid] = item
    return list(merged.values())


def _zabbix_item_time_agg(
    item: dict[str, Any],
    time_from: int,
    time_till: int,
    *,
    agg: str = "max",
    require_positive: bool = False,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> float | None:
    itemid = str(item.get("itemid", "")).strip()
    if not itemid:
        return None
    agg_l = (agg or "max").strip().lower()
    if agg_l not in {"max", "min"}:
        agg_l = "max"
    span = max(0, int(time_till) - int(time_from))
    if span >= 7200:
        trend_field = "value_max" if agg_l == "max" else "value_min"
        rows = _zabbix_rpc(
            "trend.get",
            {
                "output": [trend_field],
                "itemids": [itemid],
                "time_from": int(time_from),
                "time_till": int(time_till),
                "sortfield": "clock",
                "sortorder": "ASC",
                "limit": 10000,
            },
            url_override=url_override,
            token_override=token_override,
            verify_ssl_override=verify_ssl_override,
        ) or []
        vals = [_parse_num(r.get(trend_field)) for r in rows]
        nums = [v for v in vals if v is not None]
        if require_positive:
            nums = [v for v in nums if v > 0]
        if nums:
            chosen = max(nums) if agg_l == "max" else min(nums)
            return chosen * _normalize_units_multiplier(str(item.get("units", "")), str(item.get("key_", "")))
    history_type = int(_parse_num(item.get("value_type")) or 3)
    rows = _zabbix_rpc(
        "history.get",
        {
            "output": ["value", "clock"],
            "history": history_type,
            "itemids": [itemid],
            "time_from": int(time_from),
            "time_till": int(time_till),
            "sortfield": "clock",
            "sortorder": "ASC",
            "limit": 100000,
        },
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    ) or []
    vals = [_parse_num(r.get("value")) for r in rows]
    nums = [v for v in vals if v is not None]
    if require_positive:
        nums = [v for v in nums if v > 0]
    if not nums:
        return None
    chosen = max(nums) if agg_l == "max" else min(nums)
    return chosen * _normalize_units_multiplier(str(item.get("units", "")), str(item.get("key_", "")))


def _zabbix_item_time_max(
    item: dict[str, Any],
    time_from: int,
    time_till: int,
    *,
    require_positive: bool = False,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> float | None:
    return _zabbix_item_time_agg(
        item,
        time_from,
        time_till,
        agg="max",
        require_positive=require_positive,
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    )


def _zabbix_item_time_min(
    item: dict[str, Any],
    time_from: int,
    time_till: int,
    *,
    require_positive: bool = False,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> float | None:
    return _zabbix_item_time_agg(
        item,
        time_from,
        time_till,
        agg="min",
        require_positive=require_positive,
        url_override=url_override,
        token_override=token_override,
        verify_ssl_override=verify_ssl_override,
    )


def _zabbix_speed_bps_with_fallback(
    item: dict[str, Any] | None,
    *,
    time_mode: str,
    time_from: int | None = None,
    time_till: int | None = None,
    cache: dict[tuple[str, str, int, int], float | None] | None = None,
    url_override: str | None = None,
    token_override: str | None = None,
    verify_ssl_override: bool | None = None,
) -> float | None:
    if not item:
        return None
    direct = _zabbix_item_last_bps(item)
    if isinstance(direct, (int, float)) and direct > 0:
        return float(direct)

    itemid = str(item.get("itemid", "")).strip()
    if not itemid:
        return None

    now_ts = int(time.time())
    windows: list[tuple[int, int]] = []
    if time_mode in {"range_max", "range_min"} and isinstance(time_from, int) and isinstance(time_till, int) and time_till > time_from:
        windows.append((int(time_from), int(time_till)))
    for span in (86400, 604800, 2592000, 31536000):
        start = now_ts - span
        if not any(existing[0] == start and existing[1] == now_ts for existing in windows):
            windows.append((start, now_ts))

    for start, end in windows:
        cache_key = (itemid, "speed", int(start), int(end))
        cached = cache.get(cache_key) if cache is not None else None
        if cache is not None and cache_key in cache:
            val = cached
        else:
            val = _zabbix_item_time_max(
                item,
                int(start),
                int(end),
                require_positive=True,
                url_override=url_override,
                token_override=token_override,
                verify_ssl_override=verify_ssl_override,
            )
            if cache is not None:
                cache[cache_key] = val
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    return None


def _infer_bw_bps_from_iface_name(iface: str) -> float | None:
    raw = str(iface or "").strip()
    if not raw:
        return None
    norm = raw.replace(" ", "").lower()

    # Prefer explicit numeric speed hints in interface name: 10GE/25GE/100GE/400GE...
    for m in re.finditer(r"(\d{1,4})ge", norm):
        try:
            ge_val = int(m.group(1))
        except Exception:
            continue
        if ge_val in {1, 10, 25, 40, 50, 100, 200, 400, 800}:
            return float(ge_val) * 1_000_000_000.0

    prefix_map_gbps: list[tuple[tuple[str, ...], float]] = [
        (("hundredgige", "hundredgigabitethernet", "hu"), 100.0),
        (("tengige", "tengigabitethernet", "te", "xe-"), 10.0),
        (("twentyfivegige", "twentyfivegigabitethernet"), 25.0),
        (("fortygige", "fortygigabitethernet"), 40.0),
        (("gigabitethernet", "gi"), 1.0),
        (("fastethernet", "fa"), 0.1),
    ]
    for prefixes, gbps in prefix_map_gbps:
        if any(norm.startswith(p) for p in prefixes):
            return gbps * 1_000_000_000.0

    return None


def collect_zabbix_link_utilization(
    targets: list[LinkUtilTarget],
    metric: str,
    *,
    time_mode: str = "current",
    time_from: int | None = None,
    time_till: int | None = None,
    zabbix_url: str | None = None,
    zabbix_api_token: str | None = None,
    zabbix_verify_ssl: bool | None = None,
) -> dict[str, Any]:
    metric_l = (metric or "max").strip().lower()
    if metric_l not in {"tx", "rx", "min", "max"}:
        metric_l = "max"
    mode = (time_mode or "current").strip().lower()
    if mode not in {"current", "range_max", "range_min"}:
        mode = "current"
    if mode in {"range_max", "range_min"}:
        if not isinstance(time_from, int) or not isinstance(time_till, int):
            raise RuntimeError("time_from and time_till are required for range mode")
        if time_till <= time_from:
            raise RuntimeError("time_till must be greater than time_from")
    total_start = time.perf_counter()
    cleaned_targets = [
        t for t in (targets or [])
        if _looks_like_ip(str(t.source_device or "").strip()) and str(t.source_interface or "").strip()
    ]
    ip_name_hints: dict[str, str] = {}
    for t in cleaned_targets:
        src_ip_hint = str(t.source_device or "").strip()
        src_name_hint = _clean_device_name_token(str(t.source_name or "").strip())
        if _looks_like_ip(src_ip_hint) and src_name_hint:
            ip_name_hints.setdefault(src_ip_hint, src_name_hint)
        peer_ip_hint = str(t.peer_device or "").strip()
        peer_name_hint = _clean_device_name_token(str(t.peer_name or "").strip())
        if _looks_like_ip(peer_ip_hint) and peer_name_hint:
            ip_name_hints.setdefault(peer_ip_hint, peer_name_hint)
    device_ips = sorted({
        ip for ip in (
            str(t.source_device or "").strip() for t in cleaned_targets
        ) if ip
    } | {
        ip for ip in (
            str(t.peer_device or "").strip() for t in cleaned_targets
        ) if _looks_like_ip(ip)
    })
    source_names = sorted({
        name for name in (
            _clean_device_name_token(str(t.source_name or "").strip()) for t in cleaned_targets
        ) if name
    } | {
        name for name in (
            _clean_device_name_token(str(t.peer_name or "").strip()) for t in cleaned_targets
        ) if name
    } | set(ip_name_hints.values()))
    host_map = _zabbix_get_host_map_by_ip(
        device_ips,
        url_override=zabbix_url,
        token_override=zabbix_api_token,
        verify_ssl_override=zabbix_verify_ssl,
    )
    host_map_by_name = _zabbix_get_host_map_by_name(
        source_names,
        url_override=zabbix_url,
        token_override=zabbix_api_token,
        verify_ssl_override=zabbix_verify_ssl,
    )

    results: list[dict[str, Any]] = []
    queried_devices: list[str] = []
    item_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    speed_cache: dict[tuple[str, str, int, int], float | None] = {}
    bandwidth_cache = build_bandwidth_cache_index(read_link_util_cache_rows())
    for target in cleaned_targets:
        src_ip = str(target.source_device or "").strip()
        iface = str(target.source_interface or "").strip()
        util_key = str(target.util_key or "").strip()
        source_name = _clean_device_name_token(str(target.source_name or "").strip())
        if not source_name and util_key.startswith("name:"):
            source_name = _clean_device_name_token(util_key.split("||", 1)[0][5:])
        if not source_name and _looks_like_ip(src_ip):
            source_name = ip_name_hints.get(src_ip, "")
        peer_ip = str(target.peer_device or "").strip()
        peer_iface = str(target.peer_interface or "").strip()
        peer_name = _clean_device_name_token(str(target.peer_name or "").strip())
        host, items, tx_item, rx_item, speed_item = _pick_best_zabbix_host_for_target(
            src_ip,
            source_name,
            iface,
            host_map_by_ip=host_map,
            host_map_by_name=host_map_by_name,
            item_cache=item_cache,
            url_override=zabbix_url,
            token_override=zabbix_api_token,
            verify_ssl_override=zabbix_verify_ssl,
        )
        if not host:
            results.append(
                {
                    "util_key": util_key,
                    "data_source": "zabbix",
                    "source_device": src_ip,
                    "source_name": source_name,
                    "source_interface": iface,
                    "vendor": "zabbix",
                    "status": "failed",
                    "tx_pct": None,
                    "rx_pct": None,
                    "used_pct": None,
                    "tx_bps": None,
                    "rx_bps": None,
                    "bw_bps": None,
                    "error": "host not found by device IP/name in Zabbix",
                }
            )
            continue
        queried_devices.append(src_ip)
        tx_bps = (
            _zabbix_item_last_bps(tx_item)
            if mode == "current"
            else (
                _zabbix_item_time_min(
                    tx_item,
                    int(time_from),
                    int(time_till),
                    url_override=zabbix_url,
                    token_override=zabbix_api_token,
                    verify_ssl_override=zabbix_verify_ssl,
                )
                if mode == "range_min" else
                _zabbix_item_time_max(
                    tx_item,
                    int(time_from),
                    int(time_till),
                    url_override=zabbix_url,
                    token_override=zabbix_api_token,
                    verify_ssl_override=zabbix_verify_ssl,
                )
            )
        ) if tx_item else None
        rx_bps = (
            _zabbix_item_last_bps(rx_item)
            if mode == "current"
            else (
                _zabbix_item_time_min(
                    rx_item,
                    int(time_from),
                    int(time_till),
                    url_override=zabbix_url,
                    token_override=zabbix_api_token,
                    verify_ssl_override=zabbix_verify_ssl,
                )
                if mode == "range_min" else
                _zabbix_item_time_max(
                    rx_item,
                    int(time_from),
                    int(time_till),
                    url_override=zabbix_url,
                    token_override=zabbix_api_token,
                    verify_ssl_override=zabbix_verify_ssl,
                )
            )
        ) if rx_item else None
        bw_bps = bandwidth_cache.get((src_ip, iface))
        if not (isinstance(bw_bps, (int, float)) and bw_bps > 0):
            bw_bps = _zabbix_speed_bps_with_fallback(
                speed_item,
                time_mode=mode,
                time_from=int(time_from) if mode in {"range_max", "range_min"} and time_from is not None else None,
                time_till=int(time_till) if mode in {"range_max", "range_min"} and time_till is not None else None,
                cache=speed_cache,
                url_override=zabbix_url,
                token_override=zabbix_api_token,
                verify_ssl_override=zabbix_verify_ssl,
            )
            if isinstance(bw_bps, (int, float)) and bw_bps > 0:
                bandwidth_cache[(src_ip, iface)] = float(bw_bps)
        if not (isinstance(bw_bps, (int, float)) and bw_bps > 0) and _looks_like_ip(peer_ip) and peer_iface:
            bw_bps = bandwidth_cache.get((peer_ip, peer_iface))
            if not (isinstance(bw_bps, (int, float)) and bw_bps > 0):
                peer_host, peer_items, _peer_tx_item, _peer_rx_item, peer_speed_item = _pick_best_zabbix_host_for_target(
                    peer_ip,
                    peer_name,
                    peer_iface,
                    host_map_by_ip=host_map,
                    host_map_by_name=host_map_by_name,
                    item_cache=item_cache,
                    url_override=zabbix_url,
                    token_override=zabbix_api_token,
                    verify_ssl_override=zabbix_verify_ssl,
                )
                if peer_host:
                    bw_bps = _zabbix_speed_bps_with_fallback(
                        peer_speed_item,
                        time_mode=mode,
                        time_from=int(time_from) if mode in {"range_max", "range_min"} and time_from is not None else None,
                        time_till=int(time_till) if mode in {"range_max", "range_min"} and time_till is not None else None,
                        cache=speed_cache,
                        url_override=zabbix_url,
                        token_override=zabbix_api_token,
                        verify_ssl_override=zabbix_verify_ssl,
                    )
                    if isinstance(bw_bps, (int, float)) and bw_bps > 0:
                        bandwidth_cache[(peer_ip, peer_iface)] = float(bw_bps)
                        bandwidth_cache[(src_ip, iface)] = float(bw_bps)
        if not (isinstance(bw_bps, (int, float)) and bw_bps > 0):
            inferred_bw = _infer_bw_bps_from_iface_name(iface)
            if not (isinstance(inferred_bw, (int, float)) and inferred_bw > 0) and peer_iface:
                inferred_bw = _infer_bw_bps_from_iface_name(peer_iface)
            if isinstance(inferred_bw, (int, float)) and inferred_bw > 0:
                bw_bps = float(inferred_bw)
                bandwidth_cache[(src_ip, iface)] = float(inferred_bw)
                if _looks_like_ip(peer_ip) and peer_iface:
                    bandwidth_cache[(peer_ip, peer_iface)] = float(inferred_bw)
        tx_pct = (tx_bps / bw_bps * 100.0) if (tx_bps is not None and bw_bps and bw_bps > 0) else None
        rx_pct = (rx_bps / bw_bps * 100.0) if (rx_bps is not None and bw_bps and bw_bps > 0) else None
        if metric_l == "tx":
            used_pct = tx_pct
        elif metric_l == "rx":
            used_pct = rx_pct
        elif metric_l == "min":
            vals = [v for v in [tx_pct, rx_pct] if isinstance(v, (int, float))]
            used_pct = min(vals) if vals else None
        else:
            vals = [v for v in [tx_pct, rx_pct] if isinstance(v, (int, float))]
            used_pct = max(vals) if vals else None
        err_parts = []
        if not tx_item:
            err_parts.append("tx item not found")
        if not rx_item:
            err_parts.append("rx item not found")
        if not speed_item:
            err_parts.append("speed item not found")
        if (not speed_item) and isinstance(bw_bps, (int, float)) and bw_bps > 0 and _looks_like_ip(peer_ip) and peer_iface:
            err_parts.append("peer speed fallback")
        if mode in {"range_max", "range_min"}:
            if tx_item and tx_bps is None:
                err_parts.append("tx history not found in selected time window")
            if rx_item and rx_bps is None:
                err_parts.append("rx history not found in selected time window")
        status = "ok" if (isinstance(used_pct, (int, float)) or isinstance(tx_pct, (int, float)) or isinstance(rx_pct, (int, float))) else "no_data"
        results.append(
            {
                "util_key": util_key,
                "data_source": "zabbix",
                "time_mode": mode,
                "time_from": int(time_from) if mode in {"range_max", "range_min"} and time_from is not None else "",
                "time_till": int(time_till) if mode in {"range_max", "range_min"} and time_till is not None else "",
                "source_device": src_ip,
                "source_name": source_name,
                "source_interface": iface,
                "vendor": "zabbix",
                "status": status,
                "tx_pct": tx_pct,
                "rx_pct": rx_pct,
                "used_pct": used_pct,
                "tx_bps": tx_bps,
                "rx_bps": rx_bps,
                "bw_bps": bw_bps,
                "error": "; ".join(err_parts) if status != "ok" else "",
                "zabbix_host": host.get("host") or host.get("name") or "",
            }
        )
    return {
        "results": results,
        "queried_devices": sorted(set(queried_devices)),
        "total_elapsed_seconds": max(0.0, time.perf_counter() - total_start),
        "time_mode": mode,
        "time_from": int(time_from) if mode in {"range_max", "range_min"} and time_from is not None else None,
        "time_till": int(time_till) if mode in {"range_max", "range_min"} and time_till is not None else None,
    }


def read_link_util_cache_rows() -> list[dict[str, Any]]:
    if not LINK_UTIL_CACHE_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with LINK_UTIL_CACHE_FILE.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                util_key = str(r.get("util_key", "")).strip().lower()
                if not util_key:
                    continue
                rows.append(
                    {
                        "util_key": util_key,
                        "data_source": str(r.get("data_source", "")).strip() or "cli",
                        "time_mode": str(r.get("time_mode", "")).strip() or "current",
                        "time_from": str(r.get("time_from", "")).strip(),
                        "time_till": str(r.get("time_till", "")).strip(),
                        "source_device": str(r.get("source_device", "")).strip(),
                        "source_interface": str(r.get("source_interface", "")).strip(),
                        "vendor": str(r.get("vendor", "")).strip(),
                        "status": str(r.get("status", "")).strip() or "no_data",
                        "tx_pct": _parse_num(r.get("tx_pct")),
                        "rx_pct": _parse_num(r.get("rx_pct")),
                        "used_pct": _parse_num(r.get("used_pct")),
                        "tx_bps": _parse_num(r.get("tx_bps")),
                        "rx_bps": _parse_num(r.get("rx_bps")),
                        "bw_bps": _parse_num(r.get("bw_bps")),
                        "error": str(r.get("error", "")).strip(),
                        "updated_at": str(r.get("updated_at", "")).strip(),
                    }
                )
    except Exception:
        return []
    return rows


def merge_link_util_cache(results: list[dict[str, Any]]) -> int:
    by_key: dict[str, dict[str, Any]] = {
        str(r.get("util_key", "")).strip().lower(): r
        for r in read_link_util_cache_rows()
        if str(r.get("util_key", "")).strip()
    }
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updates = 0
    for r in results or []:
        util_key = str(r.get("util_key", "")).strip().lower()
        if not util_key:
            src = str(r.get("source_device", "")).strip().lower()
            sif = str(r.get("source_interface", "")).strip()
            if src and sif:
                util_key = f"{src}||{sif}".lower()
        if not util_key:
            continue
        by_key[util_key] = {
            "util_key": util_key,
            "data_source": str(r.get("data_source", "")).strip() or "cli",
            "time_mode": str(r.get("time_mode", "")).strip() or "current",
            "time_from": str(r.get("time_from", "")).strip(),
            "time_till": str(r.get("time_till", "")).strip(),
            "source_device": str(r.get("source_device", "")).strip(),
            "source_interface": str(r.get("source_interface", "")).strip(),
            "vendor": str(r.get("vendor", "")).strip(),
            "status": str(r.get("status", "")).strip() or "no_data",
            "tx_pct": r.get("tx_pct"),
            "rx_pct": r.get("rx_pct"),
            "used_pct": r.get("used_pct"),
            "tx_bps": r.get("tx_bps"),
            "rx_bps": r.get("rx_bps"),
            "bw_bps": r.get("bw_bps"),
            "error": str(r.get("error", "")).strip(),
            "updated_at": now,
        }
        updates += 1

    try:
        with LINK_UTIL_CACHE_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LINK_UTIL_CACHE_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for k in sorted(by_key.keys()):
                row = by_key[k]
                writer.writerow(
                    {
                        "util_key": row.get("util_key", ""),
                        "data_source": row.get("data_source", ""),
                        "time_mode": row.get("time_mode", ""),
                        "time_from": row.get("time_from", ""),
                        "time_till": row.get("time_till", ""),
                        "source_device": row.get("source_device", ""),
                        "source_interface": row.get("source_interface", ""),
                        "vendor": row.get("vendor", ""),
                        "status": row.get("status", ""),
                        "tx_pct": _num_or_empty(row.get("tx_pct")),
                        "rx_pct": _num_or_empty(row.get("rx_pct")),
                        "used_pct": _num_or_empty(row.get("used_pct")),
                        "tx_bps": _num_or_empty(row.get("tx_bps")),
                        "rx_bps": _num_or_empty(row.get("rx_bps")),
                        "bw_bps": _num_or_empty(row.get("bw_bps")),
                        "error": row.get("error", ""),
                        "updated_at": row.get("updated_at", ""),
                    }
                )
    except Exception:
        return 0
    return updates


def build_bandwidth_cache_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    index: dict[tuple[str, str], float] = {}
    for row in rows or []:
        src = str(row.get("source_device", "")).strip()
        iface = str(row.get("source_interface", "")).strip()
        bw = row.get("bw_bps")
        if not src or not iface or not isinstance(bw, (int, float)) or bw <= 0:
            continue
        index[(src, iface)] = float(bw)
    return index


def clear_link_util_cache() -> int:
    rows = read_link_util_cache_rows()
    try:
        with LINK_UTIL_CACHE_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LINK_UTIL_CACHE_FIELDS, extrasaction="ignore")
            writer.writeheader()
    except Exception:
        return 0
    return len(rows)


def _has_active_jobs() -> bool:
    active_status = {"queued", "running", "cancelling"}
    for jobs, lock in (
        (SQL_QUERY_JOBS, SQL_QUERY_JOB_LOCK),
        (CLI_QUERY_JOBS, CLI_QUERY_JOB_LOCK),
        (LINK_UTIL_JOBS, LINK_UTIL_JOB_LOCK),
    ):
        with lock:
            for job in jobs.values():
                status = str(job.get("status") or "").strip().lower()
                if status in active_status:
                    return True
    return False


def cleanup_temp_runtime_data() -> dict[str, int]:
    if _has_active_jobs():
        raise HTTPException(status_code=409, detail="active tasks are running; stop or wait for them before cleanup")

    removed_tmp_files = 0
    removed_snapshot_files = 0
    cleared_log_files = 0

    if TMP_DIR.exists():
        for path in TMP_DIR.iterdir():
            if not path.is_file():
                continue
            try:
                path.unlink(missing_ok=True)
                removed_tmp_files += 1
            except Exception:
                continue

    if STATE_DIR.exists():
        for path in STATE_DIR.glob("*.json"):
            try:
                path.unlink(missing_ok=True)
                removed_snapshot_files += 1
            except Exception:
                continue

    log_path = BASE_DIR / ".lldp_service.log"
    if log_path.exists():
        try:
            log_path.write_text("", encoding="utf-8")
            cleared_log_files = 1
        except Exception:
            cleared_log_files = 0

    return {
        "removed_tmp_files": removed_tmp_files,
        "removed_snapshot_files": removed_snapshot_files,
        "cleared_log_files": cleared_log_files,
    }


def _unit_multiplier(unit: str) -> float:
    u = (unit or "").strip().lower()
    if u in {"g", "gb", "gbit", "gbps"}:
        return 1_000_000_000.0
    if u in {"m", "mb", "mbit", "mbps"}:
        return 1_000_000.0
    if u in {"k", "kb", "kbit", "kbps"}:
        return 1_000.0
    return 1.0


def _parse_float_num(s: str) -> float | None:
    try:
        return float((s or "").replace(",", "").strip())
    except Exception:
        return None


def _extract_first_pct(text: str, patterns: list[str]) -> float | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        val = _parse_float_num(m.group(1))
        if val is None:
            continue
        return max(0.0, min(100.0, val))
    return None


def _extract_first_bps(text: str, patterns: list[str]) -> float | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        val = _parse_float_num(m.group(1))
        if val is None:
            continue
        unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "") or ""
        return max(0.0, val * _unit_multiplier(unit))
    return None


def _extract_bandwidth_bps(text: str) -> float | None:
    patterns = [
        r"\bBW\s*[:=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
        r"\bBandwidth\s*[:=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
        r"\bline\s+rate\s*[:=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg])\s*(?:bps|bit)",
    ]
    return _extract_first_bps(text, patterns)


def parse_interface_utilization(output: str, metric: str = "tx") -> dict[str, Any]:
    text = (output or "").replace("\r", "")
    tx_pct = _extract_first_pct(
        text,
        [
            r"\boutput\s+utili[sz]ation[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\boutput\s+utility\s+rate[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\boutput[^\n]{0,160},\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\boutput[^\n]{0,160}\(([0-9]+(?:\.[0-9]+)?)%\)",
            r"\btx[^%\n]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\boutput\s+rate\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        ],
    )
    rx_pct = _extract_first_pct(
        text,
        [
            r"\binput\s+utili[sz]ation[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\binput\s+utility\s+rate[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\binput[^\n]{0,160},\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\binput[^\n]{0,160}\(([0-9]+(?:\.[0-9]+)?)%\)",
            r"\brx[^%\n]{0,40}([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\binput\s+rate\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        ],
    )

    tx_bps = _extract_first_bps(
        text,
        [
            r"(?:30\s+seconds?\s+|5\s+minutes?\s+)?output\s+rate[^0-9\n]*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
            r"(?:5\s+minutes?\s+)?output\s+rate[^0-9\n]*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
            r"\btx[^0-9\n]{0,40}([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
        ],
    )
    rx_bps = _extract_first_bps(
        text,
        [
            r"(?:30\s+seconds?\s+|5\s+minutes?\s+)?input\s+rate[^0-9\n]*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
            r"(?:5\s+minutes?\s+)?input\s+rate[^0-9\n]*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
            r"\brx[^0-9\n]{0,40}([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kmg]?)\s*(?:bits?/s(?:ec)?|bps|bit)",
        ],
    )
    bw_bps = _extract_bandwidth_bps(text)

    if tx_pct is None and tx_bps is not None and bw_bps and bw_bps > 0:
        tx_pct = max(0.0, min(100.0, (tx_bps / bw_bps) * 100.0))
    if rx_pct is None and rx_bps is not None and bw_bps and bw_bps > 0:
        rx_pct = max(0.0, min(100.0, (rx_bps / bw_bps) * 100.0))

    m = (metric or "tx").lower()
    if m == "rx":
        used_pct = rx_pct
    elif m == "min":
        cands = [x for x in (tx_pct, rx_pct) if x is not None]
        used_pct = min(cands) if cands else None
    elif m == "max":
        cands = [x for x in (tx_pct, rx_pct) if x is not None]
        used_pct = max(cands) if cands else None
    else:
        used_pct = tx_pct

    status = "ok" if used_pct is not None or tx_pct is not None or rx_pct is not None else "no_data"
    return {
        "status": status,
        "tx_pct": tx_pct,
        "rx_pct": rx_pct,
        "used_pct": used_pct,
        "tx_bps": tx_bps,
        "rx_bps": rx_bps,
        "bw_bps": bw_bps,
    }


def run_interface_utilization_command(cli: SmcShellClient, vendor: str, ifname: str, timeout: int) -> str:
    v = (vendor or "").strip().lower()
    iface = (ifname or "").strip()
    if v == "huawei":
        cmds = [f"display interface {iface}", f"dis interface {iface}"]
    elif v == "cisco":
        cmds = [f"show interface {iface}", f"show interfaces {iface}"]
    elif v == "arista":
        cmds = [f"show interfaces {iface}", f"show interface {iface}"]
    else:
        cmds = [f"show interface {iface}", f"display interface {iface}", f"show interfaces {iface}"]
    best = ""
    invalid_outputs: list[str] = []
    for cmd in cmds:
        try:
            out = cli.exec(cmd, timeout=timeout)
        except Exception:
            continue
        if not out.strip():
            continue
        if INVALID_CMD_PATTERN.search(out):
            invalid_outputs.append(cmd)
            continue
        return out
    if invalid_outputs:
        raise RuntimeError(f"invalid interface command for {iface}: tried {', '.join(invalid_outputs)}")
    return best


def collect_device_interface_utils_once(
    source: str,
    iface_targets: dict[str, list[LinkUtilTarget]],
    metric: str,
    cfg: CliRuntimeConfig,
) -> dict[str, Any]:
    transcript: list[str] = []
    started = time.time()

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
    results: list[dict[str, Any]] = []
    error = ""
    try:
        cli.connect()
        vendor = detect_vendor(cli, timeout=min(cfg.command_timeout, 20))
        disable_paging(cli, vendor, timeout=3)
        for ifname, refs in iface_targets.items():
            parsed = {
                "status": "no_data",
                "tx_pct": None,
                "rx_pct": None,
                "used_pct": None,
                "tx_bps": None,
                "rx_bps": None,
                "bw_bps": None,
            }
            cmd_error = ""
            try:
                out = run_interface_utilization_command(cli, vendor, ifname, timeout=cfg.command_timeout)
                if not (out or "").strip():
                    raise RuntimeError(f"empty interface output for {ifname}")
                parsed = parse_interface_utilization(out, metric=metric)
                if parsed.get("status") == "no_data":
                    # Surface parsing misses as failed so frontend can distinguish from true zero-utilization.
                    cmd_error = f"no parsable utilization fields for {ifname}"
                    parsed["status"] = "failed"
            except Exception as exc:
                cmd_error = str(exc)
                parsed["status"] = "failed"
            for ref in refs:
                results.append(
                    {
                        "data_source": "cli",
                        "source_device": source,
                        "source_interface": ifname,
                        "util_key": ref.util_key or "",
                        "vendor": vendor,
                        "status": parsed.get("status", "no_data"),
                        "tx_pct": parsed.get("tx_pct"),
                        "rx_pct": parsed.get("rx_pct"),
                        "used_pct": parsed.get("used_pct"),
                        "tx_bps": parsed.get("tx_bps"),
                        "rx_bps": parsed.get("rx_bps"),
                        "bw_bps": parsed.get("bw_bps"),
                        "error": cmd_error,
                    }
                )
    except Exception as exc:
        error = str(exc)
    finally:
        cli.close()

    if error:
        results = []
        for ifname, refs in iface_targets.items():
            for ref in refs:
                results.append(
                    {
                        "data_source": "cli",
                        "source_device": source,
                        "source_interface": ifname,
                        "util_key": ref.util_key or "",
                        "vendor": vendor,
                        "status": "failed",
                        "tx_pct": None,
                        "rx_pct": None,
                        "used_pct": None,
                        "tx_bps": None,
                        "rx_bps": None,
                        "bw_bps": None,
                        "error": error,
                    }
                )
    return {
        "device": source,
        "vendor": vendor,
        "error": error,
        "duration_sec": max(0.0, time.time() - started),
        "results": results,
        "transcript": transcript[:],
    }


def collect_link_utilization(
    targets: list[LinkUtilTarget],
    metric: str,
    *,
    cli_max_workers: int | None = None,
    device_username: str | None = None,
    device_password: str | None = None,
    smc_jump_host: str | None = None,
    smc_jump_port: int | None = None,
    smc_command: str | None = None,
    cli_command_timeout: int | None = None,
    cli_connect_timeout: int | None = None,
    debug_enabled: bool = False,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    cfg = get_cli_runtime_config(
        device_username=device_username,
        device_password=device_password,
        smc_jump_host=smc_jump_host,
        smc_jump_port=smc_jump_port,
        smc_command=smc_command,
        cli_command_timeout=cli_command_timeout,
        cli_connect_timeout=cli_connect_timeout,
    )
    env_workers = int(get_env("CLI_MAX_WORKERS", "4") or "4")
    workers = int(cli_max_workers if cli_max_workers is not None else env_workers)
    workers = max(1, min(16, workers))

    grouped: dict[str, dict[str, list[LinkUtilTarget]]] = {}
    for t in targets:
        dev = normalize_device_id(t.source_device)
        iface = (t.source_interface or "").strip()
        if not dev or not iface:
            continue
        grouped.setdefault(dev, {}).setdefault(iface, []).append(t)

    out_results: list[dict[str, Any]] = []
    debug_entries: list[dict[str, Any]] = []
    device_timings: list[dict[str, Any]] = []
    total_start = time.perf_counter()
    devices = sorted(grouped.keys())
    total_devices = len(devices)
    finished_devices = 0
    per_device_timeout = max(15, min(90, int(cfg.connect_timeout) + int(cfg.command_timeout) + 10))

    def emit_progress(status: str, current_device: str = "") -> None:
        if not progress_cb:
            return
        try:
            progress_cb(
                {
                    "status": status,
                    "finished_devices": finished_devices,
                    "total_devices": total_devices,
                    "current_device": current_device,
                    "elapsed_seconds": max(0.0, time.perf_counter() - total_start),
                }
            )
        except Exception:
            pass

    emit_progress("running")

    if devices:
        pool = ThreadPoolExecutor(max_workers=min(workers, len(devices)))
        try:
            by_dev = {
                dev: pool.submit(collect_device_interface_utils_once, dev, grouped[dev], metric, cfg)
                for dev in devices
            }
            pending = dict(by_dev)
            while pending:
                if cancel_event and cancel_event.is_set():
                    for dev, fut in pending.items():
                        try:
                            fut.cancel()
                        except Exception:
                            pass
                        out_results.extend(
                            {
                                "source_device": dev,
                                "source_interface": iface,
                                "util_key": ref.util_key or "",
                                "vendor": "unknown",
                                "status": "failed",
                                "tx_pct": None,
                                "rx_pct": None,
                                "used_pct": None,
                                "tx_bps": None,
                                "rx_bps": None,
                                "bw_bps": None,
                                "error": "cancelled_by_user",
                            }
                            for iface, refs in grouped.get(dev, {}).items()
                            for ref in refs
                        )
                        debug_entries.append({"device": dev, "status": "failed", "error": "cancelled_by_user"})
                        device_timings.append(
                            {
                                "device": dev,
                                "status": "cancelled",
                                "duration_sec": 0.0,
                                "vendor": "unknown",
                                "queried_ports": len(grouped.get(dev, {})),
                                "error": "cancelled_by_user",
                            }
                        )
                    finished_devices = total_devices
                    emit_progress("cancelled")
                    break

                dev, fut = next(iter(pending.items()))
                pending.pop(dev, None)
                try:
                    res = fut.result(timeout=per_device_timeout)
                except FuturesTimeoutError:
                    out_results.extend(
                        {
                            "source_device": dev,
                            "source_interface": iface,
                            "util_key": ref.util_key or "",
                            "vendor": "unknown",
                            "status": "failed",
                            "tx_pct": None,
                            "rx_pct": None,
                            "used_pct": None,
                            "tx_bps": None,
                            "rx_bps": None,
                            "bw_bps": None,
                            "error": f"device_timeout: exceeded {per_device_timeout}s",
                        }
                        for iface, refs in grouped.get(dev, {}).items()
                        for ref in refs
                    )
                    debug_entries.append(
                        {
                            "device": dev,
                            "status": "failed",
                            "error": f"device_timeout: exceeded {per_device_timeout}s",
                        }
                    )
                    device_timings.append(
                        {
                            "device": dev,
                            "status": "failed",
                            "duration_sec": float(per_device_timeout),
                            "vendor": "unknown",
                            "queried_ports": len(grouped.get(dev, {})),
                            "error": f"device_timeout: exceeded {per_device_timeout}s",
                        }
                    )
                    finished_devices += 1
                    emit_progress("running", dev)
                    continue
                except Exception as exc:
                    out_results.extend(
                        {
                            "source_device": dev,
                            "source_interface": iface,
                            "util_key": ref.util_key or "",
                            "vendor": "unknown",
                            "status": "failed",
                            "tx_pct": None,
                            "rx_pct": None,
                            "used_pct": None,
                            "tx_bps": None,
                            "rx_bps": None,
                            "bw_bps": None,
                            "error": f"worker_exception: {exc}",
                        }
                        for iface, refs in grouped.get(dev, {}).items()
                        for ref in refs
                    )
                    debug_entries.append({"device": dev, "status": "failed", "error": f"worker_exception: {exc}"})
                    device_timings.append(
                        {
                            "device": dev,
                            "status": "failed",
                            "duration_sec": 0.0,
                            "vendor": "unknown",
                            "queried_ports": len(grouped.get(dev, {})),
                            "error": f"worker_exception: {exc}",
                        }
                    )
                    finished_devices += 1
                    emit_progress("running", dev)
                    continue
                out_results.extend(res.get("results", []))
                dev_status = "failed" if res.get("error") else "ok"
                debug_entries.append(
                    {
                        "device": dev,
                        "status": dev_status,
                        "vendor": res.get("vendor", "unknown"),
                        "error": res.get("error", ""),
                        "duration_sec": res.get("duration_sec", 0.0),
                        "transcript": (res.get("transcript", []) if debug_enabled else []),
                    }
                )
                device_timings.append(
                    {
                        "device": dev,
                        "status": dev_status,
                        "duration_sec": float(res.get("duration_sec", 0.0) or 0.0),
                        "vendor": res.get("vendor", "unknown"),
                        "queried_ports": len(grouped.get(dev, {})),
                        "error": str(res.get("error", "") or ""),
                    }
                )
                finished_devices += 1
                emit_progress("running", dev)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    total_elapsed_seconds = max(0.0, time.perf_counter() - total_start)
    emit_progress("cancelled" if (cancel_event and cancel_event.is_set()) else "completed")
    return {
        "results": out_results,
        "debug_entries": debug_entries,
        "device_timings": device_timings,
        "cli_max_workers": workers,
        "queried_devices": devices,
        "total_elapsed_seconds": total_elapsed_seconds,
        "per_device_timeout": per_device_timeout,
        "cancelled": bool(cancel_event and cancel_event.is_set()),
        "finished_devices": finished_devices,
        "total_devices": total_devices,
    }


def _cleanup_expired_jobs(job_store: dict[str, dict[str, Any]], lock: threading.Lock, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    now = time.time()
    with lock:
        stale = []
        for job_id, job in job_store.items():
            finished_at = float(job.get("finished_at") or 0.0)
            status = str(job.get("status", ""))
            base_ts = finished_at if finished_at > 0 else float(job.get("updated_at") or now)
            if status in {"completed", "failed", "cancelled"} and (now - base_ts) > ttl_seconds:
                stale.append(job_id)
        for job_id in stale:
            job_store.pop(job_id, None)


def _link_util_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": job.get("task_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "progress": job.get("progress", {}),
        "error": job.get("error", ""),
        "response": job.get("response"),
    }


def _run_link_utilization_task(task_id: str) -> None:
    with LINK_UTIL_JOB_LOCK:
        job = LINK_UTIL_JOBS.get(task_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        job["updated_at"] = time.time()
        payload = dict(job.get("payload", {}))
        cancel_event = job.get("cancel_event")

    def progress_cb(update: dict[str, Any]) -> None:
        with LINK_UTIL_JOB_LOCK:
            cur = LINK_UTIL_JOBS.get(task_id)
            if not cur:
                return
            cur["progress"] = {
                "finished_devices": int(update.get("finished_devices", 0) or 0),
                "total_devices": int(update.get("total_devices", 0) or 0),
                "current_device": str(update.get("current_device", "") or ""),
                "elapsed_seconds": float(update.get("elapsed_seconds", 0.0) or 0.0),
                "status": str(update.get("status", "running") or "running"),
            }
            cur["updated_at"] = time.time()

    try:
        target_objs = [LinkUtilTarget(**x) for x in (payload.get("targets") or [])]
        metric = (payload.get("metric") or "tx").strip().lower()
        meta = collect_link_utilization(
            target_objs,
            metric=metric,
            cli_max_workers=payload.get("cli_max_workers"),
            device_username=payload.get("device_username"),
            device_password=payload.get("device_password"),
            smc_jump_host=payload.get("smc_jump_host"),
            smc_jump_port=payload.get("smc_jump_port"),
            smc_command=payload.get("smc_command"),
            cli_command_timeout=payload.get("cli_command_timeout"),
            cli_connect_timeout=payload.get("cli_connect_timeout"),
            debug_enabled=bool(payload.get("debug_enabled")),
            progress_cb=progress_cb,
            cancel_event=cancel_event if isinstance(cancel_event, threading.Event) else None,
        )
        cache_updates = merge_link_util_cache(meta.get("results", []))
        cache_total = len(read_link_util_cache_rows())

        debug_download_url = ""
        debug_file = ""
        if payload.get("debug_enabled"):
            safe_name, debug_path = write_link_util_debug_text(
                f"link_util_debug_{int(time.time())}",
                {**meta, "metric": metric},
            )
            debug_file = str(debug_path)
            debug_download_url = f"/api/lldp-debug/file/{safe_name}"

        response = {
            "ok": True,
            "mode": "cli-link-utilization",
            "metric": metric,
            "result_count": len(meta.get("results", [])),
            "results": meta.get("results", []),
            "queried_devices": meta.get("queried_devices", []),
            "cli_max_workers": meta.get("cli_max_workers"),
            "per_device_timeout": meta.get("per_device_timeout"),
            "total_elapsed_seconds": meta.get("total_elapsed_seconds"),
            "debug_entries": meta.get("debug_entries", []),
            "device_timings": meta.get("device_timings", []),
            "finished_devices": meta.get("finished_devices"),
            "total_devices": meta.get("total_devices"),
            "cache_updates": cache_updates,
            "cache_total": cache_total,
            "debug_file": debug_file,
            "debug_download_url": debug_download_url,
        }
        final_status = "cancelled" if meta.get("cancelled") else "completed"
        with LINK_UTIL_JOB_LOCK:
            cur = LINK_UTIL_JOBS.get(task_id)
            if not cur:
                return
            cur["status"] = final_status
            cur["response"] = response
            cur["error"] = ""
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()
            cur["progress"] = {
                "finished_devices": int(meta.get("finished_devices", 0) or 0),
                "total_devices": int(meta.get("total_devices", 0) or 0),
                "current_device": "",
                "elapsed_seconds": float(meta.get("total_elapsed_seconds", 0.0) or 0.0),
                "status": final_status,
            }
    except Exception as exc:
        with LINK_UTIL_JOB_LOCK:
            cur = LINK_UTIL_JOBS.get(task_id)
            if not cur:
                return
            cur["status"] = "failed"
            cur["error"] = str(exc)
            cur["response"] = None
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()


def _create_link_utilization_task(payload: LinkUtilRequest) -> dict[str, Any]:
    _cleanup_expired_jobs(LINK_UTIL_JOBS, LINK_UTIL_JOB_LOCK, LINK_UTIL_JOB_TTL_SEC)
    task_id = uuid.uuid4().hex
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    # Password is used only in worker process memory; do not expose it through task query API.
    body_sanitized = dict(body)
    body_sanitized["device_password"] = body.get("device_password")
    created = time.time()
    job = {
        "task_id": task_id,
        "status": "queued",
        "payload": body_sanitized,
        "cancel_event": threading.Event(),
        "created_at": created,
        "started_at": 0.0,
        "updated_at": created,
        "finished_at": 0.0,
        "progress": {"finished_devices": 0, "total_devices": 0, "current_device": "", "elapsed_seconds": 0.0, "status": "queued"},
        "error": "",
        "response": None,
    }
    with LINK_UTIL_JOB_LOCK:
        LINK_UTIL_JOBS[task_id] = job
    t = threading.Thread(target=_run_link_utilization_task, args=(task_id,), daemon=True)
    t.start()
    return {"task_id": task_id, "status": "queued"}


def _run_sql_lldp_query(
    start_hostname: str,
    max_depth: int,
    *,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_name: str | None = None,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    connection_cb: Callable[[pymysql.connections.Connection | None], None] | None = None,
) -> dict[str, Any]:
    def emit_progress(status: str) -> None:
        if not progress_cb:
            return
        try:
            progress_cb({
                "status": status,
                "elapsed_seconds": max(0.0, time.perf_counter() - t0) if "t0" in locals() else 0.0,
            })
        except Exception:
            return

    if cancel_event and cancel_event.is_set():
        raise RuntimeError("cancelled_by_user")
    try:
        conn = connect_db(
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_name=db_name,
        )
    except Exception as exc:
        raise RuntimeError(f"DB connect failed: {exc}") from exc

    t0 = time.perf_counter()
    if connection_cb:
        try:
            connection_cb(conn)
        except Exception:
            pass
    try:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("cancelled_by_user")
        rows: list[dict[str, Any]] = []
        with conn.cursor() as cur:
            emit_progress("running")
            seed_hosts = _resolve_sql_seed_hosts(cur, start_hostname)
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("cancelled_by_user")
            emit_progress("running")
            rows = _query_lldp_depth_rows(cur, seed_hosts, max_depth=max_depth)
    except Exception as exc:
        elapsed = max(0.0, time.perf_counter() - t0)
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("cancelled_by_user") from exc
        raise RuntimeError(f"SQL execution failed after {elapsed:.1f}s: {exc}") from exc
    finally:
        if connection_cb:
            try:
                connection_cb(None)
            except Exception:
                pass
        conn.close()

    for r in rows:
        sip = str(r.get("sourceip", "") or "").strip()
        rh = str(r.get("remotehostname", "") or "").strip()
        rip = str(r.get("remoteip", "") or "").strip()
        r["sourceip"] = sip if _looks_like_ip(sip) else ""
        r["remotevendor"] = ""
        if _looks_like_ip(rip):
            r["remoteip"] = rip
        else:
            r["remoteip"] = rh if _looks_like_ip(rh) else ""

    rows = dedupe_lldp_rows(rows)
    safe_name, out_path, _ = write_rows_to_csv(
        f"lldp_{start_hostname.replace('/', '_').replace(' ', '_')}",
        rows,
        include_csv_text=False,
    )
    return {
        "ok": True,
        "mode": "sql",
        "start_hostname": start_hostname,
        "max_depth": max_depth,
        "row_count": len(rows),
        "elapsed_seconds": max(0.0, time.perf_counter() - t0),
        "temp_file": str(out_path),
        "download_url": f"/api/lldp-csv/file/{safe_name}",
    }


def _sql_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": job.get("task_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "progress": job.get("progress", {}),
        "error": job.get("error", ""),
        "response": job.get("response"),
    }


def _state_snapshot_path(snapshot_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "", str(snapshot_id or ""))
    if not safe_id:
        raise HTTPException(status_code=400, detail="invalid snapshot id")
    return STATE_DIR / f"{safe_id}.json"


def _state_snapshot_public(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to read state snapshot: {exc}") from exc
    return {
        "snapshot_id": str(payload.get("snapshot_id") or path.stem),
        "name": str(payload.get("name") or path.stem),
        "created_at": float(payload.get("created_at") or path.stat().st_mtime),
        "row_count": int(payload.get("row_count") or 0),
        "node_count": int(payload.get("node_count") or 0),
        "edge_count": int(payload.get("edge_count") or 0),
    }


def _cleanup_old_state_snapshots() -> None:
    files = sorted(
        [p for p in STATE_DIR.glob("*.json") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files[STATE_LIMIT:]:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


def list_state_snapshots() -> list[dict[str, Any]]:
    files = sorted(
        [p for p in STATE_DIR.glob("*.json") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [_state_snapshot_public(path) for path in files[:STATE_LIMIT]]


def save_state_snapshot(payload: StateSnapshotSaveRequest) -> dict[str, Any]:
    snapshot = payload.snapshot or {}
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=400, detail="snapshot is required")
    snapshot_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    created_at = time.time()
    name = str(payload.name or f"状态 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created_at))}").strip()
    wrapped = {
        "snapshot_id": snapshot_id,
        "name": name,
        "created_at": created_at,
        "row_count": len(snapshot.get("rows") or snapshot.get("working_rows") or []),
        "node_count": len(snapshot.get("positions") or {}),
        "edge_count": len(snapshot.get("working_rows") or snapshot.get("rows") or []),
        "snapshot": snapshot,
    }
    path = _state_snapshot_path(snapshot_id)
    path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
    _cleanup_old_state_snapshots()
    return _state_snapshot_public(path)


def load_state_snapshot(snapshot_id: str) -> dict[str, Any]:
    path = _state_snapshot_path(snapshot_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="state snapshot not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load state snapshot: {exc}") from exc
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=500, detail="state snapshot content is invalid")
    return {
        "ok": True,
        "snapshot_id": str(payload.get("snapshot_id") or snapshot_id),
        "name": str(payload.get("name") or snapshot_id),
        "created_at": float(payload.get("created_at") or path.stat().st_mtime),
        "snapshot": snapshot,
    }


def _run_sql_query_task(task_id: str) -> None:
    with SQL_QUERY_JOB_LOCK:
        job = SQL_QUERY_JOBS.get(task_id)
        if not job:
            return
        cancel_event = job.get("cancel_event")
        if str(job.get("status") or "") == "cancelled" or (isinstance(cancel_event, threading.Event) and cancel_event.is_set()):
            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            job["progress"] = {"status": "cancelled", "elapsed_seconds": 0.0}
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        job["updated_at"] = time.time()
        payload = dict(job.get("payload", {}))

    def progress_cb(update: dict[str, Any]) -> None:
        with SQL_QUERY_JOB_LOCK:
            cur = SQL_QUERY_JOBS.get(task_id)
            if not cur:
                return
            cur["progress"] = {
                "status": str(update.get("status", "running") or "running"),
                "elapsed_seconds": float(update.get("elapsed_seconds", 0.0) or 0.0),
            }
            cur["updated_at"] = time.time()

    def register_connection(conn: pymysql.connections.Connection | None) -> None:
        with SQL_QUERY_JOB_LOCK:
            cur = SQL_QUERY_JOBS.get(task_id)
            if not cur:
                return
            cur["db_connection"] = conn

    try:
        start_hostname = str(payload.get("start_hostname", "")).strip()
        max_depth = int(payload.get("max_depth", 3) or 3)
        response = _run_sql_lldp_query(
            start_hostname,
            max_depth,
            db_host=payload.get("db_host"),
            db_port=payload.get("db_port"),
            db_user=payload.get("db_user"),
            db_password=payload.get("db_password"),
            db_name=payload.get("db_name"),
            cancel_event=cancel_event if isinstance(cancel_event, threading.Event) else None,
            progress_cb=progress_cb,
            connection_cb=register_connection,
        )
        with SQL_QUERY_JOB_LOCK:
            cur = SQL_QUERY_JOBS.get(task_id)
            if not cur:
                return
            final_status = "cancelled" if (isinstance(cancel_event, threading.Event) and cancel_event.is_set()) else "completed"
            cur["status"] = final_status
            cur["response"] = response
            cur["error"] = "cancelled_by_user" if final_status == "cancelled" else ""
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()
            cur["db_connection"] = None
            cur["progress"] = {
                "status": final_status,
                "elapsed_seconds": float(response.get("elapsed_seconds", 0.0) or 0.0),
            }
    except Exception as exc:
        with SQL_QUERY_JOB_LOCK:
            cur = SQL_QUERY_JOBS.get(task_id)
            if not cur:
                return
            is_cancelled = (isinstance(cancel_event, threading.Event) and cancel_event.is_set()) or str(exc) == "cancelled_by_user"
            cur["status"] = "cancelled" if is_cancelled else "failed"
            cur["response"] = None
            cur["error"] = "cancelled_by_user" if is_cancelled else str(exc)
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()
            cur["db_connection"] = None
            cur["progress"] = {
                "status": "cancelled" if is_cancelled else "failed",
                "elapsed_seconds": float(time.time() - float(cur.get("started_at") or time.time())),
            }


def _cli_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": job.get("task_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "progress": job.get("progress", {}),
        "error": job.get("error", ""),
        "response": job.get("response"),
    }


def _run_cli_query_task(task_id: str) -> None:
    with CLI_QUERY_JOB_LOCK:
        job = CLI_QUERY_JOBS.get(task_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        job["updated_at"] = time.time()
        payload = dict(job.get("payload", {}))
        cancel_event = job.get("cancel_event")

    def progress_cb(update: dict[str, Any]) -> None:
        with CLI_QUERY_JOB_LOCK:
            cur = CLI_QUERY_JOBS.get(task_id)
            if not cur:
                return
            cur["progress"] = {
                "status": update.get("status"),
                "finished_devices": update.get("finished_devices", 0),
                "total_devices": update.get("total_devices", 0),
                "current_device": update.get("current_device", ""),
                "current_depth": update.get("current_depth", 0),
                "active_workers": update.get("active_workers", 0),
                "pending_devices": update.get("pending_devices", 0),
                "layer_total_devices": update.get("layer_total_devices", 0),
                "layer_finished_devices": update.get("layer_finished_devices", 0),
                "configured_workers": update.get("configured_workers", payload.get("cli_max_workers", 0)),
                "elapsed_seconds": update.get("elapsed_seconds", 0.0),
            }
            cur["updated_at"] = time.time()

    try:
        device_address = normalize_device_id(str(payload.get("device_address", "") or ""))
        max_depth = int(payload.get("max_depth", 3) or 3)
        rows, meta = build_cli_lldp_rows(
            device_address,
            max_depth=max_depth,
            cli_max_workers=payload.get("cli_max_workers"),
            recursive_only_172=bool(payload.get("recursive_only_172", False)),
            device_username=payload.get("device_username"),
            device_password=payload.get("device_password"),
            smc_jump_host=payload.get("smc_jump_host"),
            smc_jump_port=payload.get("smc_jump_port"),
        smc_command=payload.get("smc_command"),
        cli_command_timeout=payload.get("cli_command_timeout"),
        cli_connect_timeout=payload.get("cli_connect_timeout"),
        ping_precheck=bool(payload.get("ping_precheck", False)),
        progress_cb=progress_cb,
        cancel_event=cancel_event if isinstance(cancel_event, threading.Event) else None,
    )
        safe_name, out_path, csv_text = write_rows_to_csv(
            f"lldp_cli_{device_address.replace('/', '_').replace(' ', '_')}_d{max_depth}",
            rows,
        )
        debug_safe_name, debug_path = write_cli_debug_text(
            f"lldp_cli_debug_{device_address.replace('/', '_').replace(' ', '_')}_d{max_depth}",
            meta,
        )
        response = {
            "ok": True,
            "mode": "cli",
            "device_address": device_address,
            "max_depth": max_depth,
            "cli_max_workers": meta.get("cli_max_workers"),
            "recursive_only_172": meta.get("recursive_only_172", False),
            "total_elapsed_seconds": meta.get("total_elapsed_seconds"),
            "vendor_mode": "auto-detect-by-version",
            "detected_vendors": meta.get("detected_vendors", {}),
            "detected_device_names": meta.get("detected_device_names", {}),
            "row_count": len(rows),
            "queried_count": len(meta.get("queried_devices", [])),
            "queried_devices": meta.get("queried_devices", []),
            "failed_devices": meta.get("failed_devices", []),
            "finished_devices": meta.get("finished_devices", 0),
            "total_devices": meta.get("total_devices", 0),
            "cancelled": bool(meta.get("cancelled")),
            "temp_file": str(out_path),
            "download_url": f"/api/lldp-csv/file/{safe_name}",
            "debug_file": str(debug_path),
            "debug_download_url": f"/api/lldp-debug/file/{debug_safe_name}",
            "csv_text": csv_text,
        }
        final_status = "cancelled" if meta.get("cancelled") else "completed"
        with CLI_QUERY_JOB_LOCK:
            cur = CLI_QUERY_JOBS.get(task_id)
            if not cur:
                return
            cur["status"] = final_status
            cur["response"] = response
            cur["error"] = "cancelled_by_user" if final_status == "cancelled" else ""
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()
            cur["progress"] = {
                "status": final_status,
                "finished_devices": meta.get("finished_devices", 0),
                "total_devices": meta.get("total_devices", 0),
                "current_device": "",
                "current_depth": max_depth,
                "active_workers": 0,
                "pending_devices": 0,
                "layer_total_devices": 0,
                "layer_finished_devices": 0,
                "configured_workers": meta.get("cli_max_workers", 0),
                "elapsed_seconds": meta.get("total_elapsed_seconds", 0.0),
            }
    except Exception as exc:
        with CLI_QUERY_JOB_LOCK:
            cur = CLI_QUERY_JOBS.get(task_id)
            if not cur:
                return
            cur["status"] = "failed"
            cur["response"] = None
            cur["error"] = str(exc)
            cur["finished_at"] = time.time()
            cur["updated_at"] = time.time()


def _create_cli_query_task(payload: CliQueryRequest) -> dict[str, Any]:
    _cleanup_expired_jobs(CLI_QUERY_JOBS, CLI_QUERY_JOB_LOCK, CLI_QUERY_JOB_TTL_SEC)
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    task_id = uuid.uuid4().hex
    created = time.time()
    job = {
        "task_id": task_id,
        "status": "queued",
        "payload": body,
        "created_at": created,
        "started_at": 0.0,
        "updated_at": created,
        "finished_at": 0.0,
        "error": "",
        "response": None,
        "progress": {"status": "queued", "finished_devices": 0, "total_devices": 0, "current_device": "", "current_depth": 0, "active_workers": 0, "pending_devices": 0, "layer_total_devices": 0, "layer_finished_devices": 0, "configured_workers": body.get("cli_max_workers", 0), "elapsed_seconds": 0.0},
        "cancel_event": threading.Event(),
    }
    with CLI_QUERY_JOB_LOCK:
        CLI_QUERY_JOBS[task_id] = job
    t = threading.Thread(target=_run_cli_query_task, args=(task_id,), daemon=True)
    t.start()
    return {"task_id": task_id, "status": "queued"}


def _create_sql_query_task(payload: QueryRequest) -> dict[str, Any]:
    _cleanup_expired_jobs(SQL_QUERY_JOBS, SQL_QUERY_JOB_LOCK, SQL_QUERY_JOB_TTL_SEC)
    body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    task_id = uuid.uuid4().hex
    created = time.time()
    job = {
        "task_id": task_id,
        "status": "queued",
        "payload": body,
        "created_at": created,
        "started_at": 0.0,
        "updated_at": created,
        "finished_at": 0.0,
        "progress": {"status": "queued", "elapsed_seconds": 0.0},
        "cancel_event": threading.Event(),
        "db_connection": None,
        "error": "",
        "response": None,
    }
    with SQL_QUERY_JOB_LOCK:
        SQL_QUERY_JOBS[task_id] = job
    t = threading.Thread(target=_run_sql_query_task, args=(task_id,), daemon=True)
    t.start()
    return {"task_id": task_id, "status": "queued"}


@app.post("/api/sql/lldp-csv/tasks")
def create_sql_lldp_task(payload: QueryRequest) -> dict[str, Any]:
    start_hostname = payload.start_hostname.strip()
    if not start_hostname:
        raise HTTPException(status_code=400, detail="start_hostname is required")
    try:
        task = _create_sql_query_task(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create SQL task failed: {exc}") from exc
    return {"ok": True, "task_id": task["task_id"], "status": task["status"]}


@app.get("/api/sql/config-defaults")
def get_sql_config_defaults() -> dict[str, Any]:
    cfg = current_db_config_defaults()
    return {
        "ok": True,
        **cfg,
    }


@app.get("/api/zabbix/config")
def get_zabbix_config() -> dict[str, Any]:
    return {"ok": True, **_effective_zabbix_config()}


@app.post("/api/zabbix/config")
def save_zabbix_config(payload: ZabbixConfigSaveRequest) -> dict[str, Any]:
    item = _save_zabbix_config(payload)
    return {"ok": True, **item, "source": "file"}


@app.delete("/api/zabbix/config")
def delete_zabbix_config() -> dict[str, Any]:
    _delete_zabbix_config()
    return {"ok": True}


@app.get("/api/sql/lldp-csv/tasks/{task_id}")
def get_sql_lldp_task(task_id: str) -> dict[str, Any]:
    _cleanup_expired_jobs(SQL_QUERY_JOBS, SQL_QUERY_JOB_LOCK, SQL_QUERY_JOB_TTL_SEC)
    with SQL_QUERY_JOB_LOCK:
        job = SQL_QUERY_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        body = _sql_job_public(job)
    body["ok"] = True
    return body


@app.post("/api/sql/lldp-csv/tasks/{task_id}/cancel")
def cancel_sql_lldp_task(task_id: str) -> dict[str, Any]:
    with SQL_QUERY_JOB_LOCK:
        job = SQL_QUERY_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        status = str(job.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            return {"ok": True, "task_id": task_id, "status": status}
        ev = job.get("cancel_event")
        if isinstance(ev, threading.Event):
            ev.set()
        conn = job.get("db_connection")
        if status == "queued":
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            job["error"] = "cancelled_by_user"
            job["progress"] = {"status": "cancelled", "elapsed_seconds": 0.0}
        else:
            job["status"] = "cancelling"
            job["updated_at"] = time.time()
            job["progress"] = {
                "status": "cancelling",
                "elapsed_seconds": float(time.time() - float(job.get("started_at") or time.time())),
            }
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    return {"ok": True, "task_id": task_id, "status": "cancelling"}


@app.post("/api/cli/lldp-csv/tasks")
def create_cli_lldp_task(payload: CliQueryRequest) -> dict[str, Any]:
    device_address = normalize_device_id(payload.device_address)
    if not device_address:
        raise HTTPException(status_code=400, detail="device_address is required")
    try:
        task = _create_cli_query_task(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create CLI task failed: {exc}") from exc
    return {"ok": True, "task_id": task["task_id"], "status": task["status"]}


@app.get("/api/cli/lldp-csv/tasks/{task_id}")
def get_cli_lldp_task(task_id: str) -> dict[str, Any]:
    _cleanup_expired_jobs(CLI_QUERY_JOBS, CLI_QUERY_JOB_LOCK, CLI_QUERY_JOB_TTL_SEC)
    with CLI_QUERY_JOB_LOCK:
        job = CLI_QUERY_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        body = _cli_job_public(job)
    body["ok"] = True
    return body


@app.post("/api/cli/lldp-csv/tasks/{task_id}/cancel")
def cancel_cli_lldp_task(task_id: str) -> dict[str, Any]:
    with CLI_QUERY_JOB_LOCK:
        job = CLI_QUERY_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        status = str(job.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            return {"ok": True, "task_id": task_id, "status": status}
        ev = job.get("cancel_event")
        if isinstance(ev, threading.Event):
            ev.set()
        else:
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            job["error"] = "cancelled_by_user"
    return {"ok": True, "task_id": task_id, "status": "cancelling"}


@app.post("/api/sql/lldp-csv")
def query_lldp_csv(payload: QueryRequest) -> dict[str, Any]:
    start_hostname = payload.start_hostname.strip()
    max_depth = int(payload.max_depth)
    if not start_hostname:
        raise HTTPException(status_code=400, detail="start_hostname is required")
    try:
        return _run_sql_lldp_query(start_hostname, max_depth)
    except Exception as exc:
        msg = str(exc)
        if msg.startswith("DB connect failed:"):
            raise HTTPException(status_code=500, detail=msg) from exc
        if msg.startswith("SQL execution failed"):
            raise HTTPException(status_code=500, detail=msg) from exc
        raise HTTPException(status_code=500, detail=f"SQL query failed: {msg}") from exc


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
            recursive_only_172=payload.recursive_only_172,
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
        "recursive_only_172": meta.get("recursive_only_172", False),
        "total_elapsed_seconds": meta.get("total_elapsed_seconds"),
        "vendor_mode": "auto-detect-by-version",
        "detected_vendors": meta.get("detected_vendors", {}),
        "detected_device_names": meta.get("detected_device_names", {}),
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


@app.post("/api/ndmp/lldp-csv")
def query_lldp_csv_via_ndmp(payload: NdmpQueryRequest) -> dict[str, Any]:
    mode_raw = str(payload.query_mode or "auto").strip().lower()
    if mode_raw in {"hostname", "device_hostname"}:
        query_mode = "device_hostname"
    elif mode_raw in {"ip", "device_ip"}:
        query_mode = "device_ip"
    else:
        query_mode = "auto"

    host = normalize_device_id(payload.device_hostname or "")
    ip = normalize_device_id(payload.device_ip or "")
    if query_mode == "device_hostname":
        query_value = host or ip
    elif query_mode == "device_ip":
        query_value = ip or host
    else:
        query_value = ip if _looks_like_ip(ip) else host or ip
    query_value = normalize_device_id(query_value)
    if not query_value:
        raise HTTPException(status_code=400, detail="device_hostname or device_ip is required")

    try:
        return _run_ndmp_lldp_query(
            query_value=query_value,
            query_mode=query_mode,
            max_depth=int(payload.max_depth or 1),
            only_link_up=bool(payload.only_link_up),
            timeout_seconds=int(payload.timeout_seconds or 20),
            ndmp_url_override=payload.ndmp_url,
            verify_ssl_override=payload.verify_ssl,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"NDMP query failed: {exc}") from exc


@app.post("/api/ndmp/device-details")
def query_device_details_via_ndmp(payload: DeviceDetailsRequest) -> dict[str, Any]:
    try:
        return query_ndmp_device_details(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"NDMP device detail query failed: {exc}") from exc


@app.post("/api/cli/link-utilization/tasks")
def create_link_utilization_task(payload: LinkUtilRequest) -> dict[str, Any]:
    if not payload.targets:
        raise HTTPException(status_code=400, detail="targets is required")
    metric = (payload.metric or "tx").strip().lower()
    if metric not in {"tx", "rx", "min", "max"}:
        payload.metric = "tx"
    try:
        task = _create_link_utilization_task(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create link utilization task failed: {exc}") from exc
    return {
        "ok": True,
        "task_id": task["task_id"],
        "status": task["status"],
    }


@app.get("/api/cli/link-utilization/tasks/{task_id}")
def get_link_utilization_task(task_id: str) -> dict[str, Any]:
    _cleanup_expired_jobs(LINK_UTIL_JOBS, LINK_UTIL_JOB_LOCK, LINK_UTIL_JOB_TTL_SEC)
    with LINK_UTIL_JOB_LOCK:
        job = LINK_UTIL_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        body = _link_util_job_public(job)
    body["ok"] = True
    return body


@app.post("/api/cli/link-utilization/tasks/{task_id}/cancel")
def cancel_link_utilization_task(task_id: str) -> dict[str, Any]:
    with LINK_UTIL_JOB_LOCK:
        job = LINK_UTIL_JOBS.get(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="task not found")
        status = str(job.get("status", ""))
        if status in {"completed", "failed", "cancelled"}:
            return {"ok": True, "task_id": task_id, "status": status}
        ev = job.get("cancel_event")
        if isinstance(ev, threading.Event):
            ev.set()
        if status == "queued":
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            job["updated_at"] = time.time()
            job["error"] = "cancelled_by_user"
    return {"ok": True, "task_id": task_id, "status": "cancelling"}


@app.post("/api/cli/link-utilization")
def query_link_utilization(payload: LinkUtilRequest) -> dict[str, Any]:
    if not payload.targets:
        raise HTTPException(status_code=400, detail="targets is required")
    metric = (payload.metric or "tx").strip().lower()
    if metric not in {"tx", "rx", "min", "max"}:
        metric = "tx"
    try:
        meta = collect_link_utilization(
            payload.targets,
            metric=metric,
            cli_max_workers=payload.cli_max_workers,
            device_username=payload.device_username,
            device_password=payload.device_password,
            smc_jump_host=payload.smc_jump_host,
            smc_jump_port=payload.smc_jump_port,
            smc_command=payload.smc_command,
            cli_command_timeout=payload.cli_command_timeout,
            cli_connect_timeout=payload.cli_connect_timeout,
            debug_enabled=payload.debug_enabled,
        )
    except Exception as exc:
        msg = str(exc)
        if (
            "Missing CLI device credentials" in msg
            or "Missing SMC_COMMAND" in msg
            or "config error" in msg.lower()
        ):
            raise HTTPException(status_code=400, detail=f"CLI link utilization config error: {msg}") from exc
        raise HTTPException(status_code=500, detail=f"CLI link utilization failed: {msg}") from exc

    cache_updates = merge_link_util_cache(meta.get("results", []))
    cache_total = len(read_link_util_cache_rows())

    debug_download_url = ""
    debug_file = ""
    if payload.debug_enabled:
        safe_name, debug_path = write_link_util_debug_text(
            f"link_util_debug_{int(time.time())}",
            {**meta, "metric": metric},
        )
        debug_file = str(debug_path)
        debug_download_url = f"/api/lldp-debug/file/{safe_name}"

    return {
        "ok": True,
        "mode": "cli-link-utilization",
        "metric": metric,
        "result_count": len(meta.get("results", [])),
        "results": meta.get("results", []),
        "queried_devices": meta.get("queried_devices", []),
        "cli_max_workers": meta.get("cli_max_workers"),
        "per_device_timeout": meta.get("per_device_timeout"),
        "total_elapsed_seconds": meta.get("total_elapsed_seconds"),
        "debug_entries": meta.get("debug_entries", []),
        "device_timings": meta.get("device_timings", []),
        "finished_devices": meta.get("finished_devices"),
        "total_devices": meta.get("total_devices"),
        "cache_updates": cache_updates,
        "cache_total": cache_total,
        "debug_file": debug_file,
        "debug_download_url": debug_download_url,
    }


@app.post("/api/zabbix/link-utilization")
def query_zabbix_link_utilization(payload: ZabbixLinkUtilRequest) -> dict[str, Any]:
    if not payload.targets:
        raise HTTPException(status_code=400, detail="targets is required")
    metric = (payload.metric or "max").strip().lower()
    if metric not in {"tx", "rx", "min", "max"}:
        metric = "max"
    time_mode = (payload.time_mode or "current").strip().lower()
    if time_mode not in {"current", "range_max", "range_min"}:
        time_mode = "current"
    try:
        meta = collect_zabbix_link_utilization(
            payload.targets,
            metric=metric,
            time_mode=time_mode,
            time_from=payload.time_from,
            time_till=payload.time_till,
            zabbix_url=payload.zabbix_url,
            zabbix_api_token=payload.zabbix_api_token,
            zabbix_verify_ssl=payload.zabbix_verify_ssl,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Zabbix link utilization failed: {exc}") from exc
    cache_updates = merge_link_util_cache(meta.get("results", [])) if time_mode == "current" else 0
    cache_total = len(read_link_util_cache_rows())
    return {
        "ok": True,
        "mode": "zabbix-link-utilization",
        "metric": metric,
        "time_mode": time_mode,
        "time_from": meta.get("time_from"),
        "time_till": meta.get("time_till"),
        "result_count": len(meta.get("results", [])),
        "results": meta.get("results", []),
        "queried_devices": meta.get("queried_devices", []),
        "total_elapsed_seconds": meta.get("total_elapsed_seconds"),
        "cache_updates": cache_updates,
        "cache_total": cache_total,
    }


@app.get("/api/cli/link-utilization-cache")
def get_link_utilization_cache() -> dict[str, Any]:
    rows = read_link_util_cache_rows()
    return {
        "ok": True,
        "row_count": len(rows),
        "rows": rows,
    }


@app.get("/api/cli/link-utilization-cache/file")
def download_link_utilization_cache():
    if not LINK_UTIL_CACHE_FILE.exists():
        with LINK_UTIL_CACHE_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LINK_UTIL_CACHE_FIELDS, extrasaction="ignore")
            writer.writeheader()
    return FileResponse(LINK_UTIL_CACHE_FILE, media_type="text/csv", filename=LINK_UTIL_CACHE_FILE.name)


@app.post("/api/cli/link-utilization-cache/clear")
def clear_link_utilization_cache() -> dict[str, Any]:
    cleared = clear_link_util_cache()
    cache_total = len(read_link_util_cache_rows())
    return {
        "ok": True,
        "cleared": cleared,
        "cache_total": cache_total,
    }


@app.post("/api/admin/cleanup-temp-data")
def cleanup_temp_data() -> dict[str, Any]:
    try:
        stats = cleanup_temp_runtime_data()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"cleanup temp data failed: {exc}") from exc
    return {"ok": True, **stats}


@app.post("/api/state-snapshots")
def create_state_snapshot(payload: StateSnapshotSaveRequest) -> dict[str, Any]:
    try:
        item = save_state_snapshot(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"save state snapshot failed: {exc}") from exc
    return {"ok": True, **item}


@app.get("/api/state-snapshots")
def get_state_snapshots() -> dict[str, Any]:
    try:
        items = list_state_snapshots()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list state snapshots failed: {exc}") from exc
    return {"ok": True, "items": items}


@app.get("/api/state-snapshots/{snapshot_id}")
def get_state_snapshot(snapshot_id: str) -> dict[str, Any]:
    return load_state_snapshot(snapshot_id)


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


@app.get("/lldp.html")
def serve_lldp_html():
    page = BASE_DIR / "lldp.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="lldp.html not found")
    response = FileResponse(page, media_type="text/html")
    response.headers["X-LLDP-Route"] = "serve_lldp_html"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


ACTIVE_SHARED_DIR = SHARED_DIR if SHARED_DIR.is_dir() else LOCAL_SHARED_DIR
if ACTIVE_SHARED_DIR.is_dir():
    app.mount("/shared", StaticFiles(directory=str(ACTIVE_SHARED_DIR), html=False), name="shared")
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("lldp_sql_service:app", host="127.0.0.1", port=18080, reload=False)
