#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GPT_CONFIG_PATH = PROJECT_ROOT / "state" / "gpt_config.json"
TOKEN_STATS_PATH = PROJECT_ROOT / "state" / "token_stats.json"

DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_LOCAL_BASE_URL = "http://192.168.0.99:1234"
DEFAULT_LOCAL_MODEL = "qwen/qwen3-coder-30b"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


def load_gpt_config() -> Dict:
    if not GPT_CONFIG_PATH.is_file():
        return {
            "chatgpt_api_key": "",
            "deepseek_api_key": "",
            "custom_prompts": {},
            "provider": "chatgpt",
            "chatgpt_model": DEFAULT_GPT_MODEL,
            "local_base_url": DEFAULT_LOCAL_BASE_URL,
            "local_model": DEFAULT_LOCAL_MODEL,
            "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
            "selected_task_prompt": "",
            "selected_system_prompt": "",
        }
    try:
        data = json.loads(GPT_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid config type")
        chatgpt_api_key = data.get("chatgpt_api_key", data.get("api_key", ""))
        deepseek_api_key = data.get("deepseek_api_key", "")
        custom_prompts = data.get("custom_prompts", {})
        provider = (data.get("provider", "chatgpt") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek"}:
            provider = "chatgpt"
        chatgpt_model = str(data.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL).strip()
        local_base_url = str(data.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = str(data.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL).strip()
        deepseek_model = str(data.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL).strip()
        selected_task_prompt = str(data.get("selected_task_prompt", data.get("selected_prompt", "")) or "").strip()
        selected_system_prompt = str(data.get("selected_system_prompt", "网络工程师-严格模式") or "").strip()
        if not isinstance(custom_prompts, dict):
            custom_prompts = {}
        return {
            "chatgpt_api_key": str(chatgpt_api_key or ""),
            "deepseek_api_key": str(deepseek_api_key or ""),
            "custom_prompts": custom_prompts,
            "provider": provider,
            "chatgpt_model": chatgpt_model,
            "local_base_url": local_base_url,
            "local_model": local_model,
            "deepseek_model": deepseek_model,
            "selected_task_prompt": selected_task_prompt,
            "selected_system_prompt": selected_system_prompt,
        }
    except Exception:
        return {
            "chatgpt_api_key": "",
            "deepseek_api_key": "",
            "custom_prompts": {},
            "provider": "chatgpt",
            "chatgpt_model": DEFAULT_GPT_MODEL,
            "local_base_url": DEFAULT_LOCAL_BASE_URL,
            "local_model": DEFAULT_LOCAL_MODEL,
            "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
            "selected_task_prompt": "",
            "selected_system_prompt": "",
        }


def save_gpt_config(config: Dict) -> None:
    provider = str(config.get("provider", "chatgpt") or "chatgpt").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek"}:
        provider = "chatgpt"
    payload = {
        "chatgpt_api_key": str(config.get("chatgpt_api_key", "") or ""),
        "deepseek_api_key": str(config.get("deepseek_api_key", "") or ""),
        "custom_prompts": config.get("custom_prompts", {}) if isinstance(config.get("custom_prompts", {}), dict) else {},
        "provider": provider,
        "chatgpt_model": str(config.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL).strip(),
        "local_base_url": str(config.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip(),
        "local_model": str(config.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL).strip(),
        "deepseek_model": str(config.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL).strip(),
        "selected_task_prompt": str(config.get("selected_task_prompt", config.get("selected_prompt", "")) or "").strip(),
        "selected_system_prompt": str(config.get("selected_system_prompt", "网络工程师-严格模式") or "").strip(),
    }
    GPT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GPT_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_token_stats() -> Dict:
    default = {"total_tokens": 0, "providers": {"chatgpt": 0, "deepseek": 0, "local": 0}}
    if not TOKEN_STATS_PATH.is_file():
        return default
    try:
        data = json.loads(TOKEN_STATS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        total_tokens = int(data.get("total_tokens", 0) or 0)
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}
        return {
            "total_tokens": max(0, total_tokens),
            "providers": {
                "chatgpt": int(providers.get("chatgpt", 0) or 0),
                "deepseek": int(providers.get("deepseek", 0) or 0),
                "local": int(providers.get("local", 0) or 0),
            },
        }
    except Exception:
        return default


def save_token_stats(stats: Dict) -> None:
    TOKEN_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def add_token_usage(provider: str, used_tokens: int) -> Dict:
    stats = load_token_stats()
    used = max(0, int(used_tokens or 0))
    p = provider if provider in {"chatgpt", "deepseek", "local"} else "local"
    stats["total_tokens"] = int(stats.get("total_tokens", 0) or 0) + used
    providers = stats.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}
    providers[p] = int(providers.get(p, 0) or 0) + used
    stats["providers"] = providers
    save_token_stats(stats)
    return stats
