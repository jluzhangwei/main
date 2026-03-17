from app.diagnosis.sop_engine import (
    build_stop_decision,
    propose_sop_steps,
    score_hypotheses,
    seed_hypotheses,
)


def test_seed_hypotheses_includes_known_issue():
    hyps = seed_hypotheses(
        question="BGP 邻居抖动导致丢包",
        focus_goals=["路由稳定性", "BGP"],
        known_issue_hits=[{"issue_id": "KI-001", "title": "BGP peer flap", "score": 7.2, "diag_intents": ["bgp_summary"]}],
    )
    assert len(hyps) >= 2
    assert any(str(h.get("source")) == "known_issue" for h in hyps)


def test_propose_sop_steps_prefers_allowed_intents():
    hyps = seed_hypotheses(question="interface flap and drop", focus_goals=["链路抖动"], known_issue_hits=[])
    steps = propose_sop_steps(
        round_no=1,
        profile_map={"dev-1": "huawei_vrp"},
        hypotheses=hyps,
        max_steps=2,
    )
    intents = [str(x.get("intent") or "") for x in steps]
    assert intents
    assert intents[0] == "interface_summary"
    assert "clock_check" not in intents
    assert len(intents) <= 2
    assert all(i in {"clock_check", "version_check", "system_log_recent", "interface_summary", "interface_errors", "cpu_health", "memory_health", "routing_summary", "ospf_neighbor", "bgp_summary"} for i in intents)


def test_score_and_stop_decision_converges():
    base = seed_hypotheses(question="BGP neighbor down", focus_goals=["BGP会话"], known_issue_hits=[])
    updated = score_hypotheses(
        base,
        evidence_text="show ip bgp summary neighbor down idle routing unstable",
        known_issue_hits=[],
        round_no=3,
    )
    top = updated[0]
    assert float(top.get("score") or 0.0) >= 0.5

    forced = [dict(updated[0], score=0.91), dict(updated[1], score=0.52)] if len(updated) > 1 else [dict(updated[0], score=0.91)]
    decision = build_stop_decision(forced, round_no=3, max_rounds=6)
    assert decision["recommend_conclude"] is True


def test_score_hypotheses_accepts_structured_signals_and_command_health():
    base = seed_hypotheses(question="BGP neighbor down", focus_goals=["BGP会话"], known_issue_hits=[])
    boosted = score_hypotheses(
        base,
        evidence_text="",
        known_issue_hits=[],
        round_no=2,
        evidence_signals=[
            {
                "domain": "routing",
                "polarity": "positive",
                "signal": "routing_session_unstable",
                "weight": 0.16,
                "detail": "neighbor idle observed",
            }
        ],
        command_health={"total": 4, "valid_output": 4},
    )
    penalized = score_hypotheses(
        base,
        evidence_text="",
        known_issue_hits=[],
        round_no=2,
        evidence_signals=[],
        command_health={"total": 4, "valid_output": 1},
    )
    assert float(boosted[0].get("score") or 0.0) >= float(base[0].get("score") or 0.0)
    assert float(penalized[0].get("score") or 0.0) <= float(boosted[0].get("score") or 0.0)


def test_propose_sop_steps_prioritizes_target_probe_intents():
    hyps = seed_hypotheses(question="interface flap and drop", focus_goals=["链路抖动"], known_issue_hits=[])
    steps = propose_sop_steps(
        round_no=2,
        profile_map={"dev-1": "huawei_vrp"},
        hypotheses=hyps,
        max_steps=2,
        target_probe={
            "title": "验证管理性 down",
            "domain": "link",
            "preferred_intents": ["system_log_recent", "interface_summary"],
        },
    )
    intents = [str(x.get("intent") or "") for x in steps]
    assert intents
    assert intents[0] == "system_log_recent"
    assert "interface_summary" in intents


def test_propose_sop_steps_carries_expected_signal_from_target_probe():
    hyps = seed_hypotheses(question="interface down after shutdown", focus_goals=["端口down"], known_issue_hits=[])
    steps = propose_sop_steps(
        round_no=2,
        profile_map={"dev-1": "huawei_vrp"},
        hypotheses=hyps,
        max_steps=2,
        target_probe={
            "title": "验证管理性 down",
            "domain": "link",
            "preferred_intents": ["interface_summary"],
            "expected_evidence": ["interface_admin_shutdown_event"],
        },
    )
    assert steps
    assert str(steps[0].get("intent") or "") == "interface_summary"
    assert str(steps[0].get("expected_signal") or "") == "interface_admin_shutdown_event"
