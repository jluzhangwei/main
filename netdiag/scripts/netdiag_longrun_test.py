#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _utc_now().isoformat()


class ApiError(RuntimeError):
    pass


class NetdiagApi:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"{method} {path} -> HTTP {exc.code}: {body}") from exc
        except Exception as exc:
            raise ApiError(f"{method} {path} failed: {exc}") from exc
        try:
            obj = json.loads(raw)
        except Exception as exc:
            raise ApiError(f"{method} {path} returned non-json: {raw[:300]}") from exc
        return obj if isinstance(obj, dict) else {"ok": False, "raw": obj}

    def get(self, path: str, timeout: int = 60) -> dict[str, Any]:
        return self._request("GET", path, None, timeout)

    def post(self, path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
        return self._request("POST", path, payload, timeout)


def _run_step_with_busy_retry(
    api: NetdiagApi,
    *,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_sec: int = 180,
    max_wait_sec: int = 600,
    sleep_sec: float = 2.0,
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    while True:
        out = api.post(path, payload=payload, timeout=timeout_sec)
        if bool(out.get("busy", False)):
            if time.monotonic() - started >= max_wait_sec:
                raise ApiError(f"step timeout: {path} still busy after {max_wait_sec}s")
            time.sleep(sleep_sec)
            continue
        return out, round(time.monotonic() - started, 3)


def _safe_get_round(resp: dict[str, Any]) -> dict[str, Any]:
    row = resp.get("round")
    return row if isinstance(row, dict) else {}


def _contains_shutdown_evidence(text: str) -> bool:
    low = str(text or "").lower()
    pats = (
        "adminstatus=down",
        "the interface is shut down",
        "administratively down",
        "shutdown",
        "管理性关闭",
    )
    return any(p in low for p in pats)


def _contains_lab_noise(text: str) -> bool:
    low = str(text or "").lower()
    return ("[lab]" in low) or ("bgp neighbor down injection" in low)


def run_single_iteration(
    api: NetdiagApi,
    *,
    device_ip: str,
    username: str,
    password: str,
    question: str,
    fault_start: str,
    fault_end: str,
    timezone_name: str,
    timeout_per_device: int,
) -> dict[str, Any]:
    create_payload = {
        "question": question,
        "fault_window": {
            "start_at": fault_start,
            "end_at": fault_end,
            "timezone": timezone_name,
        },
        "context_lines": 3,
        "per_device_timeout": int(timeout_per_device),
        "focus_goals": [
            "Locate Ethernet1/0/6 alarm root cause",
            "Confirm whether shutdown operation caused the alarm",
            "Provide evidence chain in calibrated window",
        ],
        "focus_lock": True,
        "devices": [
            {
                "device_ip": device_ip,
                "device_port": 22,
                "username": username,
                "password": password,
                "vendor_hint": "huawei",
                "jump_mode": "direct",
            }
        ],
    }

    create_started = time.monotonic()
    created = api.post("/api/netdiag/sessions", payload=create_payload, timeout=90)
    create_cost = round(time.monotonic() - create_started, 3)
    session = created.get("session") if isinstance(created.get("session"), dict) else {}
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise ApiError("create session failed: missing session_id")

    baseline, baseline_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/baseline_collect",
        timeout_sec=timeout_per_device + 120,
        max_wait_sec=timeout_per_device + 240,
    )
    if not bool(baseline.get("ok", False)) and not bool(baseline.get("baseline_reused", False)):
        raise ApiError(f"baseline failed for {session_id}: {baseline}")

    plan, plan_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/plan",
        payload={"max_commands": 6, "ai_timeout_sec": 90, "ai_retries": 1},
        timeout_sec=180,
        max_wait_sec=480,
    )
    plan_round = _safe_get_round(plan)
    round_no = int(plan_round.get("round_no") or 1)

    approve, approve_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/approve",
        payload={"approved": True},
        timeout_sec=90,
        max_wait_sec=180,
    )
    if not bool(approve.get("ok", False)):
        raise ApiError(f"approve failed for {session_id}/round-{round_no}: {approve}")

    execute, execute_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/execute",
        timeout_sec=timeout_per_device + 180,
        max_wait_sec=timeout_per_device + 300,
    )
    if not bool(execute.get("ok", False)):
        raise ApiError(f"execute failed for {session_id}/round-{round_no}: {execute}")

    analyze, analyze_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/analyze",
        payload={"ai_timeout_sec": 90, "ai_retries": 1},
        timeout_sec=240,
        max_wait_sec=720,
    )
    if not bool(analyze.get("ok", False)):
        raise ApiError(f"analyze failed for {session_id}/round-{round_no}: {analyze}")
    round_row = _safe_get_round(analyze)
    analysis_text = str(round_row.get("analysis_result") or "")
    stop_decision = round_row.get("stop_decision") if isinstance(round_row.get("stop_decision"), dict) else {}
    recommend_conclude = bool(stop_decision.get("recommend_conclude", False))

    conclude_cost = 0.0
    conclude_ok = False
    if recommend_conclude:
        conclude_started = time.monotonic()
        conclude = api.post(f"/api/netdiag/sessions/{session_id}/conclude", payload={}, timeout=90)
        conclude_cost = round(time.monotonic() - conclude_started, 3)
        conclude_ok = bool(conclude.get("ok", False))

    sigs = [str(x.get("signal") or "").strip() for x in (round_row.get("evidence_signals") or []) if isinstance(x, dict)]
    has_shutdown_signal = any(
        x in {"huawei_interface_shutdown_event", "huawei_interface_admin_down", "interface_admin_shutdown_detail"}
        for x in sigs
    )
    has_shutdown_text = _contains_shutdown_evidence(analysis_text)
    has_lab_noise = _contains_lab_noise(analysis_text)
    passed = bool(has_shutdown_signal and has_shutdown_text and recommend_conclude and not has_lab_noise)

    return {
        "timestamp_utc": _ts(),
        "session_id": session_id,
        "round_no": round_no,
        "status": str(round_row.get("status") or ""),
        "stop_decision": stop_decision,
        "top_hypothesis": ((round_row.get("hypotheses") or [{}])[0] if (round_row.get("hypotheses") or []) else {}),
        "has_shutdown_signal": has_shutdown_signal,
        "has_shutdown_text": has_shutdown_text,
        "has_lab_noise": has_lab_noise,
        "passed": passed,
        "durations_sec": {
            "create": create_cost,
            "baseline": baseline_cost,
            "plan": plan_cost,
            "approve": approve_cost,
            "execute": execute_cost,
            "analyze": analyze_cost,
            "conclude": conclude_cost,
            "total": round(create_cost + baseline_cost + plan_cost + approve_cost + execute_cost + analyze_cost + conclude_cost, 3),
        },
        "execution_summary": execute.get("execution_summary") if isinstance(execute.get("execution_summary"), dict) else {},
        "analysis_excerpt": analysis_text[:1200],
        "signal_names": sigs,
        "conclude_ok": conclude_ok,
    }


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, obj: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_provider_hint(state_dir: Path) -> str:
    cfg = state_dir / "gpt_config.json"
    try:
        row = json.loads(cfg.read_text(encoding="utf-8"))
        provider = str(row.get("provider") or "").strip() or "unknown"
        model_key = f"{provider}_model"
        model = str(row.get(model_key) or row.get("chatgpt_model") or "").strip() or "unknown"
        return f"{provider}:{model}"
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Long-running netdiag real-device regression")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--device-ip", default="192.168.0.88")
    parser.add_argument("--username", default="zhangwei")
    parser.add_argument("--password", default="")
    parser.add_argument("--fault-start", default="2026-03-10T20:30:00")
    parser.add_argument("--fault-end", default="2026-03-10T23:59:59")
    parser.add_argument("--timezone", default="Asia/Singapore")
    parser.add_argument("--hours", type=float, default=10.0)
    parser.add_argument("--interval-sec", type=int, default=120)
    parser.add_argument("--timeout-per-device", type=int, default=90)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    if not str(args.password or "").strip():
        raise SystemExit("missing required --password")

    api = NetdiagApi(args.base_url)
    run_tag = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    root = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path(__file__).resolve().parents[1] / "output" / "longrun" / run_tag
    root.mkdir(parents=True, exist_ok=True)
    iter_jsonl = root / "iterations.jsonl"
    summary_json = root / "summary.json"
    runtime_log = root / "runtime.log"

    provider_hint = _load_provider_hint(Path(__file__).resolve().parents[1] / "state")
    with runtime_log.open("a", encoding="utf-8") as f:
        f.write(f"{_ts()} start longrun provider={provider_hint} base_url={args.base_url}\n")

    # Guard direct-login default to avoid accidental SMC fallback.
    cfg_ok = False
    last_cfg_err = ""
    for _ in range(12):
        try:
            api.post("/api/netdiag/connection/config", payload={"config": {"smc_jump_mode": "direct"}}, timeout=60)
            cfg_ok = True
            break
        except Exception as exc:
            last_cfg_err = str(exc)
            with runtime_log.open("a", encoding="utf-8") as f:
                f.write(f"{_ts()} warn set direct mode failed: {last_cfg_err}\n")
            time.sleep(2)
    if not cfg_ok:
        with runtime_log.open("a", encoding="utf-8") as f:
            f.write(f"{_ts()} warn continue with existing connection mode after retries: {last_cfg_err}\n")

    question = (
        "设备 {ip} 在 2026-03-10 20:30 后出现 Ethernet1/0/6 告警，"
        "请基于 show/display 证据定位根因并判断是否为 shutdown 操作。"
    ).format(ip=args.device_ip)

    started = _utc_now()
    deadline = started + timedelta(hours=float(args.hours))
    counters = {"total": 0, "passed": 0, "failed": 0}
    failures: list[dict[str, Any]] = []

    while _utc_now() < deadline:
        idx = counters["total"] + 1
        try:
            row = run_single_iteration(
                api,
                device_ip=args.device_ip,
                username=args.username,
                password=args.password,
                question=question,
                fault_start=args.fault_start,
                fault_end=args.fault_end,
                timezone_name=args.timezone,
                timeout_per_device=int(args.timeout_per_device),
            )
            row["iteration"] = idx
            _append_jsonl(iter_jsonl, row)
            counters["total"] += 1
            if bool(row.get("passed", False)):
                counters["passed"] += 1
            else:
                counters["failed"] += 1
                failures.append(
                    {
                        "iteration": idx,
                        "session_id": row.get("session_id"),
                        "reason": "assertion_failed",
                        "stop_decision": row.get("stop_decision"),
                    }
                )
            with runtime_log.open("a", encoding="utf-8") as f:
                f.write(
                    f"{_ts()} iter={idx} session={row.get('session_id')} "
                    f"passed={row.get('passed')} total_cost={row.get('durations_sec', {}).get('total')}\n"
                )
        except Exception as exc:
            counters["total"] += 1
            counters["failed"] += 1
            err_row = {
                "timestamp_utc": _ts(),
                "iteration": idx,
                "passed": False,
                "error": str(exc),
            }
            _append_jsonl(iter_jsonl, err_row)
            failures.append({"iteration": idx, "reason": str(exc)})
            with runtime_log.open("a", encoding="utf-8") as f:
                f.write(f"{_ts()} iter={idx} failed error={exc}\n")

        # Incremental summary checkpoint.
        pass_rate = round((counters["passed"] / counters["total"]) if counters["total"] > 0 else 0.0, 4)
        summary = {
            "run_tag": run_tag,
            "started_utc": started.isoformat(),
            "deadline_utc": deadline.isoformat(),
            "last_update_utc": _ts(),
            "provider": provider_hint,
            "base_url": args.base_url,
            "target_device": args.device_ip,
            "scenario": {
                "fault_start": args.fault_start,
                "fault_end": args.fault_end,
                "timezone": args.timezone,
                "expected_root_cause": "interface Ethernet1/0/6 administratively shutdown",
            },
            "counters": {**counters, "pass_rate": pass_rate},
            "recent_failures": failures[-20:],
            "artifacts": {
                "iterations_jsonl": str(iter_jsonl),
                "runtime_log": str(runtime_log),
            },
        }
        _write_json(summary_json, summary)
        if _utc_now() >= deadline:
            break
        time.sleep(max(1, int(args.interval_sec)))

    with runtime_log.open("a", encoding="utf-8") as f:
        f.write(f"{_ts()} finished total={counters['total']} passed={counters['passed']} failed={counters['failed']}\n")
    print(str(summary_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
