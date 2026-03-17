from app.diagnosis.duel_store import NetdiagDuelStore


def _store(tmp_path):
    return NetdiagDuelStore((tmp_path / "state" / "duels.json").as_posix())


def test_duel_create_inject_judge_and_delete(tmp_path):
    store = _store(tmp_path)
    duel = store.create_duel(
        {
            "title": "bgp drill",
            "environment_tag": "lab",
            "mode": "simulated",
            "template_id": "lab_bgp_neighbor_down",
            "target_devices": ["dev-1"],
            "variables": {"asn": "65001", "peer_ip": "10.1.1.1"},
        }
    )
    did = duel["duel_id"]
    assert did
    assert duel["status"] == "created"
    assert len(duel.get("inject_plan", [])) >= 1

    injected = store.set_inject_result(did, {"detail": "sim"})
    assert injected is not None
    assert injected["status"] == "injected"

    judged = store.judge_duel(
        did,
        {
            "predicted_domain": "routing",
            "predicted_root_cause": "BGP neighbor session idle instability",
            "evidence_signals": ["bgp_peer_down", "routing_session_unstable"],
            "confidence": 0.92,
            "recovery_verified": True,
        },
    )
    assert judged is not None
    jr = judged.get("judge_result", {})
    assert float(jr.get("score") or 0.0) >= 70.0
    assert str(jr.get("verdict") or "") == "pass"

    marked = store.set_case_result(did, {"ok": True, "case_id": "case-1"})
    assert marked is not None
    assert str(marked.get("status") or "") == "promoted"

    assert store.delete_duel(did) is True
