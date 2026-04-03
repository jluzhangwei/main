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
    detail_payload: Optional[dict[str, Any]] = None


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
    nvidia_api_key: Optional[str] = None
    base_url: Optional[str] = None
    nvidia_base_url: Optional[str] = None
    model: Optional[str] = None
    failover_enabled: Optional[bool] = None
    model_candidates: Optional[list[str]] = None
    batch_execution_enabled: Optional[bool] = None


class LLMConfigResponse(BaseModel):
    enabled: bool
    base_url: str
    nvidia_base_url: Optional[str] = None
    model: str
    active_model: Optional[str] = None
    failover_enabled: bool = True
    batch_execution_enabled: bool = True
    model_candidates: list[str] = Field(default_factory=list)
    nvidia_enabled: bool = False
    last_error: Optional[str] = None
    last_error_code: Optional[str] = None
    unavailable_reason: Optional[str] = None
    last_failover_at: Optional[datetime] = None


class LLMPromptPolicyResponse(BaseModel):
    enabled: bool
    base_url: str
    nvidia_base_url: Optional[str] = None
    model: str
    batch_execution_enabled: bool = True
    nvidia_enabled: bool = False
    prompts: dict[str, str] = Field(default_factory=dict)


class SOPArchiveCommandTemplate(BaseModel):
    vendor: str = "generic"
    commands: list[str] = Field(default_factory=list)


class SOPArchiveEntryResponse(BaseModel):
    id: str
    status: Optional[str] = None
    name: str
    summary: str
    usage_hint: str
    trigger_keywords: list[str] = Field(default_factory=list)
    vendor_tags: list[str] = Field(default_factory=list)
    version_signatures: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    anti_conditions: list[str] = Field(default_factory=list)
    evidence_goals: list[str] = Field(default_factory=list)
    command_templates: list[SOPArchiveCommandTemplate] = Field(default_factory=list)
    fallback_commands: list[str] = Field(default_factory=list)
    expected_findings: list[str] = Field(default_factory=list)
    source_run_ids: list[str] = Field(default_factory=list)
    version: int = 1
    matched_count: int = 0
    referenced_count: int = 0
    success_count: int = 0
    review_notes: Optional[str] = None
    generated_by_model: Optional[str] = None
    generated_by_prompt_version: Optional[str] = None
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SOPArchiveResponse(BaseModel):
    total: int
    matched: list[SOPArchiveEntryResponse] = Field(default_factory=list)
    items: list[SOPArchiveEntryResponse] = Field(default_factory=list)


