import pytest

from app.models.schemas import (
    AutomationLevel,
    CommandExecution,
    CommandPolicyUpdateRequest,
    CommandStatus,
    ConfirmCommandRequest,
    DeviceProtocol,
    DeviceTarget,
    IncidentSummary,
    RiskLevel,
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


class UnknownCommandDiagnoser:
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
                "title": "未知命令",
                "command": "totally-unknown-command",
                "reason": "验证白名单确认逻辑",
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "done",
            "follow_up_action": "done",
            "confidence": 0.5,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class _BatchDisabledDiagnoser:
    enabled = True
    batch_execution_enabled = False

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
                "title": "查询链路状态",
                "commands": [
                    {"title": "检查版本", "command": "show version"},
                    {"title": "检查接口摘要", "command": "show interfaces status"},
                    {"title": "检查路由摘要", "command": "show ip route summary"},
                ],
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "done",
            "follow_up_action": "done",
            "confidence": 0.9,
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


class _StatefulSessionAdapter:
    def __init__(self):
        self.connected = False
        self.privileged = False
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self):
        self.connect_calls += 1
        self.connected = True

    async def run_command(self, command: str):
        normalized = command.strip().lower()
        if normalized == "enable":
            self.privileged = True
            return "enable\nArista-EOS-1#"
        if normalized == "show privilege":
            return "Current privilege level is 15" if self.privileged else "Current privilege level is 1"
        if "configure terminal" in normalized:
            if not self.privileged:
                raise RuntimeError("Failed to enter configuration mode.")
            return "Enter configuration commands, one per line. End with CNTL/Z."
        return "ok"

    async def close(self):
        self.close_calls += 1
        self.connected = False


class _StatefulConfigDiagnoser:
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
            return {"decision": "run_command", "title": "提权", "command": "enable"}
        if iteration == 2:
            return {"decision": "run_command", "title": "检查特权", "command": "show privilege"}
        if iteration == 3:
            return {"decision": "run_command", "title": "进入配置", "command": "configure terminal"}
        return {
            "decision": "final",
            "mode": "config",
            "query_result": "ok",
            "follow_up_action": "done",
            "confidence": 0.8,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class _StatefulPendingConfirmDiagnoser:
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
            return {"decision": "run_command", "title": "提权", "command": "enable"}
        if iteration == 2:
            return {"decision": "run_command", "title": "进入配置", "command": "configure terminal"}
        return {
            "decision": "final",
            "mode": "config",
            "query_result": "ok",
            "follow_up_action": "done",
            "confidence": 0.8,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


@pytest.mark.asyncio
async def test_assisted_mode_executes_whitelisted_high_risk_without_confirmation():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = ScriptedDiagnoser()
    store.update_command_policy(
        CommandPolicyUpdateRequest(
            executable_patterns=[
                "show ",
                "display ",
                "enable",
                "configure terminal",
                "interface ",
                "shutdown",
            ],
        )
    )

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
    assert all(command.status == CommandStatus.succeeded for command in commands)
    assert all("command_pending_confirmation" not in event for event in events)


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


def test_huawei_vendor_defaults_to_huawei_device_type():
    store = InMemoryStore()
    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.9", protocol=DeviceProtocol.ssh, vendor="huawei"),
            automation_level=AutomationLevel.assisted,
        )
    )
    assert session.device.device_type == "huawei"


def test_default_vendor_and_device_type_are_neutral_for_autodetect():
    store = InMemoryStore()
    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.19", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.assisted,
        )
    )
    assert session.device.vendor == "unknown"
    assert session.device.device_type == "autodetect"


@pytest.mark.asyncio
async def test_unknown_command_executes_without_confirmation_in_full_auto_mode():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = UnknownCommandDiagnoser()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.10", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
        )
    )

    events = []
    async for event in orchestrator.stream_message(session.id, "执行一个未知命令"):
        events.append(event)

    commands = store.list_commands(session.id)
    assert any(command.command == "totally-unknown-command" and command.status == CommandStatus.succeeded for command in commands)
    assert all("command_pending_confirmation" not in event for event in events)


@pytest.mark.asyncio
async def test_batch_disabled_mode_executes_multiple_commands_without_batch_grouping():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _BatchDisabledDiagnoser()

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.30", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "查询设备状态"):
        pass

    commands = store.list_commands(session.id)
    planned = [cmd for cmd in commands if cmd.title in {"检查版本", "检查接口摘要", "检查路由摘要"}]
    assert len(planned) == 3
    assert all(cmd.batch_id is None for cmd in planned)

    trace_steps = store.list_trace_steps(session.id)
    assert any(step.step_type == "policy_decision" for step in trace_steps)
    assert any(step.step_type == "plan_decision" for step in trace_steps)


@pytest.mark.asyncio
async def test_reuses_same_adapter_so_enable_state_survives_following_commands(monkeypatch):
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _StatefulConfigDiagnoser()

    created_adapters: list[_StatefulSessionAdapter] = []

    def _build_stateful_adapter(session, allow_simulation=True):
        adapter = _StatefulSessionAdapter()
        created_adapters.append(adapter)
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_stateful_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.11", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "进入配置模式"):
        pass

    commands = store.list_commands(session.id)
    assert any(cmd.command == "enable" and cmd.status == CommandStatus.succeeded for cmd in commands)
    privilege_checks = [cmd for cmd in commands if cmd.command == "show privilege"]
    assert privilege_checks and "15" in (privilege_checks[0].output or "")
    assert any(cmd.command == "configure terminal" and cmd.status == CommandStatus.succeeded for cmd in commands)
    assert len(created_adapters) == 1


@pytest.mark.asyncio
async def test_confirm_command_uses_same_adapter_context_after_enable(monkeypatch):
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _StatefulPendingConfirmDiagnoser()

    created_adapters: list[_StatefulSessionAdapter] = []

    def _build_stateful_adapter(session, allow_simulation=True):
        adapter = _StatefulSessionAdapter()
        created_adapters.append(adapter)
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_stateful_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.12", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.assisted,
        )
    )
    store.update_command_policy(
        CommandPolicyUpdateRequest(
            executable_patterns=["show ", "display ", "enable"],
        )
    )

    async for _ in orchestrator.stream_message(session.id, "进入配置模式"):
        pass

    pending = [cmd for cmd in store.list_commands(session.id) if cmd.status == CommandStatus.pending_confirm]
    assert len(pending) == 1
    assert pending[0].command == "configure terminal"

    result = await orchestrator.confirm_command(session.id, pending[0].id, ConfirmCommandRequest(approved=True))
    assert result.status == CommandStatus.succeeded
    assert len(created_adapters) == 1


def test_append_command_result_includes_permission_hint_for_low_privilege():
    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="10.0.0.13", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.assisted,
        )
    )

    command = CommandExecution(
        session_id=session.id,
        step_no=1,
        title="检查权限",
        command="show privilege",
        adapter_type=DeviceProtocol.ssh,
        risk_level=RiskLevel.low,
        status=CommandStatus.succeeded,
        output="Current privilege level is 1",
    )

    orchestrator._append_command_result_to_ai_context(session.id, command)
    latest = store.list_ai_context(session.id)[-1]["content"]
    assert "permission_state: insufficient(level=1)" in latest
    assert "权限不足" in latest
