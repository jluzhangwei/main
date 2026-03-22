from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models.schemas import (
    CommandCapabilityResetRequest,
    CommandCapabilityResetResponse,
    CommandCapabilityRule,
    CommandCapabilityUpsertRequest,
    CommandPolicy,
    CommandPolicyUpdateRequest,
    ConfirmCommandRequest,
    ExportRequest,
    LLMConfigRequest,
    LLMConfigResponse,
    LLMPromptPolicyResponse,
    MessageCreateRequest,
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
from app.services.orchestrator import ConversationOrchestrator
from app.services.store import InMemoryStore

router = APIRouter(prefix="/v1", tags=["netops"])
store = InMemoryStore()
orchestrator = ConversationOrchestrator(
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
