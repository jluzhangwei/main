from __future__ import annotations

import asyncio

from app.routers import netdiag as nd
from app.main import app
from fastapi.testclient import TestClient


def test_resolve_llm_route_filters_invalid_entries() -> None:
    route = nd._resolve_llm_route(
        {
            "llm_route": {
                "primary": {"provider": "deepseek", "model": "deepseek-chat"},
                "failover": {"provider": "chatgpt", "model": "gpt-4o-mini"},
                "noise": {"provider": "xxx"},
            }
        }
    )
    assert route.get("primary", {}).get("provider") == "deepseek"
    assert route.get("primary", {}).get("model") == "deepseek-chat"
    assert route.get("failover", {}).get("provider") == "chatgpt"
    assert route.get("failover", {}).get("model") == "gpt-4o-mini"


def test_build_llm_input_applies_selector_provider_model(monkeypatch) -> None:
    monkeypatch.setattr(
        nd,
        "load_gpt_config",
        lambda: {
            "provider": "deepseek",
            "deepseek_api_key": "k-deepseek",
            "chatgpt_api_key": "k-openai",
            "chatgpt_model": "gpt-default",
            "deepseek_model": "deepseek-default",
            "qwen_model": "qwen-default",
            "gemini_model": "gemini-default",
            "nvidia_model": "nvidia-default",
            "local_model": "local-default",
            "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "selected_system_prompt": "sys",
            "selected_task_prompt": "task",
        },
    )
    monkeypatch.setattr(nd, "merged_system_prompt_catalog", lambda: {"sys": "SYS"})
    monkeypatch.setattr(nd, "merged_task_prompt_catalog", lambda: {"task": "TASK"})

    llm = nd._build_llm_input({"provider": "chatgpt", "model": "gpt-4.1-mini"})
    assert llm["provider"] == "chatgpt"
    assert llm["api_key"] == "k-openai"
    assert llm["chatgpt_model"] == "gpt-4.1-mini"
    assert llm["system_prompt_text"] == "SYS"
    assert llm["task_prompt_text"] == "TASK"


def test_llm_retry_uses_failover_when_primary_fails(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_run_analysis(llm_input: dict[str, str], report_text: str, request_timeout_sec: int | None = None):  # noqa: ARG001
        provider = str(llm_input.get("provider") or "")
        calls.append(provider)
        if provider == "deepseek":
            raise RuntimeError("primary failed")
        return "ok by failover", {}

    monkeypatch.setattr(nd, "run_analysis", _fake_run_analysis)

    text, err = asyncio.run(
        nd._run_llm_text_with_retry(
            llm_input={"provider": "deepseek", "deepseek_model": "deepseek-chat"},
            failover_llm_input={"provider": "chatgpt", "chatgpt_model": "gpt-4.1-mini"},
            report_text="demo",
            timeout_sec=12,
            attempts=1,
            failover_attempts=1,
        )
    )
    assert text == "ok by failover"
    assert err == ""
    assert calls == ["deepseek", "chatgpt"]


def test_resolve_llm_runtime_inputs_switches_to_ready_failover() -> None:
    primary = {"provider": "chatgpt", "api_key": "", "chatgpt_model": "gpt-4.1-mini"}
    failover = {"provider": "deepseek", "api_key": "k-ds", "deepseek_model": "deepseek-chat"}
    p, f, diag = nd._resolve_llm_runtime_inputs(primary, failover)
    assert p["provider"] == "deepseek"
    assert f is None
    assert diag["switched_to_failover"] is True
    assert diag["no_ready_model"] is False


def test_resolve_llm_runtime_inputs_marks_no_ready_model() -> None:
    primary = {"provider": "chatgpt", "api_key": "", "chatgpt_model": "gpt-4.1-mini"}
    failover = {"provider": "deepseek", "api_key": "", "deepseek_model": "deepseek-chat"}
    p, f, diag = nd._resolve_llm_runtime_inputs(primary, failover)
    assert p["provider"] == "chatgpt"
    assert f is None
    assert diag["no_ready_model"] is True
    assert "API Key not set" in str(diag.get("unavailable_reason") or "")


def test_llm_route_check_endpoint_switches_to_failover(monkeypatch) -> None:
    c = TestClient(app)
    monkeypatch.setattr(
        nd,
        "load_gpt_config",
        lambda: {
            "provider": "chatgpt",
            "chatgpt_api_key": "",
            "deepseek_api_key": "k-ds",
            "chatgpt_model": "gpt-4.1-mini",
            "deepseek_model": "deepseek-chat",
            "selected_system_prompt": "sys",
            "selected_task_prompt": "task",
        },
    )
    monkeypatch.setattr(nd, "merged_system_prompt_catalog", lambda: {"sys": "SYS"})
    monkeypatch.setattr(nd, "merged_task_prompt_catalog", lambda: {"task": "TASK"})

    resp = c.post(
        "/api/netdiag/llm/route/check",
        json={
            "llm_route": {
                "primary": {"provider": "chatgpt", "model": "gpt-4.1-mini"},
                "failover": {"provider": "deepseek", "model": "deepseek-chat"},
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    runtime = data.get("runtime", {})
    assert runtime.get("switched_to_failover") is True
    assert runtime.get("primary_provider") == "deepseek"


def test_llm_route_check_endpoint_reports_unavailable(monkeypatch) -> None:
    c = TestClient(app)
    monkeypatch.setattr(
        nd,
        "load_gpt_config",
        lambda: {
            "provider": "chatgpt",
            "chatgpt_api_key": "",
            "deepseek_api_key": "",
            "chatgpt_model": "gpt-4.1-mini",
            "deepseek_model": "deepseek-chat",
            "selected_system_prompt": "sys",
            "selected_task_prompt": "task",
        },
    )
    monkeypatch.setattr(nd, "merged_system_prompt_catalog", lambda: {"sys": "SYS"})
    monkeypatch.setattr(nd, "merged_task_prompt_catalog", lambda: {"task": "TASK"})

    resp = c.post(
        "/api/netdiag/llm/route/check",
        json={
            "llm_route": {
                "primary": {"provider": "chatgpt", "model": "gpt-4.1-mini"},
                "failover": {"provider": "deepseek", "model": "deepseek-chat"},
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    runtime = data.get("runtime", {})
    assert runtime.get("no_ready_model") is True
