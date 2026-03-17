from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = PROJECT_ROOT / "state"
GPT_CONFIG_PATH = STATE_DIR / "gpt_config.json"
TOKEN_STATS_PATH = STATE_DIR / "token_stats.json"

DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:1234"
DEFAULT_LOCAL_MODEL = "qwen/qwen3-coder-30b"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_QWEN_MODEL = "qwen-plus"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.1-70b-instruct"
DEFAULT_SYSTEM_PROMPT_KEY = "网络设备故障诊断专家-严格模式"
DEFAULT_TASK_PROMPT_KEY = "网络设备故障诊断-标准版"
LEGACY_SYSTEM_PROMPT_KEYS = {"网络日志诊断专家-严格模式", "网络日志诊断专家-变更评审"}
LEGACY_TASK_PROMPT_KEYS = {"日志异常诊断-标准版"}


def _default_config() -> dict[str, Any]:
    return {
        "chatgpt_api_key": "",
        "deepseek_api_key": "",
        "qwen_api_key": "",
        "gemini_api_key": "",
        "nvidia_api_key": "",
        "custom_prompts": {},
        "provider": "chatgpt",
        "chatgpt_model": DEFAULT_GPT_MODEL,
        "local_base_url": DEFAULT_LOCAL_BASE_URL,
        "local_model": DEFAULT_LOCAL_MODEL,
        "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
        "qwen_model": DEFAULT_QWEN_MODEL,
        "qwen_base_url": DEFAULT_QWEN_BASE_URL,
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "nvidia_model": DEFAULT_NVIDIA_MODEL,
        "selected_task_prompt": DEFAULT_TASK_PROMPT_KEY,
        "selected_system_prompt": DEFAULT_SYSTEM_PROMPT_KEY,
        "system_prompt_extra": "",
        "task_prompt_extra": "",
        "batched_analysis": 0,
        "fragmented_analysis": 0,
        "analysis_parallelism": 2,
        "large_report_chunk_items": 4,
        "max_tokens_per_chunk": 4500,
        "max_chunks_per_device": 12,
        "chunk_strategy": "hybrid",
        "chunk_parallelism": 1,
        "llm_call_timeout_sec": 240,
        "analysis_retries": 1,
    }


def _normalize_prompt_selection(cfg: dict[str, Any]) -> dict[str, Any]:
    system_selected = str(cfg.get("selected_system_prompt", "") or "").strip()
    task_selected = str(cfg.get("selected_task_prompt", "") or "").strip()
    if not system_selected or system_selected in LEGACY_SYSTEM_PROMPT_KEYS:
        cfg["selected_system_prompt"] = DEFAULT_SYSTEM_PROMPT_KEY
    if not task_selected or task_selected in LEGACY_TASK_PROMPT_KEYS:
        cfg["selected_task_prompt"] = DEFAULT_TASK_PROMPT_KEY
    return cfg


def load_gpt_config() -> dict[str, Any]:
    if not GPT_CONFIG_PATH.is_file():
        return _default_config()
    try:
        data = json.loads(GPT_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_config()
        cfg = _default_config()
        cfg.update(data)
        provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
            provider = "chatgpt"
        cfg["provider"] = provider
        before_sys = str(cfg.get("selected_system_prompt", "") or "")
        before_task = str(cfg.get("selected_task_prompt", "") or "")
        cfg = _normalize_prompt_selection(cfg)
        if before_sys != str(cfg.get("selected_system_prompt", "") or "") or before_task != str(cfg.get("selected_task_prompt", "") or ""):
            try:
                save_gpt_config(cfg)
            except Exception:
                pass
        return cfg
    except Exception:
        return _default_config()


def save_gpt_config(config: dict[str, Any]) -> None:
    cfg = _default_config()
    cfg.update(config or {})
    provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        provider = "chatgpt"
    cfg["provider"] = provider
    cfg = _normalize_prompt_selection(cfg)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    GPT_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_token_stats() -> dict[str, Any]:
    default = {"total_tokens": 0, "providers": {"chatgpt": 0, "deepseek": 0, "qwen": 0, "gemini": 0, "nvidia": 0, "local": 0}}
    if not TOKEN_STATS_PATH.is_file():
        return default
    try:
        data = json.loads(TOKEN_STATS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        return data
    except Exception:
        return default


def save_token_stats(stats: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def add_token_usage(provider: str, used_tokens: int) -> dict[str, Any]:
    stats = load_token_stats()
    p = provider if provider in {"chatgpt", "deepseek", "qwen", "gemini", "nvidia", "local"} else "local"
    used = max(0, int(used_tokens or 0))
    stats["total_tokens"] = int(stats.get("total_tokens", 0) or 0) + used
    providers = stats.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}
    providers[p] = int(providers.get(p, 0) or 0) + used
    stats["providers"] = providers
    save_token_stats(stats)
    return stats
