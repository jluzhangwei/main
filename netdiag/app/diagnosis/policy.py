from __future__ import annotations

import re

_READ_ONLY_PREFIX = (
    "show ",
    "display ",
    "dis ",
)

_DENY_PATTERNS = [
    re.compile(r"\bconfigure\b", re.IGNORECASE),
    re.compile(r"\bconf t\b", re.IGNORECASE),
    re.compile(r"\breload\b", re.IGNORECASE),
    re.compile(r"\bwrite\b", re.IGNORECASE),
    re.compile(r"\bdelete\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
]

_PLACEHOLDER_PATTERNS = [
    re.compile(r"[\u4e00-\u9fff]"),  # Chinese placeholder text should never appear in CLI commands
    re.compile(r"[{}<>]"),
    re.compile(r"\.\.\."),
    re.compile(r"\b(example|placeholder|interface_name|device_id)\b", re.IGNORECASE),
    re.compile(r"\b(例如|比如|具体|接口名|管理网)\b"),
    re.compile(r"\|\s*include\s*\(", re.IGNORECASE),
]


def has_placeholder_token(command: str) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return True
    return any(p.search(cmd) for p in _PLACEHOLDER_PATTERNS)


def is_read_only_command(command: str) -> bool:
    cmd = str(command or "").strip().lower()
    if not cmd:
        return False
    if has_placeholder_token(command):
        return False
    if any(p.search(cmd) for p in _DENY_PATTERNS):
        return False
    return cmd.startswith(_READ_ONLY_PREFIX)


def validate_commands(commands: list[str]) -> tuple[bool, list[str]]:
    bad: list[str] = []
    for c in commands:
        if not is_read_only_command(c):
            bad.append(c)
    return (len(bad) == 0, bad)
