#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified NetOps run client: one host => single run, multiple hosts => multi-device coordinated run"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend API base URL")
    parser.add_argument("--hosts", default="", help="Host list, split by comma/space/newline")
    parser.add_argument(
        "--host",
        action="append",
        dest="host_items",
        default=[],
        help="Single host, repeatable. Can be used multiple times as an alias of --hosts",
    )
    parser.add_argument("--username", required=True, help="Shared SSH username")
    parser.add_argument("--password", required=True, help="Shared SSH password")
    parser.add_argument("--protocol", default="ssh", choices=["ssh", "telnet", "api"], help="Device protocol")
    parser.add_argument("--port", type=int, default=22, help="Device port")
    parser.add_argument("--mode", default="diagnosis", choices=["diagnosis", "query", "config"], help="Task mode")
    parser.add_argument(
        "--automation-level",
        default="assisted",
        choices=["read_only", "assisted", "full_auto"],
        help="Automation level",
    )
    parser.add_argument("--question", default="", help="User question / problem statement")
    parser.add_argument("--problem", default="", help="Alias of --question")
    parser.add_argument("--api-key", default="", help="Unified API key")
    parser.add_argument(
        "--auto-create-key",
        action="store_true",
        help="Force creating a temporary API key when --api-key is empty (default behavior also tries this automatically)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto approve pending commands/action groups when approval is required",
    )
    parser.add_argument("--approve-reason", default="approved by unified client", help="Approval reason text")
    parser.add_argument("--timeout", type=float, default=600.0, help="Max wait seconds")
    parser.add_argument("--poll", type=float, default=1.0, help="Poll interval seconds")
    parser.add_argument("--save-json", default="", help="Optional output path for timeline json")
    parser.add_argument(
        "--stream-events",
        action="store_true",
        help="Stream unified run events from /api/runs/{runId}/events while waiting",
    )
    args = parser.parse_args()
    args.question = str(args.question or args.problem or "").strip()
    merged_hosts: list[str] = []
    if str(args.hosts or "").strip():
      merged_hosts.append(str(args.hosts or "").strip())
    merged_hosts.extend(str(item or "").strip() for item in (args.host_items or []) if str(item or "").strip())
    args.hosts = " ".join(merged_hosts).strip()
    if not args.hosts:
        parser.error("the following arguments are required: --hosts or --host")
    if not args.question:
        parser.error("the following arguments are required: --question or --problem")
    return args


def parse_hosts(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\s,;]+", raw.strip()) if item.strip()]


