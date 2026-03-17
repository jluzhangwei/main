#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _utc_now().isoformat()


class ApiError(RuntimeError):
    pass


class HttpStatusError(ApiError):
    def __init__(self, method: str, path: str, status_code: int, body: str):
        super().__init__(f"{method} {path} -> HTTP {status_code}: {body}")
        self.method = method
        self.path = path
        self.status_code = int(status_code)
        self.body = body


class NetdiagApi:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request_raw(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 180,
        allow_error_status: bool = False,
    ) -> tuple[int, dict[str, Any]]:
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
                code = int(resp.getcode() or 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            code = int(exc.code or 500)
            if not allow_error_status:
                raise HttpStatusError(method, path, code, raw) from exc
        except Exception as exc:
            raise ApiError(f"{method} {path} failed: {exc}") from exc
        try:
            obj = json.loads(raw) if raw.strip() else {}
        except Exception as exc:
            raise ApiError(f"{method} {path} returned non-json: {raw[:300]}") from exc
        if not isinstance(obj, dict):
            obj = {"ok": False, "raw": obj}
        return code, obj

    def get(self, path: str, timeout: int = 60) -> dict[str, Any]:
        _status, body = self._request_raw("GET", path, timeout=timeout)
        return body

    def post(self, path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
        _status, body = self._request_raw("POST", path, payload=payload, timeout=timeout)
        return body

    def post_allow_error(self, path: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> tuple[int, dict[str, Any]]:
        return self._request_raw("POST", path, payload=payload, timeout=timeout, allow_error_status=True)


def _run_step_with_busy_retry(
    api: NetdiagApi,
    *,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_sec: int = 180,
    max_wait_sec: int = 300,
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


def _analysis_has_shutdown_text(text: str) -> bool:
    low = str(text or "").lower()
    needles = (
        "administratively down",
        "admin down",
        "shutdown",
        "管理性关闭",
        "接口被管理性关闭",
        "the interface is shut down",
    )
    return any(x in low for x in needles)


def _round_commands(round_row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cmd in (round_row.get("commands") or []):
        if not isinstance(cmd, dict):
            continue
        text = str(cmd.get("command") or "").strip()
        if text:
            out.append(text)
    return out


def _assert(ok: bool, msg: str) -> None:
    if not ok:
        raise AssertionError(msg)


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: str
    detail: str
    evidence: dict[str, Any]


def _default_route() -> dict[str, Any]:
    return {
        "primary": {"provider": "chatgpt", "model": "gpt-4.1-mini"},
        "failover": {"provider": "deepseek", "model": "deepseek-chat"},
    }


def _create_session_payload(device_ip: str, username: str, password: str, *, question: str, fault_start: str, fault_end: str) -> dict[str, Any]:
    return {
        "question": question,
        "fault_window": {
            "start_at": fault_start,
            "end_at": fault_end,
            "timezone": "Asia/Singapore",
        },
        "context_lines": 3,
        "per_device_timeout": 90,
        "focus_goals": [
            "定位端口告警根因",
            "确认是否为 shutdown/admin-down 导致",
            "给出时间校准后的证据链",
        ],
        "focus_lock": True,
        "devices": [
            {
                "device_id": "dev-1",
                "device_ip": device_ip,
                "device_port": 22,
                "username": username,
                "password": password,
                "vendor_hint": "huawei",
                "jump_mode": "direct",
            }
        ],
    }


def _create_real_session(
    api: NetdiagApi,
    *,
    device_ip: str,
    username: str,
    password: str,
    question: str,
    fault_start: str,
    fault_end: str,
) -> tuple[str, float]:
    payload = _create_session_payload(
        device_ip,
        username,
        password,
        question=question,
        fault_start=fault_start,
        fault_end=fault_end,
    )
    started = time.monotonic()
    body = api.post("/api/netdiag/sessions", payload=payload, timeout=90)
    cost = round(time.monotonic() - started, 3)
    session = body.get("session") if isinstance(body.get("session"), dict) else {}
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise ApiError(f"create session failed: {body}")
    return session_id, cost


def check_llm_route_guard(api: NetdiagApi) -> CheckResult:
    body = api.post("/api/netdiag/llm/route/check", payload={"llm_route": _default_route()}, timeout=30)
    runtime = body.get("runtime") if isinstance(body.get("runtime"), dict) else {}
    primary = body.get("primary") if isinstance(body.get("primary"), dict) else {}
    _assert(bool(body.get("ok", False)), "route check endpoint returned ok=false")
    _assert(bool(runtime.get("switched_to_failover", False)), "route guard did not switch from unavailable ChatGPT to failover")
    _assert(str(runtime.get("primary_provider") or "") == "deepseek", "runtime primary provider should be deepseek after failover switch")
    _assert(bool(primary) and primary.get("ready") is False, "primary ChatGPT should be marked unavailable")
    return CheckResult(
        check_id="R-01",
        title="模型路由守卫：首选 ChatGPT 不可用时自动切到 DeepSeek",
        status="PASS",
        detail=str(body.get("message") or "").strip() or "route guard switched to failover",
        evidence={
            "runtime": runtime,
            "primary": primary,
            "failover": body.get("failover"),
        },
    )


def check_intent_parse(api: NetdiagApi) -> CheckResult:
    body = api.post(
        "/api/netdiag/intent/parse",
        payload={
            "text": "设备 192。168。0。88 端口 down，时间昨天到今天，帮我定位原因",
            "timezone": "Asia/Singapore",
            "use_llm": True,
            "llm_route": _default_route(),
        },
        timeout=45,
    )
    parsed = body.get("parsed") if isinstance(body.get("parsed"), dict) else {}
    _assert(bool(body.get("ok", False)), "intent parse returned ok=false")
    _assert(str(parsed.get("device_ip") or "") == "192.168.0.88", "intent parse failed to normalize fullwidth IP")
    _assert(str(parsed.get("fault_start") or "").strip(), "intent parse did not produce fault_start")
    _assert(str(parsed.get("fault_end") or "").strip(), "intent parse did not produce fault_end")
    return CheckResult(
        check_id="R-02",
        title="自然语言解析：全角 IP + 模糊时间窗可识别",
        status="PASS",
        detail="intent parse returned device_ip/fault window",
        evidence={
            "parsed": parsed,
            "source": body.get("source"),
            "llm": body.get("llm"),
            "missing": body.get("missing"),
        },
    )


def run_real_iteration(
    api: NetdiagApi,
    *,
    device_ip: str,
    username: str,
    password: str,
    fault_start: str,
    fault_end: str,
    iteration: int,
) -> CheckResult:
    question = (
        f"设备 {device_ip} 在 2026-03-10 20:30 后出现 Ethernet1/0/6 告警，"
        "请只用 display/show/dis 命令定位根因，并判断是否为 shutdown/admin-down 操作。"
    )
    session_id, create_cost = _create_real_session(
        api,
        device_ip=device_ip,
        username=username,
        password=password,
        question=question,
        fault_start=fault_start,
        fault_end=fault_end,
    )

    baseline, baseline_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/baseline_collect",
        timeout_sec=220,
        max_wait_sec=360,
    )
    _assert(bool(baseline.get("ok", False)), f"baseline failed: {baseline}")
    baseline_again, baseline_again_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/baseline_collect",
        timeout_sec=60,
        max_wait_sec=120,
    )
    _assert(bool(baseline_again.get("baseline_reused", False)), "second baseline call did not reuse existing baseline")

    plan, plan_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/plan",
        payload={
            "max_commands": 6,
            "ai_timeout_sec": 45,
            "ai_retries": 1,
            "llm_route": _default_route(),
        },
        timeout_sec=120,
        max_wait_sec=180,
    )
    _assert(bool(plan.get("ok", False)), f"plan failed: {plan}")
    round_row = _safe_get_round(plan)
    round_no = int(round_row.get("round_no") or 1)
    planned_commands = _round_commands(round_row)
    unique_planned_commands = sorted(set(planned_commands))
    _assert(len(planned_commands) == len(unique_planned_commands), "planned commands contain duplicates")
    low_planned = [x.lower() for x in planned_commands]
    _assert("display clock" not in low_planned, "planned commands should not rerun baseline clock check")
    _assert("display version" not in low_planned, "planned commands should not rerun baseline version check")
    _assert("display cpu-usage" not in low_planned, "planned commands should not rerun baseline cpu check")

    approve, approve_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/approve",
        payload={"approved": True},
        timeout_sec=60,
        max_wait_sec=120,
    )
    _assert(bool(approve.get("ok", False)), f"approve failed: {approve}")

    execute, execute_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/execute",
        timeout_sec=220,
        max_wait_sec=360,
    )
    _assert(bool(execute.get("ok", False)), f"execute failed: {execute}")
    exec_summary = execute.get("execution_summary") if isinstance(execute.get("execution_summary"), dict) else {}

    outputs = api.get(f"/api/netdiag/sessions/{session_id}/outputs?tail_chars=3000&max_items=200", timeout=60)
    items = outputs.get("items") if isinstance(outputs.get("items"), list) else []
    round_items = [x for x in items if isinstance(x, dict) and int(x.get("round_no") or 0) == round_no]
    round_cmds = [str(x.get("command") or "").strip() for x in round_items if str(x.get("command") or "").strip()]
    _assert(len(round_cmds) == len(set(round_cmds)), "session output history contains duplicate command rows for same round")
    _assert(len(round_cmds) >= 1, "session output history is empty after execute")

    analyze, analyze_cost = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{session_id}/rounds/{round_no}/analyze",
        payload={
            "ai_timeout_sec": 45,
            "ai_retries": 1,
            "llm_route": _default_route(),
        },
        timeout_sec=160,
        max_wait_sec=240,
    )
    _assert(bool(analyze.get("ok", False)), f"analyze failed: {analyze}")
    analyzed_round = _safe_get_round(analyze)
    analysis_text = str(analyzed_round.get("analysis_result") or "")
    stop_decision = analyzed_round.get("stop_decision") if isinstance(analyzed_round.get("stop_decision"), dict) else {}
    _assert(analysis_text.strip() != "", "analysis_result is empty")
    _assert(_analysis_has_shutdown_text(analysis_text), "analysis_result does not contain shutdown/admin-down conclusion")
    _assert(bool(stop_decision.get("recommend_conclude", False)), "stop_decision did not recommend conclude")
    _assert(float(analyze_cost) <= 120.0, f"analyze exceeded 120s budget: {analyze_cost}s")

    next_action = api.get(f"/api/netdiag/sessions/{session_id}/next_action", timeout=30).get("next", {})
    _assert(str(next_action.get("action") or "") == "conclude", f"next_action should be conclude after direct evidence, got {next_action}")

    conclude_started = time.monotonic()
    conclude = api.post(f"/api/netdiag/sessions/{session_id}/conclude", payload={}, timeout=60)
    conclude_cost = round(time.monotonic() - conclude_started, 3)
    _assert(bool(conclude.get("ok", False)), f"conclude failed: {conclude}")
    session_row = conclude.get("session") if isinstance(conclude.get("session"), dict) else {}
    _assert(str(session_row.get("status") or "") == "concluded", "session status did not become concluded")
    _assert(str(((session_row.get("next_action") or {}).get("action")) or "") == "none", "concluded session next_action should be none")

    evidence = {
        "session_id": session_id,
        "round_no": round_no,
        "durations_sec": {
            "create": create_cost,
            "baseline": baseline_cost,
            "baseline_reuse": baseline_again_cost,
            "plan": plan_cost,
            "approve": approve_cost,
            "execute": execute_cost,
            "analyze": analyze_cost,
            "conclude": conclude_cost,
            "total": round(
                create_cost
                + baseline_cost
                + baseline_again_cost
                + plan_cost
                + approve_cost
                + execute_cost
                + analyze_cost
                + conclude_cost,
                3,
            ),
        },
        "planned_commands": planned_commands,
        "execution_summary": exec_summary,
        "output_commands": round_cmds,
        "stop_decision": stop_decision,
        "next_action_after_analyze": next_action,
        "analysis_excerpt": analysis_text[:1800],
    }
    return CheckResult(
        check_id=f"R-03.{iteration}",
        title=f"真实设备闭环诊断 #{iteration}",
        status="PASS",
        detail="baseline one-time, plan/execute/analyze/conclude all passed on 192.168.0.88",
        evidence=evidence,
    )


