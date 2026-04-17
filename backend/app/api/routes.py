from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ApiKeyRecord,
    ApiKeyRotateRequest,
    ApiKeyRotateResponse,
    ApiKeyUpdateRequest,
    AuditLog,
    AuditLogExportResponse,
    AutomationLevel,
    CommandCapabilityResetRequest,
    CommandCapabilityResetResponse,
    CommandCapabilityRule,
    CommandCapabilityUpsertRequest,
    CommandPolicy,
    CommandPolicyUpdateRequest,
    ConfirmCommandRequest,
    ExportRequest,
    ExportResponse,
    JobActionDecisionRequest,
    JobActionDecisionResponse,
    JobBulkActionDecisionRequest,
    JobBulkActionDecisionResponse,
    JobCreateRequest,
    JobDeviceRequest,
    JobListResponse,
    JobMode,
    JobRCAWeightsUpdateRequest,
    JobReportResponse,
    JobResponse,
    JobStatus,
    JobTopologyUpdateRequest,
    JobTimelineResponse,
    LLMConfigRequest,
    LLMConfigResponse,
    LLMPromptPolicyResponse,
    MessageCreateRequest,
    CommandProfile,
    RunActionDecisionItem,
    RunActionDecisionRequest,
    RunActionDecisionResponse,
    RunCreateRequest,
    RunKind,
    RunListResponse,
    RunResponse,
    RunStatus,
    RunStopResponse,
    RunTimelineResponse,
    SessionCreateRequest,
    SessionCredentialUpdateRequest,
    SessionListItem,
    SessionResponse,
    SessionUpdateRequest,
    ServiceTraceResponse,
    SessionStopResponse,
    RiskPolicy,
    RiskPolicyUpdateRequest,
    DeviceTarget,
    ConfirmCommandResponse,
    CommandStatus,
    OperationMode,
    SOPArchiveResponse,
    SOPArchiveEntryResponse,
    SOPArchiveDecisionPoint,
    SOPArchiveKeyStep,
    SOPExtractFromRunRequest,
    SOPListResponse,
    SOPPublishResponse,
    SOPStatus,
    SOPUpsertRequest,
    make_id,
    now_utc,
)
from app.services.exporter import export_timeline_markdown
from app.services.llm_diagnoser import SOP_EXTRACTION_PROMPT_VERSION
from app.services.job_orchestrator_v2 import JobV2Orchestrator
from app.services.orchestrator import ConversationOrchestrator
from app.services.sop_archive import SOPArchive
from app.services.store import InMemoryStore
from app.services.unified_run_service import UnifiedRunService

router = APIRouter(prefix="/v1", tags=["netops"])
router_v2 = APIRouter(prefix="/v2", tags=["netops-v2"])
router_api = APIRouter(prefix="/api", tags=["netops-unified"])
store = InMemoryStore()
orchestrator = ConversationOrchestrator(
    store,
    allow_simulation=os.getenv("NETOPS_ALLOW_SIMULATION_FALLBACK", "0").strip().lower() in {"1", "true", "yes"},
)
orchestrator_v2 = JobV2Orchestrator(
    store,
    allow_simulation=os.getenv("NETOPS_ALLOW_SIMULATION_FALLBACK", "0").strip().lower() in {"1", "true", "yes"},
)
# Use one shared LLM diagnoser instance for single-run and multi-run pipelines.
# This keeps provider/model config and runtime health (last_error) consistent.
orchestrator_v2.llm_diagnoser = orchestrator.llm_diagnoser
sop_archive = SOPArchive()


def get_unified_runs_service() -> UnifiedRunService:
    return UnifiedRunService(store, orchestrator, orchestrator_v2, sop_archive)


def _call_with_supported_kwargs(func, **kwargs):
    filtered = {key: value for key, value in kwargs.items() if value is not None}
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(**filtered)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return func(**filtered)
    supported = {
        key: value
        for key, value in filtered.items()
        if key in signature.parameters
    }
    return func(**supported)


def _single_session_response(session_id: str) -> SessionResponse:
    session = store.get_session(session_id)
    return SessionResponse(
        id=session.id,
        automation_level=session.automation_level,
        operation_mode=session.operation_mode,
        sop_enabled=session.sop_enabled,
        status=session.status,
        created_at=session.created_at,
    )


def _patch_single_session_automation(session_id: str, automation_level: AutomationLevel) -> SessionResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    store.update_session_automation(session_id, automation_level)
    return _single_session_response(session_id)


def _patch_single_session_sop(session_id: str, sop_enabled: bool) -> SessionResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    store.update_session_sop_enabled(session_id, sop_enabled)
    return _single_session_response(session_id)


def _patch_single_session_credentials(session_id: str, req: SessionCredentialUpdateRequest) -> SessionResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    store.update_session_credentials(session_id, req)
    return _single_session_response(session_id)


