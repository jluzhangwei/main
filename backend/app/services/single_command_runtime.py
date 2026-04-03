from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SingleCommandRuntimeResult:
    stopped: bool
    succeeded: bool
    output: str
    error: str


async def execute_single_command(
    adapter: Any,
    command_text: str,
    *,
    timeout_seconds: float | None = None,
    should_stop: Callable[[], Any] | None = None,
    on_rejected: Callable[[str], Any] | None = None,
    on_running: Callable[[], Any] | None = None,
    on_success: Callable[[str], Any] | None = None,
    on_failure: Callable[[str], Any] | None = None,
) -> SingleCommandRuntimeResult:
    if should_stop is not None and await _maybe_await(should_stop()):
        message = "Stopped by operator"
        if on_rejected is not None:
            await _maybe_await(on_rejected(message))
        return SingleCommandRuntimeResult(stopped=True, succeeded=False, output="", error=message)

    if on_running is not None:
        await _maybe_await(on_running())

    try:
        timeout = timeout_seconds
        if timeout is None:
            timeout = float(os.getenv("COMMAND_EXECUTION_TIMEOUT", "20"))
        if timeout and timeout > 0:
            output = await asyncio.wait_for(_maybe_await(adapter.run_command(command_text)), timeout=float(timeout))
        else:
            output = await _maybe_await(adapter.run_command(command_text))
    except asyncio.TimeoutError:
        message = f"Command execution timeout after {float(timeout):.0f}s"
        if on_failure is not None:
            await _maybe_await(on_failure(message))
        return SingleCommandRuntimeResult(stopped=False, succeeded=False, output="", error=message)
    except Exception as exc:
        message = str(exc)
        if on_failure is not None:
            await _maybe_await(on_failure(message))
        return SingleCommandRuntimeResult(stopped=False, succeeded=False, output="", error=message)

    text = str(output or "")
    if on_success is not None:
        await _maybe_await(on_success(text))
    return SingleCommandRuntimeResult(stopped=False, succeeded=True, output=text, error="")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
