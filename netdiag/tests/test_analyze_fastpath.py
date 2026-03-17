from types import SimpleNamespace

from app.routers.netdiag import (
    _apply_expected_signal_stop_decision,
    _build_validation_task,
    _build_round_conclusion_block,
    _derive_next_target_probe,
    _build_fastpath_analysis_text,
    _direct_evidence_should_conclude,
    _expected_signal_review,
    _inject_direct_evidence_hypothesis,
    _round_response_payload,
    _session_next_target_probe,
    _should_use_analysis_fast_path,
)


def _session_stub():
    return SimpleNamespace(
        fault_window=SimpleNamespace(
            start_at="2026-03-10 20:30:00",
            end_at="2026-03-10 21:30:00",
            timezone="Asia/Singapore",
        )
    )


def test_should_use_analysis_fast_path_when_direct_hint_present():
    ok, reason = _should_use_analysis_fast_path(
        parsed_evidence={
            "command_health": {"total": 4, "valid_output": 4, "error_output": 0},
            "domain_delta": {"link": 0.42},
        },
        direct_evidence_hints=["interface_admin_shutdown_event: Ethernet1/0/6"],
    )
    assert ok is True
    assert "direct_hints" in reason


def test_should_not_use_analysis_fast_path_when_evidence_weak():
    ok, reason = _should_use_analysis_fast_path(
        parsed_evidence={
            "command_health": {"total": 4, "valid_output": 2, "error_output": 2},
            "domain_delta": {"link": 0.08},
        },
        direct_evidence_hints=[],
    )
    assert ok is False
    assert reason


def test_build_fastpath_analysis_text_contains_required_sections():
    text = _build_fastpath_analysis_text(
        session=_session_stub(),
        round_no=1,
        parsed_evidence={
            "command_health": {"total": 3, "valid_output": 3, "error_output": 0},
            "domain_delta": {"link": 0.88},
        },
        exec_records=[
            {
                "device_id": "dev-1",
                "command": "display interface Eth1/0/6",
                "status": "success",
                "output_text": "Ethernet1/0/6 current state : DOWN",
            }
        ],
        direct_evidence_hints=["interface_admin_shutdown_event: Ethernet1/0/6"],
        reason="direct_hints=1",
    )
    for token in (
        "### 1) 当前判定",
        "### 2) 证据链",
        "### 3) 根因假设与置信度",
        "### 4) 建议后续操作",
        "### 5) 下一轮建议命令",
        "### 6) 时间维度校验",
    ):
        assert token in text
    assert "域异常（delta=" not in text
    assert "max_abs_domain_delta" not in text
    assert "接口被管理性关闭" in text


def test_fastpath_reason_is_sanitized_for_user_view():
    text = _build_fastpath_analysis_text(
        session=_session_stub(),
        round_no=2,
        parsed_evidence={
            "command_health": {"total": 2, "valid_output": 2, "error_output": 0},
            "domain_delta": {"link": 0.95},
        },
        exec_records=[],
        direct_evidence_hints=[],
        reason="max_abs_domain_delta=0.95",
    )
    assert "reason=domain_signal_converged" in text
    assert "max_abs_domain_delta=0.95" not in text


def test_direct_evidence_should_conclude_when_shutdown_signals_and_health_good():
    ok, reason = _direct_evidence_should_conclude(
        parsed_evidence={"command_health": {"total": 4, "valid_output": 4, "error_output": 0}},
        direct_evidence_hints=[
            "interface_admin_shutdown_event: Generic shutdown/admin-down indicators in log: 4",
            "huawei_interface_admin_down: Administratively down interfaces: Ethernet1/0/6",
        ],
        focus_review={"uncovered": []},
    )
    assert ok is True
    assert "direct_evidence_converged" in reason


