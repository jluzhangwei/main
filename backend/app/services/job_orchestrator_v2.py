from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from app.models.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ApiKeyRecord,
    CommandExecution,
    CommandStatus,
    Evidence,
    AuditLog,
    AutomationLevel,
    CausalEdge,
    CommandProfile,
    DeviceProtocol,
    DeviceTarget,
    IncidentCluster,
    IncidentEvent,
    Job,
    JobActionDecisionResponse,
    JobActionGroup,
    JobActionGroupStatus,
    JobCommandResult,
    JobCommandStatus,
    JobCreateRequest,
    JobDevice,
    JobEvidence,
    JobEvent,
    JobMode,
    JobPhase,
    JobReportResponse,
    JobResponse,
    JobStatus,
    JobTimelineResponse,
    JobTopologyEdge,
    RCAWeights,
    RCAResult,
    RiskLevel,
    OperationMode,
    Session,
    TopologyMode,
    make_id,
    now_utc,
)
from app.services.adapters import build_adapter
from app.services.adapter_runtime import close_connected_adapter, ensure_connected_adapter
from app.services.command_group_runtime import execute_command_group
from app.services.command_runtime import (
    apply_adapter_command_meta,
    apply_device_profile_to_job_device,
    parse_command_runtime,
)
from app.services.deepseek_diagnoser import DeepSeekDiagnoser
from app.services.llm_planner_bridge import LLMPlannerBridge
from app.services.risk_engine import RiskEngine
from app.services.single_command_runtime import execute_single_command
from app.services.sop_archive import SOPArchive
from app.services.store import InMemoryStore


