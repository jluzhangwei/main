from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import AutomationLevel, RiskLevel


@dataclass
class RiskDecision:
    risk_level: RiskLevel
    allowed: bool
    requires_confirmation: bool
    reason: str = ""


class RiskEngine:
    readonly_prefixes = (
        "show ",
        "display ",
        "ping ",
        "traceroute ",
        "tracert ",
    )

    hard_block_patterns = (
        "write erase",
        "erase startup-config",
        "factory-reset",
        "format",
        "delete flash",
        "reload",
    )

    high_risk_patterns = (
        "configure terminal",
        "conf t",
        "interface ",
        "shutdown",
        "no shutdown",
        "router bgp",
        "router ospf",
        "ip route ",
        "commit",
        "save",
    )

    medium_risk_patterns = (
        "clear arp",
        "clear ip route",
        "debug",
        "reset",
    )

    def classify(self, command: str) -> RiskLevel:
        normalized = command.lower()

        if self.is_read_only_command(command):
            return RiskLevel.low

        if any(pattern in normalized for pattern in self.high_risk_patterns):
            return RiskLevel.high
        if any(pattern in normalized for pattern in self.medium_risk_patterns):
            return RiskLevel.medium
        return RiskLevel.low

    def is_read_only_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        return normalized.startswith(self.readonly_prefixes)

    def decide(self, command: str, automation_level: AutomationLevel) -> RiskDecision:
        normalized = command.lower()

        if any(pattern in normalized for pattern in self.hard_block_patterns):
            return RiskDecision(
                risk_level=RiskLevel.high,
                allowed=False,
                requires_confirmation=False,
                reason="Command is hard-blocked by safety policy.",
            )

        risk = self.classify(command)

        if automation_level == AutomationLevel.read_only:
            if not self.is_read_only_command(command):
                return RiskDecision(
                    risk_level=risk,
                    allowed=False,
                    requires_confirmation=False,
                    reason="Read-only mode blocks non-read commands.",
                )
            return RiskDecision(risk_level=risk, allowed=True, requires_confirmation=False)

        if automation_level == AutomationLevel.assisted:
            return RiskDecision(
                risk_level=risk,
                allowed=True,
                requires_confirmation=(risk == RiskLevel.high),
                reason="High-risk commands require approval in assisted mode." if risk == RiskLevel.high else "",
            )

        return RiskDecision(risk_level=risk, allowed=True, requires_confirmation=False)
