from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models.schemas import (
    CommandPolicy,
    CommandPolicyUpdateRequest,
    ConfirmCommandRequest,
    ExportRequest,
    LLMConfigRequest,
    LLMConfigResponse,
    LLMPromptPolicyResponse,
    MessageCreateRequest,
    SessionCreateRequest,
    SessionListItem,
    SessionResponse,
    SessionUpdateRequest,
    ServiceTraceResponse,
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


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, req: MessageCreateRequest):
    if session_id not in store.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    generator = orchestrator.stream_message(session_id, req.content)
    return StreamingResponse(generator, media_type="text/event-stream")


@router.get("/command-policy", response_model=CommandPolicy)
async def get_command_policy() -> CommandPolicy:
    return store.get_command_policy()


@router.put("/command-policy", response_model=CommandPolicy)
async def update_command_policy(req: CommandPolicyUpdateRequest) -> CommandPolicy:
    return store.update_command_policy(req)


@router.post("/command-policy/reset", response_model=CommandPolicy)
async def reset_command_policy() -> CommandPolicy:
    return store.reset_command_policy()


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
