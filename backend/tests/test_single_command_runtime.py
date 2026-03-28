from __future__ import annotations

import pytest

from app.services.single_command_runtime import execute_single_command


class _Adapter:
    def __init__(self, *, output: str = "", error: Exception | None = None):
        self.output = output
        self.error = error
        self.commands: list[str] = []

    async def run_command(self, command: str) -> str:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return self.output


@pytest.mark.asyncio
async def test_execute_single_command_rejects_before_run_when_stopped():
    adapter = _Adapter(output="ok")
    called: list[str] = []

    result = await execute_single_command(
        adapter,
        "show version",
        should_stop=lambda: True,
        on_rejected=lambda message: called.append(message),
    )

    assert result.stopped is True
    assert result.succeeded is False
    assert adapter.commands == []
    assert called == ["Stopped by operator"]


@pytest.mark.asyncio
async def test_execute_single_command_calls_running_and_success_hooks():
    adapter = _Adapter(output="version output")
    called: list[tuple[str, str]] = []

    result = await execute_single_command(
        adapter,
        "show version",
        on_running=lambda: called.append(("running", "")),
        on_success=lambda output: called.append(("success", output)),
    )

    assert result.succeeded is True
    assert result.output == "version output"
    assert adapter.commands == ["show version"]
    assert called == [("running", ""), ("success", "version output")]


@pytest.mark.asyncio
async def test_execute_single_command_calls_failure_hook():
    adapter = _Adapter(error=RuntimeError("boom"))
    called: list[str] = []

    result = await execute_single_command(
        adapter,
        "show version",
        on_failure=lambda message: called.append(message),
    )

    assert result.succeeded is False
    assert result.error == "boom"
    assert called == ["boom"]
