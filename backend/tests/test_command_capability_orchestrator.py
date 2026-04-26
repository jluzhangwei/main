from __future__ import annotations

import pytest

from app.models.schemas import (
    AutomationLevel,
    CommandCapabilityUpsertRequest,
    DeviceProtocol,
    DeviceTarget,
    OperationMode,
    SessionCreateRequest,
)
from app.services.orchestrator import ConversationOrchestrator
from app.services.store import InMemoryStore


class _AlwaysRunSameCommandDiagnoser:
    enabled = True

    def __init__(self, command: str):
        self.command = command

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
                "title": "命令探测",
                "command": self.command,
                "reason": "test",
            }
        return {
            "decision": "final",
            "mode": "query",
            "query_result": "done",
            "follow_up_action": "已完成",
            "confidence": 0.8,
            "evidence_refs": [],
        }

    async def diagnose(self, session, commands, evidences):
        return None


class _ErrorOutputAdapter:
    def __init__(self):
        self.last_command_meta = {}
        self.calls: list[str] = []

    async def connect(self):
        return None

    async def close(self):
        return None

    async def run_command(self, command: str):
        self.calls.append(command)
        self.last_command_meta = {
            "original_command": command,
            "translated_command": command,
            "effective_command": command,
            "retry_used": False,
            "simulated": False,
        }
        if command.strip().lower() == "show inventory":
            return "            ^\nError: Unrecognized command found at '^' position."
        return "ok"


class _PermissionErrorAdapter:
    def __init__(self):
        self.last_command_meta = {}
        self.calls: list[str] = []

    async def connect(self):
        return None

    async def close(self):
        return None

    async def run_command(self, command: str):
        self.calls.append(command)
        self.last_command_meta = {
            "original_command": command,
            "translated_command": command,
            "effective_command": command,
            "retry_used": False,
            "simulated": False,
        }
        return "% Invalid input (privileged mode required)"


class _AlwaysSyntaxErrorAdapter:
    def __init__(self):
        self.last_command_meta = {}
        self.calls: list[str] = []

    async def connect(self):
        return None

    async def close(self):
        return None

    async def run_command(self, command: str):
        self.calls.append(command)
        self.last_command_meta = {
            "original_command": command,
            "translated_command": command,
            "effective_command": command,
            "retry_used": False,
            "simulated": False,
        }
        return "        ^\nError: Unrecognized command found at '^' position."


class _RetrySuccessAdapter:
    def __init__(self):
        self.last_command_meta = {}
        self.calls: list[str] = []

    async def connect(self):
        return None

    async def close(self):
        return None

    async def run_command(self, command: str):
        self.calls.append(command)
        lowered = command.strip().lower()
        if lowered == "show inventory":
            self.last_command_meta = {
                "original_command": "show inventory",
                "translated_command": "show inventory",
                "effective_command": "display version",
                "retry_used": True,
                "retry_from": "show inventory",
                "retry_to": "display version",
                "retry_error": "Error: Unrecognized command found at '^' position.",
                "simulated": False,
            }
            return "Huawei Versatile Routing Platform Software\nDevice-102 uptime is 3 days"

        self.last_command_meta = {
            "original_command": command,
            "translated_command": command,
            "effective_command": command,
            "retry_used": False,
            "simulated": False,
        }
        return "Huawei Versatile Routing Platform Software\nDevice-102 uptime is 3 days"


@pytest.mark.asyncio
async def test_learns_block_rule_from_syntax_error_and_blocks_next_time(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.102", version_signature="huawei|ne40e|8.180")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("show inventory")

    adapter = _ErrorOutputAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )

    async for _ in orchestrator.stream_message(session.id, "第一次检查"):
        pass

    learned_rules = store.list_command_capability_rules(host="192.168.0.102")
    assert any(item.action == "block" and item.command_key == "show inventory" for item in learned_rules)
    capability_traces = [
        step
        for step in store.list_trace_steps(session.id)
        if step.step_type == "capability_decision" and step.title == "命令能力学习（block）"
    ]
    assert capability_traces
    assert all(step.status == "succeeded" for step in capability_traces)
    calls_before_second_check = list(adapter.calls)

    async for _ in orchestrator.stream_message(session.id, "第二次检查"):
        pass

    inventory_runs = [item for item in store.list_commands(session.id) if item.original_command == "show inventory"]
    assert inventory_runs
    if len(inventory_runs) >= 2:
        assert inventory_runs[-1].status.value == "blocked"
        assert inventory_runs[-1].capability_state == "block_hit"
    else:
        assert adapter.calls == calls_before_second_check
    blocked_traces = [
        step
        for step in store.list_trace_steps(session.id)
        if step.step_type == "capability_decision" and step.title == "执行前命令能力判定（阻断）"
    ]
    if blocked_traces:
        assert all(step.status == "blocked" for step in blocked_traces)


@pytest.mark.asyncio
async def test_learns_rewrite_rule_and_rewrites_before_execution(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.102", version_signature="huawei|ne40e|8.180")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("show inventory")

    adapter = _RetrySuccessAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )

    async for _ in orchestrator.stream_message(session.id, "第一次检查"):
        pass

    learned_rules = store.list_command_capability_rules(host="192.168.0.102")
    assert any(
        item.action == "rewrite"
        and item.command_key == "show inventory"
        and item.rewrite_to == "display version"
        for item in learned_rules
    )

    async for _ in orchestrator.stream_message(session.id, "第二次检查"):
        pass

    # Second pass should be rewritten before adapter call.
    assert "display version" in [item.strip().lower() for item in adapter.calls]

    rows = [item for item in store.list_commands(session.id) if item.original_command == "show inventory"]
    assert len(rows) >= 2
    assert rows[-1].command.strip().lower() == "display version"
    assert rows[-1].capability_state in {"rewrite_hit", "learned_update"}


