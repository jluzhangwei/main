from __future__ import annotations

import pytest

from app.models.schemas import CommandExecution, CommandStatus, DeviceProtocol, DeviceTarget, Evidence, RiskLevel, Session
from app.services.deepseek_diagnoser import DeepSeekDiagnoser


class StubDiagnoser(DeepSeekDiagnoser):
    def __init__(self, responses: list[str]):
        super().__init__()
        self.responses = responses
        self.api_key = "stub-key"

    async def _chat_completion(self, *, system_prompt: str, user_payload: dict):
        if not self.responses:
            return ""
        return self.responses.pop(0)


def _sample_session_bundle():
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
            output="Ethernet1/0/6 administratively down down",
        )
    ]
    evidences = [
        Evidence(
            session_id=session.id,
            command_id=commands[0].id,
            category="interface",
            raw_output=commands[0].output or "",
            parsed_data={"down_interfaces": ["Ethernet1/0/6"]},
            conclusion="Detected down interfaces: Ethernet1/0/6",
        )
    ]
    return session, commands, evidences


@pytest.mark.asyncio
async def test_diagnose_primary_pass_review_pass():
    session, commands, evidences = _sample_session_bundle()
    diagnoser = StubDiagnoser(
        responses=[
            '{"root_cause":"Ethernet1/0/6 is administratively shutdown","impact_scope":"traffic via Ethernet1/0/6 is down","recommendation":"undo shutdown","confidence":0.93,"evidence_refs":[{"command_step":1,"quote":"administratively down","why":"shows admin shutdown"}]}',
            '{"verdict":"pass","issues":[],"corrected_summary":null}',
        ]
    )

    summary = await diagnoser.diagnose(session=session, commands=commands, evidences=evidences)

    assert summary is not None
    assert "shutdown" in summary.root_cause.lower()
    assert summary.confidence == 0.93
    assert len(summary.evidence_refs) == 1


@pytest.mark.asyncio
async def test_diagnose_rewrite_path():
    session, commands, evidences = _sample_session_bundle()
    diagnoser = StubDiagnoser(
        responses=[
            '{"root_cause":"default route missing","impact_scope":"internet","recommendation":"add route","confidence":0.44,"evidence_refs":[{"command_step":1,"quote":"none","why":"bad"}]}',
            '{"verdict":"fail","issues":["root cause not supported"],"corrected_summary":null}',
            '{"root_cause":"Ethernet1/0/6 was administratively shutdown by config","impact_scope":"Ethernet1/0/6 traffic affected","recommendation":"undo shutdown on Ethernet1/0/6","confidence":0.9,"evidence_refs":[{"command_step":1,"quote":"administratively down","why":"direct evidence"}]}',
            '{"verdict":"pass","issues":[],"corrected_summary":null}',
        ]
    )

    summary = await diagnoser.diagnose(session=session, commands=commands, evidences=evidences)

    assert summary is not None
    assert "shutdown" in summary.root_cause.lower()
    assert summary.confidence == 0.9
