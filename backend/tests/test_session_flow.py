from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.schemas import IncidentSummary, Message, SOPStatus, SOPUpsertRequest


client = TestClient(app)


def _seed_test_sop(record_id: str = "test_history_ospf_flap"):
    return routes.sop_archive.upsert_record(
        record_id,
        SOPUpsertRequest(
            topic_key="test-history-ospf-flap",
            topic_name="测试 OSPF 历史抖动取证",
            name="测试 OSPF 历史抖动取证",
            summary="用于测试的 OSPF 历史抖动取证 SOP。",
            usage_hint="仅供测试匹配与引用。",
            trigger_keywords=["ospf", "闪断", "历史"],
            vendor_tags=["huawei_like"],
            version_signatures=[],
            preconditions=[],
            anti_conditions=[],
            evidence_goals=["协议事件日志"],
            key_steps=[],
            decision_points=[],
            command_templates=[],
            fallback_commands=[],
            expected_findings=[],
            source_run_ids=[],
            generated_by_model="test",
            generated_by_prompt_version="test",
            review_notes="test",
        ),
        status=SOPStatus.published,
        version=1,
    )


class ScriptedDiagnoser:
    enabled = True
    api_key = "scripted-test-key"
    base_url = "https://api.deepseek.com"
    model = "deepseek-chat"

    async def propose_next_step(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        conversation_history=None,
    ):
        if iteration == 1:
            return {
                "decision": "run_command",
                "title": "接口基础检查",
                "command": "show ip interface brief",
                "reason": "收集接口状态",
            }
        if iteration == 2:
            return {
                "decision": "run_command",
                "title": "高风险修复命令",
                "command": "configure terminal ; interface Gi0/0/1 ; shutdown",
                "reason": "验证高风险拦截",
            }
        return {
            "decision": "final",
            "root_cause": "AI summary placeholder",
            "impact_scope": "AI impact placeholder",
            "recommendation": "AI recommendation placeholder",
            "confidence": 0.5,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return IncidentSummary(
            session_id=session.id,
            root_cause="AI summary placeholder",
            impact_scope="AI impact placeholder",
            recommendation="AI recommendation placeholder",
            confidence=0.5,
            evidence_refs=[],
        )

    def configure(
        self,
        *,
        api_key=None,
        base_url=None,
        model=None,
        failover_enabled=None,
        model_candidates=None,
        batch_execution_enabled=None,
    ):
        if api_key is not None:
            self.api_key = api_key
            self.enabled = bool(api_key)
        if base_url:
            self.base_url = base_url
        if model:
            self.model = model

    def delete_saved_config(self):
        self.api_key = ""
        self.enabled = False
        self.base_url = "https://api.deepseek.com"
        self.model = "deepseek-chat"

    def status(self):
        return {
            "enabled": bool(self.api_key),
            "base_url": self.base_url,
            "model": self.model,
        }

    def prompt_strategy(self):
        return {
            "enabled": bool(self.api_key),
            "base_url": self.base_url,
            "model": self.model,
            "prompts": {
                "next_step_history": "history prompt",
                "next_step_default": "default prompt",
                "summary_primary": "primary prompt",
            },
        }


class FollowupSummaryDiagnoser(ScriptedDiagnoser):
    async def propose_next_step(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        conversation_history=None,
    ):
        if iteration == 1:
            return {
                "decision": "final",
                "root_cause": f"summary for {user_problem}",
                "impact_scope": "impact",
                "recommendation": "next",
                "confidence": 0.6,
                "evidence_refs": [],
            }
        return {
            "decision": "final",
            "root_cause": f"summary for {user_problem}",
            "impact_scope": "impact",
            "recommendation": "next",
            "confidence": 0.6,
            "evidence_refs": [],
        }


@pytest.fixture(autouse=True)
def use_scripted_diagnoser():
    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = ScriptedDiagnoser()
    yield
    routes.orchestrator.deepseek_diagnoser = original


def _create_session(automation_level: str = "assisted", operation_mode: str = "diagnosis") -> str:
    payload = {
        "device": {
            "host": "192.168.0.88",
            "protocol": "ssh",
            "vendor": "huawei_like",
        },
        "automation_level": automation_level,
        "operation_mode": operation_mode,
    }
    response = client.post("/v1/sessions", json=payload)
    assert response.status_code == 200
    return response.json()["id"]


def test_list_sessions_endpoint_returns_created_sessions():
    sid_a = _create_session("assisted")
    sid_b = _create_session("read_only")

    response = client.get("/v1/sessions")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    ids = [item["id"] for item in payload]
    assert sid_a in ids
    assert sid_b in ids
    for item in payload:
        assert "host" in item
        assert "protocol" in item
        assert "operation_mode" in item
        assert "password" not in item


def _stream_message(session_id: str, content: str) -> str:
    with client.stream("POST", f"/v1/sessions/{session_id}/messages", json={"content": content}) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def _internal() -> dict[str, str]:
    return {"X-Internal-UI": "1"}


def test_read_only_session_blocks_risky_execution_but_keeps_read_only_steps():
    session_id = _create_session("read_only")
    body = _stream_message(session_id, "请自动修复接口故障")

    assert "message_ack" in body
    assert "command_completed" in body
    assert "command_blocked" in body
    assert "final_summary" in body

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    assert timeline.status_code == 200
    data = timeline.json()

    statuses = {command["status"] for command in data["commands"]}
    assert "blocked" in statuses
    assert "succeeded" in statuses
    assert "pending_confirm" not in statuses
    assert data["summary"]["root_cause"] == "AI summary placeholder"


def test_assisted_session_requires_confirmation_for_non_whitelisted_high_risk_commands():
    session_id = _create_session("assisted", "config")
    body = _stream_message(session_id, "请自动修复接口故障")

    assert "command_pending_confirmation" in body

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    data = timeline.json()
    assert timeline.status_code == 200
    assert any(command["status"] == "succeeded" for command in data["commands"])
    assert any(command["status"] == "pending_confirm" for command in data["commands"])
    assert len(data["evidences"]) >= 1


def test_followup_question_replaces_previous_summary():
    session_id = _create_session("assisted", "diagnosis")
    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = FollowupSummaryDiagnoser()
    try:
        first = _stream_message(session_id, "先看 ospf 状态")
        assert "final_summary" in first
        first_timeline = client.get(f"/v1/sessions/{session_id}/timeline")
        assert first_timeline.status_code == 200
        assert "先看 ospf 状态" in first_timeline.json()["summary"]["root_cause"]

        second = _stream_message(session_id, "现在改查 lldp 邻居")
        assert "final_summary" in second
        second_timeline = client.get(f"/v1/sessions/{session_id}/timeline")
        assert second_timeline.status_code == 200
        summary = second_timeline.json()["summary"]["root_cause"]
        assert "现在改查 lldp 邻居" in summary
        assert "先看 ospf 状态" not in summary
    finally:
        routes.orchestrator.deepseek_diagnoser = original


def test_followup_question_replaces_previous_assistant_final_message():
    session_id = _create_session("assisted", "diagnosis")
    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = FollowupSummaryDiagnoser()
    try:
        _stream_message(session_id, "先看 ospf 状态")
        _stream_message(session_id, "现在改查 lldp 邻居")

        timeline = client.get(f"/v1/sessions/{session_id}/timeline")
        assert timeline.status_code == 200
        data = timeline.json()

        assistant_messages = [item["content"] for item in data["messages"] if item["role"] == "assistant"]
        assert assistant_messages, "expected assistant final summary messages"
        latest_assistant = assistant_messages[-1]
        assert "现在改查 lldp 邻居" in latest_assistant
        assert "先看 ospf 状态" not in latest_assistant
    finally:
        routes.orchestrator.deepseek_diagnoser = original


def test_full_auto_session_executes_without_confirmation():
    session_id = _create_session("full_auto", "config")
    body = _stream_message(session_id, "请自动修复接口故障")

    assert "command_pending_confirmation" not in body
    assert "command_blocked" not in body

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    data = timeline.json()

    statuses = {command["status"] for command in data["commands"]}
    assert statuses == {"succeeded"}
    assert len(data["evidences"]) >= 3


def test_llm_config_endpoint_enables_and_disables_runtime_key():
    status_before = client.get("/v1/llm/status")
    assert status_before.status_code == 200
    assert status_before.json()["enabled"] is True

    enable = client.post("/v1/llm/config", json={"api_key": "sk-test-runtime"})
    assert enable.status_code == 200
    assert enable.json()["enabled"] is True

    status_after = client.get("/v1/llm/status")
    assert status_after.status_code == 200
    assert status_after.json()["enabled"] is True

    disable = client.post("/v1/llm/config", json={"api_key": ""})
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False


def test_llm_config_delete_endpoint_clears_saved_key():
    enable = client.post("/v1/llm/config", json={"api_key": "sk-to-delete"})
    assert enable.status_code == 200
    assert enable.json()["enabled"] is True

    deleted = client.delete("/v1/llm/config")
    assert deleted.status_code == 200
    assert deleted.json()["enabled"] is False


def test_llm_prompt_policy_endpoint_returns_visible_templates():
    response = client.get("/v1/llm/prompt-policy")
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "deepseek-chat"
    assert "prompts" in payload
    assert payload["prompts"]["next_step_default"] == "default prompt"


def test_command_policy_endpoint_can_view_and_update_rules():
    initial = client.get("/v1/command-policy")
    assert initial.status_code == 200
    before = initial.json()
    assert "blocked_patterns" in before
    assert "executable_patterns" in before

    updated = client.put(
        "/v1/command-policy",
        json={
            "blocked_patterns": ["reload", "factory-reset", "custom-block"],
            "executable_patterns": ["show ", "display ", "custom-allow"],
            "legality_check_enabled": False,
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert "custom-block" in payload["blocked_patterns"]
    assert "custom-allow" in payload["executable_patterns"]
    assert payload["legality_check_enabled"] is False

    reset = client.post("/v1/command-policy/reset")
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert "custom-block" not in reset_payload["blocked_patterns"]
    assert "show " in [item.lower() for item in reset_payload["executable_patterns"]]
    assert reset_payload["legality_check_enabled"] is True


def test_command_capability_endpoint_crud():
    initial = client.get("/v1/command-capability")
    assert initial.status_code == 200
    assert initial.json() == []

    created = client.put(
        "/v1/command-capability",
        json={
            "scope_type": "version",
            "version_signature": "huawei|ne40e|8.180",
            "protocol": "ssh",
            "command_key": "show version",
            "action": "rewrite",
            "rewrite_to": "display version",
            "reason_text": "learned from retry",
            "source": "manual",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["command_key"] == "show version"
    assert payload["action"] == "rewrite"
    rule_id = payload["id"]

    listed = client.get("/v1/command-capability", params={"version_signature": "huawei|ne40e|8.180"})
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == rule_id

    disabled = client.put(
        "/v1/command-capability",
        json={
            "id": rule_id,
            "scope_type": "version",
            "version_signature": "huawei|ne40e|8.180",
            "protocol": "ssh",
            "command_key": "show version",
            "action": "rewrite",
            "rewrite_to": "display version",
            "source": "manual",
            "enabled": False,
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    removed = client.delete(f"/v1/command-capability/{rule_id}")
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True

    reset = client.post("/v1/command-capability/reset", json={"version_signature": "huawei|ne40e|8.180"})
    assert reset.status_code == 200
    assert reset.json()["remaining"] == 0


def test_risk_policy_endpoint_can_view_and_update_rules():
    initial = client.get("/v1/risk-policy")
    assert initial.status_code == 200
    before = initial.json()
    assert "high_risk_patterns" in before
    assert "medium_risk_patterns" in before
    assert "shutdown" in [item.lower() for item in before["high_risk_patterns"]]
    assert "clear " in [item.lower() for item in before["high_risk_patterns"]]

    updated = client.put(
        "/v1/risk-policy",
        json={
            "high_risk_patterns": ["shutdown", "custom-high"],
            "medium_risk_patterns": ["debug", "custom-medium"],
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert "custom-high" in payload["high_risk_patterns"]
    assert "custom-medium" in payload["medium_risk_patterns"]

    reset = client.post("/v1/risk-policy/reset")
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert "custom-high" not in [item.lower() for item in reset_payload["high_risk_patterns"]]
    assert "shutdown" in [item.lower() for item in reset_payload["high_risk_patterns"]]


def test_service_trace_endpoint_returns_step_timings():
    session_id = _create_session("assisted")
    _stream_message(session_id, "请检查接口状态并分析")

    trace = client.get(f"/v1/sessions/{session_id}/trace")
    assert trace.status_code == 200
    payload = trace.json()
    assert payload["session_id"] == session_id
    assert isinstance(payload["steps"], list)
    assert len(payload["steps"]) > 0
    assert any(step["step_type"] == "llm_plan" for step in payload["steps"])
    assert any(step["step_type"] in {"command_execution", "command_confirm_execution"} for step in payload["steps"])
    for step in payload["steps"]:
        if step.get("status") != "running":
            assert step.get("completed_at") is not None


def test_service_trace_records_ai_sop_reference_when_plan_mentions_archive():
    _seed_test_sop()
    class SopReferencingDiagnoser(ScriptedDiagnoser):
        async def propose_next_step(
            self,
            *,
            session,
            user_problem: str,
            commands,
            evidences,
            iteration: int,
            max_iterations: int,
            conversation_history=None,
        ):
            if iteration > 1:
                return {
                    "decision": "final",
                    "root_cause": "SOP trace placeholder",
                    "impact_scope": "trace test",
                    "recommendation": "done",
                    "confidence": 0.5,
                    "evidence_refs": [],
                }
            return {
                "decision": "run_command",
                "title": "历史 OSPF 抖动取证",
                "command": "display ospf peer",
                "reason": "引用 test_history_ospf_flap SOP，先确认邻接状态与最近抖动线索。",
            }

    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = SopReferencingDiagnoser()
    try:
        session_id = _create_session("assisted")
        _stream_message(session_id, "查一下上次 OSPF 闪断的原因")
        trace = client.get(f"/v1/sessions/{session_id}/trace")
        assert trace.status_code == 200
        steps = trace.json()["steps"]
        assert any("AI 引用 SOP 档案" in str(step.get("title", "")) for step in steps)
    finally:
        routes.orchestrator.deepseek_diagnoser = original


def test_stop_session_endpoint_appends_stop_message_and_returns_status():
    session_id = _create_session("assisted")

    stopped = client.post(f"/v1/sessions/{session_id}/stop")
    assert stopped.status_code == 200
    payload = stopped.json()
    assert payload["session_id"] == session_id
    assert payload["stop_requested"] is True
    assert "message" in payload

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    assert timeline.status_code == 200
    messages = timeline.json()["messages"]
    assert any(msg["role"] == "system" and "会话已手动停止" in msg["content"] for msg in messages)


def test_session_store_persists_history_across_store_reinit(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_SESSION_STORE_PATH", str(tmp_path / "session_store.json"))
    monkeypatch.setenv("NETOPS_COMMAND_POLICY_PATH", str(tmp_path / "command_policy.json"))
    monkeypatch.setenv("NETOPS_RISK_POLICY_PATH", str(tmp_path / "risk_policy.json"))
    monkeypatch.setenv("NETOPS_COMMAND_CAPABILITY_SNAPSHOT_PATH", str(tmp_path / "command_capability_snapshot.json"))
    monkeypatch.setenv("NETOPS_COMMAND_CAPABILITY_WAL_PATH", str(tmp_path / "command_capability.wal"))

    from app.services.store import InMemoryStore
    from app.models.schemas import SessionCreateRequest, DeviceTarget

    store = InMemoryStore()
    created = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.101",
                protocol="ssh",
            )
        )
    )
    store.add_message(Message(session_id=created.id, role="user", content="test history"))

    reloaded = InMemoryStore()
    items = reloaded.list_session_items()
    assert any(item.id == created.id for item in items)
    assert len(reloaded.list_messages(created.id)) == 1


def test_update_session_credentials_and_timeline_hides_secrets():
    session_id = _create_session("assisted")

    updated = client.patch(
        f"/v1/sessions/{session_id}/credentials",
        json={
            "username": "zhangwei",
            "password": "Admin@123",
            "jump_host": "10.0.0.10",
            "jump_port": 22,
            "jump_username": "jump-user",
            "jump_password": "jump-pass",
        },
    )
    assert updated.status_code == 200

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    assert timeline.status_code == 200
    session = timeline.json()["session"]
    assert session["device"]["username"] is None
    assert session["device"]["password"] is None
    assert session["device"]["jump_username"] is None
    assert session["device"]["jump_password"] is None


def test_legacy_v1_read_endpoints_match_unified_run_endpoints():
    session_id = _create_session("assisted", "config")
    _stream_message(session_id, "请自动修复接口故障")
    run_id = f"run_s:{session_id}"

    legacy_timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    assert legacy_timeline.status_code == 200
    unified_timeline = client.get(f"/api/runs/{run_id}/timeline", headers=_internal())
    assert unified_timeline.status_code == 200

    legacy_timeline_payload = legacy_timeline.json()
    unified_timeline_payload = unified_timeline.json()["timeline"]
    assert legacy_timeline_payload == unified_timeline_payload

    legacy_trace = client.get(f"/v1/sessions/{session_id}/trace")
    assert legacy_trace.status_code == 200
    unified_trace = client.get(f"/api/runs/{run_id}/trace", headers=_internal())
    assert unified_trace.status_code == 200
    assert legacy_trace.json() == unified_trace.json()

    legacy_export = client.post(f"/v1/sessions/{session_id}/export", json={"format": "markdown"})
    assert legacy_export.status_code == 200
    unified_export = client.post(
        f"/api/runs/{run_id}/export",
        json={"format": "markdown"},
        headers=_internal(),
    )
    assert unified_export.status_code == 200
    assert legacy_export.json() == unified_export.json()
