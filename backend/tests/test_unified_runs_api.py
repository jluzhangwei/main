from __future__ import annotations

import asyncio
import threading
import time

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

    def configure(self, **kwargs):
        return None

    def delete_saved_config(self):
        return None

    def status(self):
        return {
            "enabled": True,
            "base_url": self.base_url,
            "model": self.model,
        }

    def prompt_strategy(self):
        return {
            "enabled": True,
            "base_url": self.base_url,
            "model": self.model,
            "prompts": {},
        }


@pytest.fixture(autouse=True)
def use_scripted_diagnoser(monkeypatch):
    original = routes.orchestrator.deepseek_diagnoser
    routes.orchestrator.deepseek_diagnoser = ScriptedDiagnoser()

    def _launch_single_run_task(session_id: str, problem: str):
        coro = routes._consume_single_run(session_id, problem)
        thread = threading.Thread(target=lambda: asyncio.run(coro), name=f"test-run-{session_id}", daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(routes, "_launch_single_run_task", _launch_single_run_task)
    monkeypatch.setattr(
        routes.orchestrator_v2,
        "_baseline_collect_commands",
        lambda: [("版本探测", "show version"), ("接口摘要", "show ip interface brief"), ("权限探测", "show privilege")],
    )
    yield
    routes.orchestrator.deepseek_diagnoser = original


def _internal() -> dict[str, str]:
    return {"X-Internal-UI": "1"}


def _wait_run_status(run_id: str, expected: set[str], timeout_seconds: float = 8.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}", headers=_internal())
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        last_payload = payload
        if payload["status"] in expected:
            return payload
        time.sleep(0.2)
    return last_payload


def _stream_run_message(run_id: str, content: str) -> str:
    with client.stream("POST", f"/api/runs/{run_id}/messages", json={"content": content}, headers=_internal()) as response:
        assert response.status_code == 200, response.text
        return "".join(response.iter_text())


def _stream_run_events(run_id: str, *, timeout_chunks: int = 200) -> str:
    with client.stream("GET", f"/api/runs/{run_id}/events?from_seq=0", headers=_internal()) as response:
        assert response.status_code == 200, response.text
        chunks: list[str] = []
        for index, chunk in enumerate(response.iter_text()):
            chunks.append(chunk)
            if "event: completed" in chunk:
                break
            if index >= timeout_chunks:
                break
        return "".join(chunks)


def test_api_runs_single_create_and_reject_pending_action():
    payload = {
        "problem": "检查接口并继续诊断",
        "automation_level": "assisted",
        "operation_mode": "config",
        "devices": [
            {
                "host": "192.168.0.88",
                "protocol": "ssh",
                "vendor": "huawei_like",
            }
        ],
    }
    created = client.post("/api/runs", json=payload, headers=_internal())
    assert created.status_code == 200, created.text
    run = created.json()
    assert run["kind"] == "single"
    run_id = run["id"]

    waiting = _wait_run_status(run_id, {"waiting_approval", "completed"})
    assert waiting["status"] == "waiting_approval"
    assert waiting["pending_actions"] >= 1

    rejected = client.post(f"/api/runs/{run_id}/actions/reject", json={}, headers=_internal())
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["updated"] >= 1

    timeline = client.get(f"/api/runs/{run_id}/timeline", headers=_internal())
    assert timeline.status_code == 200, timeline.text
    payload = timeline.json()
    assert payload["run"]["kind"] == "single"
    assert isinstance(payload["trace"], list)
    assert "payload" in payload
    assert "timeline" in payload
    assert "service_trace" in payload


def test_api_runs_single_message_stream_uses_unified_run_endpoint():
    created = client.post(
        "/api/runs",
        json={
            "automation_level": "assisted",
            "operation_mode": "config",
            "devices": [
                {
                    "host": "192.168.0.88",
                    "protocol": "ssh",
                    "vendor": "huawei_like",
                }
            ],
        },
        headers=_internal(),
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]

    body = _stream_run_message(run_id, "请自动修复接口故障")
    assert "message_ack" in body
    assert "command_pending_confirmation" in body

    run = client.get(f"/api/runs/{run_id}", headers=_internal())
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "waiting_approval"


def test_api_runs_multi_create_list_and_timeline():
    payload = {
        "problem": "查一下两台设备的关联异常",
        "operation_mode": "diagnosis",
        "devices": [
            {"host": "192.0.2.10", "protocol": "api", "vendor": "unknown"},
            {"host": "192.0.2.11", "protocol": "api", "vendor": "unknown"},
        ],
    }
    created = client.post("/api/runs", json=payload, headers=_internal())
    assert created.status_code == 200, created.text
    run = created.json()
    assert run["kind"] == "multi"
    run_id = run["id"]

    listing = client.get("/api/runs", headers=_internal())
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert any(item["id"] == run_id for item in items)

    timeline = client.get(f"/api/runs/{run_id}/timeline", headers=_internal())
    assert timeline.status_code == 200, timeline.text
    payload = timeline.json()
    assert payload["run"]["kind"] == "multi"
    assert "job" in payload["payload"]
    assert isinstance(payload["trace"], list)
    assert "timeline" in payload
    assert "service_trace" in payload
    assert any(
        str(step.get("id", "")).find(":evt:") >= 0 and str(step.get("title", "")).startswith("阶段切换：")
        for step in payload["trace"]
    )


def test_api_runs_trace_export_and_sop_library():
    created = client.post(
        "/api/runs",
        json={
            "problem": "查一下上次 OSPF 闪断的原因",
            "operation_mode": "diagnosis",
            "devices": [
                {"host": "192.0.2.10", "protocol": "api", "vendor": "huawei"},
                {"host": "192.0.2.11", "protocol": "api", "vendor": "huawei"},
            ],
        },
        headers=_internal(),
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]

    trace = client.get(f"/api/runs/{run_id}/trace", headers=_internal())
    assert trace.status_code == 200, trace.text
    assert "steps" in trace.json()

    exported = client.post(f"/api/runs/{run_id}/export", json={"format": "markdown"}, headers=_internal())
    assert exported.status_code == 200, exported.text
    assert "content" in exported.json()

    sop = client.get("/api/sop-library?problem=上次ospf闪断", headers=_internal())
    assert sop.status_code == 200, sop.text
    payload = sop.json()
    assert payload["total"] >= 1
    assert any(item["id"] == "history_ospf_flap" for item in payload["matched"])


def test_api_runs_events_stream_for_single_and_multi():
    single = client.post(
        "/api/runs",
        json={
            "problem": "检查接口并继续诊断",
            "automation_level": "read_only",
            "operation_mode": "diagnosis",
            "devices": [
                {
                    "host": "192.168.0.88",
                    "protocol": "ssh",
                    "vendor": "huawei_like",
                }
            ],
        },
        headers=_internal(),
    )
    assert single.status_code == 200, single.text
    single_run_id = single.json()["id"]
    single_events = _stream_run_events(single_run_id)
    assert "event: trace_step" in single_events
    assert '"step_type"' in single_events
    assert "event: completed" in single_events

    multi = client.post(
        "/api/runs",
        json={
            "problem": "查一下两台设备的关联异常",
            "operation_mode": "diagnosis",
            "devices": [
                {"host": "192.0.2.10", "protocol": "api", "vendor": "unknown"},
                {"host": "192.0.2.11", "protocol": "api", "vendor": "unknown"},
            ],
        },
        headers=_internal(),
    )
    assert multi.status_code == 200, multi.text
    multi_run_id = multi.json()["id"]
    multi_events = _stream_run_events(multi_run_id)
    assert "event: trace_step" in multi_events
    assert "event: completed" in multi_events
