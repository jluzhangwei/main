from __future__ import annotations

import json
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


def test_llm_config_is_persisted_and_loaded_from_server_temp_file(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))

    diagnoser = DeepSeekDiagnoser()
    diagnoser.configure(api_key="sk-persisted", base_url="https://api.deepseek.com", model="deepseek-chat")

    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["api_key"] == "sk-persisted"

    reloaded = DeepSeekDiagnoser()
    assert reloaded.enabled is True
    assert reloaded.api_key == "sk-persisted"


def test_delete_saved_llm_config_removes_temp_file(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    diagnoser = DeepSeekDiagnoser()
    diagnoser.configure(api_key="sk-persisted")
    assert config_path.exists()

    diagnoser.delete_saved_config()

    assert diagnoser.enabled is False
    assert diagnoser.api_key == ""
    assert config_path.exists() is False


def test_llm_config_migrates_from_legacy_temp_file(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    legacy_tmp = tmp_path / "legacy_tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    legacy_tmp.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("NETOPS_LLM_CONFIG_PATH", raising=False)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setattr("app.services.deepseek_diagnoser.tempfile.gettempdir", lambda: str(legacy_tmp))

    legacy_path = legacy_tmp / "netops_ai_v1_llm_config.json"
    legacy_path.write_text(
        json.dumps({"api_key": "sk-legacy", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}),
        encoding="utf-8",
    )

    diagnoser = DeepSeekDiagnoser()

    assert diagnoser.enabled is True
    assert diagnoser.api_key == "sk-legacy"
    assert diagnoser.config_path == home_dir / ".netops-ai-v1" / "llm_config.json"
    assert diagnoser.config_path.exists() is True
    assert legacy_path.exists() is False


def test_llm_batch_execution_flag_can_be_configured_and_persisted(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))

    diagnoser = DeepSeekDiagnoser()
    diagnoser.configure(api_key="sk-persisted", batch_execution_enabled=False)

    status = diagnoser.status()
    assert status["batch_execution_enabled"] is False
    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["batch_execution_enabled"] is False

    reloaded = DeepSeekDiagnoser()
    assert reloaded.batch_execution_enabled is False


def test_nvidia_api_key_can_be_configured_and_persisted(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))

    diagnoser = DeepSeekDiagnoser()
    diagnoser.configure(nvidia_api_key="nvapi-test-token")

    status = diagnoser.status()
    assert status["enabled"] is True
    assert status["nvidia_enabled"] is True
    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["nvidia_api_key"] == "nvapi-test-token"

    reloaded = DeepSeekDiagnoser()
    assert reloaded.enabled is True
    assert reloaded.nvidia_api_key == "nvapi-test-token"


def test_nvidia_base_url_prefers_nvidia_api_key(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-main")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-token")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    diagnoser = DeepSeekDiagnoser()
    diagnoser.configure(base_url="https://integrate.api.nvidia.com/v1")

    selected = diagnoser._resolve_request_api_key(
        model="meta/llama-3.1-70b-instruct",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    assert selected == "nvapi-token"
