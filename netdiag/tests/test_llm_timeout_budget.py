from __future__ import annotations

import pytest

from app.ai import llm_client


def test_run_analysis_passes_timeout_to_openai_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    def _fake_call(
        base_url: str,  # noqa: ARG001
        api_key: str,  # noqa: ARG001
        model: str,  # noqa: ARG001
        system_prompt: str,  # noqa: ARG001
        task_prompt: str,  # noqa: ARG001
        report_text: str,  # noqa: ARG001
        timeout_sec: int = 45,
    ):
        captured["timeout_sec"] = int(timeout_sec)
        return "ok", {}

    monkeypatch.setattr(llm_client, "_run_openai_compatible", _fake_call)
    out, usage = llm_client.run_analysis(
        {
            "provider": "chatgpt",
            "api_key": "dummy",
            "chatgpt_model": "gpt-4o-mini",
            "system_prompt_text": "sys",
            "task_prompt_text": "task",
        },
        "report",
        request_timeout_sec=17,
    )
    assert out == "ok"
    assert usage == {}
    assert captured["timeout_sec"] == 17


def test_run_analysis_qwen_splits_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def _fake_call(
        base_url: str,  # noqa: ARG001
        api_key: str,  # noqa: ARG001
        model: str,  # noqa: ARG001
        system_prompt: str,  # noqa: ARG001
        task_prompt: str,  # noqa: ARG001
        report_text: str,  # noqa: ARG001
        timeout_sec: int = 45,
    ):
        calls.append(int(timeout_sec))
        raise RuntimeError("mock timeout")

    monkeypatch.setattr(llm_client, "_run_openai_compatible", _fake_call)
    with pytest.raises(RuntimeError):
        llm_client.run_analysis(
            {
                "provider": "qwen",
                "api_key": "dummy",
                "qwen_model": "qwen-plus",
                "qwen_base_url": llm_client.QWEN_INTL_BASE_URL,
                "system_prompt_text": "sys",
                "task_prompt_text": "task",
            },
            "report",
            request_timeout_sec=12,
        )

    assert calls
    assert len(calls) <= 2
    assert all(5 <= x <= 12 for x in calls)
