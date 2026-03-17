from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_lab_duel_api_end_to_end():
    c = TestClient(app)
    token = uuid4().hex[:8]

    templates = c.get("/api/netdiag/lab/templates")
    assert templates.status_code == 200
    items = templates.json().get("items", [])
    assert any(str(x.get("template_id")) == "lab_bgp_neighbor_down" for x in items)

    denied = c.post(
        "/api/netdiag/lab/duels",
        json={
            "title": f"denied-{token}",
            "environment_tag": "lab",
            "mode": "manual",
            "template_id": "lab_bgp_neighbor_down",
            "target_devices": ["dev-1"],
            "variables": {"asn": "65001", "peer_ip": "10.1.1.1"},
        },
    )
    assert denied.status_code == 400

    created = c.post(
        "/api/netdiag/lab/duels",
        json={
            "title": f"duel-{token}",
            "environment_tag": "lab",
            "mode": "simulated",
            "template_id": "lab_bgp_neighbor_down",
            "target_devices": ["dev-1"],
            "variables": {"asn": "65001", "peer_ip": "10.1.1.1"},
        },
    )
    assert created.status_code == 200
    duel = created.json()["duel"]
    duel_id = duel["duel_id"]
    assert duel["status"] == "created"

    inj = c.post(f"/api/netdiag/lab/duels/{duel_id}/inject", json={"detail": "unit-test simulated inject"})
    assert inj.status_code == 200
    assert inj.json()["duel"]["status"] == "injected"

    start = (datetime.now() - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    session_resp = c.post(
        "/api/netdiag/sessions",
        json={
            "question": f"routing instability {token}",
            "fault_window": {"start_at": start, "end_at": end, "timezone": "Asia/Singapore"},
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "10.0.0.1",
                    "username": "u",
                    "password": "p",
                    "vendor_hint": "cisco",
                    "jump_mode": "direct",
                }
            ],
        },
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["session"]["session_id"]

    bind = c.post(
        f"/api/netdiag/lab/duels/{duel_id}/bind_blue_session",
        json={"session_id": session_id, "round_no": 0},
    )
    assert bind.status_code == 200

    judged = c.post(
        f"/api/netdiag/lab/duels/{duel_id}/judge",
        json={
            "predicted_domain": "routing",
            "predicted_root_cause": "BGP neighbor session idle instability",
            "evidence_signals": ["bgp_peer_down", "routing_session_unstable"],
            "confidence": 0.91,
            "recovery_verified": True,
        },
    )
    assert judged.status_code == 200
    jr = judged.json().get("judge_result", {})
    assert float(jr.get("score") or 0.0) >= 70.0
    assert str(jr.get("verdict") or "") == "pass"

    promoted = c.post(f"/api/netdiag/lab/duels/{duel_id}/promote_case", json={})
    assert promoted.status_code == 200
    pdata = promoted.json()
    assert pdata.get("ok") is True
    assert str(pdata.get("case", {}).get("case_id") or "")

    rb = c.post(f"/api/netdiag/lab/duels/{duel_id}/rollback", json={"ok": True, "detail": "done"})
    assert rb.status_code == 200
    assert rb.json()["duel"]["status"] == "rolled_back"

    deleted = c.delete(f"/api/netdiag/lab/duels/{duel_id}")
    assert deleted.status_code == 200
    assert deleted.json().get("deleted") is True