class SOPStatus(str, Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class SOPRecord(BaseModel):
    id: str = Field(default_factory=make_id)
    version: int = 1
    status: SOPStatus = SOPStatus.draft
    name: str
    summary: str
    usage_hint: str
    trigger_keywords: list[str] = Field(default_factory=list)
    vendor_tags: list[str] = Field(default_factory=list)
    version_signatures: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    anti_conditions: list[str] = Field(default_factory=list)
    evidence_goals: list[str] = Field(default_factory=list)
    command_templates: list[SOPArchiveCommandTemplate] = Field(default_factory=list)
    fallback_commands: list[str] = Field(default_factory=list)
    expected_findings: list[str] = Field(default_factory=list)
    source_run_ids: list[str] = Field(default_factory=list)
    generated_by_model: Optional[str] = None
    generated_by_prompt_version: Optional[str] = None
    review_notes: Optional[str] = None
    matched_count: int = 0
    referenced_count: int = 0
    success_count: int = 0
    last_matched_at: Optional[datetime] = None
    last_referenced_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    def to_archive_response(self) -> SOPArchiveEntryResponse:
        return SOPArchiveEntryResponse(
            id=self.id,
            status=self.status.value,
            name=self.name,
            summary=self.summary,
            usage_hint=self.usage_hint,
            trigger_keywords=list(self.trigger_keywords),
            vendor_tags=list(self.vendor_tags),
            version_signatures=list(self.version_signatures),
            preconditions=list(self.preconditions),
            anti_conditions=list(self.anti_conditions),
            evidence_goals=list(self.evidence_goals),
            command_templates=[item.model_copy(deep=True) for item in self.command_templates],
            fallback_commands=list(self.fallback_commands),
            expected_findings=list(self.expected_findings),
            source_run_ids=list(self.source_run_ids),
            version=self.version,
            matched_count=self.matched_count,
            referenced_count=self.referenced_count,
            success_count=self.success_count,
            review_notes=self.review_notes,
            generated_by_model=self.generated_by_model,
            generated_by_prompt_version=self.generated_by_prompt_version,
            published_at=self.published_at,
            updated_at=self.updated_at,
        )


class SOPListResponse(BaseModel):
    total: int
    items: list[SOPArchiveEntryResponse] = Field(default_factory=list)


class SOPExtractFromRunRequest(BaseModel):
    run_id: str
    force: bool = False


class SOPUpsertRequest(BaseModel):
    name: str
    summary: str
    usage_hint: str
    trigger_keywords: list[str] = Field(default_factory=list)
    vendor_tags: list[str] = Field(default_factory=list)
    version_signatures: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    anti_conditions: list[str] = Field(default_factory=list)
    evidence_goals: list[str] = Field(default_factory=list)
    command_templates: list[SOPArchiveCommandTemplate] = Field(default_factory=list)
    fallback_commands: list[str] = Field(default_factory=list)
    expected_findings: list[str] = Field(default_factory=list)
    source_run_ids: list[str] = Field(default_factory=list)
    generated_by_model: Optional[str] = None
    generated_by_prompt_version: Optional[str] = None
    review_notes: Optional[str] = None


class SOPPublishResponse(BaseModel):
    item: SOPArchiveEntryResponse
    previous_status: SOPStatus
    current_status: SOPStatus


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


class JobMode(str, Enum):
    diagnosis = "diagnosis"
    inspection = "inspection"
    repair = "repair"


class TopologyMode(str, Enum):
    auto = "auto"
    external = "external"
    hybrid = "hybrid"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobPhase(str, Enum):
    collect = "collect"
    correlate = "correlate"
    plan = "plan"
    approve = "approve"
    execute = "execute"
    analyze = "analyze"
    conclude = "conclude"


class JobActionGroupStatus(str, Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobCommandStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"
    rejected = "rejected"


class JobDeviceRequest(BaseModel):
    host: str
    name: Optional[str] = None
    port: int = 22
    protocol: DeviceProtocol = DeviceProtocol.ssh
    vendor: str = "unknown"
    username: Optional[str] = None
    password: Optional[str] = None
    jump_host: Optional[str] = None
    jump_port: int = 22
    jump_username: Optional[str] = None
    jump_password: Optional[str] = None
    api_token: Optional[str] = None
    device_type: str = "autodetect"


class JobTopologyEdge(BaseModel):
    source: str
    target: str
    kind: str = "dependency"
    confidence: float = 0.8
    reason: Optional[str] = None


class RCAWeights(BaseModel):
    anomaly: float = 1.0
    timing: float = 1.0
    topology: float = 1.0
    change: float = 1.0
    consistency: float = 1.0


class JobCreateRequest(BaseModel):
    name: Optional[str] = None
    problem: str
    mode: JobMode = JobMode.diagnosis
    devices: list[JobDeviceRequest] = Field(default_factory=list)
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    max_gap_seconds: int = 300
    topology_mode: TopologyMode = TopologyMode.hybrid
    topology_edges: list[JobTopologyEdge] = Field(default_factory=list)
    max_device_concurrency: int = 20
    execution_policy: Literal["stop_on_failure", "continue_on_failure", "rollback_template"] = "stop_on_failure"
    rca_weights: RCAWeights = Field(default_factory=RCAWeights)
    webhook_url: Optional[str] = None
    webhook_events: list[str] = Field(default_factory=list)


class JobDevice(BaseModel):
    id: str = Field(default_factory=make_id)
    host: str
    name: Optional[str] = None
    port: int = 22
    protocol: DeviceProtocol = DeviceProtocol.ssh
    vendor: str = "unknown"
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
    status: str = "pending"
    last_error: Optional[str] = None


class JobCommandResult(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    device_id: str
    action_group_id: Optional[str] = None
    step_no: int
    title: str
    command: str
    effective_command: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.low
    status: JobCommandStatus = JobCommandStatus.queued
    output: Optional[str] = None
    error: Optional[str] = None
    capability_state: Optional[str] = None
    capability_reason: Optional[str] = None
    constraint_source: Optional[str] = None
    constraint_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class JobEvidence(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    device_id: str
    command_id: str
    category: str
    raw_output: str
    parsed_data: dict[str, Any] = Field(default_factory=dict)
    conclusion: str
    created_at: datetime = Field(default_factory=now_utc)


class IncidentEvent(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    device_id: str
    timestamp: datetime = Field(default_factory=now_utc)
    severity: Literal["low", "medium", "high"] = "medium"
    category: str = "generic"
    title: str
    detail: str
    evidence_id: Optional[str] = None


class IncidentCluster(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    start_at: datetime
    end_at: datetime
    incident_ids: list[str] = Field(default_factory=list)
    device_ids: list[str] = Field(default_factory=list)
    incident_count: int = 0


class CausalEdge(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    source_device_id: str
    target_device_id: str
    kind: str = "dependency"
    confidence: float = 0.8
    reason: Optional[str] = None


class RCAResult(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    root_device_id: Optional[str] = None
    root_device_name: Optional[str] = None
    root_device_host: Optional[str] = None
    root_cause: str = ""
    impact_scope: str = ""
    confidence: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    impacted_device_ids: list[str] = Field(default_factory=list)
    causal_edges: list[CausalEdge] = Field(default_factory=list)
    summary: str = ""
    recommendation: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class JobActionGroup(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    device_id: str
    title: str
    commands: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.low
    requires_approval: bool = False
    rollback_commands: list[str] = Field(default_factory=list)
    status: JobActionGroupStatus = JobActionGroupStatus.pending_approval
    approve_reason: Optional[str] = None
    reject_reason: Optional[str] = None
    approved_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class JobEvent(BaseModel):
    id: str = Field(default_factory=make_id)
    job_id: str
    seq_no: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)


class Job(BaseModel):
    id: str = Field(default_factory=make_id)
    name: Optional[str] = None
    problem: str
    mode: JobMode = JobMode.diagnosis
    status: JobStatus = JobStatus.queued
    phase: JobPhase = JobPhase.collect
    topology_mode: TopologyMode = TopologyMode.hybrid
    max_gap_seconds: int = 300
    max_device_concurrency: int = 20
    execution_policy: Literal["stop_on_failure", "continue_on_failure", "rollback_template"] = "stop_on_failure"
    rca_weights: RCAWeights = Field(default_factory=RCAWeights)
    idempotency_key: Optional[str] = None
    requester_key_id: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_events: list[str] = Field(default_factory=list)
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    devices: list[JobDevice] = Field(default_factory=list)
    external_topology_edges: list[JobTopologyEdge] = Field(default_factory=list)
    command_results: list[JobCommandResult] = Field(default_factory=list)
    evidences: list[JobEvidence] = Field(default_factory=list)
    incidents: list[IncidentEvent] = Field(default_factory=list)
    clusters: list[IncidentCluster] = Field(default_factory=list)
    causal_edges: list[CausalEdge] = Field(default_factory=list)
    action_groups: list[JobActionGroup] = Field(default_factory=list)
    rca_result: Optional[RCAResult] = None
    created_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=now_utc)
    error: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    name: Optional[str] = None
    problem: str
    mode: JobMode
    status: JobStatus
    phase: JobPhase
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime
    device_count: int
    command_count: int
    pending_action_groups: int = 0
    root_device_id: Optional[str] = None


class JobListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[JobResponse] = Field(default_factory=list)


class JobTimelineResponse(BaseModel):
    job: Job
    events: list[JobEvent] = Field(default_factory=list)


class JobActionDecisionRequest(BaseModel):
    reason: Optional[str] = None


class JobBulkActionDecisionRequest(BaseModel):
    action_group_ids: list[str] = Field(default_factory=list)
    reason: Optional[str] = None


class JobBulkActionDecisionResponse(BaseModel):
    job_id: str
    total: int
    updated: int
    skipped: int
    results: list[JobActionDecisionResponse] = Field(default_factory=list)


class JobActionDecisionResponse(BaseModel):
    job_id: str
    action_group_id: str
    status: JobActionGroupStatus
    message: str


class JobReportResponse(BaseModel):
    filename: str
    mime_type: str
    content: str


class JobTopologyUpdateRequest(BaseModel):
    edges: list[JobTopologyEdge] = Field(default_factory=list)
    replace: bool = False


class JobRCAWeightsUpdateRequest(BaseModel):
    rca_weights: RCAWeights


class RunKind(str, Enum):
    single = "single"
    multi = "multi"


class RunStatus(str, Enum):
    open = "open"
    running = "running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunCreateRequest(BaseModel):
    name: Optional[str] = None
    problem: Optional[str] = None
    devices: list[JobDeviceRequest] = Field(default_factory=list)
    automation_level: AutomationLevel = AutomationLevel.assisted
    operation_mode: OperationMode = OperationMode.diagnosis
    issue_scope: list[str] = Field(default_factory=lambda: ["connectivity", "interface", "routing"])
    max_gap_seconds: int = 300
    topology_mode: TopologyMode = TopologyMode.hybrid
    topology_edges: list[JobTopologyEdge] = Field(default_factory=list)
    max_device_concurrency: int = 20
    execution_policy: Literal["stop_on_failure", "continue_on_failure", "rollback_template"] = "stop_on_failure"
    webhook_url: Optional[str] = None
    webhook_events: list[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    id: str
    source_id: str
    kind: RunKind
    name: Optional[str] = None
    protocol: Optional[DeviceProtocol] = None
    problem: Optional[str] = None
    status: RunStatus
    phase: Optional[str] = None
    automation_level: AutomationLevel = AutomationLevel.assisted
    operation_mode: OperationMode = OperationMode.diagnosis
    created_at: datetime
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    device_count: int = 1
    device_hosts: list[str] = Field(default_factory=list)
    pending_actions: int = 0
    sop_extracted: bool = False
    sop_draft_count: int = 0
    sop_published_count: int = 0
    primary_sop_id: Optional[str] = None


class RunListResponse(BaseModel):
    total: int
    items: list[RunResponse] = Field(default_factory=list)


class RunTimelineResponse(BaseModel):
    run: RunResponse
    payload: dict[str, Any] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    timeline: TimelineResponse
    service_trace: ServiceTraceResponse


class RunActionDecisionRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    reason: Optional[str] = None


class RunActionDecisionItem(BaseModel):
    item_id: str
    status: str
    message: str


class RunActionDecisionResponse(BaseModel):
    run_id: str
    total: int
    updated: int
    skipped: int
    results: list[RunActionDecisionItem] = Field(default_factory=list)


class RunStopResponse(BaseModel):
    run_id: str
    source_id: str
    kind: RunKind
    status: RunStatus
    stop_requested: bool
    message: str


class ApiKeyCreateRequest(BaseModel):
    name: str
    permissions: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class ApiKeyListItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    disabled_reason: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None


class ApiKeyCreateResponse(ApiKeyListItem):
    api_key: str


class ApiKeyRecord(BaseModel):
    id: str = Field(default_factory=make_id)
    name: str
    key_prefix: str
    key_hash: str
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    disabled_reason: Optional[str] = None
    expires_at: Optional[datetime] = None
    rotated_from_id: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    last_used_at: Optional[datetime] = None


class ApiKeyUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    disabled_reason: Optional[str] = None
    expires_at: Optional[datetime] = None


class ApiKeyRotateRequest(BaseModel):
    name: Optional[str] = None
    permissions: Optional[list[str]] = None
    expires_at: Optional[datetime] = None


class ApiKeyRotateResponse(ApiKeyCreateResponse):
    rotated_from_id: str


class AuditLogExportResponse(BaseModel):
    filename: str
    mime_type: str
    content: str


class AuditLog(BaseModel):
    id: str = Field(default_factory=make_id)
    ts: datetime = Field(default_factory=now_utc)
    actor_key_id: Optional[str] = None
    actor_name: Optional[str] = None
    action: str
    resource: str
    status: Literal["ok", "denied", "error"] = "ok"
    detail: Optional[str] = None


class CommandProfile(BaseModel):
    id: str = Field(default_factory=make_id)
    version_signature: str
    command_key: str
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    rewrite_hit_count: int = 0
    last_error: Optional[str] = None
    updated_at: datetime = Field(default_factory=now_utc)
