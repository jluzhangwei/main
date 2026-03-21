from app.models.schemas import CommandPolicy, CommandPolicyUpdateRequest, RiskPolicyUpdateRequest
from app.services.command_policy import CommandPolicyEngine, default_command_policy
from app.services.store import InMemoryStore


def test_default_command_policy_includes_existing_block_and_execute_rules():
    policy = default_command_policy()

    assert "reload" in [item.lower() for item in policy.blocked_patterns]
    assert "show " in [item.lower() for item in policy.executable_patterns]
    assert "configure terminal" not in [item.lower() for item in policy.executable_patterns]


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


def test_command_policy_persists_after_update(tmp_path, monkeypatch):
    policy_path = tmp_path / "command_policy.json"
    monkeypatch.setenv("NETOPS_COMMAND_POLICY_PATH", str(policy_path))

    store = InMemoryStore()
    updated = store.update_command_policy(
        CommandPolicyUpdateRequest(
            blocked_patterns=["reload", "factory-reset"],
            executable_patterns=["show ", "display ", "custom-allow"],
            legality_check_enabled=False,
        )
    )

    assert policy_path.exists() is True
    assert "custom-allow" in [item.lower() for item in updated.executable_patterns]

    reloaded = InMemoryStore()
    current = reloaded.get_command_policy()
    assert "custom-allow" in [item.lower() for item in current.executable_patterns]
    assert current.legality_check_enabled is False


def test_risk_policy_persists_after_update(tmp_path, monkeypatch):
    risk_policy_path = tmp_path / "risk_policy.json"
    monkeypatch.setenv("NETOPS_RISK_POLICY_PATH", str(risk_policy_path))

    store = InMemoryStore()
    updated = store.update_risk_policy(
        RiskPolicyUpdateRequest(
            high_risk_patterns=["shutdown", "custom-high"],
            medium_risk_patterns=["debug", "custom-medium"],
        )
    )

    assert risk_policy_path.exists() is True
    assert "custom-high" in [item.lower() for item in updated.high_risk_patterns]

    reloaded = InMemoryStore()
    current = reloaded.get_risk_policy()
    assert "custom-high" in [item.lower() for item in current.high_risk_patterns]
    assert "custom-medium" in [item.lower() for item in current.medium_risk_patterns]