def test_direct_evidence_should_not_conclude_when_focus_uncovered():
    ok, reason = _direct_evidence_should_conclude(
        parsed_evidence={"command_health": {"total": 4, "valid_output": 4, "error_output": 0}},
        direct_evidence_hints=["interface_admin_shutdown_event: hit"],
        focus_review={"uncovered": ["CPU"]},
    )
    assert ok is False
    assert "focus_goals_uncovered" in reason


def test_inject_direct_evidence_hypothesis_boosts_link_domain():
    rows = _inject_direct_evidence_hypothesis(
        hypotheses=[
            {
                "hypothesis_id": "h-clock",
                "title": "Time skew affecting event correlation",
                "domain": "clock",
                "score": 0.68,
                "confidence": 0.68,
                "status": "possible",
                "evidence_for": [],
                "evidence_against": [],
                "next_intents": ["clock_check"],
            }
        ],
        parsed_evidence={"command_health": {"total": 3, "valid_output": 3, "error_output": 0}},
        direct_evidence_hints=["huawei_interface_shutdown_event: Admin shutdown log events: ethernet1/0/6"],
    )
    assert rows
    assert str(rows[0].get("domain")) == "link"
    assert float(rows[0].get("score") or 0.0) >= 0.93


def test_round_conclusion_block_contains_required_fields():
    text = _build_round_conclusion_block(
        hypotheses=[
            {
                "title": "Interface administratively down",
                "domain": "link",
                "score": 0.96,
                "confidence": 0.96,
            }
        ],
        stop_decision={
            "recommend_conclude": True,
            "reason": "direct_evidence_converged",
            "next_action": "conclude_with_verification",
        },
        focus_review={"uncovered": []},
        retrospective={"execution_success_rate": 1.0, "top_hypothesis_score_delta": 0.32},
    )
    assert "[Round Conclusion]" in text
    for token in ("当前判定", "头号假设", "置信度", "下一步", "原因", "执行概况"):
        assert token in text


def test_expected_signal_review_matches_keywords():
    review = _expected_signal_review(
        {
            "expected_signals": ["接口配置", "最近接口日志", "STP阻塞"],
            "expected_evidence": ["interface_admin_shutdown_event"],
        },
        "display current-configuration interface Ethernet1/0/6\n"
        "display logbuffer\n"
        "interface configuration changed by shutdown\n"
        "interface admin shutdown event observed\n",
    )
    assert "接口配置" in review["matched"]
    assert "最近接口日志" in review["matched"]
    assert "interface_admin_shutdown_event" in review["matched"]
    assert review["unmatched"] == ["STP阻塞"]
    assert float(review["coverage_ratio"]) == 0.75


def test_expected_signal_stop_decision_concludes_when_all_expected_signals_match():
    stop = _apply_expected_signal_stop_decision(
        {
            "recommend_conclude": False,
            "reason": "top_score=0.70, gap=0.05",
            "confidence": 0.72,
            "next_action": "next_round_targeted_checks",
        },
        target_probe={
            "stop_if_matched": True,
            "stop_reason": "conclude_with_verification",
        },
        expected_signal_review={
            "expected_signals": ["接口配置", "最近接口日志"],
            "matched": ["接口配置", "最近接口日志"],
            "unmatched": [],
            "coverage_ratio": 1.0,
        },
        focus_review={"uncovered": []},
    )
    assert stop["recommend_conclude"] is True
    assert stop["next_action"] == "conclude_with_verification"
    assert "expected_signals_fully_matched" in str(stop["reason"])
    assert float(stop["confidence"]) >= 0.9


