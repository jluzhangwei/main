#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any

import httpx


TERMINAL = {"completed", "failed", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple stress runner for /v2 multi-device jobs")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--api-key", default="", help="Existing admin API key. If omitted, bootstrap one.")
    parser.add_argument("--jobs", type=int, default=20, help="Number of jobs to create")
    parser.add_argument("--device-count", type=int, default=5, help="Devices per job")
    parser.add_argument("--create-concurrency", type=int, default=10, help="Concurrent create requests")
    parser.add_argument("--poll-interval", type=float, default=0.8, help="Polling interval seconds")
    parser.add_argument("--timeout", type=float, default=180.0, help="Global timeout seconds")
    parser.add_argument("--mode", choices=["diagnosis", "inspection", "repair"], default="diagnosis")
    return parser.parse_args()


async def bootstrap_key(client: httpx.AsyncClient, base_url: str) -> str:
    resp = await client.post(
        f"{base_url}/v2/keys",
        json={"name": "stress-admin", "permissions": ["*"]},
        timeout=20.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    return str(payload["api_key"])


def make_devices(job_idx: int, count: int) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for offset in range(count):
        host = f"192.0.2.{(job_idx % 150) + offset + 1}"
        devices.append({"host": host, "protocol": "api", "vendor": "unknown"})
    return devices


async def create_job(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    idx: int,
    device_count: int,
    mode: str,
) -> str:
    payload = {
        "name": f"stress-{idx:04d}",
        "problem": "stress run: multi device correlation",
        "mode": mode,
        "max_gap_seconds": 300,
        "topology_mode": "hybrid",
        "max_device_concurrency": 20,
        "devices": make_devices(idx, device_count),
    }
    resp = await client.post(
        f"{base_url}/v2/jobs",
        headers={"X-API-Key": api_key, "Idempotency-Key": f"stress-{idx}-{int(time.time())}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return str(resp.json()["id"])


async def wait_job(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    job_id: str,
    poll_interval: float,
    timeout: float,
) -> tuple[str, float]:
    start = time.perf_counter()
    deadline = start + timeout
    status = "queued"
    while time.perf_counter() < deadline:
        resp = await client.get(
            f"{base_url}/v2/jobs/{job_id}",
            headers={"X-API-Key": api_key},
            timeout=20.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        status = str(payload.get("status") or "unknown")
        if status in TERMINAL:
            return status, time.perf_counter() - start
        await asyncio.sleep(max(0.1, poll_interval))
    return status, time.perf_counter() - start


async def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    limits = httpx.Limits(max_connections=max(50, args.jobs * 2), max_keepalive_connections=20)
    async with httpx.AsyncClient(limits=limits) as client:
        api_key = args.api_key.strip()
        if not api_key:
            print("[info] No API key provided, bootstrapping admin key...")
            api_key = await bootstrap_key(client, base_url)
            print(f"[info] Bootstrap key: {api_key}")

        print(f"[info] Creating {args.jobs} jobs with concurrency={args.create_concurrency}")
        sem = asyncio.Semaphore(max(1, args.create_concurrency))

        async def create_one(i: int) -> str:
            async with sem:
                return await create_job(client, base_url, api_key, i, args.device_count, args.mode)

        started = time.perf_counter()
        job_ids = await asyncio.gather(*(create_one(i) for i in range(args.jobs)))
        create_cost = time.perf_counter() - started
        print(f"[info] Created {len(job_ids)} jobs in {create_cost:.2f}s")

        print("[info] Polling completion...")
        completed = await asyncio.gather(
            *(
                wait_job(
                    client,
                    base_url,
                    api_key,
                    job_id,
                    poll_interval=args.poll_interval,
                    timeout=args.timeout,
                )
                for job_id in job_ids
            )
        )

    by_status: dict[str, int] = {}
    durations: list[float] = []
    for status, seconds in completed:
        by_status[status] = by_status.get(status, 0) + 1
        durations.append(seconds)

    print("\n=== Stress Summary ===")
    print(f"jobs={len(job_ids)} device_count/job={args.device_count} mode={args.mode}")
    print(f"status_distribution={by_status}")
    if durations:
        print(f"latency_avg={statistics.mean(durations):.2f}s")
        print(f"latency_p50={statistics.median(durations):.2f}s")
        print(f"latency_max={max(durations):.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
