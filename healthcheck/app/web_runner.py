#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import cgi
import base64
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
SCRIPT_PATH = APP_DIR / "healthcheck.py"
INTENTS_PATH = PROJECT_ROOT / "data" / "intents.txt"
REPORT_DIR = PROJECT_ROOT / "output" / "reports"
GPT_CONFIG_PATH = PROJECT_ROOT / "state" / "gpt_config.json"
TMP_DIR = PROJECT_ROOT / "runtime" / "tmp"
COMMAND_MAP_PATH = PROJECT_ROOT / "config" / "command_map.yaml"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "default"
CUSTOM_PROMPTS_DIR = PROMPTS_DIR / "custom"
DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_LOCAL_BASE_URL = "http://192.168.0.99:1234"
DEFAULT_LOCAL_MODEL = "qwen/qwen3-coder-30b"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
CHATGPT_MODEL_OPTIONS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "o1",
    "o1-mini",
    "o3",
    "o3-mini",
]
DEEPSEEK_MODEL_OPTIONS = [
    "deepseek-chat",
    "deepseek-reasoner",
]
LOCAL_MODEL_OPTIONS = [
    "deepseek-r1-distill-llama-70b",
    "deepseek-r1-distill-qwen-32b",
    "gemma-2-27b-it",
    "llama-3.1-70b-instruct",
    "llama-3.1-8b-instruct",
    "mistral-large-instruct",
    "qwen/qwen2.5-72b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "qwen/qwen3-coder-30b",
]
MAX_HISTORY_REPORT_BYTES = 2 * 1024 * 1024
JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()

DEFAULT_NETWORK_PROMPTS: Dict[str, str] = {
    "基础巡检诊断": (
        "你是企业网络运维专家。请基于巡检日志和结构化报告做健康诊断，并严格按以下结构输出：\n"
        "【1. 总体结论】\n"
        "- 健康等级：健康/一般/高风险\n"
        "- 结论摘要：不超过120字\n"
        "【2. 关键异常】\n"
        "- 列出最重要的3~8个异常，格式：设备/IP | 检查项 | 现象 | 影响等级(高/中/低)\n"
        "【3. 可能根因】\n"
        "- 针对每个高/中风险异常给出可能根因（配置、链路、协议邻居、资源瓶颈、时钟、硬件）\n"
        "【4. 处置建议】\n"
        "- 给出按优先级排序的可执行步骤（先止血、后根治），每步包含命令建议或检查点\n"
        "【5. 复核与监控建议】\n"
        "- 给出修复后需复核的命令与关键指标阈值\n"
        "要求：结论要具体，避免空泛表述；如果信息不足，明确指出缺失数据。"
    ),
    "接口与链路诊断": (
        "你是网络接口与链路故障专家。请聚焦接口与链路稳定性，输出结构如下：\n"
        "【1. 异常接口清单】\n"
        "- 逐条列出：设备/IP | 接口名 | Physical/Protocol 状态 | 错误/丢包/抖动迹象 | 风险等级\n"
        "【2. 影响范围评估】\n"
        "- 识别上联/核心链路/业务口，判断是否可能影响生产业务\n"
        "【3. 根因判断】\n"
        "- 从配置问题、速率双工不一致、光模块/光功率异常、链路抖动、聚合状态异常等角度判断\n"
        "【4. 排查顺序（最少5步）】\n"
        "- 按优先级给出可执行排查流程，包含建议命令\n"
        "【5. 修复建议】\n"
        "- 提供短期缓解与长期整改两类建议\n"
        "如果接口数量很多，请先汇总高风险接口TOP N，再给总体结论。"
    ),
    "路由与协议诊断": (
        "你是路由与控制平面诊断专家。请重点分析路由、BGP/OSPF、NTP、STP 等协议状态：\n"
        "【1. 协议健康概览】\n"
        "- 分协议给出健康状态：正常/告警/异常\n"
        "【2. 潜在协议异常】\n"
        "- 列出邻居抖动、路由收敛异常、路由缺失/泄漏、时钟不同步、环路风险等现象\n"
        "【3. 业务影响判断】\n"
        "- 说明是否可能导致中断、绕路、时延抖动或安全风险\n"
        "【4. 修复与优化建议】\n"
        "- 给出具体操作建议（优先修复项、回退建议、观察指标）\n"
        "【5. 后续观察清单】\n"
        "- 列出建议持续监控的关键KPI（邻居稳定性、路由条目变化、CPU/内存、时钟偏差）\n"
        "输出应偏工程落地，避免泛化描述。"
    ),
}


def ensure_prompt_dirs() -> None:
    DEFAULT_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)


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
    for name, content in DEFAULT_NETWORK_PROMPTS.items():
        target = DEFAULT_PROMPTS_DIR / prompt_file_name(name)
        if not target.is_file():
            target.write_text(str(content).strip() + "\n", encoding="utf-8")


def load_default_checks() -> List[str]:
    checks: List[str] = []
    if not INTENTS_PATH.is_file():
        return checks

    for line in INTENTS_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("@"):
            checks.append(raw)
        else:
            checks.append(f"@{raw}")
    return checks


DEFAULT_CHECKS = load_default_checks()

CHECK_CATEGORIES: List[tuple] = [
    ("设备软件层", {"@version", "@uptime", "@cpu_usage", "@memory_usage", "@running_config", "@log_recent", "@ntp_status"}),
    ("设备硬件层", {"@environmental", "@power_status", "@fan_status", "@transceiver", "@alarm_active"}),
    ("协议层面", {"@route_summary", "@bgp_summary", "@ospf_summary", "@spanning_tree", "@lldp_neighbors"}),
    ("端口层面", {"@interface_brief", "@interface_errors", "@arp_table", "@mac_table", "@acl_summary"}),
    ("更多分类", set()),
]


def default_form_values() -> Dict[str, str]:
    return {
        "username": "",
        "password": "",
        "devices": "",
        "custom_commands": "",
        "execution_mode": "auto",
        "parallel_workers": "",
        "connect_retry": "0",
        "debug_mode": "",
    }


def list_report_files() -> List[Path]:
    if not REPORT_DIR.is_dir():
        return []
    files = [
        p
        for p in REPORT_DIR.iterdir()
        if p.is_file() and p.name.startswith("inspection_report_") and p.suffix.lower() in {".json", ".csv"}
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def get_latest_report_by_suffix(suffix: str) -> Path:
    for path in list_report_files():
        if path.suffix.lower() == suffix:
            return path
    return Path()


def is_safe_report_name(name: str) -> bool:
    if not name:
        return False
    if name != os.path.basename(name):
        return False
    return bool(re.match(r"^inspection_report_[A-Za-z0-9_]+\.(json|csv)$", name))


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
            "selected_prompt": "",
        }
    try:
        data = json.loads(GPT_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {
                "chatgpt_api_key": "",
                "deepseek_api_key": "",
                "custom_prompts": {},
                "provider": "chatgpt",
                "chatgpt_model": DEFAULT_GPT_MODEL,
                "local_base_url": DEFAULT_LOCAL_BASE_URL,
                "local_model": DEFAULT_LOCAL_MODEL,
                "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
                "selected_prompt": "",
            }
        # Backward compatibility: old "api_key" is treated as chatgpt_api_key.
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
        selected_prompt = str(data.get("selected_prompt", "") or "").strip()
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
            "selected_prompt": selected_prompt,
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
            "selected_prompt": "",
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
        "selected_prompt": str(config.get("selected_prompt", "") or "").strip(),
    }
    GPT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GPT_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sanitize_prompt_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        return ""
    cleaned = cleaned[:40]
    cleaned = re.sub(r"[^\w\u4e00-\u9fff ._-]", "", cleaned)
    return cleaned.strip()


def merged_prompt_catalog() -> Dict[str, str]:
    config = load_gpt_config()
    ensure_prompt_dirs()

    # 1) Default templates: prefer files, fallback to built-ins.
    default_from_files = load_prompt_dir(DEFAULT_PROMPTS_DIR)
    merged = dict(default_from_files) if default_from_files else dict(DEFAULT_NETWORK_PROMPTS)

    # 2) Custom templates from files.
    custom_from_files = load_prompt_dir(CUSTOM_PROMPTS_DIR)
    for key, value in custom_from_files.items():
        if key and value.strip():
            merged[key] = value.strip()

    # 3) Backward compatibility: old inline templates in gpt_config.json.
    custom = config.get("custom_prompts", {}) if isinstance(config.get("custom_prompts"), dict) else {}
    for key, value in custom.items():
        if key and isinstance(value, str) and value.strip():
            clean_key = sanitize_prompt_name(str(key))
            merged[clean_key] = value.strip()
            # Best-effort migration to prompts/custom directory.
            write_prompt_file(CUSTOM_PROMPTS_DIR, clean_key, value.strip())

    # Clean legacy in-config templates after migration.
    if custom:
        config["custom_prompts"] = {}
        save_gpt_config(config)
    return merged


def parse_openai_response_text(payload: Dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text", "").strip():
        return payload["output_text"].strip()

    outputs = payload.get("output", [])
    if isinstance(outputs, list):
        texts: List[str] = []
        for item in outputs:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str) and t.strip():
                        texts.append(t.strip())
        if texts:
            return "\n\n".join(texts)
    return ""


def build_openai_ssl_context() -> ssl.SSLContext:
    no_verify = os.environ.get("OPENAI_SSL_NO_VERIFY", "").strip() in {"1", "true", "yes", "on"}
    ssl_ctx = ssl.create_default_context()
    if no_verify:
        return ssl._create_unverified_context()  # nosec B323
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl_ctx


def call_openai_analysis(api_key: str, prompt_text: str, report_text: str, model: str = DEFAULT_GPT_MODEL) -> str:
    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "你是资深网络运维专家，输出要结构化、可落地。"}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"诊断要求：\n{prompt_text}\n\n巡检数据：\n{report_text}",
                    }
                ],
            },
        ],
    }
    req = urlrequest.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    ssl_ctx = build_openai_ssl_context()

    try:
        with urlrequest.urlopen(req, timeout=120, context=ssl_ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "SSL certificate verify failed. "
            "请先执行: pip3 install certifi；"
            "macOS 可再执行: /Applications/Python\\ 3.9/Install\\ Certificates.command"
        ) from exc
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"OpenAI API response parse failed: {exc}") from exc

    text = parse_openai_response_text(payload)
    if not text:
        raise RuntimeError("OpenAI API returned empty analysis text")
    return text


