from __future__ import annotations

import re
from typing import Any, Iterable


FILTER_OPERATOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("include", re.compile(r"\|\s*include\b", re.IGNORECASE)),
    ("exclude", re.compile(r"\|\s*exclude\b", re.IGNORECASE)),
    ("begin", re.compile(r"\|\s*begin\b", re.IGNORECASE)),
    ("section", re.compile(r"\|\s*section\b", re.IGNORECASE)),
    ("match", re.compile(r"\|\s*match\b", re.IGNORECASE)),
    ("count", re.compile(r"(?:\|\s*count\b|\bcount\b)", re.IGNORECASE)),
    ("grep", re.compile(r"\|\s*grep\b", re.IGNORECASE)),
    ("regex", re.compile(r"\|\s*regex\b", re.IGNORECASE)),
)

OUTPUT_TOO_LONG_CHAR_THRESHOLD = 1800
OUTPUT_TOO_LONG_LINE_THRESHOLD = 45

FAILURE_HINTS = (
    "invalid input",
    "unrecognized command",
    "wrong parameter",
    "syntax error",
    "syntax failure",
    "ambiguous command",
    "incomplete command",
    "command not found",
    "error:",
)


def _get_field(item: Any, name: str) -> str:
    value = getattr(item, name, "")
    if value is None:
        return ""
    return str(value)


def _status_value(item: Any) -> str:
    status = getattr(item, "status", "")
    if hasattr(status, "value"):
        return str(status.value or "").strip().lower()
    return str(status or "").strip().lower()


def detect_filter_operators(command_text: str) -> list[str]:
    normalized = str(command_text or "").strip()
    if not normalized:
        return []
    rows: list[str] = []
    for label, pattern in FILTER_OPERATOR_PATTERNS:
        if pattern.search(normalized):
            rows.append(label)
    return rows


def vendor_filter_defaults(vendor: str) -> list[str]:
    normalized = str(vendor or "").strip().lower()
    if "huawei" in normalized:
        return ["include", "begin", "count"]
    if "arista" in normalized or "cisco" in normalized:
        return ["include", "section", "begin", "count", "grep"]
    return ["include", "begin", "count"]


def build_problem_targets(problem: str) -> list[str]:
    text = str(problem or "")
    matches: list[str] = []
    patterns = (
        r"\b(?:Eth(?:ernet)?\d+(?:/\d+){0,3}|Et\d+(?:/\d+){0,3}|GigabitEthernet\d+(?:/\d+){0,3}|Gi\d+(?:/\d+){0,3})\b",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    )
    for pattern in patterns:
        for matched in re.findall(pattern, text, flags=re.IGNORECASE):
            value = str(matched).strip()
            if value and value.lower() not in {item.lower() for item in matches}:
                matches.append(value)
    keyword_map = (
        ("ospf", "OSPF"),
        ("bgp", "BGP"),
        ("isis", "ISIS"),
        ("route", "路由"),
        ("routing", "路由"),
        ("neighbor", "邻居"),
        ("peer", "邻居"),
        ("shutdown", "shutdown"),
        ("disable", "disable"),
    )
    lowered = text.lower()
    for token, label in keyword_map:
        if token in lowered and label.lower() not in {item.lower() for item in matches}:
            matches.append(label)
    return matches[:4]


def _is_failed_filtered_command(item: Any) -> bool:
    merged = f"{_get_field(item, 'output')}\n{_get_field(item, 'error')}".strip().lower()
    status = _status_value(item)
    if status in {"failed", "blocked", "rejected"}:
        return True
    return any(token in merged for token in FAILURE_HINTS)


def _is_successful_filtered_command(item: Any) -> bool:
    if _is_failed_filtered_command(item):
        return False
    status = _status_value(item)
    if status and status not in {"succeeded", "completed"}:
        return False
    return True


