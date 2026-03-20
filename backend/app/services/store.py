from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from app.models.schemas import (
    AutomationLevel,
    CommandExecution,
    DeviceProtocol,
    Evidence,
    IncidentSummary,
    Message,
    Session,
    SessionCreateRequest,
    TimelineResponse,
)


class InMemoryStore:
    """V1 store abstraction.

    Production can replace this with PostgreSQL + Redis repositories.
    """

    def __init__(self) -> None:
        self.sessions: Dict[str, Session] = {}
        self.messages: Dict[str, List[Message]] = defaultdict(list)
        self.commands: Dict[str, List[CommandExecution]] = defaultdict(list)
        self.evidences: Dict[str, List[Evidence]] = defaultdict(list)
        self.summary: Dict[str, IncidentSummary] = {}
        self.ai_context: Dict[str, List[dict[str, str]]] = defaultdict(list)

    def create_session(self, req: SessionCreateRequest) -> Session:
        device = req.device.model_copy(deep=True)
        device.device_type = self._normalize_device_type(device.vendor, device.protocol, device.device_type)
        session = Session(
            device=device,
            automation_level=req.automation_level,
            operation_mode=req.operation_mode,
            issue_scope=req.issue_scope,
        )
        self.sessions[session.id] = session
        self.ai_context[session.id] = []
        return session

    def get_session(self, session_id: str) -> Session:
        return self.sessions[session_id]

    def update_session_automation(self, session_id: str, automation_level: AutomationLevel) -> Session:
        session = self.sessions[session_id]
        session.automation_level = automation_level
        self.sessions[session_id] = session
        return session

    def add_message(self, message: Message) -> None:
        self.messages[message.session_id].append(message)

    def list_messages(self, session_id: str) -> list[Message]:
        return self.messages[session_id]

    def add_command(self, command: CommandExecution) -> None:
        self.commands[command.session_id].append(command)

    def update_command(self, command: CommandExecution) -> None:
        session_commands = self.commands[command.session_id]
        for idx, existing in enumerate(session_commands):
            if existing.id == command.id:
                session_commands[idx] = command
                return

    def get_command(self, session_id: str, command_id: str) -> CommandExecution:
        for command in self.commands[session_id]:
            if command.id == command_id:
                return command
        raise KeyError(f"Command {command_id} not found")

    def list_commands(self, session_id: str) -> list[CommandExecution]:
        return self.commands[session_id]

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidences[evidence.session_id].append(evidence)

    def list_evidence(self, session_id: str) -> list[Evidence]:
        return self.evidences[session_id]

    def set_summary(self, summary: IncidentSummary) -> None:
        self.summary[summary.session_id] = summary

    def get_summary(self, session_id: str) -> Optional[IncidentSummary]:
        return self.summary.get(session_id)

    def clear_summary(self, session_id: str) -> None:
        self.summary.pop(session_id, None)

    def get_timeline(self, session_id: str) -> TimelineResponse:
        return TimelineResponse(
            session=self.get_session(session_id),
            messages=self.list_messages(session_id),
            commands=self.list_commands(session_id),
            evidences=self.list_evidence(session_id),
            summary=self.get_summary(session_id),
        )

    def append_ai_context(self, session_id: str, role: str, content: str) -> None:
        self.ai_context[session_id].append({"role": role, "content": content})

    def list_ai_context(self, session_id: str) -> list[dict[str, str]]:
        return self.ai_context[session_id]

    def reset_ai_context(self, session_id: str) -> None:
        self.ai_context[session_id] = []

    def _normalize_device_type(self, vendor: str, protocol: DeviceProtocol, current: str) -> str:
        if protocol == DeviceProtocol.api:
            return current

        normalized_vendor = (vendor or "").strip().lower()
        normalized_current = (current or "").strip().lower()

        if "huawei" in normalized_vendor and normalized_current in {"", "cisco_ios"}:
            return "huawei"
        if "arista" in normalized_vendor and normalized_current in {"", "cisco_ios"}:
            return "arista_eos"
        if "cisco" in normalized_vendor and not normalized_current:
            return "cisco_ios"
        return current
