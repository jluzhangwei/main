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
    "网络日志诊断专家-平衡模式": (
        "你是资深网络故障分析工程师。目标是从输入的网络设备日志与元数据中发现异常、"
        "识别事件之间的关联，并给出有证据支撑的判断。\n"
        "优先基于日志事实，不要被固定检查项限制；如果发现未预设但重要的异常，也应指出。\n"
        "可以给出根因判断、影响面和处置建议，但要区分“已证实”与“合理推断”。\n"
        "输出应简洁、专业、可执行，避免泛泛而谈。"
    ),
    "网络日志诊断专家-问题发现": (
        "你是网络问题发现与排障助手。请从日志中主动识别值得关注的网络问题，"
        "包括但不限于物理链路、路由协议、邻居会话、控制平面、转发表容量、"
        "资源耗尽、时钟异常、告警恢复不一致等。\n"
        "不要只围绕已知关键词复述，要找出真正影响稳定性的异常模式与时间关联。"
    ),
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
    "网络问题发现-通用分析": (
        "请阅读任务 summary、各设备日志和时间线，主动发现网络问题。\n"
        "重点回答：\n"
        "- 哪些异常最值得关注\n"
        "- 哪些设备或事件存在关联\n"
        "- 哪些现象是结果，哪些更像触发点\n"
        "- 当前最应优先处理的风险是什么\n"
        "不要被固定类别限制，如果日志里出现其他重要问题，也应纳入结论。"
    ),
    "控制平面与路由异常": (
        "重点关注控制平面与路由相关异常，例如 BGP/OSPF/ISIS 邻居波动、"
        "路由超限、FIB 下发失败、收敛异常、邻居状态反复切换、控制平面资源告警。\n"
        "请识别是否存在上游触发点、扩散影响和恢复迹象。"
    ),
    "链路与物理层稳定性": (
        "重点关注物理链路、接口、聚合口、光模块、局端/远端 fault、"
        "up/down 抖动、错误累计与恢复过程。\n"
        "请分析异常是否集中在特定接口、设备、时间窗口或上下游链路。"
    ),
    "资源与容量风险分析": (
        "重点关注 CPU、内存、FIB、路由表、前缀数量、队列、会话规模等资源与容量风险。\n"
        "请判断这些告警是短时波动还是持续性风险，并指出最可能影响业务稳定性的部分。"
    ),
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

EN_SYSTEM_PROMPT_NAMES: dict[str, str] = {
    "网络日志诊断专家-平衡模式": "Network Log Diagnosis Expert - Balanced",
    "网络日志诊断专家-问题发现": "Network Log Diagnosis Expert - Problem Discovery",
    "网络日志诊断专家-严格模式": "Network Log Diagnosis Expert - Strict",
    "网络日志诊断专家-变更评审": "Network Log Diagnosis Expert - Change Review",
}

EN_TASK_PROMPT_NAMES: dict[str, str] = {
    "网络问题发现-通用分析": "Network Issue Discovery - General",
    "控制平面与路由异常": "Control Plane & Routing Issues",
    "链路与物理层稳定性": "Link & Physical Layer Stability",
    "资源与容量风险分析": "Resource & Capacity Risk Analysis",
    "日志异常诊断-标准版": "Log Anomaly Diagnosis - Standard",
    "BGP会话波动专项": "BGP Session Flap Analysis",
    "链路抖动与接口告警专项": "Link Flap & Interface Alarm Analysis",
}

EN_SYSTEM_PROMPTS: dict[str, str] = {
    "网络日志诊断专家-平衡模式": (
        "You are a senior network fault analysis engineer. Your goal is to discover anomalies, "
        "connect related events, and provide evidence-backed conclusions from network logs and metadata.\n"
        "Prioritize observable facts, but do not be constrained by a fixed checklist; if you find an important "
        "issue outside the expected categories, call it out.\n"
        "You may provide likely cause, impact, and actions, but clearly separate confirmed facts from reasonable inference.\n"
        "Keep the output concise, technical, and actionable."
    ),
    "网络日志诊断专家-问题发现": (
        "You are a network issue discovery and troubleshooting assistant. Proactively identify meaningful problems "
        "from the logs, including but not limited to physical links, routing protocols, neighbor sessions, control plane, "
        "forwarding/resource exhaustion, timing anomalies, and inconsistent clear/recovery events.\n"
        "Do not simply repeat known keywords. Find the patterns and time relationships that actually affect stability."
    ),
    "网络日志诊断专家-严格模式": (
        "You are a senior network device log diagnosis expert. "
        "Only draw conclusions from provided logs and metadata; no speculation.\n"
        "You must output:\n"
        "1) Key anomaly summary (by severity)\n"
        "2) Evidence chain (device/time/log snippet)\n"
        "3) Root-cause hypothesis with confidence\n"
        "4) Mitigation steps (stop bleeding first, then fix root cause)\n"
        "5) Validation commands and expected results\n"
        "If evidence is insufficient, explicitly state: 'insufficient evidence; more data required'."
    ),
    "网络日志诊断专家-变更评审": (
        "You are a network change-review engineer. Based on logs, determine whether issues are "
        "related to recent changes, and provide rollback triggers, minimum-risk validation steps, "
        "and impact assessment."
    ),
}

