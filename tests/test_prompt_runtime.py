from app.ai.prompt_runtime import (
    build_llm_input_from_config,
    compact_runtime_prompt,
    final_runtime_prompt,
    normalize_analysis_language,
)


def test_normalize_analysis_language_maps_en_and_zh():
    assert normalize_analysis_language("en") == "en"
    assert normalize_analysis_language("en-US") == "en"
    assert normalize_analysis_language("zh") == "zh"
    assert normalize_analysis_language("zh-CN") == "zh"
    assert normalize_analysis_language(None) == "zh"


def test_build_llm_input_from_config_uses_localized_prompt_catalog():
    cfg = {
        "provider": "chatgpt",
        "analysis_language": "en",
        "selected_system_prompt": "网络日志诊断专家-平衡模式",
        "selected_task_prompt": "网络问题发现-通用分析",
        "system_prompt_extra": "Keep findings actionable.",
        "task_prompt_extra": "Focus on routing symptoms.",
        "chatgpt_api_key": "k-test",
        "chatgpt_model": "gpt-test",
    }
    llm = build_llm_input_from_config(cfg)
    assert llm["provider"] == "chatgpt"
    assert llm["api_key"] == "k-test"
    assert llm["analysis_language"] == "en"
    assert "[Extra System Constraints]" in llm["system_prompt_text"]
    assert "Output language must be English." in llm["system_prompt_text"]
    assert "[Extra Task Requirements]" in llm["task_prompt_text"]
    assert "Focus on routing symptoms." in llm["task_prompt_text"]


def test_compact_runtime_prompt_contains_runtime_scope_and_json_contract():
    out = compact_runtime_prompt(
        "Base prompt",
        lang="zh",
        scope="device_summary",
        device_label="R1",
        device_ip="10.0.0.1",
        device_id="dev-1",
        chunk_index=1,
        chunk_total=3,
    )
    assert "Base prompt" in out
    assert "[运行模式]" in out
    assert "- 范围: device_summary" in out
    assert "- 分片: 1/3" in out
    assert "仅返回 JSON" in out


def test_final_runtime_prompt_uses_expected_english_sections():
    out = final_runtime_prompt("Base prompt", lang="en")
    assert "Base prompt" in out
    assert "[Runtime mode]" in out
    assert "Scope: global_summary" in out
    assert "Overall Conclusion, Key Anomalies, Evidence Chain, Impact, Actions" in out
