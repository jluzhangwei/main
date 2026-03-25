from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
    Session,
    TopologyMode,
    make_id,
    now_utc,
)
from app.services.adapters import build_adapter
from app.services.parsers import parse_command_output
from app.services.risk_engine import RiskEngine
from app.services.store import InMemoryStore


class JobV2Orchestrator:
    """Multi-device asynchronous job orchestrator for /v2 APIs."""

    def __init__(self, store: InMemoryStore, *, allow_simulation: bool = True):
        self.store = store
        self.allow_simulation = allow_simulation
        self.risk_engine = RiskEngine()

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
            self._append_event(
                job,
                "job_created",
                {
                    "job_id": job.id,
                    "device_count": len(job.devices),
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
            self._append_event(
                job,
                "topology_updated",
                {
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
            self._append_event(
                job,
                "rca_weights_updated",
                {
                    "weights": weights.model_dump(mode="json"),
                },
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
                self._append_event(
                    job,
                    "job_cancelled",
                    {
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
            self._append_event(
                job,
                "action_group_approved",
                {
                    "action_group_id": action.id,
                    "device_id": action.device_id,
                    "approved_by": actor_name,
                },
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
            self._append_event(
                job,
                "action_group_rejected",
                {
                    "action_group_id": action.id,
                    "device_id": action.device_id,
                    "rejected_by": actor_name,
                },
            )

            if all(item.status != JobActionGroupStatus.pending_approval for item in job.action_groups):
                if all(item.status in {JobActionGroupStatus.rejected, JobActionGroupStatus.succeeded, JobActionGroupStatus.failed} for item in job.action_groups):
                    job.phase = JobPhase.conclude
                    job.status = JobStatus.completed
                    if not job.completed_at:
                        job.completed_at = now_utc()
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
                self._append_event(job, "phase_changed", {"phase": job.phase.value, "status": job.status.value})
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
                    self._append_event(job, "job_cancelled", {"reason": "task_cancelled"})
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
                self._append_event(job, "job_failed", {"error": str(exc)[:500]})
                self._save_state()
            await self._close_job_adapters(job_id)

    async def _collect_phase(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            job.phase = JobPhase.collect
            job.status = JobStatus.running
            job.updated_at = now_utc()
            self._append_event(job, "phase_changed", {"phase": "collect", "status": job.status.value})
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
            self._append_event(job, "device_collect_started", {"device_id": device.id, "host": device.host})
            self._save_state()

        try:
            adapter = await self._get_adapter(job_id, device_id)
            for step_no, (title, command_text) in enumerate(self._baseline_collect_commands(), start=1):
                await self._run_device_command(
                    job_id,
                    device_id,
                    title=title,
                    command_text=command_text,
                    step_no=step_no,
                    action_group_id=None,
                )

            async with self._state_lock:
                job = self._jobs[job_id]
                device = self._find_device(job, device_id)
                if device:
                    device.status = "collected"
                    device.last_error = None
                self._append_event(job, "device_collect_completed", {"device_id": device_id})
                self._save_state()
        except Exception as exc:
            async with self._state_lock:
                job = self._jobs[job_id]
                device = self._find_device(job, device_id)
                if device:
                    device.status = "failed"
                    device.last_error = str(exc)
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

    async def _get_adapter(self, job_id: str, device_id: str):
        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                raise RuntimeError(f"device not found: {device_id}")
            existing = self._adapters[job_id].get(device_id)

        if existing is not None:
            await existing.connect()
            return existing

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
        adapter = build_adapter(session, allow_simulation=self.allow_simulation)
        await adapter.connect()

        async with self._state_lock:
            self._adapters[job_id][device_id] = adapter
        return adapter

    async def _close_job_adapters(self, job_id: str) -> None:
        rows = self._adapters.pop(job_id, {})
        for adapter in rows.values():
            try:
                await adapter.close()
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
    ) -> JobCommandResult:
        adapter = await self._get_adapter(job_id, device_id)

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
                    self._append_event(
                        job,
                        "capability_decision",
                        {
                            "device_id": device_id,
                            "command_id": command.id,
                            "decision": "block_hit",
                            "rule_id": rule.id,
                            "command": command.command,
                            "reason": command.error,
                        },
                    )
                    self._touch_command_profile(device.version_signature, command_text, success=False, error=command.error)
                    self._save_state()
                    return command
                if rule.action == "rewrite" and rule.rewrite_to:
                    command_to_run = rule.rewrite_to
                    capability_state = "rewrite"
                    capability_reason = rule.reason_text or "rewritten by capability rule"
                    self._append_event(
                        job,
                        "capability_decision",
                        {
                            "device_id": device_id,
                            "command_id": command.id,
                            "decision": "rewrite_hit",
                            "rule_id": rule.id,
                            "from": command.command,
                            "to": command_to_run,
                        },
                    )

        try:
            output = await adapter.run_command(command_to_run)
            finished_at = now_utc()
        except Exception as exc:
            async with self._state_lock:
                job = self._jobs[job_id]
                device = self._find_device(job, device_id)
                command.status = JobCommandStatus.failed
                command.error = str(exc)
                command.effective_command = command_to_run
                command.capability_state = capability_state
                command.capability_reason = capability_reason
                command.completed_at = finished_at if "finished_at" in locals() else now_utc()
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
                self._touch_command_profile(device.version_signature, command_text, success=False, error=command.error)
                self._save_state()
            return command

        category, parsed_data, conclusion = parse_command_output(command_to_run, output)

        async with self._state_lock:
            job = self._jobs[job_id]
            device = self._find_device(job, device_id)
            if not device:
                raise RuntimeError(f"device not found: {device_id}")

            evidence = JobEvidence(
                job_id=job_id,
                device_id=device_id,
                command_id=command.id,
                category=category,
                raw_output=output,
                parsed_data=parsed_data,
                conclusion=conclusion,
            )
            job.evidences.append(evidence)

            command.status = JobCommandStatus.succeeded
            command.output = output
            command.effective_command = command_to_run
            command.capability_state = capability_state
            command.capability_reason = capability_reason
            command.completed_at = finished_at
            command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000)) if command.started_at else 0

            self._apply_device_profile_from_parsed(device, parsed_data)
            self._apply_incidents_from_evidence(job, device, evidence)
            self._collect_topology_hints(job, device, command_to_run, output)

            self._learn_capability_from_command(job, device, command, category)
            self._touch_command_profile(device.version_signature, command_text, success=True, error=None, rewritten=bool(capability_state == "rewrite"))

            self._append_event(
                job,
                "command_completed",
                {
                    "command_id": command.id,
                    "device_id": device_id,
                    "status": command.status.value,
                    "category": category,
                    "conclusion": conclusion[:220],
                },
            )
            self._save_state()

        return command

    def _apply_device_profile_from_parsed(self, device: JobDevice, parsed_data: dict[str, Any]) -> None:
        if not isinstance(parsed_data, dict):
            return
        name = str(parsed_data.get("device_name") or "").strip()
        vendor = str(parsed_data.get("vendor") or "").strip()
        platform = str(parsed_data.get("platform") or "").strip()
        version = str(parsed_data.get("software_version") or "").strip()
        signature = str(parsed_data.get("version_signature") or "").strip().lower()
        if name:
            device.name = name
        if vendor:
            device.vendor = vendor
        if platform:
            device.platform = platform
        if version:
            device.software_version = version
        if signature:
            device.version_signature = signature

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
                self._append_event(
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
                self._append_event(
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
            self._append_event(job, "phase_changed", {"phase": "correlate", "status": job.status.value})

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
            self._append_event(
                job,
                "correlate_completed",
                {
                    "cluster_count": len(job.clusters),
                    "incident_count": len(job.incidents),
                    "root_device_id": job.rca_result.root_device_id if job.rca_result else None,
                },
            )
            self._save_state()

    def _resolve_causal_graph_and_root(self, job: Job) -> None:
        if not job.incidents:
            job.rca_result = RCAResult(
                job_id=job.id,
                summary="No incident evidence captured within the selected window.",
                recommendation="Expand time window or add more devices and rerun collection.",
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
        for command in job.command_results:
            if command.status in {JobCommandStatus.failed, JobCommandStatus.blocked, JobCommandStatus.rejected}:
                continue
            if command.risk_level in {RiskLevel.medium, RiskLevel.high}:
                change_counts[command.device_id] += 1
        for device_id, rows in incidents_by_device.items():
            anomaly_score = sum(self._severity_weight(item.severity) for item in rows)
            early_bonus = max(0.0, 2.0 - float(order_rank.get(device_id, 0)) * 0.3)
            upstream_bonus = float(outdegree.get(device_id, 0)) * 0.8 + float(propagation.get(device_id, 0)) * 1.2
            change_bonus = min(3.0, float(change_counts.get(device_id, 0)) * 0.5)
            consistency = max(
                0.0,
                float(len(rows)) - float(len([item for item in rows if item.category == "command_error"])) * 0.4,
            )
            score_map[device_id] = (
                weights.anomaly * anomaly_score
                + weights.timing * early_bonus
                + weights.topology * upstream_bonus
                + weights.change * change_bonus
                + weights.consistency * consistency
            )

        if not score_map:
            job.rca_result = RCAResult(
                job_id=job.id,
                summary="Unable to score root cause due to insufficient clustered incidents.",
                recommendation="Collect additional protocol and interface evidence.",
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
        summary = (
            f"Root cause candidate is {root_device.host if root_device else root_device_id}; "
            f"correlated {len(job.incidents)} incidents across {len(scoped_device_ids)} devices within {job.max_gap_seconds}s window."
        )
        recommendation = "Prioritize remediation on the root candidate first, then validate downstream devices along causal edges."

        score_breakdown = {
            str(device_id): float(score)
            for device_id, score in sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        }

        job.rca_result = RCAResult(
            job_id=job.id,
            root_device_id=root_device_id,
            root_device_name=root_device.name if root_device else None,
            root_device_host=root_device.host if root_device else None,
            confidence=confidence,
            score_breakdown=score_breakdown,
            impacted_device_ids=impacted,
            causal_edges=edges,
            summary=summary,
            recommendation=recommendation,
        )

    def _resolve_device_ref(self, value: str, by_host: dict[str, JobDevice], by_name: dict[str, JobDevice]) -> Optional[JobDevice]:
        key = str(value or "").strip().lower()
        if not key:
            return None
        if key in by_host:
            return by_host[key]
        if key in by_name:
            return by_name[key]
        return None

    async def _plan_phase(self, job_id: str) -> None:
        async with self._state_lock:
            job = self._jobs[job_id]
            job.phase = JobPhase.plan
            job.updated_at = now_utc()
            self._append_event(job, "phase_changed", {"phase": "plan", "status": job.status.value})

            if job.mode != JobMode.repair:
                self._append_event(job, "plan_completed", {"action_group_count": 0})
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
            self._append_event(
                job,
                "plan_completed",
                {
                    "action_group_count": len(action_groups),
                    "pending_approval": len([item for item in action_groups if item.status == JobActionGroupStatus.pending_approval]),
                    "auto_approved": len([item for item in action_groups if item.status == JobActionGroupStatus.approved]),
                },
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
                self._append_event(job, "job_completed", {"mode": job.mode.value})
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
                self._append_event(job, "job_completed", {"mode": job.mode.value, "message": "no approved action groups"})
                self._save_state()
                await self._close_job_adapters(job_id)
                return

            job.phase = JobPhase.execute
            job.status = JobStatus.executing
            job.updated_at = now_utc()
            self._append_event(job, "phase_changed", {"phase": "execute", "status": job.status.value})
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
                self._save_state()
                return

            job.phase = JobPhase.analyze
            job.updated_at = now_utc()
            self._append_event(job, "phase_changed", {"phase": "analyze", "status": job.status.value})
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
            self._append_event(job, "job_completed", {"mode": job.mode.value})
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
            self._append_event(
                job,
                "action_group_started",
                {
                    "action_group_id": action.id,
                    "device_id": device_id,
                    "command_count": len(commands),
                },
            )
            self._save_state()

        failed = False
        rollback_needed = False
        for idx, command_text in enumerate(commands, start=1):
            async with self._state_lock:
                job = self._jobs.get(job_id)
                if job is None or job.status == JobStatus.cancelled:
                    return
            result = await self._run_device_command(
                job_id,
                device_id,
                title=f"执行修复命令 {idx}",
                command_text=command_text,
                step_no=idx,
                action_group_id=action_group_id,
            )
            if result.status in {JobCommandStatus.failed, JobCommandStatus.blocked, JobCommandStatus.rejected}:
                failed = True
                if job.execution_policy == "continue_on_failure":
                    continue
                if job.execution_policy == "rollback_template":
                    rollback_needed = True
                    break
                break

        if failed and rollback_needed and action.rollback_commands:
            for ridx, rollback in enumerate(action.rollback_commands, start=1):
                await self._run_device_command(
                    job_id,
                    device_id,
                    title=f"执行回滚命令 {ridx}",
                    command_text=rollback,
                    step_no=1000 + ridx,
                    action_group_id=action_group_id,
                )

        async with self._state_lock:
            job = self._jobs[job_id]
            action = next((item for item in job.action_groups if item.id == action_group_id), None)
            if action is None:
                return
            action.status = JobActionGroupStatus.failed if failed else JobActionGroupStatus.succeeded
            action.updated_at = now_utc()
            self._append_event(
                job,
                "action_group_completed",
                {
                    "action_group_id": action.id,
                    "status": action.status.value,
                },
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