async def _stop_unified_run(run_id: str, *, actor_name: str | None = None) -> RunStopResponse:
    kind, source_id = _parse_unified_run_id(run_id)
    if kind == RunKind.single:
        if source_id not in store.sessions:
            raise HTTPException(status_code=404, detail="Run not found")
        payload = await orchestrator.stop_session(source_id)
        message = str(payload.get("message") or "Stop requested")
    else:
        try:
            await orchestrator_v2.cancel_job(source_id, reason="manual-stop", actor_name=actor_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        message = "Run stop requested."
    try:
        run = await get_unified_runs_service().get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    return RunStopResponse(
        run_id=run.id,
        source_id=run.source_id,
        kind=run.kind,
        status=run.status,
        stop_requested=True,
        message=message,
    )


async def _decide_single_run_actions(source_id: str, target_ids: list[str], *, approved: bool) -> RunActionDecisionResponse:
    if source_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Run not found")
    results: list[RunActionDecisionItem] = []
    for item_id in target_ids:
        resp: ConfirmCommandResponse = await orchestrator.confirm_command(
            source_id,
            item_id,
            ConfirmCommandRequest(approved=approved),
        )
        results.append(
            RunActionDecisionItem(
                item_id=resp.command_id,
                status=resp.status.value,
                message=resp.message,
            )
        )
    return RunActionDecisionResponse(
        run_id=UnifiedRunService.single_run_id(source_id),
        total=len(target_ids),
        updated=len(results),
        skipped=max(0, len(target_ids) - len(results)),
        results=results,
    )


async def _decide_multi_run_actions(
    source_id: str,
    target_ids: list[str],
    *,
    approved: bool,
    actor: ApiKeyRecord,
    reason: str | None,
) -> RunActionDecisionResponse:
    try:
        timeline = await orchestrator_v2.get_timeline(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    pending_ids = [
        item.id
        for item in timeline.job.action_groups
        if str(item.status.value if hasattr(item.status, "value") else item.status) == "pending_approval"
    ]
    requested_ids = target_ids or pending_ids
    if approved:
        results = await orchestrator_v2.bulk_approve_action_groups(
            source_id,
            requested_ids,
            actor_key_id=actor.id,
            actor_name=actor.name,
            reason=reason,
        )
    else:
        results = await orchestrator_v2.bulk_reject_action_groups(
            source_id,
            requested_ids,
            actor_key_id=actor.id,
            actor_name=actor.name,
            reason=reason,
        )
    return RunActionDecisionResponse(
        run_id=UnifiedRunService.multi_run_id(source_id),
        total=len(requested_ids),
        updated=len(results),
        skipped=max(0, len(requested_ids) - len(results)),
        results=[
            RunActionDecisionItem(
                item_id=item.action_group_id,
                status=item.status.value,
                message=item.message,
            )
            for item in results
        ],
    )


@router.post("/sessions", response_model=SessionResponse)
async def create_session(req: SessionCreateRequest) -> SessionResponse:
    session = store.create_session(req)
    return SessionResponse(
        id=session.id,
        automation_level=session.automation_level,
        operation_mode=session.operation_mode,
        sop_enabled=session.sop_enabled,
        status=session.status,
        created_at=session.created_at,
    )


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions() -> list[SessionListItem]:
    return store.list_session_items()


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(session_id: str, req: SessionUpdateRequest) -> SessionResponse:
    if req.automation_level is not None:
        return _patch_single_session_automation(session_id, req.automation_level)
    if req.sop_enabled is not None:
        return _patch_single_session_sop(session_id, req.sop_enabled)
    raise HTTPException(status_code=400, detail="no session fields to update")


@router.patch("/sessions/{session_id}/credentials", response_model=SessionResponse)
async def update_session_credentials(session_id: str, req: SessionCredentialUpdateRequest) -> SessionResponse:
    return _patch_single_session_credentials(session_id, req)


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
    status = orchestrator.llm_diagnoser.status()
    return LLMConfigResponse(**status)


@router.post("/llm/config", response_model=LLMConfigResponse)
async def configure_llm(req: LLMConfigRequest) -> LLMConfigResponse:
    _call_with_supported_kwargs(
        orchestrator.llm_diagnoser.configure,
        provider=req.provider,
        api_key=req.api_key,
        nvidia_api_key=req.nvidia_api_key,
        base_url=req.base_url,
        nvidia_base_url=req.nvidia_base_url,
        provider_base_url=req.provider_base_url,
        model=req.model,
        failover_enabled=req.failover_enabled,
        model_candidates=req.model_candidates,
        batch_execution_enabled=req.batch_execution_enabled,
    )
    status = orchestrator.llm_diagnoser.status()
    return LLMConfigResponse(**status)


@router.delete("/llm/config", response_model=LLMConfigResponse)
async def delete_llm_config() -> LLMConfigResponse:
    orchestrator.llm_diagnoser.delete_saved_config()
    status = orchestrator.llm_diagnoser.status()
    return LLMConfigResponse(**status)


@router.get("/llm/prompt-policy", response_model=LLMPromptPolicyResponse)
async def get_llm_prompt_policy() -> LLMPromptPolicyResponse:
    payload = orchestrator.llm_diagnoser.prompt_strategy()
    runtime = orchestrator.prompt_runtime_policy()
    prompts = payload.get("prompts")
    if isinstance(prompts, dict):
        payload["prompts"] = {**prompts, **runtime}
    return LLMPromptPolicyResponse(**payload)


@router.post("/sessions/{session_id}/commands/{command_id}/confirm")
async def confirm_high_risk_command(session_id: str, command_id: str, req: ConfirmCommandRequest):
    try:
        result = await _decide_single_run_actions(session_id, [command_id], approved=req.approved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not result.results:
        raise HTTPException(status_code=404, detail="Command not found")
    return {
        "command_id": result.results[0].item_id,
        "status": result.results[0].status,
        "message": result.results[0].message,
    }


@router.get("/sessions/{session_id}/timeline")
async def get_timeline(session_id: str):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        payload = await get_unified_runs_service().get_timeline(UnifiedRunService.single_run_id(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return payload.timeline


@router.get("/sessions/{session_id}/trace", response_model=ServiceTraceResponse)
async def get_service_trace(session_id: str) -> ServiceTraceResponse:
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        payload = await get_unified_runs_service().get_timeline(UnifiedRunService.single_run_id(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return payload.service_trace


@router.post("/sessions/{session_id}/export")
async def export_session(session_id: str, req: ExportRequest):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        markdown = await get_unified_runs_service().export_timeline_markdown(
            UnifiedRunService.single_run_id(session_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

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


def _is_truthy_header(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _require_v2_permission(
    permission: str,
    x_api_key: str | None,
    authorization: str | None,
    x_internal_ui: str | None,
) -> ApiKeyRecord:
    token = _extract_api_key_token(x_api_key, authorization)
    if settings.ui_trusted_v2_bypass and _is_truthy_header(x_internal_ui):
        return ApiKeyRecord(
            id="internal-ui",
            name="internal-ui",
            key_prefix="internal-ui",
            key_hash="",
            permissions=["*"],
            enabled=True,
        )
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
        x_internal_ui: str | None = Header(default=None, alias="X-Internal-UI"),
    ) -> ApiKeyRecord:
        return await _require_v2_permission(permission, x_api_key, authorization, x_internal_ui)

    return _dep


@router_api.get("/sop-library", response_model=SOPArchiveResponse)
async def get_sop_library(
    problem: str | None = None,
    vendor: str | None = None,
    version_signature: str | None = None,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> SOPArchiveResponse:
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.list",
        resource="sop:*",
        status="ok",
    )
    return sop_archive.response(problem=problem, vendor=vendor, version_signature=version_signature)


def _normalize_text_list(values) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[\n;,|]+", values)
    seen: set[str] = set()
    result: list[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _normalize_command_templates(values) -> list[dict]:
    rows: list[dict] = []
    if isinstance(values, str):
        values = [{"vendor": "generic", "commands": re.split(r"[;\n]+", values)}]
    for item in values or []:
        if isinstance(item, str):
            item = {"vendor": "generic", "commands": re.split(r"[;\n]+", item)}
        if not isinstance(item, dict):
            continue
        vendor = str(item.get("vendor") or "generic").strip() or "generic"
        commands = _normalize_text_list(item.get("commands") or [])
        if not commands:
            continue
        rows.append({"vendor": vendor, "commands": commands})
    return rows


_SOP_SPECIFIC_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Ethernet\d+(?:/\d+){0,2}", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"XGigabitEthernet[\d/]+", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"GigabitEthernet[\d/]+", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"(?<![A-Za-z])Eth\d+(?:/\d+){1,2}", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"(?<![A-Za-z])GE[\d/]+", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"Loopback\d+", flags=re.IGNORECASE), "<环回接口>"),
    (re.compile(r"(?<![A-Za-z])Lo\d+", flags=re.IGNORECASE), "<环回接口>"),
    (re.compile(r"<if>", flags=re.IGNORECASE), "<接口>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b"), "<目标前缀>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<IP>"),
]


def _contains_specific_sop_object(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(pattern.search(text) is not None for pattern, _replacement in _SOP_SPECIFIC_TEXT_PATTERNS)


def _generalize_sop_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    result = text
    for pattern, replacement in _SOP_SPECIFIC_TEXT_PATTERNS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\s{2,}", " ", result).strip()
    return result


def _normalize_key_steps(values) -> list[dict]:
    rows: list[dict] = []
    for idx, item in enumerate(values or [], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or f"关键步骤 {idx}"
        goal = str(item.get("goal") or "").strip()
        commands = _normalize_text_list(item.get("commands") or [])
        expected_signals = _normalize_text_list(item.get("expected_signals") or item.get("expected_findings") or [])
        if not goal and not commands:
            continue
        rows.append(
            {
                "step_no": int(item.get("step_no") or idx),
                "title": _generalize_sop_text(title),
                "goal": _generalize_sop_text(goal or title),
                "commands": [_generalize_sop_text(command) for command in commands],
                "expected_signals": [_generalize_sop_text(signal) for signal in expected_signals],
            }
        )
    return rows


def _normalize_decision_points(values) -> list[dict]:
    rows: list[dict] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("signal") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        if not signal or not meaning:
            continue
        rows.append({"signal": _generalize_sop_text(signal), "meaning": _generalize_sop_text(meaning)})
    return rows


def _truncate_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _compact_json_value(value: object, *, max_string: int = 320, max_items: int = 6, depth: int = 0):
    if depth >= 2:
        return _truncate_text(value, max_string)
    if isinstance(value, dict):
        compacted: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                compacted["__truncated__"] = f"+{len(value) - max_items} keys"
                break
            compacted[str(key)] = _compact_json_value(item, max_string=max_string, max_items=max_items, depth=depth + 1)
        return compacted
    if isinstance(value, list):
        items = [
            _compact_json_value(item, max_string=max_string, max_items=max_items, depth=depth + 1)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append(f"... +{len(value) - max_items} items")
        return items
    return _truncate_text(value, max_string)


def _build_sop_extract_payload(run_timeline: RunTimelineResponse) -> dict:
    timeline = run_timeline.timeline
    trace = run_timeline.service_trace.steps or []
    relevant_trace = [
        item
        for item in trace
        if item.step_type in {
            "user_input",
            "context_snapshot",
            "ai_context_submit",
            "llm_request",
            "llm_response",
            "llm_plan",
            "policy_decision",
            "command_execution",
            "evidence_parse",
            "llm_final",
        }
    ]
    return {
        "run": {
            "id": run_timeline.run.id,
            "source_id": run_timeline.run.source_id,
            "kind": run_timeline.run.kind.value if hasattr(run_timeline.run.kind, "value") else str(run_timeline.run.kind),
            "problem": run_timeline.run.problem,
            "status": run_timeline.run.status.value if hasattr(run_timeline.run.status, "value") else str(run_timeline.run.status),
            "phase": run_timeline.run.phase,
            "automation_level": run_timeline.run.automation_level.value if hasattr(run_timeline.run.automation_level, "value") else str(run_timeline.run.automation_level),
            "operation_mode": run_timeline.run.operation_mode.value if hasattr(run_timeline.run.operation_mode, "value") else str(run_timeline.run.operation_mode),
        },
        "device": {
            "host": timeline.session.device.host,
            "name": timeline.session.device.name,
            "protocol": timeline.session.device.protocol.value if hasattr(timeline.session.device.protocol, "value") else str(timeline.session.device.protocol),
            "version_signature": timeline.session.device.version_signature,
        },
        "messages": [
            {
                "role": item.role,
                "content": _truncate_text(item.content, 600),
                "created_at": item.created_at.isoformat(),
            }
            for item in timeline.messages[-6:]
        ],
        "commands": [
            {
                "step_no": item.step_no,
                "title": item.title,
                "command": item.command,
                "status": item.status.value if hasattr(item.status, "value") else str(item.status),
                "output": _truncate_text(item.output, 800),
                "error": _truncate_text(item.error, 300),
            }
            for item in timeline.commands[-12:]
        ],
        "evidences": [
            {
                "category": item.category,
                "conclusion": _truncate_text(item.conclusion, 300),
                "raw_output": _truncate_text(item.raw_output, 800),
                "parsed_data": _compact_json_value(item.parsed_data, max_string=240, max_items=6),
            }
            for item in timeline.evidences[-10:]
        ],
        "summary": timeline.summary.model_dump(mode="json") if timeline.summary else None,
        "service_trace_stats": {
            "total_steps": len(trace),
            "included_steps": len(relevant_trace[-18:]),
        },
        "service_trace": [
            {
                "seq_no": item.seq_no,
                "step_type": item.step_type,
                "title": item.title,
                "status": item.status,
                "detail": _truncate_text(item.detail, 260),
            }
            for item in relevant_trace[-18:]
        ],
        "task": "请提炼为一个未来可复用的SOP草稿，而不是复述本次会话。",
    }


def _normalize_sop_upsert_request(parsed: dict, *, source_run_id: str) -> SOPUpsertRequest:
    specific_object_detected = False

    def _generalize_and_track(value: object) -> str:
        nonlocal specific_object_detected
        original = str(value or "").strip()
        if _contains_specific_sop_object(original):
            specific_object_detected = True
        return _generalize_sop_text(original)

    topic_name = _generalize_sop_text(parsed.get("topic_name") or parsed.get("name") or "") or f"SOP主题-{source_run_id[:8]}"
    topic_key = str(parsed.get("topic_key") or "").strip() or topic_name
    existing_review_notes = _generalize_and_track(parsed.get("review_notes") or "")
    review_notes_parts = [existing_review_notes] if existing_review_notes else []
    name = _generalize_and_track(parsed.get("name") or "") or f"SOP-{source_run_id[:8]}"
    summary = _generalize_and_track(parsed.get("summary") or "") or "由 AI 从历史会话提炼的 SOP 草稿"
    usage_hint = _generalize_and_track(parsed.get("usage_hint") or "") or "调用前请人工审核适用条件和最小命令组。"
    trigger_keywords = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("trigger_keywords") or [])]
    version_signatures = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("version_signatures") or [])]
    preconditions = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("preconditions") or [])]
    anti_conditions = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("anti_conditions") or [])]
    evidence_goals = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("evidence_goals") or [])]
    key_steps = [SOPArchiveKeyStep.model_validate(item) for item in _normalize_key_steps(parsed.get("key_steps") or [])]
    decision_points = [SOPArchiveDecisionPoint.model_validate(item) for item in _normalize_decision_points(parsed.get("decision_points") or [])]
    command_templates = [
        {
            "vendor": str(item.get("vendor") or "generic").strip() or "generic",
            "commands": [_generalize_and_track(command) for command in (item.get("commands") or [])],
        }
        for item in _normalize_command_templates(parsed.get("command_templates") or [])
    ]
    fallback_commands = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("fallback_commands") or [])]
    expected_findings = [_generalize_and_track(item) for item in _normalize_text_list(parsed.get("expected_findings") or [])]
    if specific_object_detected:
        review_notes_parts.append("系统审查提示：原始 AI 提炼结果包含单次现场对象，已在入库前做占位符泛化，请人工确认是否仍具备广泛复用意义。")
    return SOPUpsertRequest(
        topic_key=topic_key,
        topic_name=topic_name,
        name=name,
        summary=summary,
        usage_hint=usage_hint,
        trigger_keywords=trigger_keywords,
        vendor_tags=_normalize_text_list(parsed.get("vendor_tags") or []),
        version_signatures=version_signatures,
        preconditions=preconditions,
        anti_conditions=anti_conditions,
        evidence_goals=evidence_goals,
        key_steps=key_steps,
        decision_points=decision_points,
        command_templates=command_templates,
        fallback_commands=fallback_commands,
        expected_findings=expected_findings,
        source_run_ids=[source_run_id],
        generated_by_model=orchestrator.llm_diagnoser.status().get("active_model") or orchestrator.llm_diagnoser.status().get("model"),
        generated_by_prompt_version=SOP_EXTRACTION_PROMPT_VERSION,
        review_notes=" ".join(part for part in review_notes_parts if part).strip() or None,
    )


