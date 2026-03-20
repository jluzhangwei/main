from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import (  # noqa: E402
    AutomationLevel,
    ConfirmCommandRequest,
    DeviceProtocol,
    DeviceTarget,
    SessionCreateRequest,
)
from app.services.orchestrator import ConversationOrchestrator  # noqa: E402
from app.services.store import InMemoryStore  # noqa: E402


REQUIRED_ENV_VARS = (
    "DEVICE_HOST",
    "DEVICE_USERNAME",
    "DEVICE_PASSWORD",
    "DEVICE_PROTOCOL",
    "VENDOR",
    "AUTOMATION_LEVEL",
    "DIAG_MESSAGE",
)


def main() -> int:
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr, flush=True)
        return 2

    try:
        result = asyncio.run(run_once())
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        return 1

    return result


async def run_once() -> int:
    device = build_device_from_env()
    automation_level = parse_automation_level(required_env("AUTOMATION_LEVEL"))
    diag_message = required_env("DIAG_MESSAGE")
    allow_simulation = os.getenv("NETOPS_ALLOW_SIMULATION_FALLBACK", "0").strip().lower() in {"1", "true", "yes"}
    auto_approve_high_risk = os.getenv("AUTO_APPROVE_HIGH_RISK", "0").strip().lower() in {"1", "true", "yes"}

    store = InMemoryStore()
    orchestrator = ConversationOrchestrator(store, allow_simulation=allow_simulation)
    session = store.create_session(
        SessionCreateRequest(
            device=device,
            automation_level=automation_level,
        )
    )

    print(
        f"[session] id={session.id} host={mask_host(device.host)} "
        f"protocol={device.protocol.value} vendor={device.vendor} automation={automation_level.value}",
        flush=True,
    )

    events: list[dict[str, Any]] = []
    async for chunk in orchestrator.stream_message(session.id, diag_message):
        parsed = parse_sse_chunk(chunk)
        if parsed is not None:
            events.append(parsed)

    commands = store.list_commands(session.id)
    summary = store.get_summary(session.id)

    pending_commands = [cmd for cmd in commands if cmd.status.value == "pending_confirm"]
    blocked_commands = [cmd for cmd in commands if cmd.status.value == "blocked"]
    failed_commands = [cmd for cmd in commands if cmd.status.value == "failed"]

    if pending_commands and auto_approve_high_risk:
        print("[auto-approve] approving pending high-risk commands", flush=True)
        for cmd in pending_commands:
            result = await orchestrator.confirm_command(
                session.id,
                cmd.id,
                ConfirmCommandRequest(approved=True),
            )
            print(f"  command_id={cmd.id} status={result.status.value}", flush=True)

        commands = store.list_commands(session.id)
        summary = store.get_summary(session.id)
        pending_commands = [cmd for cmd in commands if cmd.status.value == "pending_confirm"]
        blocked_commands = [cmd for cmd in commands if cmd.status.value == "blocked"]
        failed_commands = [cmd for cmd in commands if cmd.status.value == "failed"]

    print("[result]", flush=True)
    if summary:
        print(f"  root_cause: {summary.root_cause}", flush=True)
        print(f"  impact_scope: {summary.impact_scope}", flush=True)
        print(f"  recommendation: {summary.recommendation}", flush=True)
        if summary.confidence is not None:
            print(f"  confidence: {summary.confidence:.2f}", flush=True)
    else:
        print("  summary: unavailable", flush=True)

    if commands:
        print("[commands]", flush=True)
        for cmd in commands:
            status = cmd.status.value
            print(f"  {cmd.step_no}. {cmd.title} | {status} | risk={cmd.risk_level.value} | {cmd.command}", flush=True)
    else:
        print("[commands] none", flush=True)

    if pending_commands:
        print("[pending]", flush=True)
        for cmd in pending_commands:
            print(f"  confirm required for: {cmd.command}", flush=True)

    if blocked_commands:
        print("[blocked]", flush=True)
        for cmd in blocked_commands:
            reason = cmd.error or "blocked"
            print(f"  {cmd.command} | {reason}", flush=True)

    if failed_commands:
        print("[failed]", flush=True)
        for cmd in failed_commands:
            reason = cmd.error or "failed"
            print(f"  {cmd.command} | {reason}", flush=True)

    print(f"[events] {len(events)}", flush=True)

    if failed_commands:
        return 1
    if pending_commands:
        return 3
    return 0


def build_device_from_env() -> DeviceTarget:
    protocol = parse_protocol(required_env("DEVICE_PROTOCOL"))
    vendor = required_env("VENDOR")
    device_type = infer_device_type(vendor, protocol)

    return DeviceTarget(
        host=required_env("DEVICE_HOST"),
        port=int(os.getenv("DEVICE_PORT", "22")),
        vendor=vendor,
        protocol=protocol,
        username=required_env("DEVICE_USERNAME"),
        password=required_env("DEVICE_PASSWORD", trim=False),
        device_type=device_type,
    )


def infer_device_type(vendor: str, protocol: DeviceProtocol) -> str:
    normalized = vendor.strip().lower()
    if protocol == DeviceProtocol.api:
        return "api"
    if protocol == DeviceProtocol.telnet:
        if "huawei" in normalized:
            return "huawei_telnet"
        if "arista" in normalized:
            return "arista_eos_telnet"
        return "cisco_ios_telnet"
    if "huawei" in normalized:
        return "huawei"
    if "juniper" in normalized:
        return "juniper_junos"
    if "arista" in normalized:
        return "arista_eos"
    return "cisco_ios"


def parse_protocol(raw: str) -> DeviceProtocol:
    normalized = raw.strip().lower()
    if normalized == "ssh":
        return DeviceProtocol.ssh
    if normalized == "telnet":
        return DeviceProtocol.telnet
    if normalized == "api":
        return DeviceProtocol.api
    raise ValueError("DEVICE_PROTOCOL must be ssh, telnet, or api")


def parse_automation_level(raw: str) -> AutomationLevel:
    normalized = raw.strip().lower()
    try:
        return AutomationLevel(normalized)
    except ValueError as exc:
        raise ValueError("AUTOMATION_LEVEL must be read_only, assisted, or full_auto") from exc


def parse_sse_chunk(chunk: str) -> Optional[Dict[str, Any]]:
    event_name = None
    data_text = None
    for line in chunk.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            data_text = line.removeprefix("data: ").strip()

    if not event_name or data_text is None:
        return None

    try:
        payload = json.loads(data_text)
    except json.JSONDecodeError:
        payload = {"raw": data_text}

    return {"event": event_name, "payload": payload}


def required_env(name: str, *, trim: bool = True) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is required")
    if trim:
        value = value.strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def mask_host(host: str) -> str:
    return host


if __name__ == "__main__":
    raise SystemExit(main())
