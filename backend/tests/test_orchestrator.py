import pytest

from app.models.schemas import (
    AutomationLevel,
    CommandStatus,
    DeviceProtocol,
    DeviceTarget,
    SessionCreateRequest,
)
from app.services.orchestrator import ConversationOrchestrator
from app.services.store import InMemoryStore


class ScriptedDiagnoser:
    enabled = True

    async def propose_next_step(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        conversation_history=None,
    ):
        if iteration == 1:
            return {
                "decision": "run_command",
                "title": "高风险修复命令",
                "command": "configure terminal ; interface Gi0/0/1 ; shutdown",
                "reason": "验证高风险确认",
            }
        return {
            "decision": "final",
            "root_cause": "AI summary placeholder",
            "impact_scope": "AI impact placeholder",
            "recommendation": "AI recommendation placeholder",
            "confidence": 0.5,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


@pytest.mark.asyncio
async def test_assisted_mode_keeps_high_risk_pending_confirmation():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = ScriptedDiagnoser()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.1", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.assisted,
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "请自动修复接口故障"):
        events.append(event)

    commands = store.list_commands(session.id)
    assert any(command.status == CommandStatus.pending_confirm for command in commands)
    assert any("command_pending_confirmation" in event for event in events)


@pytest.mark.asyncio
async def test_when_llm_unavailable_returns_unavailable_summary():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser.api_key = ""

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.1", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.assisted,
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "请排查接口问题"):
        events.append(event)

    commands = store.list_commands(session.id)
    summary = store.get_summary(session.id)
    assert commands == []
    assert summary is not None
    assert "LLM 服务不可用" in summary.root_cause
    assert any("final_summary" in event for event in events)
