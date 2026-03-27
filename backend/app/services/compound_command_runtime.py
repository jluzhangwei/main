from __future__ import annotations

import inspect
import re
from datetime import datetime
from typing import Any, Callable


async def run_compound_command_batch(
    adapter: Any,
    commands: list[Any],
    *,
    get_command_text: Callable[[Any], str],
    mark_running: Callable[[Any, datetime], Any],
    mark_failed: Callable[[Any, str, datetime], Any],
    record_output: Callable[[Any, str], Any],
    now_factory: Callable[[], datetime],
) -> str:
    if not commands:
        return ""

    started_at = now_factory()
    for command in commands:
        await _maybe_await(mark_running(command, started_at))

    combined = " ; ".join(get_command_text(command).strip() for command in commands if get_command_text(command).strip())
    try:
        output = await _maybe_await(adapter.run_command(combined))
    except Exception as exc:
        failed_at = now_factory()
        base_error = str(exc)
        for idx, command in enumerate(commands):
            error = base_error if idx == 0 else f"{base_error} (skipped subsequent command in same batch)"
            await _maybe_await(mark_failed(command, error, failed_at))
        raise

    chunks = split_batch_output_by_markers(str(output or ""))
    chunk_cursor = 0
    for command in commands:
        command_output, chunk_cursor = resolve_command_output(
            get_command_text(command),
            str(output or ""),
            chunks,
            chunk_cursor,
            batch_size=len(commands),
        )
        await _maybe_await(record_output(command, command_output))

    return str(output or "")


def split_batch_output_by_markers(output: str) -> list[tuple[str, str]]:
    text = str(output or "")
    if not text.strip():
        return []

    chunks: list[tuple[str, str]] = []
    current_cmd: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        marker = re.match(r"^\s*#\s*(.+?)\s*$", line)
        if marker:
            if current_cmd is not None:
                chunks.append((current_cmd, "\n".join(current_lines).strip()))
            current_cmd = marker.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)

    if current_cmd is not None:
        chunks.append((current_cmd, "\n".join(current_lines).strip()))
    return chunks


def resolve_command_output(
    command_text: str,
    full_output: str,
    chunks: list[tuple[str, str]],
    chunk_cursor: int,
    *,
    batch_size: int,
) -> tuple[str, int]:
    command_output = ""
    normalized_target = re.sub(r"\s+", " ", str(command_text or "").strip().lower())
    next_cursor = chunk_cursor

    for idx in range(chunk_cursor, len(chunks)):
        marker_cmd, marker_output = chunks[idx]
        normalized_marker = re.sub(r"\s+", " ", marker_cmd.strip().lower())
        if normalized_marker == normalized_target:
            command_output = marker_output
            next_cursor = idx + 1
            break

    if command_output:
        return command_output, next_cursor
    if batch_size == 1:
        return str(full_output or ""), next_cursor
    if next_cursor < len(chunks):
        return chunks[next_cursor][1], next_cursor + 1
    return str(full_output or ""), next_cursor


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
