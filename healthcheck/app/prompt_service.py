#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
from pathlib import Path
from typing import Dict

from app.state_store import load_gpt_config, save_gpt_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
SYSTEM_DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "system_default"
SYSTEM_CUSTOM_PROMPTS_DIR = PROMPTS_DIR / "system_custom"
TASK_DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "task_default"
TASK_CUSTOM_PROMPTS_DIR = PROMPTS_DIR / "task_custom"

DEFAULT_SYSTEM_PROMPTS: Dict[str, str] = {
    "网络工程师-严格模式": (
        "你是资深企业网络工程师审计助手。你的输出必须严谨、可执行、可复核。\n"
        "强制规则：\n"
        "1. 仅基于输入报告与日志做判断，不得臆测，不得编造设备状态。\n"
        "2. 每条结论必须给证据：设备/IP + 检查项/命令 + 原始片段摘要。\n"
        "3. 证据不足时必须明确写“证据不足/需补采集”。\n"
        "4. 风险分级仅允许：高/中/低；置信度仅允许：高/中/低。\n"
        "5. 优先给可执行步骤，先止血再根治；避免破坏性建议作为第一步。\n"
        "6. 输出中文，术语专业，避免空泛套话。\n"
        "输出结构固定：\n"
        "【总体结论】\n"
        "【关键异常】\n"
        "【证据链】\n"
        "【根因判断】\n"
        "【处置计划（优先级）】\n"
        "【复核命令与通过标准】\n"
    ),
    "网络工程师-变更评审模式": (
        "你是网络变更评审工程师。请重点识别变更相关风险，并给出回退友好建议。\n"
        "要求：\n"
        "1. 区分“现网告警”与“疑似变更引入”两类问题。\n"
        "2. 每条问题必须有证据引用；无证据则标记待确认。\n"
        "3. 建议中必须包含：变更前检查、变更后验证、回退触发条件。\n"
        "4. 不提供高风险操作的一步到位命令，必须先做验证步骤。\n"
    ),
}

DEFAULT_TASK_PROMPTS: Dict[str, str] = {
    "基础巡检诊断": (
        "请对本次巡检结果做全量健康评估：\n"
        "1. 汇总高风险问题 TopN\n"
        "2. 给出每个问题的业务影响\n"
        "3. 给出排障优先级与执行顺序\n"
        "4. 给出修复后复核项与阈值"
    ),
    "接口与链路诊断": (
        "请聚焦接口与链路：\n"
        "1. 列出异常接口（Physical/Protocol不一致、error/丢包/抖动）\n"
        "2. 判断是否涉及上联/核心路径\n"
        "3. 给出逐步排查命令顺序（至少5步）"
    ),
    "路由与协议诊断": (
        "请聚焦路由与协议：\n"
        "1. 分析 BGP/OSPF/NTP/STP 的健康度\n"
        "2. 指出邻居抖动、收敛异常、路由缺失/泄漏风险\n"
        "3. 给出可执行修复建议与观察指标"
    ),
    "性能与资源诊断": (
        "请聚焦资源瓶颈：\n"
        "1. 识别 CPU/内存/温度/风扇/电源异常\n"
        "2. 评估是否影响控制平面或转发稳定性\n"
        "3. 给出处置优先级与容量优化建议"
    ),
}


def sanitize_prompt_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        return ""
    cleaned = cleaned[:40]
    cleaned = re.sub(r"[^\w\u4e00-\u9fff ._-]", "", cleaned)
    return cleaned.strip()


def ensure_prompt_dirs() -> None:
    SYSTEM_DEFAULT_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEM_CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DEFAULT_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    TASK_CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_prompt_dirs() -> None:
    legacy_default = PROMPTS_DIR / "default"
    legacy_custom = PROMPTS_DIR / "custom"
    if legacy_default.is_dir():
        for src in legacy_default.glob("*.txt"):
            target = TASK_DEFAULT_PROMPTS_DIR / src.name
            if not target.exists():
                try:
                    target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
    if legacy_custom.is_dir():
        for src in legacy_custom.glob("*.txt"):
            target = TASK_CUSTOM_PROMPTS_DIR / src.name
            if not target.exists():
                try:
                    target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass


def prompt_file_name(name: str) -> str:
    cleaned = sanitize_prompt_name(name)
    if not cleaned:
        return ""
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", cleaned)
    safe = re.sub(r"\s+", "_", safe).strip("._")
    return safe[:80] + ".txt" if safe else ""


def write_prompt_file(prompt_dir: Path, prompt_name: str, content: str) -> bool:
    filename = prompt_file_name(prompt_name)
    text = str(content or "").strip()
    if not filename or not text:
        return False
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / filename).write_text(text + "\n", encoding="utf-8")
    return True


def load_prompt_dir(prompt_dir: Path) -> Dict[str, str]:
    if not prompt_dir.is_dir():
        return {}
    prompts: Dict[str, str] = {}
    for path in sorted(prompt_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            prompts[path.stem.replace("_", " ")] = text
    return prompts


def initialize_default_prompt_files() -> None:
    ensure_prompt_dirs()
    migrate_legacy_prompt_dirs()
    for name, content in DEFAULT_SYSTEM_PROMPTS.items():
        target = SYSTEM_DEFAULT_PROMPTS_DIR / prompt_file_name(name)
        if not target.is_file():
            target.write_text(str(content).strip() + "\n", encoding="utf-8")
    for name, content in DEFAULT_TASK_PROMPTS.items():
        target = TASK_DEFAULT_PROMPTS_DIR / prompt_file_name(name)
        if not target.is_file():
            target.write_text(str(content).strip() + "\n", encoding="utf-8")


def merged_prompt_catalog(default_prompts: Dict[str, str], default_dir: Path, custom_dir: Path) -> Dict[str, str]:
    config = load_gpt_config()
    ensure_prompt_dirs()

    default_from_files = load_prompt_dir(default_dir)
    merged = dict(default_from_files) if default_from_files else dict(default_prompts)

    custom_from_files = load_prompt_dir(custom_dir)
    for key, value in custom_from_files.items():
        if key and value.strip():
            merged[key] = value.strip()

    custom = config.get("custom_prompts", {}) if isinstance(config.get("custom_prompts"), dict) else {}
    for key, value in custom.items():
        if key and isinstance(value, str) and value.strip():
            clean_key = sanitize_prompt_name(str(key))
            merged[clean_key] = value.strip()
            write_prompt_file(TASK_CUSTOM_PROMPTS_DIR, clean_key, value.strip())

    if custom:
        config["custom_prompts"] = {}
        save_gpt_config(config)
    return merged


def merged_task_prompt_catalog() -> Dict[str, str]:
    return merged_prompt_catalog(DEFAULT_TASK_PROMPTS, TASK_DEFAULT_PROMPTS_DIR, TASK_CUSTOM_PROMPTS_DIR)


def merged_system_prompt_catalog() -> Dict[str, str]:
    return merged_prompt_catalog(DEFAULT_SYSTEM_PROMPTS, SYSTEM_DEFAULT_PROMPTS_DIR, SYSTEM_CUSTOM_PROMPTS_DIR)


def prompt_catalog_by_kind(kind: str) -> Dict[str, str]:
    return merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
