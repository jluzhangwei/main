from datetime import datetime

from app.diagnosis.models import CommandExecution, DiagnosisSessionCreate, FaultWindow, PlannedCommand, SessionDeviceInput
from app.diagnosis.session_manager import DiagnosisSessionManager
from app.routers.netdiag import _normalize_session_status_for_ui


def _payload() -> DiagnosisSessionCreate:
    return DiagnosisSessionCreate(
        question="端口 down 诊断",
        fault_window=FaultWindow(
            start_at=datetime(2026, 3, 11, 10, 0, 0),
            end_at=datetime(2026, 3, 11, 11, 0, 0),
            timezone="Asia/Singapore",
        ),
        devices=[
            SessionDeviceInput(
                device_id="dev-1",
                device_ip="192.168.0.88",
                username="u",
                password="p",
                vendor_hint="huawei",
                jump_mode="direct",
            )
        ],
    )


def _prepare_analyzing_round(mgr: DiagnosisSessionManager, *, session_status: str, updated_at: str):
    s = mgr.create_session(_payload())
    cmd = PlannedCommand(command_id="c1", device_id="dev-1", intent="system_log_recent", command="display logbuffer")
    mgr.append_round(
        s.session_id,
        planner_summary="s",
        planner_raw_output="raw",
        commands=[cmd],
    )
    mgr.set_round_executions(
        s.session_id,
        1,
        executions=[
            CommandExecution(
                command_id="c1",
                device_id="dev-1",
                command="display logbuffer",
                status="success",
                output_file="",
                error=None,
                duration_sec=0.2,
            )
        ],
        status="analyzing",
    )
    mgr.set_status(s.session_id, session_status)
    cur = mgr.get_session(s.session_id)
    assert cur is not None
    cur.updated_at = updated_at
    if cur.rounds:
        cur.rounds[-1].updated_at = updated_at
    return cur


def test_orphan_round_analyzing_is_auto_recovered(tmp_path):
    mgr = DiagnosisSessionManager(output_root=(tmp_path / "sessions").as_posix())
    cur = _prepare_analyzing_round(
        mgr,
        session_status="ready_for_next_probe",
        updated_at="2026-01-01 00:00:00",
    )
    fixed = _normalize_session_status_for_ui(mgr, cur)
    assert fixed is not None
    assert str(fixed.status) == "ready_for_next_probe"
    r = mgr.get_round(cur.session_id, 1)
    assert r is not None
    assert str(r.status) == "failed"
    assert "auto-recovered" in str(r.analysis_result or "")


def test_recent_analyzing_round_is_not_recovered(tmp_path):
    mgr = DiagnosisSessionManager(output_root=(tmp_path / "sessions").as_posix())
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = _prepare_analyzing_round(
        mgr,
        session_status="analyzing",
        updated_at=now_s,
    )
    _normalize_session_status_for_ui(mgr, cur)
    r = mgr.get_round(cur.session_id, 1)
    assert r is not None
    assert str(r.status) == "analyzing"
    assert not str(r.analysis_result or "").strip()
