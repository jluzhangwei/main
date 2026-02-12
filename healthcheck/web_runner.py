#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import cgi
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

BASE_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = BASE_DIR / "healthcheck.py"
INTENTS_PATH = BASE_DIR / "intents.txt"
REPORT_DIR = BASE_DIR / "reports"
GPT_CONFIG_PATH = BASE_DIR / "gpt_config.json"
DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_LOCAL_BASE_URL = "http://192.168.0.99:1234"
DEFAULT_LOCAL_MODEL = "qwen/qwen3-coder-30b"
JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()

DEFAULT_NETWORK_PROMPTS: Dict[str, str] = {
    "基础巡检诊断": (
        "你是网络运维专家。请基于巡检结果给出："
        "1) 总体健康结论；2) 关键异常点；3) 可能根因；"
        "4) 优先级最高的3条处理建议。"
    ),
    "接口与链路诊断": (
        "你是网络接口诊断专家。重点检查接口 up/down、协议状态、错误计数、光模块/邻居信息。"
        "请输出：异常接口清单、影响范围、排查顺序。"
    ),
    "路由与协议诊断": (
        "你是网络协议诊断专家。重点分析路由摘要、BGP/OSPF 邻居、NTP、STP。"
        "请输出：潜在协议异常、可能影响、修复建议。"
    ),
}


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
            "api_key": "",
            "custom_prompts": {},
            "provider": "openai",
            "local_base_url": DEFAULT_LOCAL_BASE_URL,
            "local_model": DEFAULT_LOCAL_MODEL,
        }
    try:
        data = json.loads(GPT_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {
                "api_key": "",
                "custom_prompts": {},
                "provider": "openai",
                "local_base_url": DEFAULT_LOCAL_BASE_URL,
                "local_model": DEFAULT_LOCAL_MODEL,
            }
        api_key = data.get("api_key", "")
        custom_prompts = data.get("custom_prompts", {})
        provider = (data.get("provider", "openai") or "openai").strip().lower()
        if provider not in {"openai", "local"}:
            provider = "openai"
        local_base_url = str(data.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = str(data.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL).strip()
        if not isinstance(custom_prompts, dict):
            custom_prompts = {}
        return {
            "api_key": str(api_key or ""),
            "custom_prompts": custom_prompts,
            "provider": provider,
            "local_base_url": local_base_url,
            "local_model": local_model,
        }
    except Exception:
        return {
            "api_key": "",
            "custom_prompts": {},
            "provider": "openai",
            "local_base_url": DEFAULT_LOCAL_BASE_URL,
            "local_model": DEFAULT_LOCAL_MODEL,
        }


def save_gpt_config(config: Dict) -> None:
    provider = str(config.get("provider", "openai") or "openai").strip().lower()
    if provider not in {"openai", "local"}:
        provider = "openai"
    payload = {
        "api_key": str(config.get("api_key", "") or ""),
        "custom_prompts": config.get("custom_prompts", {}) if isinstance(config.get("custom_prompts", {}), dict) else {},
        "provider": provider,
        "local_base_url": str(config.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip(),
        "local_model": str(config.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL).strip(),
    }
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
    custom = config.get("custom_prompts", {}) if isinstance(config.get("custom_prompts"), dict) else {}
    merged = dict(DEFAULT_NETWORK_PROMPTS)
    for key, value in custom.items():
        if key and isinstance(value, str) and value.strip():
            merged[str(key)] = value.strip()
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
      <form method="post" action="/run" enctype="multipart/form-data">
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
          <label>导入 command_map 文件（可选，默认使用当前目录 command_map.yaml）</label>
          <input type="file" name="command_map" accept=".yaml,.yml">
          <div class="tips">不上传时默认使用 `healthcheck/command_map.yaml`；上传时会临时覆盖本次任务。</div>
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
        </div>
      </form>
      {output_block}
    </div>
  </div>
<script>
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
  buildPreview();
</script>
</body>
</html>
"""


def build_job_html(job_id: str) -> str:
    gpt_config = load_gpt_config()
    prompts = merged_prompt_catalog()
    prompt_options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>' for name in prompts.keys()
    )
    has_saved_key = bool((gpt_config.get("api_key") or "").strip())
    provider = str(gpt_config.get("provider", "openai") or "openai")
    local_base_url = str(gpt_config.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL)
    local_model = str(gpt_config.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL)
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
        <h3 style="margin:0;">GPT 分析</h3>
        <div class="gpt-grid gpt-row">
          <div>
            <label>分析方式</label>
            <select id="provider_select">
              <option value="openai" {"selected" if provider == "openai" else ""}>OpenAI API</option>
              <option value="local" {"selected" if provider == "local" else ""}>LM Studio（本地）</option>
            </select>
          </div>
          <div>
            <label>OpenAI API Key</label>
            <input id="gpt_api_key" type="password" placeholder="{ '已保存，可留空直接使用' if has_saved_key else 'sk-...'}">
            <div class="gpt-hint">输入后可点击“保存 LLM 配置”，下次无需重复输入。</div>
          </div>
        </div>
        <div class="gpt-grid gpt-row">
          <div>
            <label>LM Studio 地址</label>
            <input id="local_base_url" type="text" value="{html.escape(local_base_url)}" placeholder="http://127.0.0.1:1234">
          </div>
          <div>
            <label>LM Studio 模型</label>
            <input id="local_model" type="text" value="{html.escape(local_model)}" placeholder="qwen/qwen3-coder-30b">
          </div>
        </div>
        <div class="gpt-grid gpt-row">
          <div>
            <label>提示词模板</label>
            <select id="prompt_select">{prompt_options}</select>
            <div class="gpt-hint">可选择默认模板，也可导入/覆盖自定义模板。</div>
          </div>
          <div></div>
        </div>
        <div class="gpt-grid gpt-row">
          <div>
            <label>导入提示词文件（txt）</label>
            <input id="prompt_file" type="file" accept=".txt">
          </div>
          <div>
            <label>导入模板名称</label>
            <input id="prompt_name" type="text" placeholder="例如：核心链路专项诊断">
          </div>
        </div>
        <div class="gpt-row">
          <label>临时提示词（可选，优先于模板）</label>
          <textarea id="custom_prompt" placeholder="不填则使用上面模板内容"></textarea>
        </div>
        <div class="gpt-actions">
          <button class="gpt-btn" id="save_key_btn" type="button">保存 LLM 配置</button>
          <button class="gpt-btn" id="test_llm_btn" type="button">连接测试</button>
          <button class="gpt-btn" id="import_prompt_btn" type="button">导入提示词</button>
          <button class="gpt-btn gpt-primary" id="analyze_btn" type="button">GPT 分析本次结果</button>
        </div>
        <div id="gpt_status" class="gpt-hint"></div>
        <div id="gpt_result">分析结果会显示在这里。</div>
      </div>
    </div>
  </div>
  <script>
    const jobId = {json.dumps(job_id)};
    const promptMap = {json.dumps(prompts, ensure_ascii=False)};
    const stateEl = document.getElementById("state");
    const outputEl = document.getElementById("output");
    const reportEl = document.getElementById("reports");
    const keyEl = document.getElementById("gpt_api_key");
    const providerEl = document.getElementById("provider_select");
    const localBaseEl = document.getElementById("local_base_url");
    const localModelEl = document.getElementById("local_model");
    const promptSelectEl = document.getElementById("prompt_select");
    const promptFileEl = document.getElementById("prompt_file");
    const promptNameEl = document.getElementById("prompt_name");
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

    async function postForm(url, formBody) {{
      const resp = await fetch(url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }},
        body: new URLSearchParams(formBody),
      }});
      return await resp.json();
    }}

    document.getElementById("save_key_btn").addEventListener("click", async () => {{
      const key = (keyEl.value || "").trim();
      const provider = (providerEl.value || "openai").trim();
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = (localModelEl.value || "").trim();
      if (provider === "openai" && !key) {{
        setGptStatus("OpenAI 模式下请先输入 API Key。");
        return;
      }}
      if (provider === "local" && (!localBaseUrl || !localModel)) {{
        setGptStatus("LM Studio 模式下请填写地址和模型。");
        return;
      }}
      setGptStatus("正在保存配置...");
      try {{
        const data = await postForm("/save_gpt_key", {{
          api_key: key,
          provider: provider,
          local_base_url: localBaseUrl,
          local_model: localModel,
        }});
        setGptStatus(data.ok ? "LLM 配置保存成功。" : ("保存失败: " + (data.error || "unknown")));
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }}
    }});

    document.getElementById("test_llm_btn").addEventListener("click", async () => {{
      const key = (keyEl.value || "").trim();
      const provider = (providerEl.value || "openai").trim();
      const localBaseUrl = (localBaseEl.value || "").trim();
      setGptStatus("正在测试连接...");
      try {{
        const data = await postForm("/test_llm", {{
          provider: provider,
          api_key: key,
          local_base_url: localBaseUrl,
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
      const name = (promptNameEl.value || "").trim();
      const file = promptFileEl.files && promptFileEl.files[0];
      if (!name || !file) {{
        setGptStatus("请同时选择提示词文件并填写模板名称。");
        return;
      }}
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

    document.getElementById("analyze_btn").addEventListener("click", async () => {{
      const key = (keyEl.value || "").trim();
      const provider = (providerEl.value || "openai").trim();
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = (localModelEl.value || "").trim();
      const selectedPrompt = promptSelectEl.value || "";
      const customPrompt = (customPromptEl.value || "").trim();
      setGptStatus("正在调用 GPT 分析，请稍候...");
      gptResultEl.textContent = "分析中...";
        try {{
            const data = await postForm("/analyze_job", {{
              job_id: jobId,
              api_key: key,
              provider: provider,
              local_base_url: localBaseUrl,
          local_model: localModel,
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
            setGptStatus("分析完成。来源: LM Studio | " + (data.local_base_url || "") + " | " + (data.model_used || ""));
          }} else {{
            setGptStatus("分析完成。来源: OpenAI | " + (data.model_used || ""));
          }}
        }} catch (e) {{
          gptResultEl.textContent = "分析失败: " + e;
          setGptStatus("分析失败。");
        }}
      }});

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
        "Command map file (default: command_map.yaml):",
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
            with tempfile.NamedTemporaryFile("wb", suffix=".yaml", delete=False, dir=BASE_DIR) as tmp:
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
                cwd=str(BASE_DIR),
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
        api_key = (form.getvalue("api_key") or "").strip()
        provider = (form.getvalue("provider") or "openai").strip().lower()
        if provider not in {"openai", "local"}:
            provider = "openai"
        local_base_url = (form.getvalue("local_base_url") or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = (form.getvalue("local_model") or DEFAULT_LOCAL_MODEL).strip()
        if provider == "openai" and not api_key:
            self._respond_json({"ok": False, "error": "API Key is empty"}, status=400)
            return
        if provider == "local" and (not local_base_url or not local_model):
            self._respond_json({"ok": False, "error": "local_base_url/local_model required"}, status=400)
            return
        cfg = load_gpt_config()
        cfg["api_key"] = api_key
        cfg["provider"] = provider
        cfg["local_base_url"] = local_base_url
        cfg["local_model"] = local_model
        save_gpt_config(cfg)
        self._respond_json({"ok": True})

    def _handle_import_prompt(self, form: cgi.FieldStorage) -> None:
        prompt_name = sanitize_prompt_name((form.getvalue("prompt_name") or "").strip())
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        upload = form["prompt_file"] if "prompt_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Prompt file is required"}, status=400)
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
        custom = cfg.get("custom_prompts", {})
        if not isinstance(custom, dict):
            custom = {}
        custom[prompt_name] = text
        cfg["custom_prompts"] = custom
        save_gpt_config(cfg)
        self._respond_json({"ok": True, "prompts": merged_prompt_catalog(), "selected_prompt": prompt_name})

    def _handle_test_llm(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        api_key = (form.getvalue("api_key") or "").strip()
        local_base_url = (form.getvalue("local_base_url") or "").strip()
        cfg = load_gpt_config()
        if provider not in {"openai", "local"}:
            provider = str(cfg.get("provider", "openai") or "openai").strip().lower()
            if provider not in {"openai", "local"}:
                provider = "openai"

        try:
            if provider == "local":
                if not local_base_url:
                    local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
                msg = test_local_lmstudio_connection(local_base_url)
                self._respond_json({"ok": True, "message": msg, "provider_used": "local"})
                return

            if not api_key:
                api_key = str(cfg.get("api_key", "") or "").strip()
            if not api_key:
                self._respond_json({"ok": False, "error": "API Key not set"}, status=400)
                return
            msg = test_openai_connection(api_key)
            self._respond_json({"ok": True, "message": msg, "provider_used": "openai"})
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)

    def _handle_analyze_job(self, form: cgi.FieldStorage) -> None:
        job_id = (form.getvalue("job_id") or "").strip()
        api_key = (form.getvalue("api_key") or "").strip()
        provider = (form.getvalue("provider") or "").strip().lower()
        local_base_url = (form.getvalue("local_base_url") or "").strip()
        local_model = (form.getvalue("local_model") or "").strip()
        prompt_key = (form.getvalue("prompt_key") or "").strip()
        custom_prompt = (form.getvalue("custom_prompt") or "").strip()

        if not job_id:
            self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            self._respond_json({"ok": False, "error": "job not found"}, status=404)
            return

        cfg = load_gpt_config()
        if provider not in {"openai", "local"}:
            provider = str(cfg.get("provider", "openai") or "openai").strip().lower()
            if provider not in {"openai", "local"}:
                provider = "openai"
        if not local_base_url:
            local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
        if not local_model:
            local_model = str(cfg.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL).strip()
        if not api_key:
            api_key = str(cfg.get("api_key", "") or "").strip()

        prompts = merged_prompt_catalog()
        prompt_text = custom_prompt or prompts.get(prompt_key, "") or DEFAULT_NETWORK_PROMPTS["基础巡检诊断"]
        analysis_input = self._build_analysis_input(job)
        try:
            if provider == "local":
                analysis = call_local_lmstudio_analysis(
                    base_url=local_base_url,
                    model=local_model,
                    prompt_text=prompt_text,
                    report_text=analysis_input,
                )
            else:
                if not api_key:
                    self._respond_json({"ok": False, "error": "API Key not set"}, status=400)
                    return
                analysis = call_openai_analysis(api_key=api_key, prompt_text=prompt_text, report_text=analysis_input)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": provider,
                "model_used": local_model if provider == "local" else DEFAULT_GPT_MODEL,
                "local_base_url": local_base_url if provider == "local" else "",
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
        if self.path == "/test_llm":
            self._handle_test_llm(form)
            return
        if self.path == "/analyze_job":
            self._handle_analyze_job(form)
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
                default_map = BASE_DIR / "command_map.yaml"
                if not default_map.is_file():
                    self._respond_html(build_html(values, selected, "", "ERROR: 默认 command_map.yaml 不存在，请上传文件"))
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
