from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from ..parsers.time_parser import extract_device_name_from_prompt
from .smc_patterns import (
    ANSI_PATTERN,
    FAIL_PATTERN,
    JUMP_PROMPT_PATTERN,
    PASSWORD_PATTERN,
    PROMPT_PATTERN,
    TOKEN_RETRY_PATTERN,
    YES_PATTERN,
)


@dataclass
class SmcShellConfig:
    smc_command: str
    device_ip: str
    username: str
    password: str
    device_port: int = 22
    jump_host: str | None = None
    jump_port: int = 22
    timeout: int = 60
    debug: Callable[[str], None] | None = None
    session_mode: str = "jump_shell"


class SmcShellClient:
    def __init__(self, config: SmcShellConfig) -> None:
        self.config = config
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.smc_mode_active = False
        self.device_prompt: str | None = None

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

    def _capture_prompt(self, text: str) -> None:
        normalized = self._clean_ansi(text or "")
        matches = PROMPT_PATTERN.findall(normalized)
        if matches:
            self.device_prompt = str(matches[-1]).strip()

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

    def _format_smc_command(self, template: str) -> str:
        raw_template = str(template or "").strip()
        cmd = raw_template
        cmd = cmd.replace("{jump_host}", str(self.config.jump_host or ""))
        cmd = cmd.replace("{jump_port}", str(self.config.jump_port or 22))
        cmd = cmd.replace("{device_ip}", str(self.config.device_ip or ""))
        cmd = cmd.replace("{device_port}", str(self.config.device_port or 22))
        cmd = cmd.replace("{username}", str(self.config.username or ""))
        if self.config.session_mode == "jump_shell":
            jump_host = str(self.config.jump_host or "").strip()
            if "{jump_host}" not in raw_template and jump_host and jump_host not in cmd:
                cmd = f"{cmd} {jump_host}".strip()
        elif self.config.session_mode == "pam_nd_direct":
            device_ip = str(self.config.device_ip or "").strip()
            if "{device_ip}" not in raw_template and device_ip and device_ip not in cmd:
                cmd = f"{cmd} {device_ip}".strip()
        return cmd.strip()

    def _start_pty_command(self, cmd: str) -> None:
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.proc:
            try:
                if self.proc.poll() is None:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    try:
                        self.proc.wait(timeout=2)
                    except Exception:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass
            self.proc = None

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

    def _connect_via_jump_shell(self) -> None:
        if os.name == "nt":
            raise RuntimeError("SMC mode is currently supported on POSIX systems only")

        cmd = self._format_smc_command(str(self.config.smc_command).strip() or "smc server toc {jump_host}")
        self._dbg(f"[SMC] start jump command: {cmd}")
        self._start_pty_command(cmd)

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

    def _connect_via_pam_nd_command(self) -> None:
        if os.name == "nt":
            raise RuntimeError("SMC mode is currently supported on POSIX systems only")

        base_cmd = self._format_smc_command(str(self.config.smc_command).strip() or "smc pam nd ssh {device_ip}")
        cmd = base_cmd
        used_skip_deviceauth = False
        password_attempts = 0
        max_password_attempts = 3

        while True:
            self._dbg(f"[SMC] start pam/nd command: {cmd}")
            self._start_pty_command(cmd)
            deadline = time.time() + max(25, self.config.timeout * 3)
            buffer = ""
            password_attempts = 0
            while time.time() < deadline:
                chunk = self._smc_read_for(1.0)
                if not chunk:
                    continue
                buffer += chunk
                if len(buffer) > 30000:
                    buffer = buffer[-30000:]
                normalized = self._clean_ansi(buffer)
                norm_low = normalized.lower()
                tail = normalized[-5000:]

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
                    raise RuntimeError("SMC PAM/ND auth failed: password prompt repeated too many times")
                if "context deadline exceeded" in norm_low and "--skip-deviceauth" in norm_low and not used_skip_deviceauth:
                    used_skip_deviceauth = True
                    cmd = base_cmd if "--skip-deviceauth" in base_cmd else f"{base_cmd} --skip-deviceauth"
                    self._dbg("[SMC] pam/nd retry with --skip-deviceauth")
                    break
                if "authentication failed" in norm_low:
                    raise RuntimeError("SMC PAM/ND auth failed")
                if "received disconnect" in norm_low or "connection is closed by ssh server" in norm_low:
                    raise RuntimeError("SMC PAM/ND auth failed: server disconnected session")
                if FAIL_PATTERN.search(normalized):
                    raise RuntimeError("SMC PAM/ND command failed before device prompt")
                if PROMPT_PATTERN.search(tail):
                    self.smc_mode_active = True
                    self._capture_prompt(tail)
                    self._dbg("[LOGIN] target device prompt reached via pam/nd")
                    return
            else:
                raise RuntimeError("SMC PAM/ND timeout: cannot reach target device prompt")

            # Restart loop only for one-time deviceauth bypass fallback.
            if used_skip_deviceauth and cmd.endswith("--skip-deviceauth"):
                continue

    def connect(self) -> None:
        if self.config.session_mode == "pam_nd_direct":
            self._connect_via_pam_nd_command()
        else:
            self._connect_via_jump_shell()
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
                norm_low = normalized.lower()

                if FAIL_PATTERN.search(normalized):
                    raise RuntimeError("SMC jump login failed while ssh to target device")
                if "authentication failed" in norm_low:
                    raise RuntimeError("SMC auth failed: invalid username/password")
                if "received disconnect" in norm_low or "connection is closed by ssh server" in norm_low:
                    raise RuntimeError("SMC auth failed: server disconnected session")
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
                    self._capture_prompt(tail)
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
                self._smc_send(f"{pager_cmd}\n")
                self._smc_read_until_prompt_via_smc(timeout=1)
            except Exception:
                continue

    def _execute_command_via_smc(self, cmd: str, timeout: int = 30) -> str:
        if not self.smc_mode_active:
            raise RuntimeError("SMC mode is not active")
        self._smc_send(cmd + "\n")
        raw_output = self._smc_read_until_prompt_via_smc(timeout=timeout)
        self._capture_prompt(raw_output)
        return self._clean_shell_output(raw_output, cmd)

    def exec(self, cmd: str, timeout: int = 30) -> str:
        return self._execute_command_via_smc(cmd, timeout=timeout)

    def try_disable_paging(self, vendor: str) -> None:
        cmds = ["terminal length 0"]
        if vendor == "huawei":
            cmds = ["screen-length 0 temporary"]
        for cmd in cmds:
            try:
                self.exec(cmd, timeout=10)
            except Exception:
                continue

    def get_device_name(self) -> str | None:
        return extract_device_name_from_prompt(self.device_prompt or "")

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
