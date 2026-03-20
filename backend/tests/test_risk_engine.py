from app.models.schemas import AutomationLevel, RiskLevel
from app.services.risk_engine import RiskEngine


def test_high_risk_requires_confirmation_in_assisted_mode():
    engine = RiskEngine()

    decision = engine.decide("configure terminal ; interface Gi0/0/1 ; shutdown", AutomationLevel.assisted)

    assert decision.risk_level == RiskLevel.high
    assert decision.allowed is True
    assert decision.requires_confirmation is True


def test_read_only_blocks_non_read_commands():
    engine = RiskEngine()

    decision = engine.decide("configure terminal", AutomationLevel.read_only)

    assert decision.allowed is False
    assert "Read-only mode" in decision.reason


def test_full_auto_allows_medium_commands_without_confirmation():
    engine = RiskEngine()

    decision = engine.decide("clear arp", AutomationLevel.full_auto)

    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_show_interface_commands_remain_low_risk():
    engine = RiskEngine()

    decision = engine.decide("show ip interface brief", AutomationLevel.assisted)

    assert decision.risk_level == RiskLevel.low
    assert decision.allowed is True
    assert decision.requires_confirmation is False
