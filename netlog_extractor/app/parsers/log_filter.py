from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .time_parser import parse_log_line_timestamp


@dataclass
class FilterResult:
    text: str
    hits_count: int
    blocks_count: int


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda x: x[0])
    merged: list[tuple[int, int]] = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def filter_log_with_context(
    raw_log_text: str,
    device_start: datetime,
    device_end: datetime,
    context_lines: int,
    vendor: str,
    reference_year: int | None = None,
) -> FilterResult:
    lines = raw_log_text.splitlines()
    if not lines:
        return FilterResult(text="", hits_count=0, blocks_count=0)

    hit_indexes: list[int] = []
    for i, line in enumerate(lines):
        ts = parse_log_line_timestamp(line, vendor=vendor, reference_year=reference_year)
        if ts is None:
            continue
        if device_start <= ts <= device_end:
            hit_indexes.append(i)

    if not hit_indexes:
        return FilterResult(text="", hits_count=0, blocks_count=0)

    ranges: list[tuple[int, int]] = []
    for idx in hit_indexes:
        start = max(0, idx - context_lines)
        end = min(len(lines) - 1, idx + context_lines)
        ranges.append((start, end))

    merged = _merge_ranges(ranges)

    output_lines: list[str] = []
    for start, end in merged:
        output_lines.extend(lines[start : end + 1])

    return FilterResult(
        text="\n".join(output_lines) + ("\n" if output_lines else ""),
        hits_count=len(hit_indexes),
        blocks_count=len(merged),
    )