class JobV2Orchestrator:
    """Multi-device asynchronous job orchestrator for /v2 APIs."""

    def __init__(self, store: InMemoryStore, *, allow_simulation: bool = True):
        self.store = store
        self.allow_simulation = allow_simulation
        self.risk_engine = RiskEngine()
        self.deepseek_diagnoser = DeepSeekDiagnoser()
        self.llm_planner_bridge = LLMPlannerBridge()
        self.sop_archive = SOPArchive()

        self._jobs: dict[str, Job] = {}
        self._events: dict[str, list[JobEvent]] = defaultdict(list)
        self._tasks: dict[str, asyncio.Task] = {}
        self._adapters: dict[str, dict[str, Any]] = defaultdict(dict)
        self._keys: dict[str, ApiKeyRecord] = {}
        self._audit_logs: list[AuditLog] = []
        self._command_profiles: dict[str, CommandProfile] = {}
        self._idempotency_index: dict[str, str] = {}
        self._webhook_tasks: set[asyncio.Task] = set()

        self._state_lock = asyncio.Lock()
        self._state_path = self._resolve_state_path()
        self._load_state()

    def _resolve_state_path(self) -> Path:
        env = (os.getenv("NETOPS_V2_STATE_PATH") or "").strip()
        if env:
            return Path(env).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "v2_state.json"
        return Path("/tmp/netops_ai_v2_state.json")

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        raw_keys = payload.get("api_keys")
        if isinstance(raw_keys, list):
            for row in raw_keys:
                try:
                    key = ApiKeyRecord.model_validate(row)
                except Exception:
                    continue
                self._keys[key.id] = key

        raw_logs = payload.get("audit_logs")
        if isinstance(raw_logs, list):
            for row in raw_logs:
                try:
                    self._audit_logs.append(AuditLog.model_validate(row))
                except Exception:
                    continue

        raw_profiles = payload.get("command_profiles")
        if isinstance(raw_profiles, list):
            for row in raw_profiles:
                try:
                    item = CommandProfile.model_validate(row)
                except Exception:
                    continue
                profile_key = self._profile_key(item.version_signature, item.command_key)
                self._command_profiles[profile_key] = item

        raw_jobs = payload.get("jobs")
        if isinstance(raw_jobs, list):
            for row in raw_jobs:
                try:
                    job = Job.model_validate(row)
                except Exception:
                    continue
                if job.status in {JobStatus.queued, JobStatus.running, JobStatus.executing, JobStatus.waiting_approval}:
                    job.status = JobStatus.failed
                    job.phase = JobPhase.conclude
                    job.error = (job.error or "") + " | job restored after restart without runtime credentials"
                    job.completed_at = now_utc()
                self._jobs[job.id] = job

        raw_events = payload.get("events")
        if isinstance(raw_events, dict):
            for job_id, rows in raw_events.items():
                if not isinstance(rows, list):
                    continue
                parsed: list[JobEvent] = []
                for row in rows:
                    try:
                        parsed.append(JobEvent.model_validate(row))
                    except Exception:
                        continue
                if parsed:
                    self._events[job_id] = parsed

        raw_idempotency = payload.get("idempotency_index")
        if isinstance(raw_idempotency, dict):
            for key, job_id in raw_idempotency.items():
                k = str(key or "").strip()
                v = str(job_id or "").strip()
                if not k or not v:
                    continue
                self._idempotency_index[k] = v

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "jobs": [self._job_for_persistence(item).model_dump(mode="json") for item in self._jobs.values()],
                "events": {jid: [e.model_dump(mode="json") for e in rows] for jid, rows in self._events.items()},
                "idempotency_index": dict(self._idempotency_index),
                "api_keys": [item.model_dump(mode="json") for item in self._keys.values()],
                "audit_logs": [item.model_dump(mode="json") for item in self._audit_logs[-5000:]],
                "command_profiles": [item.model_dump(mode="json") for item in self._command_profiles.values()],
            }
            self._state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(self._state_path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _job_for_persistence(self, job: Job) -> Job:
        safe = job.model_copy(deep=True)
        for device in safe.devices:
            device.username = None
            device.password = None
            device.jump_username = None
            device.jump_password = None
            device.api_token = None
        return safe

    def _public_job(self, job: Job) -> Job:
        safe = job.model_copy(deep=True)
        for device in safe.devices:
            device.username = None
            device.password = None
            device.jump_username = None
            device.jump_password = None
            device.api_token = None
        return safe

    def _job_summary(self, job: Job) -> JobResponse:
        pending = len([item for item in job.action_groups if item.status == JobActionGroupStatus.pending_approval])
        return JobResponse(
            id=job.id,
            name=job.name,
            problem=job.problem,
            mode=job.mode,
            status=job.status,
            phase=job.phase,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            updated_at=job.updated_at,
            device_count=len(job.devices),
            command_count=len(job.command_results),
            pending_action_groups=pending,
            root_device_id=job.rca_result.root_device_id if job.rca_result else None,
        )

    def _append_event(self, job: Job, event_type: str, payload: dict[str, Any]) -> JobEvent:
        rows = self._events[job.id]
        event = JobEvent(job_id=job.id, seq_no=len(rows) + 1, event_type=event_type, payload=payload)
        rows.append(event)
        self._dispatch_webhook(job, event)
        return event

    def _sanitize_trace_text(self, text: str) -> str:
        if not text:
            return text
        sanitized = text
        patterns = [
            (
                re.compile(
                    r"(?i)\b(username|user|account|login|password|passwd|pwd|token|api[_-]?key|secret)\b\s*[:=：]?\s*([^\s,;，。]+)"
                ),
                r"\1 [REDACTED]",
            ),
            (
                re.compile(r"(账号|用户名|密码|口令|令牌|密钥)\s*[:：]?\s*([^\s,;，。]+)", re.IGNORECASE),
                r"\1 [REDACTED]",
            ),
        ]
        for pattern, replacement in patterns:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def _clip_trace_text(self, value: Any, limit: int = 200000) -> str:
        text = str(value or "")
        if limit <= 0 or len(text) <= limit:
            return text
        return f"{text[:limit]}...(truncated,{len(text)} chars)"

    def _compact_trace_payload(
        self,
        payload: Any,
        *,
        depth: int = 0,
        max_depth: int = 10,
        max_items: int = 200,
        text_limit: int = 200000,
    ) -> Any:
        if depth >= max_depth:
            return "<max-depth>"
        if isinstance(payload, str):
            return self._clip_trace_text(self._sanitize_trace_text(payload), text_limit)
        if isinstance(payload, (int, float, bool)) or payload is None:
            return payload
        if isinstance(payload, dict):
            out: dict[str, Any] = {}
            for idx, (key, value) in enumerate(payload.items()):
                if idx >= max_items:
                    out["__truncated_items__"] = len(payload) - max_items
                    break
                out[str(key)] = self._compact_trace_payload(
                    value,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    text_limit=text_limit,
                )
            return out
        if isinstance(payload, list):
            out_list = []
            for idx, item in enumerate(payload):
                if idx >= max_items:
                    out_list.append({"__truncated_items__": len(payload) - max_items})
                    break
                out_list.append(
                    self._compact_trace_payload(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_items=max_items,
                        text_limit=text_limit,
                    )
                )
            return out_list
        return self._clip_trace_text(self._sanitize_trace_text(str(payload)), text_limit)

    def _job_command_trace_record(self, command: JobCommandResult, *, include_output: bool) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": command.id,
            "step_no": command.step_no,
            "title": command.title,
            "command": command.command,
            "original_command": command.command,
            "effective_command": command.effective_command or command.command,
            "risk_level": command.risk_level.value,
            "status": command.status.value,
            "constraint_source": command.constraint_source or "",
            "constraint_reason": command.constraint_reason or "",
            "capability_state": command.capability_state or "",
            "capability_reason": command.capability_reason or "",
            "started_at": command.started_at.isoformat() if command.started_at else None,
            "completed_at": command.completed_at.isoformat() if command.completed_at else None,
            "duration_ms": command.duration_ms,
            "error": command.error or "",
        }
        if include_output:
            record["output"] = command.output or ""
        return record

    def _job_device_trace_record(self, device: JobDevice) -> dict[str, Any]:
        return {
            "id": device.id,
            "host": device.host,
            "name": device.name or "",
            "vendor": device.vendor or "",
            "platform": device.platform or "",
            "software_version": device.software_version or "",
            "version_signature": device.version_signature or "",
            "status": device.status,
        }

    async def _trace_job_adapter_state(
        self,
        job_id: str,
        device_id: str,
        *,
        mode: str,
        status: str,
        error: str | None = None,
        closed: bool = False,
    ) -> None:
        async with self._state_lock:
            current_job = self._jobs.get(job_id)
            current_device = self._find_device(current_job, device_id) if current_job else None
            if not current_job or not current_device:
                return
            if closed:
                title = f"[{current_device.host}] 关闭设备连接"
                detail = "mode=close"
                payload = {"device": self._job_device_trace_record(current_device), "mode": "close"}
            else:
                action_text = "建立设备连接" if mode == "create" else "复用设备连接"
                title = f"[{current_device.host}] {action_text}"
                if status == "failed":
                    title = f"[{current_device.host}] {action_text}失败"
                detail = f"mode={mode}" if not error else str(error)[:280]
                payload = {"device": self._job_device_trace_record(current_device), "mode": mode}
                if error:
                    payload["error"] = error
            self._append_trace_event(
                current_job,
                "session_adapter",
                title,
                status=status,
                detail=detail,
                detail_payload=payload,
                device=current_device,
            )
            self._save_state()

    def _format_phase_label(self, phase: str) -> str:
        labels = {
            "collect": "采集",
            "correlate": "关联",
            "plan": "规划",
            "approve": "审批",
            "execute": "执行",
            "analyze": "分析",
            "conclude": "总结",
        }
        return labels.get(phase, phase or "-")

    def _build_trace_payload(
        self,
        step_type: str,
        title: str,
        *,
        status: str = "succeeded",
        detail: str = "",
        detail_payload: dict[str, Any] | None = None,
        command: JobCommandResult | None = None,
        device: JobDevice | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trace_step_type": step_type,
            "title": title,
            "status": status,
            "detail": self._clip_trace_text(self._sanitize_trace_text(detail), 12000),
            "detail_payload": self._compact_trace_payload(detail_payload or {}, max_depth=10, max_items=500, text_limit=200000),
        }
        if command is not None:
            payload["command_id"] = command.id
            payload["step_no"] = command.step_no
        if device is not None:
            payload["device_id"] = device.id
            payload["device_host"] = device.host
        if started_at is not None:
            payload["started_at"] = started_at.isoformat()
        if completed_at is not None:
            payload["completed_at"] = completed_at.isoformat()
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        return payload

    def _append_trace_event(
        self,
        job: Job,
        step_type: str,
        title: str,
        *,
        status: str = "succeeded",
        detail: str = "",
        detail_payload: dict[str, Any] | None = None,
        command: JobCommandResult | None = None,
        device: JobDevice | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> JobEvent:
        payload = self._build_trace_payload(
            step_type,
            title,
            status=status,
            detail=detail,
            detail_payload=detail_payload,
            command=command,
            device=device,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        return self._append_event(job, step_type, payload)

    def _append_event_with_trace(
        self,
        job: Job,
        event_type: str,
        payload: dict[str, Any],
        *,
        step_type: str,
        title: str,
        status: str = "succeeded",
        detail: str = "",
        detail_payload: dict[str, Any] | None = None,
        command: JobCommandResult | None = None,
        device: JobDevice | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> JobEvent:
        merged = dict(payload)
        merged.update(
            self._build_trace_payload(
                step_type,
                title,
                status=status,
                detail=detail,
                detail_payload=detail_payload,
                command=command,
                device=device,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )
        )
        return self._append_event(job, event_type, merged)

    def _build_llm_request_payload(self, debug: dict[str, Any], *, device: JobDevice | None = None) -> dict[str, Any]:
        source = debug if isinstance(debug, dict) else {}
        payload: dict[str, Any] = {}
        for key in ("iteration", "max_iterations", "with_history", "system_prompt", "request_messages", "request_payload"):
            if source.get(key) is not None:
                payload[key] = source.get(key)
        if device is not None:
            payload["device"] = self._job_device_trace_record(device)
        return self._compact_trace_payload(payload, max_depth=10, max_items=500, text_limit=200000)

    def _build_llm_response_payload(
        self,
        debug: dict[str, Any],
        parsed_plan: dict[str, Any] | None,
        *,
        device: JobDevice | None = None,
    ) -> dict[str, Any]:
        source = debug if isinstance(debug, dict) else {}
        llm_payload: dict[str, Any] = {}
        for key in ("raw_response", "parsed_response", "error"):
            if source.get(key) is not None:
                llm_payload[key] = source.get(key)
        if parsed_plan is not None:
            llm_payload["parsed_response"] = parsed_plan
        payload: dict[str, Any] = {
            "llm": llm_payload,
            "ai_response_parsed": parsed_plan,
        }
        if device is not None:
            payload["device"] = self._job_device_trace_record(device)
        return self._compact_trace_payload(payload, max_depth=10, max_items=500, text_limit=200000)

    def _build_llm_plan_payload(
        self,
        *,
        job: Job,
        device: JobDevice,
        user_problem: str,
        debug: dict[str, Any],
        parsed_plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._compact_trace_payload(
            {
                "device": self._job_device_trace_record(device),
                "job": {
                    "id": job.id,
                    "problem": job.problem,
                    "mode": job.mode.value,
                    "phase": job.phase.value,
                },
                "user_problem": user_problem,
                "to_ai": debug,
                "ai_response_parsed": parsed_plan,
            },
            max_depth=10,
            max_items=500,
            text_limit=200000,
        )

    def _normalize_idempotency_key(self, key: str | None, actor_key_id: str | None) -> str | None:
        raw = str(key or "").strip()
        if not raw:
            return None
        actor = str(actor_key_id or "anonymous").strip() or "anonymous"
        return f"{actor}::{raw}"

    def _should_emit_webhook(self, job: Job, event_type: str) -> bool:
        url = str(job.webhook_url or "").strip()
        if not url:
            return False
        selected = [str(item or "").strip() for item in job.webhook_events if str(item or "").strip()]
        if not selected:
            return True
        return event_type in selected or "*" in selected

    def _dispatch_webhook(self, job: Job, event: JobEvent) -> None:
        if not self._should_emit_webhook(job, event.event_type):
            return
        task = asyncio.create_task(self._emit_webhook_event(job, event), name=f"v2-webhook-{job.id}-{event.seq_no}")
        self._webhook_tasks.add(task)

        def _cleanup(done: asyncio.Task) -> None:
            self._webhook_tasks.discard(done)

        task.add_done_callback(_cleanup)

    async def _emit_webhook_event(self, job: Job, event: JobEvent) -> None:
        url = str(job.webhook_url or "").strip()
        if not url:
            return
        payload = {
            "job_id": job.id,
            "job_status": job.status.value,
            "job_phase": job.phase.value,
            "event": event.model_dump(mode="json"),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json=payload)
        except Exception:
            return

    def _terminal(self, status: JobStatus) -> bool:
        return status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}

    def _find_device(self, job: Job, device_id: str) -> Optional[JobDevice]:
        for item in job.devices:
            if item.id == device_id:
                return item
        return None

    async def create_job(
        self,
        req: JobCreateRequest,
        *,
        idempotency_key: str | None = None,
        actor_key_id: str | None = None,
    ) -> JobResponse:
        if not req.devices:
            raise ValueError("at least one device is required")

        normalized_idempotency = self._normalize_idempotency_key(idempotency_key, actor_key_id)
        if normalized_idempotency:
            async with self._state_lock:
                existing_id = self._idempotency_index.get(normalized_idempotency)
                existing_job = self._jobs.get(existing_id or "")
                if existing_job is not None:
                    return self._job_summary(existing_job)

        devices = [
            JobDevice(
                host=item.host,
                name=item.name,
                port=item.port,
                protocol=item.protocol,
                vendor=item.vendor,
                username=item.username,
                password=item.password,
                jump_host=item.jump_host,
                jump_port=item.jump_port,
                jump_username=item.jump_username,
                jump_password=item.jump_password,
                api_token=item.api_token,
                device_type=item.device_type,
            )
            for item in req.devices
        ]

        job = Job(
            name=req.name,
            problem=req.problem,
            mode=req.mode,
            status=JobStatus.queued,
            phase=JobPhase.collect,
            topology_mode=req.topology_mode,
            max_gap_seconds=max(30, int(req.max_gap_seconds or 300)),
            max_device_concurrency=max(1, int(req.max_device_concurrency or 20)),
            execution_policy=req.execution_policy,
            rca_weights=req.rca_weights,
            idempotency_key=normalized_idempotency,
            requester_key_id=actor_key_id,
            webhook_url=(req.webhook_url or "").strip() or None,
            webhook_events=[str(item).strip() for item in req.webhook_events if str(item).strip()],
            window_start=req.window_start,
            window_end=req.window_end,
            devices=devices,
            external_topology_edges=list(req.topology_edges),
        )

        async with self._state_lock:
            self._jobs[job.id] = job
            if normalized_idempotency:
                self._idempotency_index[normalized_idempotency] = job.id
            self._append_trace_event(
                job,
                "user_input",
                "接收用户请求",
                status="succeeded",
                detail=req.problem[:280],
                detail_payload={
                    "user_input": req.problem,
                    "device_count": len(job.devices),
                    "mode": job.mode.value,
                    "topology_mode": job.topology_mode.value,
                },
            )
            self._append_event_with_trace(
                job,
                "job_created",
                {
                    "job_id": job.id,
                    "device_count": len(job.devices),
                    "mode": job.mode.value,
                    "topology_mode": job.topology_mode.value,
                },
                step_type="session_control",
                title="创建多设备任务",
                status="succeeded",
                detail=f"devices={len(job.devices)}; mode={job.mode.value}; topology={job.topology_mode.value}",
                detail_payload={
                    "job_id": job.id,
                    "device_hosts": [item.host for item in job.devices],
                    "mode": job.mode.value,
                    "topology_mode": job.topology_mode.value,
                },
            )
            self._save_state()

        task = asyncio.create_task(self._run_job(job.id), name=f"v2-job-{job.id}")
        self._tasks[job.id] = task
        return self._job_summary(job)

    async def list_jobs(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        status: JobStatus | None = None,
        mode: JobMode | None = None,
    ) -> tuple[list[JobResponse], int]:
        async with self._state_lock:
            rows = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            if status is not None:
                rows = [item for item in rows if item.status == status]
            if mode is not None:
                rows = [item for item in rows if item.mode == mode]
            total = len(rows)
            start = max(0, int(offset))
            end = start + max(1, min(500, int(limit)))
            sliced = rows[start:end]
            return [self._job_summary(item) for item in sliced], total

    async def get_job(self, job_id: str) -> JobResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return self._job_summary(job)

    async def get_timeline(self, job_id: str) -> JobTimelineResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return JobTimelineResponse(job=self._public_job(job), events=list(self._events.get(job_id, [])))

    async def update_job_topology(self, job_id: str, edges: list[JobTopologyEdge], *, replace: bool = False) -> JobResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if replace:
                job.external_topology_edges = []
            existing = {
                (item.source.strip().lower(), item.target.strip().lower(), item.kind.strip().lower())
                for item in job.external_topology_edges
            }
            appended = 0
            for edge in edges:
                key = (edge.source.strip().lower(), edge.target.strip().lower(), edge.kind.strip().lower())
                if key in existing:
                    continue
                job.external_topology_edges.append(edge)
                existing.add(key)
                appended += 1
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "topology_updated",
                {
                    "replace": replace,
                    "append_count": appended,
                    "total_edges": len(job.external_topology_edges),
                },
                step_type="session_control",
                title="更新任务拓扑输入",
                status="succeeded",
                detail=f"replace={replace}; append_count={appended}; total_edges={len(job.external_topology_edges)}",
                detail_payload={
                    "replace": replace,
                    "append_count": appended,
                    "total_edges": len(job.external_topology_edges),
                },
            )
            self._save_state()
            return self._job_summary(job)

    async def update_job_rca_weights(self, job_id: str, weights: RCAWeights) -> JobResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            job.rca_weights = weights
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "rca_weights_updated",
                {
                    "weights": weights.model_dump(mode="json"),
                },
                step_type="session_control",
                title="更新 RCA 权重",
                status="succeeded",
                detail="rca_weights_updated",
                detail_payload={"weights": weights.model_dump(mode="json")},
            )
            self._save_state()
            return self._job_summary(job)

    async def cancel_job(self, job_id: str, *, reason: str | None = None, actor_name: str | None = None) -> JobResponse:
        close_needed = False
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if not self._terminal(job.status):
                job.status = JobStatus.cancelled
                job.phase = JobPhase.conclude
                job.error = (reason or "").strip() or None
                job.completed_at = now_utc()
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "job_cancelled",
                    {
                        "reason": reason,
                        "actor": actor_name,
                    },
                    step_type="session_control",
                    title="多设备任务已取消",
                    status="stopped",
                    detail=str(reason or actor_name or "cancelled")[:280],
                    detail_payload={
                        "reason": reason,
                        "actor": actor_name,
                    },
                )
                self._save_state()
                close_needed = True
            summary = self._job_summary(job)

        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

        if close_needed:
            await self._close_job_adapters(job_id)
        return summary

    async def list_events_since(self, job_id: str, from_seq: int = 0) -> tuple[list[JobEvent], JobStatus]:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            events = [item for item in self._events.get(job_id, []) if item.seq_no > from_seq]
            return events, job.status

    async def approve_action_group(
        self,
        job_id: str,
        action_group_id: str,
        *,
        actor_key_id: str,
        actor_name: str,
        reason: str | None = None,
    ) -> JobActionDecisionResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            action = next((item for item in job.action_groups if item.id == action_group_id), None)
            if action is None:
                raise KeyError(action_group_id)
            if action.status != JobActionGroupStatus.pending_approval:
                return JobActionDecisionResponse(
                    job_id=job_id,
                    action_group_id=action_group_id,
                    status=action.status,
                    message="Action group is not pending approval",
                )
            action.status = JobActionGroupStatus.approved
            action.approved_by = actor_name
            action.approve_reason = (reason or "").strip() or None
            action.updated_at = now_utc()
            job.updated_at = now_utc()
            device = self._find_device(job, action.device_id)
            self._append_event_with_trace(
                job,
                "action_group_approved",
                {
                    "action_group_id": action.id,
                    "device_id": action.device_id,
                    "approved_by": actor_name,
                },
                step_type="session_control",
                title=f"[{device.host if device else action.device_id}] 审批通过命令组",
                status="succeeded",
                detail=f"approved_by={actor_name}",
                detail_payload={
                    "action_group_id": action.id,
                    "approved_by": actor_name,
                    "reason": action.approve_reason or "",
                },
                device=device,
            )
            if job.status == JobStatus.waiting_approval:
                job.status = JobStatus.executing
                job.phase = JobPhase.execute
            self._save_state()

        self._audit_logs.append(
            AuditLog(
                actor_key_id=actor_key_id,
                actor_name=actor_name,
                action="job.action_group.approve",
                resource=f"job:{job_id}/action:{action_group_id}",
                status="ok",
                detail=reason,
            )
        )

        task = self._tasks.get(job_id)
        if task is None or task.done():
            self._tasks[job_id] = asyncio.create_task(self._resume_job_execution(job_id), name=f"v2-job-resume-{job_id}")

        return JobActionDecisionResponse(
            job_id=job_id,
            action_group_id=action_group_id,
            status=JobActionGroupStatus.approved,
            message="Action group approved",
        )

    async def bulk_approve_action_groups(
        self,
        job_id: str,
        action_group_ids: list[str],
        *,
        actor_key_id: str,
        actor_name: str,
        reason: str | None = None,
    ) -> list[JobActionDecisionResponse]:
        results: list[JobActionDecisionResponse] = []
        for action_group_id in action_group_ids:
            try:
                results.append(
                    await self.approve_action_group(
                        job_id,
                        action_group_id,
                        actor_key_id=actor_key_id,
                        actor_name=actor_name,
                        reason=reason,
                    )
                )
            except KeyError:
                continue
        return results

    async def reject_action_group(
        self,
        job_id: str,
        action_group_id: str,
        *,
        actor_key_id: str,
        actor_name: str,
        reason: str | None = None,
    ) -> JobActionDecisionResponse:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            action = next((item for item in job.action_groups if item.id == action_group_id), None)
            if action is None:
                raise KeyError(action_group_id)
            if action.status != JobActionGroupStatus.pending_approval:
                return JobActionDecisionResponse(
                    job_id=job_id,
                    action_group_id=action_group_id,
                    status=action.status,
                    message="Action group is not pending approval",
                )
            action.status = JobActionGroupStatus.rejected
            action.reject_reason = (reason or "").strip() or None
            action.updated_at = now_utc()
            job.updated_at = now_utc()
            device = self._find_device(job, action.device_id)
            self._append_event_with_trace(
                job,
                "action_group_rejected",
                {
                    "action_group_id": action.id,
                    "device_id": action.device_id,
                    "rejected_by": actor_name,
                },
                step_type="session_control",
                title=f"[{device.host if device else action.device_id}] 审批拒绝命令组",
                status="failed",
                detail=f"rejected_by={actor_name}",
                detail_payload={
                    "action_group_id": action.id,
                    "rejected_by": actor_name,
                    "reason": action.reject_reason or "",
                },
                device=device,
            )

            if all(item.status != JobActionGroupStatus.pending_approval for item in job.action_groups):
                if all(item.status in {JobActionGroupStatus.rejected, JobActionGroupStatus.succeeded, JobActionGroupStatus.failed} for item in job.action_groups):
                    job.phase = JobPhase.conclude
                    job.status = JobStatus.completed
                    if not job.completed_at:
                        job.completed_at = now_utc()
                    self._append_event_with_trace(
                        job,
                        "job_completed",
                        {"mode": job.mode.value, "message": "all action groups resolved after rejection"},
                        step_type="session_control",
                        title="多设备协同完成",
                        status="succeeded",
                        detail="all action groups resolved after rejection",
                        detail_payload={"mode": job.mode.value, "message": "all action groups resolved after rejection"},
                        completed_at=job.completed_at,
                        duration_ms=0,
                    )
            self._save_state()

        self._audit_logs.append(
            AuditLog(
                actor_key_id=actor_key_id,
                actor_name=actor_name,
                action="job.action_group.reject",
                resource=f"job:{job_id}/action:{action_group_id}",
                status="ok",
                detail=reason,
            )
        )

        return JobActionDecisionResponse(
            job_id=job_id,
            action_group_id=action_group_id,
            status=JobActionGroupStatus.rejected,
            message="Action group rejected",
        )

    async def bulk_reject_action_groups(
        self,
        job_id: str,
        action_group_ids: list[str],
        *,
        actor_key_id: str,
        actor_name: str,
        reason: str | None = None,
    ) -> list[JobActionDecisionResponse]:
        results: list[JobActionDecisionResponse] = []
        for action_group_id in action_group_ids:
            try:
                results.append(
                    await self.reject_action_group(
                        job_id,
                        action_group_id,
                        actor_key_id=actor_key_id,
                        actor_name=actor_name,
                        reason=reason,
                    )
                )
            except KeyError:
                continue
        return results

    async def _run_job(self, job_id: str) -> None:
        try:
            async with self._state_lock:
                job = self._jobs.get(job_id)
                if not job:
                    return
                job.status = JobStatus.running
                job.phase = JobPhase.collect
                job.started_at = now_utc()
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "phase_changed",
                    {"phase": job.phase.value, "status": job.status.value},
                    step_type="session_control",
                    title=f"阶段切换：{self._format_phase_label(job.phase.value)}",
                    status=job.status.value,
                    detail=f"phase={job.phase.value}; status={job.status.value}",
                    detail_payload={"phase": job.phase.value, "status": job.status.value},
                    started_at=job.started_at,
                    completed_at=job.updated_at,
                    duration_ms=0,
                )
                self._save_state()

            await self._collect_phase(job_id)
            await self._correlate_phase(job_id)
            await self._plan_phase(job_id)
            await self._resume_job_execution(job_id)
        except asyncio.CancelledError:
            async with self._state_lock:
                job = self._jobs.get(job_id)
                if job and not self._terminal(job.status):
                    job.status = JobStatus.cancelled
                    job.phase = JobPhase.conclude
                    job.completed_at = now_utc()
                    job.updated_at = now_utc()
                    self._append_event_with_trace(
                        job,
                        "job_cancelled",
                        {"reason": "task_cancelled"},
                        step_type="session_control",
                        title="多设备协同已取消",
                        status="stopped",
                        detail="task_cancelled",
                        detail_payload={"reason": "task_cancelled"},
                        completed_at=job.completed_at,
                        duration_ms=0,
                    )
                    self._save_state()
            await self._close_job_adapters(job_id)
            raise
        except Exception as exc:
            async with self._state_lock:
                job = self._jobs.get(job_id)
                if not job:
                    return
                job.status = JobStatus.failed
                job.phase = JobPhase.conclude
                job.error = str(exc)
                job.updated_at = now_utc()
                job.completed_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "job_failed",
                    {"error": str(exc)[:500]},
                    step_type="session_control",
                    title="多设备协同失败",
                    status="failed",
                    detail=str(exc)[:280],
                    detail_payload={"error": str(exc)},
                    completed_at=job.completed_at,
                    duration_ms=0,
                )
                self._save_state()
            await self._close_job_adapters(job_id)

    async def _collect_phase(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            job.phase = JobPhase.collect
            job.status = JobStatus.running
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "phase_changed",
                {"phase": "collect", "status": job.status.value},
                step_type="session_control",
                title=f"阶段切换：{self._format_phase_label('collect')}",
                status=job.status.value,
                detail=f"phase=collect; status={job.status.value}",
                detail_payload={"phase": "collect", "status": job.status.value},
                completed_at=job.updated_at,
                duration_ms=0,
            )
            devices = list(job.devices)
            concurrency = max(1, int(job.max_device_concurrency or 20))
            self._save_state()

        sem = asyncio.Semaphore(concurrency)

        async def worker(device: JobDevice) -> None:
            async with sem:
                await self._collect_device(job_id, device.id)

        await asyncio.gather(*(worker(item) for item in devices), return_exceptions=False)

    async def _collect_device(self, job_id: str, device_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                return
            device.status = "collecting"
            device.last_error = None
            self._append_event_with_trace(
                job,
                "device_collect_started",
                {"device_id": device.id, "host": device.host},
                step_type="session_control",
                title=f"[{device.host}] 开始设备采集",
                status="running",
                detail="phase=collect",
                detail_payload={"device": self._job_device_trace_record(device), "phase": "collect"},
                device=device,
            )
            self._save_state()

        try:
            await self._get_adapter(job_id, device_id)
            collect_commands = self._baseline_collect_commands()
            for title, command_text in collect_commands:
                normalized_cmd = " ".join(str(command_text or "").strip().lower().split())
                if "version" not in normalized_cmd:
                    async with self._state_lock:
                        current_job = self._jobs[job_id]
                        current_device = self._find_device(current_job, device_id)
                        current_vendor = (current_device.vendor if current_device else "") or ""
                    if self._should_skip_collect_command_by_vendor(current_vendor, normalized_cmd):
                        continue
                step_no = await self._allocate_next_step_no(job_id)
                await self._run_device_command(
                    job_id,
                    device_id,
                    title=title,
                    command_text=command_text,
                    step_no=step_no,
                    action_group_id=None,
                    phase="collect",
                )

            await self._collect_device_with_llm(job_id, device_id)

            async with self._state_lock:
                job = self._jobs[job_id]
                device = self._find_device(job, device_id)
                if device:
                    device.status = "collected"
                    device.last_error = None
                    self._append_event_with_trace(
                        job,
                        "device_collect_completed",
                        {"device_id": device_id},
                        step_type="session_control",
                        title=f"[{device.host}] 设备采集完成",
                        status="succeeded",
                        detail="phase=collect",
                        detail_payload={"device": self._job_device_trace_record(device), "phase": "collect"},
                        device=device,
                    )
                else:
                    self._append_event(job, "device_collect_completed", {"device_id": device_id})
                self._save_state()
        except Exception as exc:
            async with self._state_lock:
                job = self._jobs[job_id]
                device = self._find_device(job, device_id)
                if device:
                    device.status = "failed"
                    device.last_error = str(exc)
                    self._append_event_with_trace(
                        job,
                        "device_collect_failed",
                        {"device_id": device_id, "error": str(exc)[:260]},
                        step_type="session_control",
                        title=f"[{device.host}] 设备采集失败",
                        status="failed",
                        detail=str(exc)[:280],
                        detail_payload={
                            "device": self._job_device_trace_record(device),
                            "phase": "collect",
                            "error": str(exc),
                        },
                        device=device,
                    )
                else:
                    self._append_event(job, "device_collect_failed", {"device_id": device_id, "error": str(exc)[:260]})
                self._save_state()

    def _baseline_collect_commands(self) -> list[tuple[str, str]]:
        return [
            ("版本探测", "show version"),
            ("版本探测兼容", "display version"),
            ("接口摘要", "show ip interface brief"),
            ("接口摘要兼容", "display interface brief"),
            ("路由摘要", "show ip route"),
            ("路由摘要兼容", "display ip routing-table"),
            ("邻接采集", "show lldp neighbors detail"),
            ("邻接采集兼容", "display lldp neighbor"),
            ("协议邻接", "show ospf neighbor"),
            ("协议邻接兼容", "display ospf peer"),
            ("设备时钟", "show clock"),
            ("设备时钟兼容", "display clock"),
            ("权限探测", "show privilege"),
            ("权限探测兼容", "display users"),
        ]

    def _is_history_problem(self, problem: str) -> bool:
        lowered = str(problem or "").strip().lower()
        if not lowered:
            return False
        keywords = (
            "上次",
            "历史",
            "曾经",
            "闪断",
            "抖动",
            "间歇",
            "flap",
            "flapping",
            "history",
            "last",
            "intermittent",
        )
        return any(token in lowered for token in keywords)

    def _history_collect_commands(self, problem: str) -> list[tuple[str, str]]:
        lowered = str(problem or "").strip().lower()
        commands: list[tuple[str, str]] = [
            ("历史日志采集兼容", "display logbuffer"),
            ("历史日志采集", "show logging | last 200"),
            ("历史告警采集兼容", "display alarm active"),
        ]
        if "ospf" in lowered:
            commands.extend(
                [
                    ("OSPF事件日志兼容", "display logbuffer | include OSPF|DOWN|UP"),
                    ("OSPF事件日志", "show logging | include OSPF|ADJ|DOWN|UP"),
                    ("OSPF邻接补采", "display ospf peer"),
                ]
            )
        return commands

    def _should_skip_collect_command_by_vendor(self, vendor: str, normalized_command: str) -> bool:
        normalized_vendor = str(vendor or "").strip().lower()
        cmd = " ".join(str(normalized_command or "").strip().lower().split())
        if not normalized_vendor or not cmd:
            return False
        if "huawei" in normalized_vendor and cmd.startswith("show "):
            return True
        if ("arista" in normalized_vendor or "cisco" in normalized_vendor) and cmd.startswith("display "):
            return True
        return False

    async def _allocate_next_step_no(self, job_id: str) -> int:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return 1
            if not job.command_results:
                return 1
            return max(int(item.step_no or 0) for item in job.command_results) + 1

    async def _collect_device_with_llm(self, job_id: str, device_id: str) -> None:
        if not self.deepseek_diagnoser.enabled:
            return
        max_rounds = max(1, int(os.getenv("V2_COLLECT_LLM_MAX_ROUNDS", "3")))
        max_commands_per_round = max(1, int(os.getenv("V2_COLLECT_LLM_MAX_COMMANDS_PER_ROUND", "4")))
        repeated_guard: dict[str, int] = {}
        planner_context = ""

        for round_no in range(1, max_rounds + 1):
            async with self._state_lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                device = self._find_device(job, device_id)
                if device is None:
                    return
                commands = self._build_llm_device_commands(job, device_id)
                evidences = self._build_llm_device_evidences(job, device_id)
                session = self._build_llm_device_session(job, device)
                user_problem = f"{job.problem}\n目标设备: {device.host}"
                planner_context = self.sop_archive.prompt_context(job.problem, vendor=device.vendor)
                self._append_trace_event(
                    job,
                    "context_snapshot",
                    f"[{device.host}] 会话上下文快照（第 {round_no} 轮）",
                    status="succeeded",
                    detail=f"iteration={round_no}; commands={len(commands)}; evidences={len(evidences)}",
                    detail_payload={
                        "iteration": round_no,
                        "device": self._job_device_trace_record(device),
                        "counts": {
                            "commands": len(commands),
                            "evidences": len(evidences),
                        },
                        "latest_command": self._job_command_trace_record(commands[-1], include_output=True) if commands else None,
                        "latest_evidence": evidences[-1].model_dump(mode="json") if evidences else None,
                        "planner_context": planner_context or None,
                    },
                    device=device,
                )
                if planner_context:
                    self._append_trace_event(
                        job,
                        "scope_decision",
                        f"[{device.host}] SOP档案候选已装载",
                        status="succeeded",
                        detail="planner_context=sop_archive",
                        detail_payload={
                            "device": self._job_device_trace_record(device),
                            "planner_context": planner_context,
                        },
                        device=device,
                    )
                self._save_state()

            llm_started_at = now_utc()
            try:
                plan, plan_debug = await self.llm_planner_bridge.propose_next_step_with_debug(
                    self.deepseek_diagnoser,
                    session=session,
                    user_problem=user_problem,
                    commands=commands,
                    evidences=evidences,
                    iteration=round_no,
                    max_iterations=max_rounds,
                    planner_context=planner_context or None,
                )
            except Exception as exc:
                llm_finished_at = now_utc()
                async with self._state_lock:
                    job = self._jobs.get(job_id)
                    device = self._find_device(job, device_id) if job else None
                    if job is not None and device is not None:
                        self._append_trace_event(
                            job,
                            "llm_request",
                            f"[{device.host}] 提交给 AI（第 {round_no} 轮）",
                            status="failed",
                            detail="request_exception",
                            detail_payload={
                                "iteration": round_no,
                                "device": self._job_device_trace_record(device),
                                "error": str(exc),
                            },
                            device=device,
                            started_at=llm_started_at,
                            completed_at=llm_finished_at,
                            duration_ms=max(0, int((llm_finished_at - llm_started_at).total_seconds() * 1000)),
                        )
                        self._append_trace_event(
                            job,
                            "llm_response",
                            f"[{device.host}] AI 原始回复（第 {round_no} 轮）",
                            status="failed",
                            detail=str(exc)[:280],
                            detail_payload={"llm": {"error": str(exc)}, "device": self._job_device_trace_record(device)},
                            device=device,
                            started_at=llm_finished_at,
                            completed_at=llm_finished_at,
                            duration_ms=0,
                        )
                        self._append_trace_event(
                            job,
                            "llm_plan",
                            f"[{device.host}] LLM 规划第 {round_no} 轮",
                            status="failed",
                            detail=str(exc)[:280],
                            detail_payload={
                                "device": self._job_device_trace_record(device),
                                "user_problem": user_problem,
                                "to_ai": {"error": str(exc)},
                                "ai_response_parsed": None,
                            },
                            device=device,
                            started_at=llm_started_at,
                            completed_at=llm_finished_at,
                            duration_ms=max(0, int((llm_finished_at - llm_started_at).total_seconds() * 1000)),
                        )
                        self._save_state()
                return
            llm_finished_at = now_utc()
            async with self._state_lock:
                job = self._jobs.get(job_id)
                device = self._find_device(job, device_id) if job else None
                if job is not None and device is not None:
                    llm_duration = max(0, int((llm_finished_at - llm_started_at).total_seconds() * 1000))
                    self._append_trace_event(
                        job,
                        "llm_request",
                        f"[{device.host}] 提交给 AI（第 {round_no} 轮）",
                        status="succeeded",
                        detail="request_submitted",
                        detail_payload=self._build_llm_request_payload(plan_debug, device=device),
                        device=device,
                        started_at=llm_started_at,
                        completed_at=llm_started_at,
                        duration_ms=0,
                    )
                    if not plan:
                        self._append_trace_event(
                            job,
                            "llm_response",
                            f"[{device.host}] AI 原始回复（第 {round_no} 轮）",
                            status="failed",
                            detail=str(plan_debug.get("error") or "LLM未返回可解析计划")[:280],
                            detail_payload=self._build_llm_response_payload(plan_debug, None, device=device),
                            device=device,
                            started_at=llm_finished_at,
                            completed_at=llm_finished_at,
                            duration_ms=0,
                        )
                        self._append_trace_event(
                            job,
                            "llm_plan",
                            f"[{device.host}] LLM 规划第 {round_no} 轮",
                            status="failed",
                            detail=str(plan_debug.get("error") or "LLM未返回可解析计划")[:280],
                            detail_payload=self._build_llm_plan_payload(
                                job=job,
                                device=device,
                                user_problem=user_problem,
                                debug=plan_debug,
                                parsed_plan=None,
                            ),
                            device=device,
                            started_at=llm_started_at,
                            completed_at=llm_finished_at,
                            duration_ms=llm_duration,
                        )
                        self._save_state()
                    else:
                        decision_text = str(plan.get("decision", "")).strip() or "-"
                        self._append_trace_event(
                            job,
                            "llm_response",
                            f"[{device.host}] AI 原始回复（第 {round_no} 轮）",
                            status="succeeded",
                            detail=f"decision={decision_text}",
                            detail_payload=self._build_llm_response_payload(plan_debug, plan, device=device),
                            device=device,
                            started_at=llm_finished_at,
                            completed_at=llm_finished_at,
                            duration_ms=0,
                        )
                        self._append_trace_event(
                            job,
                            "llm_plan",
                            f"[{device.host}] LLM 规划第 {round_no} 轮",
                            status="succeeded",
                            detail=f"decision={decision_text}",
                            detail_payload=self._build_llm_plan_payload(
                                job=job,
                                device=device,
                                user_problem=user_problem,
                                debug=plan_debug,
                                parsed_plan=plan,
                            ),
                            device=device,
                            started_at=llm_started_at,
                            completed_at=llm_finished_at,
                            duration_ms=llm_duration,
                        )
                        self._save_state()
            if not plan:
                return

            decision = str(plan.get("decision", "")).strip().lower()
            if decision == "final":
                return

            next_step = await self._allocate_next_step_no(job_id)
            planned = self._extract_plan_commands(plan, next_step_no=next_step)
            if not planned:
                return

            executed_any = False
            for title, command_text in planned[:max_commands_per_round]:
                normalized = " ".join(str(command_text or "").strip().lower().split())
                if not normalized:
                    continue
                if repeated_guard.get(normalized, 0) >= 2:
                    continue
                repeated_guard[normalized] = repeated_guard.get(normalized, 0) + 1
                step_no = await self._allocate_next_step_no(job_id)
                await self._run_device_command(
                    job_id,
                    device_id,
                    title=title,
                    command_text=command_text,
                    step_no=step_no,
                    action_group_id=None,
                    phase="collect",
                )
                executed_any = True

            if not executed_any:
                return

    def _build_llm_device_session(self, job: Job, device: JobDevice) -> Session:
        if job.mode == JobMode.inspection:
            operation_mode = OperationMode.query
        elif job.mode == JobMode.repair:
            operation_mode = OperationMode.config
        else:
            operation_mode = OperationMode.diagnosis
        return Session(
            device=DeviceTarget(
                host=device.host,
                name=device.name,
                port=device.port,
                vendor=device.vendor,
                protocol=device.protocol,
                username=device.username,
                password=device.password,
                jump_host=device.jump_host,
                jump_port=device.jump_port,
                jump_username=device.jump_username,
                jump_password=device.jump_password,
                api_token=device.api_token,
                device_type=device.device_type,
                platform=device.platform,
                software_version=device.software_version,
                version_signature=device.version_signature,
            ),
            automation_level=AutomationLevel.assisted,
            operation_mode=operation_mode,
        )

    def _build_llm_device_commands(self, job: Job, device_id: str) -> list[CommandExecution]:
        rows: list[CommandExecution] = []
        device = self._find_device(job, device_id)
        adapter_type = device.protocol if device else DeviceProtocol.ssh
        for item in sorted(job.command_results, key=lambda row: (row.step_no, row.created_at)):
            if item.device_id != device_id:
                continue
            try:
                rows.append(
                    CommandExecution(
                        id=item.id,
                        session_id=job.id,
                        step_no=item.step_no,
                        title=item.title,
                        command=item.command,
                        adapter_type=adapter_type,
                        risk_level=item.risk_level,
                        status=CommandStatus(item.status.value),
                        output=item.output,
                        error=item.error,
                        created_at=item.created_at,
                        started_at=item.started_at,
                        completed_at=item.completed_at,
                        duration_ms=item.duration_ms,
                        original_command=item.command,
                        effective_command=item.effective_command,
                    )
                )
            except Exception:
                continue
        return rows

    def _build_llm_device_evidences(self, job: Job, device_id: str) -> list[Evidence]:
        rows: list[Evidence] = []
        for item in sorted(job.evidences, key=lambda row: row.created_at):
            if item.device_id != device_id:
                continue
            try:
                rows.append(
                    Evidence(
                        id=item.id,
                        session_id=job.id,
                        command_id=item.command_id,
                        category=item.category,
                        raw_output=item.raw_output,
                        parsed_data=item.parsed_data,
                        conclusion=item.conclusion,
                        created_at=item.created_at,
                    )
                )
            except Exception:
                continue
        return rows

    def _extract_plan_commands(self, plan: dict[str, Any], *, next_step_no: int) -> list[tuple[str, str]]:
        base_title = str(plan.get("title", "")).strip()
        raw_commands = plan.get("commands")
        parsed: list[tuple[str, str]] = []

        if isinstance(raw_commands, list):
            for idx, item in enumerate(raw_commands, start=1):
                command_text = ""
                title = ""
                if isinstance(item, str):
                    command_text = item.strip()
                elif isinstance(item, dict):
                    command_text = str(item.get("command", "")).strip()
                    title = str(item.get("title", "")).strip()

                if not command_text:
                    continue
                expanded = self._split_compound_commands(command_text)
                for sub_idx, sub_command in enumerate(expanded, start=1):
                    current_title = title
                    if not current_title:
                        if base_title and len(raw_commands) > 1:
                            current_title = f"{base_title}#{idx}"
                        else:
                            current_title = base_title or f"LLM诊断步骤{next_step_no + len(parsed)}"
                    if len(expanded) > 1:
                        current_title = f"{current_title}.{sub_idx}"
                    parsed.append((current_title, sub_command))

        if parsed:
            return parsed

        single_command = str(plan.get("command", "")).strip()
        if not single_command:
            return []
        title = base_title or f"LLM诊断步骤{next_step_no}"
        expanded_single = self._split_compound_commands(single_command)
        if len(expanded_single) <= 1:
            return [(title, single_command)]
        return [(f"{title}.{idx}", cmd) for idx, cmd in enumerate(expanded_single, start=1)]

    def _split_compound_commands(self, command_text: str) -> list[str]:
        text = str(command_text or "").strip()
        if not text:
            return []
        out: list[str] = []
        for line in text.splitlines():
            current = line.strip()
            if not current:
                continue
            if ";" in current:
                for part in current.split(";"):
                    chunk = part.strip()
                    if chunk:
                        out.append(chunk)
            else:
                out.append(current)
        return out or [text]

    def _is_write_like_command(self, command_text: str) -> bool:
        normalized = " ".join(str(command_text or "").strip().lower().split())
        if not normalized:
            return False
        write_patterns = (
            "configure terminal",
            "system-view",
            "interface ",
            "shutdown",
            "undo shutdown",
            "no shutdown",
            "write memory",
            "save",
            "commit",
            "set ",
            "delete ",
            "rollback",
            "reload",
            "reboot",
        )
        if normalized in {"enable", "terminal length 0"}:
            return False
        return any(normalized.startswith(pattern) for pattern in write_patterns)

    async def _get_adapter(self, job_id: str, device_id: str):
        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                raise RuntimeError(f"device not found: {device_id}")
            existing = self._adapters[job_id].get(device_id)

        session = Session(
            device=DeviceTarget(
                host=device.host,
                name=device.name,
                port=device.port,
                vendor=device.vendor,
                protocol=device.protocol,
                username=device.username,
                password=device.password,
                jump_host=device.jump_host,
                jump_port=device.jump_port,
                jump_username=device.jump_username,
                jump_password=device.jump_password,
                api_token=device.api_token,
                device_type=device.device_type,
                platform=device.platform,
                software_version=device.software_version,
                version_signature=device.version_signature,
            ),
            automation_level=AutomationLevel.assisted,
        )
        try:
            adapter, mode = await ensure_connected_adapter(
                existing,
                session,
                allow_simulation=self.allow_simulation,
                build_factory=build_adapter,
                on_connect_success=lambda connect_mode, _adapter: self._trace_job_adapter_state(
                    job_id,
                    device_id,
                    mode=connect_mode,
                    status="succeeded",
                ),
                on_connect_failure=lambda connect_mode, exc, _adapter: self._trace_job_adapter_state(
                    job_id,
                    device_id,
                    mode=connect_mode,
                    status="failed",
                    error=str(exc),
                ),
            )
        except Exception:
            if existing is not None:
                async with self._state_lock:
                    if self._adapters.get(job_id, {}).get(device_id) is existing:
                        self._adapters[job_id].pop(device_id, None)
            raise

        async with self._state_lock:
            self._adapters[job_id][device_id] = adapter
        return adapter

    async def _close_job_adapters(self, job_id: str) -> None:
        rows = self._adapters.pop(job_id, {})
        for device_id, adapter in rows.items():
            try:
                await close_connected_adapter(
                    adapter,
                    on_close=lambda job_id=job_id, device_id=device_id: self._trace_job_adapter_state(
                        job_id,
                        device_id,
                        mode="close",
                        status="succeeded",
                        closed=True,
                    ),
                )
            except Exception:
                pass

    async def _run_device_command(
        self,
        job_id: str,
        device_id: str,
        *,
        title: str,
        command_text: str,
        step_no: int,
        action_group_id: str | None,
        phase: str = "collect",
    ) -> JobCommandResult:
        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                raise RuntimeError(f"device not found: {device_id}")
            risk_level = self.risk_engine.classify(command_text)
            command = JobCommandResult(
                job_id=job_id,
                device_id=device_id,
                action_group_id=action_group_id,
                step_no=step_no,
                title=title,
                command=command_text,
                risk_level=risk_level,
                status=JobCommandStatus.running,
                started_at=now_utc(),
            )
            job.command_results.append(command)
            self._append_event(
                job,
                "command_started",
                {
                    "command_id": command.id,
                    "device_id": device_id,
                    "title": title,
                    "command": command_text,
                },
            )
            self._append_trace_event(
                job,
                "policy_decision",
                "执行前策略判定",
                status="succeeded",
                detail=f"decision=allow; phase={phase}; risk={risk_level.value}; source=v2_direct_allow",
                detail_payload={
                    "decision": "allow",
                    "phase": phase,
                    "risk_level": risk_level.value,
                    "source": "v2_direct_allow",
                    "command": self._job_command_trace_record(command, include_output=False),
                },
                command=command,
                device=device,
                started_at=command.started_at,
            )

            if phase == "collect" and self._is_write_like_command(command_text):
                command.status = JobCommandStatus.blocked
                command.error = "Collect phase is read-only; write/config command blocked."
                command.constraint_source = "mode_scope_block"
                command.constraint_reason = "collect_read_only_scope"
                command.completed_at = now_utc()
                if command.started_at:
                    command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
                self._append_event(
                    job,
                    "command_blocked",
                    {
                        "command_id": command.id,
                        "device_id": device_id,
                        "phase": phase,
                        "reason": command.error,
                    },
                )
                self._append_trace_event(
                    job,
                    "scope_decision",
                    "执行前会话范围判定",
                    status="blocked",
                    detail="decision=blocked; phase=collect; reason=collect_read_only_scope",
                    detail_payload={
                        "decision": "blocked",
                        "phase": phase,
                        "reason": "collect_read_only_scope",
                        "command": self._job_command_trace_record(command, include_output=False),
                    },
                    command=command,
                    device=device,
                    started_at=command.started_at,
                    completed_at=command.completed_at,
                    duration_ms=command.duration_ms,
                )
                self._append_trace_event(
                    job,
                    "command_execution",
                    f"设备执行命令 #{command.step_no}: {title}",
                    status="blocked",
                    detail=command.error,
                    detail_payload={"command": self._job_command_trace_record(command, include_output=False)},
                    command=command,
                    device=device,
                    started_at=command.started_at,
                    completed_at=command.completed_at,
                    duration_ms=command.duration_ms,
                )
                self._save_state()
                return command
            self._save_state()

        command_to_run = command_text
        capability_state = None
        capability_reason = None

        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                raise RuntimeError(f"device not found: {device_id}")
            matched = self.store.resolve_command_capability(
                host=device.host,
                protocol=device.protocol,
                device_type=device.device_type,
                vendor=device.vendor,
                version_signature=device.version_signature,
                command_text=command_text,
            )
            if matched is not None:
                rule = matched.rule
                self.store.register_command_capability_hit(rule.id)
                if rule.action == "block":
                    command.status = JobCommandStatus.blocked
                    command.error = rule.reason_text or "Blocked by capability rule"
                    command.constraint_source = "capability_block"
                    command.constraint_reason = command.error
                    command.capability_state = "block"
                    command.capability_reason = command.error
                    command.completed_at = now_utc()
                    command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000)) if command.started_at else 0
                    self._append_event(
                        job,
                        "command_blocked",
                        {
                            "command_id": command.id,
                            "device_id": device_id,
                            "reason": command.error,
                        },
                    )
                    self._append_trace_event(
                        job,
                        "capability_decision",
                        "执行前命令能力判定（阻断）",
                        status="blocked",
                        detail=f"decision=block_hit; rule_id={rule.id}; reason={command.error}",
                        detail_payload={
                            "decision": "block_hit",
                            "rule_id": rule.id,
                            "reason": command.error,
                            "command": self._job_command_trace_record(command, include_output=False),
                        },
                        command=command,
                        device=device,
                        started_at=command.started_at,
                        completed_at=command.completed_at,
                        duration_ms=command.duration_ms,
                    )
                    self._append_trace_event(
                        job,
                        "command_execution",
                        f"设备执行命令 #{command.step_no}: {title}",
                        status="blocked",
                        detail=command.error,
                        detail_payload={"command": self._job_command_trace_record(command, include_output=False)},
                        command=command,
                        device=device,
                        started_at=command.started_at,
                        completed_at=command.completed_at,
                        duration_ms=command.duration_ms,
                    )
                    self._touch_command_profile(device.version_signature, command_text, success=False, error=command.error)
                    self._save_state()
                    return command
                if rule.action == "rewrite" and rule.rewrite_to:
                    command_to_run = rule.rewrite_to
                    capability_state = "rewrite"
                    capability_reason = rule.reason_text or "rewritten by capability rule"
                    self._append_trace_event(
                        job,
                        "capability_decision",
                        "执行前命令能力判定（改写）",
                        status="succeeded",
                        detail=f"decision=rewrite_hit; rule_id={rule.id}; from={command.command}; to={command_to_run}",
                        detail_payload={
                            "decision": "rewrite_hit",
                            "rule_id": rule.id,
                            "from": command.command,
                            "to": command_to_run,
                            "command": self._job_command_trace_record(command, include_output=False),
                        },
                        command=command,
                        device=device,
                        started_at=command.started_at,
                    )

        adapter = await self._get_adapter(job_id, device_id)

        async def _should_stop() -> bool:
            async with self._state_lock:
                current_job = self._jobs.get(job_id)
                return current_job is None or current_job.status == JobStatus.cancelled

        async def _handle_rejected(_message: str) -> None:
            async with self._state_lock:
                job = self._jobs.get(job_id)
                device = self._find_device(job, device_id) if job else None
                if not job or not device:
                    return
                command.status = JobCommandStatus.rejected
                command.error = "Stopped by operator"
                command.capability_state = capability_state
                command.capability_reason = capability_reason
                command.completed_at = now_utc()
                if command.started_at:
                    command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
                self._append_trace_event(
                    job,
                    "command_execution",
                    f"设备执行命令 #{command.step_no}: {title}",
                    status="stopped",
                    detail=command.error,
                    detail_payload={"command": self._job_command_trace_record(command, include_output=False)},
                    command=command,
                    device=device,
                    started_at=command.started_at,
                    completed_at=command.completed_at,
                    duration_ms=command.duration_ms,
                )
                self._save_state()

        async def _handle_failure(message: str) -> None:
            apply_adapter_command_meta(command, adapter)
            async with self._state_lock:
                job = self._jobs.get(job_id)
                device = self._find_device(job, device_id) if job else None
                if not job or not device:
                    return
                command.status = JobCommandStatus.failed
                command.error = message
                if not command.effective_command:
                    command.effective_command = command_to_run
                command.capability_state = capability_state
                command.capability_reason = capability_reason
                command.completed_at = now_utc()
                if command.started_at:
                    command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
                self._append_event(
                    job,
                    "command_failed",
                    {
                        "command_id": command.id,
                        "device_id": device_id,
                        "error": command.error[:300],
                    },
                )
                self._append_trace_event(
                    job,
                    "command_execution",
                    f"设备执行命令 #{command.step_no}: {title}",
                    status="failed",
                    detail=(command.error or "")[:280],
                    detail_payload={"command": self._job_command_trace_record(command, include_output=False)},
                    command=command,
                    device=device,
                    started_at=command.started_at,
                    completed_at=command.completed_at,
                    duration_ms=command.duration_ms,
                )
                self._touch_command_profile(device.version_signature, command_text, success=False, error=command.error)
                self._save_state()

        async def _handle_success(output: str) -> None:
            apply_adapter_command_meta(command, adapter)
            if not command.effective_command:
                command.effective_command = command_to_run
            parsed = parse_command_runtime(command.effective_command or command_to_run, output)
            finished_at = now_utc()

            async with self._state_lock:
                job = self._jobs.get(job_id)
                device = self._find_device(job, device_id) if job else None
                if not job or not device:
                    return

                evidence = JobEvidence(
                    job_id=job_id,
                    device_id=device_id,
                    command_id=command.id,
                    category=parsed.category,
                    raw_output=output,
                    parsed_data=parsed.parsed_data,
                    conclusion=parsed.conclusion,
                )
                job.evidences.append(evidence)

                command.status = JobCommandStatus.succeeded
                command.output = output
                command.capability_state = capability_state
                command.capability_reason = capability_reason
                command.completed_at = finished_at
                command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000)) if command.started_at else 0

                apply_device_profile_to_job_device(device, parsed.device_profile)
                self._apply_incidents_from_evidence(job, device, evidence)
                self._collect_topology_hints(job, device, command_to_run, output)

                self._learn_capability_from_command(job, device, command, parsed.category)
                self._touch_command_profile(device.version_signature, command_text, success=True, error=None, rewritten=bool(capability_state == "rewrite"))

                self._append_event(
                    job,
                    "command_completed",
                    {
                        "command_id": command.id,
                        "device_id": device_id,
                        "status": command.status.value,
                        "category": parsed.category,
                        "conclusion": parsed.conclusion[:220],
                    },
                )
                self._append_trace_event(
                    job,
                    "command_execution",
                    f"设备执行命令 #{command.step_no}: {title}",
                    status="succeeded",
                    detail=(command.effective_command or command.command)[:280],
                    detail_payload={"command": self._job_command_trace_record(command, include_output=True)},
                    command=command,
                    device=device,
                    started_at=command.started_at,
                    completed_at=command.completed_at,
                    duration_ms=command.duration_ms,
                )
                self._append_trace_event(
                    job,
                    "evidence_parse",
                    "证据解析",
                    status="succeeded",
                    detail=f"category={parsed.category}; conclusion={str(parsed.conclusion)[:180]}",
                    detail_payload={
                        "command": self._job_command_trace_record(command, include_output=False),
                        "parser_result": {
                            "category": parsed.category,
                            "conclusion": parsed.conclusion,
                            "parsed_data": parsed.parsed_data,
                        },
                        "evidence": evidence.model_dump(mode="json"),
                    },
                    command=command,
                    device=device,
                    started_at=command.completed_at or finished_at,
                    completed_at=command.completed_at or finished_at,
                    duration_ms=0,
                )
                self._save_state()

        await execute_single_command(
            adapter,
            command_to_run,
            should_stop=_should_stop,
            on_rejected=_handle_rejected,
            on_success=_handle_success,
            on_failure=_handle_failure,
        )

        return command

    def _severity_weight(self, severity: str) -> float:
        if severity == "high":
            return 3.0
        if severity == "medium":
            return 2.0
        return 1.0

    def _risk_rank(self, level: RiskLevel) -> int:
        if level == RiskLevel.high:
            return 3
        if level == RiskLevel.medium:
            return 2
        return 1

    def _max_risk_level(self, commands: list[str]) -> RiskLevel:
        best = RiskLevel.low
        for command in commands:
            level = self.risk_engine.classify(command)
            if self._risk_rank(level) > self._risk_rank(best):
                best = level
        return best

    def _touch_command_profile(
        self,
        version_signature: str | None,
        command_key: str,
        *,
        success: bool,
        error: str | None,
        rewritten: bool = False,
    ) -> None:
        signature = str(version_signature or "unknown").strip().lower() or "unknown"
        key = self._profile_key(signature, command_key)
        profile = self._command_profiles.get(key)
        if profile is None:
            profile = CommandProfile(version_signature=signature, command_key=command_key.strip().lower())
            self._command_profiles[key] = profile
        profile.total_count += 1
        if success:
            profile.success_count += 1
        else:
            profile.failure_count += 1
            profile.last_error = (error or "").strip() or None
        if rewritten:
            profile.rewrite_hit_count += 1
        profile.updated_at = now_utc()

    def _profile_key(self, version_signature: str, command_key: str) -> str:
        return f"{version_signature.strip().lower()}::{command_key.strip().lower()}"

    def _learn_capability_from_command(self, job: Job, device: JobDevice, command: JobCommandResult, category: str) -> None:
        if category != "command_error" and command.status != JobCommandStatus.failed:
            return
        if not device.version_signature:
            return

        adapter = self._adapters.get(job.id, {}).get(device.id)
        last_meta = getattr(adapter, "last_command_meta", {}) if adapter is not None else {}
        retry_used = bool(last_meta.get("retry_used")) if isinstance(last_meta, dict) else False
        retry_to = str(last_meta.get("retry_to") or "").strip() if isinstance(last_meta, dict) else ""

        if retry_used and retry_to and retry_to.lower() != command.command.lower():
            learned = self.store.command_capability_store.learn_rewrite(
                host=device.host,
                protocol=device.protocol,
                device_type=device.device_type,
                vendor=device.vendor,
                version_signature=device.version_signature,
                failed_command=command.command,
                rewrite_to=retry_to,
                reason_text=(command.error or "auto learned from retry-success")[:300],
            )
            if learned is not None:
                self._append_event_with_trace(
                    job,
                    "capability_decision",
                    {
                        "device_id": device.id,
                        "command_id": command.id,
                        "decision": "learned_update",
                        "action": "rewrite",
                        "rule_id": learned.id,
                        "from": command.command,
                        "to": retry_to,
                    },
                    step_type="capability_decision",
                    title="命令能力学习更新（改写）",
                    status="succeeded",
                    detail=f"decision=learned_update; action=rewrite; from={command.command}; to={retry_to}",
                    detail_payload={
                        "decision": "learned_update",
                        "action": "rewrite",
                        "rule_id": learned.id,
                        "from": command.command,
                        "to": retry_to,
                        "device": self._job_device_trace_record(device),
                        "command": self._job_command_trace_record(command, include_output=False),
                    },
                    command=command,
                    device=device,
                )
            return

        if command.status == JobCommandStatus.failed or category == "command_error":
            learned = self.store.command_capability_store.learn_block(
                host=device.host,
                protocol=device.protocol,
                device_type=device.device_type,
                vendor=device.vendor,
                version_signature=device.version_signature,
                failed_command=command.command,
                reason_text=(command.error or "auto learned from syntax failure")[:300],
            )
            if learned is not None:
                self._append_event_with_trace(
                    job,
                    "capability_decision",
                    {
                        "device_id": device.id,
                        "command_id": command.id,
                        "decision": "learned_update",
                        "action": "block",
                        "rule_id": learned.id,
                        "command": command.command,
                    },
                    step_type="capability_decision",
                    title="命令能力学习更新（阻断）",
                    status="succeeded",
                    detail=f"decision=learned_update; action=block; command={command.command}",
                    detail_payload={
                        "decision": "learned_update",
                        "action": "block",
                        "rule_id": learned.id,
                        "command": self._job_command_trace_record(command, include_output=False),
                        "device": self._job_device_trace_record(device),
                    },
                    command=command,
                    device=device,
                )

    def _apply_incidents_from_evidence(self, job: Job, device: JobDevice, evidence: JobEvidence) -> None:
        parsed = evidence.parsed_data if isinstance(evidence.parsed_data, dict) else {}
        now_ts = evidence.created_at

        if evidence.category == "interface":
            admin_down = parsed.get("admin_down_interfaces") or []
            down_interfaces = parsed.get("down_interfaces") or []
            if admin_down:
                job.incidents.append(
                    IncidentEvent(
                        job_id=job.id,
                        device_id=device.id,
                        timestamp=now_ts,
                        severity="high",
                        category="interface",
                        title="interface_admin_down",
                        detail=",".join(str(item) for item in admin_down),
                        evidence_id=evidence.id,
                    )
                )
            elif down_interfaces:
                job.incidents.append(
                    IncidentEvent(
                        job_id=job.id,
                        device_id=device.id,
                        timestamp=now_ts,
                        severity="medium",
                        category="interface",
                        title="interface_down",
                        detail=",".join(str(item) for item in down_interfaces),
                        evidence_id=evidence.id,
                    )
                )

        if evidence.category == "routing" and bool(parsed.get("missing_default_route")):
            job.incidents.append(
                IncidentEvent(
                    job_id=job.id,
                    device_id=device.id,
                    timestamp=now_ts,
                    severity="high",
                    category="routing",
                    title="missing_default_route",
                    detail=evidence.conclusion[:240],
                    evidence_id=evidence.id,
                )
            )

        if evidence.category == "connectivity" and bool(parsed.get("packet_loss_100")):
            job.incidents.append(
                IncidentEvent(
                    job_id=job.id,
                    device_id=device.id,
                    timestamp=now_ts,
                    severity="medium",
                    category="connectivity",
                    title="packet_loss",
                    detail=evidence.conclusion[:240],
                    evidence_id=evidence.id,
                )
            )

        if evidence.category == "command_error":
            job.incidents.append(
                IncidentEvent(
                    job_id=job.id,
                    device_id=device.id,
                    timestamp=now_ts,
                    severity="low",
                    category="command_error",
                    title="command_error",
                    detail=evidence.conclusion[:240],
                    evidence_id=evidence.id,
                )
            )

        if evidence.category == "protocol":
            flap_count = int(parsed.get("ospf_flap_log_count") or 0)
            non_full_count = int(parsed.get("non_full_count") or 0)
            neighbor_count = int(parsed.get("neighbor_count") or 0)

            if flap_count > 0:
                job.incidents.append(
                    IncidentEvent(
                        job_id=job.id,
                        device_id=device.id,
                        timestamp=now_ts,
                        severity="high",
                        category="protocol",
                        title="ospf_flap_history",
                        detail=evidence.conclusion[:240],
                        evidence_id=evidence.id,
                    )
                )
            elif non_full_count > 0:
                job.incidents.append(
                    IncidentEvent(
                        job_id=job.id,
                        device_id=device.id,
                        timestamp=now_ts,
                        severity="medium",
                        category="protocol",
                        title="ospf_neighbor_not_full",
                        detail=evidence.conclusion[:240],
                        evidence_id=evidence.id,
                    )
                )
            elif neighbor_count == 0 and ("ospf" in (evidence.conclusion or "").lower()):
                job.incidents.append(
                    IncidentEvent(
                        job_id=job.id,
                        device_id=device.id,
                        timestamp=now_ts,
                        severity="medium",
                        category="protocol",
                        title="ospf_neighbor_missing",
                        detail=evidence.conclusion[:240],
                        evidence_id=evidence.id,
                    )
                )

    def _collect_topology_hints(self, job: Job, device: JobDevice, command: str, output: str) -> None:
        lowered_cmd = command.strip().lower()
        if "lldp" not in lowered_cmd and "ospf" not in lowered_cmd and "isis" not in lowered_cmd and "bgp" not in lowered_cmd:
            return

        raw_neighbors: set[str] = set()
        patterns = [
            r"(?im)^\s*system\s+name\s*[:=]\s*([A-Za-z0-9._-]{1,64})",
            r"(?im)^\s*device\s+id\s*[:=]\s*([A-Za-z0-9._-]{1,64})",
            r"(?im)^\s*neighbor\s*[:=]\s*([A-Za-z0-9._-]{1,64})",
            r"(?im)^\s*peer\s+router\s+id\s*[:=]\s*([A-Za-z0-9._-]{1,64})",
        ]
        for pattern in patterns:
            for match in __import__("re").findall(pattern, output or ""):
                value = str(match).strip()
                if value:
                    raw_neighbors.add(value)

        for neighbor in sorted(raw_neighbors):
            edge = JobTopologyEdge(source=device.host, target=neighbor, kind="inferred", confidence=0.7, reason="neighbor_discovery")
            if not any(
                item.source == edge.source and item.target == edge.target and item.kind == edge.kind
                for item in job.external_topology_edges
            ):
                job.external_topology_edges.append(edge)

    async def _correlate_phase(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            job.phase = JobPhase.correlate
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "phase_changed",
                {"phase": "correlate", "status": job.status.value},
                step_type="session_control",
                title=f"阶段切换：{self._format_phase_label('correlate')}",
                status=job.status.value,
                detail=f"phase=correlate; status={job.status.value}",
                detail_payload={"phase": "correlate", "status": job.status.value},
                completed_at=job.updated_at,
                duration_ms=0,
            )

            incidents = sorted(job.incidents, key=lambda item: item.timestamp)
            clusters: list[IncidentCluster] = []
            for incident in incidents:
                if not clusters:
                    clusters.append(
                        IncidentCluster(
                            job_id=job.id,
                            start_at=incident.timestamp,
                            end_at=incident.timestamp,
                            incident_ids=[incident.id],
                            device_ids=[incident.device_id],
                            incident_count=1,
                        )
                    )
                    continue
                tail = clusters[-1]
                gap = int((incident.timestamp - tail.end_at).total_seconds())
                if gap <= job.max_gap_seconds:
                    tail.end_at = incident.timestamp
                    tail.incident_ids.append(incident.id)
                    if incident.device_id not in tail.device_ids:
                        tail.device_ids.append(incident.device_id)
                    tail.incident_count = len(tail.incident_ids)
                else:
                    clusters.append(
                        IncidentCluster(
                            job_id=job.id,
                            start_at=incident.timestamp,
                            end_at=incident.timestamp,
                            incident_ids=[incident.id],
                            device_ids=[incident.device_id],
                            incident_count=1,
                        )
                    )

            job.clusters = clusters
            self._resolve_causal_graph_and_root(job)
            self._append_event_with_trace(
                job,
                "correlate_completed",
                {
                    "cluster_count": len(job.clusters),
                    "incident_count": len(job.incidents),
                    "root_device_id": job.rca_result.root_device_id if job.rca_result else None,
                },
                step_type="evidence_parse",
                title="多设备关联分析完成",
                status="succeeded",
                detail=(
                    f"clusters={len(job.clusters)}; "
                    f"incidents={len(job.incidents)}; "
                    f"root_device={job.rca_result.root_device_id if job.rca_result else '-'}"
                ),
                detail_payload={
                    "cluster_count": len(job.clusters),
                    "incident_count": len(job.incidents),
                    "root_device_id": job.rca_result.root_device_id if job.rca_result else None,
                },
                completed_at=job.updated_at,
                duration_ms=0,
            )
            self._save_state()

        await self._llm_refine_rca(job_id)

    def _resolve_causal_graph_and_root(self, job: Job) -> None:
        prefer_zh = self._prefer_chinese_output(job.problem)
        focus = self._infer_problem_focus(job.problem)
        if not job.incidents:
            base_root_cause = (
                "未采集到可用于根因判断的异常证据。"
                if prefer_zh
                else "No actionable incident evidence was captured."
            )
            base_impact_scope = (
                f"当前涉及设备 {len(job.devices)} 台，但证据为空。"
                if prefer_zh
                else f"{len(job.devices)} devices in scope, but evidence set is empty."
            )
            job.rca_result = RCAResult(
                job_id=job.id,
                root_cause=base_root_cause,
                impact_scope=base_impact_scope,
                summary=(
                    "在所选时间窗内未采集到可用于根因判断的异常证据。"
                    if prefer_zh
                    else "No incident evidence captured within the selected window."
                ),
                recommendation=(
                    "建议扩大时间窗或补充设备范围后重试，并优先采集历史日志与协议邻接变化证据。"
                    if prefer_zh
                    else "Expand time window or add more devices and rerun collection."
                ),
                confidence=0.0,
            )
            return

        primary_cluster = max(job.clusters, key=lambda item: (item.incident_count, -item.start_at.timestamp())) if job.clusters else None
        scoped_device_ids = set(primary_cluster.device_ids) if primary_cluster else {item.device_id for item in job.incidents}
        devices_by_id = {item.id: item for item in job.devices}
        devices_by_host = {item.host.lower(): item for item in job.devices}
        devices_by_name = {str(item.name or "").strip().lower(): item for item in job.devices if item.name}

        edges: list[CausalEdge] = []
        if job.topology_mode in {TopologyMode.external, TopologyMode.hybrid, TopologyMode.auto}:
            for raw_edge in job.external_topology_edges:
                source = self._resolve_device_ref(raw_edge.source, devices_by_host, devices_by_name)
                target = self._resolve_device_ref(raw_edge.target, devices_by_host, devices_by_name)
                if source is None or target is None:
                    continue
                if source.id == target.id:
                    continue
                if source.id not in scoped_device_ids and target.id not in scoped_device_ids:
                    continue
                edge = CausalEdge(
                    job_id=job.id,
                    source_device_id=source.id,
                    target_device_id=target.id,
                    kind=raw_edge.kind,
                    confidence=raw_edge.confidence,
                    reason=raw_edge.reason,
                )
                if not any(item.source_device_id == edge.source_device_id and item.target_device_id == edge.target_device_id for item in edges):
                    edges.append(edge)

        job.causal_edges = edges

        incidents_by_device: dict[str, list[IncidentEvent]] = defaultdict(list)
        for item in job.incidents:
            if item.device_id in scoped_device_ids:
                incidents_by_device[item.device_id].append(item)

        first_seen: dict[str, datetime] = {}
        for device_id, rows in incidents_by_device.items():
            first_seen[device_id] = min(row.timestamp for row in rows)

        ordered_devices = sorted(first_seen.items(), key=lambda kv: kv[1])
        order_rank = {device_id: idx for idx, (device_id, _) in enumerate(ordered_devices)}

        outdegree = defaultdict(int)
        propagation = defaultdict(float)
        for edge in edges:
            if edge.source_device_id in scoped_device_ids and edge.target_device_id in scoped_device_ids:
                outdegree[edge.source_device_id] += 1
                src_ts = first_seen.get(edge.source_device_id)
                dst_ts = first_seen.get(edge.target_device_id)
                if src_ts and dst_ts and dst_ts >= src_ts:
                    gap = max(1, (dst_ts - src_ts).total_seconds())
                    if gap <= job.max_gap_seconds * 2:
                        propagation[edge.source_device_id] += 1.0

        score_map: dict[str, float] = {}
        weights = job.rca_weights or RCAWeights()
        change_counts: dict[str, int] = defaultdict(int)
        relevant_incident_total = 0
        for command in job.command_results:
            if command.status in {JobCommandStatus.failed, JobCommandStatus.blocked, JobCommandStatus.rejected}:
                continue
            if command.risk_level in {RiskLevel.medium, RiskLevel.high}:
                change_counts[command.device_id] += 1
        for device_id, rows in incidents_by_device.items():
            relevant_rows = [item for item in rows if self._incident_matches_focus(item, focus)]
            relevant_incident_total += len(relevant_rows)
            anomaly_score = sum(self._severity_weight(item.severity) for item in rows)
            early_bonus = max(0.0, 2.0 - float(order_rank.get(device_id, 0)) * 0.3)
            upstream_bonus = float(outdegree.get(device_id, 0)) * 0.8 + float(propagation.get(device_id, 0)) * 1.2
            change_bonus = min(3.0, float(change_counts.get(device_id, 0)) * 0.5)
            consistency = max(
                0.0,
                float(len(rows)) - float(len([item for item in rows if item.category == "command_error"])) * 0.4,
            )
            relevance_bonus = min(3.0, float(len(relevant_rows)) * 0.8)
            score_map[device_id] = (
                weights.anomaly * anomaly_score
                + weights.timing * early_bonus
                + weights.topology * upstream_bonus
                + weights.change * change_bonus
                + weights.consistency * consistency
                + relevance_bonus
            )

        if not score_map:
            base_root_cause = (
                "异常证据不足，无法稳定排序根因设备。"
                if prefer_zh
                else "Insufficient clustered incidents to rank a root device."
            )
            base_impact_scope = self._build_impact_scope_text(job, scoped_device_ids, prefer_zh)
            job.rca_result = RCAResult(
                job_id=job.id,
                root_cause=base_root_cause,
                impact_scope=base_impact_scope,
                summary=(
                    "聚类异常证据不足，暂时无法稳定计算根因排序。"
                    if prefer_zh
                    else "Unable to score root cause due to insufficient clustered incidents."
                ),
                recommendation=(
                    "建议补充协议邻接、接口状态与历史日志证据后再次分析。"
                    if prefer_zh
                    else "Collect additional protocol and interface evidence."
                ),
                confidence=0.1,
            )
            return

        sorted_scores = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        root_device_id, root_score = sorted_scores[0]
        second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0
        margin = max(0.0, root_score - second_score)
        confidence = min(0.95, 0.35 + min(0.4, margin / max(1.0, root_score)) + min(0.2, len(scoped_device_ids) / 20.0))

        root_device = devices_by_id.get(root_device_id)
        impacted = sorted(scoped_device_ids)
        low_confidence = confidence < 0.55
        protocol_focus_without_evidence = focus in {"ospf", "bgp"} and relevant_incident_total == 0
        uncertain = protocol_focus_without_evidence or (low_confidence and not edges)

        if uncertain:
            if prefer_zh:
                missing_reason = (
                    "当前证据未形成协议级根因链（缺少直接邻接抖动/日志证据）。"
                    if protocol_focus_without_evidence
                    else "当前证据区分度不足（置信度较低且缺少传播拓扑证据）。"
                )
                root_cause_text = f"不确定。{missing_reason}"
                summary = (
                    f"暂无法确认唯一根因。{missing_reason}"
                    f" 在 {job.max_gap_seconds} 秒时间窗内仅形成 {len(job.incidents)} 条异常关联，"
                    f"涉及 {len(scoped_device_ids)} 台设备。"
                )
                recommendation = (
                    "建议补采协议历史日志（如 OSPF 邻接 down/up 记录）、告警时间线与链路拓扑关系，"
                    "再进行根因排序。"
                )
            else:
                root_cause_text = (
                    "Uncertain root cause. Evidence does not form a stable protocol-level or topology-backed causal chain."
                )
                summary = (
                    "Unable to confirm a single root cause: evidence is insufficient to form a protocol-level causal chain "
                    "or confidence is too low without topology propagation support."
                )
                recommendation = "Collect protocol history logs, alarm timeline, and topology evidence, then rerun RCA."
            impact_scope_text = self._build_impact_scope_text(job, scoped_device_ids, prefer_zh)
            score_breakdown = {
                str(device_id): float(score)
                for device_id, score in sorted(score_map.items(), key=lambda item: item[1], reverse=True)
            }
            score_breakdown["meta_relevant_incidents"] = float(relevant_incident_total)
            job.rca_result = RCAResult(
                job_id=job.id,
                root_device_id=None,
                root_device_name=None,
                root_device_host=None,
                root_cause=root_cause_text,
                impact_scope=impact_scope_text,
                confidence=min(confidence, 0.49),
                score_breakdown=score_breakdown,
                impacted_device_ids=impacted,
                causal_edges=edges,
                summary=summary,
                recommendation=recommendation,
            )
            return

        if prefer_zh:
            root_host = root_device.host if root_device else root_device_id
            root_cause_text = self._build_root_cause_text(job, root_device_id, root_device, incidents_by_device, prefer_zh)
            impact_scope_text = self._build_impact_scope_text(job, scoped_device_ids, prefer_zh)
            summary = (
                f"根因候选设备为 {root_host}；"
                f"在 {job.max_gap_seconds} 秒时间窗内关联到 {len(job.incidents)} 条异常，涉及 {len(scoped_device_ids)} 台设备。"
            )
            if edges:
                recommendation = "建议优先处置根因候选设备，并按因果链路依次校验受影响设备。"
            else:
                recommendation = "建议优先处置根因候选设备，并补充拓扑/邻接关系证据以提高传播链路可信度。"
        else:
            root_cause_text = self._build_root_cause_text(job, root_device_id, root_device, incidents_by_device, prefer_zh)
            impact_scope_text = self._build_impact_scope_text(job, scoped_device_ids, prefer_zh)
            summary = (
                f"Root cause candidate is {root_device.host if root_device else root_device_id}; "
                f"correlated {len(job.incidents)} incidents across {len(scoped_device_ids)} devices within {job.max_gap_seconds}s window."
            )
            recommendation = "Prioritize remediation on the root candidate first, then validate downstream devices along causal edges."

        score_breakdown = {
            str(device_id): float(score)
            for device_id, score in sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        }
        score_breakdown["meta_relevant_incidents"] = float(relevant_incident_total)

        job.rca_result = RCAResult(
            job_id=job.id,
            root_device_id=root_device_id,
            root_device_name=root_device.name if root_device else None,
            root_device_host=root_device.host if root_device else None,
            root_cause=root_cause_text,
            impact_scope=impact_scope_text,
            confidence=confidence,
            score_breakdown=score_breakdown,
            impacted_device_ids=impacted,
            causal_edges=edges,
            summary=summary,
            recommendation=recommendation,
        )

    def _prefer_chinese_output(self, problem: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in str(problem or ""))

    def _infer_problem_focus(self, problem: str) -> str:
        lowered = str(problem or "").strip().lower()
        if not lowered:
            return "generic"
        if "ospf" in lowered:
            return "ospf"
        if "bgp" in lowered:
            return "bgp"
        if any(token in lowered for token in ("接口", "port", "interface", "链路", "link")):
            return "interface"
        if any(token in lowered for token in ("路由", "routing", "route", "下一跳", "next-hop")):
            return "routing"
        return "generic"

    def _incident_matches_focus(self, incident: IncidentEvent, focus: str) -> bool:
        if focus == "generic":
            return True
        text = " ".join(
            [
                str(incident.category or ""),
                str(incident.title or ""),
                str(incident.detail or ""),
            ]
        ).lower()
        if focus == "ospf":
            return "ospf" in text
        if focus == "bgp":
            return "bgp" in text
        if focus == "interface":
            return incident.category == "interface" or "interface" in text or "接口" in text
        if focus == "routing":
            return incident.category == "routing" or "route" in text or "路由" in text
        return True

    def _resolve_device_ref(self, value: str, by_host: dict[str, JobDevice], by_name: dict[str, JobDevice]) -> Optional[JobDevice]:
        key = str(value or "").strip().lower()
        if not key:
            return None
        if key in by_host:
            return by_host[key]
        if key in by_name:
            return by_name[key]
        return None

    def _build_root_cause_text(
        self,
        job: Job,
        root_device_id: str,
        root_device: JobDevice | None,
        incidents_by_device: dict[str, list[IncidentEvent]],
        prefer_zh: bool,
    ) -> str:
        rows = incidents_by_device.get(root_device_id, [])
        if not rows:
            if prefer_zh:
                return f"根因设备候选为 {root_device.host if root_device else root_device_id}，但缺少该设备的直接异常明细。"
            return f"Root device candidate is {root_device.host if root_device else root_device_id}, but direct incident details are missing."

        title_count: dict[str, int] = defaultdict(int)
        detail_snippets: list[str] = []
        for item in rows:
            label = self._incident_title_label(item.title, prefer_zh)
            title_count[label] += 1
            detail = str(item.detail or "").strip()
            if detail and len(detail_snippets) < 2:
                detail_snippets.append(detail[:120])

        top_titles = sorted(title_count.items(), key=lambda pair: pair[1], reverse=True)[:2]
        title_text = "、".join(f"{name}x{count}" for name, count in top_titles) if prefer_zh else ", ".join(f"{name}x{count}" for name, count in top_titles)
        detail_text = "；".join(detail_snippets) if prefer_zh else "; ".join(detail_snippets)
        host = root_device.host if root_device else root_device_id
        if prefer_zh:
            if detail_text:
                return f"{host} 最早且最集中出现异常，主要表现为 {title_text}。关键证据: {detail_text}。"
            return f"{host} 最早且最集中出现异常，主要表现为 {title_text}。"
        if detail_text:
            return f"{host} is the earliest and densest anomaly source, mainly {title_text}. Key evidence: {detail_text}."
        return f"{host} is the earliest and densest anomaly source, mainly {title_text}."

    def _build_impact_scope_text(self, job: Job, scoped_device_ids: set[str], prefer_zh: bool) -> str:
        id_to_host = {item.id: item.host for item in job.devices}
        hosts = [id_to_host[item] for item in sorted(scoped_device_ids) if item in id_to_host]
        host_text = ", ".join(hosts[:8]) if hosts else "-"
        if len(hosts) > 8:
            host_text = f"{host_text} ...(+{len(hosts) - 8})"
        if prefer_zh:
            return (
                f"影响设备 {len(scoped_device_ids)} 台（{host_text}），"
                f"事件聚类 {len(job.clusters)} 组，时间关联窗口 {job.max_gap_seconds} 秒。"
            )
        return (
            f"Impacted devices: {len(scoped_device_ids)} ({host_text}), "
            f"clusters: {len(job.clusters)}, time window: {job.max_gap_seconds}s."
        )

    def _incident_title_label(self, title: str, prefer_zh: bool) -> str:
        key = str(title or "").strip().lower()
        labels_zh = {
            "interface_admin_down": "管理性关闭接口",
            "interface_down": "接口down",
            "missing_default_route": "默认路由缺失",
            "packet_loss": "高丢包",
            "command_error": "命令失败",
            "ospf_flap_history": "OSPF历史抖动",
            "ospf_neighbor_not_full": "OSPF邻接非Full",
            "ospf_neighbor_missing": "OSPF邻接缺失",
        }
        labels_en = {
            "interface_admin_down": "admin-down interface",
            "interface_down": "interface down",
            "missing_default_route": "missing default route",
            "packet_loss": "packet loss",
            "command_error": "command error",
            "ospf_flap_history": "OSPF flap history",
            "ospf_neighbor_not_full": "OSPF non-full adjacency",
            "ospf_neighbor_missing": "OSPF missing adjacency",
        }
        return (labels_zh if prefer_zh else labels_en).get(key, key or ("未知异常" if prefer_zh else "unknown incident"))

    async def _llm_refine_rca(self, job_id: str) -> None:
        if not self.deepseek_diagnoser.enabled:
            return

        async with self._state_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            payload = self._build_llm_rca_payload(job)
            prefer_zh = self._prefer_chinese_output(job.problem)
            representative = job.devices[0] if job.devices else None
            if representative is not None:
                self._append_trace_event(
                    job,
                    "context_snapshot",
                    "多设备 RCA 上下文快照",
                    status="succeeded",
                    detail=f"devices={len(job.devices)}; incidents={len(job.incidents)}; evidences={len(job.evidences)}; commands={len(job.command_results)}",
                    detail_payload={
                        "counts": {
                            "devices": len(job.devices),
                            "incidents": len(job.incidents),
                            "evidences": len(job.evidences),
                            "commands": len(job.command_results),
                        },
                        "job": {
                            "id": job.id,
                            "problem": job.problem,
                            "mode": job.mode.value,
                            "phase": job.phase.value,
                        },
                        "payload_preview": payload,
                    },
                    device=representative,
                )
                self._save_state()

        system_prompt = (
            "你是网络运维RCA助手。"
            "请严格基于给定多设备证据包输出JSON，不得编造证据。"
            "如果证据不足，必须明确写“证据不足/不确定”。"
            "输出字段: summary, recommendation, confidence, root_device_host, root_cause, impact_scope。"
            "confidence 范围 0~1。"
        )
        if not prefer_zh:
            system_prompt = (
                "You are a network RCA assistant. "
                "Return strict JSON based only on provided multi-device evidence. "
                "If evidence is insufficient, explicitly say uncertain/insufficient evidence. "
                "Fields: summary, recommendation, confidence, root_device_host, root_cause, impact_scope. "
                "confidence must be between 0 and 1."
            )

        llm_started_at = now_utc()
        try:
            content = await self.deepseek_diagnoser._chat_completion(
                system_prompt=system_prompt,
                user_payload=payload,
            )
        except Exception as exc:
            llm_finished_at = now_utc()
            async with self._state_lock:
                job = self._jobs.get(job_id)
                representative = job.devices[0] if job and job.devices else None
                if job is not None:
                    request_payload = self._compact_trace_payload(
                        {"system_prompt": system_prompt, "request_payload": payload},
                        max_depth=10,
                        max_items=500,
                        text_limit=200000,
                    )
                    self._append_trace_event(
                        job,
                        "llm_request",
                        "提交给 AI（多设备 RCA）",
                        status="failed",
                        detail="request_exception",
                        detail_payload=request_payload,
                        device=representative,
                        started_at=llm_started_at,
                        completed_at=llm_started_at,
                        duration_ms=0,
                    )
                    self._append_trace_event(
                        job,
                        "llm_response",
                        "AI 原始回复（多设备 RCA）",
                        status="failed",
                        detail=str(exc)[:280],
                        detail_payload={"llm": {"error": str(exc)}},
                        device=representative,
                        started_at=llm_finished_at,
                        completed_at=llm_finished_at,
                        duration_ms=0,
                    )
                    self._save_state()
            return
        llm_finished_at = now_utc()
        async with self._state_lock:
            job = self._jobs.get(job_id)
            representative = job.devices[0] if job and job.devices else None
            if job is not None:
                request_payload = self._compact_trace_payload(
                    {"system_prompt": system_prompt, "request_payload": payload},
                    max_depth=10,
                    max_items=500,
                    text_limit=200000,
                )
                self._append_trace_event(
                    job,
                    "llm_request",
                    "提交给 AI（多设备 RCA）",
                    status="succeeded",
                    detail="request_submitted",
                    detail_payload=request_payload,
                    device=representative,
                    started_at=llm_started_at,
                    completed_at=llm_started_at,
                    duration_ms=0,
                )
        if not content:
            async with self._state_lock:
                job = self._jobs.get(job_id)
                representative = job.devices[0] if job and job.devices else None
                if job is not None:
                    self._append_trace_event(
                        job,
                        "llm_response",
                        "AI 原始回复（多设备 RCA）",
                        status="failed",
                        detail="empty_response",
                        detail_payload={"llm": {"raw_response": "", "error": "empty_response"}},
                        device=representative,
                        started_at=llm_finished_at,
                        completed_at=llm_finished_at,
                        duration_ms=0,
                    )
                    self._save_state()
            return
        parsed = self.deepseek_diagnoser._parse_json_object(content)
        async with self._state_lock:
            job = self._jobs.get(job_id)
            representative = job.devices[0] if job and job.devices else None
            if job is not None:
                self._append_trace_event(
                    job,
                    "llm_response",
                    "AI 原始回复（多设备 RCA）",
                    status="succeeded" if parsed else "failed",
                    detail="decision=final" if parsed else "unparseable_json",
                    detail_payload=self._compact_trace_payload(
                        {
                            "llm": {
                                "raw_response": content,
                                "parsed_response": parsed,
                                "error": None if parsed else "unparseable_json",
                            },
                            "ai_response_parsed": parsed,
                        },
                        max_depth=10,
                        max_items=500,
                        text_limit=200000,
                    ),
                    device=representative,
                    started_at=llm_finished_at,
                    completed_at=llm_finished_at,
                    duration_ms=0,
                )
                self._save_state()
        if not parsed:
            return

        summary_text = str(parsed.get("summary") or "").strip()
        recommendation_text = str(parsed.get("recommendation") or "").strip()
        root_cause_text = str(parsed.get("root_cause") or "").strip()
        impact_scope_text = str(parsed.get("impact_scope") or "").strip()
        if not summary_text:
            if root_cause_text and impact_scope_text:
                summary_text = f"{root_cause_text} | {impact_scope_text}"
            else:
                summary_text = root_cause_text or impact_scope_text
        if not summary_text or not recommendation_text:
            return

        confidence = self._normalize_llm_confidence(parsed.get("confidence"))
        root_host = str(parsed.get("root_device_host") or "").strip().lower()

        async with self._state_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.rca_result is None:
                job.rca_result = RCAResult(job_id=job.id)

            job.rca_result.summary = summary_text
            job.rca_result.recommendation = recommendation_text
            if root_cause_text:
                job.rca_result.root_cause = root_cause_text
            if impact_scope_text:
                job.rca_result.impact_scope = impact_scope_text
            if confidence is not None:
                job.rca_result.confidence = confidence

            mapped = None
            if root_host:
                mapped = next((item for item in job.devices if item.host.strip().lower() == root_host), None)
            if mapped is not None:
                job.rca_result.root_device_id = mapped.id
                job.rca_result.root_device_host = mapped.host
                job.rca_result.root_device_name = mapped.name

            job.updated_at = now_utc()
            representative = job.devices[0] if job.devices else None
            self._append_event_with_trace(
                job,
                "llm_rca_refined",
                {
                    "root_device_id": job.rca_result.root_device_id,
                    "confidence": job.rca_result.confidence,
                    "summary": summary_text[:220],
                },
                step_type="llm_response",
                title="AI RCA 精炼回复",
                status="succeeded",
                detail=summary_text[:220],
                detail_payload={
                    "ai_response_parsed": parsed,
                    "final_summary": job.rca_result.model_dump(mode="json"),
                },
                device=representative,
            )
            self._append_trace_event(
                job,
                "llm_final",
                "多设备总结输出",
                status="succeeded",
                detail=f"confidence={job.rca_result.confidence}; root_device={job.rca_result.root_device_host or '-'}",
                detail_payload={
                    "final_summary": job.rca_result.model_dump(mode="json"),
                    "ai_response_parsed": parsed,
                },
                device=representative,
            )
            self._save_state()

    def _build_llm_rca_payload(self, job: Job) -> dict[str, Any]:
        incidents = sorted(job.incidents, key=lambda item: item.timestamp)
        evidences = sorted(job.evidences, key=lambda item: item.created_at)
        commands = sorted(job.command_results, key=lambda item: (item.step_no, item.created_at))
        return {
            "job": {
                "id": job.id,
                "mode": job.mode.value,
                "problem": job.problem,
                "max_gap_seconds": job.max_gap_seconds,
                "topology_mode": job.topology_mode.value,
                "time_window": {
                    "start": job.window_start.isoformat() if job.window_start else None,
                    "end": job.window_end.isoformat() if job.window_end else None,
                },
            },
            "devices": [
                {
                    "id": item.id,
                    "host": item.host,
                    "name": item.name,
                    "vendor": item.vendor,
                    "platform": item.platform,
                    "software_version": item.software_version,
                    "version_signature": item.version_signature,
                    "status": item.status,
                }
                for item in job.devices
            ],
            "incidents": [
                {
                    "device_id": item.device_id,
                    "severity": item.severity,
                    "category": item.category,
                    "title": item.title,
                    "detail": item.detail,
                    "timestamp": item.timestamp.isoformat(),
                }
                for item in incidents[-180:]
            ],
            "clusters": [
                {
                    "start_at": item.start_at.isoformat(),
                    "end_at": item.end_at.isoformat(),
                    "device_ids": item.device_ids,
                    "incident_count": item.incident_count,
                }
                for item in job.clusters[-60:]
            ],
            "causal_edges": [
                {
                    "source_device_id": item.source_device_id,
                    "target_device_id": item.target_device_id,
                    "kind": item.kind,
                    "confidence": item.confidence,
                    "reason": item.reason,
                }
                for item in job.causal_edges[-120:]
            ],
            "commands": [
                {
                    "device_id": item.device_id,
                    "step_no": item.step_no,
                    "title": item.title,
                    "command": item.command,
                    "effective_command": item.effective_command,
                    "status": item.status.value,
                    "risk_level": item.risk_level.value,
                    "error": (item.error or "")[:260],
                    "output": (item.output or "")[:1200],
                }
                for item in commands[-220:]
            ],
            "evidence_conclusions": [
                {
                    "device_id": item.device_id,
                    "category": item.category,
                    "conclusion": item.conclusion,
                    "parsed_data": item.parsed_data,
                }
                for item in evidences[-180:]
            ],
            "deterministic_rca": (
                {
                    "root_device_id": job.rca_result.root_device_id if job.rca_result else None,
                    "root_device_host": job.rca_result.root_device_host if job.rca_result else None,
                    "root_cause": job.rca_result.root_cause if job.rca_result else None,
                    "impact_scope": job.rca_result.impact_scope if job.rca_result else None,
                    "confidence": job.rca_result.confidence if job.rca_result else None,
                    "summary": job.rca_result.summary if job.rca_result else None,
                    "recommendation": job.rca_result.recommendation if job.rca_result else None,
                    "score_breakdown": job.rca_result.score_breakdown if job.rca_result else {},
                }
            ),
        }

    def _normalize_llm_confidence(self, value: Any) -> float | None:
        try:
            parsed = float(value)
        except Exception:
            return None
        if parsed < 0:
            return 0.0
        if parsed > 1:
            return 1.0
        return parsed

    async def _plan_phase(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            job.phase = JobPhase.plan
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "phase_changed",
                {"phase": "plan", "status": job.status.value},
                step_type="session_control",
                title=f"阶段切换：{self._format_phase_label('plan')}",
                status=job.status.value,
                detail=f"phase=plan; status={job.status.value}",
                detail_payload={"phase": "plan", "status": job.status.value},
                completed_at=job.updated_at,
                duration_ms=0,
            )

            if job.mode != JobMode.repair:
                self._append_event_with_trace(
                    job,
                    "plan_completed",
                    {"action_group_count": 0},
                    step_type="plan_decision",
                    title="修复计划生成完成",
                    status="succeeded",
                    detail="action_groups=0; pending=0; auto_approved=0",
                    detail_payload={"action_group_count": 0, "pending_approval": 0, "auto_approved": 0},
                    completed_at=job.updated_at,
                    duration_ms=0,
                )
                self._save_state()
                return

            root_id = job.rca_result.root_device_id if job.rca_result else None
            root_device = self._find_device(job, root_id) if root_id else None
            action_groups: list[JobActionGroup] = []

            if root_device is not None:
                interfaces: list[str] = []
                for incident in job.incidents:
                    if incident.device_id != root_device.id:
                        continue
                    if incident.title != "interface_admin_down":
                        continue
                    for token in str(incident.detail or "").split(","):
                        name = token.strip()
                        if name and name not in interfaces:
                            interfaces.append(name)

                for iface in interfaces:
                    commands, rollback_commands = self._repair_commands_for_device(root_device, iface)
                    risk_level = self._max_risk_level(commands)
                    requires_approval = risk_level in {RiskLevel.medium, RiskLevel.high}
                    action_groups.append(
                        JobActionGroup(
                            job_id=job.id,
                            device_id=root_device.id,
                            title=f"修复接口 {iface}",
                            commands=commands,
                            risk_level=risk_level,
                            requires_approval=requires_approval,
                            rollback_commands=rollback_commands,
                            status=JobActionGroupStatus.pending_approval if requires_approval else JobActionGroupStatus.approved,
                        )
                    )

            job.action_groups = action_groups
            if action_groups and any(item.status == JobActionGroupStatus.pending_approval for item in action_groups):
                job.phase = JobPhase.approve
                job.status = JobStatus.waiting_approval
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "phase_changed",
                    {"phase": "approve", "status": job.status.value},
                    step_type="session_control",
                    title=f"阶段切换：{self._format_phase_label('approve')}",
                    status=job.status.value,
                    detail=f"phase=approve; status={job.status.value}",
                    detail_payload={"phase": "approve", "status": job.status.value},
                    completed_at=job.updated_at,
                    duration_ms=0,
                )
            pending_count = len([item for item in action_groups if item.status == JobActionGroupStatus.pending_approval])
            auto_count = len([item for item in action_groups if item.status == JobActionGroupStatus.approved])
            self._append_event_with_trace(
                job,
                "plan_completed",
                {
                    "action_group_count": len(action_groups),
                    "pending_approval": pending_count,
                    "auto_approved": auto_count,
                },
                step_type="plan_decision",
                title="修复计划生成完成",
                status="succeeded",
                detail=f"action_groups={len(action_groups)}; pending={pending_count}; auto_approved={auto_count}",
                detail_payload={
                    "action_group_count": len(action_groups),
                    "pending_approval": pending_count,
                    "auto_approved": auto_count,
                },
                completed_at=job.updated_at,
                duration_ms=0,
            )
            self._save_state()

    def _repair_commands_for_device(self, device: JobDevice, interface_name: str) -> tuple[list[str], list[str]]:
        vendor = str(device.vendor or "").strip().lower()
        if "huawei" in vendor:
            return (
                [
                    "system-view",
                    f"interface {interface_name}",
                    "undo shutdown",
                    "return",
                    "save",
                ],
                [
                    "system-view",
                    f"interface {interface_name}",
                    "shutdown",
                    "return",
                    "save",
                ],
            )
        return (
            [
                "configure terminal",
                f"interface {interface_name}",
                "no shutdown",
                "end",
                "write memory",
            ],
            [
                "configure terminal",
                f"interface {interface_name}",
                "shutdown",
                "end",
                "write memory",
            ],
        )

    async def _resume_job_execution(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if job.status == JobStatus.cancelled:
                return
            if job.mode != JobMode.repair:
                job.phase = JobPhase.conclude
                job.status = JobStatus.completed
                job.completed_at = now_utc()
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "job_completed",
                    {"mode": job.mode.value},
                    step_type="session_control",
                    title="多设备协同完成",
                    status="succeeded",
                    detail=f"mode={job.mode.value}",
                    detail_payload={"mode": job.mode.value},
                    completed_at=job.completed_at,
                    duration_ms=0,
                )
                self._save_state()
                await self._close_job_adapters(job_id)
                return

            pending = [item for item in job.action_groups if item.status == JobActionGroupStatus.pending_approval]
            approved = [item for item in job.action_groups if item.status == JobActionGroupStatus.approved]
            if pending and not approved:
                job.phase = JobPhase.approve
                job.status = JobStatus.waiting_approval
                job.updated_at = now_utc()
                self._save_state()
                return

            if not approved and not pending:
                job.phase = JobPhase.conclude
                job.status = JobStatus.completed
                job.completed_at = now_utc()
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "job_completed",
                    {"mode": job.mode.value, "message": "no approved action groups"},
                    step_type="session_control",
                    title="多设备协同完成",
                    status="succeeded",
                    detail=f"mode={job.mode.value}; message=no approved action groups",
                    detail_payload={"mode": job.mode.value, "message": "no approved action groups"},
                    completed_at=job.completed_at,
                    duration_ms=0,
                )
                self._save_state()
                await self._close_job_adapters(job_id)
                return

            job.phase = JobPhase.execute
            job.status = JobStatus.executing
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "phase_changed",
                {"phase": "execute", "status": job.status.value},
                step_type="session_control",
                title=f"阶段切换：{self._format_phase_label('execute')}",
                status=job.status.value,
                detail=f"phase=execute; status={job.status.value}",
                detail_payload={"phase": "execute", "status": job.status.value},
                completed_at=job.updated_at,
                duration_ms=0,
            )
            self._save_state()

        for group in approved:
            async with self._state_lock:
                current = self._jobs.get(job_id)
                if current is None or current.status == JobStatus.cancelled:
                    return
            await self._execute_action_group(job_id, group.id)

        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if any(item.status == JobActionGroupStatus.pending_approval for item in job.action_groups):
                job.phase = JobPhase.approve
                job.status = JobStatus.waiting_approval
                job.updated_at = now_utc()
                self._append_event_with_trace(
                    job,
                    "phase_changed",
                    {"phase": "approve", "status": job.status.value},
                    step_type="session_control",
                    title=f"阶段切换：{self._format_phase_label('approve')}",
                    status=job.status.value,
                    detail=f"phase=approve; status={job.status.value}",
                    detail_payload={"phase": "approve", "status": job.status.value},
                    completed_at=job.updated_at,
                    duration_ms=0,
                )
                self._save_state()
                return

            job.phase = JobPhase.analyze
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "phase_changed",
                {"phase": "analyze", "status": job.status.value},
                step_type="session_control",
                title=f"阶段切换：{self._format_phase_label('analyze')}",
                status=job.status.value,
                detail=f"phase=analyze; status={job.status.value}",
                detail_payload={"phase": "analyze", "status": job.status.value},
                completed_at=job.updated_at,
                duration_ms=0,
            )
            self._save_state()

        await self._correlate_phase(job_id)

        async with self._state_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.phase = JobPhase.conclude
            job.status = JobStatus.completed
            job.completed_at = now_utc()
            job.updated_at = now_utc()
            self._append_event_with_trace(
                job,
                "job_completed",
                {"mode": job.mode.value},
                step_type="session_control",
                title="多设备协同完成",
                status="succeeded",
                detail=f"mode={job.mode.value}",
                detail_payload={"mode": job.mode.value},
                completed_at=job.completed_at,
                duration_ms=0,
            )
            self._save_state()

        await self._close_job_adapters(job_id)

    async def _execute_action_group(self, job_id: str, action_group_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            action = next((item for item in job.action_groups if item.id == action_group_id), None)
            if action is None:
                return
            if action.status not in {JobActionGroupStatus.approved, JobActionGroupStatus.running}:
                return
            action.status = JobActionGroupStatus.running
            action.updated_at = now_utc()
            device_id = action.device_id
            commands = list(action.commands)
            device = self._find_device(job, device_id)
            self._append_event_with_trace(
                job,
                "action_group_started",
                {
                    "action_group_id": action.id,
                    "device_id": device_id,
                    "command_count": len(commands),
                },
                step_type="session_control",
                title=f"[{device.host if device else device_id}] 开始执行命令组",
                status="running",
                detail=f"command_count={len(commands)}",
                detail_payload={
                    "action_group_id": action.id,
                    "command_count": len(commands),
                    "title": action.title,
                },
                device=device,
                completed_at=action.updated_at,
                duration_ms=0,
            )
            self._save_state()

        execution_policy = job.execution_policy
        rollback_commands = list(action.rollback_commands or [])

        async def _should_stop() -> bool:
            async with self._state_lock:
                current_job = self._jobs.get(job_id)
                return current_job is None or current_job.status == JobStatus.cancelled

        async def _execute_command(command_text: str, idx: int):
            step_no = await self._allocate_next_step_no(job_id)
            return await self._run_device_command(
                job_id,
                device_id,
                title=f"执行修复命令 {idx}",
                command_text=command_text,
                step_no=step_no,
                action_group_id=action_group_id,
                phase="execute",
            )

        async def _execute_rollback(rollback: str, ridx: int):
            step_no = await self._allocate_next_step_no(job_id)
            return await self._run_device_command(
                job_id,
                device_id,
                title=f"执行回滚命令 {ridx}",
                command_text=rollback,
                step_no=step_no,
                action_group_id=action_group_id,
                phase="execute",
            )

        group_result = await execute_command_group(
            commands,
            execute_item=_execute_command,
            is_failure=lambda result: result.status in {JobCommandStatus.failed, JobCommandStatus.blocked, JobCommandStatus.rejected},
            continue_on_failure=execution_policy == "continue_on_failure",
            should_stop=_should_stop,
            rollback_items=rollback_commands if execution_policy == "rollback_template" else None,
            execute_rollback_item=_execute_rollback if rollback_commands else None,
        )

        failed = bool(group_result.failed)
        if group_result.stopped:
            return

        async with self._state_lock:
            job = self._jobs[job_id]
            action = next((item for item in job.action_groups if item.id == action_group_id), None)
            if action is None:
                return
            action.status = JobActionGroupStatus.failed if failed else JobActionGroupStatus.succeeded
            action.updated_at = now_utc()
            device = self._find_device(job, action.device_id)
            self._append_event_with_trace(
                job,
                "action_group_completed",
                {
                    "action_group_id": action.id,
                    "device_id": action.device_id,
                    "status": action.status.value,
                    "failed": failed,
                    "processed_count": group_result.processed_count,
                    "rollback_executed": group_result.rollback_count,
                },
                step_type="session_control",
                title=f"[{device.host if device else action.device_id}] 命令组执行完成",
                status=action.status.value,
                detail=f"status={action.status.value}; processed={group_result.processed_count}; rollback={group_result.rollback_count}",
                detail_payload={
                    "action_group_id": action.id,
                    "status": action.status.value,
                    "failed": failed,
                    "processed_count": group_result.processed_count,
                    "rollback_executed": group_result.rollback_count,
                },
                device=device,
                completed_at=action.updated_at,
                duration_ms=0,
            )
            self._save_state()

    async def build_report(self, job_id: str, fmt: str = "json") -> JobReportResponse | dict[str, Any]:
        timeline = await self.get_timeline(job_id)
        job = timeline.job
        if fmt == "json":
            return timeline.model_dump(mode="json")

        lines: list[str] = []
        lines.append(f"# Job Report {job.id}")
        lines.append("")
        lines.append(f"- Name: {job.name or '-'}")
        lines.append(f"- Problem: {job.problem}")
        lines.append(f"- Mode: {job.mode.value}")
        lines.append(f"- Status: {job.status.value}")
        lines.append(f"- Phase: {job.phase.value}")
        lines.append(f"- Device Count: {len(job.devices)}")
        lines.append(f"- Command Count: {len(job.command_results)}")
        lines.append("")

        if job.rca_result:
            lines.append("## Root Cause")
            lines.append("")
            lines.append(f"- Root Device: {job.rca_result.root_device_host or '-'}")
            lines.append(f"- Confidence: {job.rca_result.confidence:.2f}")
            lines.append(f"- Summary: {job.rca_result.summary}")
            lines.append(f"- Recommendation: {job.rca_result.recommendation}")
            lines.append("")

        lines.append("## Correlation")
        lines.append("")
        lines.append(f"- Incident Count: {len(job.incidents)}")
        lines.append(f"- Cluster Count: {len(job.clusters)}")
        lines.append(f"- Causal Edge Count: {len(job.causal_edges)}")
        lines.append("")
        if job.clusters:
            lines.append("### Clusters")
            for cluster in job.clusters:
                lines.append(
                    f"- {cluster.id}: incidents={cluster.incident_count}, devices={len(cluster.device_ids)}, "
                    f"window={cluster.start_at.isoformat()} -> {cluster.end_at.isoformat()}"
                )
            lines.append("")
        if job.causal_edges:
            lines.append("### Causal Edges")
            for edge in job.causal_edges:
                lines.append(
                    f"- {edge.source_device_id} -> {edge.target_device_id} | "
                    f"kind={edge.kind} | confidence={edge.confidence:.2f} | reason={edge.reason or '-'}"
                )
            lines.append("")

        lines.append("## Action Groups")
        lines.append("")
        for item in job.action_groups:
            lines.append(
                f"- {item.title} | device={item.device_id} | status={item.status.value} | "
                f"risk={item.risk_level.value} | requires_approval={item.requires_approval}"
            )
            for command in item.commands:
                lines.append(f"  - `{command}`")
        lines.append("")

        lines.append("## Command Results")
        lines.append("")
        for command in job.command_results:
            lines.append(
                f"- step={command.step_no} device={command.device_id} status={command.status.value} "
                f"risk={command.risk_level.value} command=`{command.command}`"
            )
        lines.append("")

        lines.append("## Timeline Events")
        lines.append("")
        for event in timeline.events:
            lines.append(f"- [{event.seq_no}] {event.event_type} @ {event.created_at.isoformat()}")

        content = "\n".join(lines)
        response = JobReportResponse(filename=f"job-{job.id}.md", mime_type="text/markdown", content=content)
        if fmt == "pdf":
            response.filename = response.filename.replace(".md", ".pdf")
            response.mime_type = "application/pdf"
        return response

    async def create_api_key(self, req: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
        name = req.name.strip()
        if not name:
            raise ValueError("name is required")
        permissions = [item.strip() for item in req.permissions if str(item).strip()]
        if not permissions:
            permissions = ["*"]

        raw_key = f"na3_{secrets.token_urlsafe(30)}"
        record = ApiKeyRecord(
            name=name,
            key_prefix=raw_key[:12],
            key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
            permissions=permissions,
            expires_at=req.expires_at,
        )
        async with self._state_lock:
            self._keys[record.id] = record
            self._save_state()
        return ApiKeyCreateResponse(
            id=record.id,
            name=record.name,
            key_prefix=record.key_prefix,
            permissions=list(record.permissions),
            enabled=record.enabled,
            disabled_reason=record.disabled_reason,
            expires_at=record.expires_at,
            created_at=record.created_at,
            last_used_at=record.last_used_at,
            api_key=raw_key,
        )

    async def list_api_keys(self) -> list[ApiKeyListItem]:
        async with self._state_lock:
            rows = sorted(self._keys.values(), key=lambda item: item.created_at, reverse=True)
            return [
                ApiKeyListItem(
                    id=item.id,
                    name=item.name,
                    key_prefix=item.key_prefix,
                    permissions=list(item.permissions),
                    enabled=item.enabled,
                    disabled_reason=item.disabled_reason,
                    expires_at=item.expires_at,
                    created_at=item.created_at,
                    last_used_at=item.last_used_at,
                )
                for item in rows
            ]

    async def update_api_key(self, key_id: str, *, enabled: bool | None, disabled_reason: str | None, expires_at: datetime | None) -> ApiKeyListItem:
        async with self._state_lock:
            record = self._keys.get(key_id)
            if record is None:
                raise KeyError(key_id)
            if enabled is not None:
                record.enabled = bool(enabled)
            if disabled_reason is not None:
                record.disabled_reason = (disabled_reason or "").strip() or None
            if expires_at is not None:
                record.expires_at = expires_at
            self._save_state()
            return ApiKeyListItem(
                id=record.id,
                name=record.name,
                key_prefix=record.key_prefix,
                permissions=list(record.permissions),
                enabled=record.enabled,
                disabled_reason=record.disabled_reason,
                expires_at=record.expires_at,
                created_at=record.created_at,
                last_used_at=record.last_used_at,
            )

    async def rotate_api_key(self, key_id: str, *, name: str | None, permissions: list[str] | None, expires_at: datetime | None) -> ApiKeyCreateResponse:
        async with self._state_lock:
            old = self._keys.get(key_id)
            if old is None:
                raise KeyError(key_id)
            old.enabled = False
            old.disabled_reason = "rotated"

            raw_key = f"na3_{secrets.token_urlsafe(30)}"
            new_record = ApiKeyRecord(
                name=(name or old.name).strip() or old.name,
                key_prefix=raw_key[:12],
                key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
                permissions=[item.strip() for item in (permissions or old.permissions) if str(item).strip()] or ["*"],
                enabled=True,
                expires_at=expires_at if expires_at is not None else old.expires_at,
                rotated_from_id=old.id,
            )
            self._keys[new_record.id] = new_record
            self._save_state()

            return ApiKeyCreateResponse(
                id=new_record.id,
                name=new_record.name,
                key_prefix=new_record.key_prefix,
                permissions=list(new_record.permissions),
                enabled=new_record.enabled,
                disabled_reason=new_record.disabled_reason,
                expires_at=new_record.expires_at,
                created_at=new_record.created_at,
                last_used_at=new_record.last_used_at,
                api_key=raw_key,
            )

    async def delete_api_key(self, key_id: str) -> bool:
        async with self._state_lock:
            if key_id not in self._keys:
                return False
            self._keys.pop(key_id, None)
            self._save_state()
            return True

    async def key_count(self) -> int:
        async with self._state_lock:
            return len(self._keys)

    async def authenticate(self, raw_key: str | None) -> ApiKeyRecord:
        token = str(raw_key or "").strip()
        if not token:
            raise PermissionError("missing_api_key")
        hashed = hashlib.sha256(token.encode("utf-8")).hexdigest()

        async with self._state_lock:
            for record in self._keys.values():
                if not record.enabled:
                    continue
                if record.key_hash != hashed:
                    continue
                if record.expires_at and record.expires_at <= now_utc():
                    continue
                record.last_used_at = now_utc()
                self._save_state()
                return record
        raise PermissionError("invalid_api_key")

    def has_permission(self, actor: ApiKeyRecord, permission: str) -> bool:
        perms = {str(item).strip() for item in actor.permissions}
        return "*" in perms or permission in perms

    async def append_audit(
        self,
        *,
        actor: ApiKeyRecord | None,
        action: str,
        resource: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        entry = AuditLog(
            actor_key_id=actor.id if actor else None,
            actor_name=actor.name if actor else None,
            action=action,
            resource=resource,
            status=status,
            detail=detail,
        )
        async with self._state_lock:
            self._audit_logs.append(entry)
            if len(self._audit_logs) > 5000:
                self._audit_logs = self._audit_logs[-5000:]
            self._save_state()

    async def list_audit_logs(
        self,
        *,
        action: str | None = None,
        status: str | None = None,
        actor_key_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AuditLog]:
        async with self._state_lock:
            rows = list(self._audit_logs)
        if action:
            rows = [item for item in rows if item.action == action]
        if status:
            rows = [item for item in rows if item.status == status]
        if actor_key_id:
            rows = [item for item in rows if item.actor_key_id == actor_key_id]
        start = max(0, int(offset))
        end = start + max(1, min(5000, int(limit)))
        return rows[start:end]

    async def audit_report(
        self,
        *,
        action: str | None = None,
        status: str | None = None,
        actor_key_id: str | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        logs = await self.list_audit_logs(action=action, status=status, actor_key_id=actor_key_id, limit=5000, offset=0)
        total = len(logs)
        by_action: dict[str, int] = defaultdict(int)
        by_status: dict[str, int] = defaultdict(int)
        for item in logs:
            by_action[item.action] += 1
            by_status[item.status] += 1
        base = {
            "total": total,
            "by_action": dict(sorted(by_action.items(), key=lambda kv: kv[0])),
            "by_status": dict(sorted(by_status.items(), key=lambda kv: kv[0])),
            "latest": [item.model_dump(mode="json") for item in logs[-100:]],
        }
        fmt = (format or "json").strip().lower()
        if fmt == "json":
            return base
        if fmt == "csv":
            headers = ["ts", "actor_key_id", "actor_name", "action", "resource", "status", "detail"]
            rows = [",".join(headers)]
            for item in logs:
                row = [
                    item.ts.isoformat(),
                    str(item.actor_key_id or ""),
                    str(item.actor_name or ""),
                    str(item.action or ""),
                    str(item.resource or ""),
                    str(item.status or ""),
                    str((item.detail or "").replace(",", " ")),
                ]
                rows.append(",".join(row))
            return {
                "filename": "audit-report.csv",
                "mime_type": "text/csv",
                "content": "\n".join(rows),
                **base,
            }
        if fmt == "pdf":
            lines = ["Audit Report", ""]
            lines.append(f"Total: {total}")
            lines.append("")
            for item in logs[-200:]:
                lines.append(
                    f"{item.ts.isoformat()} | {item.status.upper()} | {item.action} | {item.resource} | actor={item.actor_name or '-'} | {item.detail or ''}"
                )
            return {
                "filename": "audit-report.pdf",
                "mime_type": "application/pdf",
                "content": "\n".join(lines),
                **base,
            }
        return base

    async def list_command_profiles(self) -> list[CommandProfile]:
        async with self._state_lock:
            return sorted(self._command_profiles.values(), key=lambda item: item.updated_at, reverse=True)
