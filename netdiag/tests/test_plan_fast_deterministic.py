from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.diagnosis.models import PlannedCommand
from app.main import app


def _create_session(client: TestClient) -> str:
    start = (datetime.now() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    resp = client.post(
        "/api/netdiag/sessions",
        json={
            "question": "排查 192.168.0.88 端口 down 告警，定位根因",
            "fault_window": {"start_at": start, "end_at": end, "timezone": "Asia/Singapore"},
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "192.168.0.88",
                    "username": "u",
                    "password": "p",
                    "vendor_hint": "huawei",
                    "jump_mode": "direct",
                }
            ],
            "focus_goals": ["端口down", "日志关联", "根因定位"],
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["session"]["session_id"]
    mgr = app.state.diag_session_manager
    mgr.set_time_calibration(
        sid,
        [
            {
                "device_id": "dev-1",
                "device_ip": "192.168.0.88",
                "status": "success",
                "vendor": "huawei",
                "os_family": "huawei_vrp",
                "version": "8.180",
                "offset_seconds": -1.2,
            }
        ],
    )
    mgr.set_status(sid, "ready_for_next_probe")
    return sid


def test_plan_first_round_uses_fast_deterministic_and_skips_llm(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_run_planner_llm should not be called in fast deterministic first round")

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _should_not_call)

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={"max_commands": 4, "fast_plan_enabled": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 0
    rnd = body.get("round") or {}
    assert len(rnd.get("commands") or []) > 0
    assert len(rnd.get("commands") or []) <= 2
    assert "fast_deterministic" in str(rnd.get("planner_raw_output") or "")
    perf = body.get("performance") or {}
    assert int(perf.get("effective_max_commands") or 0) <= 2


def test_plan_with_follow_up_falls_back_to_llm(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _fake_llm(*_args, **_kwargs):
        called["llm"] += 1
        return (
            "llm summary",
            [
                PlannedCommand(
                    command_id="c1",
                    device_id="dev-1",
                    intent="system_log_recent",
                    command="display logbuffer",
                    reason="check logs",
                    expected_signal="Collect recent system logs around fault window.",
                    risk_level="read_only",
                    requires_approval=True,
                    approved=False,
                )
            ],
            "{\"engine\":\"llm\"}",
        )

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _fake_llm)

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "max_commands": 4,
            "fast_plan_enabled": True,
            "follow_up": "重点检查接口日志是否出现新的 flap",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 1
    rnd = body.get("round") or {}
    assert (rnd.get("planner_summary") or "").startswith("llm summary")


def test_plan_with_follow_up_and_unavailable_model_uses_deterministic_fallback(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_run_planner_llm should be skipped when no model route is ready")

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _should_not_call)
    monkeypatch.setattr(
        "app.routers.netdiag.load_gpt_config",
        lambda: {
            "provider": "chatgpt",
            "chatgpt_api_key": "",
            "deepseek_api_key": "",
            "qwen_api_key": "",
            "gemini_api_key": "",
            "nvidia_api_key": "",
            "chatgpt_model": "gpt-4.1-mini",
            "deepseek_model": "deepseek-chat",
            "qwen_model": "qwen-plus",
            "gemini_model": "gemini-2.5-flash",
            "nvidia_model": "meta/llama-3.1-70b-instruct",
            "local_model": "qwen/qwen3-coder-30b",
            "local_base_url": "http://127.0.0.1:1234",
            "qwen_base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "selected_system_prompt": "sys",
            "selected_task_prompt": "task",
        },
    )
    monkeypatch.setattr("app.routers.netdiag.merged_system_prompt_catalog", lambda: {"sys": "SYS"})
    monkeypatch.setattr("app.routers.netdiag.merged_task_prompt_catalog", lambda: {"task": "TASK"})

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "max_commands": 4,
            "fast_plan_enabled": True,
            "follow_up": "重点检查接口日志是否出现新的 flap",
            "llm_route": {"primary": {"provider": "chatgpt", "model": "gpt-4.1-mini"}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 0
    rnd = body.get("round") or {}
    assert len(rnd.get("commands") or []) > 0
    assert "Planner deterministic fallback" in str(rnd.get("planner_summary") or "")


def test_plan_with_target_probe_keeps_fast_plan_and_persists_target(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_run_planner_llm should not be called for target_probe fast plan")

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _should_not_call)

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "max_commands": 4,
            "fast_plan_enabled": True,
            "target_probe": {
                "title": "验证头号假设: 接口管理性 down",
                "domain": "link",
                "reason": "上一轮已经高度怀疑接口被人为 shutdown",
                "uncovered": ["接口配置", "最近接口日志"],
                "source": "continue_probe",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 0
    rnd = body.get("round") or {}
    assert len(rnd.get("commands") or []) > 0
    assert len(rnd.get("commands") or []) <= 2
    assert (rnd.get("target_probe") or {}).get("domain") == "link"
    assert "接口管理性 down" in str((rnd.get("target_probe") or {}).get("title") or "")
    perf = body.get("performance") or {}
    assert (perf.get("target_probe") or {}).get("source") == "continue_probe"
    assert "\"reason\": \"target_probe\"" in str(rnd.get("planner_raw_output") or "")


def test_plan_with_stop_if_matched_target_probe_uses_single_probe_budget(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_run_planner_llm should not be called for stop_if_matched fast plan")

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _should_not_call)

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "max_commands": 4,
            "fast_plan_enabled": True,
            "target_probe": {
                "title": "验证管理性 down",
                "domain": "link",
                "reason": "命中后即可收敛",
                "preferred_intents": ["interface_summary", "system_log_recent"],
                "expected_evidence": ["interface_admin_shutdown_event"],
                "stop_if_matched": True,
                "preferred_scope": "related_commands",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 0
    rnd = body.get("round") or {}
    assert len(rnd.get("commands") or []) == 1
    cmd0 = (rnd.get("commands") or [])[0]
    assert str(cmd0.get("expected_signal") or "") == "interface_admin_shutdown_event"
    assert (rnd.get("target_probe") or {}).get("stop_if_matched") is True
    perf = body.get("performance") or {}
    assert int(perf.get("effective_max_commands") or 0) == 1


def test_plan_can_consume_validation_task_without_direct_target_probe(monkeypatch):
    client = TestClient(app)
    sid = _create_session(client)

    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_run_planner_llm should not be called for validation_task fast plan")

    monkeypatch.setattr("app.routers.netdiag._run_planner_llm", _should_not_call)

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "max_commands": 6,
            "fast_plan_enabled": True,
            "validation_task": {
                "current_probe": {
                    "title": "验证管理性 down",
                    "domain": "link",
                    "reason": "上一枪已发现 shutdown 迹象",
                },
                "next_probe": {
                    "title": "补齐最近接口日志",
                    "domain": "link",
                    "preferred_scope": "related_commands",
                    "preferred_intents": ["system_log_recent", "interface_summary"],
                    "expected_signals": ["最近接口日志"],
                    "expected_evidence": ["最近接口日志"],
                    "stop_if_matched": True,
                    "stop_reason": "conclude_with_verification",
                },
                "unmatched_signals": ["最近接口日志"],
                "preferred_scope": "related_commands",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert called["llm"] == 0
    rnd = body.get("round") or {}
    assert (rnd.get("target_probe") or {}).get("title") == "补齐最近接口日志"
    assert "system_log_recent" in ((rnd.get("target_probe") or {}).get("preferred_intents") or [])
    assert ((rnd.get("evidence_overview") or {}).get("validation_task") or {}).get("next_probe")
    perf = body.get("performance") or {}
    assert (perf.get("validation_task") or {}).get("preferred_scope") == "related_commands"
