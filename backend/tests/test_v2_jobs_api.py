from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.schemas import (
    JobActionGroup,
    JobActionGroupStatus,
    JobPhase,
    JobStatus,
    RiskLevel,
    now_utc,
)


client = TestClient(app)


def _auth(api_key: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key": api_key}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _bootstrap_admin_key() -> str:
    resp = client.post("/v2/keys", json={"name": "admin", "permissions": ["*"]})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["name"] == "admin"
    assert payload["api_key"].startswith("na3_")
    return payload["api_key"]


def _create_job(
    api_key: str,
    mode: str = "diagnosis",
    *,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
    webhook_events: list[str] | None = None,
) -> str:
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
    if webhook_url:
        payload["webhook_url"] = webhook_url
    if webhook_events:
        payload["webhook_events"] = webhook_events
    resp = client.post("/v2/jobs", json=payload, headers=_auth(api_key, idempotency_key=idempotency_key))
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


def _wait_job_status(job_id: str, api_key: str, target_status: str, timeout_seconds: float = 8.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/v2/jobs/{job_id}", headers=_auth(api_key))
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        last_payload = payload
        if payload["status"] == target_status:
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

    # Repair mode requires command.execute permission.
    repair_denied = client.post(
        "/v2/jobs",
        json={
            "problem": "repair down interface",
            "mode": "repair",
            "devices": [{"host": "192.0.2.11", "protocol": "api"}],
        },
        headers=_auth(operator_key),
    )
    assert repair_denied.status_code == 403


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


def test_v2_cancel_job_endpoint(monkeypatch):
    admin_key = _bootstrap_admin_key()
    monkeypatch.setattr(
        routes.orchestrator_v2,
        "_baseline_collect_commands",
        lambda: [(f"step-{i}", "show version") for i in range(60)],
    )

    job_id = _create_job(admin_key)
    cancelled = client.post(f"/v2/jobs/{job_id}/cancel?reason=manual-stop", headers=_auth(admin_key))
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["id"] == job_id
    assert cancelled.json()["status"] in {"cancelled", "completed"}


def test_v2_idempotency_key_returns_same_job():
    admin_key = _bootstrap_admin_key()
    key = "same-request-001"
    job_a = _create_job(admin_key, idempotency_key=key)
    job_b = _create_job(admin_key, idempotency_key=key)
    assert job_a == job_b


def test_v2_query_jobs_supports_pagination_and_total():
    admin_key = _bootstrap_admin_key()
    _create_job(admin_key, idempotency_key="query-job-1")
    _create_job(admin_key, idempotency_key="query-job-2")

    query = client.get("/v2/jobs/query?offset=0&limit=1", headers=_auth(admin_key))
    assert query.status_code == 200, query.text
    payload = query.json()
    assert payload["total"] >= 2
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert len(payload["items"]) == 1


def test_v2_webhook_dispatch_on_selected_events(monkeypatch):
    admin_key = _bootstrap_admin_key()
    captured: list[str] = []

    async def _fake_emit(job, event):
        captured.append(event.event_type)

    monkeypatch.setattr(routes.orchestrator_v2, "_emit_webhook_event", _fake_emit)
    _create_job(
        admin_key,
        idempotency_key="webhook-job-1",
        webhook_url="http://example.invalid/callback",
        webhook_events=["job_created"],
    )
    time.sleep(0.05)
    assert "job_created" in captured


def test_v2_batch_action_decisions():
    admin_key = _bootstrap_admin_key()
    job_id = _create_job(admin_key, mode="repair")

    task = routes.orchestrator_v2._tasks.get(job_id)
    if task and not task.done():
        task.cancel()

    job = routes.orchestrator_v2._jobs[job_id]
    device = job.devices[0]
    group = JobActionGroup(
        job_id=job.id,
        device_id=device.id,
        title="batch-repair",
        commands=["configure terminal", "interface Ethernet2", "no shutdown", "end", "write memory"],
        risk_level=RiskLevel.high,
        requires_approval=True,
        status=JobActionGroupStatus.pending_approval,
    )
    job.action_groups = [group]
    job.phase = JobPhase.approve
    job.status = JobStatus.waiting_approval
    job.updated_at = now_utc()
    routes.orchestrator_v2._append_event(
        job,
        "plan_completed",
        {"action_group_count": 1, "pending_approval": 1, "auto_approved": 0},
    )
    routes.orchestrator_v2._save_state()

    timeline = client.get(f"/v2/jobs/{job_id}/timeline", headers=_auth(admin_key))
    assert timeline.status_code == 200, timeline.text
    pending_ids = [
        row["id"] for row in timeline.json()["job"]["action_groups"] if row["status"] == "pending_approval"
    ]
    assert pending_ids

    approved = client.post(
        f"/v2/jobs/{job_id}/actions/approve-batch",
        json={"action_group_ids": pending_ids, "reason": "approve-batch-test"},
        headers=_auth(admin_key),
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()
    assert approved_payload["updated"] == len(pending_ids)
    assert approved_payload["total"] == len(pending_ids)

    job_id_2 = _create_job(admin_key, mode="repair")
    task2 = routes.orchestrator_v2._tasks.get(job_id_2)
    if task2 and not task2.done():
        task2.cancel()

    job2 = routes.orchestrator_v2._jobs[job_id_2]
    device2 = job2.devices[0]
    group2 = JobActionGroup(
        job_id=job2.id,
        device_id=device2.id,
        title="batch-repair-2",
        commands=["configure terminal", "interface Ethernet3", "shutdown", "end", "write memory"],
        risk_level=RiskLevel.high,
        requires_approval=True,
        status=JobActionGroupStatus.pending_approval,
    )
    job2.action_groups = [group2]
    job2.phase = JobPhase.approve
    job2.status = JobStatus.waiting_approval
    job2.updated_at = now_utc()
    routes.orchestrator_v2._append_event(
        job2,
        "plan_completed",
        {"action_group_count": 1, "pending_approval": 1, "auto_approved": 0},
    )
    routes.orchestrator_v2._save_state()

    timeline2 = client.get(f"/v2/jobs/{job_id_2}/timeline", headers=_auth(admin_key))
    assert timeline2.status_code == 200, timeline2.text
    pending_ids_2 = [
        row["id"] for row in timeline2.json()["job"]["action_groups"] if row["status"] == "pending_approval"
    ]
    rejected = client.post(
        f"/v2/jobs/{job_id_2}/actions/reject-batch",
        json={"action_group_ids": pending_ids_2, "reason": "reject-batch-test"},
        headers=_auth(admin_key),
    )
    assert rejected.status_code == 200, rejected.text
    rejected_payload = rejected.json()
    assert rejected_payload["updated"] == len(pending_ids_2)
    post_timeline = client.get(f"/v2/jobs/{job_id_2}/timeline", headers=_auth(admin_key))
    assert post_timeline.status_code == 200
    statuses = {row["status"] for row in post_timeline.json()["job"]["action_groups"]}
    assert "rejected" in statuses


def test_v2_topology_and_rca_weights_updates():
    admin_key = _bootstrap_admin_key()
    job_id = _create_job(admin_key, idempotency_key="topology-weights-job")
    _wait_job_done(job_id, admin_key)

    topology = client.put(
        f"/v2/jobs/{job_id}/topology",
        json={
            "replace": True,
            "edges": [
                {
                    "source": "192.0.2.10",
                    "target": "192.0.2.11",
                    "kind": "uplink",
                    "confidence": 0.88,
                    "reason": "manual seed",
                }
            ],
        },
        headers=_auth(admin_key),
    )
    assert topology.status_code == 200, topology.text
    assert topology.json()["id"] == job_id

    weights = client.put(
        f"/v2/jobs/{job_id}/rca-weights",
        json={
            "rca_weights": {
                "anomaly": 0.4,
                "timing": 0.2,
                "topology": 0.2,
                "change": 0.1,
                "consistency": 0.1,
            }
        },
        headers=_auth(admin_key),
    )
    assert weights.status_code == 200, weights.text
    timeline = client.get(f"/v2/jobs/{job_id}/timeline", headers=_auth(admin_key))
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["job"]["rca_weights"]["anomaly"] == 0.4


def test_v2_api_key_update_rotate_and_expiry():
    admin_key = _bootstrap_admin_key()
    created = client.post(
        "/v2/keys",
        json={"name": "rotatable", "permissions": ["job.read", "job.write"]},
        headers=_auth(admin_key),
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]

    patched = client.patch(
        f"/v2/keys/{key_id}",
        json={"enabled": False, "disabled_reason": "maintenance"},
        headers=_auth(admin_key),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False
    assert patched.json()["disabled_reason"] == "maintenance"

    rotated = client.post(
        f"/v2/keys/{key_id}/rotate",
        json={"name": "rotated-key", "permissions": ["job.read", "job.write"]},
        headers=_auth(admin_key),
    )
    assert rotated.status_code == 200, rotated.text
    rotated_payload = rotated.json()
    assert rotated_payload["api_key"].startswith("na3_")
    assert rotated_payload["rotated_from_id"] == key_id

    expired_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    expired_created = client.post(
        "/v2/keys",
        json={"name": "expired", "permissions": ["job.read"], "expires_at": expired_time},
        headers=_auth(admin_key),
    )
    assert expired_created.status_code == 200, expired_created.text
    expired_key = expired_created.json()["api_key"]
    denied = client.get("/v2/jobs", headers=_auth(expired_key))
    assert denied.status_code == 401


def test_v2_audit_filters_and_export_formats():
    admin_key = _bootstrap_admin_key()
    _create_job(admin_key, idempotency_key="audit-filter-job")

    logs = client.get("/v2/audit/logs?action=job.create&status=ok&limit=10&offset=0", headers=_auth(admin_key))
    assert logs.status_code == 200, logs.text
    for row in logs.json():
        assert row["action"] == "job.create"
        assert row["status"] == "ok"

    csv_report = client.get("/v2/audit/reports?format=csv", headers=_auth(admin_key))
    assert csv_report.status_code == 200, csv_report.text
    csv_payload = csv_report.json()
    assert csv_payload["filename"] == "audit-report.csv"
    assert "content" in csv_payload

    pdf_report = client.get("/v2/audit/reports?format=pdf", headers=_auth(admin_key))
    assert pdf_report.status_code == 200, pdf_report.text
    pdf_payload = pdf_report.json()
    assert pdf_payload["filename"] == "audit-report.pdf"
    assert "content" in pdf_payload


def test_v2_permission_templates_endpoint():
    admin_key = _bootstrap_admin_key()
    ok = client.get("/v2/security/permission-templates", headers=_auth(admin_key))
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert "templates" in payload
    assert payload["templates"]["platform_admin"] == ["*"]

    # no permission key should be denied
    operator = client.post(
        "/v2/keys",
        json={"name": "operator-no-policy", "permissions": ["job.read"]},
        headers=_auth(admin_key),
    )
    assert operator.status_code == 200
    operator_key = operator.json()["api_key"]
    denied = client.get("/v2/security/permission-templates", headers=_auth(operator_key))
    assert denied.status_code == 403


def test_v2_internal_ui_header_can_bypass_api_key_for_local_ui():
    payload = {
        "name": "ui-bypass-job",
        "problem": "ui local diagnostics",
        "mode": "diagnosis",
        "devices": [{"host": "192.0.2.10", "protocol": "api"}],
    }
    created = client.post(
        "/v2/jobs",
        json=payload,
        headers={"X-Internal-UI": "1"},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    got = client.get(f"/v2/jobs/{job_id}", headers={"X-Internal-UI": "1"})
    assert got.status_code == 200, got.text
