from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from app.models.schemas import (
    AutomationLevel,
    CommandPolicy,
    CommandPolicyUpdateRequest,
    CommandExecution,
    DeviceProtocol,
    Evidence,
    IncidentSummary,
    Message,
    Session,
    SessionCreateRequest,
    SessionListItem,
    ServiceTraceResponse,
    ServiceTraceStep,
    TimelineResponse,
)
from app.services.command_policy import default_command_policy


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
        self.command_policy: CommandPolicy = default_command_policy()
        self.trace_steps: Dict[str, List[ServiceTraceStep]] = defaultdict(list)

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
        self.trace_steps[session.id] = []
        return session

    def get_session(self, session_id: str) -> Session:
        return self.sessions[session_id]

    def list_sessions(self) -> list[Session]:
        return sorted(
            self.sessions.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def list_session_items(self) -> list[SessionListItem]:
        return [
            SessionListItem(
                id=session.id,
                host=session.device.host,
                protocol=session.device.protocol,
                automation_level=session.automation_level,
                operation_mode=session.operation_mode,
                status=session.status,
                created_at=session.created_at,
            )
            for session in self.list_sessions()
        ]

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

    def next_trace_seq(self, session_id: str) -> int:
        return len(self.trace_steps[session_id]) + 1

    def add_trace_step(self, step: ServiceTraceStep) -> None:
        self.trace_steps[step.session_id].append(step)

    def update_trace_step(self, step: ServiceTraceStep) -> None:
        session_steps = self.trace_steps[step.session_id]
        for idx, existing in enumerate(session_steps):
            if existing.id == step.id:
                session_steps[idx] = step
                return

    def list_trace_steps(self, session_id: str) -> list[ServiceTraceStep]:
        return self.trace_steps[session_id]

    def get_service_trace(self, session_id: str) -> ServiceTraceResponse:
        return ServiceTraceResponse(
            session_id=session_id,
            steps=self.list_trace_steps(session_id),
        )

    def get_command_policy(self) -> CommandPolicy:
        return self.command_policy.model_copy(deep=True)

    def update_command_policy(self, req: CommandPolicyUpdateRequest) -> CommandPolicy:
        if req.blocked_patterns is not None:
            self.command_policy.blocked_patterns = self._normalize_patterns(req.blocked_patterns)
        if req.executable_patterns is not None:
            self.command_policy.executable_patterns = self._normalize_patterns(req.executable_patterns)
        if req.legality_check_enabled is not None:
            self.command_policy.legality_check_enabled = bool(req.legality_check_enabled)
        return self.get_command_policy()

    def reset_command_policy(self) -> CommandPolicy:
        self.command_policy = default_command_policy()
        return self.get_command_policy()

    def _normalize_device_type(self, vendor: str, protocol: DeviceProtocol, current: str) -> str:
        if protocol == DeviceProtocol.api:
            return current

        normalized_vendor = (vendor or "").strip().lower()
        normalized_current = (current or "").strip().lower()

        if "huawei" in normalized_vendor and normalized_current in {"", "autodetect", "cisco_ios"}:
            return "huawei"
        if "arista" in normalized_vendor and normalized_current in {"", "autodetect", "cisco_ios"}:
            return "arista_eos"
        if "cisco" in normalized_vendor and normalized_current in {"", "autodetect"}:
            return "cisco_ios"
        return current

    def _normalize_patterns(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
        return normalized
