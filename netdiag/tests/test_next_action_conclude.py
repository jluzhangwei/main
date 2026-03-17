from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.diagnosis.models import PlannedCommand


def _create_session_with_baseline(client: TestClient) -> str:
    start = (datetime.now() - timedelta(hours=3)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    resp = client.post(
        "/api/netdiag/sessions",
        json={
            "question": "排查 192.168.0.88 端口 down 告警",
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
                "offset_seconds": 0.2,
            }
        ],
    )
    mgr.set_status(sid, "ready_for_next_probe")
    return sid


def test_next_action_uses_conclude_when_stop_decision_requests_verification_conclude():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager

    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_admin_config",
                command="display current-configuration interface Ethernet1/0/6",
                reason="verify admin state",
            )
        ],
        stop_decision={},
    )
    assert rnd is not None
    mgr.approve_round(sid, 1, approved=True)
    mgr.set_round_executions(sid, 1, executions=[], status="analyzing")
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 已发现直接证据\n- 下一步: conclude_with_verification",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "direct evidence already sufficient",
            "next_action": "conclude_with_verification",
        },
    )
    mgr.set_status(sid, "ready_for_next_probe")

    resp = client.get(f"/api/netdiag/sessions/{sid}/next_action")
    assert resp.status_code == 200
    body = resp.json()
    assert body["next"]["action"] == "conclude"
    assert body["next"]["raw_action"] == "conclude"
    assert body["next"]["ui_action"] == "conclude"
    assert body["next"]["round_no"] == 1


def test_next_action_exposes_continue_probe_ui_alias_and_ui_status():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_summary",
                command="display interface brief",
                reason="probe interface state",
            )
        ],
        stop_decision={},
    )
    assert rnd is not None
    mgr.approve_round(sid, 1, approved=True)
    mgr.set_round_executions(sid, 1, executions=[], status="completed")
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 仍需继续验证",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
        },
    )
    mgr.set_status(sid, "ready_for_next_probe")

    resp = client.get(f"/api/netdiag/sessions/{sid}/next_action")
    assert resp.status_code == 200
    body = resp.json()
    assert body["next"]["action"] == "continue_probe"
    assert body["next"]["raw_action"] == "continue_probe"
    assert body["next"]["legacy_action"] == "next_round"
    assert body["next"]["ui_action"] == "continue_probe"
    assert (body["next"]["target_probe"] or {}).get("source") in {"analyze_continue_probe", "continue_probe"}
    assert (body["next"]["validation_task"] or {}).get("next_probe")

    session_resp = client.get(f"/api/netdiag/sessions/{sid}")
    assert session_resp.status_code == 200
    session = session_resp.json()["session"]
    assert session["status"] == "ready_for_next_probe"
    assert session["ui_status"] == "ready_for_next_probe"
    assert (session["next_target_probe"] or {}).get("source") in {"analyze_continue_probe", "continue_probe"}
    assert session["next_target_probe"] == body["next"]["target_probe"]
    assert (session["validation_task"] or {}).get("next_probe") == (body["next"]["validation_task"] or {}).get("next_probe")


def test_session_ui_status_prefers_continue_probe_workflow_over_raw_status():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_summary",
                command="display interface brief",
                reason="probe interface state",
            )
        ],
        stop_decision={},
    )
    assert rnd is not None
    mgr.approve_round(sid, 1, approved=True)
    mgr.set_round_executions(sid, 1, executions=[], status="completed")
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 仍需继续验证",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
        },
    )
    mgr.set_status(sid, "completed")

    session_resp = client.get(f"/api/netdiag/sessions/{sid}")
    assert session_resp.status_code == 200
    session = session_resp.json()["session"]
    assert session["status"] == "completed"
    assert session["ui_status"] == "ready_for_next_probe"
    assert (session["next_action"] or {}).get("action") == "continue_probe"


def test_ready_for_next_probe_raw_status_is_accepted_end_to_end():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_summary",
                command="display interface brief",
                reason="probe interface state",
            )
        ],
        stop_decision={},
    )
    assert rnd is not None
    mgr.approve_round(sid, 1, approved=True)
    mgr.set_round_executions(sid, 1, executions=[], status="completed")
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 仍需继续验证",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
        },
    )
    mgr.set_status(sid, "ready_for_next_probe")

    next_resp = client.get(f"/api/netdiag/sessions/{sid}/next_action")
    assert next_resp.status_code == 200
    next_body = next_resp.json()["next"]
    assert next_body["action"] == "continue_probe"
    assert next_body["raw_action"] == "continue_probe"

    session_resp = client.get(f"/api/netdiag/sessions/{sid}")
    assert session_resp.status_code == 200
    session = session_resp.json()["session"]
    assert session["status"] == "ready_for_next_probe"
    assert session["ui_status"] == "ready_for_next_probe"
    assert (session["next_action"] or {}).get("action") == "continue_probe"


def test_plan_round_accepts_continue_probe_workflow_state():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_summary",
                command="display interface brief",
                reason="probe interface state",
            )
        ],
        stop_decision={},
    )
    assert rnd is not None
    mgr.approve_round(sid, 1, approved=True)
    mgr.set_round_executions(sid, 1, executions=[], status="completed")
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 仍需继续验证",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
        },
    )
    mgr.set_status(sid, "ready_for_next_probe")

    resp = client.post(
        f"/api/netdiag/sessions/{sid}/rounds/plan",
        json={
            "validation_task": {
                "next_probe": {
                    "title": "补齐最近接口日志",
                    "domain": "link",
                    "preferred_intents": ["system_log_recent"],
                    "source": "test_continue_probe",
                }
            }
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert (body["round"]["target_probe"] or {}).get("title") == "补齐最近接口日志"


def test_session_dump_includes_normalized_round_validation_task():
    client = TestClient(app)
    sid = _create_session_with_baseline(client)
    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="unit plan",
        planner_raw_output="{}",
        commands=[
            PlannedCommand(
                command_id="c1",
                device_id="dev-1",
                intent="interface_summary",
                command="display interface brief",
                reason="probe interface state",
            )
        ],
        target_probe={
            "title": "检查端口状态",
            "domain": "link",
            "preferred_intents": ["interface_summary"],
            "expected_signals": ["最近接口日志"],
            "preferred_scope": "related_commands",
        },
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
            "top_hypothesis": {"title": "Link issue", "domain": "link"},
        },
        focus_review={"uncovered": ["日志关联"]},
        hypotheses=[
            {
                "hypothesis_id": "hyp-link",
                "title": "Link issue",
                "domain": "link",
                "next_intents": ["system_log_recent"],
            }
        ],
    )
    assert rnd is not None
    mgr.set_round_analysis(
        sid,
        1,
        analysis_result="[Round Conclusion]\n- 当前判定: 仍需继续验证",
        status="completed",
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "next_action": "next_round_targeted_checks",
            "top_hypothesis": {"title": "Link issue", "domain": "link"},
        },
        evidence_overview={
            "expected_signal_review": {
                "matched": [],
                "unmatched": ["最近接口日志"],
                "coverage_ratio": 0.0,
            }
        },
    )
    mgr.set_status(sid, "ready_for_next_probe")

    resp = client.get(f"/api/netdiag/sessions/{sid}")
    assert resp.status_code == 200
    session = resp.json()["session"]
    latest = (session.get("rounds") or [])[-1]
    evidence = latest.get("evidence_overview") or {}
    task = evidence.get("validation_task") or {}
    assert (task.get("current_probe") or {}).get("title") == "检查端口状态"
    assert "最近接口日志" in (task.get("unmatched_signals") or [])
    assert (evidence.get("next_target_probe") or {}).get("title")
