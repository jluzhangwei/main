from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "success", "partial_failed", "failed"]
DeviceStatus = Literal["pending", "running", "success", "failed"]
JumpMode = Literal["direct", "smc"]
Vendor = Literal["cisco", "arista", "huawei", "paloalto", "unknown"]


class DeviceInput(BaseModel):
    device_ip: str
    device_port: int = 22
    device_name: str | None = None
    username: str
    password: str
    vendor_hint: Vendor | None = None
    jump_mode: JumpMode = "direct"
    jump_host: str | None = None
    jump_port: int | None = 22
    smc_command: str | None = None


class DeviceResult(BaseModel):
    device_id: str
    device_ip: str
    device_name: str | None = None
    status: DeviceStatus = "pending"
    reason: str | None = None
    vendor: str = "unknown"
    os_family: str | None = None
    model: str | None = None
    version: str | None = None
    reference_time: str | None = None
    device_time: str | None = None
    offset_seconds: float | None = None
    hits_count: int = 0
    blocks_count: int = 0
    raw_log_path: str | None = None
    filtered_log_path: str | None = None
    meta_path: str | None = None
    debug_log_path: str | None = None


class TaskRecord(BaseModel):
    task_id: str
    created_at: str
    updated_at: str
    status: TaskStatus = "pending"
    progress_total: int = 0
    progress_done: int = 0
    params: dict[str, Any] = Field(default_factory=dict)
    devices: list[DeviceResult] = Field(default_factory=list)


class TaskCreatePayload(BaseModel):
    start_time: datetime
    end_time: datetime
    context_lines: int = 3
    concurrency: int = 10
    per_device_timeout: int = 60
    default_username: str | None = None
    default_password: str | None = None
    default_jump_mode: JumpMode = "direct"
    jump_host: str | None = None
    jump_port: int = 22
    smc_command: str | None = None
    debug_mode: bool = False
    devices: list[DeviceInput]
