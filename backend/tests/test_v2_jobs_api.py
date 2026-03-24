from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app


client = TestClient(app)


def _auth(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _bootstrap_admin_key() -> str:
    resp = client.post("/v2/keys", json={"name": "admin", "permissions": ["*"]})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["name"] == "admin"
    assert payload["api_key"].startswith("na3_")
    return payload["api_key"]


def _create_job(api_key: str, mode: str = "diagnosis") -> str:
    payload = {
        "name": "v2-test-job",
        "problem": "check interfaces and root cause",
        "mode": mode,
        "devices": [
            {
                "host": "192.0.2.10",
                "protocol": "api",
                "vendor": "unknown",
            }
        ],
        "max_gap_seconds": 300,
        "topology_mode": "hybrid",
    }
    resp = client.post("/v2/jobs", json=payload, headers=_auth(api_key))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _wait_job_done(job_id: str, api_key: str, timeout_seconds: float = 8.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/v2/jobs/{job_id}", headers=_auth(api_key))
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        last_payload = payload
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.2)
    return last_payload


@pytest.fixture(autouse=True)
def speed_up_collect(monkeypatch):
    monkeypatch.setattr(
        routes.orchestrator_v2,
        "_baseline_collect_commands",
        lambda: [("版本探测", "show version"), ("接口摘要", "show ip interface brief"), ("权限探测", "show privilege")],
    )


def test_v2_key_bootstrap_and_permission_checks():
    admin_key = _bootstrap_admin_key()

    # Existing keys -> unauthenticated key creation must be denied.
    denied = client.post("/v2/keys", json={"name": "denied", "permissions": ["job.read"]})
    assert denied.status_code == 401

    # Create operator key via admin policy permission.
    op = client.post(
        "/v2/keys",
        json={"name": "operator", "permissions": ["job.write", "job.read"]},
        headers=_auth(admin_key),
    )
    assert op.status_code == 200, op.text
    operator_key = op.json()["api_key"]

    # No auth -> cannot create job.
    no_auth = client.post("/v2/jobs", json={"problem": "x", "devices": [{"host": "192.0.2.1"}]})
    assert no_auth.status_code == 401

    # Operator can create/read jobs, but cannot read audit.
    job_id = _create_job(operator_key)
    read_job = client.get(f"/v2/jobs/{job_id}", headers=_auth(operator_key))
    assert read_job.status_code == 200
    no_audit = client.get("/v2/audit/logs", headers=_auth(operator_key))
    assert no_audit.status_code == 403


def test_v2_job_timeline_events_and_report():
    admin_key = _bootstrap_admin_key()
    job_id = _create_job(admin_key)
    _wait_job_done(job_id, admin_key)

    timeline = client.get(f"/v2/jobs/{job_id}/timeline", headers=_auth(admin_key))
    assert timeline.status_code == 200, timeline.text
    timeline_payload = timeline.json()
    assert timeline_payload["job"]["id"] == job_id
    assert isinstance(timeline_payload["events"], list)

    report_json = client.get(f"/v2/jobs/{job_id}/report?format=json", headers=_auth(admin_key))
    assert report_json.status_code == 200
    assert report_json.json()["job"]["id"] == job_id

    report_md = client.get(f"/v2/jobs/{job_id}/report?format=markdown", headers=_auth(admin_key))
    assert report_md.status_code == 200
    md_payload = report_md.json()
    assert md_payload["filename"].endswith(".md")
    assert md_payload["mime_type"] == "text/markdown"


def test_v2_keys_and_audit_endpoints():
    admin_key = _bootstrap_admin_key()
    created = client.post(
        "/v2/keys",
        json={"name": "auditor", "permissions": ["audit.read"]},
        headers=_auth(admin_key),
    )
    assert created.status_code == 200, created.text
    auditor = created.json()

    listed = client.get("/v2/keys", headers=_auth(admin_key))
    assert listed.status_code == 200
    assert any(item["id"] == auditor["id"] for item in listed.json())

    logs = client.get("/v2/audit/logs", headers=_auth(admin_key))
    assert logs.status_code == 200
    assert isinstance(logs.json(), list)

    report = client.get("/v2/audit/reports", headers=_auth(admin_key))
    assert report.status_code == 200
    assert "total" in report.json()

    deleted = client.delete(f"/v2/keys/{auditor['id']}", headers=_auth(admin_key))
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
