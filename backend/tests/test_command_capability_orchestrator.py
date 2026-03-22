from __future__ import annotations

import pytest

from app.models.schemas import (
    AutomationLevel,
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

    async for _ in orchestrator.stream_message(session.id, "第二次检查"):
        pass

    inventory_runs = [item for item in store.list_commands(session.id) if item.original_command == "show inventory"]
    assert len(inventory_runs) >= 2
    assert inventory_runs[-1].status.value == "blocked"
    assert inventory_runs[-1].capability_state == "block_hit"


@pytest.mark.asyncio
async def test_learns_rewrite_rule_and_rewrites_before_execution(monkeypatch):
    store = InMemoryStore()
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
