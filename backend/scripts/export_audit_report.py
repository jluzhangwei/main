#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export /v2 audit report to local file")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend URL")
    parser.add_argument("--api-key", required=True, help="API key with audit.read")
    parser.add_argument("--format", default="json", choices=["json", "csv", "pdf"], help="Report format")
    parser.add_argument("--action", default="", help="Optional action filter")
    parser.add_argument("--status", default="", help="Optional status filter")
    parser.add_argument("--actor-key-id", default="", help="Optional actor key id filter")
    parser.add_argument("--output", default="", help="Output file path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    params: dict[str, str] = {"format": args.format}
    if args.action.strip():
        params["action"] = args.action.strip()
    if args.status.strip():
        params["status"] = args.status.strip()
    if args.actor_key_id.strip():
        params["actor_key_id"] = args.actor_key_id.strip()

    headers = {"X-API-Key": args.api_key}

    with httpx.Client() as client:
        resp = client.get(f"{base_url}/v2/audit/reports", headers=headers, params=params, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()

    filename = payload.get("filename") or f"audit_report.{args.format}"
    output = args.output.strip() or filename
    path = Path(output).expanduser().resolve()
    content = payload.get("content", "")
    path.write_text(str(content), encoding="utf-8")
    print(f"exported: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
