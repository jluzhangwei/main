from __future__ import annotations

import json
import subprocess
import sys

from ssh_proxy.audit import AuditWriter
from ssh_proxy.policy import CommandPolicy


def test_audit_writer_creates_session_files_and_redacts_secrets(tmp_path) -> None:
    writer = AuditWriter(tmp_path, ["internal-login", "--device", "10.1.1.1"])
    policy = CommandPolicy()
    decision = policy.decide("show version")

    writer.command("show version", decision)
    writer.event("secret_test", {"password": "plain", "line": "token=abc123"})
    writer.replay("stdout", "Password: abc123\nnormal output")
    writer.close(0)

    assert writer.paths.metadata_path.exists()
    assert writer.paths.events_path.exists()
    assert writer.paths.replay_path.exists()

    metadata = json.loads(writer.paths.metadata_path.read_text(encoding="utf-8"))
    assert metadata["session_id"] == writer.session_id
    assert metadata["target_hint"] == "10.1.1.1"
    assert metadata["exit_code"] == 0

    events = writer.paths.events_path.read_text(encoding="utf-8")
    replay = writer.paths.replay_path.read_text(encoding="utf-8")

    assert "show version" in events
    assert "plain" not in events
    assert "token=abc123" not in events
    assert "token***" in events
    assert "Password***" in replay

    assert (tmp_path / "latest").read_text(encoding="utf-8").strip() == writer.session_id
    index_text = (tmp_path / "sessions.jsonl").read_text(encoding="utf-8")
    assert writer.session_id in index_text


def test_audit_cli_lists_shows_and_filters_sessions(tmp_path) -> None:
    writer = AuditWriter(tmp_path, ["internal-login", "--device", "10.1.1.1"])
    policy = CommandPolicy()
    writer.command("show version", policy.decide("show version"))
    writer.command("reload", policy.decide("reload"))
    writer.replay("stdout", "fake-device> ")
    writer.replay("stdin_echo", "reload\r\n")
    writer.replay("stdin_echo", "Blocked by SSH proxy policy: dangerous command is not allowed (reload)\r\n")
    writer.close(0)

    list_out = _run_audit_cli(tmp_path, "list")
    assert writer.session_id[:12] in list_out
    assert "10.1.1.1" in list_out

    show_out = _run_audit_cli(tmp_path, "show")
    assert f"session_id: {writer.session_id}" in show_out
    assert "login_command: internal-login --device 10.1.1.1" in show_out

    commands_out = _run_audit_cli(tmp_path, "commands")
    assert "ALLOW" in commands_out
    assert "show version" in commands_out
    assert "BLOCK" in commands_out
    assert "reload" in commands_out

    blocked_out = _run_audit_cli(tmp_path, "blocked")
    assert "reload" in blocked_out
    assert "show version" not in blocked_out

    replay_out = _run_audit_cli(tmp_path, "replay")
    assert "fake-device>" in replay_out
    assert "Blocked by SSH proxy policy" in replay_out


def _run_audit_cli(audit_dir, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "ssh_proxy.audit_cli", "--audit-dir", str(audit_dir), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout
