from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ApiKeyRecord,
    AuditLog,
    CommandCapabilityResetRequest,
    CommandCapabilityResetResponse,
    CommandCapabilityRule,
    CommandCapabilityUpsertRequest,
    CommandPolicy,
    CommandPolicyUpdateRequest,
    ConfirmCommandRequest,
    ExportRequest,
    JobActionDecisionRequest,
    JobActionDecisionResponse,
    JobCreateRequest,
    JobListResponse,
    JobMode,
    JobReportResponse,
    JobResponse,
    JobStatus,
    JobTimelineResponse,
    LLMConfigRequest,
    LLMConfigResponse,
    LLMPromptPolicyResponse,
    MessageCreateRequest,
    CommandProfile,
    SessionCreateRequest,
    SessionCredentialUpdateRequest,
    SessionListItem,
    SessionResponse,
    SessionUpdateRequest,
    ServiceTraceResponse,
    SessionStopResponse,
    RiskPolicy,
    RiskPolicyUpdateRequest,
)
from app.services.exporter import export_timeline_markdown
from app.services.job_orchestrator_v2 import JobV2Orchestrator
from app.services.orchestrator import ConversationOrchestrator
from app.services.store import InMemoryStore

router = APIRouter(prefix="/v1", tags=["netops"])
router_v2 = APIRouter(prefix="/v2", tags=["netops-v2"])
store = InMemoryStore()
orchestrator = ConversationOrchestrator(
    store,
    allow_simulation=os.getenv("NETOPS_ALLOW_SIMULATION_FALLBACK", "0").strip().lower() in {"1", "true", "yes"},
)
orchestrator_v2 = JobV2Orchestrator(
    store,
    allow_simulation=os.getenv("NETOPS_ALLOW_SIMULATION_FALLBACK", "0").strip().lower() in {"1", "true", "yes"},
)


@router.post("/sessions", response_model=SessionResponse)
async def create_session(req: SessionCreateRequest) -> SessionResponse:
    session = store.create_session(req)
    return SessionResponse(
        id=session.id,
        automation_level=session.automation_level,
        operation_mode=session.operation_mode,
        status=session.status,
        created_at=session.created_at,
    )


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions() -> list[SessionListItem]:
    return store.list_session_items()


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(session_id: str, req: SessionUpdateRequest) -> SessionResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = store.update_session_automation(session_id, req.automation_level)
    return SessionResponse(
        id=session.id,
        automation_level=session.automation_level,
        operation_mode=session.operation_mode,
        status=session.status,
        created_at=session.created_at,
    )


@router.patch("/sessions/{session_id}/credentials", response_model=SessionResponse)
async def update_session_credentials(session_id: str, req: SessionCredentialUpdateRequest) -> SessionResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = store.update_session_credentials(session_id, req)
    return SessionResponse(
        id=session.id,
        automation_level=session.automation_level,
        operation_mode=session.operation_mode,
        status=session.status,
        created_at=session.created_at,
    )


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, req: MessageCreateRequest):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    generator = orchestrator.stream_message(session_id, req.content)
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/sessions/{session_id}/stop", response_model=SessionStopResponse)
async def stop_session(session_id: str) -> SessionStopResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    payload = await orchestrator.stop_session(session_id)
    return SessionStopResponse(**payload)


@router.get("/command-policy", response_model=CommandPolicy)
async def get_command_policy() -> CommandPolicy:
    return store.get_command_policy()


@router.get("/command-capability", response_model=list[CommandCapabilityRule])
async def get_command_capability(
    host: str | None = None,
    version_signature: str | None = None,
    scope_key: str | None = None,
) -> list[CommandCapabilityRule]:
    return store.list_command_capability_rules(
        host=host,
        version_signature=version_signature,
        scope_key=scope_key,
    )


@router.put("/command-capability", response_model=CommandCapabilityRule)
async def upsert_command_capability(req: CommandCapabilityUpsertRequest) -> CommandCapabilityRule:
    return store.upsert_command_capability_rule(req)


@router.delete("/command-capability/{rule_id}")
async def delete_command_capability(rule_id: str):
    deleted = store.delete_command_capability_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Command capability rule not found")
    return {"deleted": True}


@router.post("/command-capability/reset", response_model=CommandCapabilityResetResponse)
async def reset_command_capability(req: CommandCapabilityResetRequest) -> CommandCapabilityResetResponse:
    removed, remaining = store.reset_command_capability_rules(
        host=req.host,
        version_signature=req.version_signature,
    )
    return CommandCapabilityResetResponse(removed=removed, remaining=remaining)


@router.put("/command-policy", response_model=CommandPolicy)
async def update_command_policy(req: CommandPolicyUpdateRequest) -> CommandPolicy:
    return store.update_command_policy(req)


@router.post("/command-policy/reset", response_model=CommandPolicy)
async def reset_command_policy() -> CommandPolicy:
    return store.reset_command_policy()


@router.get("/risk-policy", response_model=RiskPolicy)
async def get_risk_policy() -> RiskPolicy:
    return store.get_risk_policy()


@router.put("/risk-policy", response_model=RiskPolicy)
async def update_risk_policy(req: RiskPolicyUpdateRequest) -> RiskPolicy:
    return store.update_risk_policy(req)


@router.post("/risk-policy/reset", response_model=RiskPolicy)
async def reset_risk_policy() -> RiskPolicy:
    return store.reset_risk_policy()


@router.get("/llm/status", response_model=LLMConfigResponse)
async def get_llm_status() -> LLMConfigResponse:
    status = orchestrator.deepseek_diagnoser.status()
    return LLMConfigResponse(**status)


@router.post("/llm/config", response_model=LLMConfigResponse)
async def configure_llm(req: LLMConfigRequest) -> LLMConfigResponse:
    orchestrator.deepseek_diagnoser.configure(
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
        failover_enabled=req.failover_enabled,
        model_candidates=req.model_candidates,
        batch_execution_enabled=req.batch_execution_enabled,
    )
    status = orchestrator.deepseek_diagnoser.status()
    return LLMConfigResponse(**status)


@router.delete("/llm/config", response_model=LLMConfigResponse)
async def delete_llm_config() -> LLMConfigResponse:
    orchestrator.deepseek_diagnoser.delete_saved_config()
    status = orchestrator.deepseek_diagnoser.status()
    return LLMConfigResponse(**status)


@router.get("/llm/prompt-policy", response_model=LLMPromptPolicyResponse)
async def get_llm_prompt_policy() -> LLMPromptPolicyResponse:
    payload = orchestrator.deepseek_diagnoser.prompt_strategy()
    runtime = orchestrator.prompt_runtime_policy()
    prompts = payload.get("prompts")
    if isinstance(prompts, dict):
        payload["prompts"] = {**prompts, **runtime}
    return LLMPromptPolicyResponse(**payload)