def check_stop_resume_and_history(
    api: NetdiagApi,
    *,
    device_ip: str,
    username: str,
    password: str,
    fault_start: str,
    fault_end: str,
) -> CheckResult:
    sid1, _ = _create_real_session(
        api,
        device_ip=device_ip,
        username=username,
        password=password,
        question=f"停止/恢复测试：{device_ip} 端口 down",
        fault_start=fault_start,
        fault_end=fault_end,
    )
    baseline, _ = _run_step_with_busy_retry(
        api,
        path=f"/api/netdiag/sessions/{sid1}/baseline_collect",
        timeout_sec=220,
        max_wait_sec=360,
    )
    _assert(bool(baseline.get("ok", False)), f"baseline failed before stop test: {baseline}")

    stopped = api.post(f"/api/netdiag/sessions/{sid1}/stop", payload={"reason": "regression stop test"}, timeout=30)
    _assert(bool(stopped.get("stopped", False)), "stop endpoint did not acknowledge stopped=true")
    stopped_session = stopped.get("session") if isinstance(stopped.get("session"), dict) else {}
    _assert(str(stopped_session.get("status") or "") == "aborted", "session status after stop should be aborted")

    status_code, blocked = api.post_allow_error(
        f"/api/netdiag/sessions/{sid1}/rounds/plan",
        payload={"max_commands": 4, "llm_route": _default_route()},
        timeout=60,
    )
    _assert(int(status_code) == 409, f"plan should be blocked while emergency stop is active, got {status_code}")
    _assert("paused by emergency stop" in json.dumps(blocked, ensure_ascii=False), "blocked plan response missing emergency stop explanation")

    resumed = api.post(f"/api/netdiag/sessions/{sid1}/resume", payload={}, timeout=30)
    _assert(bool(resumed.get("resumed", False)), "resume endpoint did not acknowledge resumed=true")
    resumed_session = resumed.get("session") if isinstance(resumed.get("session"), dict) else {}
    _assert(str(resumed_session.get("status") or "") == "ready_for_next_probe", f"resumed session should return to ready_for_next_probe, got {resumed_session.get('status')}")
    _assert(str(((resumed_session.get("next_action") or {}).get("action")) or "") == "plan", "resumed baseline-complete session should continue from plan")

    sid2, _ = _create_real_session(
        api,
        device_ip=device_ip,
        username=username,
        password=password,
        question=f"新会话历史保留测试：{device_ip} 端口 down",
        fault_start=fault_start,
        fault_end=fault_end,
    )
    sessions = api.get("/api/netdiag/sessions", timeout=30)
    items = sessions.get("items") if isinstance(sessions.get("items"), list) else []
    ids = [str(x.get("session_id") or "").strip() for x in items if isinstance(x, dict)]
    _assert(sid1 in ids, "stopped/resumed session not found in session history list")
    _assert(sid2 in ids, "new session not found in session history list")
    _assert(ids and ids[0] == sid2, "newest session should appear first in history list")

    return CheckResult(
        check_id="R-04",
        title="急刹/恢复/历史会话",
        status="PASS",
        detail="stop blocks workflow, resume restores next step, new session remains in history",
        evidence={
            "stopped_session_id": sid1,
            "new_session_id": sid2,
            "blocked_status_code": status_code,
            "blocked_response": blocked,
            "resumed_status": resumed_session.get("status"),
            "resumed_next_action": resumed_session.get("next_action"),
            "history_top_ids": ids[:10],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="NetDiag issue regression checklist runner")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--device-ip", default="192.168.0.88")
    parser.add_argument("--username", default="zhangwei")
    parser.add_argument("--password", required=True)
    parser.add_argument("--fault-start", default="2026-03-10T20:30:00")
    parser.add_argument("--fault-end", default="2026-03-10T23:59:59")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    root = (
        Path(args.output_dir).expanduser().resolve()
        if str(args.output_dir or "").strip()
        else (Path(__file__).resolve().parents[1] / "output" / "regression" / _utc_now().strftime("%Y%m%dT%H%M%SZ"))
    )
    root.mkdir(parents=True, exist_ok=True)
    out_json = root / "issue_regression_report.json"

    api = NetdiagApi(args.base_url)
    api.post("/api/netdiag/connection/config", payload={"config": {"smc_jump_mode": "direct"}}, timeout=30)

    results: list[CheckResult] = []
    results.append(check_llm_route_guard(api))
    results.append(check_intent_parse(api))
    for idx in range(1, max(1, int(args.iterations)) + 1):
        results.append(
            run_real_iteration(
                api,
                device_ip=args.device_ip,
                username=args.username,
                password=args.password,
                fault_start=args.fault_start,
                fault_end=args.fault_end,
                iteration=idx,
            )
        )
    results.append(
        check_stop_resume_and_history(
            api,
            device_ip=args.device_ip,
            username=args.username,
            password=args.password,
            fault_start=args.fault_start,
            fault_end=args.fault_end,
        )
    )

    passed = sum(1 for x in results if x.status == "PASS")
    body = {
        "ok": passed == len(results),
        "generated_at_utc": _ts(),
        "base_url": args.base_url,
        "device_ip": args.device_ip,
        "fault_window": {"start": args.fault_start, "end": args.fault_end, "timezone": "Asia/Singapore"},
        "summary": {
            "total_checks": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": [
            {
                "check_id": x.check_id,
                "title": x.title,
                "status": x.status,
                "detail": x.detail,
                "evidence": x.evidence,
            }
            for x in results
        ],
    }
    out_json.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
