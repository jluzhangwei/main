from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DiagnosisSessionStatus = Literal[
    "draft",
    "calibrating_time",
    "planning",
    "waiting_approval",
    "executing",
    "analyzing",
    "ready_for_next_probe",
    "concluded",
    "failed",
    "aborted",
]

RoundStatus = Literal[
    "planning",
    "waiting_approval",
    "executing",
    "analyzing",
    "completed",
    "failed",
]

CommandRisk = Literal["read_only", "low", "medium", "high"]
JumpMode = Literal["direct", "smc"]


class FaultWindow(BaseModel):
    start_at: datetime
    end_at: datetime
    timezone: str = "Asia/Singapore"


class SessionDeviceInput(BaseModel):
    device_id: str | None = None
    device_ip: str
    device_port: int = 22
    username: str
    password: str
    vendor_hint: str | None = None
    jump_mode: JumpMode = "direct"
    jump_host: str | None = None
    jump_port: int = 22
    smc_command: str | None = None


class SessionDevicePublic(BaseModel):
    device_id: str
    device_ip: str
    device_port: int = 22
    username: str
    vendor_hint: str | None = None
    jump_mode: JumpMode = "direct"
    jump_host: str | None = None
    jump_port: int = 22
    smc_command: str | None = None


class DiagnosisSessionCreate(BaseModel):
    question: str
    fault_window: FaultWindow
    devices: list[SessionDeviceInput] = Field(default_factory=list)
    context_lines: int = 3
    per_device_timeout: int = 60
    focus_goals: list[str] = Field(default_factory=list)
    focus_lock: bool = True


class PlannedCommand(BaseModel):
    command_id: str
    device_id: str = "*"
    intent: str = ""
    command: str
    reason: str = ""
    expected_signal: str = ""
    risk_level: CommandRisk = "read_only"
    requires_approval: bool = True
    approved: bool = False


class CommandExecution(BaseModel):
    command_id: str
    device_id: str
    command: str
    status: str
    reused: bool = False
    reused_from_round: int | None = None
    reused_from_command_id: str | None = None
    output_file: str | None = None
    error: str | None = None
    duration_sec: float | None = None


class DiagnosisRound(BaseModel):
    round_no: int
    status: RoundStatus = "planning"
    planner_summary: str = ""
    planner_raw_output: str = ""
    target_probe: dict[str, Any] = Field(default_factory=dict)
    commands: list[PlannedCommand] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    known_issue_hits: list[dict[str, Any]] = Field(default_factory=list)
    stop_decision: dict[str, Any] = Field(default_factory=dict)
    focus_review: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False
    executions: list[CommandExecution] = Field(default_factory=list)
    analysis_result: str = ""
    evidence_overview: dict[str, Any] = Field(default_factory=dict)
    evidence_signals: list[dict[str, Any]] = Field(default_factory=list)
    retrospective: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class DiagnosisSessionRecord(BaseModel):
    session_id: str
    question: str
    fault_window: FaultWindow
    status: DiagnosisSessionStatus = "draft"
    context_lines: int = 3
    per_device_timeout: int = 60
    devices: list[SessionDevicePublic] = Field(default_factory=list)
    focus_goals: list[str] = Field(default_factory=list)
    focus_lock: bool = True
    sop_notes: list[str] = Field(default_factory=list)
    time_calibration: list[dict[str, Any]] = Field(default_factory=list)
    rounds: list[DiagnosisRound] = Field(default_factory=list)
    last_error: str = ""
    created_at: str
    updated_at: str
