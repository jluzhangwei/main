from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class CommandGroupResult:
    failed: bool
    stopped: bool
    processed_count: int
    rollback_count: int


async def execute_command_group(
    items: Iterable[Any],
    *,
    execute_item: Callable[[Any, int], Any],
    is_failure: Callable[[Any], bool],
    continue_on_failure: bool = False,
    should_stop: Callable[[], Any] | None = None,
    rollback_items: Iterable[Any] | None = None,
    execute_rollback_item: Callable[[Any, int], Any] | None = None,
    on_stopped_remaining: Callable[[list[Any], int], Any] | None = None,
    on_failed_remaining: Callable[[list[Any], Any, int], Any] | None = None,
) -> CommandGroupResult:
    rows = list(items)
    processed_count = 0
    rollback_count = 0
    failed = False

    for idx, item in enumerate(rows, start=1):
        if should_stop is not None and await _maybe_await(should_stop()):
            if on_stopped_remaining is not None:
                await _maybe_await(on_stopped_remaining(rows[idx - 1 :], idx))
            return CommandGroupResult(
                failed=failed,
                stopped=True,
                processed_count=processed_count,
                rollback_count=rollback_count,
            )

        result = await _maybe_await(execute_item(item, idx))
        processed_count += 1
        if not is_failure(result):
            continue

        failed = True
        if continue_on_failure:
            continue

        if on_failed_remaining is not None and idx < len(rows):
            await _maybe_await(on_failed_remaining(rows[idx:], result, idx))

        if rollback_items and execute_rollback_item is not None:
            for rollback_idx, rollback in enumerate(rollback_items, start=1):
                await _maybe_await(execute_rollback_item(rollback, rollback_idx))
                rollback_count += 1
        break

    return CommandGroupResult(
        failed=failed,
        stopped=False,
        processed_count=processed_count,
        rollback_count=rollback_count,
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