def test_expected_signal_stop_decision_partial_match_boosts_confidence_only():
    stop = _apply_expected_signal_stop_decision(
        {
            "recommend_conclude": False,
            "reason": "top_score=0.61, gap=0.03",
            "confidence": 0.61,
            "next_action": "next_round_targeted_checks",
        },
        target_probe={
            "stop_if_matched": True,
            "stop_reason": "conclude_with_verification",
        },
        expected_signal_review={
            "expected_signals": ["接口配置", "最近接口日志"],
            "matched": ["接口配置"],
            "unmatched": ["最近接口日志"],
            "coverage_ratio": 0.5,
        },
        focus_review={"uncovered": ["日志关联"]},
    )
    assert stop["recommend_conclude"] is False
    assert stop["next_action"] == "next_round_targeted_checks"
    assert "expected_signals_partial_match" in str(stop["reason"])
    assert float(stop["confidence"]) > 0.61


def test_expected_signal_stop_reason_can_override_default_action():
    stop = _apply_expected_signal_stop_decision(
        {
            "recommend_conclude": False,
            "reason": "top_score=0.70, gap=0.05",
            "confidence": 0.72,
            "next_action": "next_round_targeted_checks",
        },
        target_probe={
            "stop_if_matched": True,
            "stop_reason": "verified_conclude",
        },
        expected_signal_review={
            "expected_signals": ["interface_admin_shutdown_event"],
            "matched": ["interface_admin_shutdown_event"],
            "unmatched": [],
            "coverage_ratio": 1.0,
        },
        focus_review={"uncovered": []},
    )
    assert stop["recommend_conclude"] is True
    assert stop["next_action"] == "verified_conclude"
    assert "stop_reason=verified_conclude" in str(stop["reason"])


def test_derive_next_target_probe_prioritizes_unmatched_expected_signals():
    probe = _derive_next_target_probe(
        target_probe={
            "title": "Interface instability or physical/optic issue",
            "domain": "link",
            "stop_reason": "conclude_with_verification",
        },
        hypotheses=[
            {
                "hypothesis_id": "hyp-link",
                "title": "Interface instability or physical/optic issue",
                "domain": "link",
                "next_intents": ["interface_summary", "interface_errors"],
            }
        ],
        stop_decision={
            "recommend_conclude": False,
            "reason": "need another targeted probe",
            "top_hypothesis": {
                "hypothesis_id": "hyp-link",
                "title": "Interface instability or physical/optic issue",
            },
        },
        focus_review={"uncovered": ["日志关联"]},
        expected_signal_review={
            "expected_signals": ["接口配置", "最近接口日志"],
            "matched": ["接口配置"],
            "unmatched": ["最近接口日志"],
            "coverage_ratio": 0.5,
        },
    )
    assert probe["source"] == "analyze_continue_probe"
    assert probe["stop_if_matched"] is True
    assert probe["expected_signals"] == ["最近接口日志"]
    assert probe["expected_evidence"] == ["最近接口日志"]
    assert "system_log_recent" in probe["preferred_intents"]


def test_build_validation_task_merges_current_next_probe_and_review():
    task = _build_validation_task(
        target_probe={
            "title": "当前验证",
            "preferred_intents": ["interface_summary"],
            "expected_signals": ["接口配置"],
            "expected_evidence": ["interface_admin_shutdown_event"],
            "preferred_scope": "related_commands",
            "stop_if_matched": True,
            "stop_reason": "conclude_with_verification",
        },
        next_target_probe={
            "title": "下一枪验证",
            "preferred_intents": ["system_log_recent"],
            "preferred_scope": "related_commands",
        },
        expected_signal_review={
            "matched": ["接口配置"],
            "unmatched": ["最近接口日志"],
            "coverage_ratio": 0.5,
        },
        focus_review={"covered": ["端口down"], "uncovered": ["日志关联"]},
        stop_decision={"next_action": "next_round_targeted_checks", "confidence": 0.74, "reason": "need another probe"},
    )
    assert task["current_probe"]["title"] == "当前验证"
    assert task["next_probe"]["title"] == "下一枪验证"
    assert task["preferred_scope"] == "related_commands"
    assert task["preferred_intents"] == ["system_log_recent"]
    assert task["matched_signals"] == ["接口配置"]
    assert task["unmatched_signals"] == ["最近接口日志"]
    assert task["uncovered_goals"] == ["日志关联"]
    assert task["stop_reason"] == "conclude_with_verification"
    assert task["next_action"] == "next_round_targeted_checks"
    assert task["reason"] == "need another probe"


