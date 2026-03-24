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
    name: Optional[str] = None
    port: int = 22
    vendor: str = "unknown"
    protocol: DeviceProtocol = DeviceProtocol.ssh
    username: Optional[str] = None
    password: Optional[str] = None
    jump_host: Optional[str] = None
    jump_port: int = 22
    jump_username: Optional[str] = None
    jump_password: Optional[str] = None
    api_token: Optional[str] = None
    device_type: str = "autodetect"
    platform: Optional[str] = None
    software_version: Optional[str] = None
    version_signature: Optional[str] = None


class SessionCreateRequest(BaseModel):
    device: DeviceTarget
    automation_level: AutomationLevel = AutomationLevel.assisted
    operation_mode: OperationMode = OperationMode.diagnosis
    issue_scope: list[str] = Field(default_factory=lambda: ["connectivity", "interface", "routing"])


class SessionUpdateRequest(BaseModel):
    automation_level: AutomationLevel


class SessionCredentialUpdateRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    jump_host: Optional[str] = None
    jump_port: Optional[int] = None
    jump_username: Optional[str] = None
    jump_password: Optional[str] = None
    api_token: Optional[str] = None


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


class SessionListItem(BaseModel):
    id: str
    host: str
    device_name: Optional[str] = None
    protocol: DeviceProtocol
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
    batch_id: Optional[str] = None
    batch_index: Optional[int] = None
    batch_total: Optional[int] = None
    created_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    original_command: Optional[str] = None
    effective_command: Optional[str] = None
    capability_state: Optional[str] = None
    capability_reason: Optional[str] = None
    capability_rule_id: Optional[str] = None
    constraint_source: Optional[str] = None
    constraint_reason: Optional[str] = None


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


class SessionStopResponse(BaseModel):
    session_id: str
    stop_requested: bool
    adapter_closed: bool
    running: bool
    message: str


class TimelineResponse(BaseModel):
    session: Session
    messages: list[Message]
    commands: list[CommandExecution]
    evidences: list[Evidence]
    summary: Optional[IncidentSummary] = None


class ServiceTraceStep(BaseModel):
    id: str = Field(default_factory=make_id)
    session_id: str
    seq_no: int
    step_type: str
    title: str
    status: str = "running"
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    command_id: Optional[str] = None
    detail: Optional[str] = None


class ServiceTraceResponse(BaseModel):
    session_id: str
    steps: list[ServiceTraceStep] = Field(default_factory=list)


class ExportRequest(BaseModel):
    format: Literal["markdown", "pdf"] = "markdown"


class ExportResponse(BaseModel):
    filename: str
    mime_type: str
    content: str


class EventEnvelope(BaseModel):
    event: str
    payload: dict[str, Any]


class LLMConfigRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    failover_enabled: Optional[bool] = None
    model_candidates: Optional[list[str]] = None
    batch_execution_enabled: Optional[bool] = None


class LLMConfigResponse(BaseModel):
    enabled: bool
    base_url: str
    model: str
    active_model: Optional[str] = None
    failover_enabled: bool = True
    batch_execution_enabled: bool = True
    model_candidates: list[str] = Field(default_factory=list)
    last_error: Optional[str] = None
    last_error_code: Optional[str] = None
    unavailable_reason: Optional[str] = None
    last_failover_at: Optional[datetime] = None


class LLMPromptPolicyResponse(BaseModel):
    enabled: bool
    base_url: str
    model: str
    batch_execution_enabled: bool = True
    prompts: dict[str, str] = Field(default_factory=dict)


class CommandPolicy(BaseModel):
    blocked_patterns: list[str] = Field(default_factory=list)
    executable_patterns: list[str] = Field(default_factory=list)
    legality_check_enabled: bool = True


class CommandPolicyUpdateRequest(BaseModel):
    blocked_patterns: Optional[list[str]] = None
    executable_patterns: Optional[list[str]] = None
    legality_check_enabled: Optional[bool] = None


class CommandCapabilityHistoryItem(BaseModel):
    changed_at: datetime = Field(default_factory=now_utc)
    action: Literal["rewrite", "block"]
    rewrite_to: Optional[str] = None
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None


class CommandCapabilityRule(BaseModel):
    id: str = Field(default_factory=make_id)
    scope_type: Literal["version", "device", "vendor", "global"] = "version"
    scope_key: str
    host: Optional[str] = None
    protocol: DeviceProtocol
    device_type: Optional[str] = None
    vendor: Optional[str] = None
    version_signature: Optional[str] = None
    command_key: str
    action: Literal["rewrite", "block"]
    rewrite_to: Optional[str] = None
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    source: Literal["learned", "manual"] = "learned"
    enabled: bool = True
    hit_count: int = 0
    last_hit_at: Optional[datetime] = None
    history: list[CommandCapabilityHistoryItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class CommandCapabilityUpsertRequest(BaseModel):
    id: Optional[str] = None
    scope_type: Optional[Literal["version", "device", "vendor", "global"]] = None
    host: Optional[str] = None
    protocol: DeviceProtocol = DeviceProtocol.ssh
    device_type: Optional[str] = None
    vendor: Optional[str] = None
    version_signature: Optional[str] = None
    command_key: str
    action: Literal["rewrite", "block"]
    rewrite_to: Optional[str] = None
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    source: Optional[Literal["learned", "manual"]] = None
    enabled: Optional[bool] = None


class CommandCapabilityResetRequest(BaseModel):
    host: Optional[str] = None
    version_signature: Optional[str] = None


class CommandCapabilityResetResponse(BaseModel):
    removed: int
    remaining: int


class RiskPolicy(BaseModel):
    high_risk_patterns: list[str] = Field(default_factory=list)
    medium_risk_patterns: list[str] = Field(default_factory=list)


class RiskPolicyUpdateRequest(BaseModel):
    high_risk_patterns: Optional[list[str]] = None
    medium_risk_patterns: Optional[list[str]] = None
