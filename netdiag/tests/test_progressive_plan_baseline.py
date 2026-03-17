from types import SimpleNamespace

from app.diagnosis.models import PlannedCommand
from app.routers.netdiag import _enforce_progressive_plan


def _cmd(cid: str, intent: str, command: str) -> PlannedCommand:
    return PlannedCommand(
        command_id=cid,
        device_id="dev-1",
        intent=intent,
        command=command,
        reason="test",
        expected_signal="",
        risk_level="read_only",
        requires_approval=True,
        approved=False,
    )


def _session(question: str, focus_goals: list[str], top_domain: str = "link"):
    return SimpleNamespace(
        question=question,
        focus_goals=focus_goals,
        time_calibration=[{"device_id": "dev-1", "status": "success"}],
        rounds=[SimpleNamespace(hypotheses=[{"domain": top_domain, "score": 0.8}], executions=[], commands=[])],
    )


def _session_first_round(question: str, focus_goals: list[str]):
    return SimpleNamespace(
        question=question,
        focus_goals=focus_goals,
        time_calibration=[{"device_id": "dev-1", "status": "success"}],
        rounds=[],
    )


def test_progressive_plan_filters_baseline_like_intents_in_first_round_after_baseline():
    commands = [
        _cmd("c1", "clock_check", "display clock"),
        _cmd("c2", "version_check", "display version"),
        _cmd("c3", "cpu_health", "display cpu-usage"),
        _cmd("c4", "interface_summary", "display interface brief"),
        _cmd("c5", "system_log_recent", "display logbuffer"),
    ]
    out = _enforce_progressive_plan(
        commands=commands,
        session=_session_first_round("端口 down 抖动排查", ["关注链路 flap"]),
        profile_map={"dev-1": "huawei_vrp"},
        version_map={},
        max_commands=6,
        learning_store=None,
    )
    intents = [str(x.intent or "").strip().lower() for x in out]
    assert "interface_summary" in intents
    assert "system_log_recent" in intents
    assert "clock_check" not in intents
    assert "version_check" not in intents
    assert "cpu_health" not in intents


def test_progressive_plan_filters_baseline_like_intents_after_baseline():
    commands = [
        _cmd("c1", "clock_check", "display clock"),
        _cmd("c2", "version_check", "display version"),
        _cmd("c3", "interface_summary", "display interface brief"),
    ]
    out = _enforce_progressive_plan(
        commands=commands,
        session=_session("端口 down 抖动排查", ["关注链路 flap"]),
        profile_map={"dev-1": "huawei_vrp"},
        version_map={},
        max_commands=6,
        learning_store=None,
    )
    intents = [str(x.intent or "").strip().lower() for x in out]
    assert "interface_summary" in intents
    assert "clock_check" not in intents
    assert "version_check" not in intents


def test_progressive_plan_keeps_clock_intent_when_clock_focus_requested():
    commands = [
        _cmd("c1", "clock_check", "display clock"),
        _cmd("c2", "interface_summary", "display interface brief"),
    ]
    out = _enforce_progressive_plan(
        commands=commands,
        session=_session("时间对齐异常排查", ["确认时钟偏移与 NTP 同步"]),
        profile_map={"dev-1": "huawei_vrp"},
        version_map={},
        max_commands=6,
        learning_store=None,
    )
    intents = [str(x.intent or "").strip().lower() for x in out]
    assert "clock_check" in intents


def test_progressive_plan_filters_clock_intent_when_only_hypothesis_is_clock():
    commands = [
        _cmd("c1", "clock_check", "display clock"),
        _cmd("c2", "interface_summary", "display interface brief"),
    ]
    out = _enforce_progressive_plan(
        commands=commands,
        session=_session("端口 down 抖动排查", ["关注链路 flap"], top_domain="clock"),
        profile_map={"dev-1": "huawei_vrp"},
        version_map={},
        max_commands=6,
        learning_store=None,
    )
    intents = [str(x.intent or "").strip().lower() for x in out]
    assert "interface_summary" in intents
    assert "clock_check" not in intents


def test_progressive_plan_keeps_time_evidence_focus_without_readding_baseline_checks():
    commands = [
        _cmd("c1", "clock_check", "display clock"),
        _cmd("c2", "version_check", "display version"),
        _cmd("c3", "interface_summary", "display interface brief"),
        _cmd("c4", "system_log_recent", "display logbuffer"),
    ]
    out = _enforce_progressive_plan(
        commands=commands,
        session=_session_first_round("端口告警根因定位", ["给出时间校准后的证据链"]),
        profile_map={"dev-1": "huawei_vrp"},
        version_map={},
        max_commands=6,
        learning_store=None,
    )
    intents = [str(x.intent or "").strip().lower() for x in out]
    assert "interface_summary" in intents
    assert "system_log_recent" in intents
    assert "clock_check" not in intents
    assert "version_check" not in intents
