from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .state_store import load_gpt_config, save_gpt_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
SYSTEM_DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "system_default"
SYSTEM_CUSTOM_PROMPTS_DIR = PROMPTS_DIR / "system_custom"
TASK_DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "task_default"
TASK_CUSTOM_PROMPTS_DIR = PROMPTS_DIR / "task_custom"

DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "网络日志诊断专家-严格模式": (
        "你是资深网络设备日志诊断专家。仅基于输入日志与元数据给出结论，不得臆测。\n"
        "必须输出：\n"
        "1) 关键异常摘要（按严重度）\n"
        "2) 证据链（设备/时间/日志片段）\n"
        "3) 根因假设与置信度\n"
        "4) 处置步骤（先止血后根治）\n"
        "5) 复核命令与预期结果\n"
        "若证据不足，明确写“证据不足，需补充采集”。"
    ),
    "网络日志诊断专家-变更评审": (
        "你是网络变更评审工程师。请基于日志判断是否与近期变更相关，\n"
        "并给出回退触发条件、最小风险验证步骤和影响评估。"
    ),
}

DEFAULT_TASK_PROMPTS: dict[str, str] = {
    "日志异常诊断-标准版": (
        "请基于任务 summary 与各设备 filtered/raw 日志，输出：\n"
        "- Top 风险事件\n"
        "- 逐设备结论\n"
        "- 关联性分析（是否同因）\n"
        "- 优先级处置清单"
    ),
    "BGP会话波动专项": (
        "重点识别 BGP 邻居 flap/holdtime expired/max-prefix 等事件，\n"
        "分析影响范围并给出稳定性恢复步骤。"
    ),
    "链路抖动与接口告警专项": (
        "重点识别 link up/down、CRC/error、聚合链路状态切换，\n"
        "给出链路稳定性排查路径与复核步骤。"
    ),
}


def sanitize_prompt_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        return ""
    cleaned = cleaned[:40]
    cleaned = re.sub(r"[^\w\u4e00-\u9fff ._-]", "", cleaned)
    return cleaned.strip()


def prompt_file_name(name: str) -> str:
    cleaned = sanitize_prompt_name(name)
    if not cleaned:
        return ""
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", cleaned)
    safe = re.sub(r"\s+", "_", safe).strip("._")
    return safe[:80] + ".txt" if safe else ""


def ensure_prompt_dirs() -> None:
    SYSTEM_DEFAULT_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEM_CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DEFAULT_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    TASK_CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)


def _write_if_missing(target: Path, content: str) -> None:
    if not target.is_file():
        target.write_text(content.strip() + "\n", encoding="utf-8")


def initialize_default_prompt_files() -> None:
    ensure_prompt_dirs()
    for name, content in DEFAULT_SYSTEM_PROMPTS.items():
        _write_if_missing(SYSTEM_DEFAULT_PROMPTS_DIR / prompt_file_name(name), content)
    for name, content in DEFAULT_TASK_PROMPTS.items():
        _write_if_missing(TASK_DEFAULT_PROMPTS_DIR / prompt_file_name(name), content)


def _load_prompt_dir(prompt_dir: Path) -> dict[str, str]:
    if not prompt_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in sorted(prompt_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            out[path.stem.replace("_", " ")] = text
    return out


def merged_prompt_catalog(default_prompts: dict[str, str], default_dir: Path, custom_dir: Path) -> dict[str, str]:
    initialize_default_prompt_files()
    merged = _load_prompt_dir(default_dir) or dict(default_prompts)
    merged.update(_load_prompt_dir(custom_dir))

    cfg = load_gpt_config()
    custom = cfg.get("custom_prompts", {}) if isinstance(cfg.get("custom_prompts"), dict) else {}
    if custom:
        for key, value in custom.items():
            if key and isinstance(value, str) and value.strip():
                merged[sanitize_prompt_name(str(key))] = value.strip()
        cfg["custom_prompts"] = {}
        save_gpt_config(cfg)
    return merged


def merged_system_prompt_catalog() -> dict[str, str]:
    return merged_prompt_catalog(DEFAULT_SYSTEM_PROMPTS, SYSTEM_DEFAULT_PROMPTS_DIR, SYSTEM_CUSTOM_PROMPTS_DIR)


def merged_task_prompt_catalog() -> dict[str, str]:
    return merged_prompt_catalog(DEFAULT_TASK_PROMPTS, TASK_DEFAULT_PROMPTS_DIR, TASK_CUSTOM_PROMPTS_DIR)


def save_custom_prompt(kind: str, name: str, content: str) -> str:
    ensure_prompt_dirs()
    prompt_kind = (kind or "").strip().lower()
    prompt_name = sanitize_prompt_name(name)
    text = (content or "").strip()
    if prompt_kind not in {"system", "task"}:
        raise ValueError("kind must be system or task")
    if not prompt_name:
        raise ValueError("prompt name is required")
    if not text:
        raise ValueError("prompt content is empty")
    file_name = prompt_file_name(prompt_name)
    if not file_name:
        raise ValueError("invalid prompt name")
    target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
    target = target_dir / file_name
    target.write_text(text + "\n", encoding="utf-8")
    return prompt_name
