from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditWriter
from .policy import ALLOW_PREFIXES, COMPOUND_PATTERNS, DANGEROUS_PATTERNS, CommandPolicy, PolicyDecision


PROMPT_PATTERN = re.compile(
    r"(?m)(^|\n).{0,120}([>#]\s*|\$\s*|<[^>\r\n]{1,80}>\s*|\[[^\]\r\n]{1,80}\]\s*)$"
)
RAW_MODE_COMMANDS = {"vi", "vim", "less", "more", "top", "htop", "nano", "man"}
_DANGEROUS_WORDS = {
    "bash",
    "commit",
    "conf",
    "config",
    "configure",
    "del",
    "delete",
    "erase",
    "format",
    "lua",
    "perl",
    "python",
    "reboot",
    "reload",
    "restart",
    "rm",
    "ruby",
    "save",
    "sh",
    "shut",
    "shutdown",
    "su",
    "sudo",
    "system-view",
    "write",
}


@dataclass
class RunnerConfig:
    command: list[str]
    audit_dir: Path | None = None
    block_message_prefix: str = "Blocked by SSH proxy policy"


class PtyProxyRunner:
    def __init__(self, config: RunnerConfig) -> None:
        if not config.command:
            raise ValueError("login command is required")
        self.config = config
        self.policy = CommandPolicy()
        self.audit = AuditWriter(config.audit_dir, config.command)
        self.master_fd: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.filtering_active = False
        self.raw_bypass = False
        self._stdin_buffer = bytearray()
        self._remote_line_active = False
        self._drop_next_enter = False
        self._output_tail = ""
        self._suppress_echo: str | None = None
        self._old_term_attrs: list[int | bytes] | None = None

    def run(self) -> int:
        master_fd, slave_fd = pty.openpty()
        self._prepare_slave_pty(slave_fd)
        self.master_fd = master_fd
        try:
            self.proc = subprocess.Popen(
                self.config.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )
        finally:
            os.close(slave_fd)

        self.audit.event("child_start", {"pid": self.proc.pid, "argv": self.config.command})
        self._install_signal_handlers()
        exit_code: int | None = None
        try:
            self._enter_raw_terminal()
            exit_code = self._loop()
            return int(exit_code or 0)
        finally:
            self._restore_terminal()
            self._close_child()
            if exit_code is None and self.proc is not None:
                exit_code = self.proc.poll()
            self.audit.close(exit_code)
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None

    def _loop(self) -> int | None:
        assert self.master_fd is not None
        assert self.proc is not None
        stdin_fd = sys.stdin.fileno()
        stdin_open = True
        while True:
            if self.proc.poll() is not None:
                self._drain_child()
                return self.proc.returncode

            read_fds = [self.master_fd]
            if stdin_open:
                read_fds.append(stdin_fd)
            readable, _, _ = select.select(read_fds, [], [], 0.1)
            if self.master_fd in readable:
                self._handle_child_output()
            if stdin_open and stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if not data:
                    stdin_open = False
                    self.audit.event("stdin_closed", {})
                    continue
                self._handle_user_input(data)

    def _handle_child_output(self) -> bool:
        assert self.master_fd is not None
        try:
            data = os.read(self.master_fd, 65535)
        except OSError:
            return False
        if not data:
            return False
        text = data.decode("utf-8", errors="replace")
        self.policy.observe_output(text)
        self._output_tail = (self._output_tail + text)[-4000:]
        if not self.filtering_active and PROMPT_PATTERN.search(_strip_ansi(self._output_tail)):
            self.filtering_active = True
            self.audit.event("filtering_active", {"reason": "prompt_detected"})

        display = self._strip_allowed_echo(text)
        if display:
            os.write(sys.stdout.fileno(), display.encode("utf-8", errors="replace"))
            self.audit.replay("stdout", display)
        return True

    def _handle_user_input(self, data: bytes) -> None:
        if not self.filtering_active or self.raw_bypass:
            self._send_to_child(data)
            self.audit.replay("stdin", data)
            return

        for b in data:
            if b in (3, 4, 26):  # Ctrl-C, Ctrl-D, Ctrl-Z
                self._stdin_buffer.clear()
                self._remote_line_active = False
                self._send_to_child(bytes([b]))
                self.audit.replay("stdin_control", f"0x{b:02x}")
                continue
            if b in (10, 13):
                if self._drop_next_enter and not self._stdin_buffer:
                    self._drop_next_enter = False
                    continue
                self._drop_next_enter = False
                command = self._stdin_buffer.decode("utf-8", errors="replace")
                self._stdin_buffer.clear()
                self._submit_command(command, remote_line_active=self._remote_line_active)
                self._remote_line_active = False
                continue
            if b in (8, 127):
                if self._stdin_buffer:
                    self._stdin_buffer = self._stdin_buffer[:-1]
                    self._send_to_child(bytes([b]))
                    self.audit.replay("stdin", bytes([b]))
                continue
            if b == 9:
                self._handle_tab_key()
                continue
            if b == 32:
                self._handle_realtime_key(bytes([b]), "space")
                continue
            if b == 63:  # '?' asks network devices for contextual help without Enter.
                self._handle_realtime_key(bytes([b]), "question-mark")
                continue
            if b >= 32:
                self._handle_realtime_key(bytes([b]), "printable")

    def _handle_tab_key(self) -> None:
        completion = self._local_tab_completion()
        if completion:
            decision = self._decide_realtime(
                (bytes(self._stdin_buffer) + completion).decode("utf-8", errors="replace"),
                boundary_key=True,
            )
            self.audit.event(
                "assist_key",
                {
                    "key": "tab",
                    "command_prefix": self._stdin_buffer.decode("utf-8", errors="replace"),
                    "local_completion": completion.decode("utf-8", errors="replace"),
                    "decision": decision.__dict__,
                },
            )
            if not decision.allowed:
                message = f"{self.config.block_message_prefix}: {decision.reason} [{decision.rule}]\r\n"
                self._echo(message.encode("utf-8"))
                return
            self._stdin_buffer.extend(completion)
            self._send_to_child(completion)
            self.audit.replay("stdin", completion)
            self._remote_line_active = True
            return
        self._handle_realtime_key(b"\t", "tab")

    def _local_tab_completion(self) -> bytes:
        line = self._stdin_buffer.decode("utf-8", errors="replace")
        if not line or line.endswith((" ", "\t")):
            return b""
        if any(sep in line for sep in (";", "&", "|", "`", "$(")):
            return b""
        words = line.split()
        if len(words) != 1:
            return b""
        token = words[0].lower()
        candidates = [
            prefix
            for prefix in ALLOW_PREFIXES
            if prefix != "?" and prefix.startswith(token) and prefix != token
        ]
        if len(candidates) != 1:
            return b""
        suffix = candidates[0][len(token) :]
        return (suffix + " ").encode("utf-8")

    def _handle_realtime_key(self, key: bytes, key_name: str) -> None:
        before = self._stdin_buffer.decode("utf-8", errors="replace")
        keep_in_buffer = key_name not in {"question-mark", "tab"}
        after_bytes = bytes(self._stdin_buffer) + (key if keep_in_buffer else b"")
        after = after_bytes.decode("utf-8", errors="replace")
        boundary_key = key_name in {"space", "question-mark", "tab"}
        decision = self._decide_realtime(after if keep_in_buffer else before, boundary_key=boundary_key)
        self.audit.event(
            "assist_key",
            {
                "key": key_name,
                "command_prefix": before,
                "command_after_key": after if keep_in_buffer else before,
                "decision": decision.__dict__,
            },
        )
        if not decision.allowed:
            if self._remote_line_active:
                self._send_to_child(b"\x15")
                self.audit.replay("stdin_control", "0x15")
            self._stdin_buffer.clear()
            self._remote_line_active = False
            self._drop_next_enter = True
            message = f"{self.config.block_message_prefix}: {decision.reason} [{decision.rule}]\r\n"
            self._echo(message.encode("utf-8"))
            self.audit.event("command_blocked", {"command": after, "decision": decision.__dict__})
            return

        if keep_in_buffer:
            self._stdin_buffer = bytearray(after_bytes)
        self._send_to_child(key)
        self.audit.replay("stdin", key)
        self._remote_line_active = True

    def _submit_command(self, command: str, remote_line_active: bool = False) -> None:
        stripped = command.strip()
        decision = self.policy.decide(command)
        self.audit.command(command, decision)
        if not decision.allowed:
            if remote_line_active:
                self._send_to_child(b"\x15")  # Ctrl-U clears the remote line on common CLIs.
                self.audit.replay("stdin_control", "0x15")
            else:
                self._echo(b"\r\n")
            message = f"{self.config.block_message_prefix}: {decision.reason} [{decision.rule}]\r\n"
            self._echo(message.encode("utf-8"))
            self.audit.event("command_blocked", {"command": command, "decision": decision.__dict__})
            return

        if _first_word(stripped).lower() in RAW_MODE_COMMANDS:
            self.raw_bypass = True
            self.audit.event("raw_bypass_active", {"command": stripped})

        if remote_line_active:
            self._send_to_child(b"\n")
            self.audit.replay("stdin", "\n")
        else:
            self._echo(b"\r\n")
            self._suppress_echo = stripped if stripped else None
            self._send_to_child((command + "\n").encode("utf-8", errors="replace"))

    def _send_to_child(self, data: bytes) -> None:
        assert self.master_fd is not None
        try:
            os.write(self.master_fd, data)
        except OSError:
            return

    def _echo(self, data: bytes) -> None:
        os.write(sys.stdout.fileno(), data)
        self.audit.replay("stdin_echo", data)

    def _strip_allowed_echo(self, text: str) -> str:
        if not self._suppress_echo:
            return text
        normalized = text.replace("\r", "")
        target = self._suppress_echo
        if normalized.startswith(target + "\n"):
            self._suppress_echo = None
            return normalized[len(target) + 1 :]
        if normalized.strip() == target:
            self._suppress_echo = None
            return ""
        return text

    def _decide_realtime(self, command: str, boundary_key: bool = False) -> PolicyDecision:
        text = command.strip().lower()
        if not text:
            return PolicyDecision(True, command, "realtime input allowed on empty line", "realtime-empty")
        for rule, pattern in COMPOUND_PATTERNS:
            if pattern.search(command):
                return PolicyDecision(False, command, f"realtime input blocked for compound expression ({rule})", rule)
        if any(
            prefix.startswith(text) or text == prefix or text.startswith(prefix + " ")
            for prefix in ALLOW_PREFIXES
            if prefix != "?"
        ):
            return PolicyDecision(True, command, "realtime input allowed for read-only prefix", "realtime-allow-prefix")
        if boundary_key:
            for rule, pattern in DANGEROUS_PATTERNS:
                if pattern.search(command):
                    return PolicyDecision(False, command, f"realtime input blocked for dangerous command ({rule})", rule)
            first = _first_word(text)
            if first in _DANGEROUS_WORDS:
                return PolicyDecision(False, command, "realtime input blocked at command boundary", "realtime-danger-boundary")
        return PolicyDecision(True, command, "realtime input allowed pending Enter policy check", "realtime")

    def _drain_child(self) -> None:
        assert self.master_fd is not None
        deadline = time.time() + 0.5
        while True:
            timeout = max(0.0, deadline - time.time())
            readable, _, _ = select.select([self.master_fd], [], [], min(0.05, timeout))
            if self.master_fd not in readable:
                if time.time() >= deadline:
                    break
                continue
            if not self._handle_child_output():
                break
            deadline = time.time() + 0.1

    def _enter_raw_terminal(self) -> None:
        if not sys.stdin.isatty():
            return
        self._old_term_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())

    def _prepare_slave_pty(self, slave_fd: int) -> None:
        try:
            attrs = termios.tcgetattr(slave_fd)
            # Prevent the PTY output layer from expanding TAB into spaces before
            # it reaches the SSH/login child. This keeps Tab as Ctrl-I.
            if hasattr(termios, "OPOST"):
                attrs[1] &= ~termios.OPOST
            if hasattr(termios, "TABDLY"):
                attrs[1] &= ~termios.TABDLY
            if hasattr(termios, "XTABS"):
                attrs[1] &= ~termios.XTABS
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except Exception:
            return

    def _restore_terminal(self) -> None:
        if self._old_term_attrs is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_term_attrs)
            self._old_term_attrs = None

    def _install_signal_handlers(self) -> None:
        if os.name == "nt":
            return

        def _resize(_signum: int, _frame: object) -> None:
            self._forward_winch()

        signal.signal(signal.SIGWINCH, _resize)
        self._forward_winch()

    def _forward_winch(self) -> None:
        if self.master_fd is None:
            return
        try:
            import fcntl
            import struct

            packed = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, packed)
            rows, cols, _, _ = struct.unpack("HHHH", packed)
            self.audit.event("terminal_resize", {"rows": rows, "cols": cols})
        except Exception:
            return

    def _close_child(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            else:
                self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                else:
                    self.proc.kill()
            except Exception:
                pass


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _first_word(command: str) -> str:
    return command.split()[0] if command.split() else ""
