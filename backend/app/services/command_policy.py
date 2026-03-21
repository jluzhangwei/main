from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from app.models.schemas import CommandPolicy
from app.services.risk_engine import RiskEngine


@dataclass
class CommandPolicyDecision:
    result: Literal["blocked", "allowed", "needs_confirmation"]
    reason: str = ""
    matched_pattern: str = ""


def default_command_policy() -> CommandPolicy:
    risk = RiskEngine()
    executable_patterns = list(
        dict.fromkeys(
            [
                *risk.readonly_prefixes,
                "enable",
                "return",
                "exit",
                "end",
            ]
        )
    )
    # Keep default allow-list conservative: high-risk config commands are opt-in.
    blocked_patterns = list(dict.fromkeys([*risk.hard_block_patterns]))
    return CommandPolicy(
        blocked_patterns=blocked_patterns,
        executable_patterns=executable_patterns,
        legality_check_enabled=True,
    )


class CommandPolicyEngine:
    def evaluate(self, command: str, policy: CommandPolicy) -> CommandPolicyDecision:
        normalized = command.strip().lower()
        if not normalized:
            return CommandPolicyDecision(
                result="blocked",
                reason="Command is empty.",
            )

        if policy.legality_check_enabled:
            legal, reason = self._validate_legality(command)
            if not legal:
                return CommandPolicyDecision(
                    result="blocked",
                    reason=f"Command legality pre-check failed: {reason}",
                )

        for pattern in policy.blocked_patterns:
            if self._matches_pattern(normalized, pattern):
                return CommandPolicyDecision(
                    result="blocked",
                    reason=f"Command matches blocked rule: {pattern}",
                    matched_pattern=pattern,
                )

        for pattern in policy.executable_patterns:
            if self._matches_pattern(normalized, pattern):
                return CommandPolicyDecision(
                    result="allowed",
                    reason="Command matched executable rule.",
                    matched_pattern=pattern,
                )

        return CommandPolicyDecision(
            result="needs_confirmation",
            reason="Command did not match executable rules; operator confirmation is required.",
        )

    def _validate_legality(self, command: str) -> tuple[bool, str]:
        compact = command.strip()
        if len(compact) > 512:
            return False, "command length exceeds 512"
        if any(ch in compact for ch in ("\n", "\r", "\x00")):
            return False, "command contains unsupported control characters"
        if "&&" in compact or "||" in compact or "$(" in compact or "`" in compact:
            return False, "command contains unsupported shell-like operators"

        parts = [part.strip() for part in compact.split(";")]
        if any(not part for part in parts):
            return False, "empty command segment detected"

        return True, ""

    def _matches_pattern(self, command: str, pattern: str) -> bool:
        normalized_pattern = pattern.strip().lower()
        if not normalized_pattern:
            return False

        # For plain tokens, use token-boundary matching to avoid accidental substring hits
        # such as "show" matching "showdown".
        if re.fullmatch(r"[a-z0-9_]+", normalized_pattern):
            return (
                re.search(
                    rf"(^|[^a-z0-9_]){re.escape(normalized_pattern)}([^a-z0-9_]|$)",
                    command,
                )
                is not None
            )

        return normalized_pattern in command
