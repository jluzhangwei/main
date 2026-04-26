from __future__ import annotations

import subprocess
import sys


def test_policy_cli_lists_backend_rules() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ssh_proxy.policy_cli", "list"],
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
        [sys.executable, "-m", "ssh_proxy.policy_cli", "check", "show version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    blocked = subprocess.run(
        [sys.executable, "-m", "ssh_proxy.policy_cli", "check", "reload"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert allowed.returncode == 0
    assert "ALLOW" in allowed.stdout
    assert blocked.returncode == 1
    assert "BLOCK" in blocked.stdout