@router.post("/sessions/{session_id}/commands/{command_id}/confirm")
async def confirm_high_risk_command(session_id: str, command_id: str, req: ConfirmCommandRequest):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = await orchestrator.confirm_command(session_id, command_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result


@router.get("/sessions/{session_id}/timeline")
async def get_timeline(session_id: str):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return store.get_timeline(session_id)


@router.get("/sessions/{session_id}/trace", response_model=ServiceTraceResponse)
async def get_service_trace(session_id: str) -> ServiceTraceResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return store.get_service_trace(session_id)


@router.post("/sessions/{session_id}/export")
async def export_session(session_id: str, req: ExportRequest):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    timeline = store.get_timeline(session_id)
    markdown = export_timeline_markdown(timeline)

    if req.format == "pdf":
        markdown.filename = markdown.filename.replace(".md", ".pdf")
        markdown.mime_type = "application/pdf"

    return markdown


def _extract_api_key_token(x_api_key: str | None, authorization: str | None) -> str | None:
    raw = (x_api_key or "").strip()
    if raw:
        return raw
    auth = (authorization or "").strip()
    if not auth:
        return None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


async def _require_v2_permission(
    permission: str,
    x_api_key: str | None,
    authorization: str | None,
) -> ApiKeyRecord:
    token = _extract_api_key_token(x_api_key, authorization)
    try:
        actor = await orchestrator_v2.authenticate(token)
    except PermissionError as exc:
        await orchestrator_v2.append_audit(
            actor=None,
            action="auth.check",
            resource=f"permission:{permission}",
            status="denied",
            detail=str(exc),
        )
        raise HTTPException(status_code=401, detail="API key missing or invalid") from exc

    if not orchestrator_v2.has_permission(actor, permission):
        await orchestrator_v2.append_audit(
            actor=actor,
            action="auth.check",
            resource=f"permission:{permission}",
            status="denied",
            detail="missing permission",
        )
        raise HTTPException(status_code=403, detail=f"forbidden: missing permission '{permission}'")
    return actor


def require_v2_permission(permission: str):
    async def _dep(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> ApiKeyRecord:
        return await _require_v2_permission(permission, x_api_key, authorization)

    return _dep


@router_v2.post("/jobs", response_model=JobResponse)
async def create_job_v2(
    req: JobCreateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobResponse:
    if req.mode == JobMode.repair and not orchestrator_v2.has_permission(actor, "command.execute"):
        await orchestrator_v2.append_audit(
            actor=actor,
            action="auth.check",
            resource="permission:command.execute",
            status="denied",
            detail="repair mode requires command.execute",
        )
        raise HTTPException(status_code=403, detail="forbidden: missing permission 'command.execute' for repair mode")

    try:
        created = await orchestrator_v2.create_job(
            req,
            idempotency_key=idempotency_key,
            actor_key_id=actor.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.create",
        resource=f"job:{created.id}",
        status="ok",
    )
    return created


@router_v2.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job_v2(
    job_id: str,
    reason: str | None = Query(default=None),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> JobResponse:
    try:
        payload = await orchestrator_v2.cancel_job(job_id, reason=reason, actor_name=actor.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.cancel",
        resource=f"job:{job_id}",
        status="ok",
        detail=reason,
    )
    return payload


@router_v2.get("/jobs", response_model=list[JobResponse])
async def list_jobs_v2(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    status: JobStatus | None = Query(default=None),
    mode: JobMode | None = Query(default=None),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> list[JobResponse]:
    rows, _ = await orchestrator_v2.list_jobs(
        offset=offset,
        limit=limit,
        status=status,
        mode=mode,
    )
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.list",
        resource=f"job:*?offset={offset}&limit={limit}",
        status="ok",
    )
    return rows


@router_v2.get("/jobs/query", response_model=JobListResponse)
async def query_jobs_v2(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    status: JobStatus | None = Query(default=None),
    mode: JobMode | None = Query(default=None),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobListResponse:
    rows, total = await orchestrator_v2.list_jobs(
        offset=offset,
        limit=limit,
        status=status,
        mode=mode,
    )
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.query",
        resource=f"job:*?offset={offset}&limit={limit}",
        status="ok",
    )
    return JobListResponse(total=total, offset=offset, limit=limit, items=rows)


@router_v2.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_v2(
    job_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobResponse:
    try:
        payload = await orchestrator_v2.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.read",
        resource=f"job:{job_id}",
        status="ok",
    )
    return payload


@router_v2.get("/jobs/{job_id}/events")
async def get_job_events_v2(
    job_id: str,
    from_seq: int = Query(default=0, ge=0),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
):
    async def _stream():
        seq = int(from_seq)
        idle_ticks = 0
        terminal = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
        while True:
            try:
                events, status = await orchestrator_v2.list_events_since(job_id, from_seq=seq)
            except KeyError:
                payload = {"error": "Job not found", "job_id": job_id}
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            if events:
                for event in events:
                    seq = event.seq_no
                    blob = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"event: {event.event_type}\ndata: {blob}\n\n"
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks % 15 == 0:
                    yield "event: ping\ndata: {}\n\n"

            if status in terminal and not events:
                yield "event: completed\ndata: {}\n\n"
                return
            await asyncio.sleep(0.8)

    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.events",
        resource=f"job:{job_id}",
        status="ok",
    )
    return StreamingResponse(_stream(), media_type="text/event-stream")


@router_v2.post("/jobs/{job_id}/actions/{action_group_id}/approve", response_model=JobActionDecisionResponse)
async def approve_action_group_v2(
    job_id: str,
    action_group_id: str,
    req: JobActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> JobActionDecisionResponse:
    try:
        return await orchestrator_v2.approve_action_group(
            job_id,
            action_group_id,
            actor_key_id=actor.id,
            actor_name=actor.name,
            reason=req.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job or action group not found") from exc


@router_v2.post("/jobs/{job_id}/actions/{action_group_id}/reject", response_model=JobActionDecisionResponse)
async def reject_action_group_v2(
    job_id: str,
    action_group_id: str,
    req: JobActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> JobActionDecisionResponse:
    try:
        return await orchestrator_v2.reject_action_group(
            job_id,
            action_group_id,
            actor_key_id=actor.id,
            actor_name=actor.name,
            reason=req.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job or action group not found") from exc


@router_v2.get("/jobs/{job_id}/timeline", response_model=JobTimelineResponse)
async def get_job_timeline_v2(
    job_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobTimelineResponse:
    try:
        payload = await orchestrator_v2.get_timeline(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.timeline",
        resource=f"job:{job_id}",
        status="ok",
    )
    return payload


@router_v2.get("/jobs/{job_id}/report")
async def get_job_report_v2(
    job_id: str,
    format: str = Query(default="json"),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobReportResponse | dict:
    fmt = str(format or "json").strip().lower()
    if fmt not in {"json", "markdown", "pdf"}:
        raise HTTPException(status_code=400, detail="format must be one of: json, markdown, pdf")
    try:
        payload = await orchestrator_v2.build_report(job_id, fmt=fmt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.report",
        resource=f"job:{job_id}",
        status="ok",
        detail=f"format={fmt}",
    )
    return payload


@router_v2.post("/keys", response_model=ApiKeyCreateResponse)
async def create_api_key_v2(
    req: ApiKeyCreateRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ApiKeyCreateResponse:
    actor: ApiKeyRecord | None = None
    if await orchestrator_v2.key_count() > 0:
        actor = await _require_v2_permission("policy.write", x_api_key, authorization)

    try:
        payload = await orchestrator_v2.create_api_key(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await orchestrator_v2.append_audit(
        actor=actor,
        action="key.create",
        resource=f"key:{payload.id}",
        status="ok",
    )
    return payload


@router_v2.get("/keys", response_model=list[ApiKeyListItem])
async def list_api_keys_v2(
    actor: ApiKeyRecord = Depends(require_v2_permission("policy.write")),
) -> list[ApiKeyListItem]:
    payload = await orchestrator_v2.list_api_keys()
    await orchestrator_v2.append_audit(
        actor=actor,
        action="key.list",
        resource="key:*",
        status="ok",
    )
    return payload


@router_v2.delete("/keys/{key_id}")
async def delete_api_key_v2(
    key_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("policy.write")),
):
    deleted = await orchestrator_v2.delete_api_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="API key not found")
    await orchestrator_v2.append_audit(
        actor=actor,
        action="key.delete",
        resource=f"key:{key_id}",
        status="ok",
    )
    return {"deleted": True}


@router_v2.get("/audit/logs", response_model=list[AuditLog])
async def get_audit_logs_v2(
    actor: ApiKeyRecord = Depends(require_v2_permission("audit.read")),
) -> list[AuditLog]:
    payload = await orchestrator_v2.list_audit_logs()
    await orchestrator_v2.append_audit(
        actor=actor,
        action="audit.logs",
        resource="audit:logs",
        status="ok",
    )
    return payload


@router_v2.get("/audit/reports")
async def get_audit_report_v2(
    actor: ApiKeyRecord = Depends(require_v2_permission("audit.read")),
):
    payload = await orchestrator_v2.audit_report()
    await orchestrator_v2.append_audit(
        actor=actor,
        action="audit.report",
        resource="audit:report",
        status="ok",
    )
    return payload


@router_v2.get("/command-profiles", response_model=list[CommandProfile])
async def get_command_profiles_v2(
    actor: ApiKeyRecord = Depends(require_v2_permission("audit.read")),
) -> list[CommandProfile]:
    payload = await orchestrator_v2.list_command_profiles()
    await orchestrator_v2.append_audit(
        actor=actor,
        action="profile.list",
        resource="command_profile:*",
        status="ok",
    )
    return payload
