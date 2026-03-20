import pytest

from app.models.schemas import (
    AutomationLevel,
    CommandStatus,
    DeviceProtocol,
    DeviceTarget,
    IncidentSummary,
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


class ConnectFailAdapter:
    async def connect(self):
        raise RuntimeError("connection reset by peer")

    async def run_command(self, command: str):
        return ""

    async def close(self):
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


@pytest.mark.asyncio
async def test_connection_failure_generates_final_summary_instead_of_stream_crash(monkeypatch):
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store, allow_simulation=False)
    orchestrator.deepseek_diagnoser = ScriptedDiagnoser()

    monkeypatch.setattr(
        "app.services.orchestrator.build_adapter",
        lambda session, allow_simulation=True: ConnectFailAdapter(),
    )

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.1", protocol=DeviceProtocol.ssh, vendor="huawei"),
            automation_level=AutomationLevel.assisted,
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "帮我看看设备版本"):
        events.append(event)

    summary = store.get_summary(session.id)
    assert summary is not None
    assert "设备连接或执行失败" in summary.root_cause
    assert any("command_completed" in event for event in events)
    assert any("final_summary" in event for event in events)


@pytest.mark.asyncio
async def test_connection_failure_replaces_previous_summary(monkeypatch):
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store, allow_simulation=False)
    orchestrator.deepseek_diagnoser = ScriptedDiagnoser()
    monkeypatch.setattr(
        "app.services.orchestrator.build_adapter",
        lambda session, allow_simulation=True: ConnectFailAdapter(),
    )

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.2", protocol=DeviceProtocol.ssh, vendor="huawei"),
            automation_level=AutomationLevel.assisted,
        )
    )
    store.set_summary(
        IncidentSummary(
            session_id=session.id,
            root_cause="old result",
            impact_scope="old scope",
            recommendation="old advice",
        )
    )

    async for _ in orchestrator.stream_message(session.id, "帮我看看设备版本"):
        pass

    summary = store.get_summary(session.id)
    assert summary is not None
    assert summary.root_cause != "old result"
    assert "设备连接或执行失败" in summary.root_cause
