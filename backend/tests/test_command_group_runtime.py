from __future__ import annotations

import pytest

from app.services.command_group_runtime import execute_command_group


@pytest.mark.asyncio
async def test_execute_command_group_stops_on_first_failure():
    executed: list[str] = []

    async def _execute(item: str, _idx: int):
        executed.append(item)
        return {"item": item, "failed": item == "cmd-2"}

    result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
    )

    assert executed == ["cmd-1", "cmd-2"]
    assert result.failed is True
    assert result.stopped is False
    assert result.processed_count == 2
    assert result.rollback_count == 0


@pytest.mark.asyncio
async def test_execute_command_group_continue_on_failure():
    executed: list[str] = []

    async def _execute(item: str, _idx: int):
        executed.append(item)
        return {"item": item, "failed": item == "cmd-2"}

    result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
        continue_on_failure=True,
    )

    assert executed == ["cmd-1", "cmd-2", "cmd-3"]
    assert result.failed is True
    assert result.processed_count == 3
    assert result.rollback_count == 0


@pytest.mark.asyncio
async def test_execute_command_group_runs_rollbacks_on_failure():
    executed: list[str] = []
    rolled_back: list[str] = []

    async def _execute(item: str, _idx: int):
        executed.append(item)
        return {"item": item, "failed": item == "cmd-2"}

    async def _rollback(item: str, _idx: int):
        rolled_back.append(item)

    result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
        rollback_items=["rollback-1", "rollback-2"],
        execute_rollback_item=_rollback,
    )

    assert executed == ["cmd-1", "cmd-2"]
    assert rolled_back == ["rollback-1", "rollback-2"]
    assert result.failed is True
    assert result.rollback_count == 2


@pytest.mark.asyncio
async def test_execute_command_group_honors_stop_request():
    executed: list[str] = []
    stop_checks = {"count": 0}

    async def _execute(item: str, _idx: int):
        executed.append(item)
        return {"item": item, "failed": False}

    async def _should_stop():
        stop_checks["count"] += 1
        return stop_checks["count"] >= 2

    result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
        should_stop=_should_stop,
    )

    assert executed == ["cmd-1"]
    assert result.failed is False
    assert result.stopped is True
    assert result.processed_count == 1


@pytest.mark.asyncio
async def test_execute_command_group_notifies_remaining_items_on_stop_and_failure():
    stopped_remaining: list[str] = []
    failed_remaining: list[str] = []

    async def _execute(item: str, _idx: int):
        return {"item": item, "failed": item == "cmd-2"}

    stop_counter = {"count": 0}

    async def _should_stop():
        stop_counter["count"] += 1
        return stop_counter["count"] >= 2

    stop_result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
        should_stop=_should_stop,
        on_stopped_remaining=lambda rows, _idx: stopped_remaining.extend(rows),
    )

    assert stop_result.stopped is True
    assert stopped_remaining == ["cmd-2", "cmd-3"]

    fail_result = await execute_command_group(
        ["cmd-1", "cmd-2", "cmd-3"],
        execute_item=_execute,
        is_failure=lambda payload: bool(payload["failed"]),
        on_failed_remaining=lambda rows, _result, _idx: failed_remaining.extend(rows),
    )

    assert fail_result.failed is True
    assert failed_remaining == ["cmd-3"]
