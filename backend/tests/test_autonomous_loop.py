from __future__ import annotations

import pytest

from app.models.schemas import AutomationLevel, DeviceProtocol, DeviceTarget, SessionCreateRequest
from app.services.orchestrator import ConversationOrchestrator
from app.services.store import InMemoryStore


class FakeAutonomousDiagnoser:
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
                "title": "接口排查",
                "command": "show ip interface brief",
                "reason": "收集接口状态",
            }
        return {
            "decision": "final",
            "root_cause": "管理员关闭了接口Ethernet1/0/6",
            "impact_scope": "接口Ethernet1/0/6相关业务中断",
            "recommendation": "确认变更后执行 undo shutdown",
            "confidence": 0.97,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class FakeAutonomousDiagnoserWithStringRefs(FakeAutonomousDiagnoser):
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
                "title": "接口排查",
                "command": "show ip interface brief",
                "reason": "收集接口状态",
            }
        return {
            "decision": "final",
            "root_cause": "管理员关闭了接口Ethernet1/0/6",
            "impact_scope": "接口Ethernet1/0/6相关业务中断",
            "recommendation": "确认变更后执行 undo shutdown",
            "confidence": 0.91,
            "evidence_refs": ["administratively down", {"command_step": 2, "quote": "shutdown"}],
        }


@pytest.mark.asyncio
async def test_autonomous_loop_bootstrap_then_ai_command_then_final_summary():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeAutonomousDiagnoser()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.88",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="test-password",
                device_type="huawei",
            ),
            automation_level=AutomationLevel.assisted,
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "端口 Ethernet 1/0/6 down了"):
        events.append(event)

    commands = store.list_commands(session.id)
    assert len(commands) == 2
    assert commands[0].command == "display version"
    assert commands[1].command == "show ip interface brief"

    summary = store.get_summary(session.id)
    assert summary is not None
    assert "管理员关闭" in summary.root_cause
    assert summary.confidence == 0.97
    assert any("final_summary" in event for event in events)


@pytest.mark.asyncio
async def test_autonomous_loop_accepts_string_evidence_refs_from_llm():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeAutonomousDiagnoserWithStringRefs()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.88",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="test-password",
                device_type="huawei",
            ),
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "端口 Ethernet 1/0/6 down了"):
        pass

    summary = store.get_summary(session.id)
    assert summary is not None
    assert isinstance(summary.evidence_refs, list)
    assert summary.evidence_refs[0]["quote"] == "administratively down"


@pytest.mark.asyncio
async def test_ai_context_is_persisted_and_appended_in_same_session():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeAutonomousDiagnoser()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.88",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="test-password",
                device_type="huawei",
            ),
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "第一次问题"):
        pass
    first_len = len(store.list_ai_context(session.id))

    async for _ in orchestrator.stream_message(session.id, "第二次问题"):
        pass
    second_len = len(store.list_ai_context(session.id))

    assert second_len > first_len
