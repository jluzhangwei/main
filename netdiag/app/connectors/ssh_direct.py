from __future__ import annotations

import asyncio
import os
import pty
import re
import select
import signal
import subprocess
import time
from typing import Callable

from .smc_patterns import ANSI_PATTERN, FAIL_PATTERN, PASSWORD_PATTERN, PROMPT_PATTERN, YES_PATTERN

MORE_PATTERN = re.compile(r"(--more--|----\s*more\s*----|<---\s*more\s*--->|press any key)", re.IGNORECASE)


class DirectSSHClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = 30,
        debug: Callable[[str], None] | None = None,
    ) -> None:
        self.host = str(host or "").strip()
        self.port = int(port or 22)
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.timeout = int(timeout or 30)
        self._debug = debug
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self._connected = False

    def _dbg(self, message: str) -> None:
        if self._debug:
            safe_message = message
            if self.password:
                safe_message = safe_message.replace(self.password, "***")
            self._debug(safe_message)

    def _clean_ansi(self, text: str) -> str:
        return ANSI_PATTERN.sub("", text).replace("\r", "")

    def _send(self, text: str, mask: bool = False) -> None:
        if self.master_fd is None:
            raise RuntimeError("direct ssh PTY not initialized")
        self._dbg(f"[SEND] {'***' if mask else text.rstrip()}")
        os.write(self.master_fd, text.encode("utf-8", errors="ignore"))

    def _read_for(self, timeout: float) -> str:
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

    def _read_until_prompt(self, timeout: int = 30) -> str:
        chunks: list[str] = []
        deadline = time.time() + max(1, int(timeout))
        last_recv_ts = time.time()
        while time.time() < deadline:
            piece = self._read_for(0.35)
            if piece:
                chunks.append(piece)
                last_recv_ts = time.time()
                tail = self._clean_ansi("".join(chunks)[-4000:])
                if MORE_PATTERN.search(tail):
                    self._send(" ")
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
        normalized = self._clean_ansi(raw_output).replace("\x08", "")
        lines = normalized.splitlines()
        cleaned: list[str] = []
        command_skipped = False
        command_norm = command.strip()
        for line in lines:
            stripped = line.strip()
            if not command_skipped and stripped == command_norm:
                command_skipped = True
                continue
            if PROMPT_PATTERN.match(stripped):
                continue
            if MORE_PATTERN.search(stripped):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _connect_sync(self) -> None:
        if os.name == "nt":
            raise RuntimeError("Direct SSH shell mode is currently supported on POSIX systems only")
        if self.master_fd is not None or self.proc is not None:
            return
        ssh_cmd = (
            "ssh "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o ServerAliveInterval=30 "
            f"-p {self.port} {self.username}@{self.host}"
        )
        self._dbg(f"[LOGIN] connect {self.username}@{self.host}:{self.port}")

        master_fd, slave_fd = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                ["bash", "-lc", ssh_cmd],
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

        deadline = time.time() + max(20, self.timeout * 3)
        buffer = ""
        password_attempts = 0
        max_password_attempts = 3
        while time.time() < deadline:
            chunk = self._read_for(1.0)
            if not chunk:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError("SSH connection closed")
                continue
            buffer += chunk
            if len(buffer) > 20000:
                buffer = buffer[-20000:]
            normalized = self._clean_ansi(buffer)

            if FAIL_PATTERN.search(normalized):
                raise RuntimeError("direct ssh login failed")
            if YES_PATTERN.search(normalized):
                self._send("yes\n")
                buffer = ""
                continue
            if PASSWORD_PATTERN.search(normalized):
                if password_attempts < max_password_attempts:
                    password_attempts += 1
                    self._dbg(f"[LOGIN] password prompt detected, attempt={password_attempts}")
                    self._send(self.password + "\n", mask=True)
                    buffer = ""
                    continue
                raise RuntimeError("direct ssh auth failed: password prompt repeated too many times")
            tail = normalized[-4000:]
            if PROMPT_PATTERN.search(tail):
                self._connected = True
                self._dbg("[LOGIN] connected")
                break
        if not self._connected:
            raise RuntimeError("direct ssh login timeout")

    def _exec_sync(self, command: str, timeout: int = 30) -> str:
        if not self._connected or self.master_fd is None:
            raise RuntimeError("SSH connection is not established")
        self._dbg(f"[CMD] {command}")
        self._send(command + "\n")
        raw_output = self._read_until_prompt(timeout=timeout)
        output = self._clean_shell_output(raw_output, command)
        if output:
            self._dbg(f"[OUT]\n{output}\n")
        else:
            self._dbg("[OUT] <empty>")
        return output

    def _close_sync(self) -> None:
        self._connected = False
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
        self._dbg("[LOGIN] connection closed")

    async def connect(self) -> None:
        await asyncio.to_thread(self._connect_sync)

    async def exec(self, command: str, timeout: int = 30) -> str:
        return await asyncio.to_thread(self._exec_sync, command, timeout)

    async def try_disable_paging(self, vendor: str) -> None:
        cmds = ["terminal length 0"]
        if vendor == "huawei":
            cmds = ["screen-length 0 temporary", "screen-length 0"]
        for cmd in cmds:
            try:
                await self.exec(cmd, timeout=10)
            except Exception:
                continue

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)
