from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    normalized: str
    reason: str
    rule: str


ALLOW_PREFIXES = (
    "show",
    "display",
    "dis",
    "ping",
    "traceroute",
    "tracert",
    "quit",
    "exit",
    "end",
    "?",
)

CONFIG_EXIT_COMMANDS = {"quit", "exit", "end", "abort", "return"}

DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("configure", re.compile(r"(^|\s)(configure|conf\s+t|config\s+terminal)(\s|$)", re.I)),
    ("system-view", re.compile(r"(^|\s)system-view(\s|$)", re.I)),
    ("commit", re.compile(r"(^|\s)commit(\s|$)", re.I)),
    ("write", re.compile(r"(^|\s)(write|save)(\s|$)", re.I)),
    ("reload", re.compile(r"(^|\s)(reload|reboot|restart)(\s|$)", re.I)),
    ("delete", re.compile(r"(^|\s)(delete|del|erase|format)(\s|$)", re.I)),
    ("shutdown", re.compile(r"(^|\s)(shutdown|shut)(\s|$)", re.I)),
    ("shell-delete", re.compile(r"(^|\s)rm\s+(-[A-Za-z]*[rf][A-Za-z]*|--recursive|--force)", re.I)),
    ("privilege", re.compile(r"(^|\s)(sudo|su)(\s|$)", re.I)),
    ("script-exec", re.compile(r"(^|\s)(bash|sh|python|perl|ruby|lua)(\s|$)", re.I)),
)

COMPOUND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("compound-separator", re.compile(r"(;|&&|\|\|)")),
    ("command-substitution", re.compile(r"(`|\$\()")),
    ("pipe-to-shell", re.compile(r"\|\s*(sh|bash|python|perl|ruby)\b", re.I)),
)

POLICY_DESCRIPTIONS = {
    "allow_prefixes": list(ALLOW_PREFIXES),
    "config_exit_commands": sorted(CONFIG_EXIT_COMMANDS),
    "dangerous_patterns": [
        {"rule": rule, "pattern": pattern.pattern}
        for rule, pattern in DANGEROUS_PATTERNS
    ],
    "compound_patterns": [
        {"rule": rule, "pattern": pattern.pattern}
        for rule, pattern in COMPOUND_PATTERNS
    ],
}

CONFIG_PROMPT_PATTERN = re.compile(
    r"(\(config[^\)]*\)#\s*$|\[.*\]\s*$|config-[A-Za-z0-9_-]+[#>]\s*$)",
    re.I,
)


def normalize_command(command: str) -> str:
    text = str(command or "").replace("\x00", "")
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def split_pasted_commands(command: str) -> list[str]:
    normalized = normalize_command(command)
    if not normalized:
        return []
    return [line.strip() for line in normalized.split("\n") if line.strip()]


class CommandPolicy:
    def __init__(self, allow_prefixes: tuple[str, ...] = ALLOW_PREFIXES) -> None:
        self.allow_prefixes = tuple(x.lower() for x in allow_prefixes)
        self.in_config_mode = False

    def observe_output(self, output: str) -> None:
        tail = str(output or "")[-1200:]
        if CONFIG_PROMPT_PATTERN.search(tail):
            self.in_config_mode = True

    def decide(self, command: str) -> PolicyDecision:
        normalized = normalize_command(command)
        if not normalized:
            return PolicyDecision(True, normalized, "empty line", "empty")

        lines = split_pasted_commands(normalized)
        if len(lines) > 1:
            decisions = [self.decide(line) for line in lines]
            bad = [d for d in decisions if not d.allowed]
            if bad:
                first = bad[0]
                return PolicyDecision(False, normalized, f"pasted command blocked: {first.reason}", first.rule)
            return PolicyDecision(True, normalized, "all pasted commands allowed", "paste")

        cmd = lines[0]
        low = cmd.lower()
        first_word = _first_word(low)

        if self.in_config_mode:
            if first_word in CONFIG_EXIT_COMMANDS:
                if first_word in {"quit", "exit", "end", "return"}:
                    self.in_config_mode = False
                return PolicyDecision(True, cmd, "config mode exit command allowed", "config-exit")
            return PolicyDecision(False, cmd, "only exit/end/quit are allowed while in config mode", "config-mode")

        for rule, pattern in COMPOUND_PATTERNS:
            if pattern.search(cmd):
                return PolicyDecision(False, cmd, f"compound or shell-like expression is not allowed ({rule})", rule)

        for rule, pattern in DANGEROUS_PATTERNS:
            if pattern.search(cmd):
                if rule in {"configure", "system-view"}:
                    self.in_config_mode = True
                return PolicyDecision(False, cmd, f"dangerous command is not allowed ({rule})", rule)

        if any(low == p or low.startswith(p + " ") for p in self.allow_prefixes):
            return PolicyDecision(True, cmd, "read-only command allowed", "allow-prefix")

        return PolicyDecision(False, cmd, "command is not in the read-only allow list", "default-deny")


def _first_word(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return parts[0].lower() if parts else ""
