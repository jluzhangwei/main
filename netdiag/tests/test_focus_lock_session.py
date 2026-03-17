from datetime import datetime

from app.diagnosis.models import DiagnosisSessionCreate, FaultWindow, PlannedCommand, SessionDeviceInput
from app.diagnosis import session_manager as session_manager_module
from app.diagnosis.session_manager import DiagnosisSessionManager


def _build_payload() -> DiagnosisSessionCreate:
    return DiagnosisSessionCreate(
        question="诊断管理网抖动",
        fault_window=FaultWindow(
            start_at=datetime(2026, 3, 7, 1, 55, 0),
            end_at=datetime(2026, 3, 7, 2, 25, 0),
            timezone="Asia/Singapore",
        ),
        devices=[
            SessionDeviceInput(
                device_id="dev-1",
                device_ip="10.0.0.1",
                username="u",
                password="p",
                vendor_hint="huawei",
                jump_mode="direct",
            )
        ],
        focus_goals=["链路抖动", "链路抖动", "丢包"],
    )


def test_focus_goals_persist_and_append(tmp_path):
    mgr = DiagnosisSessionManager(output_root=(tmp_path / "sessions").as_posix())
    s = mgr.create_session(_build_payload())
    assert s.focus_goals == ["链路抖动", "丢包"]
    assert s.focus_lock is True

    s2 = mgr.append_focus_goals(s.session_id, ["高时延", "丢包"])
    assert s2 is not None
    assert s2.focus_goals == ["链路抖动", "丢包", "高时延"]


def test_round_focus_review_can_be_updated(tmp_path):
    mgr = DiagnosisSessionManager(output_root=(tmp_path / "sessions").as_posix())
    s = mgr.create_session(_build_payload())

    cmd = PlannedCommand(command_id="c1", command="display clock")
    rnd = mgr.append_round(
        s.session_id,
        planner_summary="s",
        planner_raw_output="raw",
        commands=[cmd],
        focus_review={"coverage_ratio": 0.5, "uncovered": ["丢包"]},
    )
    assert rnd is not None
    assert rnd.focus_review.get("coverage_ratio") == 0.5

    rnd2 = mgr.set_round_analysis(
        s.session_id,
        round_no=1,
        analysis_result="done",
        focus_review={"coverage_ratio": 1.0, "uncovered": []},
    )
    assert rnd2 is not None
    assert rnd2.focus_review.get("coverage_ratio") == 1.0


def test_list_sessions_orders_by_latest_updated_at(tmp_path, monkeypatch):
    counter = {"i": 0}

    def _fake_now() -> str:
        i = counter["i"]
        counter["i"] += 1
        return f"2026-03-11 10:00:{i:02d}"

    monkeypatch.setattr(session_manager_module, "_now_s", _fake_now)
    mgr = DiagnosisSessionManager(output_root=(tmp_path / "sessions").as_posix())
    s1 = mgr.create_session(_build_payload())
    s2 = mgr.create_session(_build_payload())
    before = [x.session_id for x in mgr.list_sessions()]
    assert before[0] == s2.session_id

    changed = mgr.set_status(s1.session_id, "aborted")
    assert changed is not None
    after = [x.session_id for x in mgr.list_sessions()]
    assert after[0] == s1.session_id
