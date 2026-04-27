from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import textwrap
import time
from pathlib import Path


def test_sandbox_blocks_dangerous_command_transparently_and_audits(tmp_path: Path) -> None:
    received_path = tmp_path / "received.jsonl"
    audit_dir = tmp_path / "audit"
    fake_login = tmp_path / "fake_login.py"
    fake_login.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path

            received = Path(sys.argv[1])
            sys.stdout.write("fake-device> ")
            sys.stdout.flush()
            for line in sys.stdin:
                command = line.rstrip("\\r\\n")
                with received.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"command": command}) + "\\n")
                if command in {"exit", "quit"}:
                    sys.stdout.write("logout\\n")
                    sys.stdout.flush()
                    break
                sys.stdout.write(f"executed: {command}\\n")
                sys.stdout.write("fake-device> ")
                sys.stdout.flush()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SSH_PROXY_AUDIT_DIR"] = str(audit_dir)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ssh_proxy",
            "--",
            sys.executable,
            str(fake_login),
            str(received_path),
        ],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=Path.cwd(),
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)
    try:
        initial = _read_until(master_fd, "fake-device> ")
        assert "fake-device>" in initial

        os.write(master_fd, b"show version\r")
        allowed_output = _read_until(master_fd, "fake-device> ")
        assert "show version" in allowed_output
        assert "executed: show version" in allowed_output

        os.write(master_fd, b"reload\r")
        blocked_output = _read_until(master_fd, "Blocked by SSH proxy policy")
        assert "reload" in blocked_output
        assert "Blocked by SSH proxy policy" in blocked_output
        time.sleep(0.2)

        os.write(master_fd, b"exit\r")
        exit_output = _read_until(master_fd, "logout")
        assert "logout" in exit_output
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        os.close(master_fd)

    received = [
        json.loads(line)["command"]
        for line in received_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert received == ["show version", "exit"]
    assert "reload" not in received

    session_dirs = [p for p in audit_dir.iterdir() if p.is_dir()]
    assert len(session_dirs) == 1
    events_text = (session_dirs[0] / "events.jsonl").read_text(encoding="utf-8")
    replay_text = (session_dirs[0] / "replay.jsonl").read_text(encoding="utf-8")
    metadata = json.loads((session_dirs[0] / "metadata.json").read_text(encoding="utf-8"))

    assert metadata["exit_code"] == 0
    assert "show version" in events_text
    assert "reload" in events_text
    assert "command_blocked" in events_text
    assert "Blocked by SSH proxy policy" in replay_text
    assert "executed: show version" in replay_text


def test_sandbox_passes_question_mark_and_tab_for_device_help(tmp_path: Path) -> None:
    received_path = tmp_path / "assist.jsonl"
    audit_dir = tmp_path / "audit"
    fake_login = tmp_path / "fake_assist_login.py"
    fake_login.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import termios
            import tty
            from pathlib import Path

            received = Path(sys.argv[1])
            old = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
            line = ""
            try:
                sys.stdout.write("fake-device> ")
                sys.stdout.flush()
                while True:
                    ch = sys.stdin.read(1)
                    if not ch:
                        break
                    if ch == "\\t":
                        with received.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"assist": "tab", "line": line}) + "\\n")
                        sys.stdout.write("\\n<TAB-COMPLETE>\\nfake-device> " + line)
                        sys.stdout.flush()
                        continue
                    if ch == " ":
                        with received.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"assist": "space", "line": line}) + "\\n")
                        line += ch
                        sys.stdout.write(" <SPACE>")
                        sys.stdout.flush()
                        continue
                    if ch == "?":
                        with received.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"assist": "question", "line": line}) + "\\n")
                        sys.stdout.write("\\n<HELP> version interface clock\\nfake-device> " + line)
                        sys.stdout.flush()
                        continue
                    if ch in "\\r\\n":
                        command = line
                        with received.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"command": command}) + "\\n")
                        if command == "exit":
                            sys.stdout.write("\\nlogout\\n")
                            sys.stdout.flush()
                            break
                        sys.stdout.write("\\nexecuted: " + command + "\\nfake-device> ")
                        sys.stdout.flush()
                        line = ""
                        continue
                    if ch == "\\x15":
                        line = ""
                        sys.stdout.write("^U\\nfake-device> ")
                        sys.stdout.flush()
                        continue
                    line += ch
                    sys.stdout.write(ch)
                    sys.stdout.flush()
            finally:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SSH_PROXY_AUDIT_DIR"] = str(audit_dir)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ssh_proxy",
            "--",
            sys.executable,
            str(fake_login),
            str(received_path),
        ],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=Path.cwd(),
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)
    try:
        _read_until(master_fd, "fake-device> ")

        os.write(master_fd, b"display ")
        space_output = _read_until(master_fd, "<SPACE>")
        assert "<SPACE>" in space_output

        os.write(master_fd, b"?")
        display_help_output = _read_until(master_fd, "<HELP>")
        assert "version interface clock" in display_help_output

        os.write(master_fd, b"\r")
        _read_until(master_fd, "fake-device> ")

        os.write(master_fd, b"show ?")
        help_output = _read_until(master_fd, "<HELP>")
        assert "version interface clock" in help_output

        os.write(master_fd, b"\r")
        _read_until(master_fd, "fake-device> ")

        os.write(master_fd, b"show \t")
        tab_output = _read_until(master_fd, "<TAB-COMPLETE>")
        assert "<TAB-COMPLETE>" in tab_output

        os.write(master_fd, b"\r")
        _read_until(master_fd, "fake-device> ")

        os.write(master_fd, b"dis\t")
        display_completion_output = _read_until(master_fd, "<SPACE>")
        assert "play" in display_completion_output

        os.write(master_fd, b"\r")
        _read_until(master_fd, "fake-device> ")

        os.write(master_fd, b"reload ")
        blocked_output = _read_until(master_fd, "Blocked by SSH proxy policy")
        assert "Blocked by SSH proxy policy" in blocked_output
        os.write(master_fd, b"\r")

        os.write(master_fd, b"exit\r")
        _read_until(master_fd, "logout")
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        os.close(master_fd)

    rows = [json.loads(line) for line in received_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {"assist": "space", "line": "display"} in rows
    assert {"assist": "question", "line": "display "} in rows
    assert {"assist": "question", "line": "show "} in rows
    assert {"assist": "tab", "line": "show "} in rows
    assert {"assist": "space", "line": "display"} in rows
    assert {"assist": "space", "line": "reload"} not in rows

    session_dirs = [p for p in audit_dir.iterdir() if p.is_dir()]
    events_text = (session_dirs[0] / "events.jsonl").read_text(encoding="utf-8")
    replay_text = (session_dirs[0] / "replay.jsonl").read_text(encoding="utf-8")
    assert "assist_key" in events_text
    assert "space" in events_text
    assert "question-mark" in events_text
    assert "tab" in events_text
    assert "command_blocked" in events_text
    assert "<HELP>" in replay_text
    assert "<TAB-COMPLETE>" in replay_text


def _read_until(fd: int, needle: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    chunks: list[str] = []
    while time.time() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.05)
        if not readable:
            continue
        chunk = os.read(fd, 65535)
        if not chunk:
            break
        chunks.append(chunk.decode("utf-8", errors="replace"))
        text = "".join(chunks)
        if needle in text:
            return text
    raise AssertionError(f"timed out waiting for {needle!r}; got {''.join(chunks)!r}")
