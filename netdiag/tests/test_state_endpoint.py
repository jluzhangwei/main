from fastapi.testclient import TestClient

from app.main import app


def test_state_ingest_and_fetch():
    c = TestClient(app)
    r = c.post(
        "/api/netdiag/state/ingest",
        json={
            "points": [
                {"ts": 1700000000, "device_id": "dev-x", "domain": "link", "key": "domain_delta", "value": 0.23},
                {"ts": 1700000010, "device_id": "dev-x", "domain": "global", "key": "command_valid_rate", "value": 1.0},
            ]
        },
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    q = c.get("/api/netdiag/state/dev-x", params={"domain": "link", "key": "domain_delta", "limit": 10})
    assert q.status_code == 200
    payload = q.json()
    assert payload.get("ok") is True
    assert payload.get("count", 0) >= 1