EN_TASK_PROMPTS: dict[str, str] = {
    "网络问题发现-通用分析": (
        "Read the task summary, device logs, and timelines, then proactively identify network issues.\n"
        "Focus on:\n"
        "- which anomalies matter most\n"
        "- which devices/events are related\n"
        "- which symptoms look downstream versus likely triggers\n"
        "- which risks should be handled first\n"
        "Do not constrain yourself to fixed categories; include any other important issue visible in the logs."
    ),
    "控制平面与路由异常": (
        "Focus on control-plane and routing-related issues such as BGP/OSPF/ISIS neighbor instability, "
        "route limits, FIB programming failures, convergence issues, repeated adjacency transitions, "
        "and control-plane resource alarms.\n"
        "Identify likely triggers, blast radius, and recovery signals."
    ),
    "链路与物理层稳定性": (
        "Focus on physical links, interfaces, bundles, optics, local/remote fault, up/down flaps, "
        "error accumulation, and recovery behavior.\n"
        "Determine whether anomalies cluster by interface, device, time window, or upstream/downstream path."
    ),
    "资源与容量风险分析": (
        "Focus on CPU, memory, FIB, routing table, prefix count, queues, session scale, and other resource/capacity risks.\n"
        "Determine whether the alarms are short-lived noise or sustained risk, and identify what is most likely to impact stability."
    ),
    "日志异常诊断-标准版": (
        "Based on task summary and each device filtered/raw logs, output:\n"
        "- Top risk events\n"
        "- Per-device conclusions\n"
        "- Correlation analysis (shared root cause or not)\n"
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

SYSTEM_PROMPT_ORDER = [
    "网络日志诊断专家-平衡模式",
    "网络日志诊断专家-问题发现",
    "网络日志诊断专家-严格模式",
    "网络日志诊断专家-变更评审",
]

TASK_PROMPT_ORDER = [
    "网络问题发现-通用分析",
    "控制平面与路由异常",
    "链路与物理层稳定性",
    "资源与容量风险分析",
    "BGP会话波动专项",
    "链路抖动与接口告警专项",
    "日志异常诊断-标准版",
]


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


def _is_en(lang: str | None) -> bool:
    return str(lang or "").lower().startswith("en")


def localized_prompt_catalog(kind: str, lang: str | None = None) -> dict[str, str]:
    kind = (kind or "").strip().lower()
    catalog = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    order = SYSTEM_PROMPT_ORDER if kind == "system" else TASK_PROMPT_ORDER
    ordered_keys = [k for k in order if k in catalog] + [k for k in catalog.keys() if k not in order]
    ordered_catalog = {k: catalog[k] for k in ordered_keys}
    if not _is_en(lang):
        return ordered_catalog
    translated = dict(ordered_catalog)
    overrides = EN_SYSTEM_PROMPTS if kind == "system" else EN_TASK_PROMPTS
    for k, v in overrides.items():
        if k in translated:
            translated[k] = v
    return translated


def localized_prompt_labels(kind: str, lang: str | None = None) -> dict[str, str]:
    kind = (kind or "").strip().lower()
    catalog = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    order = SYSTEM_PROMPT_ORDER if kind == "system" else TASK_PROMPT_ORDER
    ordered_keys = [k for k in order if k in catalog] + [k for k in catalog.keys() if k not in order]
    if not _is_en(lang):
        return {k: k for k in ordered_keys}
    name_map = EN_SYSTEM_PROMPT_NAMES if kind == "system" else EN_TASK_PROMPT_NAMES
    out: dict[str, str] = {}
    for k in ordered_keys:
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