def build_filter_capability_context(
    *,
    vendor: str,
    version_signature: str | None,
    commands: Iterable[Any],
    problem: str,
) -> str:
    recent = list(commands)[-18:]
    if not recent and not vendor:
        return ""
    success_ops: dict[str, int] = {}
    failed_ops: dict[str, int] = {}
    for item in recent:
        command_text = _get_field(item, "effective_command") or _get_field(item, "command")
        operators = detect_filter_operators(command_text)
        if not operators:
            continue
        if _is_successful_filtered_command(item):
            for op in operators:
                success_ops[op] = success_ops.get(op, 0) + 1
        elif _is_failed_filtered_command(item):
            for op in operators:
                failed_ops[op] = failed_ops.get(op, 0) + 1

    preferred = vendor_filter_defaults(vendor)
    lines = [
        "系统已整理当前设备的过滤/输出压缩能力信号：",
        f"- 过滤语法建议: 优先候选 {' / '.join(preferred)}",
    ]
    if version_signature:
        lines.append(f"- 版本指纹: {str(version_signature).strip()}")
    targets = build_problem_targets(problem)
    if targets:
        lines.append(f"- 当前目标对象: {' / '.join(targets)}")
    if success_ops:
        labels = [f"{name}({count})" for name, count in sorted(success_ops.items(), key=lambda item: (-item[1], item[0]))]
        lines.append(f"- 已验证可用过滤: {', '.join(labels[:4])}")
    if failed_ops:
        labels = [f"{name}({count})" for name, count in sorted(failed_ops.items(), key=lambda item: (-item[1], item[0]))]
        lines.append(f"- 已验证失败过滤: {', '.join(labels[:4])}")
    lines.append("- 过滤规则: 单条命令优先只使用一种过滤/截断方式，先摘要后详情。")
    return "\n".join(lines)


def build_output_compaction_context(
    *,
    commands: Iterable[Any],
    problem: str,
) -> str:
    problem_text = str(problem or "").lower()
    long_rows: list[tuple[str, int, int, bool]] = []
    for item in list(commands)[-12:]:
        output = _get_field(item, "output")
        if not output:
            continue
        line_count = len(output.splitlines())
        char_count = len(output)
        if char_count < OUTPUT_TOO_LONG_CHAR_THRESHOLD and line_count < OUTPUT_TOO_LONG_LINE_THRESHOLD:
            continue
        command_text = (_get_field(item, "effective_command") or _get_field(item, "command")).strip()
        if not command_text:
            continue
        long_rows.append((command_text, char_count, line_count, bool(detect_filter_operators(command_text))))
    if not long_rows:
        long_rows = []

    lines = [
        "系统已整理当前任务的输出压缩信号：",
    ]
    if long_rows:
        lines.append("- 输出压缩状态: 最近命令回显过长，下一轮应优先缩窄查询范围。")
        for command_text, char_count, line_count, already_filtered in long_rows[-2:]:
            suffix = "；当前命令已带过滤但范围仍偏宽" if already_filtered else ""
            lines.append(
                f"- 过长回显命令: {command_text[:140]} (约 {line_count} 行 / {char_count} 字符){suffix}"
            )
    else:
        lines.append("- 输出压缩状态: 暂无明显超长回显，但仍应维持先摘要后详情的查询方式。")
    targets = build_problem_targets(problem)
    if targets:
        lines.append(f"- 输出压缩建议: 围绕 {' / '.join(targets)} 做过滤，避免再次执行同类全量命令。")
    else:
        lines.append("- 输出压缩建议: 优先围绕目标接口、邻居、协议关键词或关键错误词做过滤。")
    if any(token in problem_text for token in ("状态", "status", "检查", "check", "查询", "query", "是否")):
        lines.append("- 摘要优先规则: 这是状态/存在性检查任务，先使用 brief/summary/count 级命令，再按命中结果展开详情。")
    if any(token in problem_text for token in ("ospf", "bgp", "isis", "neighbor", "peer", "邻居", "协议")):
        lines.append("- 协议检查规则: 先查看协议邻居/接口/路由摘要，若摘要已能说明异常，就不要立即展开全量配置或全量路由。")
    if targets:
        lines.append("- 目标对象规则: 若已识别出具体接口/IP/邻居，下一轮命令应直接命中该对象或用过滤条件锁定该对象。")
    return "\n".join(lines)