def test_session_next_target_probe_prefers_persisted_validation_task_next_probe():
    session = SimpleNamespace(
        rounds=[
            SimpleNamespace(
                round_no=1,
                status="completed",
                target_probe={"title": "旧目标", "domain": "link"},
                evidence_overview={
                    "validation_task": {
                        "next_probe": {
                            "title": "补齐最近接口日志",
                            "domain": "link",
                            "preferred_intents": ["system_log_recent"],
                            "source": "validation_task",
                        }
                    }
                },
                stop_decision={},
                focus_review={},
                hypotheses=[],
            )
        ]
    )
    probe = _session_next_target_probe(session)
    assert probe["title"] == "补齐最近接口日志"
    assert probe["source"] == "validation_task"


def test_round_response_payload_includes_validation_task():
    round_obj = SimpleNamespace(
        round_no=1,
        status="completed",
        target_probe={"title": "检查端口状态", "domain": "link", "preferred_intents": ["interface_summary"]},
        evidence_overview={
            "expected_signal_review": {
                "matched": [],
                "unmatched": ["最近接口日志"],
                "coverage_ratio": 0.0,
            }
        },
        focus_review={"uncovered": ["最近接口日志"]},
        stop_decision={"next_action": "next_round_targeted_checks", "reason": "need another probe", "confidence": 0.71},
        hypotheses=[
            {
                "hypothesis_id": "hyp-link",
                "title": "Link issue",
                "domain": "link",
                "next_intents": ["system_log_recent"],
            }
        ],
    )
    row = _round_response_payload(round_obj)
    evidence = (row or {}).get("evidence_overview") or {}
    task = evidence.get("validation_task") or {}
    assert task.get("current_probe", {}).get("title") == "检查端口状态"
    assert "system_log_recent" in (task.get("next_probe", {}).get("preferred_intents") or [])


def test_round_response_payload_backfills_incomplete_validation_task_from_round_context():
    round_obj = SimpleNamespace(
        round_no=1,
        status="completed",
        target_probe={
            "title": "检查端口状态",
            "domain": "link",
            "preferred_intents": ["interface_summary"],
            "expected_signals": ["接口配置"],
            "expected_evidence": ["interface_admin_shutdown_event"],
            "preferred_scope": "related_commands",
            "stop_if_matched": True,
            "stop_reason": "conclude_with_verification",
        },
        evidence_overview={
            "validation_task": {
                "current_probe": {"title": "检查端口状态", "domain": "link"},
            },
            "expected_signal_review": {
                "matched": ["接口配置"],
                "unmatched": ["最近接口日志"],
                "coverage_ratio": 0.5,
            },
        },
        focus_review={"covered": ["端口状态"], "uncovered": ["最近接口日志"]},
        stop_decision={"next_action": "continue_probe", "reason": "need another probe", "confidence": 0.78},
        hypotheses=[
            {
                "hypothesis_id": "hyp-link",
                "title": "Link issue",
                "domain": "link",
                "next_intents": ["system_log_recent"],
            }
        ],
    )
    row = _round_response_payload(round_obj)
    task = ((row or {}).get("evidence_overview") or {}).get("validation_task") or {}
    assert task.get("preferred_scope") == "related_commands"
    assert task.get("matched_signals") == ["接口配置"]
    assert task.get("unmatched_signals") == ["最近接口日志"]
    assert task.get("covered_goals") == ["端口状态"]
    assert task.get("uncovered_goals") == ["最近接口日志"]
    assert task.get("stop_reason") == "conclude_with_verification"
    assert task.get("next_action") == "continue_probe"
    assert "system_log_recent" in ((task.get("next_probe") or {}).get("preferred_intents") or [])
