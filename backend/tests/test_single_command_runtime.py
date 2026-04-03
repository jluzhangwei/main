from __future__ import annotations

import asyncio

import pytest

from app.services.single_command_runtime import execute_single_command


class _SlowAdapter:
    async def run_command(self, command: str) -> str:
        await asyncio.sleep(0.2)
        return f"ok:{command}"


@pytest.mark.asyncio
async def test_execute_single_command_times_out_and_reports_failure():
    failures: list[str] = []

    result = await execute_single_command(
        _SlowAdapter(),
        "show clock",
        timeout_seconds=0.01,
        on_failure=lambda message: failures.append(message),
    )

    assert result.succeeded is False
    assert result.stopped is False
    assert "timeout" in result.error.lower()
    assert failures and "timeout" in failures[0].lower()


@pytest.mark.asyncio
async def test_execute_single_command_uses_tighter_default_timeout(monkeypatch):
    monkeypatch.setenv("COMMAND_EXECUTION_TIMEOUT", "0.01")
    failures: list[str] = []

    result = await execute_single_command(
        _SlowAdapter(),
        "show version",
        on_failure=lambda message: failures.append(message),
    )

    assert result.succeeded is False
    assert "timeout" in result.error.lower()
    assert failures and "timeout" in failures[0].lower()