@pytest.mark.asyncio
async def test_learned_block_rule_does_not_bleed_to_peer_device_with_same_version(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.102", version_signature="huawei|ne40e|8.180")
    store.reset_command_capability_rules(host="192.168.0.103", version_signature="huawei|ne40e|8.180")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("show inventory")

    adapter = _ErrorOutputAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session_a = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session_a.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )

    async for _ in orchestrator.stream_message(session_a.id, "第一次检查"):
        pass

    host_rules = store.list_command_capability_rules(host="192.168.0.102")
    assert any(item.action == "block" and item.command_key == "show inventory" for item in host_rules)
    version_rules = store.list_command_capability_rules(version_signature="huawei|ne40e|8.180")
    assert not any(item.action == "block" and item.command_key == "show inventory" for item in version_rules)

    session_b = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.103", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session_b.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )

    async for _ in orchestrator.stream_message(session_b.id, "第二台检查"):
        pass

    peer_rows = [item for item in store.list_commands(session_b.id) if item.original_command == "show inventory"]
    assert peer_rows
    assert peer_rows[-1].status.value != "blocked"


@pytest.mark.asyncio
async def test_permission_mode_error_is_not_learned_as_block(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.102", version_signature="arista|veos-lab|4.32.4.1m")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("show running-config | include routing")

    adapter = _PermissionErrorAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="arista",
        platform="veos-lab",
        software_version="4.32.4.1m",
        version_signature="arista|veos-lab|4.32.4.1m",
    )

    async for _ in orchestrator.stream_message(session.id, "检查一次"):
        pass

    learned_rules = store.list_command_capability_rules(version_signature="arista|veos-lab|4.32.4.1m")
    assert not any(
        item.action == "block" and item.command_key == "show running-config | include routing"
        for item in learned_rules
    )
    rows = [item for item in store.list_commands(session.id) if item.original_command == "show running-config | include routing"]
    if rows:
        assert rows[-1].capability_state == "learn_skipped"


@pytest.mark.asyncio
async def test_permission_sensitive_learned_block_rule_is_ignored(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.102", version_signature="arista|veos-lab|4.32.4.1m")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("show running-config | include routing")

    adapter = _ErrorOutputAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.query,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="arista",
        platform="veos-lab",
        software_version="4.32.4.1m",
        version_signature="arista|veos-lab|4.32.4.1m",
    )
    store.upsert_command_capability_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            protocol=DeviceProtocol.ssh,
            version_signature="arista|veos-lab|4.32.4.1m",
            command_key="show running-config | include routing",
            action="block",
            reason_code="cli_syntax_error",
            reason_text="% Invalid input (privileged mode required)",
            source="learned",
            enabled=True,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "检查一次"):
        pass

    rows = [item for item in store.list_commands(session.id) if item.original_command == "show running-config | include routing"]
    assert rows
    assert rows[-1].status.value != "blocked"
    assert rows[-1].capability_state in {"block_skip_permission", "learn_skipped", "learned_update", "rewrite_hit", None}


@pytest.mark.asyncio
async def test_mode_sensitive_error_is_not_learned_as_block(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.88", version_signature="huawei|ne40e|8.180")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("interface Ethernet1/0/6")

    adapter = _AlwaysSyntaxErrorAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.config,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )

    async for _ in orchestrator.stream_message(session.id, "打开接口"):
        pass

    learned_rules = store.list_command_capability_rules(version_signature="huawei|ne40e|8.180")
    assert not any(item.action == "block" and item.command_key == "interface ethernet1/0/6" for item in learned_rules)
    rows = [item for item in store.list_commands(session.id) if item.original_command == "interface Ethernet1/0/6"]
    if rows:
        assert rows[-1].capability_state == "learn_skipped"


@pytest.mark.asyncio
async def test_mode_sensitive_learned_block_rule_is_ignored(monkeypatch):
    store = InMemoryStore()
    store.reset_command_capability_rules(host="192.168.0.88", version_signature="huawei|ne40e|8.180")
    orchestrator = ConversationOrchestrator(store)
    orchestrator.deepseek_diagnoser = _AlwaysRunSameCommandDiagnoser("interface Ethernet1/0/6")

    adapter = _ErrorOutputAdapter()

    def _build_adapter(_session, *, allow_simulation=True):
        return adapter

    monkeypatch.setattr("app.services.orchestrator.build_adapter", _build_adapter)

    session = store.create_session(
        SessionCreateRequest(
            device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh),
            automation_level=AutomationLevel.full_auto,
            operation_mode=OperationMode.config,
        )
    )
    store.update_session_device_profile(
        session.id,
        vendor="huawei",
        platform="ne40e",
        software_version="8.180",
        version_signature="huawei|ne40e|8.180",
    )
    store.upsert_command_capability_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            protocol=DeviceProtocol.ssh,
            version_signature="huawei|ne40e|8.180",
            command_key="interface Ethernet1/0/6",
            action="block",
            reason_code="cli_syntax_error",
            reason_text="Error: Unrecognized command found at '^' position.",
            source="learned",
            enabled=True,
        )
    )

    async for _ in orchestrator.stream_message(session.id, "打开接口"):
        pass

    rows = [item for item in store.list_commands(session.id) if item.original_command == "interface Ethernet1/0/6"]
    assert rows
    assert rows[-1].status.value != "blocked"
    assert rows[-1].capability_state in {"block_skip_mode_sensitive", "learn_skipped", "learned_update", "rewrite_hit", None}
