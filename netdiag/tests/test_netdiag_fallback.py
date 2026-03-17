from app.routers.netdiag import _deterministic_analysis_fallback


def test_deterministic_analysis_fallback_contains_core_sections():
    text = _deterministic_analysis_fallback(
        error_message="The read operation timed out",
        parsed_evidence={
            "command_health": {"total": 3, "valid_output": 2, "error_output": 1},
            "summary_lines": ["link: delta=+0.18", "command_health: valid=2/3"],
        },
        issue_hits=[
            {
                "issue_id": "KI-001",
                "score": 6.3,
                "title": "BGP flap",
                "match_reasons": ["profile_exact=cisco_nxos"],
            }
        ],
        hypotheses=[
            {
                "hypothesis_id": "hyp-1",
                "title": "Routing protocol/session instability",
                "domain": "routing",
                "score": 0.88,
                "confidence": 0.88,
                "evidence_for": ["signal+ idle"],
                "evidence_against": [],
                "next_intents": ["bgp_summary", "system_log_recent"],
            }
        ],
    )
    assert "Deterministic Analyzer Fallback" in text
    assert "TopHypothesis" in text
    assert "CommandHealth" in text
    assert "[KnownIssueHints]" in text
