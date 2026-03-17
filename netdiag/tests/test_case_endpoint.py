from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.diagnosis.models import PlannedCommand
from app.main import app


def test_case_from_session_and_library_endpoints():
    c = TestClient(app)
    unique = uuid4().hex[:8]
    start = (datetime.now() - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    create = c.post(
        "/api/netdiag/sessions",
        json={
            "question": f"case endpoint test {unique}",
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
    assert create.status_code == 200
    sid = create.json()["session"]["session_id"]

    mgr = app.state.diag_session_manager
    rnd = mgr.append_round(
        sid,
        planner_summary="s",
        planner_raw_output="raw",
        commands=[
            PlannedCommand(
                command_id=f"cmd-{unique}",
                device_id="dev-1",
                intent="bgp_summary",
                command="show ip bgp summary",
                reason="check BGP",
            )
        ],
    )
    assert rnd is not None
    mgr.set_round_analysis(
        sid,
        round_no=1,
        analysis_result=f"analysis-{unique}",
        hypotheses=[
            {
                "hypothesis_id": f"hyp-{unique}",
                "title": f"Routing instability {unique}",
                "domain": "routing",
                "score": 0.86,
                "confidence": 0.86,
                "status": "likely",
                "evidence_for": ["bgp idle"],
                "evidence_against": [],
                "next_intents": ["bgp_summary"],
            }
        ],
        evidence_signals=[
            {
                "domain": "routing",
                "polarity": "positive",
                "signal": f"routing_session_unstable_{unique}",
                "weight": 0.18,
            }
        ],
        stop_decision={"recommend_conclude": True, "reason": "unit-test"},
    )

    built = c.post(
        f"/api/netdiag/cases/from_session/{sid}",
        json={"title": f"case-{unique}", "root_cause": f"root-{unique}"},
    )
    assert built.status_code == 200
    payload = built.json()
    assert payload.get("ok") is True
    cid = payload["case"]["case_id"]

    lib = c.get("/api/netdiag/cases/library")
    assert lib.status_code == 200
    items = lib.json().get("items", [])
    assert any(str(x.get("case_id")) == cid for x in items)

    search = c.post(
        "/api/netdiag/cases/search",
        json={
            "query_text": unique,
            "profile": "cisco_iosxe",
            "domain": "routing",
            "evidence_text": f"routing_session_unstable_{unique}",
        },
    )
    assert search.status_code == 200
    sh = search.json().get("items", [])
    assert any(str(x.get("case_id")) == cid for x in sh)

    disable = c.post(f"/api/netdiag/cases/library/{cid}/enabled", json={"enabled": False})
    assert disable.status_code == 200
    assert disable.json()["case"]["enabled"] is False

    delete = c.delete(f"/api/netdiag/cases/library/{cid}")
    assert delete.status_code == 200
    assert delete.json().get("deleted") is True
