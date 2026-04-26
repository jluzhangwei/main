from __future__ import annotations

import asyncio
import json
import pytest

from app.models.schemas import CommandExecution, CommandStatus, DeviceProtocol, DeviceTarget, Evidence, RiskLevel, Session
from app.services.llm_diagnoser import DeepSeekDiagnoser, SOP_EXTRACTION_SYSTEM_PROMPT, NEXT_STEP_SYSTEM_PROMPT
from app.services.llm_planner_bridge import LLMPlannerBridge


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
    monkeypatch.setenv("CODEX_AUTH_PATH", str(tmp_path / "missing-codex-auth.json"))

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
    monkeypatch.setattr("app.services.llm_diagnoser.tempfile.gettempdir", lambda: str(legacy_tmp))

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


def test_loading_config_resets_legacy_cross_provider_base_url(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_runtime.json"
    monkeypatch.setenv("NETOPS_LLM_CONFIG_PATH", str(config_path))

    config_path.write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key": "sk-test",
                "nvidia_api_key": "nv-test",
                "provider_base_urls": {
                    "deepseek": "https://integrate.api.nvidia.com/v1",
                    "nvidia": "https://integrate.api.nvidia.com/v1",
                },
            }
        ),
        encoding="utf-8",
    )

    diagnoser = DeepSeekDiagnoser()

    assert diagnoser.provider == "deepseek"
    assert diagnoser.base_url == "https://api.deepseek.com"
    assert diagnoser.nvidia_base_url == "https://integrate.api.nvidia.com/v1"


def test_codex_auth_loader_supports_nested_tokens(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "codex-access",
                    "account_id": "codex-account",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_AUTH_PATH", raising=False)

    diagnoser = DeepSeekDiagnoser()
    loaded = diagnoser._load_codex_auth()

    assert loaded == {"access_token": "codex-access", "account_id": "codex-account"}


def test_gpt5_routes_to_codex_when_openai_key_absent_and_codex_auth_exists(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "codex-access", "account_id": "codex-account"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    diagnoser = DeepSeekDiagnoser()

    assert diagnoser._resolve_effective_provider(model="gpt-5.4", provider="openai") == "codex"


def test_gpt5_keeps_openai_when_openai_key_present(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "codex-access", "account_id": "codex-account"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    diagnoser = DeepSeekDiagnoser()

    assert diagnoser._resolve_effective_provider(model="gpt-5.4", provider="openai") == "openai"


def test_extract_content_supports_codex_output_shape():
    diagnoser = DeepSeekDiagnoser()
    text = diagnoser._extract_content(
        {
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"text": "thinking summary"}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"decision\":\"final\"}"}],
                },
            ]
        }
    )
    assert text == "thinking summary\n{\"decision\":\"final\"}"


def test_collect_codex_sse_response_parses_completed_and_output_items():
    diagnoser = DeepSeekDiagnoser()
    body = """event: response.output_item.done
data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","content":[{"type":"output_text","text":"ready"}]}}

event: response.completed
data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","output":[],"usage":{"input_tokens":1,"output_tokens":1}}}

"""

    data = diagnoser._collect_codex_sse_response(body)

    assert data is not None
    assert data["id"] == "resp_1"
    assert data["output"][0]["content"][0]["text"] == "ready"


def test_candidate_model_order_stays_within_same_provider():
    diagnoser = DeepSeekDiagnoser()
    diagnoser.model_candidates = [
        "deepseek-chat",
        "codex/gpt-5.3-codex",
        "meta/llama-3.1-70b-instruct",
        "deepseek-reasoner",
    ]

    ordered = diagnoser._candidate_model_order("deepseek-chat")

    assert "deepseek-chat" in ordered
    assert "deepseek-reasoner" in ordered
    assert "codex/gpt-5.3-codex" not in ordered
    assert "meta/llama-3.1-70b-instruct" not in ordered


def test_sop_extraction_prompt_discourages_single_incident_specific_objects():
    assert "禁止将单次会话里的具体接口名" in SOP_EXTRACTION_SYSTEM_PROMPT
    assert "<接口>" in SOP_EXTRACTION_SYSTEM_PROMPT
    assert "fallback_commands" in SOP_EXTRACTION_SYSTEM_PROMPT
    assert "输出最终JSON前，必须进行一次自检" in SOP_EXTRACTION_SYSTEM_PROMPT
    assert "<本端设备>" in SOP_EXTRACTION_SYSTEM_PROMPT


