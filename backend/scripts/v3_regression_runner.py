#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V3 multi-device regression scenario via /v2 API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend URL")
    parser.add_argument("--api-key", required=True, help="V2 API key")
    parser.add_argument("--scenario", required=True, help="Path to scenario json")
    parser.add_argument("--timeout", type=float, default=600.0, help="Max wait seconds")
    parser.add_argument("--poll", type=float, default=1.0, help="Poll interval seconds")
    parser.add_argument("--output", default="", help="Optional output markdown report path")
    return parser.parse_args()


def load_scenario(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("scenario must be a json object")
    required = ["problem", "devices"]
    for key in required:
        if key not in data:
            raise ValueError(f"missing scenario field: {key}")
    if not isinstance(data["devices"], list) or not data["devices"]:
        raise ValueError("scenario.devices must be non-empty array")
    return data


def build_job_payload(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name") or "v3-regression-job",
        "problem": s["problem"],
        "mode": s.get("mode") or "diagnosis",
        "max_gap_seconds": int(s.get("max_gap_seconds") or 300),
        "topology_mode": s.get("topology_mode") or "hybrid",
        "max_device_concurrency": int(s.get("max_device_concurrency") or 20),
        "execution_policy": s.get("execution_policy") or "stop_on_failure",
        "window_start": s.get("window_start"),
        "window_end": s.get("window_end"),
        "devices": s["devices"],
        "topology_edges": s.get("topology_edges") or [],
        "webhook_url": s.get("webhook_url"),
        "webhook_events": s.get("webhook_events") or [],
    }


def wait_job(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    job_id: str,
    timeout: float,
    poll: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"{base_url}/v2/jobs/{job_id}", headers={"X-API-Key": api_key}, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
        last = payload
        status = str(payload.get("status") or "")
        phase = str(payload.get("phase") or "")
        print(f"[job] status={status:<16} phase={phase:<10} pending={payload.get('pending_action_groups', 0)}")
        if status in TERMINAL_STATUSES:
            return payload
        time.sleep(max(0.2, poll))
    return last


def format_report(timeline: dict[str, Any]) -> str:
    job = timeline.get("job", {})
    events = timeline.get("events", [])
    rca = job.get("rca_result") or {}
    lines: list[str] = []
    lines.append(f"# V3 Regression Report: {job.get('id', '-')}")
    lines.append("")
    lines.append(f"- Name: {job.get('name', '-')}")
    lines.append(f"- Problem: {job.get('problem', '-')}")
    lines.append(f"- Status: {job.get('status', '-')}")
    lines.append(f"- Phase: {job.get('phase', '-')}")
    lines.append(f"- Mode: {job.get('mode', '-')}")
    lines.append(f"- Devices: {len(job.get('devices') or [])}")
    lines.append(f"- Commands: {len(job.get('command_results') or [])}")
    lines.append("")
    lines.append("## RCA")
    lines.append("")
    lines.append(f"- Root device: {rca.get('root_device_host') or rca.get('root_device_id') or '-'}")
    lines.append(f"- Confidence: {rca.get('confidence', 0)}")
    lines.append(f"- Summary: {rca.get('summary', '-')}")
    lines.append(f"- Recommendation: {rca.get('recommendation', '-')}")
    lines.append("")
    lines.append("## Causal Edges")
    lines.append("")
    edges = job.get("causal_edges") or []
    if not edges:
        lines.append("- (none)")
    else:
        for edge in edges:
            lines.append(
                f"- {edge.get('source_device_id', '-')} -> {edge.get('target_device_id', '-')} | "
                f"kind={edge.get('kind', '-')} confidence={edge.get('confidence', '-')}")
    lines.append("")
    lines.append("## Action Groups")
    lines.append("")
    groups = job.get("action_groups") or []
    if not groups:
        lines.append("- (none)")
    else:
        for group in groups:
            cmds = "; ".join(group.get("commands") or [])
            lines.append(
                f"- [{group.get('status', '-')}] {group.get('title', '-')} "
                f"device={group.get('device_id', '-')} risk={group.get('risk_level', '-')} cmd={cmds}")
    lines.append("")
    lines.append("## Events")
    lines.append("")
    for event in events[-120:]:
        lines.append(f"- [{event.get('seq_no', '-')}] {event.get('event_type', '-')} @ {event.get('created_at', '-')}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    scenario = load_scenario(args.scenario)
    payload = build_job_payload(scenario)

    idempotency = f"v3-regression-{int(time.time())}"
    headers = {
        "X-API-Key": args.api_key,
        "Idempotency-Key": idempotency,
        "Content-Type": "application/json",
    }

    with httpx.Client() as client:
        print(f"[create] scenario={args.scenario}")
        resp = client.post(f"{base_url}/v2/jobs", headers=headers, json=payload, timeout=30.0)
        resp.raise_for_status()
        job = resp.json()
        job_id = str(job["id"])
        print(f"[create] job_id={job_id}")

        status = wait_job(client, base_url, args.api_key, job_id, args.timeout, args.poll)
        print(f"[done] status={status.get('status')} phase={status.get('phase')}")

        tl = client.get(f"{base_url}/v2/jobs/{job_id}/timeline", headers={"X-API-Key": args.api_key}, timeout=20.0)
        tl.raise_for_status()
        timeline = tl.json()
        report = format_report(timeline)

        output = args.output.strip()
        if not output:
            output = f"v3_regression_{job_id}.md"
        output_path = Path(output).expanduser().resolve()
        output_path.write_text(report, encoding="utf-8")
        print(f"[report] {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
