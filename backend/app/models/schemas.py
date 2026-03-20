from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_id() -> str:
    return str(uuid4())


class DeviceProtocol(str, Enum):
    ssh = "ssh"
    telnet = "telnet"
    api = "api"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CommandStatus(str, Enum):
    queued = "queued"
    pending_confirm = "pending_confirm"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"
    rejected = "rejected"


class SessionStatus(str, Enum):
    open = "open"
    closed = "closed"


class AutomationLevel(str, Enum):
    read_only = "read_only"
    assisted = "assisted"
    full_auto = "full_auto"


class OperationMode(str, Enum):
    diagnosis = "diagnosis"
    query = "query"
    config = "config"


class DeviceTarget(BaseModel):
    host: str
    port: int = 22
    vendor: str = "cisco_like"
    protocol: DeviceProtocol = DeviceProtocol.ssh
    username: Optional[str] = None
    password: Optional[str] = None
    api_token: Optional[str] = None
    device_type: str = "cisco_ios"


class SessionCreateRequest(BaseModel):
    device: DeviceTarget
    automation_level: AutomationLevel = AutomationLevel.assisted
    operation_mode: OperationMode = OperationMode.diagnosis
    issue_scope: list[str] = Field(default_factory=lambda: ["connectivity", "interface", "routing"])


class SessionUpdateRequest(BaseModel):
    automation_level: AutomationLevel


class Session(BaseModel):
    id: str = Field(default_factory=make_id)
    device: DeviceTarget
    automation_level: AutomationLevel = AutomationLevel.assisted
    operation_mode: OperationMode = OperationMode.diagnosis
    issue_scope: list[str] = Field(default_factory=list)
    status: SessionStatus = SessionStatus.open
    created_at: datetime = Field(default_factory=now_utc)


class SessionResponse(BaseModel):
    id: str
    automation_level: AutomationLevel
    operation_mode: OperationMode
    status: SessionStatus
    created_at: datetime


class MessageCreateRequest(BaseModel):
    content: str


class Message(BaseModel):
    id: str = Field(default_factory=make_id)
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime = Field(default_factory=now_utc)


class CommandExecution(BaseModel):
    id: str = Field(default_factory=make_id)
    session_id: str
    step_no: int
    title: str
    command: str
    adapter_type: DeviceProtocol
    risk_level: RiskLevel
    status: CommandStatus = CommandStatus.queued
    requires_confirmation: bool = False
    output: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)


class Evidence(BaseModel):
    id: str = Field(default_factory=make_id)
    session_id: str
    command_id: str
    category: str
    raw_output: str
    parsed_data: dict[str, Any] = Field(default_factory=dict)
    conclusion: str
    created_at: datetime = Field(default_factory=now_utc)


class IncidentSummary(BaseModel):
    id: str = Field(default_factory=make_id)
    session_id: str
    mode: Literal["diagnosis", "query", "config", "unavailable", "error"] = "diagnosis"
    root_cause: str
    impact_scope: str
    recommendation: str
    query_result: Optional[str] = None
    follow_up_action: Optional[str] = None
    confidence: Optional[float] = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class ConfirmCommandRequest(BaseModel):
    approved: bool


class ConfirmCommandResponse(BaseModel):
    command_id: str
    status: CommandStatus
    message: str


class TimelineResponse(BaseModel):
    session: Session
    messages: list[Message]
    commands: list[CommandExecution]
    evidences: list[Evidence]
    summary: Optional[IncidentSummary] = None


class ExportRequest(BaseModel):
    format: Literal["markdown", "pdf"] = "markdown"


class ExportResponse(BaseModel):
    filename: str
    mime_type: str
    content: str


class EventEnvelope(BaseModel):
    event: str
    payload: dict[str, Any]
