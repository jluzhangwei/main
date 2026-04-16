from __future__ import annotations

import asyncio
import json
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.schemas import IncidentSummary, Job, JobDevice, JobEvent, JobPhase, JobStatus, JobTimelineResponse, SOPStatus, SOPUpsertRequest
from app.services.unified_run_service import UnifiedRunService


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
            vendor_tags=["huawei"],
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

    async def extract_sop_draft(self, *, run_payload):
        return {
            "name": "接口管理性关闭排查",
            "summary": "从历史会话提炼的接口 shutdown 排查方法。",
            "usage_hint": "仅在接口被管理性关闭或疑似 shutdown 时参考。",
            "trigger_keywords": ["接口", "shutdown", "disable"],
            "vendor_tags": ["arista"],
            "version_signatures": ["veos"],
            "preconditions": ["接口"],
            "anti_conditions": ["物理断链"],
            "evidence_goals": ["确认接口配置中存在 shutdown"],
            "command_templates": [
                {"vendor": "arista", "commands": ["show running-config interfaces", "show interfaces status"]},
            ],
            "fallback_commands": ["show interfaces Ethernet2"],
            "expected_findings": ["running-config 中命中 shutdown"],
            "review_notes": "自动提炼草稿",
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
    original_single = routes.orchestrator.deepseek_diagnoser
    original_multi = routes.orchestrator_v2.deepseek_diagnoser
    scripted = ScriptedDiagnoser()
    routes.orchestrator.deepseek_diagnoser = scripted
    routes.orchestrator_v2.deepseek_diagnoser = scripted

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
    routes.orchestrator.deepseek_diagnoser = original_single
    routes.orchestrator_v2.deepseek_diagnoser = original_multi


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


def test_single_and_multi_share_same_diagnoser_instance():
    assert routes.orchestrator_v2.deepseek_diagnoser is routes.orchestrator.deepseek_diagnoser


def test_multi_trace_finalizes_instant_running_session_control_steps():
    service = UnifiedRunService(routes.orchestrator.store, routes.orchestrator, routes.orchestrator_v2, routes.sop_archive)
    job = Job(
        id="job-running-history",
        problem="check ospf state",
        status=JobStatus.completed,
        phase=JobPhase.conclude,
        devices=[JobDevice(id="dev-1", host="192.0.2.10", protocol="ssh")],
    )
    timeline = JobTimelineResponse(
        job=job,
        events=[
            JobEvent(job_id=job.id, seq_no=1, event_type="phase_changed", payload={"phase": "collect", "status": "running"}),
            JobEvent(job_id=job.id, seq_no=2, event_type="device_collect_started", payload={"device_id": "dev-1", "host": "192.0.2.10"}),
            JobEvent(job_id=job.id, seq_no=3, event_type="job_completed", payload={"mode": "diagnosis"}),
        ],
    )

    trace = service._normalize_multi_trace(timeline)
    statuses = {(step.title, step.status) for step in trace.steps}

    assert ("阶段切换：采集", "succeeded") in statuses
    assert ("[192.0.2.10] 开始设备采集", "succeeded") in statuses


def test_normalize_sop_upsert_request_generalizes_incident_specific_objects():
    payload = routes._normalize_sop_upsert_request(
        {
            "topic_name": "LLDP互通但OSPF邻接不建立排查（华为VRP）",
            "topic_key": "lldp-ospf-adjacency-huawei",
            "name": "LLDP互通但OSPF邻接不建立排查（华为VRP）",
            "summary": "聚焦 Eth1/0/0 两端接口的 OSPF 不起邻现象",
            "usage_hint": "用于 192.168.0.102 与 192.168.0.103 之间 OSPF 邻接不建立",
            "preconditions": ["接口 Eth1/0/0 已知为互联口"],
            "key_steps": [
                {
                    "step_no": 1,
                    "title": "检查 Eth1/0/0 接口",
                    "goal": "验证 Eth1/0/0 是否具备起邻条件",
                    "commands": ["display interface Eth1/0/0", "display ospf interface Eth1/0/0"],
                    "expected_signals": ["Eth1/0/0 协议状态正常"],
                }
            ],
            "command_templates": [
                {"vendor": "Huawei VRP", "commands": ["display interface Eth1/0/0", "display ospf interface Eth1/0/0"]}
            ],
        },
        source_run_id="run_m:test",
    )

    assert "<接口>" in payload.summary
    assert payload.key_steps[0].commands == ["display interface <接口>", "display ospf interface <接口>"]
    assert payload.command_templates[0].commands == ["display interface <接口>", "display ospf interface <接口>"]
    assert payload.review_notes is not None
    assert "系统审查提示" in payload.review_notes


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
    assert run["problem"] == "检查接口并继续诊断"
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


def test_api_runs_multi_message_stream_creates_followup_run():
    created = client.post(
        "/api/runs",
        json={
            "problem": "检查两台设备 OSPF 状态",
            "automation_level": "assisted",
            "operation_mode": "diagnosis",
            "devices": [
                {
                    "host": "192.168.0.83",
                    "protocol": "ssh",
                    "vendor": "huawei",
                },
                {
                    "host": "192.168.0.84",
                    "protocol": "ssh",
                    "vendor": "huawei",
                },
            ],
        },
        headers=_internal(),
    )
    assert created.status_code == 200, created.text
    source_run_id = created.json()["id"]
    completed = _wait_run_status(source_run_id, {"completed", "failed", "cancelled"})
    assert completed.get("status") in {"completed", "failed", "cancelled"}

    body = _stream_run_message(source_run_id, "继续排查邻接与路由发布关系")
    assert "event: run_created" in body
    assert "event: completed" in body

    run_match = re.search(r'event: run_created\s+data: (\{.*\})', body)
    assert run_match, body
    payload = json.loads(run_match.group(1))
    followup_run_id = payload.get("run_id")
    assert isinstance(followup_run_id, str) and followup_run_id.startswith("run_m:")
    assert payload.get("source_run_id") == source_run_id

    followup = client.get(f"/api/runs/{followup_run_id}", headers=_internal())
    assert followup.status_code == 200, followup.text
    assert followup.json()["kind"] == "multi"
    assert followup.json()["problem"] == "继续排查邻接与路由发布关系"


def test_api_runs_single_patch_automation_and_credentials():
    created = client.post(
        "/api/runs",
        json={
            "automation_level": "assisted",
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
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    session_id = created.json()["source_id"]

    patched = client.patch(
        f"/api/runs/{run_id}",
        json={"automation_level": "full_auto"},
        headers=_internal(),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["automation_level"] == "full_auto"

    creds = client.patch(
        f"/api/runs/{run_id}/credentials",
        json={
            "username": "tester",
            "password": "secret",
            "jump_host": "10.0.0.1",
            "jump_username": "jumper",
        },
        headers=_internal(),
    )
    assert creds.status_code == 200, creds.text
    session = routes.store.get_session(session_id)
    assert session.automation_level.value == "full_auto"
    assert session.device.username == "tester"
    assert session.device.password == "secret"
    assert session.device.jump_host == "10.0.0.1"
    assert session.device.jump_username == "jumper"


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
    command_steps = [
        step for step in payload["trace"]
        if step.get("step_type") == "command_execution" and str(step.get("command_id") or "").startswith("v2cmd:")
    ]
    command_step_keys = {
        (
            step.get("command_id"),
            step.get("status"),
            step.get("started_at"),
            step.get("completed_at"),
        )
        for step in command_steps
    }
    assert len(command_steps) == len(command_step_keys)


def test_api_runs_trace_export_and_sop_library():
    _seed_test_sop()
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
    _wait_run_status(run_id, {"completed", "failed", "cancelled"})

    trace = client.get(f"/api/runs/{run_id}/trace", headers=_internal())
    assert trace.status_code == 200, trace.text
    trace_payload = trace.json()
    assert "steps" in trace_payload
    steps = trace_payload["steps"]
    titles = [str(step.get("title", "")) for step in steps]
    assert any("开始设备采集" in title for title in titles)
    assert any(str(step.get("id", "")).find(":evt:") >= 0 and "创建多设备任务" in str(step.get("title", "")) for step in steps)

    exported = client.post(f"/api/runs/{run_id}/export", json={"format": "markdown"}, headers=_internal())
    assert exported.status_code == 200, exported.text
    assert "content" in exported.json()

    sop = client.get("/api/sop-library?problem=上次ospf闪断", headers=_internal())
    assert sop.status_code == 200, sop.text
    payload = sop.json()
    assert payload["total"] >= 1
    assert payload["matched"]
    matched_ids = {item["id"] for item in payload["matched"]}
    assert "test_history_ospf_flap" in matched_ids


def test_api_sop_extract_and_published_update_creates_new_draft():
    created = client.post(
        "/api/runs",
        json={
            "problem": "检查接口 Eth2 disable 问题",
            "automation_level": "assisted",
            "operation_mode": "diagnosis",
            "devices": [
                {
                    "host": "192.168.0.102",
                    "protocol": "ssh",
                    "vendor": "arista_like",
                }
            ],
        },
        headers=_internal(),
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    _wait_run_status(run_id, {"completed", "waiting_approval"})

    extracted = client.post(
        "/api/sops/extract-from-run",
        json={"run_id": run_id},
        headers=_internal(),
    )
    assert extracted.status_code == 200, extracted.text
    draft = extracted.json()
    assert draft["status"] == "draft"
    assert draft["source_run_ids"]

    conflict = client.post(
        "/api/sops/extract-from-run",
        json={"run_id": run_id},
        headers=_internal(),
    )
    assert conflict.status_code == 409, conflict.text

    published = client.post(f"/api/sops/{draft['id']}/publish", headers=_internal())
    assert published.status_code == 200, published.text
    published_item = published.json()["item"]
    assert published_item["status"] == "published"

    updated = client.put(
        f"/api/sops/{draft['id']}",
        json={
            "name": "接口管理性关闭排查 v2",
            "summary": "发布后编辑应生成新的草稿版本。",
            "usage_hint": "草稿二次修订",
            "trigger_keywords": ["接口", "shutdown"],
            "vendor_tags": ["arista"],
            "version_signatures": ["veos"],
            "preconditions": ["接口"],
            "anti_conditions": [],
            "evidence_goals": ["确认 shutdown"],
            "command_templates": [{"vendor": "arista", "commands": ["show running-config interfaces"]}],
            "fallback_commands": [],
            "expected_findings": ["命中 shutdown"],
            "source_run_ids": published_item["source_run_ids"],
            "review_notes": "published -> new draft",
        },
        headers=_internal(),
    )
    assert updated.status_code == 200, updated.text
    new_draft = updated.json()
    assert new_draft["id"] != draft["id"]
    assert new_draft["status"] == "draft"
    assert new_draft["version"] == published_item["version"] + 1

    listed = client.get("/api/sops?status=draft", headers=_internal())
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == new_draft["id"] for item in listed.json()["items"])


def test_api_sop_extract_accepts_legacy_source_id():
    created = client.post(
        "/api/runs",
        json={
            "problem": "检查接口 disable 问题",
            "automation_level": "assisted",
            "operation_mode": "diagnosis",
            "devices": [{"host": "192.168.0.102", "protocol": "ssh", "vendor": "arista_like"}],
        },
        headers=_internal(),
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["source_id"]
    run_id = created.json()["id"]
    _wait_run_status(run_id, {"completed", "waiting_approval"})

    extracted = client.post(
        "/api/sops/extract-from-run",
        json={"run_id": source_id, "force": True},
        headers=_internal(),
    )
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["status"] == "draft"

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
