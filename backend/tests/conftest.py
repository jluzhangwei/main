from __future__ import annotations

import pytest

from app.api import routes
from app.services.sop_archive import SOPArchive
from app.services.command_capability_store import CommandCapabilityStore
from app.services.job_orchestrator_v2 import JobV2Orchestrator


@pytest.fixture(autouse=True)
def reset_global_store(tmp_path, monkeypatch):
    policy_path = tmp_path / "command_policy.json"
    risk_policy_path = tmp_path / "risk_policy.json"
    session_store_path = tmp_path / "session_store.json"
    capability_snapshot_path = tmp_path / "command_capability_snapshot.json"
    capability_wal_path = tmp_path / "command_capability.wal"
    v2_state_path = tmp_path / "v2_state.json"
    monkeypatch.setenv("NETOPS_COMMAND_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("NETOPS_RISK_POLICY_PATH", str(risk_policy_path))
    monkeypatch.setenv("NETOPS_SESSION_STORE_PATH", str(session_store_path))
    monkeypatch.setenv("NETOPS_COMMAND_CAPABILITY_SNAPSHOT_PATH", str(capability_snapshot_path))
    monkeypatch.setenv("NETOPS_COMMAND_CAPABILITY_WAL_PATH", str(capability_wal_path))
    monkeypatch.setenv("NETOPS_V2_STATE_PATH", str(v2_state_path))
    monkeypatch.setenv("NETOPS_SOP_SNAPSHOT_PATH", str(tmp_path / "sop_snapshot.json"))
    monkeypatch.setenv("NETOPS_SOP_WAL_PATH", str(tmp_path / "sop.wal"))
    monkeypatch.setenv("NETOPS_SIMULATION_ONLY", "1")

    routes.store.sessions.clear()
    routes.store.messages.clear()
    routes.store.commands.clear()
    routes.store.evidences.clear()
    routes.store.summary.clear()
    routes.store.ai_context.clear()
    routes.store.trace_steps.clear()
    routes.store.command_policy_path = policy_path
    routes.store.risk_policy_path = risk_policy_path
    routes.store.session_store_path = session_store_path
    routes.store.command_capability_snapshot_path = capability_snapshot_path
    routes.store.command_capability_wal_path = capability_wal_path
    routes.store.command_capability_store = CommandCapabilityStore(
        snapshot_path=capability_snapshot_path,
        wal_path=capability_wal_path,
        compact_interval_seconds=3600,
        compact_event_threshold=100000,
    )
    routes.store.reset_command_policy()
    routes.store.reset_risk_policy()
    routes.store.reset_command_capability_rules()
    diagnoser = routes.orchestrator.deepseek_diagnoser
    diagnoser.config_path = tmp_path / "llm_config.json"
    diagnoser.api_key = ""
    diagnoser.base_url = diagnoser.default_base_url
    diagnoser.model = diagnoser.default_model
    routes.orchestrator.allow_simulation = True
    routes.sop_archive = SOPArchive()
    routes.orchestrator.sop_archive = routes.sop_archive
    routes.orchestrator_v2 = JobV2Orchestrator(routes.store, allow_simulation=True)
    yield
