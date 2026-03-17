from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.diagnosis.models import CommandExecution, PlannedCommand
from app.main import app


def test_round_outputs_endpoint_returns_output_tail(tmp_path):
    c = TestClient(app)
    start = (datetime.now() - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    create = c.post(
        "/api/netdiag/sessions",
        json={
            "question": "outputs endpoint test",
            "fault_window": {"start_at": start, "end_at": end, "timezone": "Asia/Singapore"},
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "10.0.0.1",
                    "username": "u",
                    "password": "p",
                    "jump_mode": "direct",
                }
            ],
        },
    )
    assert create.status_code == 200
    sid = create.json()["session"]["session_id"]

    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="summary",
        planner_raw_output="raw",
        commands=[PlannedCommand(command_id="cmd-1", device_id="dev-1", command="show version")],
    )
    assert rnd is not None

    out_file = tmp_path / "round1_cmd1.txt"
    out_file.write_text("line-1\nline-2\nline-3\n", encoding="utf-8")
    mgr.set_round_executions(
        sid,
        round_no=1,
        executions=[
            CommandExecution(
                command_id="cmd-1",
                device_id="dev-1",
                command="show version",
                status="success",
                output_file=out_file.as_posix(),
                error=None,
                duration_sec=0.5,
            )
        ],
        status="analyzing",
    )

    resp = c.get(f"/api/netdiag/sessions/{sid}/rounds/1/outputs", params={"tail_chars": 200})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("ok") is True
    assert len(payload.get("items", [])) == 1
    assert "line-3" in str(payload["items"][0].get("output_tail") or "")


def test_session_outputs_endpoint_keeps_history_across_rounds(tmp_path):
    c = TestClient(app)
    start = (datetime.now() - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    create = c.post(
        "/api/netdiag/sessions",
        json={
            "question": "session outputs history test",
            "fault_window": {"start_at": start, "end_at": end, "timezone": "Asia/Singapore"},
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "10.0.0.2",
                    "username": "u",
                    "password": "p",
                    "jump_mode": "direct",
                }
            ],
        },
    )
    assert create.status_code == 200
    sid = create.json()["session"]["session_id"]

    mgr = app.state.diag_session_manager
    rnd1 = mgr.append_round(
        sid,
        planner_summary="summary-1",
        planner_raw_output="raw-1",
        commands=[PlannedCommand(command_id="cmd-1", device_id="dev-1", command="show version")],
    )
    rnd2 = mgr.append_round(
        sid,
        planner_summary="summary-2",
        planner_raw_output="raw-2",
        commands=[PlannedCommand(command_id="cmd-2", device_id="dev-1", command="show interface brief")],
    )
    assert rnd1 is not None and rnd2 is not None

    out1 = tmp_path / "round1_cmd1.txt"
    out2 = tmp_path / "round2_cmd2.txt"
    out1.write_text("r1-line\n", encoding="utf-8")
    out2.write_text("r2-line\n", encoding="utf-8")

    mgr.set_round_executions(
        sid,
        round_no=1,
        executions=[
            CommandExecution(
                command_id="cmd-1",
                device_id="dev-1",
                command="show version",
                status="success",
                output_file=out1.as_posix(),
                duration_sec=0.4,
            )
        ],
        status="completed",
    )
    mgr.set_round_executions(
        sid,
        round_no=2,
        executions=[
            CommandExecution(
                command_id="cmd-2",
                device_id="dev-1",
                command="show interface brief",
                status="success",
                output_file=out2.as_posix(),
                duration_sec=0.7,
            )
        ],
        status="completed",
    )

    resp = c.get(f"/api/netdiag/sessions/{sid}/outputs", params={"tail_chars": 200, "max_items": 50})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("ok") is True
    items = payload.get("items", [])
    assert len(items) == 2
    assert {int(x.get("round_no") or 0) for x in items} == {1, 2}
    assert any("r1-line" in str(x.get("output_tail") or "") for x in items)
    assert any("r2-line" in str(x.get("output_tail") or "") for x in items)
