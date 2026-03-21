from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.schemas import IncidentSummary


client = TestClient(app)


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

    def configure(self, *, api_key=None, base_url=None, model=None):
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


@pytest.fixture(autouse=True)
def use_scripted_diagnoser():
    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = ScriptedDiagnoser()
    yield
    routes.orchestrator.deepseek_diagnoser = original


def _create_session(automation_level: str = "assisted") -> str:
    payload = {
        "device": {
            "host": "192.168.0.88",
            "protocol": "ssh",
            "vendor": "huawei_like",
        },
        "automation_level": automation_level,
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


def test_assisted_session_prompts_for_confirmation_then_executes_on_approve():
    session_id = _create_session("assisted")
    body = _stream_message(session_id, "请自动修复接口故障")

    assert "command_pending_confirmation" in body

    timeline = client.get(f"/v1/sessions/{session_id}/timeline")
    pending = [command for command in timeline.json()["commands"] if command["status"] == "pending_confirm"]
    assert len(pending) >= 1
    batch_ids = {command.get("batch_id") for command in pending}
    assert len(batch_ids) == 1

    confirm = client.post(f"/v1/sessions/{session_id}/commands/{pending[0]['id']}/confirm", json={"approved": True})
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "succeeded"

    timeline_after = client.get(f"/v1/sessions/{session_id}/timeline")
    data = timeline_after.json()
    assert any(command["status"] == "succeeded" for command in data["commands"])
    assert len(data["evidences"]) >= 1


def test_full_auto_session_executes_without_confirmation():
    session_id = _create_session("full_auto")
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
