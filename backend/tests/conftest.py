from __future__ import annotations

import pytest

from app.api import routes


@pytest.fixture(autouse=True)
def reset_global_store(tmp_path, monkeypatch):
    policy_path = tmp_path / "command_policy.json"
    session_store_path = tmp_path / "session_store.json"
    monkeypatch.setenv("NETOPS_COMMAND_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("NETOPS_SESSION_STORE_PATH", str(session_store_path))

    routes.store.sessions.clear()
    routes.store.messages.clear()
    routes.store.commands.clear()
    routes.store.evidences.clear()
    routes.store.summary.clear()
    routes.store.ai_context.clear()
    routes.store.trace_steps.clear()
    routes.store.command_policy_path = policy_path
    routes.store.session_store_path = session_store_path
    routes.store.reset_command_policy()
    diagnoser = routes.orchestrator.deepseek_diagnoser
    diagnoser.config_path = tmp_path / "llm_config.json"
    diagnoser.api_key = ""
    diagnoser.base_url = diagnoser.default_base_url
    diagnoser.model = diagnoser.default_model
    routes.orchestrator.allow_simulation = True
    yield
