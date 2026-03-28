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
    parser = argparse.ArgumentParser(description="Run multi-device regression scenario via unified /api/runs API")
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


def normalize_operation_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "inspection":
        return "query"
    if raw == "repair":
        return "config"
    if raw in {"diagnosis", "query", "config"}:
        return raw
    return "diagnosis"


def build_run_payload(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name") or "v3-regression-job",
        "problem": s["problem"],
        "operation_mode": normalize_operation_mode(s.get("operation_mode") or s.get("mode")),
        "automation_level": s.get("automation_level") or "assisted",
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


def wait_run(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    run_id: str,
    timeout: float,
    poll: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"{base_url}/api/runs/{run_id}", headers={"X-API-Key": api_key}, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
        last = payload
        status = str(payload.get("status") or "")
        phase = str(payload.get("phase") or "")
        print(f"[run] status={status:<16} phase={phase:<10} pending={payload.get('pending_actions', 0)}")
        if status in TERMINAL_STATUSES:
            return payload
        time.sleep(max(0.2, poll))
    return last


def format_report(run_timeline: dict[str, Any]) -> str:
    run = run_timeline.get("run", {})
    timeline = run_timeline.get("timeline", {})
    payload = run_timeline.get("payload") or {}
    service_trace = run_timeline.get("service_trace") or {}
    summary = timeline.get("summary") or {}
    session = timeline.get("session") or {}
    commands = timeline.get("commands") or []
    trace_steps = service_trace.get("steps") or []
    rca = payload.get("rca_result") or {}
    lines: list[str] = []
    lines.append(f"# Unified Run Regression Report: {run.get('source_id', '-')}")
    lines.append("")
    lines.append(f"- Run ID: {run.get('id', '-')}")
    lines.append(f"- Name: {run.get('name', '-')}")
    lines.append(f"- Problem: {run.get('problem', '-')}")
    lines.append(f"- Status: {run.get('status', '-')}")
    lines.append(f"- Phase: {run.get('phase', '-')}")
    lines.append(f"- Mode: {run.get('operation_mode', '-')}")
    lines.append(f"- Devices: {run.get('device_count', 0)}")
    lines.append(f"- Commands: {len(commands)}")
    lines.append(f"- Session: {session.get('id', '-')}")
    lines.append("")
    lines.append("## RCA")
    lines.append("")
    lines.append(f"- Root device: {rca.get('root_device_host') or rca.get('root_device_id') or summary.get('device_scope') or '-'}")
    lines.append(f"- Confidence: {rca.get('confidence', summary.get('confidence', 0))}")
    lines.append(f"- Summary: {rca.get('summary') or summary.get('root_cause') or '-'}")
    lines.append(f"- Recommendation: {rca.get('recommendation') or summary.get('recommendation') or '-'}")
    lines.append("")
    lines.append("## Causal Edges")
    lines.append("")
    edges = payload.get("causal_edges") or []
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
    groups = payload.get("action_groups") or []
    if not groups:
        lines.append("- (none)")
    else:
        for group in groups:
            cmds = "; ".join(group.get("commands") or [])
            lines.append(
                f"- [{group.get('status', '-')}] {group.get('title', '-')} "
                f"device={group.get('device_id', '-')} risk={group.get('risk_level', '-')} cmd={cmds}")
    lines.append("")
    lines.append("## Trace Steps")
    lines.append("")
    for step in trace_steps[-120:]:
        lines.append(
            f"- [{step.get('seq_no', '-')}] {step.get('step_type', '-')} / {step.get('status', '-')} / "
            f"{step.get('title', '-')} @ {step.get('started_at', '-')}"
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    scenario = load_scenario(args.scenario)
    payload = build_run_payload(scenario)

    idempotency = f"v3-regression-{int(time.time())}"
    headers = {
        "X-API-Key": args.api_key,
        "Idempotency-Key": idempotency,
        "Content-Type": "application/json",
    }

    with httpx.Client() as client:
        print(f"[create] scenario={args.scenario}")
        resp = client.post(f"{base_url}/api/runs", headers=headers, json=payload, timeout=30.0)
        resp.raise_for_status()
        run = resp.json()
        run_id = str(run["id"])
        print(f"[create] run_id={run_id}")

        status = wait_run(client, base_url, args.api_key, run_id, args.timeout, args.poll)
        print(f"[done] status={status.get('status')} phase={status.get('phase')}")

        tl = client.get(f"{base_url}/api/runs/{run_id}/timeline", headers={"X-API-Key": args.api_key}, timeout=20.0)
        tl.raise_for_status()
        run_timeline = tl.json()
        report = format_report(run_timeline)

        output = args.output.strip()
        if not output:
            output = f"run_regression_{run.get('source_id', run_id)}.md"
        output_path = Path(output).expanduser().resolve()
        output_path.write_text(report, encoding="utf-8")
        print(f"[report] {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
