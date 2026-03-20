from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.schemas import (  # noqa: E402
    CommandExecution,
    CommandStatus,
    DeviceProtocol,
    DeviceTarget,
    Evidence,
    RiskLevel,
    Session,
)
from app.services.deepseek_diagnoser import DeepSeekDiagnoser  # noqa: E402


async def main() -> int:
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is required", file=sys.stderr)
        return 2

    session = Session(
        device=DeviceTarget(
            host="192.168.0.88",
            protocol=DeviceProtocol.ssh,
            vendor="huawei",
            device_type="huawei",
        ),
    )

    commands = [
        CommandExecution(
            session_id=session.id,
            step_no=1,
            title="接口状态",
            command="show ip interface brief",
            adapter_type=DeviceProtocol.ssh,
            risk_level=RiskLevel.low,
            status=CommandStatus.succeeded,
            output=(
                "Interface                         PHY   Protocol  InUti OutUti inErrors outErrors\n"
                "Ethernet1/0/5                     up    up        0%    0%     0        0\n"
                "Ethernet1/0/6                     administratively down down 0% 0% 0 0\n"
            ),
        ),
        CommandExecution(
            session_id=session.id,
            step_no=2,
            title="接口详情",
            command="show interface status",
            adapter_type=DeviceProtocol.ssh,
            risk_level=RiskLevel.low,
            status=CommandStatus.succeeded,
            output=(
                "Ethernet1/0/6 current state : DOWN\n"
                "Line protocol current state : DOWN\n"
                "Description: --\n"
                "Administratively DOWN by configuration\n"
            ),
        ),
    ]

    evidences = [
        Evidence(
            session_id=session.id,
            command_id=commands[0].id,
            category="interface",
            raw_output=commands[0].output or "",
            parsed_data={"down_interfaces": ["Ethernet1/0/6"]},
            conclusion="Detected down interfaces: Ethernet1/0/6",
        ),
        Evidence(
            session_id=session.id,
            command_id=commands[1].id,
            category="interface",
            raw_output=commands[1].output or "",
            parsed_data={"admin_down_interfaces": ["Ethernet1/0/6"]},
            conclusion="Interface Ethernet1/0/6 is administratively down.",
        ),
    ]

    diagnoser = DeepSeekDiagnoser()
    summary = await diagnoser.diagnose(session=session, commands=commands, evidences=evidences)
    if summary is None:
        print("DeepSeek diagnosis failed", file=sys.stderr)
        return 1

    print("root_cause:", summary.root_cause)
    print("impact_scope:", summary.impact_scope)
    print("recommendation:", summary.recommendation)

    root = summary.root_cause.lower()
    ok = (
        "shutdown" in root
        or "administratively down" in root
        or "admin down" in root
        or "管理员" in summary.root_cause
    )
    if not ok:
        print("Unexpected root_cause for target case", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
