from fastapi.testclient import TestClient

from app.main import app


def test_create_session_defaults_fault_window_when_missing():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/sessions",
        json={
            "question": "设备 192.168.0.88 端口 down，帮我定位原因",
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "192.168.0.88",
                    "username": "u",
                    "password": "p",
                    "jump_mode": "direct",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    session = data.get("session") or {}
    fw = session.get("fault_window") or {}
    assert str(fw.get("start_at") or "").strip()
    assert str(fw.get("end_at") or "").strip()
    assert str(fw.get("timezone") or "").strip() == "Asia/Singapore"
