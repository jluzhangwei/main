from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import tempfile
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
    SessionCredentialUpdateRequest,
    SessionListItem,
    ServiceTraceResponse,
    ServiceTraceStep,
    TimelineResponse,
    RiskPolicy,
    RiskPolicyUpdateRequest,
)
from app.services.command_policy import default_command_policy
from app.services.risk_engine import RiskEngine


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
        self.command_policy_path: Path = self._resolve_command_policy_path()
        self._load_command_policy()
        self.risk_policy: RiskPolicy = RiskEngine.default_policy()
        self.risk_policy_path: Path = self._resolve_risk_policy_path()
        self._load_risk_policy()
        self.session_store_path: Path = self._resolve_session_store_path()
        self.trace_steps: Dict[str, List[ServiceTraceStep]] = defaultdict(list)
        self.persist_session_credentials = (
            os.getenv("NETOPS_PERSIST_SESSION_CREDENTIALS", "1").strip().lower() in {"1", "true", "yes"}
        )
        self._load_session_store()

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
        self._save_session_store()
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
                device_name=session.device.name,
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
        self._save_session_store()
        return session

    def update_session_credentials(self, session_id: str, req: SessionCredentialUpdateRequest) -> Session:
        session = self.sessions[session_id]
        if req.username is not None:
            session.device.username = str(req.username).strip() or None
        if req.password is not None:
            session.device.password = str(req.password).strip() or None
        if req.api_token is not None:
            session.device.api_token = str(req.api_token).strip() or None
        self.sessions[session_id] = session
        self._save_session_store()
        return session

    def update_session_device_name(self, session_id: str, device_name: str) -> Session:
        session = self.sessions[session_id]
        session.device.name = (device_name or "").strip() or None
        self.sessions[session_id] = session
        self._save_session_store()
        return session

    def add_message(self, message: Message) -> None:
        self.messages[message.session_id].append(message)
        self._save_session_store()

    def list_messages(self, session_id: str) -> list[Message]:
        return self.messages[session_id]

    def add_command(self, command: CommandExecution) -> None:
        self.commands[command.session_id].append(command)
        self._save_session_store()

    def update_command(self, command: CommandExecution) -> None:
        session_commands = self.commands[command.session_id]
        for idx, existing in enumerate(session_commands):
            if existing.id == command.id:
                session_commands[idx] = command
                self._save_session_store()
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
        self._save_session_store()

    def list_evidence(self, session_id: str) -> list[Evidence]:
        return self.evidences[session_id]

    def set_summary(self, summary: IncidentSummary) -> None:
        self.summary[summary.session_id] = summary
        self._save_session_store()

    def get_summary(self, session_id: str) -> Optional[IncidentSummary]:
        return self.summary.get(session_id)

    def clear_summary(self, session_id: str) -> None:
        self.summary.pop(session_id, None)
        self._save_session_store()

    def get_timeline(self, session_id: str) -> TimelineResponse:
        return TimelineResponse(
            session=self._session_for_api(self.get_session(session_id)),
            messages=self.list_messages(session_id),
            commands=self.list_commands(session_id),
            evidences=self.list_evidence(session_id),
            summary=self.get_summary(session_id),
        )

    def append_ai_context(self, session_id: str, role: str, content: str) -> None:
        self.ai_context[session_id].append({"role": role, "content": content})
        self._save_session_store()

    def list_ai_context(self, session_id: str) -> list[dict[str, str]]:
        return self.ai_context[session_id]

    def reset_ai_context(self, session_id: str) -> None:
        self.ai_context[session_id] = []
        self._save_session_store()

    def next_trace_seq(self, session_id: str) -> int:
        return len(self.trace_steps[session_id]) + 1

    def add_trace_step(self, step: ServiceTraceStep) -> None:
        self.trace_steps[step.session_id].append(step)
        self._save_session_store()

    def update_trace_step(self, step: ServiceTraceStep) -> None:
        session_steps = self.trace_steps[step.session_id]
        for idx, existing in enumerate(session_steps):
            if existing.id == step.id:
                session_steps[idx] = step
                self._save_session_store()
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
        self._save_command_policy()
        return self.get_command_policy()

    def reset_command_policy(self) -> CommandPolicy:
        self.command_policy = default_command_policy()
        self._save_command_policy()
        return self.get_command_policy()

    def get_risk_policy(self) -> RiskPolicy:
        return self.risk_policy.model_copy(deep=True)

    def update_risk_policy(self, req: RiskPolicyUpdateRequest) -> RiskPolicy:
        if req.high_risk_patterns is not None:
            self.risk_policy.high_risk_patterns = self._normalize_patterns(req.high_risk_patterns)
        if req.medium_risk_patterns is not None:
            self.risk_policy.medium_risk_patterns = self._normalize_patterns(req.medium_risk_patterns)
        self._save_risk_policy()
        return self.get_risk_policy()

    def reset_risk_policy(self) -> RiskPolicy:
        self.risk_policy = RiskEngine.default_policy()
        self._save_risk_policy()
        return self.get_risk_policy()

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

    def _resolve_command_policy_path(self) -> Path:
        env_path = (os.getenv("NETOPS_COMMAND_POLICY_PATH") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "command_policy.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_command_policy.json"

    def _resolve_session_store_path(self) -> Path:
        env_path = (os.getenv("NETOPS_SESSION_STORE_PATH") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "session_store.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_session_store.json"

    def _resolve_risk_policy_path(self) -> Path:
        env_path = (os.getenv("NETOPS_RISK_POLICY_PATH") or "").strip()
        if env_path:
            return Path(env_path).expanduser()
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "risk_policy.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_risk_policy.json"

    def _load_command_policy(self) -> None:
        path = self.command_policy_path
        if not path.exists():
            return
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return
            blocked = payload.get("blocked_patterns")
            executable = payload.get("executable_patterns")
            enabled = payload.get("legality_check_enabled")
            if isinstance(blocked, list):
                self.command_policy.blocked_patterns = self._normalize_patterns([str(item) for item in blocked])
            if isinstance(executable, list):
                self.command_policy.executable_patterns = self._normalize_patterns([str(item) for item in executable])
            if isinstance(enabled, bool):
                self.command_policy.legality_check_enabled = enabled
        except Exception:
            return

    def _save_command_policy(self) -> None:
        path = self.command_policy_path
        payload = self.command_policy.model_dump(mode="json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _load_risk_policy(self) -> None:
        path = self.risk_policy_path
        if not path.exists():
            return
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return
            high = payload.get("high_risk_patterns")
            medium = payload.get("medium_risk_patterns")
            if isinstance(high, list):
                self.risk_policy.high_risk_patterns = self._normalize_patterns([str(item) for item in high])
            if isinstance(medium, list):
                self.risk_policy.medium_risk_patterns = self._normalize_patterns([str(item) for item in medium])
        except Exception:
            return

    def _save_risk_policy(self) -> None:
        path = self.risk_policy_path
        payload = self.risk_policy.model_dump(mode="json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _load_session_store(self) -> None:
        path = self.session_store_path
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return

            sessions_raw = payload.get("sessions", [])
            messages_raw = payload.get("messages", {})
            commands_raw = payload.get("commands", {})
            evidences_raw = payload.get("evidences", {})
            summary_raw = payload.get("summary", {})
            ai_context_raw = payload.get("ai_context", {})
            trace_raw = payload.get("trace_steps", {})

            if isinstance(sessions_raw, list):
                for item in sessions_raw:
                    try:
                        session = Session.model_validate(item)
                        self.sessions[session.id] = session
                    except Exception:
                        continue

            if isinstance(messages_raw, dict):
                for sid, rows in messages_raw.items():
                    if not isinstance(rows, list):
                        continue
                    parsed: list[Message] = []
                    for row in rows:
                        try:
                            parsed.append(Message.model_validate(row))
                        except Exception:
                            continue
                    self.messages[sid] = parsed

            if isinstance(commands_raw, dict):
                for sid, rows in commands_raw.items():
                    if not isinstance(rows, list):
                        continue
                    parsed: list[CommandExecution] = []
                    for row in rows:
                        try:
                            parsed.append(CommandExecution.model_validate(row))
                        except Exception:
                            continue
                    self.commands[sid] = parsed

            if isinstance(evidences_raw, dict):
                for sid, rows in evidences_raw.items():
                    if not isinstance(rows, list):
                        continue
                    parsed: list[Evidence] = []
                    for row in rows:
                        try:
                            parsed.append(Evidence.model_validate(row))
                        except Exception:
                            continue
                    self.evidences[sid] = parsed

            if isinstance(summary_raw, dict):
                for sid, row in summary_raw.items():
                    try:
                        self.summary[sid] = IncidentSummary.model_validate(row)
                    except Exception:
                        continue

            if isinstance(ai_context_raw, dict):
                for sid, rows in ai_context_raw.items():
                    if not isinstance(rows, list):
                        continue
                    parsed: list[dict[str, str]] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        role = str(row.get("role", "")).strip()
                        content = str(row.get("content", "")).strip()
                        if not role or not content:
                            continue
                        parsed.append({"role": role, "content": content})
                    self.ai_context[sid] = parsed

            if isinstance(trace_raw, dict):
                for sid, rows in trace_raw.items():
                    if not isinstance(rows, list):
                        continue
                    parsed: list[ServiceTraceStep] = []
                    for row in rows:
                        try:
                            parsed.append(ServiceTraceStep.model_validate(row))
                        except Exception:
                            continue
                    self.trace_steps[sid] = parsed
        except Exception:
            return

    def _save_session_store(self) -> None:
        path = self.session_store_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": [self._session_for_persistence(item).model_dump(mode="json") for item in self.sessions.values()],
                "messages": {sid: [msg.model_dump(mode="json") for msg in rows] for sid, rows in self.messages.items()},
                "commands": {sid: [cmd.model_dump(mode="json") for cmd in rows] for sid, rows in self.commands.items()},
                "evidences": {sid: [ev.model_dump(mode="json") for ev in rows] for sid, rows in self.evidences.items()},
                "summary": {sid: item.model_dump(mode="json") for sid, item in self.summary.items()},
                "ai_context": self.ai_context,
                "trace_steps": {sid: [step.model_dump(mode="json") for step in rows] for sid, rows in self.trace_steps.items()},
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _session_for_persistence(self, session: Session) -> Session:
        safe = session.model_copy(deep=True)
        if not self.persist_session_credentials:
            safe.device.username = None
            safe.device.password = None
            safe.device.api_token = None
        return safe

    def _session_for_api(self, session: Session) -> Session:
        safe = session.model_copy(deep=True)
        safe.device.username = None
        safe.device.password = None
        safe.device.api_token = None
        return safe
