from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.services.compound_command_runtime import run_compound_command_batch
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


@pytest.mark.asyncio
async def test_run_compound_command_batch_times_out_and_marks_all_failed(monkeypatch):
    monkeypatch.setenv("COMMAND_EXECUTION_TIMEOUT", "0.01")

    class _Command:
        def __init__(self, text: str) -> None:
            self.command = text
            self.status = "queued"
            self.error = None
            self.started_at = None
            self.completed_at = None

    commands = [_Command("enable"), _Command("show privilege")]

    async def _mark_running(command, started_at):
        command.status = "running"
        command.started_at = started_at

    async def _mark_failed(command, error, failed_at):
        command.status = "failed"
        command.error = error
        command.completed_at = failed_at

    async def _record_output(command, output):
        command.output = output

    with pytest.raises(TimeoutError):
        await run_compound_command_batch(
            _SlowAdapter(),
            commands,
            get_command_text=lambda item: item.command,
            mark_running=_mark_running,
            mark_failed=_mark_failed,
            record_output=_record_output,
            now_factory=datetime.utcnow,
        )

    assert commands[0].status == "failed"
    assert "timeout" in (commands[0].error or "").lower()
    assert commands[1].status == "failed"
    assert "skipped subsequent command" in (commands[1].error or "").lower()
