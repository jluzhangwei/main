from __future__ import annotations

import subprocess
import sys


def test_main_cli_help_shows_proxy_audit_and_policy_usage() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "ssh-proxy -- <login command...>" in result.stdout
    assert "ssh-proxy audit <subcommand>" in result.stdout
    assert "ssh-proxy policy <subcommand>" in result.stdout
    assert "ssh-proxy policy check \"reload\"" in result.stdout


def test_main_cli_help_topic_dispatches_to_subcommand_help() -> None:
    audit = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "help", "audit"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    policy_check = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "help", "policy", "check"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "usage: ssh-proxy audit" in audit.stdout
    assert "usage: ssh-proxy policy check" in policy_check.stdout


def test_policy_cli_lists_backend_rules() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "policy", "list"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "ALLOW prefixes" in result.stdout
    assert "BLOCK dangerous patterns" in result.stdout
    assert "reload" in result.stdout
    assert "system-view" in result.stdout


def test_policy_cli_check_returns_status_code() -> None:
    allowed = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "policy", "check", "show version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    blocked = subprocess.run(
        [sys.executable, "-m", "ssh_proxy", "policy", "check", "reload"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert allowed.returncode == 0
    assert "ALLOW" in allowed.stdout
    assert blocked.returncode == 1
    assert "BLOCK" in blocked.stdout
