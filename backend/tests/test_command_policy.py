from app.models.schemas import CommandPolicy
from app.services.command_policy import CommandPolicyEngine, default_command_policy


def test_default_command_policy_includes_existing_block_and_execute_rules():
    policy = default_command_policy()

    assert "reload" in [item.lower() for item in policy.blocked_patterns]
    assert "show " in [item.lower() for item in policy.executable_patterns]
    assert "configure terminal" in [item.lower() for item in policy.executable_patterns]


def test_unknown_command_requires_confirmation():
    policy = default_command_policy()
    engine = CommandPolicyEngine()

    decision = engine.evaluate("totally-unknown-command", policy)

    assert decision.result == "needs_confirmation"
    assert "confirmation" in decision.reason.lower()


def test_legality_precheck_blocks_shell_like_operators_when_enabled():
    policy = default_command_policy()
    policy.legality_check_enabled = True
    engine = CommandPolicyEngine()

    decision = engine.evaluate("show version && reload", policy)

    assert decision.result == "blocked"
    assert "legality pre-check failed" in decision.reason.lower()


def test_legality_precheck_can_be_disabled():
    policy = CommandPolicy(
        blocked_patterns=[],
        executable_patterns=["show "],
        legality_check_enabled=False,
    )
    engine = CommandPolicyEngine()

    decision = engine.evaluate("show version && reload", policy)

    assert decision.result == "allowed"
