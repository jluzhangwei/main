from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routers import netdiag as netdiag_router


def test_config_snapshot_history_and_diff(monkeypatch, tmp_path):
    counter = {"n": 0}

    async def _fake_run_read_only_commands(device, commands, output_dir, timeout_per_command=30, debug_mode=True):  # noqa: ARG001
        counter["n"] += 1
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{counter['n']:02d}_show_running-config.txt"
        text = "interface Eth1/1\n description OLD\n"
        if counter["n"] >= 2:
            text = "interface Eth1/1\n description NEW\n router bgp 65001\n"
        out_file.write_text(text, encoding="utf-8")
        return [
            {
                "command": commands[0] if commands else "show running-config",
                "status": "success",
                "output_file": out_file.as_posix(),
                "error": "",
                "duration_sec": 0.1,
                "debug_log_path": None,
            }
        ]

    monkeypatch.setattr(netdiag_router, "run_read_only_commands", _fake_run_read_only_commands)

    c = TestClient(app)
    start = (datetime.now() - timedelta(minutes=20)).replace(microsecond=0).isoformat()
    end = datetime.now().replace(microsecond=0).isoformat()
    create = c.post(
        "/api/netdiag/sessions",
        json={
            "question": "config snapshot test",
            "fault_window": {"start_at": start, "end_at": end, "timezone": "Asia/Singapore"},
            "devices": [
                {
                    "device_id": "dev-1",
                    "device_ip": "10.0.0.1",
                    "username": "u",
                    "password": "p",
                    "jump_mode": "direct",
                }
            ],
        },
    )
    assert create.status_code == 200
    sid = create.json()["session"]["session_id"]

    s1 = c.post("/api/netdiag/config/snapshot", json={"session_id": sid})
    assert s1.status_code == 200
    assert s1.json().get("ok") is True
    s2 = c.post("/api/netdiag/config/snapshot", json={"session_id": sid})
    assert s2.status_code == 200
    assert s2.json().get("ok") is True

    hist = c.get("/api/netdiag/config/dev-1/history", params={"session_id": sid, "limit": 10})
    assert hist.status_code == 200
    items = hist.json().get("items", [])
    assert len(items) >= 2
    a = items[0]["snapshot_id"]
    b = items[1]["snapshot_id"]
    diff = c.post("/api/netdiag/config/diff", json={"snapshot_id_a": a, "snapshot_id_b": b})
    assert diff.status_code == 200
    payload = diff.json()
    assert payload.get("ok") is True
    assert isinstance(payload.get("signals"), list)
