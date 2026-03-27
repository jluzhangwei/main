from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.compound_command_runtime import (
    resolve_command_output,
    run_compound_command_batch,
    split_batch_output_by_markers,
)


class _Command:
    def __init__(self, command: str):
        self.command = command
        self.status = "queued"
        self.started_at = None
        self.completed_at = None
        self.duration_ms = None
        self.error = ""
        self.output = ""


class _Adapter:
    def __init__(self, output: str = "", error: Exception | None = None):
        self.output = output
        self.error = error
        self.commands: list[str] = []

    async def run_command(self, command: str) -> str:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return self.output


def test_split_batch_output_by_markers_keeps_marker_order():
    chunks = split_batch_output_by_markers(
        "\n# show version\nversion output\n# show clock\nclock output\n"
    )

    assert chunks == [
        ("show version", "version output"),
        ("show clock", "clock output"),
    ]


def test_resolve_command_output_falls_back_to_next_chunk_and_full_output():
    chunks = [("show version", "version output"), ("show clock", "clock output")]

    output, cursor = resolve_command_output("show ip route", "full", chunks, 0, batch_size=3)
    assert output == "version output"
    assert cursor == 1

    output, cursor = resolve_command_output("show ip route", "full", chunks, 2, batch_size=3)
    assert output == "full"
    assert cursor == 2


@pytest.mark.asyncio
async def test_run_compound_command_batch_updates_running_and_records_outputs():
    adapter = _Adapter(
        output="\n# show version\nversion output\n# show clock\nclock output\n"
    )
    commands = [_Command("show version"), _Command("show clock")]
    running: list[str] = []
    recorded: list[tuple[str, str]] = []

    async def _mark_running(command: _Command, started_at: datetime):
        running.append(command.command)
        command.status = "running"
        command.started_at = started_at

    async def _mark_failed(command: _Command, error: str, failed_at: datetime):
        command.status = "failed"
        command.error = error
        command.completed_at = failed_at

    async def _record_output(command: _Command, output: str):
        command.status = "succeeded"
        command.output = output
        recorded.append((command.command, output))

    await run_compound_command_batch(
        adapter,
        commands,
        get_command_text=lambda item: item.command,
        mark_running=_mark_running,
        mark_failed=_mark_failed,
        record_output=_record_output,
        now_factory=lambda: datetime(2026, 3, 27, tzinfo=timezone.utc),
    )

    assert adapter.commands == ["show version ; show clock"]
    assert running == ["show version", "show clock"]
    assert recorded == [("show version", "version output"), ("show clock", "clock output")]


@pytest.mark.asyncio
async def test_run_compound_command_batch_marks_following_commands_as_skipped_on_error():
    adapter = _Adapter(error=RuntimeError("boom"))
    commands = [_Command("show version"), _Command("show clock")]
    failures: list[tuple[str, str]] = []

    async def _mark_running(command: _Command, started_at: datetime):
        command.status = "running"
        command.started_at = started_at

    async def _mark_failed(command: _Command, error: str, failed_at: datetime):
        command.status = "failed"
        command.error = error
        command.completed_at = failed_at
        failures.append((command.command, error))

    with pytest.raises(RuntimeError, match="boom"):
        await run_compound_command_batch(
            adapter,
            commands,
            get_command_text=lambda item: item.command,
            mark_running=_mark_running,
            mark_failed=_mark_failed,
            record_output=lambda _command, _output: None,
            now_factory=lambda: datetime(2026, 3, 27, tzinfo=timezone.utc),
        )

    assert failures == [
        ("show version", "boom"),
        ("show clock", "boom (skipped subsequent command in same batch)"),
    ]
