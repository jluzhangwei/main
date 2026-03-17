from app.diagnosis.case_store import NetdiagCaseStore


def _store(tmp_path):
    return NetdiagCaseStore((tmp_path / "state" / "cases.json").as_posix())


def test_upsert_search_toggle_and_delete(tmp_path):
    store = _store(tmp_path)
    row = store.upsert_case(
        {
            "title": "BGP flap with packet loss",
            "question": "bgp flap and loss on edge",
            "focus_goals": ["bgp", "packet loss"],
            "vendor_profiles": ["cisco_iosxe"],
            "domains": ["routing"],
            "root_cause": "peer side instability",
            "resolution_steps": ["check optic", "contact peer"],
            "verify_commands": ["show ip bgp summary"],
            "evidence_signals": ["routing_session_unstable"],
            "priority": 150,
            "enabled": True,
        }
    )
    cid = row["case_id"]
    assert cid

    hits = store.search(
        query_text="bgp flap packet loss",
        profiles=["cisco_iosxe"],
        domains=["routing"],
        evidence_text="routing_session_unstable observed",
        limit=5,
    )
    assert len(hits) == 1
    assert hits[0]["case_id"] == cid
    assert float(hits[0]["score"]) > 0.0

    disabled = store.set_case_enabled(cid, enabled=False)
    assert disabled is not None
    assert disabled["enabled"] is False
    hits2 = store.search(
        query_text="bgp flap packet loss",
        profiles=["cisco_iosxe"],
        domains=["routing"],
        evidence_text="routing_session_unstable observed",
        limit=5,
    )
    assert hits2 == []

    assert store.delete_case(cid) is True
    assert store.delete_case(cid) is False
