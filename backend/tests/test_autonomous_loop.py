from __future__ import annotations

import pytest

from app.models.schemas import (
    AutomationLevel,
    CommandPolicyUpdateRequest,
    CommandStatus,
    ConfirmCommandRequest,
    DeviceProtocol,
    DeviceTarget,
    OperationMode,
    SessionCreateRequest,
)
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


class FakeQueryDiagnoser:
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
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "设备版本为 VRP V8R11，型号 NE40E。",
            "follow_up_action": "如需详细版本信息，可继续查询补丁与启动文件。",
            "confidence": 0.96,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class FakeMisclassifiedQueryDiagnoser:
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
        return {
            "decision": "final",
            "mode": "diagnosis",
            "root_cause": "OSPF 邻居状态正常，Eth1/0/1 与 192.168.0.84 Full。",
            "impact_scope": "无异常影响。",
            "recommendation": "如需继续请查询 OSPF 路由明细。",
            "confidence": 0.95,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class CaptureContextDiagnoser:
    enabled = True

    def __init__(self):
        self.captured_history: list[dict[str, str]] = []

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
        self.captured_history = list(conversation_history or [])
        return {
            "decision": "final",
            "root_cause": "test",
            "impact_scope": "test",
            "recommendation": "test",
            "confidence": 0.5,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class FakeRepeatCommandDiagnoser:
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
                "title": "端口状态复核",
                "command": "show interfaces description",
                "reason": "确认当前状态",
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "检查完成",
            "follow_up_action": "done",
            "confidence": 0.9,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class FailThenFinalizeDiagnoser:
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
                "title": "尝试执行配置命令",
                "command": "configure terminal ; interface Gi0/0/1 ; shutdown",
                "reason": "验证失败后自我修正",
            }
        return {
            "decision": "final",
            "mode": "config",
            "query_result": "第一次命令执行失败，AI已基于错误回显完成自我判断。",
            "follow_up_action": "建议重新生成命令链后再次执行。",
            "confidence": 0.71,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class BatchCommandDiagnoser:
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
                "title": "批量采集",
                "commands": [
                    {"title": "接口摘要", "command": "show ip interface brief"},
                    "show ip route",
                ],
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "批量执行完成",
            "follow_up_action": "done",
            "confidence": 0.8,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class BatchWithPendingDiagnoser:
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
                "title": "批量执行",
                "commands": [
                    "show ip interface brief",
                    "configure terminal",
                    "show ip route",
                ],
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "should not reach this in assisted flow",
            "follow_up_action": "done",
            "confidence": 0.5,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class BatchMixedConfirmDiagnoser:
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
        if any(str(getattr(item, "command", "")).strip().lower() == "totally-unknown-command" for item in commands):
            return {
                "decision": "final",
                "mode": "query",
                "query_result": "done",
                "follow_up_action": "done",
                "confidence": 0.7,
                "evidence_refs": [],
            }
        if iteration == 1:
            return {
                "decision": "run_command",
                "title": "批量混合命令",
                "commands": [
                    "show ip interface brief",
                    "totally-unknown-command",
                    "show ip route",
                ],
            }
        return None

    async def diagnose(self, session, commands, evidences):
        return None


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
            operation_mode=OperationMode.query,
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
            operation_mode=OperationMode.query,
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
            operation_mode=OperationMode.query,
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


@pytest.mark.asyncio
async def test_ai_context_does_not_leak_between_sessions():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeAutonomousDiagnoser()

    s1 = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.88",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="test-password",
                device_type="huawei",
            ),
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.assisted,
        )
    )
    async for _ in orchestrator.stream_message(s1.id, "第一个会话的问题"):
        pass

    s2 = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.99",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="test-password",
                device_type="huawei",
            ),
            automation_level=AutomationLevel.assisted,
        )
    )
    async for _ in orchestrator.stream_message(s2.id, "第二个会话的问题"):
        pass

    context_1 = "\n".join(item["content"] for item in store.list_ai_context(s1.id))
    context_2 = "\n".join(item["content"] for item in store.list_ai_context(s2.id))
    assert "第一个会话的问题" in context_1
    assert "第一个会话的问题" not in context_2


@pytest.mark.asyncio
async def test_ai_context_redacts_credentials_before_sending_to_llm():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    capture = CaptureContextDiagnoser()
    orchestrator.deepseek_diagnoser = capture

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(
                host="192.168.0.88",
                protocol=DeviceProtocol.ssh,
                vendor="huawei",
                username="zhangwei",
                password="Huawei@123",
                device_type="huawei",
            ),
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "设备账号 zhangwei 密码 Huawei@123，帮我看版本"):
        pass

    joined = "\n".join(item.get("content", "") for item in capture.captured_history)
    assert "Huawei@123" not in joined
    assert "zhangwei" not in joined
    assert "[REDACTED]" in joined


@pytest.mark.asyncio
async def test_query_task_outputs_query_summary_and_message():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeQueryDiagnoser()

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
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "帮我检查版本"):
        pass

    summary = store.get_summary(session.id)
    assert summary is not None
    assert summary.mode == "query"
    assert "VRP" in (summary.query_result or "")

    messages = store.list_messages(session.id)
    assert messages[-1].role == "assistant"
    assert messages[-1].content.startswith("查询完成。结果:")


