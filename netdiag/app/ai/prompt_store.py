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
    "网络设备故障诊断专家-严格模式": (
        "你是资深网络设备故障诊断专家。仅基于输入日志/命令回显/监控信号与元数据给出结论，不得臆测。\n"
        "必须输出：\n"
        "1) 关键异常摘要（按严重度）\n"
        "2) 证据链（设备/时间/命令或日志片段）\n"
        "3) 根因假设与置信度\n"
        "4) 下一步诊断动作（仅只读 show/display）\n"
        "5) 处置建议（先止血后根治）\n"
        "6) 时间维度校验（故障时间窗与设备时间/时区是否一致）\n"
        "若证据不足，明确写“证据不足，需补充采集”。"
    ),
}

DEFAULT_TASK_PROMPTS: dict[str, str] = {
    "网络设备故障诊断-标准版": (
        "请基于会话问题描述、时间窗、设备执行回显、监控信号，输出：\n"
        "- Top 风险事件\n"
        "- 逐设备结论\n"
        "- 关联性分析（是否同因）\n"
        "- 下一步诊断命令建议（只读 show/display）\n"
        "- 时间维度校验结论（校准偏移/时区/证据时间是否落在故障窗）\n"
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

EN_SYSTEM_PROMPT_NAMES: dict[str, str] = {
    "网络设备故障诊断专家-严格模式": "Network Device Fault Diagnosis Expert - Strict",
}

EN_TASK_PROMPT_NAMES: dict[str, str] = {
    "网络设备故障诊断-标准版": "Network Device Fault Diagnosis - Standard",
    "BGP会话波动专项": "BGP Session Flap Analysis",
    "链路抖动与接口告警专项": "Link Flap & Interface Alarm Analysis",
}

EN_SYSTEM_PROMPTS: dict[str, str] = {
    "网络设备故障诊断专家-严格模式": (
        "You are a senior network device fault-diagnosis expert. "
        "Only draw conclusions from provided logs/CLI outputs/monitoring signals and metadata; no speculation.\n"
        "You must output:\n"
        "1) Key anomaly summary (by severity)\n"
        "2) Evidence chain (device/time/command or log snippet)\n"
        "3) Root-cause hypothesis with confidence\n"
        "4) Next diagnostic actions (read-only show/display only)\n"
        "5) Mitigation suggestions (stop bleeding first, then fix root cause)\n"
        "6) Time-dimension validation (fault window vs device time/timezone consistency)\n"
        "If evidence is insufficient, explicitly state: 'insufficient evidence; more data required'."
    ),
}

EN_TASK_PROMPTS: dict[str, str] = {
    "网络设备故障诊断-标准版": (
        "Based on session problem statement, fault time window, device command outputs, and monitoring signals, output:\n"
        "- Top risk events\n"
        "- Per-device conclusions\n"
        "- Correlation analysis (shared root cause or not)\n"
        "- Next read-only diagnostic commands (show/display)\n"
        "- Time-dimension validation result (clock offset/timezone/evidence-in-window)\n"
        "- Prioritized action list"
    ),
    "BGP会话波动专项": (
        "Focus on BGP neighbor flap/holdtime expired/max-prefix events, assess impact scope, "
        "and provide stabilization/recovery steps."
    ),
    "链路抖动与接口告警专项": (
        "Focus on link up/down, CRC/error, and port-channel state transitions. "
        "Provide troubleshooting path and validation steps for link stability."
    ),
}

DEPRECATED_PROMPT_KEYS: set[str] = {
    "网络日志诊断专家-严格模式",
    "网络日志诊断专家-变更评审",
    "日志异常诊断-标准版",
}


def _is_deprecated_prompt_key(name: str) -> bool:
    return sanitize_prompt_name(name) in DEPRECATED_PROMPT_KEYS


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
    # Remove deprecated built-in templates from default directories.
    for name in DEPRECATED_PROMPT_KEYS:
        file_name = prompt_file_name(name)
        if not file_name:
            continue
        (SYSTEM_DEFAULT_PROMPTS_DIR / file_name).unlink(missing_ok=True)
        (TASK_DEFAULT_PROMPTS_DIR / file_name).unlink(missing_ok=True)


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
            key = path.stem.replace("_", " ")
            if _is_deprecated_prompt_key(key):
                continue
            out[key] = text
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
                k = sanitize_prompt_name(str(key))
                if not k or _is_deprecated_prompt_key(k):
                    continue
                merged[k] = value.strip()
        cfg["custom_prompts"] = {}
        save_gpt_config(cfg)
    return merged


def merged_system_prompt_catalog() -> dict[str, str]:
    return merged_prompt_catalog(DEFAULT_SYSTEM_PROMPTS, SYSTEM_DEFAULT_PROMPTS_DIR, SYSTEM_CUSTOM_PROMPTS_DIR)


def merged_task_prompt_catalog() -> dict[str, str]:
    return merged_prompt_catalog(DEFAULT_TASK_PROMPTS, TASK_DEFAULT_PROMPTS_DIR, TASK_CUSTOM_PROMPTS_DIR)


def _is_en(lang: str | None) -> bool:
    return str(lang or "").lower().startswith("en")


def localized_prompt_catalog(kind: str, lang: str | None = None) -> dict[str, str]:
    kind = (kind or "").strip().lower()
    catalog = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    if not _is_en(lang):
        return catalog
    translated = dict(catalog)
    overrides = EN_SYSTEM_PROMPTS if kind == "system" else EN_TASK_PROMPTS
    for k, v in overrides.items():
        if k in translated:
            translated[k] = v
    return translated


def localized_prompt_labels(kind: str, lang: str | None = None) -> dict[str, str]:
    kind = (kind or "").strip().lower()
    catalog = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    if not _is_en(lang):
        return {k: k for k in catalog.keys()}
    name_map = EN_SYSTEM_PROMPT_NAMES if kind == "system" else EN_TASK_PROMPT_NAMES
    out: dict[str, str] = {}
    for k in catalog.keys():
        out[k] = name_map.get(k, k)
    return out


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


def _prompt_dirs(kind: str) -> tuple[Path, Path]:
    prompt_kind = (kind or "").strip().lower()
    if prompt_kind == "system":
        return SYSTEM_DEFAULT_PROMPTS_DIR, SYSTEM_CUSTOM_PROMPTS_DIR
    if prompt_kind == "task":
        return TASK_DEFAULT_PROMPTS_DIR, TASK_CUSTOM_PROMPTS_DIR
    raise ValueError("kind must be system or task")


def is_custom_prompt(kind: str, name: str) -> bool:
    ensure_prompt_dirs()
    prompt_name = sanitize_prompt_name(name)
    if not prompt_name:
        return False
    _, custom_dir = _prompt_dirs(kind)
    f = prompt_file_name(prompt_name)
    if not f:
        return False
    return (custom_dir / f).is_file()


def delete_custom_prompt(kind: str, name: str) -> bool:
    ensure_prompt_dirs()
    prompt_name = sanitize_prompt_name(name)
    if not prompt_name:
        raise ValueError("prompt name is required")
    _, custom_dir = _prompt_dirs(kind)
    f = prompt_file_name(prompt_name)
    if not f:
        raise ValueError("invalid prompt name")
    target = custom_dir / f
    if not target.is_file():
        return False
    target.unlink(missing_ok=True)
    return True
