from __future__ import annotations

from typing import Any

from .prompt_store import localized_prompt_catalog


def normalize_analysis_language(value: Any) -> str:
    return "en" if str(value or "zh").strip().lower().startswith("en") else "zh"


def lang_text(lang: str, zh: str, en: str) -> str:
    return en if normalize_analysis_language(lang) == "en" else zh


def build_llm_input_from_config(cfg: dict[str, Any]) -> dict[str, str]:
    provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
    lang = normalize_analysis_language(cfg.get("analysis_language", "zh"))
    system_prompts = localized_prompt_catalog("system", lang)
    task_prompts = localized_prompt_catalog("task", lang)

    system_key = str(cfg.get("selected_system_prompt") or "网络日志诊断专家-平衡模式")
    task_key = str(cfg.get("selected_task_prompt") or "网络问题发现-通用分析")
    system_base = system_prompts.get(system_key, next(iter(system_prompts.values()), ""))
    task_base = task_prompts.get(task_key, next(iter(task_prompts.values()), ""))

    system_extra = str(cfg.get("system_prompt_extra") or "").strip()
    task_extra = str(cfg.get("task_prompt_extra") or "").strip()

    extra_system_header = lang_text(lang, "[补充系统约束]", "[Extra System Constraints]")
    extra_task_header = lang_text(lang, "[补充任务要求]", "[Extra Task Requirements]")
    language_requirement = lang_text(
        lang,
        "输出语言必须为中文。除原始日志、命令、协议字段、设备名称、英文事件码和必要引用外，不要切换到英文。",
        "Output language must be English. Do not switch to Chinese except when quoting raw log lines, commands, protocol fields, device names, event codes, or necessary evidence.",
    )

    system_text = system_base + (f"\n\n{extra_system_header}\n" + system_extra if system_extra else "")
    task_text = task_base + (f"\n\n{extra_task_header}\n" + task_extra if task_extra else "")
    system_text = system_text.rstrip() + "\n\n" + language_requirement

    api_key = ""
    if provider == "chatgpt":
        api_key = str(cfg.get("chatgpt_api_key") or "")
    elif provider == "deepseek":
        api_key = str(cfg.get("deepseek_api_key") or "")
    elif provider == "qwen":
        api_key = str(cfg.get("qwen_api_key") or "")
    elif provider == "gemini":
        api_key = str(cfg.get("gemini_api_key") or "")
    elif provider == "nvidia":
        api_key = str(cfg.get("nvidia_api_key") or "")

    return {
        "provider": provider,
        "api_key": api_key,
        "chatgpt_model": str(cfg.get("chatgpt_model") or ""),
        "codex_model": str(cfg.get("codex_model") or ""),
        "codex_cli_path": str(cfg.get("codex_cli_path") or ""),
        "local_base_url": str(cfg.get("local_base_url") or ""),
        "local_model": str(cfg.get("local_model") or ""),
        "deepseek_model": str(cfg.get("deepseek_model") or ""),
        "qwen_model": str(cfg.get("qwen_model") or ""),
        "qwen_base_url": str(cfg.get("qwen_base_url") or ""),
        "gemini_model": str(cfg.get("gemini_model") or ""),
        "nvidia_model": str(cfg.get("nvidia_model") or ""),
        "system_prompt_text": system_text,
        "task_prompt_text": task_text,
        "system_prompt_key": system_key,
        "task_prompt_key": task_key,
        "analysis_language": lang,
        "llm_call_timeout_sec": str(cfg.get("llm_call_timeout_sec") or ""),
        "text_compression_strategy": str(cfg.get("text_compression_strategy") or ""),
    }


def compact_runtime_prompt(
    base_task_prompt: str,
    *,
    lang: str,
    scope: str,
    device_label: str,
    device_ip: str,
    device_id: str,
    chunk_index: int | None = None,
    chunk_total: int | None = None,
) -> str:
    is_en = normalize_analysis_language(lang) == "en"
    suffix_lines = [
        "[Runtime mode]" if is_en else "[运行模式]",
        (f"- Scope: {scope}" if is_en else f"- 范围: {scope}"),
        (f"- Device: {device_label}" if is_en else f"- 设备: {device_label}"),
    ]
    if chunk_index is not None and chunk_total is not None:
        suffix_lines.append(f"- Chunk: {chunk_index}/{chunk_total}" if is_en else f"- 分片: {chunk_index}/{chunk_total}")
    suffix_lines.extend(
        [
            "- Output language must be English." if is_en else "- 输出语言必须为中文。",
            "- Output must be compact, evidence-first, and machine-friendly."
            if is_en
            else "- 输出要紧凑、证据优先、便于后续机器汇总。",
            "- Return JSON only. No markdown fences."
            if is_en
            else "- 仅返回 JSON，不要输出 markdown 代码块。",
            '- JSON schema: {"verdict":"...","anomalies":[{"severity":"...","event":"...","object":"...","time_range":"...","reason":"..."}],"evidence":["..."],"actions":["..."]}',
            "- Max anomalies: 4; max evidence: 5; max actions: 3."
            if is_en
            else "- anomalies 最多 4 条，evidence 最多 5 条，actions 最多 3 条。",
            "- Each string should be short; do not restate benign or repetitive details."
            if is_en
            else "- 每个字符串都要尽量短，不要重复描述无害或重复性细节。",
            "- Include exact event code/object/time-range evidence when available."
            if is_en
            else "- 有条件时必须写出准确的事件码、对象和时间范围证据。",
        ]
    )
    return (base_task_prompt or "").rstrip() + "\n\n" + "\n".join(suffix_lines)


def final_runtime_prompt(base_task_prompt: str, *, lang: str) -> str:
    is_en = normalize_analysis_language(lang) == "en"
    suffix_lines = [
        "[Runtime mode]" if is_en else "[运行模式]",
        "- Scope: global_summary" if is_en else "- 范围: global_summary",
        "- Output language must be English." if is_en else "- 输出语言必须为中文。",
        "- Produce the final user-facing report." if is_en else "- 生成最终面向用户的报告。",
        "- Keep the report concise and evidence-first." if is_en else "- 报告保持简洁，证据优先。",
        (
            "- Use exactly these markdown sections: Overall Conclusion, Key Anomalies, Evidence Chain, Impact, Actions."
            if is_en
            else "- 必须严格使用这些 markdown 标题：总体结论、关键异常、证据链、影响判断、处置建议。"
        ),
        "- Keep anomalies max 8 bullets, evidence max 8 bullets, actions max 5 bullets."
        if is_en
        else "- 关键异常最多 8 条，证据链最多 8 条，处置建议最多 5 条。",
        "- Avoid long prose and avoid repeating the same symptom multiple times."
        if is_en
        else "- 不要写成长篇大论，也不要多次重复同一症状。",
    ]
    return (base_task_prompt or "").rstrip() + "\n\n" + "\n".join(suffix_lines)