@pytest.mark.asyncio
async def test_query_intent_is_forced_to_query_mode_even_if_llm_returns_diagnosis_shape():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeMisclassifiedQueryDiagnoser()

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
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "查询一下 ospf peer状态"):
        pass

    summary = store.get_summary(session.id)
    assert summary is not None
    assert summary.mode == "query"
    assert "OSPF" in (summary.query_result or "")

    messages = store.list_messages(session.id)
    assert messages[-1].content.startswith("查询完成。结果:")


@pytest.mark.asyncio
async def test_recheck_request_reexecutes_previous_command_once():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeRepeatCommandDiagnoser()

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
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "检查up的端口"):
        pass
    first_count = len(store.list_commands(session.id))
    assert first_count == 2

    async for _ in orchestrator.stream_message(session.id, "再检查一次"):
        pass
    second_count = len(store.list_commands(session.id))

    assert second_count == first_count + 1
    assert store.list_commands(session.id)[-1].command == "show interfaces description"


@pytest.mark.asyncio
async def test_normal_followup_also_reexecutes_duplicate_command():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = FakeRepeatCommandDiagnoser()

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
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "检查up的端口"):
        pass
    first_count = len(store.list_commands(session.id))
    assert first_count == 2

    async for _ in orchestrator.stream_message(session.id, "继续"):
        pass
    second_count = len(store.list_commands(session.id))

    assert second_count == first_count + 1
    assert store.list_commands(session.id)[-1].command == "show interfaces description"


@pytest.mark.asyncio
async def test_failed_command_result_is_fed_back_to_ai_and_loop_continues(monkeypatch):
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store, allow_simulation=False)
    orchestrator.deepseek_diagnoser = FailThenFinalizeDiagnoser()

    call_counter = {"n": 0}

    class _Adapter:
        async def connect(self):
            return None

        async def run_command(self, command: str):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                return "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.180"
            raise RuntimeError("simulated command failure")

        async def close(self):
            return None

    monkeypatch.setattr(
        "app.services.orchestrator.build_adapter",
        lambda session, allow_simulation=True: _Adapter(),
    )

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
            operation_mode=OperationMode.config,
            automation_level=AutomationLevel.full_auto,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "执行端口配置"):
        pass

    commands = store.list_commands(session.id)
    assert len(commands) == 4
    assert commands[0].status == CommandStatus.succeeded
    assert all(item.status == CommandStatus.failed for item in commands[1:])
    assert all("simulated command failure" in (item.error or "") for item in commands[1:])

    summary = store.get_summary(session.id)
    assert summary is not None
    assert summary.mode == "config"
    assert "AI已基于错误回显完成自我判断" in (summary.query_result or "")


@pytest.mark.asyncio
async def test_batch_commands_from_single_ai_step_are_executed_in_order():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = BatchCommandDiagnoser()

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
            operation_mode=OperationMode.query,
            automation_level=AutomationLevel.full_auto,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "批量采集一下"):
        pass

    commands = store.list_commands(session.id)
    assert len(commands) == 3
    assert commands[0].command == "display version"
    assert commands[1].command == "show ip interface brief"
    assert commands[2].command == "show ip route"
    assert all(cmd.status == CommandStatus.succeeded for cmd in commands)


@pytest.mark.asyncio
async def test_batch_executes_without_confirmation_when_commands_are_whitelisted():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = BatchWithPendingDiagnoser()

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
            operation_mode=OperationMode.config,
            automation_level=AutomationLevel.assisted,
        )
    )
    store.update_command_policy(
        CommandPolicyUpdateRequest(
            executable_patterns=[
                "show ",
                "display ",
                "enable",
                "configure terminal",
            ]
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "执行批量命令"):
        events.append(event)

    commands = store.list_commands(session.id)
    assert len(commands) == 4
    assert commands[1].command == "show ip interface brief"
    assert commands[1].status == CommandStatus.succeeded
    assert commands[2].command == "configure terminal"
    assert commands[2].status == CommandStatus.succeeded
    assert commands[3].command == "show ip route"
    assert commands[3].status == CommandStatus.succeeded
    assert commands[2].batch_id == commands[3].batch_id
    assert all("command_pending_confirmation" not in event for event in events)


@pytest.mark.asyncio
async def test_batch_with_mixed_rules_requires_single_confirmation_then_executes_all():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = BatchMixedConfirmDiagnoser()

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
            operation_mode=OperationMode.config,
            automation_level=AutomationLevel.assisted,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "执行批量命令"):
        pass

    commands = store.list_commands(session.id)
    assert len(commands) == 4
    assert commands[0].status == CommandStatus.succeeded
    assert commands[1].status == CommandStatus.succeeded
    assert all(item.status == CommandStatus.pending_confirm for item in commands[2:])
    assert len({item.batch_id for item in commands[2:]}) == 1

    approved = await orchestrator.confirm_command(session.id, commands[2].id, ConfirmCommandRequest(approved=True))
    assert approved.status == CommandStatus.succeeded

    commands_after = store.list_commands(session.id)
    assert all(item.status == CommandStatus.succeeded for item in commands_after[1:])