def test_next_step_prompt_allows_candidate_repair_plans_when_user_authorizes_scheme_choice():
    assert "自己给合适方案/随机给方案/直接修复/帮我修复" in NEXT_STEP_SYSTEM_PROMPT
    assert "最小假设修复方案" in NEXT_STEP_SYSTEM_PROMPT
    assert "会话模式为config且用户已明确要求修复时，不要机械退回成纯排障模式" in NEXT_STEP_SYSTEM_PROMPT
    assert "在query/diagnosis模式，或目标对象尚未锁定时，优先只读排查命令" in NEXT_STEP_SYSTEM_PROMPT
    assert "当前任务设备范围" in NEXT_STEP_SYSTEM_PROMPT


def test_next_step_payload_trims_commands_and_long_outputs():
    session, commands, evidences = _sample_session_bundle()
    for idx in range(2, 15):
        commands.append(
            CommandExecution(
                session_id=session.id,
                step_no=idx,
                title=f"命令{idx}",
                command=f"show logging {idx}",
                adapter_type=DeviceProtocol.ssh,
                risk_level=RiskLevel.low,
                status=CommandStatus.failed if idx % 4 == 0 else CommandStatus.succeeded,
                output="\n".join(f"line {n} {'x'*40}" for n in range(60)),
                error="% Invalid input" if idx % 4 == 0 else "",
            )
        )

    diagnoser = DeepSeekDiagnoser()
    payload = diagnoser._build_next_step_payload(
        session=session,
        user_problem="检查接口状态",
        commands=commands,
        evidences=evidences,
        iteration=2,
        max_iterations=8,
        planner_context="test",
    )

    assert len(payload["commands"]) <= 10
    assert any("omitted middle lines" in str(item.get("output")) for item in payload["commands"])


def test_history_messages_are_capped_and_clipped():
    diagnoser = DeepSeekDiagnoser()
    messages = [{"role": "user", "content": "x" * 3000} for _ in range(20)]
    normalized = diagnoser._normalize_history_messages(messages)
    assert len(normalized) == 12
    assert all(len(item["content"]) <= 1625 for item in normalized)


def test_final_with_missing_evidence_and_actionable_commands_is_promoted():
    diagnoser = DeepSeekDiagnoser()
    plan = {
        "decision": "final",
        "mode": "diagnosis",
        "root_cause": "证据不足/不确定。尚未执行源端前缀发布性验证，无法确定具体根因。",
        "recommendation": (
            "1. 在设备192.168.0.103上执行'show ip ospf database'。"
            "2. 在设备192.168.0.103上执行'show run section ospf'。"
        ),
    }
    promoted = diagnoser._promote_final_to_run_command_if_actionable(plan, iteration=2, max_iterations=6)
    assert promoted is not None
    assert promoted["plan"]["decision"] == "run_command"
    assert len(promoted["plan"]["commands"]) == 2
    assert promoted["plan"]["commands"][0]["command"] == "show ip ospf database"


def test_final_without_actionable_commands_is_not_promoted():
    diagnoser = DeepSeekDiagnoser()
    plan = {
        "decision": "final",
        "mode": "diagnosis",
        "root_cause": "证据不足/不确定。尚未执行关键验证。",
        "recommendation": "建议联系管理员进一步检查。",
    }
    promoted = diagnoser._promote_final_to_run_command_if_actionable(plan, iteration=2, max_iterations=6)
    assert promoted is None


@pytest.mark.asyncio
async def test_planner_bridge_marks_timeout_in_runtime_health():
    session, commands, evidences = _sample_session_bundle()

    class TimeoutDiagnoser(DeepSeekDiagnoser):
        def __init__(self):
            super().__init__()
            self.api_key = "stub-key"

        async def propose_next_step_with_debug(self, **kwargs):
            await asyncio.sleep(0.05)
            return {"decision": "run_command", "command": "show version"}, {}

    diagnoser = TimeoutDiagnoser()
    bridge = LLMPlannerBridge()
    plan, debug = await bridge.propose_next_step_with_debug(
        diagnoser,
        session=session,
        user_problem="check interface",
        commands=commands,
        evidences=evidences,
        iteration=1,
        max_iterations=2,
        timeout_seconds=0.01,
    )

    assert plan is None
    assert debug["error"] == "llm_timeout"
    assert diagnoser.last_error_code == "llm_timeout"


@pytest.mark.asyncio
async def test_empty_response_marks_runtime_health():
    session, commands, evidences = _sample_session_bundle()
    diagnoser = StubDiagnoser(responses=[""])

    plan, debug = await diagnoser.propose_next_step_with_debug(
        session=session,
        user_problem="check interface",
        commands=commands,
        evidences=evidences,
        iteration=1,
        max_iterations=2,
    )

    assert plan is None
    assert debug["error"] == "empty_response"
    assert diagnoser.last_error_code == "empty_response"