def _get_sop_or_404(sop_id: str):
    try:
        return sop_archive.get_record(sop_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SOP not found") from exc


def _resolve_run_id_for_source(source_run_id: str) -> str:
    if source_run_id in store.sessions:
        return UnifiedRunService.single_run_id(source_run_id)
    try:
        orchestrator_v2.get_job(source_run_id)
        return UnifiedRunService.multi_run_id(source_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Source run not found") from exc


@router_api.get("/sops", response_model=SOPListResponse)
async def list_sops_api(
    status: SOPStatus | None = Query(default=None),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> SOPListResponse:
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.manage.list",
        resource=f"sop:{status.value if status else '*'}",
        status="ok",
    )
    return sop_archive.list_records(status=status)


@router_api.get("/sops/{sop_id}", response_model=SOPArchiveEntryResponse)
async def get_sop_api(
    sop_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> SOPArchiveEntryResponse:
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.read",
        resource=f"sop:{sop_id}",
        status="ok",
    )
    return _get_sop_or_404(sop_id).to_archive_response()


@router_api.post("/sops/extract-from-run", response_model=SOPArchiveEntryResponse)
async def extract_sop_from_run_api(
    req: SOPExtractFromRunRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> SOPArchiveEntryResponse:
    run_id = str(req.run_id or "").strip()
    try:
        run_timeline = await get_unified_runs_service().get_timeline(run_id)
    except KeyError:
        run_timeline = await get_unified_runs_service().get_timeline(_resolve_run_id_for_source(run_id))
    source_run_id = run_timeline.run.source_id
    if sop_archive.has_source_run(source_run_id) and not req.force:
        raise HTTPException(status_code=409, detail="SOP already extracted from this run; retry with force=true to create a new draft version")
    if run_timeline.timeline.summary is None:
        raise HTTPException(status_code=400, detail="cannot extract SOP without final conclusion/summary")
    if not run_timeline.timeline.commands or not run_timeline.timeline.evidences:
        raise HTTPException(status_code=400, detail="cannot extract SOP without commands and evidences")
    draft_payload = await orchestrator.llm_diagnoser.extract_sop_draft(
        run_payload=_build_sop_extract_payload(run_timeline),
    )
    if not draft_payload:
        raise HTTPException(status_code=503, detail=orchestrator.llm_diagnoser.last_error or "LLM failed to extract SOP draft")
    normalized = _normalize_sop_upsert_request(draft_payload, source_run_id=source_run_id)
    version = sop_archive.store.next_version_for_topic(normalized.topic_key or normalized.name)
    record = sop_archive.upsert_record(
        make_id(),
        normalized,
        status=SOPStatus.draft,
        version=version,
    )
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.extract",
        resource=f"sop:{record.id}",
        status="ok",
        detail=f"run={req.run_id}",
    )
    return record.to_archive_response()


@router_api.post("/sops/{sop_id}/reextract", response_model=SOPArchiveEntryResponse)
async def reextract_sop_api(
    sop_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> SOPArchiveEntryResponse:
    current = _get_sop_or_404(sop_id)
    source_run_id = next((item for item in current.source_run_ids if item), "")
    if not source_run_id:
        raise HTTPException(status_code=400, detail="SOP has no source run to re-extract")
    run_id = _resolve_run_id_for_source(source_run_id)
    return await extract_sop_from_run_api(SOPExtractFromRunRequest(run_id=run_id, force=True), actor)


@router_api.put("/sops/{sop_id}", response_model=SOPArchiveEntryResponse)
async def update_sop_api(
    sop_id: str,
    req: SOPUpsertRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> SOPArchiveEntryResponse:
    current = _get_sop_or_404(sop_id)
    if current.status == SOPStatus.published:
        record = sop_archive.upsert_record(
            make_id(),
            req,
            status=SOPStatus.draft,
            version=current.version + 1,
        )
    else:
        record = sop_archive.upsert_record(sop_id, req, status=current.status, version=current.version)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.update",
        resource=f"sop:{record.id}",
        status="ok",
    )
    return record.to_archive_response()


@router_api.post("/sops/{sop_id}/publish", response_model=SOPPublishResponse)
async def publish_sop_api(
    sop_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> SOPPublishResponse:
    _get_sop_or_404(sop_id)
    payload = sop_archive.publish_record(sop_id)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.publish",
        resource=f"sop:{sop_id}",
        status="ok",
    )
    return payload


@router_api.post("/sops/{sop_id}/archive", response_model=SOPPublishResponse)
async def archive_sop_api(
    sop_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> SOPPublishResponse:
    _get_sop_or_404(sop_id)
    payload = sop_archive.archive_record(sop_id)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.archive",
        resource=f"sop:{sop_id}",
        status="ok",
    )
    return payload


@router_api.delete("/sops/{sop_id}")
async def delete_sop_api(
    sop_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> dict[str, bool]:
    deleted = sop_archive.delete_record(sop_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="SOP not found")
    await orchestrator_v2.append_audit(
        actor=actor,
        action="sop.delete",
        resource=f"sop:{sop_id}",
        status="ok",
    )
    return {"deleted": True}


def _operation_mode_to_job_mode(mode: OperationMode) -> JobMode:
    if mode == OperationMode.query:
        return JobMode.inspection
    if mode == OperationMode.config:
        return JobMode.repair
    return JobMode.diagnosis


def _single_pending_leader_command_ids(session_id: str) -> list[str]:
    pending = [item for item in store.list_commands(session_id) if item.status == CommandStatus.pending_confirm]
    pending.sort(key=lambda item: int(item.step_no or 0))
    results: list[str] = []
    seen_batch: set[str] = set()
    for item in pending:
        batch_id = str(item.batch_id or "").strip()
        if batch_id:
            if batch_id in seen_batch:
                continue
            seen_batch.add(batch_id)
        results.append(item.id)
    return results


def _parse_unified_run_id(run_id: str) -> tuple[RunKind, str]:
    try:
        return UnifiedRunService.parse_run_id(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


def _run_device_to_target(device: JobDeviceRequest) -> DeviceTarget:
    return DeviceTarget(
        host=device.host,
        name=device.name,
        port=device.port,
        protocol=device.protocol,
        vendor=device.vendor,
        username=device.username,
        password=device.password,
        jump_host=device.jump_host,
        jump_port=device.jump_port,
        jump_username=device.jump_username,
        jump_password=device.jump_password,
        api_token=device.api_token,
        device_type=device.device_type,
    )


async def _consume_single_run(session_id: str, problem: str) -> None:
    async for _ in orchestrator.stream_message(session_id, problem):
        pass


def _launch_single_run_task(session_id: str, problem: str):
    return asyncio.create_task(_consume_single_run(session_id, problem), name=f"api-run-single-{session_id}")


@router_api.post("/runs", response_model=RunResponse)
async def create_run_api(
    req: RunCreateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RunResponse:
    if not req.devices:
        raise HTTPException(status_code=400, detail="at least one device is required")

    if req.operation_mode == OperationMode.config and not orchestrator_v2.has_permission(actor, "command.execute"):
        await orchestrator_v2.append_audit(
            actor=actor,
            action="auth.check",
            resource="permission:command.execute",
            status="denied",
            detail="config mode requires command.execute",
        )
        raise HTTPException(status_code=403, detail="forbidden: missing permission 'command.execute' for config mode")

    if len(req.devices) == 1:
        session = store.create_session(
            SessionCreateRequest(
                device=_run_device_to_target(req.devices[0]),
                automation_level=req.automation_level,
                operation_mode=req.operation_mode,
                sop_enabled=req.sop_enabled,
                issue_scope=req.issue_scope,
            )
        )
        problem = str(req.problem or "").strip()
        if problem:
            _launch_single_run_task(session.id, problem)
        await orchestrator_v2.append_audit(
            actor=actor,
            action="run.create",
            resource=f"run:{UnifiedRunService.single_run_id(session.id)}",
            status="ok",
            detail="kind=single",
        )
        response = get_unified_runs_service().build_single_run_response(session.id)
        if problem and not str(response.problem or "").strip():
            response.problem = problem
        return response

    problem = str(req.problem or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="problem is required for multi-device runs")

    created = await orchestrator_v2.create_job(
        JobCreateRequest(
            name=req.name,
            problem=problem,
            mode=_operation_mode_to_job_mode(req.operation_mode),
            sop_enabled=req.sop_enabled,
            devices=req.devices,
            max_gap_seconds=req.max_gap_seconds,
            topology_mode=req.topology_mode,
            topology_edges=req.topology_edges,
            max_device_concurrency=req.max_device_concurrency,
            execution_policy=req.execution_policy,
            webhook_url=req.webhook_url,
            webhook_events=req.webhook_events,
        ),
        idempotency_key=idempotency_key,
        actor_key_id=actor.id,
    )
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.create",
        resource=f"run:{UnifiedRunService.multi_run_id(created.id)}",
        status="ok",
        detail="kind=multi",
    )
    return get_unified_runs_service().build_multi_run_response_from_job(created)


@router_api.post("/runs/{run_id}/messages")
async def post_run_message_api(
    run_id: str,
    req: MessageCreateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
):
    kind, source_id = _parse_unified_run_id(run_id)
    content = str(req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="message content is required")

    if kind == RunKind.single:
        if source_id not in store.sessions:
            raise HTTPException(status_code=404, detail="Run not found")
        await orchestrator_v2.append_audit(
            actor=actor,
            action="run.message",
            resource=f"run:{run_id}",
            status="ok",
        )
        generator = orchestrator.stream_message(source_id, content)
        return StreamingResponse(generator, media_type="text/event-stream")

    async with orchestrator_v2._state_lock:
        source_job = orchestrator_v2._jobs.get(source_id)
        if not source_job:
            raise HTTPException(status_code=404, detail="Run not found")

    if source_job.status not in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        raise HTTPException(
            status_code=409,
            detail="multi-device run is active; wait for completion before sending a follow-up message",
        )

    updated = await orchestrator_v2.continue_job(
        source_id,
        content,
        actor_key_id=actor.id,
    )
    resumed_run_id = UnifiedRunService.multi_run_id(updated.id)

    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.message",
        resource=f"run:{run_id}",
        status="ok",
        detail=f"multi_continue={resumed_run_id}",
    )

    async def _multi_followup_stream():
        message_payload = {
            "id": f"runmsg:user:{source_id}:{int(time.time() * 1000)}",
            "session_id": UnifiedRunService.history_session_id_for_multi(source_id),
            "role": "user",
            "content": content,
            "created_at": now_utc().isoformat(),
        }
        yield f"event: message_ack\ndata: {json.dumps({'message': message_payload}, ensure_ascii=False)}\n\n"
        payload = {
            "run_id": resumed_run_id,
            "source_run_id": run_id,
            "kind": "multi",
            "accepted": True,
            "continued": True,
        }
        yield f"event: run_resumed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        done = {"run_id": resumed_run_id, "status": "accepted"}
        yield f"event: completed\ndata: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(_multi_followup_stream(), media_type="text/event-stream")


@router_api.patch("/runs/{run_id}", response_model=RunResponse)
async def patch_run_api(
    run_id: str,
    req: SessionUpdateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> RunResponse:
    kind, source_id = _parse_unified_run_id(run_id)
    if kind == RunKind.single:
        if req.automation_level is not None:
            _patch_single_session_automation(source_id, req.automation_level)
        if req.sop_enabled is not None:
            _patch_single_session_sop(source_id, req.sop_enabled)
        if req.automation_level is None and req.sop_enabled is None:
            raise HTTPException(status_code=400, detail="no run fields to update")
    else:
        async with orchestrator_v2._state_lock:
            job = orchestrator_v2._jobs.get(source_id)
            if not job:
                raise HTTPException(status_code=404, detail="Run not found")
            if req.sop_enabled is not None:
                job.sop_enabled = bool(req.sop_enabled)
                job.updated_at = now_utc()
                orchestrator_v2._append_event_with_trace(
                    job,
                    "sop_mode_updated",
                    {"sop_enabled": job.sop_enabled},
                    step_type="session_control",
                    title="更新 SOP 模式",
                    status="succeeded",
                    detail=f"sop_enabled={str(job.sop_enabled).lower()}",
                    detail_payload={"sop_enabled": job.sop_enabled},
                )
                orchestrator_v2._save_state()
            if req.sop_enabled is None and req.automation_level is None:
                raise HTTPException(status_code=400, detail="no run fields to update")
    payload = await get_unified_runs_service().get_run(run_id)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.update",
        resource=f"run:{run_id}",
        status="ok",
        detail=(
            f"automation_level={req.automation_level.value}"
            if req.automation_level is not None
            else f"sop_enabled={str(req.sop_enabled).lower()}"
        ),
    )
    return payload


@router_api.patch("/runs/{run_id}/credentials", response_model=RunResponse)
async def patch_run_credentials_api(
    run_id: str,
    req: SessionCredentialUpdateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> RunResponse:
    kind, source_id = _parse_unified_run_id(run_id)
    if kind == RunKind.single:
        _patch_single_session_credentials(source_id, req)
    else:
        terminal = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
        async with orchestrator_v2._state_lock:
            job = orchestrator_v2._jobs.get(source_id)
            if not job:
                raise HTTPException(status_code=404, detail="Run not found")
            if job.status not in terminal:
                raise HTTPException(
                    status_code=409,
                    detail="multi-device run is active; wait for completion before patching shared credentials",
                )

            updated_fields: list[str] = []

            def _apply_if_present(field_name: str, value):
                nonlocal updated_fields
                if value is None:
                    return
                updated_fields.append(field_name)
                for device in job.devices:
                    if field_name in {"jump_port"}:
                        setattr(device, field_name, int(value))
                    else:
                        setattr(device, field_name, str(value).strip() or None)

            _apply_if_present("username", req.username)
            _apply_if_present("password", req.password)
            _apply_if_present("jump_host", req.jump_host)
            _apply_if_present("jump_port", req.jump_port)
            _apply_if_present("jump_username", req.jump_username)
            _apply_if_present("jump_password", req.jump_password)
            _apply_if_present("api_token", req.api_token)

            if not updated_fields:
                raise HTTPException(status_code=400, detail="no credential fields to update")

            job.updated_at = now_utc()
            orchestrator_v2._append_event_with_trace(
                job,
                "credentials_updated",
                {
                    "scope": "all_devices",
                    "device_count": len(job.devices),
                    "updated_fields": sorted(set(updated_fields)),
                },
                step_type="session_control",
                title="更新多设备凭据",
                status="succeeded",
                detail=(
                    f"scope=all_devices; updated={','.join(sorted(set(updated_fields)))}; "
                    f"devices={len(job.devices)}"
                ),
                detail_payload={
                    "scope": "all_devices",
                    "device_count": len(job.devices),
                    "updated_fields": sorted(set(updated_fields)),
                },
            )
            orchestrator_v2._save_state()

    payload = await get_unified_runs_service().get_run(run_id)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.credentials.update",
        resource=f"run:{run_id}",
        status="ok",
    )
    return payload


@router_api.get("/runs", response_model=RunListResponse)
async def list_runs_api(
    kind: RunKind | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> RunListResponse:
    payload = await get_unified_runs_service().list_runs(kind=kind, offset=offset, limit=limit)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.list",
        resource=f"run:*?offset={offset}&limit={limit}",
        status="ok",
        detail=f"kind={kind.value if kind else 'all'}",
    )
    return payload


@router_api.get("/runs/{run_id}", response_model=RunResponse)
async def get_run_api(
    run_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> RunResponse:
    try:
        payload = await get_unified_runs_service().get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.read",
        resource=f"run:{run_id}",
        status="ok",
    )
    return payload


@router_api.get("/runs/{run_id}/timeline", response_model=RunTimelineResponse)
async def get_run_timeline_api(
    run_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> RunTimelineResponse:
    try:
        payload = await get_unified_runs_service().get_timeline(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.timeline",
        resource=f"run:{run_id}",
        status="ok",
    )
    return payload


@router_api.get("/runs/{run_id}/trace", response_model=ServiceTraceResponse)
async def get_run_trace_api(
    run_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> ServiceTraceResponse:
    try:
        payload = await get_unified_runs_service().get_service_trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.trace",
        resource=f"run:{run_id}",
        status="ok",
    )
    return payload


@router_api.get("/runs/{run_id}/events")
async def get_run_events_api(
    run_id: str,
    from_seq: int = Query(default=0, ge=0),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
):
    async def _stream():
        seq = int(from_seq)
        idle_ticks = 0
        terminal = {RunStatus.completed, RunStatus.failed, RunStatus.cancelled}
        while True:
            try:
                rows, status = await get_unified_runs_service().list_trace_steps_since(run_id, from_seq=seq)
            except KeyError:
                payload = {"error": "Run not found", "run_id": run_id}
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            if rows:
                for step in rows:
                    seq = max(seq, int(step.seq_no or 0))
                    blob = json.dumps(step.model_dump(mode="json"), ensure_ascii=False)
                    yield f"event: trace_step\ndata: {blob}\n\n"
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks % 15 == 0:
                    yield "event: ping\ndata: {}\n\n"

            if status in terminal and not rows:
                terminal_payload = {"run_id": run_id, "status": status.value}
                yield f"event: completed\ndata: {json.dumps(terminal_payload, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.8)

    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.events",
        resource=f"run:{run_id}",
        status="ok",
    )
    return StreamingResponse(_stream(), media_type="text/event-stream")


@router_api.get("/runs/{run_id}/report")
async def get_run_report_api(
    run_id: str,
    format: str = Query(default="json"),
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobReportResponse | dict:
    fmt = str(format or "json").strip().lower()
    if fmt not in {"json", "markdown", "pdf"}:
        raise HTTPException(status_code=400, detail="format must be one of: json, markdown, pdf")
    try:
        run_timeline = await get_unified_runs_service().get_timeline(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    if fmt == "json":
        payload: JobReportResponse | dict = run_timeline.timeline.model_dump(mode="json")
    else:
        markdown = export_timeline_markdown(run_timeline.timeline)
        markdown.filename = f"run-{run_timeline.run.source_id}.{'pdf' if fmt == 'pdf' else 'md'}"
        if fmt == "pdf":
            markdown.mime_type = "application/pdf"
        payload = markdown
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.report",
        resource=f"run:{run_id}",
        status="ok",
        detail=f"format={fmt}",
    )
    return payload


@router_api.post("/runs/{run_id}/export", response_model=ExportResponse)
async def export_run_api(
    run_id: str,
    req: ExportRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> ExportResponse:
    try:
        payload = await get_unified_runs_service().export_timeline_markdown(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    if req.format == "pdf":
        payload.filename = payload.filename.replace(".md", ".pdf")
        payload.mime_type = "application/pdf"
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.export",
        resource=f"run:{run_id}",
        status="ok",
        detail=f"format={req.format}",
    )
    return payload


@router_api.post("/runs/{run_id}/stop", response_model=RunStopResponse)
async def stop_run_api(
    run_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> RunStopResponse:
    payload = await _stop_unified_run(run_id, actor_name=actor.name)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="run.stop",
        resource=f"run:{run_id}",
        status="ok",
    )
    return payload


@router_api.post("/runs/{run_id}/actions/approve", response_model=RunActionDecisionResponse)
async def approve_run_actions_api(
    run_id: str,
    req: RunActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> RunActionDecisionResponse:
    kind, source_id = _parse_unified_run_id(run_id)
    if kind == RunKind.single:
        target_ids = req.item_ids or _single_pending_leader_command_ids(source_id)
        result = await _decide_single_run_actions(source_id, target_ids, approved=True)
        result.run_id = run_id
        return result

    result = await _decide_multi_run_actions(
        source_id,
        req.item_ids or [],
        approved=True,
        actor=actor,
        reason=req.reason,
    )
    result.run_id = run_id
    return result


@router_api.post("/runs/{run_id}/actions/reject", response_model=RunActionDecisionResponse)
async def reject_run_actions_api(
    run_id: str,
    req: RunActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> RunActionDecisionResponse:
    kind, source_id = _parse_unified_run_id(run_id)
    if kind == RunKind.single:
        target_ids = req.item_ids or _single_pending_leader_command_ids(source_id)
        result = await _decide_single_run_actions(source_id, target_ids, approved=False)
        result.run_id = run_id
        return result

    result = await _decide_multi_run_actions(
        source_id,
        req.item_ids or [],
        approved=False,
        actor=actor,
        reason=req.reason,
    )
    result.run_id = run_id
    return result


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


@router_v2.post("/jobs/{job_id}/actions/approve-batch", response_model=JobBulkActionDecisionResponse)
async def approve_action_groups_batch_v2(
    job_id: str,
    req: JobBulkActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> JobBulkActionDecisionResponse:
    if not req.action_group_ids:
        raise HTTPException(status_code=400, detail="action_group_ids is required")
    results = await orchestrator_v2.bulk_approve_action_groups(
        job_id,
        req.action_group_ids,
        actor_key_id=actor.id,
        actor_name=actor.name,
        reason=req.reason,
    )
    updated = len(results)
    skipped = max(0, len(req.action_group_ids) - updated)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.action_group.approve_batch",
        resource=f"job:{job_id}",
        status="ok",
        detail=f"updated={updated},skipped={skipped}",
    )
    return JobBulkActionDecisionResponse(
        job_id=job_id,
        total=len(req.action_group_ids),
        updated=updated,
        skipped=skipped,
        results=results,
    )


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


@router_v2.post("/jobs/{job_id}/actions/reject-batch", response_model=JobBulkActionDecisionResponse)
async def reject_action_groups_batch_v2(
    job_id: str,
    req: JobBulkActionDecisionRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("command.approve")),
) -> JobBulkActionDecisionResponse:
    if not req.action_group_ids:
        raise HTTPException(status_code=400, detail="action_group_ids is required")
    results = await orchestrator_v2.bulk_reject_action_groups(
        job_id,
        req.action_group_ids,
        actor_key_id=actor.id,
        actor_name=actor.name,
        reason=req.reason,
    )
    updated = len(results)
    skipped = max(0, len(req.action_group_ids) - updated)
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.action_group.reject_batch",
        resource=f"job:{job_id}",
        status="ok",
        detail=f"updated={updated},skipped={skipped}",
    )
    return JobBulkActionDecisionResponse(
        job_id=job_id,
        total=len(req.action_group_ids),
        updated=updated,
        skipped=skipped,
        results=results,
    )


@router_v2.get("/jobs/{job_id}/timeline", response_model=JobTimelineResponse)
async def get_job_timeline_v2(
    job_id: str,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.read")),
) -> JobTimelineResponse:
    try:
        run_payload = await get_unified_runs_service().get_timeline(UnifiedRunService.multi_run_id(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.timeline",
        resource=f"job:{job_id}",
        status="ok",
    )
    return JobTimelineResponse.model_validate(run_payload.payload)


@router_v2.put("/jobs/{job_id}/topology", response_model=JobResponse)
async def update_job_topology_v2(
    job_id: str,
    req: JobTopologyUpdateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> JobResponse:
    try:
        payload = await orchestrator_v2.update_job_topology(job_id, req.edges, replace=req.replace)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.topology.update",
        resource=f"job:{job_id}",
        status="ok",
        detail=f"replace={req.replace},edges={len(req.edges)}",
    )
    return payload


@router_v2.put("/jobs/{job_id}/rca-weights", response_model=JobResponse)
async def update_job_rca_weights_v2(
    job_id: str,
    req: JobRCAWeightsUpdateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("job.write")),
) -> JobResponse:
    try:
        payload = await orchestrator_v2.update_job_rca_weights(job_id, req.rca_weights)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="job.rca_weights.update",
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
    if fmt == "json":
        try:
            payload: JobReportResponse | dict = await orchestrator_v2.build_report(job_id, fmt=fmt)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
    else:
        try:
            payload = await get_unified_runs_service().export_timeline_markdown(UnifiedRunService.multi_run_id(job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if fmt == "pdf":
            payload.filename = payload.filename.replace(".md", ".pdf")
            payload.mime_type = "application/pdf"
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
    x_internal_ui: str | None = Header(default=None, alias="X-Internal-UI"),
) -> ApiKeyCreateResponse:
    actor: ApiKeyRecord | None = None
    if await orchestrator_v2.key_count() > 0:
        actor = await _require_v2_permission("policy.write", x_api_key, authorization, x_internal_ui)

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


@router_v2.patch("/keys/{key_id}", response_model=ApiKeyListItem)
async def update_api_key_v2(
    key_id: str,
    req: ApiKeyUpdateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("policy.write")),
) -> ApiKeyListItem:
    try:
        payload = await orchestrator_v2.update_api_key(
            key_id,
            enabled=req.enabled,
            disabled_reason=req.disabled_reason,
            expires_at=req.expires_at,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="API key not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="key.update",
        resource=f"key:{key_id}",
        status="ok",
    )
    return payload


@router_v2.post("/keys/{key_id}/rotate", response_model=ApiKeyRotateResponse)
async def rotate_api_key_v2(
    key_id: str,
    req: ApiKeyRotateRequest,
    actor: ApiKeyRecord = Depends(require_v2_permission("policy.write")),
) -> ApiKeyRotateResponse:
    try:
        payload = await orchestrator_v2.rotate_api_key(
            key_id,
            name=req.name,
            permissions=req.permissions,
            expires_at=req.expires_at,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="API key not found") from exc
    await orchestrator_v2.append_audit(
        actor=actor,
        action="key.rotate",
        resource=f"key:{key_id}",
        status="ok",
        detail=f"to={payload.id}",
    )
    return ApiKeyRotateResponse(
        id=payload.id,
        name=payload.name,
        key_prefix=payload.key_prefix,
        permissions=payload.permissions,
        enabled=payload.enabled,
        disabled_reason=payload.disabled_reason,
        expires_at=payload.expires_at,
        created_at=payload.created_at,
        last_used_at=payload.last_used_at,
        api_key=payload.api_key,
        rotated_from_id=key_id,
    )


@router_v2.get("/audit/logs", response_model=list[AuditLog])
async def get_audit_logs_v2(
    action: str | None = Query(default=None),
    status: str | None = Query(default=None),
    actor_key_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    actor: ApiKeyRecord = Depends(require_v2_permission("audit.read")),
) -> list[AuditLog]:
    payload = await orchestrator_v2.list_audit_logs(
        action=action,
        status=status,
        actor_key_id=actor_key_id,
        limit=limit,
        offset=offset,
    )
    await orchestrator_v2.append_audit(
        actor=actor,
        action="audit.logs",
        resource="audit:logs",
        status="ok",
    )
    return payload


@router_v2.get("/audit/reports")
async def get_audit_report_v2(
    format: str = Query(default="json"),
    action: str | None = Query(default=None),
    status: str | None = Query(default=None),
    actor_key_id: str | None = Query(default=None),
    actor: ApiKeyRecord = Depends(require_v2_permission("audit.read")),
):
    payload = await orchestrator_v2.audit_report(
        action=action,
        status=status,
        actor_key_id=actor_key_id,
        format=format,
    )
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


@router_v2.get("/security/permission-templates")
async def get_permission_templates_v2(
    actor: ApiKeyRecord = Depends(require_v2_permission("policy.write")),
):
    templates = {
        "viewer": ["job.read"],
        "operator": ["job.read", "job.write"],
        "approver": ["job.read", "command.approve"],
        "auditor": ["job.read", "audit.read"],
        "repair_operator": ["job.read", "job.write", "command.execute"],
        "platform_admin": ["*"],
    }
    await orchestrator_v2.append_audit(
        actor=actor,
        action="security.permission_templates",
        resource="security:permission_templates",
        status="ok",
    )
    return {"templates": templates}