def save_json(path: str, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {target}")


def auth_headers(api_key: str) -> dict[str, str]:
    token = str(api_key or "").strip()
    return {"X-API-Key": token} if token else {}


def try_create_api_key(client: httpx.Client, base_url: str) -> str:
    payload = {"name": f"cli-auto-{int(time.time())}", "permissions": ["*"]}
    resp = client.post(
        f"{base_url}/v2/keys",
        headers={"X-Internal-UI": "1"},
        json=payload,
        timeout=20.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = str(body.get("api_key") or "").strip()
    if not token:
        raise RuntimeError("auto-created key is empty")
    print(f"[auth] auto-created api key: {body.get('key_prefix', '-')}")
    return token


def create_run(client: httpx.Client, args: argparse.Namespace, hosts: list[str], api_key: str) -> dict[str, Any]:
    payload = {
        "problem": args.question,
        "operation_mode": args.mode,
        "automation_level": args.automation_level,
        "devices": [
            {
                "host": host,
                "protocol": args.protocol,
                "port": args.port,
                "username": args.username,
                "password": args.password,
            }
            for host in hosts
        ],
    }
    resp = client.post(f"{args.base_url}/api/runs", headers=auth_headers(api_key), json=payload, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def wait_run(client: httpx.Client, args: argparse.Namespace, run_id: str, api_key: str) -> dict[str, Any]:
    deadline = time.time() + args.timeout
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"{args.base_url}/api/runs/{run_id}", headers=auth_headers(api_key), timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
        last_payload = payload
        status = str(payload.get("status") or "")
        phase = str(payload.get("phase") or "")
        pending = int(payload.get("pending_actions") or 0)
        print(f"[run] status={status:<16} phase={phase:<10} pending={pending}")

        if status in TERMINAL_RUN_STATUSES:
            return payload

        if status == "waiting_approval":
            if not args.auto_approve:
                return payload
            approve = client.post(
                f"{args.base_url}/api/runs/{run_id}/actions/approve",
                headers=auth_headers(api_key),
                json={"reason": args.approve_reason},
                timeout=30.0,
            )
            approve.raise_for_status()
            body = approve.json()
            print(f"[run] approved items updated={body.get('updated')} skipped={body.get('skipped')}")

        time.sleep(max(0.2, args.poll))
    return last_payload


def stream_run_events(base_url: str, timeout: float, run_id: str, api_key: str) -> None:
    url = f"{base_url}/api/runs/{run_id}/events?from_seq=0"
    try:
        with httpx.Client() as stream_client:
            with stream_client.stream("GET", url, headers=auth_headers(api_key), timeout=timeout) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        text = line.decode("utf-8", errors="replace")
                    else:
                        text = str(line)
                    if text.startswith("event: "):
                        print(f"[events] {text[7:]}")
                    elif text.startswith("data: "):
                        print(f"[events] {text[6:]}")
    except Exception as exc:
        print(f"[events] stream error: {exc}")


def summarize_timeline(timeline: dict[str, Any]) -> None:
    run = timeline.get("run") or {}
    payload = timeline.get("payload") or {}
    kind = str(run.get("kind") or "")
    if kind == "single":
        summary = payload.get("summary") or {}
        print(f"[single] root_cause={summary.get('root_cause', '-')} recommendation={summary.get('recommendation', '-')}")
        return
    job = payload.get("job") or {}
    rca = job.get("rca_result") or {}
    print(
        f"[multi] root={rca.get('root_device_host') or rca.get('root_device_id') or '-'} "
        f"confidence={rca.get('confidence', 0)}"
    )
    print(f"[multi] summary={rca.get('summary', '-')}")
    print(f"[multi] recommendation={rca.get('recommendation', '-')}")


def main() -> int:
    args = parse_args()
    args.base_url = args.base_url.rstrip("/")
    hosts = parse_hosts(args.hosts)
    if not hosts:
        print("No valid hosts provided")
        return 2

    try:
        with httpx.Client() as client:
            api_key = str(args.api_key or "").strip()
            if not api_key:
                try:
                    api_key = try_create_api_key(client, args.base_url)
                except Exception as exc:
                    detail = str(exc).strip() or "unknown error"
                    print("[auth] 未提供 --api-key，已自动尝试创建临时 API key，但失败。")
                    print(f"[auth] 自动创建失败原因: {detail}")
                    print("[auth] 请重试以下任一方式：")
                    print("        1) 增加 --auto-create-key（强制显式使用自动建 key 模式）")
                    print("        2) 在前端“第三方 Key 服务”页面创建后，通过 --api-key 传入")
                    return 2
            if not api_key:
                print("[auth] 缺少 API key。可直接增加 --api-key，或让客户端自动创建临时 key。")
                return 2

            run = create_run(client, args, hosts, api_key)
            run_id = str(run.get("id") or "")
            print(f"[run] id={run_id} kind={run.get('kind')} devices={run.get('device_count')}")

            event_thread: threading.Thread | None = None
            if args.stream_events:
                event_thread = threading.Thread(
                    target=stream_run_events,
                    args=(args.base_url, args.timeout, run_id, api_key),
                    name=f"run-events-{run_id}",
                    daemon=True,
                )
                event_thread.start()

            final_state = wait_run(client, args, run_id, api_key)
            timeline_resp = client.get(f"{args.base_url}/api/runs/{run_id}/timeline", headers=auth_headers(api_key), timeout=30.0)
            timeline_resp.raise_for_status()
            timeline = timeline_resp.json()

            if args.save_json:
                save_json(args.save_json, timeline)

            summarize_timeline(timeline)
            status = str(final_state.get("status") or run.get("status") or "")
            return 0 if status in {"completed", "waiting_approval", "open"} else 1
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        detail = ""
        try:
            detail = exc.response.text if exc.response is not None else ""
        except Exception:
            detail = ""
        print(f"HTTP error {status}: {detail}")
        return 1
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