def test_openai_connection(api_key: str) -> str:
    req = urlrequest.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    ssl_ctx = build_openai_ssl_context()
    try:
        with urlrequest.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "SSL certificate verify failed. "
            "请先执行: pip3 install certifi；"
            "macOS 可再执行: /Applications/Python\\ 3.9/Install\\ Certificates.command"
        ) from exc
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
        return f"OpenAI 连接成功，models={count}"
    except Exception:
        return "OpenAI 连接成功"


def call_deepseek_analysis(api_key: str, prompt_text: str, report_text: str, model: str = DEFAULT_DEEPSEEK_MODEL) -> str:
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你是资深网络运维专家，输出要结构化、可落地。"},
            {"role": "user", "content": f"诊断要求：\n{prompt_text}\n\n巡检数据：\n{report_text}"},
        ],
    }
    req = urlrequest.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        choices = payload.get("choices", [])
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, str) and content.strip():
                return content.strip()
    except Exception:
        pass
    raise RuntimeError("DeepSeek API returned empty analysis text")


def test_deepseek_connection(api_key: str) -> str:
    req = urlrequest.Request(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
        return f"DeepSeek 连接成功，models={count}"
    except Exception:
        return "DeepSeek 连接成功"


def call_local_lmstudio_analysis(base_url: str, model: str, prompt_text: str, report_text: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("LM Studio base_url is empty")
    if not model.strip():
        raise RuntimeError("LM Studio model is empty")

    body = {
        "model": model.strip(),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你是资深网络运维专家，输出要结构化、可落地。"},
            {"role": "user", "content": f"诊断要求：\n{prompt_text}\n\n巡检数据：\n{report_text}"},
        ],
    }
    req = urlrequest.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio API HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"LM Studio API request failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"LM Studio response parse failed: {exc}") from exc

    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise RuntimeError("LM Studio returned empty analysis text")


def test_local_lmstudio_connection(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("LM Studio base_url is empty")
    req = urlrequest.Request(f"{base}/v1/models", method="GET")
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"LM Studio API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        data = payload.get("data", [])
        count = len(data) if isinstance(data, list) else 0
        return f"LM Studio 连接成功，models={count}"
    except Exception:
        return "LM Studio 连接成功"


def decode_best_effort_text(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except Exception:
        pass
    try:
        return raw.decode("gb18030")
    except Exception:
        return raw.decode("utf-8", errors="ignore")


def read_uploaded_report(upload: cgi.FieldStorage) -> str:
    raw = upload.file.read()
    if not raw:
        raise RuntimeError("历史报告文件为空")
    if len(raw) > MAX_HISTORY_REPORT_BYTES:
        raise RuntimeError(f"历史报告文件过大，最大支持 {MAX_HISTORY_REPORT_BYTES // (1024 * 1024)}MB")

    filename = str(getattr(upload, "filename", "") or "uploaded_report")
    text = decode_best_effort_text(raw)
    # If decoded text looks like binary noise, fallback to base64 payload.
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    ratio = (printable / len(text)) if text else 0.0
    if not text or ratio < 0.65:
        b64 = base64.b64encode(raw).decode("ascii")
        b64 = b64[:200000]
        return (
            f"文件名: {filename}\n"
            "文件内容可能是二进制格式，以下为 base64 片段（已截断）：\n"
            f"{b64}"
        )

    text = text[:200000]
    return f"文件名: {filename}\n文件文本内容（可能已截断）：\n{text}"


def build_html(values: Dict[str, str], selected_checks: List[str], output_text: str, status: str) -> str:
    category_items: Dict[str, List[str]] = {name: [] for name, _ in CHECK_CATEGORIES}
    selected_set = set(selected_checks)
    for item in DEFAULT_CHECKS:
        placed = False
        for category_name, members in CHECK_CATEGORIES:
            if members and item in members:
                category_items[category_name].append(item)
                placed = True
                break
        if not placed:
            category_items["更多分类"].append(item)

    checks_blocks: List[str] = []
    for idx, (category_name, _) in enumerate(CHECK_CATEGORIES):
        items = category_items.get(category_name, [])
        group_id = f"cat_{idx}"
        item_html: List[str] = []
        for item in items:
            checked = "checked" if item in selected_set else ""
            item_html.append(
                f'<label class="check-item"><input type="checkbox" name="checks" value="{html.escape(item)}" '
                f'data-category="{group_id}" {checked}>{html.escape(item)}</label>'
            )
        content_html = f'<div class="checks">{"".join(item_html)}</div>' if item_html else '<div class="empty-cat">暂无检查项</div>'

        checks_blocks.append(
            '<div class="check-group">'
            f'<div class="check-group-head"><strong>{html.escape(category_name)}</strong>'
            f'<label class="select-all"><input type="checkbox" class="category-toggle" data-category="{group_id}">全选</label>'
            '</div>'
            f"{content_html}"
            "</div>"
        )

    status_block = ""
    if status:
        css = "ok" if status.startswith("SUCCESS") else "err"
        status_block = f'<div class="status {css}">{html.escape(status)}</div>'

    output_block = ""
    if output_text:
        output_block = f"<h3>执行输出</h3><pre>{html.escape(output_text)}</pre>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HealthCheck 执行页面</title>
  <style>
    :root {{
      --bg: #f3f5f7;
      --card: #ffffff;
      --text: #0f172a;
      --line: #d6dce3;
      --brand: #0b6e4f;
      --brand-weak: #e5f5ef;
      --err: #9b1c1c;
      --ok: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top right, #e1efe9 0%, var(--bg) 40%);
      color: var(--text);
      font: 14px/1.5 "Helvetica Neue", "PingFang SC", sans-serif;
    }}
    .wrap {{ max-width: 980px; margin: 28px auto; padding: 0 16px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 8px 18px rgba(14, 30, 37, 0.05);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .sub {{ margin: 0 0 18px; color: #334155; }}
    h2 {{ margin: 14px 0 10px; font-size: 18px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    label {{ display: block; font-weight: 600; margin: 0 0 6px; }}
    input[type=text], input[type=password], input[type=number], select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fcfdff;
      outline: none;
    }}
    textarea {{ min-height: 110px; resize: vertical; }}
    .checks {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 6px 10px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      max-height: 220px;
      overflow: auto;
    }}
    .check-groups {{ display: grid; gap: 10px; }}
    .check-group {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #f8fbfd;
    }}
    .check-group-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }}
    .select-all {{ font-size: 13px; font-weight: 600; }}
    .empty-cat {{ color: #64748b; font-size: 13px; padding: 8px 2px; }}
    .check-item {{ display: flex; gap: 6px; align-items: center; font-weight: 500; }}
    .row {{ margin-bottom: 12px; }}
    .tips {{ color: #475569; font-size: 12px; margin-top: 4px; }}
    .import-result {{ color: #0b6e4f; font-size: 12px; margin-top: 6px; }}
    .preview-box {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 10px;
      margin-top: 6px;
      max-height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Menlo, Consolas, monospace;
      font-size: 12px;
      color: #0f172a;
    }}
    button {{
      border: 0;
      border-radius: 8px;
      padding: 10px 16px;
      background: var(--brand);
      color: #fff;
      cursor: pointer;
      font-weight: 700;
    }}
    button:hover {{ filter: brightness(0.95); }}
    .btn-secondary {{
      background: #64748b;
    }}
    .status {{
      margin: 12px 0;
      padding: 10px;
      border-radius: 8px;
      border: 1px solid;
      font-weight: 700;
    }}
    .ok {{ color: var(--ok); border-color: #99f6e4; background: var(--brand-weak); }}
    .err {{ color: var(--err); border-color: #fecaca; background: #fef2f2; }}
    pre {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #0f172a;
      color: #e2e8f0;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 460px;
      overflow: auto;
    }}
    @media (max-width: 740px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>HealthCheck Web Runner</h1>
      <p class="sub">输入设备地址、勾选检查项、上传 command_map 文件后，点击执行 `healthcheck.py`。</p>
      {status_block}
      <form id="run_form" method="post" action="/run" enctype="multipart/form-data">
        <div class="grid row">
          <div>
            <label>SSH 用户名</label>
            <input type="text" name="username" value="{html.escape(values.get("username", ""))}" required>
          </div>
          <div>
            <label>SSH 密码</label>
            <input type="password" name="password" value="{html.escape(values.get("password", ""))}" required>
          </div>
        </div>
        <div class="row">
          <label>设备地址（每行一个）</label>
          <textarea name="devices">{html.escape(values.get("devices", ""))}</textarea>
          <div class="tips">支持手动输入；导入设备文件后会直接刷新到此文本框，你可继续编辑。</div>
        </div>
        <div class="row">
          <label>导入设备文件（可选）</label>
          <input type="file" id="devices_file" name="devices_file" accept=".txt,.csv,.list">
          <div class="tips">文件支持按换行/逗号/分号分隔，`#` 开头行会忽略。</div>
          <div id="import_result" class="import-result"></div>
        </div>
        <div class="row">
          <label>检查项（可多选）</label>
          <div class="check-groups">{''.join(checks_blocks)}</div>
        </div>
        <div class="row">
          <label>自定义命令（可选，按行执行）</label>
          <textarea name="custom_commands" placeholder="例如：&#10;display ip interface brief&#10;display current-configuration | no-more">{html.escape(values.get("custom_commands", ""))}</textarea>
          <div class="tips">会在勾选检查项之后执行，顺序按从上到下。支持换行/逗号/分号分隔，`#` 开头会忽略。</div>
        </div>
        <div class="grid row">
          <div>
            <label>执行模式</label>
            <select name="execution_mode">
              <option value="auto" {"selected" if values.get("execution_mode", "auto") == "auto" else ""}>auto（推荐）</option>
              <option value="serial" {"selected" if values.get("execution_mode") == "serial" else ""}>serial</option>
              <option value="parallel" {"selected" if values.get("execution_mode") == "parallel" else ""}>parallel</option>
            </select>
          </div>
          <div>
            <label>并发 workers（可选）</label>
            <input type="number" name="parallel_workers" min="1" step="1" value="{html.escape(values.get("parallel_workers", ""))}" placeholder="留空自动推荐">
          </div>
        </div>
        <div class="row">
          <label>连接重试次数</label>
          <input type="number" name="connect_retry" min="0" step="1" value="{html.escape(values.get("connect_retry", "0"))}">
        </div>
        <div class="row">
          <label>导入 command_map 文件（可选，默认使用 config/command_map.yaml）</label>
          <input type="file" name="command_map" accept=".yaml,.yml">
          <div class="tips">不上传时默认使用 `config/command_map.yaml`；上传时会临时覆盖本次任务。</div>
        </div>
        <div class="row">
          <label class="check-item"><input type="checkbox" name="debug_mode" value="1" {"checked" if values.get("debug_mode") else ""}>开启 Debug 模式（显示完整执行日志）</label>
          <div class="tips">默认关闭。关闭时会隐藏交互提示等噪音输出，任务状态页更干净。</div>
        </div>
        <div class="row">
          <label>执行队列预览（提交前）</label>
          <div id="command_preview" class="preview-box">暂无待执行项</div>
          <div class="tips">顺序：先检查项，再自定义命令（从上到下）。</div>
        </div>
        <div class="row">
          <button type="submit">执行 Python 巡检脚本</button>
          <button id="clear_saved_btn" class="btn-secondary" type="button" style="margin-left:8px;">清空已保存配置</button>
          <div class="tips">清空后会删除本地记忆的首页配置（用户名/设备/检查项/执行参数等）。</div>
        </div>
      </form>
      {output_block}
    </div>
  </div>
<script>
  const HOME_FORM_STORAGE_KEY = "hc_home_form_v1";

  function saveHomeFormState() {{
    try {{
      const state = {{
        username: (document.querySelector('input[name="username"]') || {{}}).value || "",
        devices: (document.querySelector('textarea[name="devices"]') || {{}}).value || "",
        custom_commands: (document.querySelector('textarea[name="custom_commands"]') || {{}}).value || "",
        execution_mode: (document.querySelector('select[name="execution_mode"]') || {{}}).value || "auto",
        parallel_workers: (document.querySelector('input[name="parallel_workers"]') || {{}}).value || "",
        connect_retry: (document.querySelector('input[name="connect_retry"]') || {{}}).value || "0",
        debug_mode: !!(document.querySelector('input[name="debug_mode"]') || {{}}).checked,
        checks: Array.from(document.querySelectorAll('input[name="checks"]:checked')).map(i => i.value),
      }};
      localStorage.setItem(HOME_FORM_STORAGE_KEY, JSON.stringify(state));
    }} catch (e) {{}}
  }}

  function restoreHomeFormState() {{
    try {{
      const raw = localStorage.getItem(HOME_FORM_STORAGE_KEY);
      if (!raw) return;
      const state = JSON.parse(raw);
      if (!state || typeof state !== "object") return;

      const usernameEl = document.querySelector('input[name="username"]');
      const devicesEl = document.querySelector('textarea[name="devices"]');
      const customCommandsEl2 = document.querySelector('textarea[name="custom_commands"]');
      const modeEl = document.querySelector('select[name="execution_mode"]');
      const workersEl = document.querySelector('input[name="parallel_workers"]');
      const retryEl = document.querySelector('input[name="connect_retry"]');
      const debugEl = document.querySelector('input[name="debug_mode"]');

      if (usernameEl && typeof state.username === "string") usernameEl.value = state.username;
      if (devicesEl && typeof state.devices === "string") devicesEl.value = state.devices;
      if (customCommandsEl2 && typeof state.custom_commands === "string") customCommandsEl2.value = state.custom_commands;
      if (modeEl && typeof state.execution_mode === "string") modeEl.value = state.execution_mode || "auto";
      if (workersEl && typeof state.parallel_workers === "string") workersEl.value = state.parallel_workers;
      if (retryEl && typeof state.connect_retry === "string") retryEl.value = state.connect_retry || "0";
      if (debugEl) debugEl.checked = !!state.debug_mode;

      if (Array.isArray(state.checks)) {{
        const checkedSet = new Set(state.checks.map(v => String(v)));
        document.querySelectorAll('input[name="checks"]').forEach((el) => {{
          el.checked = checkedSet.has(el.value);
        }});
      }}
    }} catch (e) {{}}
  }}

  function updateCategoryToggle(category) {{
    const items = document.querySelectorAll('input[name="checks"][data-category="' + category + '"]');
    const toggle = document.querySelector('.category-toggle[data-category="' + category + '"]');
    if (!items.length || !toggle) return;
    const checked = Array.from(items).filter(i => i.checked).length;
    toggle.checked = checked === items.length;
    toggle.indeterminate = checked > 0 && checked < items.length;
  }}

  document.querySelectorAll('.category-toggle').forEach(toggle => {{
    const category = toggle.getAttribute('data-category');
    toggle.addEventListener('change', () => {{
      document.querySelectorAll('input[name="checks"][data-category="' + category + '"]').forEach(i => {{
        i.checked = toggle.checked;
      }});
      updateCategoryToggle(category);
    }});
    updateCategoryToggle(category);
  }});

  document.querySelectorAll('input[name="checks"][data-category]').forEach(item => {{
    item.addEventListener('change', () => {{
      updateCategoryToggle(item.getAttribute('data-category'));
    }});
  }});

  const devicesFileEl = document.getElementById('devices_file');
  const runFormEl = document.getElementById('run_form');
  const clearSavedBtnEl = document.getElementById('clear_saved_btn');
  const devicesTextEl = document.querySelector('textarea[name="devices"]');
  const importResultEl = document.getElementById('import_result');
  const customCommandsEl = document.querySelector('textarea[name="custom_commands"]');
  const previewEl = document.getElementById('command_preview');

  function parseDevicesText(raw) {{
    const dedup = [];
    const seen = new Set();
    raw.split(/[\\n,;]+/).forEach((part) => {{
      const v = part.trim();
      if (!v || v.startsWith('#')) return;
      if (!seen.has(v)) {{
        seen.add(v);
        dedup.push(v);
      }}
    }});
    return dedup;
  }}

  function parseItems(raw) {{
    return String(raw || '')
      .split(/[\\n,;]+/)
      .map(s => s.trim())
      .filter(s => s && !s.startsWith('#'));
  }}

  function buildPreview() {{
    if (!previewEl) return;
    const selectedChecks = Array.from(document.querySelectorAll('input[name="checks"]:checked')).map(i => i.value);
    const customCommands = parseItems(customCommandsEl ? customCommandsEl.value : '');
    const finalItems = selectedChecks.concat(customCommands);
    if (!finalItems.length) {{
      previewEl.textContent = '暂无待执行项';
      return;
    }}
    previewEl.textContent = finalItems.map((item, idx) => (String(idx + 1) + '. ' + item)).join('\\n');
  }}

  if (devicesFileEl && devicesTextEl) {{
    devicesFileEl.addEventListener('change', () => {{
      const file = devicesFileEl.files && devicesFileEl.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        const text = String(reader.result || '');
        const devices = parseDevicesText(text);
        devicesTextEl.value = devices.join('\\n');
        if (importResultEl) {{
          importResultEl.textContent = devices.length > 0 ? ('成功导入 ' + devices.length + ' 台设备，已刷新到设备地址文本框。') : '未解析到有效设备地址。';
        }}
        saveHomeFormState();
      }};
      reader.onerror = () => {{
        if (importResultEl) {{
          importResultEl.textContent = '设备文件读取失败，请重试。';
        }}
      }};
      reader.readAsText(file);
    }});
  }}

  document.querySelectorAll('input[name="checks"]').forEach(el => {{
    el.addEventListener('change', buildPreview);
  }});
  if (customCommandsEl) {{
    customCommandsEl.addEventListener('input', buildPreview);
    customCommandsEl.addEventListener('change', buildPreview);
  }}
  if (runFormEl) {{
    runFormEl.addEventListener('submit', saveHomeFormState);
  }}
  if (clearSavedBtnEl) {{
    clearSavedBtnEl.addEventListener('click', () => {{
      const ok = window.confirm('确认清空本地保存的首页配置吗？');
      if (!ok) return;
      try {{
        localStorage.removeItem(HOME_FORM_STORAGE_KEY);
      }} catch (e) {{}}
      const passwordEl = document.querySelector('input[name="password"]');
      if (runFormEl) runFormEl.reset();
      if (passwordEl) passwordEl.value = "";
      document.querySelectorAll('input[name="checks"]').forEach((el, idx) => {{
        el.checked = idx < 3;
      }});
      document.querySelectorAll('.category-toggle').forEach(toggle => {{
        updateCategoryToggle(toggle.getAttribute('data-category'));
      }});
      if (importResultEl) importResultEl.textContent = "已清空本地保存配置。";
      buildPreview();
    }});
  }}

  restoreHomeFormState();
  document.querySelectorAll('.category-toggle').forEach(toggle => {{
    updateCategoryToggle(toggle.getAttribute('data-category'));
  }});
  buildPreview();
</script>
</body>
</html>
"""


def build_job_html(job_id: str) -> str:
    gpt_config = load_gpt_config()
    prompts = merged_prompt_catalog()
    selected_prompt = str(gpt_config.get("selected_prompt", "") or "")
    prompt_options = "".join(
        [
            f'<option value="" {"selected" if not selected_prompt else ""}>不使用模板</option>'
        ]
        + [
            f'<option value="{html.escape(name)}" {"selected" if selected_prompt == name else ""}>{html.escape(name)}</option>'
            for name in prompts.keys()
        ]
    )
    has_chatgpt_key = bool((gpt_config.get("chatgpt_api_key") or "").strip())
    has_deepseek_key = bool((gpt_config.get("deepseek_api_key") or "").strip())
    provider = str(gpt_config.get("provider", "chatgpt") or "chatgpt")
    chatgpt_model = str(gpt_config.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL)
    local_base_url = str(gpt_config.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL)
    local_model = str(gpt_config.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL)
    deepseek_model = str(gpt_config.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL)
    chatgpt_in_options = chatgpt_model in CHATGPT_MODEL_OPTIONS
    local_in_options = local_model in LOCAL_MODEL_OPTIONS
    deepseek_in_options = deepseek_model in DEEPSEEK_MODEL_OPTIONS
    chatgpt_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if chatgpt_model == m else ""}>{html.escape(m)}</option>' for m in CHATGPT_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not chatgpt_in_options else ""}>自定义</option>']
    )
    local_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if local_model == m else ""}>{html.escape(m)}</option>' for m in LOCAL_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not local_in_options else ""}>自定义</option>']
    )
    deepseek_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if deepseek_model == m else ""}>{html.escape(m)}</option>' for m in DEEPSEEK_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not deepseek_in_options else ""}>自定义</option>']
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>任务执行中</title>
  <style>
    body {{
      margin: 0;
      background: #f1f5f9;
      color: #0f172a;
      font: 14px/1.5 "Helvetica Neue", "PingFang SC", sans-serif;
    }}
    .wrap {{ max-width: 980px; margin: 26px auto; padding: 0 16px; }}
    .card {{
      background: #fff;
      border: 1px solid #d6dce3;
      border-radius: 12px;
      padding: 16px;
    }}
    .head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .tag {{
      border-radius: 999px;
      padding: 4px 10px;
      font-weight: 700;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
    }}
    .running {{ color: #92400e; border-color: #fcd34d; background: #fffbeb; }}
    .ok {{ color: #065f46; border-color: #6ee7b7; background: #ecfdf5; }}
    .err {{ color: #991b1b; border-color: #fecaca; background: #fef2f2; }}
    pre {{
      margin-top: 12px;
      border: 1px solid #d6dce3;
      border-radius: 8px;
      background: #0f172a;
      color: #e2e8f0;
      padding: 10px;
      min-height: 240px;
      max-height: 540px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    a {{ color: #0b6e4f; font-weight: 700; text-decoration: none; }}
    .report-links {{
      margin-top: 10px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .report-links a {{
      display: inline-block;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 6px 10px;
      background: #f8fafc;
    }}
    .report-links a:hover {{ background: #eef2f7; }}
    .gpt-card {{
      margin-top: 12px;
      border: 1px solid #d6dce3;
      border-radius: 10px;
      padding: 12px;
      background: #f8fafc;
    }}
    .gpt-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .gpt-row {{ margin-top: 10px; }}
    .gpt-card input[type=text], .gpt-card input[type=password], .gpt-card textarea, .gpt-card select {{
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 8px 10px;
      background: #fff;
      box-sizing: border-box;
    }}
    .gpt-card textarea {{ min-height: 90px; }}
    .gpt-details {{
      margin-top: 10px;
      border: 1px dashed #cbd5e1;
      border-radius: 8px;
      padding: 8px 10px;
      background: #ffffff;
    }}
    .gpt-details summary {{ cursor: pointer; font-weight: 700; }}
    .gpt-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    .gpt-btn {{
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      padding: 8px 12px;
      cursor: pointer;
      font-weight: 700;
    }}
    .gpt-primary {{ background: #0b6e4f; color: #fff; border-color: #0b6e4f; }}
    .gpt-hint {{ font-size: 12px; color: #475569; margin-top: 4px; }}
    .ai-head {{
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 6px;
      margin-bottom: 8px;
    }}
    .ai-brand {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      flex: 0 0 20px;
    }}
    .ai-brand img {{
      width: 18px;
      height: 18px;
      border-radius: 4px;
      display: block;
    }}
    .gpt-section {{
      border: 1px solid #d7dee7;
      border-radius: 10px;
      background: #ffffff;
      padding: 10px;
      margin-top: 10px;
    }}
    .gpt-section-title {{
      font-weight: 700;
      color: #1e293b;
      margin: 0 0 8px;
    }}
    .ai-brand svg {{
      width: 18px;
      height: 18px;
      display: block;
    }}
    #gpt_result {{
      margin-top: 10px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      white-space: pre-wrap;
      max-height: 380px;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="head">
        <h2>巡检任务状态</h2>
        <span id="state" class="tag running">执行中...</span>
      </div>
      <div>任务 ID: <code>{html.escape(job_id)}</code> | <a href="/">返回首页</a></div>
      <div id="reports" class="report-links"></div>
      <pre id="output">正在启动任务，请稍候...</pre>
      <div class="gpt-card">
        <div class="ai-head">
          <span id="provider_brand_inline" class="ai-brand"></span>
          <h3 style="margin:0;">AI 诊断分析</h3>
        </div>
        <div class="gpt-section">
          <div class="gpt-section-title">大模型配置</div>
          <div class="gpt-grid gpt-row" style="margin-top:0;">
            <div>
              <label>大模型选择</label>
              <select id="provider_select">
                <option value="chatgpt" {"selected" if provider == "chatgpt" else ""}>ChatGPT</option>
                <option value="deepseek" {"selected" if provider == "deepseek" else ""}>DeepSeek</option>
                <option value="local" {"selected" if provider == "local" else ""}>本地大模型</option>
              </select>
            </div>
            <div>
              <label>API Key 管理</label>
              <div class="gpt-actions" style="margin-top:0;">
                <button class="gpt-btn" id="import_api_key_btn" type="button">导入 API Key</button>
                <button class="gpt-btn" id="save_llm_btn" type="button">保存模型配置</button>
              </div>
              <div class="gpt-hint">用途：保存当前大模型来源、模型名、本地地址、已选提示词模板。下次打开页面会自动带出。</div>
              <div id="api_key_state" class="gpt-hint">ChatGPT Key: {"已保存" if has_chatgpt_key else "未保存"} | DeepSeek Key: {"已保存" if has_deepseek_key else "未保存"}</div>
            </div>
          </div>
          <div id="chatgpt_settings" class="gpt-grid gpt-row">
            <div>
              <label>ChatGPT 模型</label>
              <select id="chatgpt_model_select">{chatgpt_model_options}</select>
            </div>
            <div id="chatgpt_custom_wrap" style="display:{'none' if chatgpt_in_options else 'block'};">
              <label>自定义 ChatGPT 模型</label>
              <input id="chatgpt_model_custom" type="text" value="{html.escape('' if chatgpt_in_options else chatgpt_model)}" placeholder="例如 gpt-4.1-mini">
            </div>
          </div>
          <div id="local_settings" class="gpt-grid gpt-row">
            <div>
              <label>本地大模型地址</label>
              <input id="local_base_url" type="text" value="{html.escape(local_base_url)}" placeholder="http://127.0.0.1:1234">
            </div>
            <div>
              <label>本地大模型模型</label>
              <select id="local_model_select">{local_model_options}</select>
            </div>
            <div id="local_custom_wrap" style="display:{'none' if local_in_options else 'block'};">
              <label>自定义本地模型</label>
              <input id="local_model_custom" type="text" value="{html.escape('' if local_in_options else local_model)}" placeholder="例如 qwen/qwen3-coder-30b">
            </div>
          </div>
          <div id="deepseek_settings" class="gpt-grid gpt-row">
            <div>
              <label>DeepSeek 模型</label>
              <select id="deepseek_model_select">{deepseek_model_options}</select>
            </div>
            <div id="deepseek_custom_wrap" style="display:{'none' if deepseek_in_options else 'block'};">
              <label>自定义 DeepSeek 模型</label>
              <input id="deepseek_model_custom" type="text" value="{html.escape('' if deepseek_in_options else deepseek_model)}" placeholder="例如 deepseek-chat">
            </div>
          </div>
        </div>

        <div class="gpt-section">
          <div class="gpt-section-title">提示词设置</div>
          <div class="gpt-grid gpt-row" style="margin-top:0;">
            <div>
              <label>提示词模板</label>
              <select id="prompt_select">{prompt_options}</select>
              <div class="gpt-hint">建议先选模板，再填写“追加分析要求”。可选择“不使用模板”。</div>
            </div>
            <div>
              <label>模板查看</label>
              <div class="gpt-actions" style="margin-top:0;">
                <button class="gpt-btn" id="review_template_btn" type="button">Review 提示词模板</button>
              </div>
              <div class="gpt-hint">点击弹窗查看当前选择模板的完整内容。</div>
            </div>
          </div>
          <details class="gpt-details gpt-row">
            <summary>模板管理（可选）</summary>
            <div class="gpt-grid" style="margin-top:8px;">
              <div>
                <label>导入提示词文件（txt）</label>
                <input id="prompt_file" type="file" accept=".txt">
                <div class="gpt-hint">留空名称时自动使用文件名。</div>
              </div>
              <div>
                <label>导入时命名（可选）</label>
                <input id="prompt_name" type="text" placeholder="例如：核心链路专项诊断（不填自动用文件名）">
              </div>
            </div>
            <div class="gpt-actions" style="margin-top:8px;">
              <button class="gpt-btn" id="import_prompt_btn" type="button">导入提示词为模板</button>
            </div>
          </details>
          <div class="gpt-grid gpt-row">
            <div class="gpt-row">
              <label>追加分析要求（可选）</label>
              <textarea id="custom_prompt" placeholder="会追加在模板后面；例如：请重点关注核心上联、邻居抖动和高风险接口"></textarea>
            </div>
            <div></div>
          </div>
        </div>

        <div class="gpt-section">
          <div class="gpt-section-title">历史报告分析</div>
          <div class="gpt-grid gpt-row" style="margin-top:0;">
            <div>
              <label>历史报告文件（任意格式）</label>
              <input id="history_report_file" type="file">
              <div class="gpt-hint">可上传历史 JSON/CSV/TXT/LOG 或其他格式文件，由 AI 尝试解析后分析。</div>
            </div>
            <div></div>
          </div>
        </div>
        <div class="gpt-actions">
          <button class="gpt-btn" id="test_llm_btn" type="button">连接测试</button>
          <button class="gpt-btn gpt-primary" id="analyze_btn" type="button">AI 分析本次结果</button>
          <button class="gpt-btn gpt-primary" id="analyze_history_btn" type="button">AI 分析历史报告</button>
        </div>
        <div id="gpt_status" class="gpt-hint"></div>
        <div id="gpt_result">分析结果会显示在这里。</div>
      </div>
    </div>
  </div>
  <script>
    const jobId = {json.dumps(job_id)};
    let promptMap = {json.dumps(prompts, ensure_ascii=False)};
    const stateEl = document.getElementById("state");
    const outputEl = document.getElementById("output");
    const reportEl = document.getElementById("reports");
    const apiKeyStateEl = document.getElementById("api_key_state");
    const hasChatgptKeySaved = {str(has_chatgpt_key).lower()};
    const hasDeepseekKeySaved = {str(has_deepseek_key).lower()};
    const providerEl = document.getElementById("provider_select");
    const providerBrandInlineEl = document.getElementById("provider_brand_inline");
    const chatgptModelSelectEl = document.getElementById("chatgpt_model_select");
    const chatgptModelCustomEl = document.getElementById("chatgpt_model_custom");
    const chatgptCustomWrapEl = document.getElementById("chatgpt_custom_wrap");
    const localBaseEl = document.getElementById("local_base_url");
    const localModelSelectEl = document.getElementById("local_model_select");
    const localModelCustomEl = document.getElementById("local_model_custom");
    const localCustomWrapEl = document.getElementById("local_custom_wrap");
    const deepseekModelSelectEl = document.getElementById("deepseek_model_select");
    const deepseekModelCustomEl = document.getElementById("deepseek_model_custom");
    const deepseekCustomWrapEl = document.getElementById("deepseek_custom_wrap");
    const chatgptSettingsEl = document.getElementById("chatgpt_settings");
    const localSettingsEl = document.getElementById("local_settings");
    const deepseekSettingsEl = document.getElementById("deepseek_settings");
    const promptSelectEl = document.getElementById("prompt_select");
    const promptFileEl = document.getElementById("prompt_file");
    const promptNameEl = document.getElementById("prompt_name");
    const historyReportFileEl = document.getElementById("history_report_file");
    const customPromptEl = document.getElementById("custom_prompt");
    const gptStatusEl = document.getElementById("gpt_status");
    const gptResultEl = document.getElementById("gpt_result");

    function setState(status, exitCode) {{
      if (status === "running") {{
        stateEl.className = "tag running";
        stateEl.textContent = "执行中...";
      }} else if (status === "success") {{
        stateEl.className = "tag ok";
        stateEl.textContent = "执行完成 (exit_code=" + exitCode + ")";
      }} else {{
        stateEl.className = "tag err";
        stateEl.textContent = "执行失败 (exit_code=" + exitCode + ")";
      }}
    }}

    function renderReports(data) {{
      if (data.status !== "success") {{
        reportEl.innerHTML = "";
        return;
      }}
      const links = [];
      if (data.report_json) {{
        links.push('<a href="/download?name=' + encodeURIComponent(data.report_json) + '">下载本次 JSON 报告</a>');
      }}
      if (data.report_csv) {{
        links.push('<a href="/download?name=' + encodeURIComponent(data.report_csv) + '">下载本次 CSV 报告</a>');
      }}
      reportEl.innerHTML = links.join("");
    }}

    function setGptStatus(msg) {{
      if (gptStatusEl) gptStatusEl.textContent = msg || "";
    }}

    function selectedModel(selectEl, customEl) {{
      if (!selectEl) return "";
      const v = (selectEl.value || "").trim();
      if (v === "__custom__") {{
        return customEl ? (customEl.value || "").trim() : "";
      }}
      return v;
    }}

    function refreshCustomModelVisibility() {{
      if (chatgptCustomWrapEl && chatgptModelSelectEl) {{
        chatgptCustomWrapEl.style.display = chatgptModelSelectEl.value === "__custom__" ? "block" : "none";
      }}
      if (localCustomWrapEl && localModelSelectEl) {{
        localCustomWrapEl.style.display = localModelSelectEl.value === "__custom__" ? "block" : "none";
      }}
      if (deepseekCustomWrapEl && deepseekModelSelectEl) {{
        deepseekCustomWrapEl.style.display = deepseekModelSelectEl.value === "__custom__" ? "block" : "none";
      }}
    }}

    function refreshProviderUI() {{
      const provider = (providerEl.value || "chatgpt").trim();
      if (chatgptSettingsEl) chatgptSettingsEl.style.display = provider === "chatgpt" ? "grid" : "none";
      if (localSettingsEl) localSettingsEl.style.display = provider === "local" ? "grid" : "none";
      if (deepseekSettingsEl) deepseekSettingsEl.style.display = provider === "deepseek" ? "grid" : "none";
      if (providerBrandInlineEl) {{
        const svgDataUri = (bg, label) => {{
          const txt = String(label || "AI").slice(0, 2).toUpperCase();
          const color = bg || "#334155";
          const svg =
            "<svg xmlns=\\"http://www.w3.org/2000/svg\\" viewBox=\\"0 0 24 24\\">" +
            "<rect width=\\"24\\" height=\\"24\\" rx=\\"6\\" fill=\\"" + color + "\\"/>" +
            "<text x=\\"12\\" y=\\"15\\" text-anchor=\\"middle\\" font-size=\\"9\\" font-family=\\"Arial, sans-serif\\" fill=\\"white\\">" + txt + "</text>" +
            "</svg>";
          return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
        }};
        const setBrandIcon = (url, alt, title, fallbackBg, fallbackLabel) => {{
          const fb = svgDataUri(fallbackBg, fallbackLabel);
          providerBrandInlineEl.innerHTML = '<img src="' + url + '" alt="' + alt + '" title="' + title + '">';
          const img = providerBrandInlineEl.querySelector("img");
          if (img) {{
            img.onerror = () => {{
              img.onerror = null;
              img.src = fb;
            }};
          }}
        }};
        const setGemmaIcon = () => {{
          providerBrandInlineEl.innerHTML =
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="9.5" fill="none" stroke="#c7d2fe" stroke-width="1.2"/>' +
            '<path d="M12 3.5v17M3.5 12h17M5.8 5.8l12.4 12.4M18.2 5.8L5.8 18.2" stroke="#e2e8f0" stroke-width="0.8"/>' +
            '<path d="M12 5.8l3.8 6.2L12 18.2 8.2 12z" fill="none" stroke="#3b82f6" stroke-width="1.6" stroke-linejoin="round"/>' +
            '</svg>';
        }};
        const setLlamaIcon = () => {{
          providerBrandInlineEl.innerHTML =
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
            '<path d="M6.2 12c1.6-3.5 3.1-3.5 5.8 0-2.7 3.5-4.2 3.5-5.8 0z" fill="none" stroke="#1d4ed8" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>' +
            '<path d="M12 12c1.6-3.5 3.1-3.5 5.8 0-2.7 3.5-4.2 3.5-5.8 0z" fill="none" stroke="#1d4ed8" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>' +
            '</svg>';
        }};
        const iconMap = {{
          openai: "https://openai.com/favicon.ico",
          deepseek: "https://www.deepseek.com/favicon.ico",
          lmstudio: "https://lmstudio.ai/favicon.ico",
          qwen: "https://upload.wikimedia.org/wikipedia/commons/6/69/Qwen_logo.svg",
          llama: "https://upload.wikimedia.org/wikipedia/commons/thumb/8/89/Meta_Platforms_Inc._logo.svg/64px-Meta_Platforms_Inc._logo.svg.png",
          mistral: "https://mistral.ai/favicon.ico",
          gemma: "https://ai.google.dev/favicon.ico",
          claude: "https://www.anthropic.com/favicon.ico",
          cohere: "https://cohere.com/favicon.ico",
          grok: "https://x.ai/favicon.ico",
          yi: "https://www.lingyiwanwu.com/favicon.ico",
        }};
        const localModelRaw = selectedModel(localModelSelectEl, localModelCustomEl).toLowerCase().trim();
        const vendorPrefix = (localModelRaw.split(/[-_/:.\\s]/).filter(Boolean)[0] || "lmstudio");
        const vendorAliasMap = {{
          google: "gemma",
          meta: "llama",
          "meta-llama": "llama",
          mixtral: "mistral",
          "command-r": "cohere",
          moonshot: "kimi",
        }};
        let localVendor = vendorAliasMap[vendorPrefix] || vendorPrefix;
        if (provider === "chatgpt") {{
          setBrandIcon(iconMap.openai, "OpenAI", "ChatGPT", "#10a37f", "OA");
          providerBrandInlineEl.title = "ChatGPT";
        }} else if (provider === "deepseek") {{
          setBrandIcon(iconMap.deepseek, "DeepSeek", "DeepSeek", "#2563eb", "DS");
          providerBrandInlineEl.title = "DeepSeek";
        }} else {{
          const icon = iconMap[localVendor] || iconMap.lmstudio;
          const fallbackMap = {{
            qwen: ["#ef4444", "QW"],
            deepseek: ["#2563eb", "DS"],
            llama: ["#0ea5e9", "LL"],
            mistral: ["#f59e0b", "MS"],
            gemma: ["#3b82f6", "GM"],
            claude: ["#8b5cf6", "CL"],
            cohere: ["#0891b2", "CO"],
            grok: ["#111827", "GX"],
            yi: ["#059669", "YI"],
            glm: ["#0f766e", "GL"],
            baichuan: ["#0ea5e9", "BC"],
            internlm: ["#9333ea", "IL"],
            doubao: ["#1d4ed8", "DB"],
            kimi: ["#ec4899", "KM"],
            phi: ["#4f46e5", "PH"],
            lmstudio: ["#334155", "LM"],
          }};
          const fb = fallbackMap[localVendor] || fallbackMap.lmstudio;
          if (localVendor === "gemma") {{
            setGemmaIcon();
          }} else if (localVendor === "llama") {{
            setBrandIcon(icon, "Llama", "本地大模型(llama)", "#1d4ed8", "LL");
            const img = providerBrandInlineEl.querySelector("img");
            if (img) {{
              img.onerror = () => {{
                img.onerror = null;
                setLlamaIcon();
              }};
            }}
          }} else {{
            setBrandIcon(icon, "Local LLM", "本地大模型(" + localVendor + ")", fb[0], fb[1]);
          }}
          providerBrandInlineEl.title = "本地大模型(" + localVendor + ")";
        }}
      }}
      refreshCustomModelVisibility();
    }}

    async function postForm(url, formBody) {{
      const resp = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }},
        body: new URLSearchParams(formBody),
      }});
      return await resp.json();
    }}

    document.getElementById("review_template_btn").addEventListener("click", () => {{
      const key = (promptSelectEl.value || "").trim();
      if (!key) {{
        window.alert("当前未选择模板（不使用模板）。");
        return;
      }}
      const content = (promptMap && promptMap[key]) ? String(promptMap[key]) : "";
      if (!content) {{
        window.alert("当前模板无内容或不存在。");
        return;
      }}
      window.alert("模板名称: " + key + "\\n\\n" + content);
    }});

    document.getElementById("save_llm_btn").addEventListener("click", async () => {{
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModelResolved = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const selectedPrompt = promptSelectEl.value || "";
      if (provider === "chatgpt" && !chatgptModel) {{
        setGptStatus("ChatGPT 模式下请选择模型或输入自定义模型。");
        return;
      }}
      if (provider === "local" && (!localBaseUrl || !localModel)) {{
        setGptStatus("本地大模型模式下请填写地址和模型。");
        return;
      }}
      if (provider === "deepseek" && !deepseekModelResolved) {{
        setGptStatus("DeepSeek 模式下请填写模型名称。");
        return;
      }}
      setGptStatus("正在保存配置...");
      try {{
        const data = await postForm("/save_gpt_key", {{
          provider: provider,
          chatgpt_model: chatgptModel,
          local_base_url: localBaseUrl,
          local_model: localModel,
          deepseek_model: deepseekModelResolved,
          selected_prompt: selectedPrompt,
        }});
        setGptStatus(data.ok ? "已保存模型配置：来源/模型/地址/提示词模板，下次会自动带出。" : ("保存失败: " + (data.error || "unknown")));
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }}
    }});

    document.getElementById("test_llm_btn").addEventListener("click", async () => {{
      const provider = (providerEl.value || "chatgpt").trim();
      const localBaseUrl = (localBaseEl.value || "").trim();
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      setGptStatus("正在测试连接...");
      try {{
        const data = await postForm("/test_llm", {{
          provider: provider,
          local_base_url: localBaseUrl,
          deepseek_model: deepseekModel,
        }});
        if (!data.ok) {{
          setGptStatus("连接测试失败: " + (data.error || "unknown"));
          return;
        }}
        setGptStatus(data.message || "连接测试成功。");
      }} catch (e) {{
        setGptStatus("连接测试失败: " + e);
      }}
    }});

    document.getElementById("import_prompt_btn").addEventListener("click", async () => {{
      const file = promptFileEl.files && promptFileEl.files[0];
      if (!file) {{
        setGptStatus("请先选择提示词文件。");
        return;
      }}
      const fallbackName = (file.name || "").replace(/\.[^/.]+$/, "");
      const name = ((promptNameEl.value || "").trim() || fallbackName).trim();
      const form = new FormData();
      form.append("prompt_name", name);
      form.append("prompt_file", file);
      setGptStatus("正在导入提示词...");
      try {{
        const resp = await fetch("/import_prompt", {{ method: "POST", body: form }});
        const data = await resp.json();
        if (!data.ok) {{
          setGptStatus("导入失败: " + (data.error || "unknown"));
          return;
        }}
        while (promptSelectEl.firstChild) promptSelectEl.removeChild(promptSelectEl.firstChild);
        const prompts = data.prompts || {{}};
        promptMap = prompts;
        const emptyOpt = document.createElement("option");
        emptyOpt.value = "";
        emptyOpt.textContent = "不使用模板";
        promptSelectEl.appendChild(emptyOpt);
        Object.keys(prompts).forEach((k) => {{
          const opt = document.createElement("option");
          opt.value = k;
          opt.textContent = k;
          promptSelectEl.appendChild(opt);
        }});
        promptSelectEl.value = data.selected_prompt || name;
        setGptStatus("提示词导入成功。");
      }} catch (e) {{
        setGptStatus("导入失败: " + e);
      }}
    }});

    document.getElementById("import_api_key_btn").addEventListener("click", async () => {{
      const provider = (providerEl.value || "chatgpt").trim();
      if (provider === "local") {{
        setGptStatus("本地大模型不需要 API Key。");
        return;
      }}
      const existed = provider === "chatgpt" ? hasChatgptKeySaved : hasDeepseekKeySaved;
      if (existed) {{
        const ok = window.confirm("已存在 API Key，是否覆盖？");
        if (!ok) return;
      }}
      const key = window.prompt("请输入 " + (provider === "chatgpt" ? "ChatGPT" : "DeepSeek") + " API Key:");
      if (!key || !key.trim()) {{
        setGptStatus("未输入 API Key。");
        return;
      }}
      setGptStatus("正在保存 API Key...");
      try {{
        const data = await postForm("/save_api_key", {{
          provider: provider,
          api_key: key.trim(),
        }});
        if (!data.ok) {{
          setGptStatus("保存失败: " + (data.error || "unknown"));
          return;
        }}
        if (apiKeyStateEl) {{
          apiKeyStateEl.textContent = "ChatGPT Key: " + (data.has_chatgpt_key ? "已保存" : "未保存") + " | DeepSeek Key: " + (data.has_deepseek_key ? "已保存" : "未保存");
        }}
        setGptStatus(data.overwritten ? "API Key 已覆盖保存。" : "API Key 保存成功。");
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }}
    }});

    document.getElementById("analyze_btn").addEventListener("click", async () => {{
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const selectedPrompt = promptSelectEl.value || "";
      const customPrompt = (customPromptEl.value || "").trim();
      setGptStatus("正在调用 AI 分析，请稍候...");
      gptResultEl.textContent = "分析中...";
        try {{
            const data = await postForm("/analyze_job", {{
          job_id: jobId,
          provider: provider,
          chatgpt_model: chatgptModel,
          local_base_url: localBaseUrl,
          local_model: localModel,
          deepseek_model: deepseekModel,
          prompt_key: selectedPrompt,
          custom_prompt: customPrompt,
        }});
          if (!data.ok) {{
            gptResultEl.textContent = "分析失败: " + (data.error || "unknown");
            setGptStatus("分析失败。");
            return;
          }}
          gptResultEl.textContent = data.analysis || "(empty)";
          if (data.provider_used === "local") {{
            setGptStatus("分析完成。来源: LM Studio | " + (data.local_base_url || "") + " | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
          }} else if (data.provider_used === "deepseek") {{
            setGptStatus("分析完成。来源: DeepSeek | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
          }} else {{
            setGptStatus("分析完成。来源: ChatGPT | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
          }}
        }} catch (e) {{
          gptResultEl.textContent = "分析失败: " + e;
          setGptStatus("分析失败。");
      }}
    }});

    document.getElementById("analyze_history_btn").addEventListener("click", async () => {{
      const file = historyReportFileEl.files && historyReportFileEl.files[0];
      if (!file) {{
        setGptStatus("请先选择历史报告文件。");
        return;
      }}
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const selectedPrompt = promptSelectEl.value || "";
      const customPrompt = (customPromptEl.value || "").trim();
      setGptStatus("正在分析历史报告，请稍候...");
      gptResultEl.textContent = "分析中...";
      try {{
        const form = new FormData();
        form.append("provider", provider);
        form.append("chatgpt_model", chatgptModel);
        form.append("local_base_url", localBaseUrl);
        form.append("local_model", localModel);
        form.append("deepseek_model", deepseekModel);
        form.append("prompt_key", selectedPrompt);
        form.append("custom_prompt", customPrompt);
        form.append("report_file", file);
        const resp = await fetch("/analyze_history_report", {{ method: "POST", body: form }});
        const data = await resp.json();
        if (!data.ok) {{
          gptResultEl.textContent = "分析失败: " + (data.error || "unknown");
          setGptStatus("分析失败。");
          return;
        }}
        gptResultEl.textContent = data.analysis || "(empty)";
        if (data.provider_used === "local") {{
          setGptStatus("分析完成。来源: LM Studio | " + (data.local_base_url || "") + " | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
        }} else if (data.provider_used === "deepseek") {{
          setGptStatus("分析完成。来源: DeepSeek | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
        }} else {{
          setGptStatus("分析完成。来源: ChatGPT | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || ""));
        }}
      }} catch (e) {{
        gptResultEl.textContent = "分析失败: " + e;
        setGptStatus("分析失败。");
      }}
    }});

    providerEl.addEventListener("change", refreshProviderUI);
    if (chatgptModelSelectEl) chatgptModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (localModelSelectEl) localModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (deepseekModelSelectEl) deepseekModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (localModelSelectEl) localModelSelectEl.addEventListener("change", refreshProviderUI);
    if (localModelCustomEl) localModelCustomEl.addEventListener("input", refreshProviderUI);
    refreshProviderUI();

    async function poll() {{
      try {{
        const resp = await fetch("/job_status?id=" + encodeURIComponent(jobId), {{ cache: "no-store" }});
        if (!resp.ok) {{
          setState("error", "-");
          outputEl.textContent = "状态获取失败: HTTP " + resp.status;
          return;
        }}
        const data = await resp.json();
        outputEl.textContent = data.output || "";
        setState(data.status, data.exit_code);
        renderReports(data);
        if (data.status === "running") {{
          setTimeout(poll, 1500);
        }}
      }} catch (e) {{
        setState("error", "-");
        outputEl.textContent = "状态获取异常: " + e;
      }}
    }}

    poll();
  </script>
</body>
</html>"""


def extract_report_name(line: str, suffix: str) -> str:
    m = re.search(rf"inspection_report_[A-Za-z0-9_]+{re.escape(suffix)}", line)
    return m.group(0) if m else ""


def clean_output_line(line: str) -> str:
    noisy_keywords = [
        "getpass.py",
        "GetPassWarning",
        "fallback_getpass(prompt, stream)",
        "Warning: Password input may be echoed.",
        "SSH username:",
        "SSH password:",
        "Command map file (default: config/command_map.yaml):",
        "Device addresses (comma/semicolon/newline) or file path:",
        "Commands or intents (e.g. @version), comma/semicolon/newline, or file path:",
        "Execution mode serial/parallel/auto (default: auto):",
        "Parallel workers (optional, auto if empty):",
        "Connection retry count (default: 0):",
        "Enable live debug output? (y/N):",
    ]
    for keyword in noisy_keywords:
        if keyword in line:
            return ""
    return line


def normalize_inline_input(raw: str) -> str:
    parts = [p.strip() for p in re.split(r"[,;\n]+", raw or "") if p.strip() and not p.strip().startswith("#")]
    parts = list(dict.fromkeys(parts))
    return ";".join(parts)


def parse_ordered_items(raw: str) -> List[str]:
    return [p.strip() for p in re.split(r"[,;\n]+", raw or "") if p.strip() and not p.strip().startswith("#")]


def start_job(
    username: str,
    password: str,
    devices: str,
    selected: List[str],
    custom_commands: str,
    map_bytes: bytes,
    debug_mode: bool,
    execution_mode: str,
    parallel_workers: str,
    connect_retry: str,
) -> str:
    job_id = uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "output": "", "exit_code": None, "report_json": "", "report_csv": ""}

    def _append(text: str) -> None:
        if not text:
            return
        with JOBS_LOCK:
            old = JOBS.get(job_id, {}).get("output", "")
            merged = old + text
            if len(merged) > 500_000:
                merged = merged[-500_000:]
            if job_id in JOBS:
                JOBS[job_id]["output"] = merged

    def _worker() -> None:
        map_tmp = None
        try:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("wb", suffix=".yaml", delete=False, dir=TMP_DIR) as tmp:
                tmp.write(map_bytes)
                map_tmp = tmp.name

            device_input = normalize_inline_input(devices)
            custom_items = parse_ordered_items(custom_commands)
            checks_input = normalize_inline_input("\n".join(selected + custom_items))
            stdin_text = "\n".join(
                [
                    username,
                    password,
                    map_tmp,
                    device_input,
                    checks_input,
                    execution_mode,
                    parallel_workers,
                    connect_retry,
                    "y" if debug_mode else "n",
                ]
            ) + "\n"

            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(PROJECT_ROOT),
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                preexec_fn=os.setsid,
            )
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(stdin_text)
            proc.stdin.close()

            for line in proc.stdout:
                if debug_mode:
                    _append(line)
                else:
                    cleaned = clean_output_line(line)
                    if cleaned:
                        _append(cleaned)
                json_name = extract_report_name(line, ".json")
                csv_name = extract_report_name(line, ".csv")
                if json_name or csv_name:
                    with JOBS_LOCK:
                        if job_id in JOBS:
                            if json_name:
                                JOBS[job_id]["report_json"] = json_name
                            if csv_name:
                                JOBS[job_id]["report_csv"] = csv_name

            exit_code = proc.wait()
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["exit_code"] = exit_code
                    JOBS[job_id]["status"] = "success" if exit_code == 0 else "error"
        except Exception as exc:
            _append(f"\n[web_runner_error] {exc}\n")
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["exit_code"] = -1
                    JOBS[job_id]["status"] = "error"
        finally:
            if map_tmp and os.path.exists(map_tmp):
                os.remove(map_tmp)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._respond_html(build_html(default_form_values(), DEFAULT_CHECKS[:3], "", ""))
            return
        if parsed.path == "/job":
            query = parse_qs(parsed.query)
            job_id = (query.get("id", [""])[0] or "").strip()
            if not job_id:
                self.send_error(400, "Missing job id")
                return
            self._respond_html(build_job_html(job_id))
            return
        if parsed.path == "/job_status":
            self._serve_job_status(parsed.query)
            return
        if parsed.path == "/download":
            self._serve_download(parsed.query)
            return
        self.send_error(404, "Not Found")
        return

    def _serve_download(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        name = (query.get("name", [""])[0] or "").strip()
        if not is_safe_report_name(name):
            self.send_error(400, "Invalid report name")
            return

        target = REPORT_DIR / name
        if not target.is_file():
            self.send_error(404, "Not Found")
            return

        data = target.read_bytes()
        ctype, _ = mimetypes.guess_type(target.name)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _serve_job_status(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        job_id = (query.get("id", [""])[0] or "").strip()
        if not job_id:
            self.send_error(400, "Missing job id")
            return

        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                payload = {"status": "error", "exit_code": -1, "output": "任务不存在或已过期"}
            else:
                payload = {
                    "status": job.get("status", "error"),
                    "exit_code": job.get("exit_code"),
                    "output": job.get("output", ""),
                    "report_json": job.get("report_json", ""),
                    "report_csv": job.get("report_csv", ""),
                }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_analysis_input(self, job: Dict) -> str:
        output = str(job.get("output", "") or "")
        report_name = str(job.get("report_json", "") or "")
        report_text = ""
        if report_name and is_safe_report_name(report_name):
            report_path = REPORT_DIR / report_name
            if report_path.is_file():
                report_text = report_path.read_text(encoding="utf-8", errors="ignore")

        output = output[-60000:]
        report_text = report_text[-120000:]
        return f"任务日志（可能已截断）：\n{output}\n\n结构化报告JSON（可能已截断）：\n{report_text}"

    def _handle_save_gpt_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek"}:
            provider = "chatgpt"
        chatgpt_model = (form.getvalue("chatgpt_model") or DEFAULT_GPT_MODEL).strip()
        local_base_url = (form.getvalue("local_base_url") or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = (form.getvalue("local_model") or DEFAULT_LOCAL_MODEL).strip()
        deepseek_model = (form.getvalue("deepseek_model") or DEFAULT_DEEPSEEK_MODEL).strip()
        selected_prompt = (form.getvalue("selected_prompt") or "").strip()
        if provider == "chatgpt" and not chatgpt_model:
            self._respond_json({"ok": False, "error": "chatgpt_model required"}, status=400)
            return
        if provider == "local" and (not local_base_url or not local_model):
            self._respond_json({"ok": False, "error": "local_base_url/local_model required"}, status=400)
            return
        if provider == "deepseek" and not deepseek_model:
            self._respond_json({"ok": False, "error": "deepseek_model required"}, status=400)
            return
        cfg = load_gpt_config()
        cfg["provider"] = provider
        cfg["chatgpt_model"] = chatgpt_model
        cfg["local_base_url"] = local_base_url
        cfg["local_model"] = local_model
        cfg["deepseek_model"] = deepseek_model
        cfg["selected_prompt"] = selected_prompt
        save_gpt_config(cfg)
        self._respond_json({"ok": True})

    def _handle_save_api_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        api_key = (form.getvalue("api_key") or "").strip()
        if provider not in {"chatgpt", "deepseek"}:
            self._respond_json({"ok": False, "error": "provider must be chatgpt/deepseek"}, status=400)
            return
        if not api_key:
            self._respond_json({"ok": False, "error": "API Key is empty"}, status=400)
            return
        cfg = load_gpt_config()
        key_field = "chatgpt_api_key" if provider == "chatgpt" else "deepseek_api_key"
        overwritten = bool(str(cfg.get(key_field, "") or "").strip())
        cfg[key_field] = api_key
        save_gpt_config(cfg)
        self._respond_json(
            {
                "ok": True,
                "overwritten": overwritten,
                "has_chatgpt_key": bool(str(cfg.get("chatgpt_api_key", "") or "").strip()),
                "has_deepseek_key": bool(str(cfg.get("deepseek_api_key", "") or "").strip()),
            }
        )

    def _handle_import_prompt(self, form: cgi.FieldStorage) -> None:
        upload = form["prompt_file"] if "prompt_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Prompt file is required"}, status=400)
            return
        raw_name = (form.getvalue("prompt_name") or "").strip()
        if not raw_name:
            raw_name = Path(str(upload.filename)).stem
        prompt_name = sanitize_prompt_name(raw_name)
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        raw = upload.file.read()
        if not raw:
            self._respond_json({"ok": False, "error": "Prompt file is empty"}, status=400)
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="ignore")
        text = text.strip()
        if not text:
            self._respond_json({"ok": False, "error": "Prompt file has no valid text"}, status=400)
            return

        cfg = load_gpt_config()
        if not write_prompt_file(CUSTOM_PROMPTS_DIR, prompt_name, text):
            self._respond_json({"ok": False, "error": "提示词模板保存失败"}, status=500)
            return
        cfg["custom_prompts"] = {}
        save_gpt_config(cfg)
        self._respond_json({"ok": True, "prompts": merged_prompt_catalog(), "selected_prompt": prompt_name})

    def _handle_test_llm(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        local_base_url = (form.getvalue("local_base_url") or "").strip()
        deepseek_model = (form.getvalue("deepseek_model") or "").strip()
        cfg = load_gpt_config()
        if provider not in {"chatgpt", "local", "deepseek"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek"}:
                provider = "chatgpt"

        try:
            if provider == "local":
                if not local_base_url:
                    local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
                msg = test_local_lmstudio_connection(local_base_url)
                self._respond_json({"ok": True, "message": msg, "provider_used": "local"})
                return

            if provider == "deepseek":
                api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "DeepSeek API Key not set"}, status=400)
                    return
                _ = deepseek_model  # reserved for future model validation call
                msg = test_deepseek_connection(api_key)
                self._respond_json({"ok": True, "message": msg, "provider_used": "deepseek"})
                return

            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
            if not api_key:
                self._respond_json({"ok": False, "error": "ChatGPT API Key not set"}, status=400)
                return
            msg = test_openai_connection(api_key)
            self._respond_json({"ok": True, "message": msg, "provider_used": "chatgpt"})
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)

    def _resolve_llm_inputs_from_form(self, form: cgi.FieldStorage) -> Dict[str, str]:
        cfg = load_gpt_config()
        provider = (form.getvalue("provider") or "").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek"}:
                provider = "chatgpt"
        local_base_url = (form.getvalue("local_base_url") or "").strip() or str(
            cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL
        ).strip()
        chatgpt_model = (form.getvalue("chatgpt_model") or "").strip() or str(
            cfg.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL
        ).strip()
        local_model = (form.getvalue("local_model") or "").strip() or str(
            cfg.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL
        ).strip()
        deepseek_model = (form.getvalue("deepseek_model") or "").strip() or str(
            cfg.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL
        ).strip()
        if provider == "deepseek":
            api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
        elif provider == "chatgpt":
            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
        else:
            api_key = ""
        prompt_key = (form.getvalue("prompt_key") or "").strip()
        custom_prompt = (form.getvalue("custom_prompt") or "").strip()
        prompts = merged_prompt_catalog()
        template_prompt = prompts.get(prompt_key, "") if prompt_key else ""
        extra_prompt = custom_prompt
        if template_prompt and extra_prompt:
            prompt_text = f"{template_prompt}\n\n【追加分析要求】\n{extra_prompt}"
            prompt_source = f"template+extra:{prompt_key}"
        elif template_prompt:
            prompt_text = template_prompt
            prompt_source = f"template:{prompt_key}"
        elif extra_prompt:
            prompt_text = extra_prompt
            prompt_source = "extra_only"
        else:
            prompt_text = DEFAULT_NETWORK_PROMPTS["基础巡检诊断"]
            prompt_source = "default:基础巡检诊断"
        return {
            "provider": provider,
            "api_key": api_key,
            "chatgpt_model": chatgpt_model,
            "local_base_url": local_base_url,
            "local_model": local_model,
            "deepseek_model": deepseek_model,
            "prompt_text": prompt_text,
            "prompt_key": prompt_key,
            "prompt_source": prompt_source,
        }

    def _handle_analyze_job(self, form: cgi.FieldStorage) -> None:
        job_id = (form.getvalue("job_id") or "").strip()
        llm = self._resolve_llm_inputs_from_form(form)
        cfg = load_gpt_config()
        cfg["selected_prompt"] = llm.get("prompt_key", "")
        save_gpt_config(cfg)

        if not job_id:
            self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            self._respond_json({"ok": False, "error": "job not found"}, status=404)
            return

        analysis_input = self._build_analysis_input(job)
        try:
            if llm["provider"] == "local":
                analysis = call_local_lmstudio_analysis(
                    base_url=llm["local_base_url"],
                    model=llm["local_model"],
                    prompt_text=llm["prompt_text"],
                    report_text=analysis_input,
                )
            elif llm["provider"] == "deepseek":
                if not llm["api_key"]:
                    self._respond_json({"ok": False, "error": "DeepSeek API Key not set"}, status=400)
                    return
                analysis = call_deepseek_analysis(
                    api_key=llm["api_key"],
                    model=llm["deepseek_model"],
                    prompt_text=llm["prompt_text"],
                    report_text=analysis_input,
                )
            else:
                if not llm["api_key"]:
                    self._respond_json({"ok": False, "error": "ChatGPT API Key not set"}, status=400)
                    return
                analysis = call_openai_analysis(
                    api_key=llm["api_key"],
                    prompt_text=llm["prompt_text"],
                    report_text=analysis_input,
                    model=llm["chatgpt_model"],
                )
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": (
                    llm["local_model"]
                    if llm["provider"] == "local"
                    else (llm["deepseek_model"] if llm["provider"] == "deepseek" else llm["chatgpt_model"])
                ),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
            }
        )

    def _handle_analyze_history_report(self, form: cgi.FieldStorage) -> None:
        upload = form["report_file"] if "report_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "report_file is required"}, status=400)
            return
        try:
            report_text = read_uploaded_report(upload)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=400)
            return

        llm = self._resolve_llm_inputs_from_form(form)
        cfg = load_gpt_config()
        cfg["selected_prompt"] = llm.get("prompt_key", "")
        save_gpt_config(cfg)
        try:
            if llm["provider"] == "local":
                analysis = call_local_lmstudio_analysis(
                    base_url=llm["local_base_url"],
                    model=llm["local_model"],
                    prompt_text=llm["prompt_text"],
                    report_text=report_text,
                )
            elif llm["provider"] == "deepseek":
                if not llm["api_key"]:
                    self._respond_json({"ok": False, "error": "DeepSeek API Key not set"}, status=400)
                    return
                analysis = call_deepseek_analysis(
                    api_key=llm["api_key"],
                    model=llm["deepseek_model"],
                    prompt_text=llm["prompt_text"],
                    report_text=report_text,
                )
            else:
                if not llm["api_key"]:
                    self._respond_json({"ok": False, "error": "ChatGPT API Key not set"}, status=400)
                    return
                analysis = call_openai_analysis(
                    api_key=llm["api_key"],
                    prompt_text=llm["prompt_text"],
                    report_text=report_text,
                    model=llm["chatgpt_model"],
                )
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": (
                    llm["local_model"]
                    if llm["provider"] == "local"
                    else (llm["deepseek_model"] if llm["provider"] == "deepseek" else llm["chatgpt_model"])
                ),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
            }
        )

    def do_POST(self) -> None:
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ)
        if self.path == "/save_gpt_key":
            self._handle_save_gpt_key(form)
            return
        if self.path == "/import_prompt":
            self._handle_import_prompt(form)
            return
        if self.path == "/save_api_key":
            self._handle_save_api_key(form)
            return
        if self.path == "/test_llm":
            self._handle_test_llm(form)
            return
        if self.path == "/analyze_job":
            self._handle_analyze_job(form)
            return
        if self.path == "/analyze_history_report":
            self._handle_analyze_history_report(form)
            return
        if self.path != "/run":
            self.send_error(404, "Not Found")
            return

        username = (form.getvalue("username") or "").strip()
        password = (form.getvalue("password") or "").strip()
        manual_devices = (form.getvalue("devices") or "").strip()
        execution_mode = (form.getvalue("execution_mode") or "auto").strip().lower()
        if execution_mode not in {"serial", "parallel", "auto"}:
            execution_mode = "auto"
        parallel_workers = (form.getvalue("parallel_workers") or "").strip()
        if parallel_workers:
            try:
                parallel_workers = str(max(1, int(parallel_workers)))
            except ValueError:
                parallel_workers = ""
        connect_retry = (form.getvalue("connect_retry") or "0").strip()
        try:
            connect_retry = str(max(0, int(connect_retry)))
        except ValueError:
            connect_retry = "0"
        custom_commands = (form.getvalue("custom_commands") or "").strip()
        debug_mode = (form.getvalue("debug_mode") or "").strip() in {"1", "true", "y", "yes", "on"}
        selected = form.getlist("checks")
        selected = [item.strip() for item in selected if item and item.strip()]
        devices_upload = form["devices_file"] if "devices_file" in form else None
        imported_devices = ""
        if devices_upload is not None and getattr(devices_upload, "filename", "") and not manual_devices:
            raw_bytes = devices_upload.file.read()
            if raw_bytes:
                try:
                    imported_devices = raw_bytes.decode("utf-8-sig")
                except UnicodeDecodeError:
                    imported_devices = raw_bytes.decode("gb18030", errors="ignore")
        devices = normalize_inline_input(manual_devices or imported_devices)

        values = {
            "username": username,
            "password": password,
            "devices": manual_devices,
            "custom_commands": custom_commands,
            "execution_mode": execution_mode,
            "parallel_workers": parallel_workers,
            "connect_retry": connect_retry,
            "debug_mode": "1" if debug_mode else "",
        }

        if not username or not password:
            self._respond_html(build_html(values, selected, "", "ERROR: 用户名和密码不能为空"))
            return
        if not devices:
            self._respond_html(build_html(values, selected, "", "ERROR: 请输入设备地址或导入设备文件"))
            return
        if not selected and not parse_ordered_items(custom_commands):
            self._respond_html(build_html(values, selected, "", "ERROR: 请至少选择一个检查项或输入一条自定义命令"))
            return

        upload = form["command_map"] if "command_map" in form else None
        try:
            data = b""
            if upload is not None and getattr(upload, "filename", ""):
                data = upload.file.read()
                if not data:
                    self._respond_html(build_html(values, selected, "", "ERROR: 上传的 command_map 文件为空"))
                    return
            else:
                default_map = COMMAND_MAP_PATH
                if not default_map.is_file():
                    self._respond_html(
                        build_html(values, selected, "", "ERROR: 默认 config/command_map.yaml 不存在，请上传文件")
                    )
                    return
                data = default_map.read_bytes()

            job_id = start_job(
                username=username,
                password=password,
                devices=devices,
                selected=selected,
                custom_commands=custom_commands,
                map_bytes=data,
                debug_mode=debug_mode,
                execution_mode=execution_mode,
                parallel_workers=parallel_workers,
                connect_retry=connect_retry,
            )
            self.send_response(303)
            self.send_header("Location", f"/job?id={job_id}")
            self.end_headers()
        except Exception as exc:
            self._respond_html(build_html(values, selected, "", f"ERROR: {exc}"))

    def _respond_html(self, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    initialize_default_prompt_files()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"HealthCheck Web Runner started at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("HC_WEB_HOST", "0.0.0.0")
    port_str = os.environ.get("HC_WEB_PORT", "8080")
    try:
        port = int(port_str)
    except ValueError:
        port = 8080
    run_server(host=host, port=port)
