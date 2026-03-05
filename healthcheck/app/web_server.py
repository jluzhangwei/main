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
import time
try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

try:
    from app import analysis_guard, analysis_pipeline, analysis_service, llm_adapter, llm_service, prompt_service, state_store, status_service, task_store
except ModuleNotFoundError:
    # Allow direct execution: python3 app/web_server.py
    _self_dir = Path(__file__).resolve().parent
    _parent_dir = _self_dir.parent
    if str(_parent_dir) not in sys.path:
        sys.path.insert(0, str(_parent_dir))
    from app import analysis_guard, analysis_pipeline, analysis_service, llm_adapter, llm_service, prompt_service, state_store, status_service, task_store

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
SCRIPT_PATH = APP_DIR / "healthcheck.py"
INTENTS_PATH = PROJECT_ROOT / "data" / "intents.txt"
REPORT_DIR = PROJECT_ROOT / "output" / "reports"
AI_REPORT_DIR = PROJECT_ROOT / "output" / "ai_reports"
TMP_DIR = PROJECT_ROOT / "runtime" / "tmp"
COMMAND_MAP_PATH = PROJECT_ROOT / "config" / "command_map.yaml"
CHECK_TEMPLATES_DIR = PROJECT_ROOT / "check_templates"
CHECK_DEFAULT_TEMPLATES_DIR = CHECK_TEMPLATES_DIR / "default"
CHECK_CUSTOM_TEMPLATES_DIR = CHECK_TEMPLATES_DIR / "custom"
SYSTEM_DEFAULT_PROMPTS_DIR = prompt_service.SYSTEM_DEFAULT_PROMPTS_DIR
SYSTEM_CUSTOM_PROMPTS_DIR = prompt_service.SYSTEM_CUSTOM_PROMPTS_DIR
TASK_DEFAULT_PROMPTS_DIR = prompt_service.TASK_DEFAULT_PROMPTS_DIR
TASK_CUSTOM_PROMPTS_DIR = prompt_service.TASK_CUSTOM_PROMPTS_DIR
DEFAULT_GPT_MODEL = state_store.DEFAULT_GPT_MODEL
DEFAULT_LOCAL_BASE_URL = state_store.DEFAULT_LOCAL_BASE_URL
DEFAULT_LOCAL_MODEL = state_store.DEFAULT_LOCAL_MODEL
DEFAULT_DEEPSEEK_MODEL = state_store.DEFAULT_DEEPSEEK_MODEL
DEFAULT_GEMINI_MODEL = state_store.DEFAULT_GEMINI_MODEL
DEFAULT_NVIDIA_MODEL = state_store.DEFAULT_NVIDIA_MODEL
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
GEMINI_MODEL_OPTIONS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
NVIDIA_MODEL_OPTIONS = [
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-405b-instruct",
    "mistralai/mixtral-8x7b-instruct-v0.1",
    "nvidia/llama-3.1-nemotron-70b-instruct",
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
ANALYSIS_JOBS: Dict[str, Dict] = {}
ANALYSIS_JOBS_LOCK = threading.Lock()
ANALYSIS_STATUS_STORE: Optional[status_service.AnalysisStatusStore] = None
LLM_ADAPTER: Optional[llm_adapter.LLMAdapter] = None
ANALYSIS_SERVICE: Optional[analysis_service.AnalysisService] = None
TASK_STORE: Optional[task_store.TaskStore] = None
DOC_VERSION = "V2.4"
DOC_VERSION_RULE = "大改动升主版本（如 V2.0），小更新升次版本（如 V1.14 -> V1.15）"
SUPPORTED_LANGS = {"zh", "en"}
PROMPT_NAME_EN = {
    "网络工程师-严格模式": "Network Engineer - Strict",
    "网络工程师-变更评审模式": "Network Engineer - Change Review",
    "基础巡检诊断": "Basic Inspection Diagnosis",
    "接口与链路诊断": "Interface and Link Diagnosis",
    "路由与协议诊断": "Routing and Protocol Diagnosis",
    "性能与资源诊断": "Performance and Resource Diagnosis",
}
CATEGORY_NAME_EN = {
    "设备软件层": "Software Layer",
    "设备硬件层": "Hardware Layer",
    "协议层面": "Protocol Layer",
    "端口层面": "Port Layer",
    "更多分类": "More Categories",
}
CHECK_TEMPLATE_NAME_EN = {
    "默认全量模板": "Default Full Template",
}
DEFAULT_CHECK_TEMPLATE_NAME = "默认全量模板"

DEFAULT_SYSTEM_PROMPTS: Dict[str, str] = prompt_service.DEFAULT_SYSTEM_PROMPTS
DEFAULT_TASK_PROMPTS: Dict[str, str] = prompt_service.DEFAULT_TASK_PROMPTS


def normalize_lang(value: str) -> str:
    v = str(value or "").strip().lower()
    if v.startswith("en"):
        return "en"
    return "zh"


def with_lang(path: str, lang: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}lang={normalize_lang(lang)}"


def build_app_header_css() -> str:
    return """
    html { overflow-y: scroll; }
    .app-topbar {
      background:#f7f8fa; color:#2f3b4c; display:flex; justify-content:space-between; align-items:center;
      padding:8px 18px 0; gap:14px;
      border-bottom: 1px solid #d7dbe3;
      min-height: 56px;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
    }
    .app-brand { display:flex; align-items:flex-end; gap:18px; min-width:0; flex: 1; }
    .app-logo { display:inline-flex; align-items:center; gap:10px; background:#fff; border-radius:10px; padding:2px 8px 2px 2px; color:#1e2b8f; font-weight:700; }
    .app-logo-mark {
      width:42px; height:42px; border-radius:999px; display:inline-block;
      background: linear-gradient(180deg, #f35a31 0 16%, #f28a2a 16% 32%, #10b981 32% 50%, #22d3ee 50% 68%, #2563eb 68% 84%, #1e3a8a 84% 100%);
    }
    .app-logo-text { display:flex; flex-direction:column; line-height:1.1; }
    .app-logo-sea { font-size:20px; font-weight:800; }
    .app-logo-sub { font-size:9px; opacity:0.9; }
    .app-top-links { display:flex; align-items:center; gap:8px; font-size:14px; color:#4f5b6e; white-space:nowrap; padding-bottom: 8px; }
    .app-top-links a, .app-top-links button { color:#4f5b6e; text-decoration:none; }
    .app-circle-btn {
      width: 28px;
      height: 28px;
      min-width: 28px;
      border-radius: 999px;
      border: 1px solid #c5cedc;
      background: #ffffff !important;
      color: #334155 !important;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      padding: 0;
      box-sizing: border-box;
    }
    .app-circle-btn:hover { background: #eef3fb !important; }
    .app-mainnav {
      background:#fff; border-bottom:1px solid #d6dde8; display:flex; justify-content:space-between;
      align-items:center; min-height:50px; padding:0 18px; gap:10px;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
    }
    .app-title {
      font-family: "Avenir Next", "SF Pro Display", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size:22px;
      font-weight:800;
      letter-spacing:0.2px;
      color:#303643;
      white-space:nowrap;
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }
    .app-menu { display:inline-flex; align-items:center; gap:16px; }
    .app-menu a {
      color:#374151; text-decoration:none; font-size:15px; font-weight:600;
      padding:18px 0 14px; border-bottom:3px solid transparent;
    }
    .app-menu a.active { color:#111827; border-bottom-color:#d43a2f; }
    @media (max-width: 740px) {
      .app-topbar, .app-mainnav { padding-left: 12px; padding-right: 12px; }
      .app-topbar { padding-top: 6px; }
      .app-logo-sea { font-size: 16px; }
      .app-menu a { font-size: 14px; padding: 10px 0 8px; }
      .app-title { font-size: 18px; }
      .app-top-links { display:none; }
    }
"""


def build_app_header_html(lang: str, active_menu: str = "runner") -> str:
    lang = normalize_lang(lang)
    home_link = with_lang("/", lang)
    tasks_link = with_lang("/tasks", lang)
    ai_link = with_lang("/ai/settings", lang)
    guide_link = with_lang("/guide", lang)
    lang_toggle = "ZH" if lang == "en" else "EN"
    labels = {
        "runner": "Create Task" if lang == "en" else "创建任务",
        "tasks": "Tasks" if lang == "en" else "任务页面",
        "ai": "AI Settings" if lang == "en" else "AI 设置",
    }
    app_title = "AIOps - HealthCheck Runner"
    return f"""
  <div class="app-topbar">
    <div class="app-brand">
      <div class="app-logo">
        <span class="app-logo-mark"></span>
        <span class="app-logo-text">
          <span class="app-logo-sea">sea</span>
          <span class="app-logo-sub">connecting the dots</span>
        </span>
      </div>
    </div>
    <div class="app-top-links">
      <a class="app-circle-btn" href="{guide_link}" title="View Docs">?</a>
      <button id="app_lang_toggle_btn" class="app-circle-btn" type="button" title="Switch Language">{lang_toggle}</button>
    </div>
  </div>
  <div class="app-mainnav">
    <div class="app-title">{app_title}</div>
    <div class="app-menu">
      <a href="{home_link}" class="{'active' if active_menu == 'runner' else ''}">{labels['runner']}</a>
      <a href="{tasks_link}" class="{'active' if active_menu == 'tasks' else ''}">{labels['tasks']}</a>
      <a href="{ai_link}" class="{'active' if active_menu == 'ai' else ''}">{labels['ai']}</a>
    </div>
  </div>
  <script>
    (function () {{
      const btn = document.getElementById('app_lang_toggle_btn');
      if (!btn) return;
      btn.addEventListener('click', () => {{
        const url = new URL(window.location.href);
        const cur = (url.searchParams.get('lang') || 'zh').toLowerCase();
        const target = cur.startsWith('en') ? 'zh' : 'en';
        url.searchParams.set('lang', target);
        window.location.href = url.toString();
      }});
    }})();
  </script>
"""


def display_prompt_name(name: str, lang: str) -> str:
    if normalize_lang(lang) == "en":
        return PROMPT_NAME_EN.get(name, name)
    return name


def localize_html_page(page_html: str, lang: str) -> str:
    if normalize_lang(lang) != "en":
        return page_html
    replacements = [
        ("HealthCheck 执行页面", "HealthCheck Runner"),
        ("查看说明文档", "View Docs"),
        ("输入设备地址、勾选检查项、上传 command_map 文件后，点击执行 `healthcheck.py`。", "Enter device addresses, select checks, upload command_map, then run `healthcheck.py`."),
        ("SSH 用户名", "SSH Username"),
        ("SSH 密码", "SSH Password"),
        ("设备地址（每行一个）", "Device Addresses (one per line)"),
        ("支持手动输入；导入设备文件后会直接刷新到此文本框，你可继续编辑。", "Manual input is supported. Imported devices will directly refresh this textbox and remain editable."),
        ("导入设备文件（可选）", "Import Device File (Optional)"),
        ("文件支持按换行/逗号/分号分隔，`#` 开头行会忽略。", "File supports newline/comma/semicolon separators. Lines starting with `#` are ignored."),
        ("检查项（可多选）", "Check Items (multi-select)"),
        ("检查项模板", "Check Template"),
        ("检查项模板管理（可选）", "Check Template Management (Optional)"),
        ("导入检查项文件（txt）", "Import Check Template File (txt)"),
        ("导入检查项模板", "Import Check Template"),
        ("保存当前选择为模板", "Save Current Selection as Template"),
        ("保存检查项模板", "Save Check Template"),
        ("模板名称", "Template Name"),
        ("核心链路巡检模板", "Core Link Check Template"),
        ("覆盖保存", "Overwrite"),
        ("另存为", "Save As"),
        ("请输入模板名称。", "Please enter template name."),
        ("默认全量模板不可覆盖，请使用其他名称。", "Default full template cannot be overwritten. Please use another name."),
        ("模板重名：可覆盖保存，或修改名称后另存为。", "Template already exists: overwrite it, or change name and save as."),
        ("模板已存在，是否覆盖保存？", "Template exists. Overwrite?"),
        ("已取消覆盖，请修改模板名称后再保存。", "Overwrite cancelled. Change template name and save again."),
        ("请修改模板名称后再点“保存”。", "Please change template name and click Save."),
        ("确认覆盖同名模板【", "Confirm overwrite template ["),
        ("请输入模板名称：", "Please enter template name:"),
        ("模板包含“已勾选检查项 + 自定义命令”。默认全量模板不可修改/删除。", "Template includes selected checks + custom commands. Default full template is read-only and cannot be deleted."),
        ("当前角色只读，无法修改模板。", "Read-only role cannot modify templates."),
        ("当前角色只读，无法保存配置。", "Read-only role cannot save settings."),
        ("当前角色只读，无法导入提示词。", "Read-only role cannot import prompts."),
        ("当前角色只读，无法保存 API Key。", "Read-only role cannot save API keys."),
        ("请先勾选检查项或输入自定义命令。", "Please select checks or enter custom commands first."),
        ("导入时命名（可选）", "Name on Import (Optional)"),
        ("默认全量模板不可修改。可导入/编辑自定义检查项模板。", "Default full template is read-only. You can import/edit custom templates."),
        ("默认全量模板不可修改/删除；仅支持编辑自定义模板。", "Default full template is read-only and cannot be deleted; only custom templates are editable."),
        ("编辑检查项模板", "Edit Check Template"),
        ("Review 检查项模板", "Review Check Template"),
        ("检查项内容不能为空。", "Check template content cannot be empty."),
        ("请先选择检查项文件。", "Please choose a check template file first."),
        ("默认全量模板不可修改。", "Default full template is read-only."),
        ("默认全量模板不可删除。", "Default full template cannot be deleted."),
        ("核心链路巡检项", "Core Link Checks"),
        ("全选", "Select All"),
        ("暂无检查项", "No items"),
        ("自定义命令（可选，按行执行）", "Custom Commands (optional, run top-to-bottom)"),
        ("会在勾选检查项之后执行，顺序按从上到下。支持换行/逗号/分号分隔，`#` 开头会忽略。", "Commands run after selected checks, top-to-bottom. Newline/comma/semicolon are supported; `#` lines are ignored."),
        ("执行模式", "Execution Mode"),
        ("auto（推荐）", "auto (recommended)"),
        ("并发 workers（可选）", "Parallel Workers (optional)"),
        ("留空自动推荐", "Leave empty for auto"),
        ("连接重试次数", "Connection Retry Count"),
        ("跳板机接入模式", "Jump Host Access Mode"),
        ("direct（直连）", "direct (direct connect)"),
        ("ssh（跳板机账号密码）", "ssh (jump host user/password)"),
        ("smc（命令行接入）", "smc (command-line access)"),
        ("跳板机地址", "Jump Host Address"),
        ("跳板机端口", "Jump Host Port"),
        ("跳板机用户名", "Jump Host Username"),
        ("跳板机密码", "Jump Host Password"),
        ("SMC 命令模板", "SMC Command Template"),
        ("SMC 模式会执行该命令；支持变量：`{jump_host}`、`{jump_port}`。", "SMC mode runs this command. Supported vars: `{jump_host}`, `{jump_port}`."),
        ("导入 command_map 文件（可选，默认使用 config/command_map.yaml）", "Import command_map (optional, default: config/command_map.yaml)"),
        ("不上传时默认使用 `config/command_map.yaml`；上传时会临时覆盖本次任务。", "If not uploaded, `config/command_map.yaml` is used. Uploaded file overrides this run only."),
        ("开启 Debug 模式（显示完整执行日志）", "Enable Debug Mode (show full logs)"),
        ("默认关闭。关闭时会隐藏交互提示等噪音输出，任务状态页更干净。", "Off by default. When off, noisy interaction prompts are hidden for cleaner status logs."),
        ("执行队列预览（提交前）", "Execution Queue Preview (before submit)"),
        ("暂无待执行项", "No pending items"),
        ("顺序：先检查项，再自定义命令（从上到下）。", "Order: selected checks first, then custom commands (top-to-bottom)."),
        ("执行 Python 巡检脚本", "Run Python Healthcheck"),
        ("历史报告分析", "Analyze Historical Report"),
        ("清空已保存配置", "Clear Saved Config"),
        ("清空后会删除本地记忆的首页配置（用户名/设备/检查项/执行参数等）。", "Clears locally remembered home config (username/devices/checks/execution params)."),
        ("执行输出", "Execution Output"),
        ("成功导入 ", "Imported "),
        (" 台设备，已刷新到设备地址文本框。", " devices. Textbox has been refreshed."),
        ("未解析到有效设备地址。", "No valid device address parsed."),
        ("设备文件读取失败，请重试。", "Failed to read device file, please retry."),
        ("ERROR: SSH 跳板模式时，跳板机地址/用户名/密码不能为空", "ERROR: Jump host address/username/password are required for SSH jump mode"),
        ("ERROR: SMC 模式时，跳板机地址和 SMC 命令模板不能为空", "ERROR: Jump host address and SMC command template are required for SMC mode"),
        ("已清空本地保存配置。", "Local saved config cleared."),
        ("确认清空本地保存的首页配置吗？", "Clear locally saved home config?"),
        ("任务执行中", "Job Running"),
        ("巡检任务状态", "Inspection Job Status"),
        ("执行中...", "Running..."),
        ("执行完成", "Completed"),
        ("执行失败", "Failed"),
        ("历史报告模式", "History Report Mode"),
        ("历史报告分析模式", "History Report Analysis Mode"),
        ("Analyze Historical Report模式", "History Report Analysis Mode"),
        ("任务 ID", "Task ID"),
        ("返回首页", "Back to Home"),
        ("AI 报告：待生成", "AI Report: Pending"),
        ("AI 报告：分析中", "AI Report: In Progress"),
        ("AI 报告：报告完成", "AI Report: Completed"),
        ("请在页面底部上传历史报告文件并点击 AI 分析。", "Upload a historical report at the bottom, then click AI Analysis."),
        ("下载本次 JSON 报告", "Download JSON Report"),
        ("下载本次 CSV 报告", "Download CSV Report"),
        ("AI 分析设置", "AI Analysis Settings"),
        ("大模型配置", "LLM Configuration"),
        ("大模型选择", "LLM Provider"),
        ("本地大模型", "Local LLM"),
        ("API Key 管理", "API Key Management"),
        ("导入 API Key", "Import API Key"),
        ("保存模型配置", "Save Model Config"),
        ("提示词设置", "Prompt Settings"),
        ("系统提示词模板（严格约束）", "System Prompt Template (Strict)"),
        ("系统模板查看", "System Template Review"),
        ("任务提示词模板", "Task Prompt Template"),
        ("任务提示词描述本次分析目标；可选择“不使用模板”。", "Task prompt defines analysis goals; you can select \"No Template\"."),
        ("模板查看", "Template Review"),
        ("提示词管理（可选）", "Prompt Management (Optional)"),
        ("导入提示词文件（txt）", "Import Prompt File (txt)"),
        ("导入提示词文件（.txt）", "Import Prompt File (.txt)"),
        ("导入到", "Import To"),
        ("任务提示词", "Task Prompt"),
        ("系统提示词", "System Prompt"),
        ("导入时命名（可选）", "Name on Import (Optional)"),
        ("导入提示词", "Import Prompt"),
        ("系统补充约束（可选）", "Extra System Constraints (Optional)"),
        ("任务补充要求（可选）", "Extra Task Requirements (Optional)"),
        ("分析执行选项", "Analysis Execution Options"),
        ("分批模式（每台设备单独提交 JSON 给 AI）", "Batch Mode (submit one device JSON per request)"),
        ("适用于本次巡检 JSON 和历史 JSON 报告；非结构化历史文件仍为单次分析。", "Applies to current-run JSON and historical JSON; non-structured history files still run single analysis."),
        ("分批大小（台/批）", "Batch Size (devices/batch)"),
        ("大报告分析模式（设备分片分析 + 汇总）", "Chunk Mode (chunk per device + summarize)"),
        ("分片模式（设备分片分析 + 汇总）", "Chunk Mode (chunk per device + summarize)"),
        ("单设备先按检查项分片提交，再生成设备汇总，最后做全局汇总。适合超大报告。", "Each device is split by check items first, then device summary and global summary. Suitable for very large reports."),
        ("分片大小（检查项/片）", "Chunk Size (checks/chunk)"),
        ("每设备分片数（仅大报告模式）", "Chunks per Device (Chunk Mode Only)"),
        ("每设备分片数（仅分片模式）", "Chunks per Device (Chunk Mode Only)"),
        ("AI 并发数（设备级，最大同时分析设备数）", "AI Parallelism (device-level, max concurrent devices)"),
        ("仅分批分析生效。每轮会按 AI 并发数并行分析设备；例如并发=2、设备=6 时共 3 轮。建议 1-4，过高可能触发 API 限流。", "Only effective for batched analysis. Each round analyzes up to AI parallelism devices; e.g., parallelism=2 with 6 devices runs 3 rounds. Recommended 1-4 to avoid API rate limits."),
        ("每设备失败重试", "Retry per Device"),
        ("重试仅针对 AI 分析请求失败，不影响巡检采集。", "Retries apply only to AI analysis request failures, not data collection."),
        ("分析预估", "Estimate"),
        ("分析预估：未计算", "Estimate: not calculated"),
        ("历史报告分析", "Historical Report Analysis"),
        ("历史报告文件（任意格式）", "History Report File (Any Format)"),
        ("导入历史报告文件（任意格式）", "Import History Report File (Any Format)"),
        ("导入History Report File (Any Format)", "Import History Report File (Any Format)"),
        ("连接测试", "Connection Test"),
        ("模型连接测试", "Test Connection"),
        ("模型连接测试结果将在此显示。", "Connection test result will be shown here."),
        ("AI 分析", "AI Analysis"),
        ("保存分析报告", "Save Analysis Report"),
        ("分析结果为空，无法保存。", "Analysis result is empty and cannot be saved."),
        ("分析结果尚未生成。", "Analysis result is not generated yet."),
        ("分析报告已保存。", "Analysis report saved."),
        ("保存失败", "Save failed"),
        ("请输入文件名（默认 .md）", "Enter filename (default .md)"),
        ("分析结果会显示在这里。", "Analysis result will be shown here."),
        ("分析中...", "Analyzing..."),
        ("文档中心", "Docs"),
        ("返回文档首页", "Back to Docs"),
        ("程序设计逻辑文档", "Design Documentation"),
        ("用户使用说明文档", "User Guide"),
        ("进入设计文档", "Open Design Doc"),
        ("进入使用文档", "Open User Guide"),
        ("部署依赖（新环境）", "Deployment Dependencies (New Environment)"),
        ("新环境部署依赖", "New Environment Dependencies"),
        ("常见问题", "FAQ"),
        ("在新环境部署前，请确保满足以下最小依赖：", "Before deploying to a new environment, ensure the following minimum requirements are met:"),
        ("推荐安装方式", "Recommended Installation"),
        ("大改动升主版本（如 V2.0），小更新升次版本（如 V1.14 -> V1.15）", "Major updates bump major version (e.g. V2.0), minor updates bump minor version (e.g. V1.14 -> V1.15)."),
        ("历史报告分析模式", "History Report Analysis Mode"),
        ("切换语言", "Switch Language"),
        ("中</button>", "ZH</button>"),
        ("用途：保存当前大模型来源、模型名、本地地址、已选提示词模板。下次打开页面会自动带出。", "Purpose: save current provider/model/local URL/selected templates for next launch."),
        ("Purpose: save current provider/model/local URL/selected templates for next launch. 下次会自动带出。", "Purpose: save current provider/model/local URL/selected templates for next launch."),
        ("已保存", "Saved"),
        ("未保存", "Not Saved"),
        ("模型", "Model"),
        ("自定义", "Custom"),
        ("例如", "e.g."),
        ("本地大模型地址", "Local LLM URL"),
        ("本地大模型模型", "Local LLM Model"),
        ("Local LLM地址", "Local LLM URL"),
        ("Local LLM模型", "Local LLM Model"),
        ("Custom本地Model", "Custom Local Model"),
        ("自定义本地模型", "Custom Local Model"),
        ("自定义 ChatGPT 模型", "Custom ChatGPT Model"),
        ("自定义 DeepSeek 模型", "Custom DeepSeek Model"),
        ("自定义 Gemini 模型", "Custom Gemini Model"),
        ("自定义 NVIDIA 模型", "Custom NVIDIA Model"),
        ("正在保存配置...", "Saving configuration..."),
        ("已保存模型配置：来源/模型/地址/提示词模板，下次会自动带出。", "Saved model config: provider/model/url/prompt templates will auto-load next time."),
        ("已Save Model Config：来源/Model/地址/提示词模板，下次会自动带出。", "Saved model config: provider/model/url/prompt templates will auto-load next time."),
        ("保存失败", "Save failed"),
        ("确认保存当前模型配置吗？", "Confirm saving current model configuration?"),
        ("确认保存当前Model配置吗？", "Confirm saving current model configuration?"),
        ("保存中...", "Saving..."),
        ("模型配置已保存。", "Model configuration saved."),
        ("正在测试连接...", "Testing connection..."),
        ("连接测试失败", "Connection test failed"),
        ("连接测试成功。", "Connection test succeeded."),
        ("请先选择提示词文件。", "Please select a prompt file first."),
        ("正在导入提示词...", "Importing prompt..."),
        ("导入失败", "Import failed"),
        ("系统提示词导入成功。", "System prompt imported."),
        ("任务提示词导入成功。", "Task prompt imported."),
        ("本地大模型不需要 API Key。", "Local LLM does not require API key."),
        ("Local LLM不需要 API Key。", "Local LLM does not require API key."),
        ("已存在 API Key，是否覆盖？", "API key already exists. Overwrite?"),
        ("请输入 ", "Please enter "),
        ("未输入 API Key。", "No API key entered."),
        ("正在保存 API Key...", "Saving API key..."),
        ("API Key 已覆盖保存。", "API key overwritten."),
        ("API Key 保存成功。", "API key saved."),
        ("检测到历史报告，正在优先分析历史报告...", "History report detected, analyzing it first..."),
        ("未导入历史报告，正在分析本次巡检结果...", "No history report uploaded, analyzing current report..."),
        ("分析失败", "Analysis failed"),
        ("未检测到可分析的 JSON 报告。请先运行巡检生成 JSON，或导入历史报告后再分析。", "No analyzable JSON report found. Run inspection to generate JSON, or upload a historical report."),
        ("分析完成。来源:", "Analysis completed. Source:"),
        ("本次Token", "Current Tokens"),
        ("累计Token", "Total Tokens"),
        ("正在启动任务，请稍候...", "Starting task, please wait..."),
        ("状态获取失败", "Failed to fetch status"),
        ("状态获取异常", "Status fetch error"),
        ("当前未选择系统模板。", "No system template selected."),
        ("当前未选择任务模板（不使用模板）。", "No task template selected (none in use)."),
        ("当前模板无内容或不存在。", "Template is empty or missing."),
        ("编辑系统提示词:", "Edit system prompt:"),
        ("编辑任务提示词:", "Edit task prompt:"),
        ("确认保存修改吗？", "Confirm save changes?"),
        ("未选择模板。", "No template selected."),
        ("提示词内容不能为空。", "Prompt content cannot be empty."),
        ("提示词修改已保存。", "Prompt update saved."),
        ("确认删除模板【", "Confirm delete template ["),
        ("】吗？", "] ?"),
        ("模板已删除。", "Template deleted."),
        ("删除失败", "Delete failed"),
        ("保存修改", "Save Changes"),
        ("删除模板", "Delete Template"),
        ("取消修改", "Cancel Edit"),
        ("关闭", "Close"),
        ("Review 系统提示词", "Review System Prompt"),
        ("Review 任务提示词", "Review Task Prompt"),
        ("编辑提示词", "Edit Prompt"),
        ("删除仅对自定义模板生效；默认模板会保留。", "Delete works for custom templates only; default templates are preserved."),
        ("删除仅对Custom模板生效；默认模板会保留。", "Delete works for custom templates only; default templates are preserved."),
        ("System Prompt用于约束 AI 行为与输出规范，建议固定使用“网络工程师-严格模式”。", "System prompt constrains AI behavior and output format; keeping the strict template is recommended."),
        ("Task Prompt描述本次分析目标；可选择“No Template”。", "Task prompt defines analysis goals; you can select \"No Template\"."),
        ("Task Prompt描述本次分析目标；可选择\"No Template\"。", "Task prompt defines analysis goals; you can select \"No Template\"."),
        ("点击弹窗查看当前系统模板内容。", "Click to view current system template in a dialog."),
        ("点击弹窗查看当前任务模板内容。", "Click to view current task template in a dialog."),
        ("留空名称时自动使用文件名。", "If empty, filename is used automatically."),
        ("核心链路专项诊断（不填自动用文件名）", "Core-link diagnosis (leave empty to use filename)"),
        ("可追加系统级约束，e.g.：每条结论必须给证据链，无证据必须输出证据不足。", "Append system constraints, e.g. every conclusion must include evidence chain; otherwise output insufficient evidence."),
        ("会追加在任务模板后面；e.g.：请重点关注核心上联、邻居抖动和高风险接口", "Appended after task template; e.g. focus on uplinks, neighbor flaps and high-risk interfaces."),
        ("不使用模板", "No Template"),
        ("提示词管理（可选）", "Prompt Management (Optional)"),
        ("导入时命名（可选）", "Name on Import (Optional)"),
        ("会追加在任务模板后面；例如：请重点关注核心上联、邻居抖动和高风险接口", "Appended after task template; e.g. focus on uplink, flapping neighbors and high-risk interfaces"),
        ("可上传历史 JSON/CSV/TXT/LOG 或其他格式文件，由 AI 尝试解析后分析。", "Upload historical JSON/CSV/TXT/LOG or other files; AI will try to parse and analyze."),
        ("编辑System Prompt:", "Edit system prompt:"),
        ("编辑Task Prompt:", "Edit task prompt:"),
        ("提示词修改Saved。", "Prompt update saved."),
        ("ChatGPT 模式下请选择Model或输入CustomModel。", "For ChatGPT, select or enter a model."),
        ("Local LLM模式下请填写地址和Model。", "For Local LLM, fill both URL and model."),
        ("DeepSeek 模式下请填写Model名称。", "For DeepSeek, enter model name."),
        ("Gemini 模式下请填写Model名称。", "For Gemini, enter model name."),
        ("NVIDIA 模式下请填写Model名称。", "For NVIDIA, enter model name."),
    ]
    localized = page_html
    for src, dst in replacements:
        localized = localized.replace(src, dst)
    return localized


def ensure_prompt_dirs() -> None:
    prompt_service.ensure_prompt_dirs()


def migrate_legacy_prompt_dirs() -> None:
    prompt_service.migrate_legacy_prompt_dirs()


def prompt_file_name(name: str) -> str:
    return prompt_service.prompt_file_name(name)


def write_prompt_file(prompt_dir: Path, prompt_name: str, content: str) -> bool:
    return prompt_service.write_prompt_file(prompt_dir, prompt_name, content)


def load_prompt_dir(prompt_dir: Path) -> Dict[str, str]:
    return prompt_service.load_prompt_dir(prompt_dir)


def initialize_default_prompt_files() -> None:
    prompt_service.initialize_default_prompt_files()


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


def ensure_check_template_dirs() -> None:
    CHECK_DEFAULT_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    CHECK_CUSTOM_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _now_ts() -> int:
    return int(time.time())


def user_can_modify(user: Dict) -> bool:
    return bool(user and user.get("can_modify"))


def parse_check_items(raw: str) -> List[str]:
    parts: List[str] = []
    seen = set()
    for item in re.split(r"[,;\n]+", raw or ""):
        v = item.strip()
        if not v or v.startswith("#"):
            continue
        if v not in seen:
            seen.add(v)
            parts.append(v)
    return parts


def check_template_file_name(name: str) -> str:
    return prompt_file_name(name)


def parse_check_template_text(raw: str) -> Tuple[List[str], List[str]]:
    text = str(raw or "")
    lines = text.splitlines()
    mode = "checks"
    checks: List[str] = []
    commands: List[str] = []
    seen_checks = set()
    seen_cmds = set()
    for line in lines:
        v = line.strip()
        if not v:
            continue
        lower = v.lower()
        if lower in {"[checks]", "#checks"}:
            mode = "checks"
            continue
        if lower in {"[commands]", "#commands", "[custom_commands]", "#custom_commands"}:
            mode = "commands"
            continue
        if v.startswith("#"):
            continue
        if mode == "commands":
            if v not in seen_cmds:
                seen_cmds.add(v)
                commands.append(v)
        else:
            if v not in seen_checks:
                seen_checks.add(v)
                checks.append(v)
    return checks, commands


def format_check_template_text(checks: List[str], commands: List[str]) -> str:
    out: List[str] = ["[checks]"]
    out.extend([str(x).strip() for x in checks if str(x).strip()])
    out.extend(["", "[commands]"])
    out.extend([str(x).strip() for x in commands if str(x).strip()])
    return "\n".join(out).strip() + "\n"


def write_check_template_file(template_dir: Path, template_name: str, checks: List[str], commands: Optional[List[str]] = None) -> bool:
    filename = check_template_file_name(template_name)
    if not filename:
        return False
    items = [str(x).strip() for x in checks if str(x).strip()]
    cmd_items = [str(x).strip() for x in (commands or []) if str(x).strip()]
    if not items and not cmd_items:
        return False
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / filename).write_text(format_check_template_text(items, cmd_items), encoding="utf-8")
    return True


def load_check_template_dir(template_dir: Path) -> Dict[str, Dict[str, List[str]]]:
    if not template_dir.is_dir():
        return {}
    templates: Dict[str, Dict[str, List[str]]] = {}
    for path in sorted(template_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        checks, commands = parse_check_template_text(text)
        if checks or commands:
            templates[path.stem.replace("_", " ")] = {"checks": checks, "commands": commands}
    return templates


def initialize_default_check_templates() -> None:
    ensure_check_template_dirs()
    target = CHECK_DEFAULT_TEMPLATES_DIR / check_template_file_name(DEFAULT_CHECK_TEMPLATE_NAME)
    if not target.is_file():
        items = DEFAULT_CHECKS[:] if DEFAULT_CHECKS else ["@uptime", "@cpu_usage", "@memory_usage"]
        target.write_text(format_check_template_text(items, []), encoding="utf-8")


def merged_check_template_catalog() -> Dict[str, Dict[str, List[str]]]:
    initialize_default_check_templates()
    defaults = load_check_template_dir(CHECK_DEFAULT_TEMPLATES_DIR)
    if not defaults:
        defaults = {
            DEFAULT_CHECK_TEMPLATE_NAME: {
                "checks": (DEFAULT_CHECKS[:] if DEFAULT_CHECKS else ["@uptime", "@cpu_usage", "@memory_usage"]),
                "commands": [],
            }
        }
    customs = load_check_template_dir(CHECK_CUSTOM_TEMPLATES_DIR)
    merged = dict(defaults)
    for key, values in customs.items():
        if key and (values.get("checks") or values.get("commands")):
            merged[key] = values
    return merged


def display_check_template_name(name: str, lang: str) -> str:
    if normalize_lang(lang) == "en":
        return CHECK_TEMPLATE_NAME_EN.get(name, name)
    return name

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
        "jump_mode": "direct",
        "jump_host": "",
        "jump_port": "22",
        "jump_username": "",
        "jump_password": "",
        "smc_command": "smc server toc {jump_host}",
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


def is_safe_ai_report_name(name: str) -> bool:
    if not name:
        return False
    if name != os.path.basename(name):
        return False
    return bool(re.match(r"^ai_analysis_[A-Za-z0-9_]+\.md$", name))


def list_ai_report_files(task_id: str, limit: int = 20) -> List[Path]:
    tid = str(task_id or "").strip()
    if not tid or not AI_REPORT_DIR.is_dir():
        return []
    prefix = f"ai_analysis_{tid}_"
    files = [p for p in AI_REPORT_DIR.iterdir() if p.is_file() and p.name.startswith(prefix) and p.suffix.lower() == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[: max(1, int(limit or 20))]


def save_ai_analysis_report(
    task_id: str,
    *,
    analysis_text: str,
    provider: str,
    model: str,
    prompt_source: str,
    duration_seconds: float,
    token_usage: Optional[Dict] = None,
    token_total: int = 0,
    source: str = "task",
    status: str = "done",
    error: str = "",
) -> str:
    tid = str(task_id or "").strip() or "unknown"
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    suffix = uuid4().hex[:6]
    name = f"ai_analysis_{tid}_{ts}_{suffix}.md"
    AI_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    usage = token_usage or {}
    text = str(analysis_text or "").strip()
    if not text and error:
        text = f"[analysis_{status}] {error}"
    lines = [
        "# AI Analysis Report",
        "",
        f"- Task ID: {tid}",
        f"- Status: {status}",
        f"- Source: {source}",
        f"- Provider: {provider or '-'}",
        f"- Model: {model or '-'}",
        f"- Prompt Source: {prompt_source or '-'}",
        f"- Duration Seconds: {max(0.0, float(duration_seconds or 0.0)):.2f}",
        f"- Token Total (global): {int(token_total or 0)}",
        f"- Token Usage (this run): {json.dumps(usage, ensure_ascii=False)}",
        "",
        "## Content",
        "",
        text or "(empty)",
        "",
    ]
    (AI_REPORT_DIR / name).write_text("\n".join(lines), encoding="utf-8")
    return name


def load_gpt_config() -> Dict:
    return state_store.load_gpt_config()


def save_gpt_config(config: Dict) -> None:
    state_store.save_gpt_config(config)


def sanitize_prompt_name(name: str) -> str:
    return prompt_service.sanitize_prompt_name(name)


def load_token_stats() -> Dict:
    return state_store.load_token_stats()


def save_token_stats(stats: Dict) -> None:
    state_store.save_token_stats(stats)


def add_token_usage(provider: str, used_tokens: int) -> Dict:
    return state_store.add_token_usage(provider, used_tokens)


def persist_analysis_result(payload: Dict) -> str:
    if not isinstance(payload, dict):
        return ""
    task_id = str(payload.get("job_id", "") or "").strip()
    if not task_id:
        return ""
    return save_ai_analysis_report(
        task_id,
        analysis_text=str(payload.get("analysis", "") or ""),
        provider=str(payload.get("provider_used", "") or ""),
        model=str(payload.get("model_used", "") or ""),
        prompt_source=str(payload.get("prompt_source", "") or ""),
        duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
        token_usage=payload.get("token_usage", {}) if isinstance(payload.get("token_usage", {}), dict) else {},
        token_total=int(payload.get("token_total", 0) or 0),
        source="batched",
        status=str(payload.get("status", "done") or "done"),
        error=str(payload.get("error", "") or ""),
    )


def ensure_analysis_services() -> None:
    global ANALYSIS_STATUS_STORE, LLM_ADAPTER, ANALYSIS_SERVICE
    if ANALYSIS_STATUS_STORE is None:
        ANALYSIS_STATUS_STORE = status_service.AnalysisStatusStore(ANALYSIS_JOBS, ANALYSIS_JOBS_LOCK)
    if LLM_ADAPTER is None:
        LLM_ADAPTER = llm_adapter.LLMAdapter()
    if ANALYSIS_SERVICE is None:
        ANALYSIS_SERVICE = analysis_service.AnalysisService(
            jobs=JOBS,
            jobs_lock=JOBS_LOCK,
            report_dir=REPORT_DIR,
            is_safe_report_name=is_safe_report_name,
            add_token_usage=add_token_usage,
            status_store=ANALYSIS_STATUS_STORE,
            llm_adapter=LLM_ADAPTER,
            persist_callback=persist_analysis_result,
        )


def ensure_task_store() -> None:
    global TASK_STORE
    if TASK_STORE is None:
        TASK_STORE = task_store.TaskStore()


def extract_token_usage(payload: Dict) -> Dict[str, int]:
    return llm_service.extract_token_usage(payload)


def merged_prompt_catalog(
    default_prompts: Dict[str, str],
    default_dir: Path,
    custom_dir: Path,
) -> Dict[str, str]:
    return prompt_service.merged_prompt_catalog(default_prompts, default_dir, custom_dir)


def merged_task_prompt_catalog() -> Dict[str, str]:
    return prompt_service.merged_task_prompt_catalog()


def merged_system_prompt_catalog() -> Dict[str, str]:
    return prompt_service.merged_system_prompt_catalog()


def prompt_catalog_by_kind(kind: str) -> Dict[str, str]:
    return prompt_service.prompt_catalog_by_kind(kind)


def parse_openai_response_text(payload: Dict) -> str:
    return llm_service.parse_openai_response_text(payload)


def build_openai_ssl_context() -> ssl.SSLContext:
    return llm_service.build_openai_ssl_context()


def call_openai_analysis(
    api_key: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    model: str = DEFAULT_GPT_MODEL,
) -> tuple:
    return llm_service.call_openai_analysis(api_key, system_prompt, task_prompt, report_text, model)


def test_openai_connection(api_key: str) -> str:
    return llm_service.test_openai_connection(api_key)


def call_deepseek_analysis(
    api_key: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    model: str = DEFAULT_DEEPSEEK_MODEL,
) -> tuple:
    return llm_service.call_deepseek_analysis(api_key, system_prompt, task_prompt, report_text, model)


def test_deepseek_connection(api_key: str) -> str:
    return llm_service.test_deepseek_connection(api_key)


def call_gemini_analysis(
    api_key: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    model: str = DEFAULT_GEMINI_MODEL,
) -> tuple:
    return llm_service.call_gemini_analysis(api_key, system_prompt, task_prompt, report_text, model)


def test_gemini_connection(api_key: str) -> str:
    return llm_service.test_gemini_connection(api_key)


def call_nvidia_analysis(
    api_key: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    model: str = DEFAULT_NVIDIA_MODEL,
) -> tuple:
    return llm_service.call_nvidia_analysis(api_key, system_prompt, task_prompt, report_text, model)


def test_nvidia_connection(api_key: str) -> str:
    return llm_service.test_nvidia_connection(api_key)


def call_local_lmstudio_analysis(
    base_url: str,
    model: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
) -> tuple:
    return llm_service.call_local_lmstudio_analysis(base_url, model, system_prompt, task_prompt, report_text)


def test_local_lmstudio_connection(base_url: str) -> str:
    return llm_service.test_local_lmstudio_connection(base_url)


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


def read_uploaded_report_raw(upload: cgi.FieldStorage) -> Tuple[str, bytes]:
    raw = upload.file.read()
    if not raw:
        raise RuntimeError("历史报告文件为空")
    if len(raw) > MAX_HISTORY_REPORT_BYTES:
        raise RuntimeError(f"历史报告文件过大，最大支持 {MAX_HISTORY_REPORT_BYTES // (1024 * 1024)}MB")
    filename = str(getattr(upload, "filename", "") or "uploaded_report")
    return filename, raw


def read_uploaded_report(upload: cgi.FieldStorage) -> str:
    filename, raw = read_uploaded_report_raw(upload)
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


def build_html(
    values: Dict[str, str],
    selected_checks: List[str],
    output_text: str,
    status: str,
    lang: str = "zh",
    selected_template: str = DEFAULT_CHECK_TEMPLATE_NAME,
    can_modify: bool = True,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    lang = normalize_lang(lang)
    is_en = lang == "en"
    choose_file_text = "选择文件" if lang == "zh" else "Choose File"
    no_file_text = "未选择文件" if lang == "zh" else "No file chosen"
    check_templates = merged_check_template_catalog()
    if not selected_template or selected_template not in check_templates:
        selected_template = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in check_templates else next(iter(check_templates.keys()), "")
    template_payload = check_templates.get(selected_template, {})
    template_checks = template_payload.get("checks", DEFAULT_CHECKS[:]) if isinstance(template_payload, dict) else DEFAULT_CHECKS[:]
    category_items: Dict[str, List[str]] = {name: [] for name, _ in CHECK_CATEGORIES}
    selected_set = set(selected_checks)
    for item in template_checks:
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

        category_label = CATEGORY_NAME_EN.get(category_name, category_name) if lang == "en" else category_name
        checks_blocks.append(
            '<div class="check-group">'
            f'<div class="check-group-head"><strong>{html.escape(category_label)}</strong>'
            f'<label class="select-all"><input type="checkbox" class="category-toggle" data-category="{group_id}">全选</label>'
            '</div>'
            f"{content_html}"
            "</div>"
        )

    category_defs_js = [(name, sorted(list(members))) for name, members in CHECK_CATEGORIES]
    check_template_options = "".join(
        [
            f'<option value="{html.escape(name)}" {"selected" if name == selected_template else ""}>'
            f'{html.escape(display_check_template_name(name, lang))}</option>'
            for name in check_templates.keys()
        ]
    )
    modify_disabled = "" if can_modify else "disabled"

    status_block = ""
    if status:
        css = "ok" if status.startswith("SUCCESS") else "err"
        status_block = f'<div class="status {css}">{html.escape(status)}</div>'

    output_block = ""
    if output_text:
        output_block = f"<h3>执行输出</h3><pre>{html.escape(output_text)}</pre>"

    _html = f"""<!DOCTYPE html>
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
      background: #f6f8fb;
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
    .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }}
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
    .warn-inline {{
      display: none;
      margin-top: 6px;
      border: 1px solid #f59e0b;
      background: #fffbeb;
      color: #92400e;
      border-radius: 8px;
      padding: 6px 8px;
      font-size: 12px;
      font-weight: 600;
    }}
    .preview-box {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 10px;
      margin-top: 6px;
      height: 180px;
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
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
    }}
    .top-actions {{
      margin-left: auto;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex: 0 0 auto;
    }}
    .help-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      color: #334155;
      text-decoration: none;
      font-weight: 800;
      font-size: 18px;
      line-height: 1;
      margin-top: 2px;
    }}
    .help-link:hover {{ background: #f8fafc; }}
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
    .modal-mask {{
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.35);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
      z-index: 20;
    }}
    .modal {{
      width: min(760px, 100%);
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 8px 18px rgba(14, 30, 37, 0.2);
    }}
    .modal-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      font-weight: 700;
    }}
    .modal textarea {{
      min-height: 260px;
      font-family: Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    .modal-actions {{
      display: flex;
      gap: 8px;
      margin-top: 8px;
      flex-wrap: wrap;
    }}
    .sub-card {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fafc;
      padding: 10px;
      margin-top: 8px;
    }}
    .sub-card-title {{
      margin: 0 0 8px;
      font-size: 14px;
      font-weight: 700;
      color: #0f172a;
    }}
    .file-picker {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .file-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-weight: 600;
      min-width: 92px;
    }}
    .file-name {{
      color: #334155;
      font-size: 13px;
    }}
    .file-real {{
      position: absolute;
      left: -9999px;
      width: 1px;
      height: 1px;
      opacity: 0;
      pointer-events: none;
    }}
    {build_app_header_css()}
    @media (max-width: 900px) {{ .grid-3 {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 740px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  {build_app_header_html(lang, "runner")}
  <div class="wrap">
    <div class="card">
      <div class="topbar">
        <div>
          <h1>HealthCheck Web Runner</h1>
          <p class="sub">输入设备地址、勾选检查项、上传 command_map 文件后，点击执行 `healthcheck.py`。</p>
        </div>
        <div class="top-actions">
          <a class="help-link" href="{with_lang('/guide', lang)}" title="查看说明文档">?</a>
          <button id="lang_toggle_btn" class="help-link" type="button" title="切换语言">{'EN' if lang == 'zh' else '中'}</button>
        </div>
      </div>
      {status_block}
      <form id="run_form" method="post" action="/run" enctype="multipart/form-data">
        <input type="hidden" name="lang" value="{lang}">
        <div class="grid row">
          <div>
            <label>设备 SSH 用户名</label>
            <input type="text" name="username" value="{html.escape(values.get("username", ""))}" required>
          </div>
          <div>
            <label>设备 SSH 密码</label>
            <input type="password" name="password" value="{html.escape(values.get("password", ""))}" required>
          </div>
        </div>
        <div class="row">
          <label>跳板机接入模式</label>
          <select name="jump_mode">
            <option value="direct" {"selected" if values.get("jump_mode", "direct") == "direct" else ""}>direct（直连）</option>
            <option value="ssh" {"selected" if values.get("jump_mode") == "ssh" else ""}>ssh（跳板机账号密码）</option>
            <option value="smc" {"selected" if values.get("jump_mode") == "smc" else ""}>smc（命令行接入）</option>
          </select>
        </div>
        <div id="jump_host_row" class="grid row" style="display:{'' if values.get('jump_mode', 'direct') != 'direct' else 'none'};">
          <div>
            <label>跳板机地址</label>
            <input type="text" name="jump_host" value="{html.escape(values.get('jump_host', ''))}" placeholder="例如 103.115.79.114">
          </div>
          <div>
            <label>跳板机端口</label>
            <input type="number" name="jump_port" min="1" step="1" value="{html.escape(values.get('jump_port', '22'))}">
          </div>
        </div>
        <div id="jump_auth_row" class="grid row" hidden style="display:{'' if values.get('jump_mode') == 'ssh' else 'none'};">
          <div>
            <label>跳板机 SSH 用户名（仅 SSH 模式）</label>
            <input type="text" name="jump_username" value="{html.escape(values.get('jump_username', ''))}">
          </div>
          <div>
            <label>跳板机 SSH 密码（仅 SSH 模式）</label>
            <input type="password" name="jump_password" value="{html.escape(values.get('jump_password', ''))}">
          </div>
        </div>
        <div id="jump_smc_row" class="row" style="display:{'' if values.get('jump_mode') == 'smc' else 'none'};">
          <label>SMC 命令模板</label>
          <input type="text" name="smc_command" value="{html.escape(values.get('smc_command', 'smc server toc {{jump_host}}'))}" placeholder="例如 smc server toc {{jump_host}}">
          <div class="tips">SMC 模式会执行该命令；支持变量：`{{jump_host}}`、`{{jump_port}}`。</div>
        </div>
        <div class="row">
          <label>设备地址（每行一个）</label>
          <textarea name="devices">{html.escape(values.get("devices", ""))}</textarea>
          <div class="tips">支持手动输入；导入设备文件后会直接刷新到此文本框，你可继续编辑。</div>
        </div>
        <div class="row">
          <label>导入设备文件（可选）</label>
          <div class="file-picker">
            <label for="devices_file" class="file-btn">{choose_file_text}</label>
            <span id="devices_file_name" class="file-name">{no_file_text}</span>
          </div>
          <input type="file" class="file-real" id="devices_file" name="devices_file" accept=".txt,.csv,.list">
          <div class="tips">文件支持按换行/逗号/分号分隔，`#` 开头行会忽略。</div>
          <div id="import_result" class="import-result"></div>
        </div>
        <div class="row">
          <label>检查项（可多选）</label>
          <div id="check_groups" class="check-groups">{''.join(checks_blocks)}</div>
          <div id="check_count_warning" class="warn-inline"></div>
        </div>
        <div class="row">
          <div class="sub-card">
            <div class="sub-card-title">检查项模板</div>
            <div class="grid">
              <div>
                <select id="check_template_select" name="check_template_key">{check_template_options}</select>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;">
                <button id="review_check_template_btn" class="btn-secondary" type="button" {modify_disabled}>Review 检查项模板</button>
                <button id="save_current_check_template_btn" class="btn-secondary" type="button" {modify_disabled}>保存当前选择为模板</button>
              </div>
            </div>
            <div class="tips">模板包含“已勾选检查项 + 自定义命令”。默认全量模板不可修改/删除。{"当前角色只读，无法修改模板。" if not can_modify else ""}</div>
          </div>
        </div>
        <div class="row">
          <label>自定义命令（可选，按行执行）</label>
          <textarea name="custom_commands" placeholder="例如：&#10;display ip interface brief&#10;display current-configuration | no-more">{html.escape(values.get("custom_commands", ""))}</textarea>
          <div class="tips">会在勾选检查项之后执行，顺序按从上到下。支持换行/逗号/分号分隔，`#` 开头会忽略。</div>
        </div>
        <div class="grid-3 row">
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
          <div>
            <label>连接重试次数</label>
            <input type="number" name="connect_retry" min="0" step="1" value="{html.escape(values.get("connect_retry", "0"))}">
          </div>
        </div>
        <div class="row">
          <label>导入 command_map 文件（可选，默认使用 config/command_map.yaml）</label>
          <div class="file-picker">
            <label for="command_map_file" class="file-btn">{choose_file_text}</label>
            <span id="command_map_file_name" class="file-name">{no_file_text}</span>
          </div>
          <input id="command_map_file" class="file-real" type="file" name="command_map" accept=".yaml,.yml">
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
          <button id="open_history_analysis_btn" class="btn-secondary" type="button" style="margin-left:8px;">历史报告分析</button>
          <button id="clear_saved_btn" class="btn-secondary" type="button" style="margin-left:8px;">清空已保存配置</button>
          <div class="tips">清空后会删除本地记忆的首页配置（用户名/设备/检查项/执行参数等）。</div>
        </div>
      </form>
      {output_block}
    </div>
  </div>
  <div id="check_template_modal" class="modal-mask">
    <div class="modal">
      <div class="modal-head">
        <div id="check_template_modal_title">编辑检查项模板</div>
        <button id="close_check_template_btn" class="btn-secondary" type="button">关闭</button>
      </div>
      <textarea id="check_template_modal_text"></textarea>
      <div class="modal-actions">
        <button id="save_check_template_btn" type="button" {modify_disabled}>保存修改</button>
        <button id="delete_check_template_btn" class="btn-secondary" type="button" {modify_disabled}>删除模板</button>
        <button id="cancel_check_template_btn" class="btn-secondary" type="button">取消修改</button>
      </div>
      <div class="tips">默认全量模板不可修改/删除；仅支持编辑自定义模板。</div>
    </div>
  </div>
  <div id="check_template_save_modal" class="modal-mask">
    <div class="modal">
      <div class="modal-head">
        <div>保存检查项模板</div>
        <button id="close_check_template_save_btn" class="btn-secondary" type="button">关闭</button>
      </div>
      <div class="row" style="margin-bottom:8px;">
        <label>模板名称</label>
        <input id="check_template_save_name" type="text" placeholder="例如：核心链路巡检模板">
      </div>
      <div id="check_template_save_msg" class="tips"></div>
      <div class="modal-actions">
        <button id="confirm_save_check_template_btn" type="button" {modify_disabled}>保存</button>
        <button id="cancel_check_template_save_btn" class="btn-secondary" type="button">取消</button>
      </div>
    </div>
  </div>
<script>
  const HOME_FORM_STORAGE_KEY = "hc_home_form_v1";
  const currentLang = {json.dumps(lang)};
  const canModify = {str(can_modify).lower()};
  const defaultCheckTemplateName = {json.dumps(DEFAULT_CHECK_TEMPLATE_NAME, ensure_ascii=False)};
  const checkTemplateNameEn = {json.dumps(CHECK_TEMPLATE_NAME_EN, ensure_ascii=False)};
  const categoryDefs = {json.dumps(category_defs_js, ensure_ascii=False)};
  const categoryNameEn = {json.dumps(CATEGORY_NAME_EN, ensure_ascii=False)};
  let checkTemplateMap = {json.dumps(check_templates, ensure_ascii=False)};

  function saveHomeFormState() {{
    try {{
      const state = {{
        username: (document.querySelector('input[name="username"]') || {{}}).value || "",
        devices: (document.querySelector('textarea[name="devices"]') || {{}}).value || "",
        custom_commands: (document.querySelector('textarea[name="custom_commands"]') || {{}}).value || "",
        execution_mode: (document.querySelector('select[name="execution_mode"]') || {{}}).value || "auto",
        parallel_workers: (document.querySelector('input[name="parallel_workers"]') || {{}}).value || "",
        connect_retry: (document.querySelector('input[name="connect_retry"]') || {{}}).value || "0",
        jump_mode: (document.querySelector('select[name="jump_mode"]') || {{}}).value || "direct",
        jump_host: (document.querySelector('input[name="jump_host"]') || {{}}).value || "",
        jump_port: (document.querySelector('input[name="jump_port"]') || {{}}).value || "22",
        jump_username: (document.querySelector('input[name="jump_username"]') || {{}}).value || "",
        jump_password: (document.querySelector('input[name="jump_password"]') || {{}}).value || "",
        smc_command: (document.querySelector('input[name="smc_command"]') || {{}}).value || "smc server toc {{jump_host}}",
        debug_mode: !!(document.querySelector('input[name="debug_mode"]') || {{}}).checked,
        check_template_key: (document.getElementById('check_template_select') || {{}}).value || defaultCheckTemplateName,
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
      const jumpModeEl = document.querySelector('select[name="jump_mode"]');
      const jumpHostEl = document.querySelector('input[name="jump_host"]');
      const jumpPortEl = document.querySelector('input[name="jump_port"]');
      const jumpUsernameEl = document.querySelector('input[name="jump_username"]');
      const jumpPasswordEl = document.querySelector('input[name="jump_password"]');
      const smcCommandEl = document.querySelector('input[name="smc_command"]');
      const debugEl = document.querySelector('input[name="debug_mode"]');
      const templateEl = document.getElementById('check_template_select');

      if (usernameEl && typeof state.username === "string") usernameEl.value = state.username;
      if (devicesEl && typeof state.devices === "string") devicesEl.value = state.devices;
      if (customCommandsEl2 && typeof state.custom_commands === "string") customCommandsEl2.value = state.custom_commands;
      if (modeEl && typeof state.execution_mode === "string") modeEl.value = state.execution_mode || "auto";
      if (workersEl && typeof state.parallel_workers === "string") workersEl.value = state.parallel_workers;
      if (retryEl && typeof state.connect_retry === "string") retryEl.value = state.connect_retry || "0";
      if (jumpModeEl && typeof state.jump_mode === "string") jumpModeEl.value = state.jump_mode || "direct";
      if (jumpHostEl && typeof state.jump_host === "string") jumpHostEl.value = state.jump_host;
      if (jumpPortEl && typeof state.jump_port === "string") jumpPortEl.value = state.jump_port || "22";
      if (jumpUsernameEl && typeof state.jump_username === "string") jumpUsernameEl.value = state.jump_username;
      if (jumpPasswordEl && typeof state.jump_password === "string") jumpPasswordEl.value = state.jump_password;
      if (smcCommandEl && typeof state.smc_command === "string") smcCommandEl.value = state.smc_command || "smc server toc {{jump_host}}";
      if (debugEl) debugEl.checked = !!state.debug_mode;
      if (templateEl && typeof state.check_template_key === "string" && checkTemplateMap[state.check_template_key]) {{
        templateEl.value = state.check_template_key;
      }}

      renderChecksFromTemplate(templateEl ? templateEl.value : defaultCheckTemplateName, state.checks || []);

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

  function escapeHtml(value) {{
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }}

  function normalizeItems(raw) {{
    const out = [];
    const seen = new Set();
    String(raw || '')
      .split(/[\\n,;]+/)
      .forEach((part) => {{
        const v = part.trim();
        if (!v || v.startsWith('#')) return;
        if (!seen.has(v)) {{
          seen.add(v);
          out.push(v);
        }}
      }});
    return out;
  }}

  function attachCheckEvents() {{
    document.querySelectorAll('.category-toggle').forEach(toggle => {{
      const category = toggle.getAttribute('data-category');
      toggle.addEventListener('change', () => {{
        document.querySelectorAll('input[name="checks"][data-category="' + category + '"]').forEach(i => {{
          i.checked = toggle.checked;
        }});
        updateCategoryToggle(category);
        buildPreview();
      }});
      updateCategoryToggle(category);
    }});
    document.querySelectorAll('input[name="checks"][data-category]').forEach(item => {{
      item.addEventListener('change', () => {{
        updateCategoryToggle(item.getAttribute('data-category'));
        buildPreview();
      }});
    }});
  }}

  const devicesFileEl = document.getElementById('devices_file');
  const jumpModeEl = document.querySelector('select[name="jump_mode"]');
  const jumpHostRowEl = document.getElementById('jump_host_row');
  const jumpAuthRowEl = document.getElementById('jump_auth_row');
  const jumpSmcRowEl = document.getElementById('jump_smc_row');
  const runFormEl = document.getElementById('run_form');
  const openHistoryAnalysisBtnEl = document.getElementById('open_history_analysis_btn');
  const clearSavedBtnEl = document.getElementById('clear_saved_btn');
  const checkTemplateSelectEl = document.getElementById('check_template_select');
  const checkGroupsEl = document.getElementById('check_groups');
  const reviewCheckTemplateBtnEl = document.getElementById('review_check_template_btn');
  const saveCurrentCheckTemplateBtnEl = document.getElementById('save_current_check_template_btn');
  const checkTemplateModalEl = document.getElementById('check_template_modal');
  const checkTemplateModalTitleEl = document.getElementById('check_template_modal_title');
  const checkTemplateModalTextEl = document.getElementById('check_template_modal_text');
  const saveCheckTemplateBtnEl = document.getElementById('save_check_template_btn');
  const deleteCheckTemplateBtnEl = document.getElementById('delete_check_template_btn');
  const closeCheckTemplateBtnEl = document.getElementById('close_check_template_btn');
  const cancelCheckTemplateBtnEl = document.getElementById('cancel_check_template_btn');
  const checkTemplateSaveModalEl = document.getElementById('check_template_save_modal');
  const checkTemplateSaveNameEl = document.getElementById('check_template_save_name');
  const checkTemplateSaveMsgEl = document.getElementById('check_template_save_msg');
  const closeCheckTemplateSaveBtnEl = document.getElementById('close_check_template_save_btn');
  const cancelCheckTemplateSaveBtnEl = document.getElementById('cancel_check_template_save_btn');
  const confirmSaveCheckTemplateBtnEl = document.getElementById('confirm_save_check_template_btn');
  const devicesTextEl = document.querySelector('textarea[name="devices"]');
  const devicesFileNameEl = document.getElementById('devices_file_name');
  const commandMapFileEl = document.getElementById('command_map_file');
  const commandMapFileNameEl = document.getElementById('command_map_file_name');
  const importResultEl = document.getElementById('import_result');
  const customCommandsEl = document.querySelector('textarea[name="custom_commands"]');
  const previewEl = document.getElementById('command_preview');
  const checkCountWarningEl = document.getElementById('check_count_warning');
  const noFileChosenText = {json.dumps(no_file_text)};

  function refreshJumpModeUI() {{
    const mode = (jumpModeEl && jumpModeEl.value) ? String(jumpModeEl.value) : 'direct';
    if (jumpHostRowEl) {{
      const hostVisible = mode !== 'direct';
      jumpHostRowEl.hidden = !hostVisible;
      jumpHostRowEl.style.display = hostVisible ? '' : 'none';
    }}
    if (jumpAuthRowEl) {{
      const authVisible = mode === 'ssh';
      jumpAuthRowEl.hidden = !authVisible;
      jumpAuthRowEl.style.display = authVisible ? '' : 'none';
    }}
    if (jumpSmcRowEl) {{
      const smcVisible = mode === 'smc';
      jumpSmcRowEl.hidden = !smcVisible;
      jumpSmcRowEl.style.display = smcVisible ? '' : 'none';
    }}
  }}

  function refreshCheckTemplateSelect(selectedName) {{
    if (!checkTemplateSelectEl) return;
    const names = Object.keys(checkTemplateMap || {{}});
    const showName = (n) => currentLang === 'en' ? (checkTemplateNameEn[n] || n) : n;
    checkTemplateSelectEl.innerHTML = names
      .map((name) => {{
        const selected = selectedName === name ? ' selected' : '';
        return '<option value="' + escapeHtml(name) + '"' + selected + '>' + escapeHtml(showName(name)) + '</option>';
      }})
      .join('');
  }}

  function categoryLabel(name) {{
    return currentLang === 'en' ? (categoryNameEn[name] || name) : name;
  }}

  function renderChecksFromTemplate(templateName, checkedValues) {{
    if (!checkGroupsEl) return;
    const tpl = checkTemplateMap[templateName] || {{}};
    const selectedValues = Array.isArray(checkedValues)
      ? new Set(checkedValues.map(v => String(v)))
      : new Set((tpl.checks || []).map(v => String(v)));
    const checks = Array.isArray(tpl.checks) ? tpl.checks : [];
    const categoryBuckets = {{}};
    categoryDefs.forEach((pair) => {{ categoryBuckets[pair[0]] = []; }});
    checks.forEach((item) => {{
      let placed = false;
      categoryDefs.forEach((pair) => {{
        const cat = pair[0];
        const members = Array.isArray(pair[1]) ? pair[1] : [];
        if (!placed && members.includes(item)) {{
          categoryBuckets[cat].push(item);
          placed = true;
        }}
      }});
      if (!placed) categoryBuckets["更多分类"].push(item);
    }});
    checkGroupsEl.innerHTML = categoryDefs.map((pair, idx) => {{
      const cat = pair[0];
      const groupId = 'cat_' + idx;
      const items = categoryBuckets[cat] || [];
      const checksHtml = items.length
        ? ('<div class="checks">' + items.map((item) => {{
            const checked = selectedValues.has(item) ? ' checked' : '';
            return '<label class="check-item"><input type="checkbox" name="checks" value="' + escapeHtml(item) + '" data-category="' + groupId + '"' + checked + '>' + escapeHtml(item) + '</label>';
          }}).join('') + '</div>')
        : '<div class="empty-cat">暂无检查项</div>';
      return (
        '<div class="check-group">' +
        '<div class="check-group-head"><strong>' + escapeHtml(categoryLabel(cat)) + '</strong>' +
        '<label class="select-all"><input type="checkbox" class="category-toggle" data-category="' + groupId + '">全选</label></div>' +
        checksHtml +
        '</div>'
      );
    }}).join('');
    attachCheckEvents();
    document.querySelectorAll('.category-toggle').forEach(toggle => {{
      updateCategoryToggle(toggle.getAttribute('data-category'));
    }});
    if (!Array.isArray(checkedValues) && customCommandsEl) {{
      customCommandsEl.value = Array.isArray(tpl.commands) ? tpl.commands.join('\\n') : '';
    }}
  }}

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

  async function postForm(url, payload) {{
    const body = new URLSearchParams();
    Object.entries(payload || {{}}).forEach(([k, v]) => body.append(k, String(v == null ? '' : v)));
    const resp = await fetch(url, {{
      method: "POST",
      headers: {{ "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }},
      body: body.toString(),
    }});
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {{
      throw new Error(data.error || ("HTTP " + resp.status));
    }}
    return data;
  }}

  function openCheckTemplateEditor() {{
    if (!canModify) {{
      window.alert('当前角色只读，无法修改模板。');
      return;
    }}
    const name = (checkTemplateSelectEl && checkTemplateSelectEl.value) || '';
    if (!name) return;
    const tpl = checkTemplateMap[name] || {{}};
    const checks = Array.isArray(tpl.checks) ? tpl.checks : [];
    const commands = Array.isArray(tpl.commands) ? tpl.commands : [];
    checkTemplateModalEl.dataset.name = name;
    checkTemplateModalTitleEl.textContent = '编辑检查项模板: ' + name;
    checkTemplateModalTextEl.value = ['[checks]'].concat(checks).concat(['', '[commands]']).concat(commands).join('\\n');
    checkTemplateModalEl.style.display = 'flex';
  }}

  function closeCheckTemplateEditor() {{
    checkTemplateModalEl.style.display = 'none';
    checkTemplateModalEl.dataset.name = '';
    checkTemplateModalTextEl.value = '';
  }}

  function openSaveTemplateModal() {{
    if (!canModify) {{
      window.alert('当前角色只读，无法修改模板。');
      return;
    }}
    if (!checkTemplateSaveModalEl || !checkTemplateSaveNameEl) return;
    const currentName = (checkTemplateSelectEl && checkTemplateSelectEl.value) || '';
    checkTemplateSaveNameEl.value = currentName === defaultCheckTemplateName ? '' : currentName;
    checkTemplateSaveMsgEl.textContent = '';
    checkTemplateSaveModalEl.style.display = 'flex';
    setTimeout(() => checkTemplateSaveNameEl.focus(), 0);
  }}

  function closeSaveTemplateModal() {{
    if (!checkTemplateSaveModalEl) return;
    checkTemplateSaveModalEl.style.display = 'none';
    if (checkTemplateSaveNameEl) checkTemplateSaveNameEl.value = '';
    if (checkTemplateSaveMsgEl) checkTemplateSaveMsgEl.textContent = '';
  }}

  async function doSaveCurrentSelectionTemplate(name, overwrite) {{
    const checks = Array.from(document.querySelectorAll('input[name="checks"]:checked')).map(i => i.value);
    const commands = parseItems(customCommandsEl ? customCommandsEl.value : '');
    if (!checks.length && !commands.length) {{
      window.alert('请先勾选检查项或输入自定义命令。');
      return;
    }}
    const body = new URLSearchParams();
    body.append('template_name', name);
    body.append('checks_text', checks.join('\\n'));
    body.append('commands_text', commands.join('\\n'));
    body.append('allow_overwrite', overwrite ? '1' : '0');
    const resp = await fetch('/save_check_template_from_selection', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }},
      body: body.toString(),
    }});
    const data = await resp.json();
    if (!resp.ok || data.ok === false) {{
      const err = String((data && data.error) || ('HTTP ' + resp.status));
      if (err === 'template_exists') {{
        const ok = window.confirm('模板已存在，是否覆盖保存？');
        if (ok) {{
          return await doSaveCurrentSelectionTemplate(name, true);
        }}
        if (checkTemplateSaveMsgEl) checkTemplateSaveMsgEl.textContent = '已取消覆盖，请修改模板名称后再保存。';
        return;
      }}
      throw new Error(err);
    }}
    checkTemplateMap = data.templates || {{}};
    const selectedName = data.selected_template || name;
    refreshCheckTemplateSelect(selectedName);
    renderChecksFromTemplate(selectedName);
    saveHomeFormState();
    buildPreview();
    closeSaveTemplateModal();
  }}

  function updateCheckSelectionWarning() {{
    if (!checkCountWarningEl) return;
    const selectedChecks = Array.from(document.querySelectorAll('input[name="checks"]:checked')).map(i => i.value);
    const count = selectedChecks.length;
    if (count > 10) {{
      const msgZh = '当前已选择 ' + count + ' 个检查项。检查项过多可能超出大模型上下文窗口，并显著增加巡检/分析耗时，建议减少检查项或启用分批模式。';
      const msgEn = 'You selected ' + count + ' check items. Too many items may exceed LLM context window and slow report generation. Consider fewer items or batched analysis.';
      checkCountWarningEl.textContent = currentLang === 'en' ? msgEn : msgZh;
      checkCountWarningEl.style.display = '';
      return;
    }}
    checkCountWarningEl.textContent = '';
    checkCountWarningEl.style.display = 'none';
  }}

  function buildPreview() {{
    updateCheckSelectionWarning();
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
      if (devicesFileNameEl) devicesFileNameEl.textContent = file ? file.name : noFileChosenText;
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
  if (commandMapFileEl && commandMapFileNameEl) {{
    commandMapFileEl.addEventListener('change', () => {{
      const file = commandMapFileEl.files && commandMapFileEl.files[0];
      commandMapFileNameEl.textContent = file ? file.name : noFileChosenText;
    }});
  }}
  if (checkTemplateSelectEl) {{
    checkTemplateSelectEl.addEventListener('change', () => {{
      renderChecksFromTemplate(checkTemplateSelectEl.value);
      saveHomeFormState();
      buildPreview();
    }});
  }}
  if (reviewCheckTemplateBtnEl) {{
    reviewCheckTemplateBtnEl.addEventListener('click', openCheckTemplateEditor);
  }}
  if (closeCheckTemplateBtnEl) closeCheckTemplateBtnEl.addEventListener('click', closeCheckTemplateEditor);
  if (cancelCheckTemplateBtnEl) cancelCheckTemplateBtnEl.addEventListener('click', closeCheckTemplateEditor);
  if (checkTemplateModalEl) {{
    checkTemplateModalEl.addEventListener('click', (e) => {{
      if (e.target === checkTemplateModalEl) closeCheckTemplateEditor();
    }});
  }}
  if (saveCheckTemplateBtnEl) {{
    saveCheckTemplateBtnEl.addEventListener('click', async () => {{
      if (!canModify) return;
      const name = (checkTemplateModalEl.dataset.name || '').trim();
      if (!name) return;
      if (name === defaultCheckTemplateName) {{
        window.alert('默认全量模板不可修改。');
        return;
      }}
      const text = (checkTemplateModalTextEl.value || '').trim();
      if (!text) {{
        window.alert('检查项内容不能为空。');
        return;
      }}
      const ok = window.confirm('确认保存修改吗？');
      if (!ok) return;
      try {{
        const data = await postForm('/update_check_template', {{
          template_name: name,
          template_text: text,
        }});
        checkTemplateMap = data.templates || {{}};
        refreshCheckTemplateSelect(data.selected_template || name);
        renderChecksFromTemplate((data.selected_template || name));
        closeCheckTemplateEditor();
        saveHomeFormState();
        buildPreview();
      }} catch (e) {{
        window.alert('保存失败: ' + (e && e.message ? e.message : e));
      }}
    }});
  }}
  if (deleteCheckTemplateBtnEl) {{
    deleteCheckTemplateBtnEl.addEventListener('click', async () => {{
      if (!canModify) return;
      const name = (checkTemplateModalEl.dataset.name || '').trim();
      if (!name) return;
      if (name === defaultCheckTemplateName) {{
        window.alert('默认全量模板不可删除。');
        return;
      }}
      const ok = window.confirm('确认删除模板【' + name + '】吗？');
      if (!ok) return;
      try {{
        const data = await postForm('/delete_check_template', {{ template_name: name }});
        checkTemplateMap = data.templates || {{}};
        const selectedName = data.selected_template || defaultCheckTemplateName;
        refreshCheckTemplateSelect(selectedName);
        renderChecksFromTemplate(selectedName);
        closeCheckTemplateEditor();
        saveHomeFormState();
        buildPreview();
      }} catch (e) {{
        window.alert('删除失败: ' + (e && e.message ? e.message : e));
      }}
    }});
  }}
  if (saveCurrentCheckTemplateBtnEl) {{
    saveCurrentCheckTemplateBtnEl.addEventListener('click', openSaveTemplateModal);
  }}
  if (confirmSaveCheckTemplateBtnEl) {{
    confirmSaveCheckTemplateBtnEl.addEventListener('click', async () => {{
      if (!canModify) return;
      const name = ((checkTemplateSaveNameEl && checkTemplateSaveNameEl.value) || '').trim();
      if (!name) {{
        if (checkTemplateSaveMsgEl) checkTemplateSaveMsgEl.textContent = '请输入模板名称。';
        return;
      }}
      if (name === defaultCheckTemplateName) {{
        if (checkTemplateSaveMsgEl) checkTemplateSaveMsgEl.textContent = '默认全量模板不可覆盖，请使用其他名称。';
        return;
      }}
      try {{
        await doSaveCurrentSelectionTemplate(name, false);
      }} catch (e) {{
        window.alert('保存失败: ' + (e && e.message ? e.message : e));
      }}
    }});
  }}
  if (closeCheckTemplateSaveBtnEl) {{
    closeCheckTemplateSaveBtnEl.addEventListener('click', closeSaveTemplateModal);
  }}
  if (cancelCheckTemplateSaveBtnEl) {{
    cancelCheckTemplateSaveBtnEl.addEventListener('click', closeSaveTemplateModal);
  }}
  if (checkTemplateSaveModalEl) {{
    checkTemplateSaveModalEl.addEventListener('click', (e) => {{
      if (e.target === checkTemplateSaveModalEl) closeSaveTemplateModal();
    }});
  }}
  if (customCommandsEl) {{
    customCommandsEl.addEventListener('input', buildPreview);
    customCommandsEl.addEventListener('change', buildPreview);
  }}
  if (runFormEl) {{
    runFormEl.addEventListener('submit', saveHomeFormState);
  }}
  if (jumpModeEl) {{
    jumpModeEl.addEventListener('change', () => {{
      refreshJumpModeUI();
      saveHomeFormState();
    }});
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
      if (checkTemplateSelectEl) checkTemplateSelectEl.value = defaultCheckTemplateName;
      const defaults = ((checkTemplateMap[defaultCheckTemplateName] || {{}}).checks || []).slice(0, 3);
      renderChecksFromTemplate(defaultCheckTemplateName, defaults);
      if (importResultEl) importResultEl.textContent = "已清空本地保存配置。";
      buildPreview();
    }});
  }}
  if (openHistoryAnalysisBtnEl) {{
    openHistoryAnalysisBtnEl.addEventListener('click', () => {{
      window.location.href = '/job?history=1&lang=' + encodeURIComponent(currentLang) + '#ai-analysis';
    }});
  }}
  const langToggleBtnEl = document.getElementById('lang_toggle_btn');
  if (langToggleBtnEl) {{
    langToggleBtnEl.addEventListener('click', () => {{
      const target = currentLang === 'zh' ? 'en' : 'zh';
      try {{ localStorage.setItem('hc_ui_lang', target); }} catch (e) {{}}
      window.location.href = '/?lang=' + encodeURIComponent(target);
    }});
  }}

  if (checkTemplateSelectEl) {{
    renderChecksFromTemplate(checkTemplateSelectEl.value || defaultCheckTemplateName, {json.dumps(selected_checks, ensure_ascii=False)});
  }} else {{
    attachCheckEvents();
  }}
  restoreHomeFormState();
  refreshJumpModeUI();
  setTimeout(refreshJumpModeUI, 0);
  if (checkTemplateSelectEl) {{
    refreshCheckTemplateSelect(checkTemplateSelectEl.value || defaultCheckTemplateName);
  }}
  buildPreview();
</script>
</body>
</html>
"""
    return localize_html_page(_html, lang)


def build_job_html(
    job_id: str,
    history_mode: bool = False,
    lang: str = "zh",
    can_modify: bool = True,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    lang = normalize_lang(lang)
    is_en = lang == "en"
    choose_file_text = "选择文件" if lang == "zh" else "Choose File"
    no_file_text = "未选择文件" if lang == "zh" else "No file chosen"
    gpt_config = load_gpt_config()
    task_prompts = merged_task_prompt_catalog()
    system_prompts = merged_system_prompt_catalog()
    selected_task_prompt = str(gpt_config.get("selected_task_prompt", gpt_config.get("selected_prompt", "")) or "")
    selected_system_prompt = str(gpt_config.get("selected_system_prompt", "网络工程师-严格模式") or "")
    if lang == "en":
        system_equiv = {
            "网络工程师-严格模式": "Network Engineer - Strict",
            "网络工程师-变更评审模式": "Network Engineer - Change Review",
        }
        task_equiv = {
            "基础巡检诊断": "Basic Inspection Diagnosis",
            "接口与链路诊断": "Interface and Link Diagnosis",
            "路由与协议诊断": "Routing and Protocol Diagnosis",
            "性能与资源诊断": "Performance and Resource Diagnosis",
        }
        selected_system_prompt = system_equiv.get(selected_system_prompt, selected_system_prompt)
        selected_task_prompt = task_equiv.get(selected_task_prompt, selected_task_prompt)
    task_prompt_options = "".join(
        [
            f'<option value="" {"selected" if not selected_task_prompt else ""}>不使用模板</option>'
        ]
        + [
            f'<option value="{html.escape(name)}" {"selected" if selected_task_prompt == name else ""}>{html.escape(display_prompt_name(name, lang))}</option>'
            for name in task_prompts.keys()
        ]
    )
    system_prompt_options = "".join(
        [
            f'<option value="{html.escape(name)}" {"selected" if selected_system_prompt == name else ""}>{html.escape(display_prompt_name(name, lang))}</option>'
            for name in system_prompts.keys()
        ]
    )
    has_chatgpt_key = bool((gpt_config.get("chatgpt_api_key") or "").strip())
    has_deepseek_key = bool((gpt_config.get("deepseek_api_key") or "").strip())
    has_gemini_key = bool((gpt_config.get("gemini_api_key") or "").strip())
    has_nvidia_key = bool((gpt_config.get("nvidia_api_key") or "").strip())
    provider = str(gpt_config.get("provider", "chatgpt") or "chatgpt")
    chatgpt_model = str(gpt_config.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL)
    local_base_url = str(gpt_config.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL)
    local_model = str(gpt_config.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL)
    deepseek_model = str(gpt_config.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL)
    gemini_model = str(gpt_config.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL)
    nvidia_model = str(gpt_config.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL)
    chatgpt_in_options = chatgpt_model in CHATGPT_MODEL_OPTIONS
    local_in_options = local_model in LOCAL_MODEL_OPTIONS
    deepseek_in_options = deepseek_model in DEEPSEEK_MODEL_OPTIONS
    gemini_in_options = gemini_model in GEMINI_MODEL_OPTIONS
    nvidia_in_options = nvidia_model in NVIDIA_MODEL_OPTIONS
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
    gemini_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if gemini_model == m else ""}>{html.escape(m)}</option>' for m in GEMINI_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not gemini_in_options else ""}>自定义</option>']
    )
    nvidia_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if nvidia_model == m else ""}>{html.escape(m)}</option>' for m in NVIDIA_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not nvidia_in_options else ""}>自定义</option>']
    )
    page_title = "历史报告分析" if history_mode else "任务执行中"
    state_class = "ok" if history_mode else "running"
    state_text = "历史报告模式" if history_mode else "执行中..."
    job_meta = (
        f'任务 ID: <code>{html.escape(job_id)}</code> | <a href="{with_lang("/", lang)}">返回首页</a> | '
        f'<span id="ai_report_status" class="meta-tag">AI 报告：待生成</span>'
        if not history_mode
        else f'历史报告分析模式 | <a href="{with_lang("/", lang)}">返回首页</a> | '
        f'<span id="ai_report_status" class="meta-tag">AI 报告：待生成</span>'
    )
    ai_report_items = list_ai_report_files(job_id, limit=12) if (job_id and not history_mode) else []
    if ai_report_items:
        ai_report_links = " | ".join(
            f'<a href="{with_lang("/download_ai?name=" + p.name, lang)}">{html.escape(p.name)}</a>'
            for p in ai_report_items
        )
        ai_report_history_html = (
            f'<div id="ai_reports" class="report-links">'
            f'AI 历史报告: {ai_report_links}'
            f"</div>"
        )
    else:
        ai_report_history_html = ""
    output_init_text = "请在页面底部上传历史报告文件并点击 AI 分析。" if history_mode else "正在启动任务，请稍候..."
    modify_disabled = "" if can_modify else "disabled"
    _html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(page_title)}</title>
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
    .head-right {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-left: auto;
    }}
    .help-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border: 1px solid #cbd5e1;
      border-radius: 999px;
      background: #fff;
      color: #334155;
      text-decoration: none;
      font-weight: 800;
      font-size: 18px;
      line-height: 1;
      padding: 0;
      cursor: pointer;
    }}
    .help-link:hover {{ background: #f8fafc; }}
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
    #ai_reports {{ margin-top: 6px; }}
    .meta-tag {{
      display: inline-block;
      border: 1px solid #d7dee7;
      border-radius: 999px;
      padding: 2px 10px;
      background: #f8fafc;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }}
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
    .file-picker {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .file-btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 12px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      cursor: pointer;
      font-weight: 700;
    }}
    .file-name {{ color: #334155; font-size: 13px; }}
    .file-real {{
      position: absolute;
      left: -9999px;
      width: 1px;
      height: 1px;
      opacity: 0;
      pointer-events: none;
    }}
    .gpt-card input[type=text], .gpt-card input[type=password], .gpt-card select {{
      min-height: 40px;
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
    .ai-settings-relocated {{ display: none; }}
    .gpt-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    .gpt-btn {{
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      padding: 8px 12px;
      cursor: pointer;
      font-weight: 700;
      min-height: 40px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .gpt-primary {{ background: #0b6e4f; color: #fff; border-color: #0b6e4f; }}
    .gpt-hint {{ font-size: 12px; color: #475569; margin-top: 4px; }}
    .analysis-progress {{
      margin-top: 8px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 8px;
      background: #f8fafc;
    }}
    .analysis-progress-bar {{
      height: 8px;
      border-radius: 999px;
      background: #e2e8f0;
      overflow: hidden;
      margin-top: 4px;
    }}
    .analysis-progress-fill {{
      height: 100%;
      width: 0%;
      background: #0b6e4f;
      transition: width 0.25s ease;
    }}
    {build_app_header_css()}
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
    .prompt-manage-narrow {{
      width: calc((100% - 10px) / 2);
      max-width: none;
    }}
    .prompt-manage-stack {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .exec-options {{
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 10px;
    }}
    .exec-card {{
      border: 1px solid #d7dee7;
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }}
    .exec-field {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .exec-field:last-child {{ margin-bottom: 0; }}
    .exec-field > label {{
      margin: 0;
      font-weight: 700;
      color: #1e293b;
      flex: 1;
      min-width: 0;
    }}
    .exec-field > input {{
      width: 54px;
      min-height: 30px;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 13px;
      margin-left: auto;
    }}
    #analysis_parallelism,
    #analysis_retries,
    #large_report_chunk_items {{
      box-sizing: border-box !important;
      width: 54px !important;
      min-width: 54px !important;
      max-width: 54px !important;
      height: 30px !important;
      min-height: 30px !important;
      padding: 3px 8px !important;
      border: 1px solid #cbd5e1 !important;
      border-radius: 6px !important;
      font-size: 13px !important;
      line-height: 1.5 !important;
    }}
    #analysis_parallelism:disabled,
    #analysis_retries:disabled,
    #large_report_chunk_items:disabled {{
      width: 54px !important;
      min-width: 54px !important;
      max-width: 54px !important;
      height: 30px !important;
      min-height: 30px !important;
    }}
    .exec-disabled {{
      opacity: 0.55;
      pointer-events: none;
    }}
    .modal-mask {{
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.45);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 9999;
      padding: 16px;
    }}
    .modal-box {{
      width: min(760px, 100%);
      background: #fff;
      border: 1px solid #cbd5e1;
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 20px 35px rgba(15, 23, 42, 0.25);
    }}
    .modal-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .modal-title {{ font-weight: 700; }}
    .modal-close {{
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      background: #fff;
      padding: 4px 8px;
      cursor: pointer;
      font-weight: 700;
    }}
    .modal-body textarea {{
      width: 100%;
      min-height: 260px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 10px;
      font-family: Menlo, Consolas, monospace;
      box-sizing: border-box;
    }}
    .danger {{
      border-color: #ef4444;
      color: #991b1b;
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
    @media (max-width: 920px) {{
      .exec-options {{ grid-template-columns: 1fr; }}
      .prompt-manage-narrow {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  {build_app_header_html(lang, "tasks")}
  <div class="wrap">
    <div class="card">
      <div class="head">
        <h2>巡检任务状态</h2>
        <div class="head-right">
          <a class="help-link" href="{with_lang('/guide', lang)}" title="查看说明文档">?</a>
          <button id="lang_toggle_btn" class="help-link" type="button" title="切换语言">{'EN' if lang == 'zh' else '中'}</button>
          <span id="state" class="tag {state_class}">{state_text}</span>
        </div>
      </div>
      <div>{job_meta}</div>
      <div id="reports" class="report-links"></div>
      {ai_report_history_html}
      <pre id="output">{output_init_text}</pre>
      <div id="ai-analysis" class="gpt-card">
        <div class="ai-head">
          <span id="provider_brand_inline" class="ai-brand"></span>
          <h3 style="margin:0;">AI 分析设置</h3>
        </div>
        <div class="gpt-hint" style="margin-bottom:8px;">大模型配置已迁移到 <a href="{with_lang('/ai/settings', lang)}">AI 设置</a> 页面；提示词配置在当前页面维护。</div>
        <details class="gpt-details gpt-row ai-settings-relocated">
          <summary>兼容参数（隐藏，仅供本页分析调用）</summary>
          <div style="display:none;">
            <select id="provider_select">
              <option value="{html.escape(provider)}" selected>{html.escape(provider)}</option>
            </select>
            <input id="local_base_url" type="text" value="{html.escape(local_base_url)}">
            <select id="chatgpt_model_select"><option value="{html.escape(chatgpt_model)}" selected>{html.escape(chatgpt_model)}</option></select>
            <select id="local_model_select"><option value="{html.escape(local_model)}" selected>{html.escape(local_model)}</option></select>
            <select id="deepseek_model_select"><option value="{html.escape(deepseek_model)}" selected>{html.escape(deepseek_model)}</option></select>
            <select id="gemini_model_select"><option value="{html.escape(gemini_model)}" selected>{html.escape(gemini_model)}</option></select>
            <select id="nvidia_model_select"><option value="{html.escape(nvidia_model)}" selected>{html.escape(nvidia_model)}</option></select>
          </div>
        </details>

        <div class="gpt-section">
          <div class="gpt-section-title">提示词设置</div>
          <div class="gpt-grid gpt-row" style="margin-top:0;">
            <div>
              <label>系统提示词模板（严格约束）</label>
              <select id="system_prompt_select">{system_prompt_options}</select>
              <div class="gpt-hint">系统提示词用于约束 AI 行为与输出规范，建议固定使用“网络工程师-严格模式”。</div>
            </div>
            <div>
              <label>系统模板查看</label>
              <div class="gpt-actions" style="margin-top:0;">
                <button class="gpt-btn" id="review_system_template_btn" type="button" {modify_disabled}>Review 系统提示词</button>
              </div>
              <div class="gpt-hint">点击弹窗查看当前系统模板内容。</div>
            </div>
          </div>
          <div class="gpt-grid gpt-row">
            <div>
              <label>任务提示词模板</label>
              <select id="task_prompt_select">{task_prompt_options}</select>
              <div class="gpt-hint">任务提示词描述本次分析目标；可选择“不使用模板”。</div>
            </div>
            <div>
              <label>模板查看</label>
              <div class="gpt-actions" style="margin-top:0;">
                <button class="gpt-btn" id="review_task_template_btn" type="button" {modify_disabled}>Review 任务提示词</button>
              </div>
              <div class="gpt-hint">点击弹窗查看当前任务模板内容。</div>
            </div>
          </div>
          <details class="gpt-details gpt-row">
            <summary>提示词管理（可选）</summary>
            <div class="prompt-manage-stack" style="margin-top:8px;">
              <div>
                <label>导入提示词文件（.txt）</label>
                <div class="file-picker">
                  <label for="prompt_file" class="file-btn">{choose_file_text}</label>
                  <span id="prompt_file_name" class="file-name">{no_file_text}</span>
                </div>
                <input id="prompt_file" class="file-real" type="file" accept=".txt">
                <div class="gpt-hint">留空名称时自动使用文件名。</div>
              </div>
              <div class="prompt-manage-narrow">
                <label>导入到</label>
                <select id="prompt_kind_select">
                  <option value="task" selected>任务提示词</option>
                  <option value="system">系统提示词</option>
                </select>
              </div>
              <div class="prompt-manage-narrow">
                <label>导入时命名（可选）</label>
                <input id="prompt_name" type="text" placeholder="例如：核心链路专项诊断（不填自动用文件名）">
              </div>
              <div class="gpt-actions">
                <button class="gpt-btn" id="import_prompt_btn" type="button" {modify_disabled}>导入提示词</button>
              </div>
            </div>
          </details>
          <div class="gpt-grid gpt-row">
            <div class="gpt-row">
              <label>系统补充约束（可选）</label>
              <textarea id="system_prompt_extra" placeholder="可追加系统级约束，例如：每条结论必须给证据链，无证据必须输出证据不足。"></textarea>
            </div>
            <div></div>
          </div>
          <div class="gpt-grid gpt-row">
            <div class="gpt-row">
              <label>任务补充要求（可选）</label>
              <textarea id="custom_prompt" placeholder="会追加在任务模板后面；例如：请重点关注核心上联、邻居抖动和高风险接口"></textarea>
            </div>
            <div></div>
          </div>
        </div>

        <div class="gpt-section">
          <div class="gpt-section-title">分析执行选项</div>
          <div class="exec-options">
            <div class="exec-card">
              <label class="check-item"><input type="checkbox" id="batched_analysis" checked>分批模式（每台设备单独提交 JSON 给 AI）</label>
              <div class="gpt-hint">适用于本次巡检 JSON 和历史 JSON 报告；非结构化历史文件仍为单次分析。</div>
              <label class="check-item" style="margin-top:10px;"><input type="checkbox" id="large_report_mode" checked>分片模式（设备分片分析 + 汇总）</label>
              <div class="gpt-hint">单设备先按检查项分片提交，再生成设备汇总，最后做全局汇总。适合超大报告。</div>
            </div>
            <div id="exec_fields_card" class="exec-card">
              <div class="exec-field">
                <label>AI 并发数（设备级，最大同时分析设备数）</label>
                <input id="analysis_parallelism" type="number" min="1" max="8" step="1" value="2">
              </div>
              <div id="chunk_items_field" class="exec-field">
                <label>每设备分片数（仅分片模式）</label>
                <input id="large_report_chunk_items" type="number" min="1" max="20" step="1" value="4">
              </div>
              <div class="exec-field">
                <label>每设备失败重试</label>
                <input id="analysis_retries" type="number" min="0" max="3" step="1" value="1">
              </div>
              <div class="gpt-hint">仅分批分析生效。每轮会按 AI 并发数并行分析设备；例如并发=2、设备=6 时共 3 轮。建议并发 1-4，过高可能触发 API 限流。</div>
            </div>
          </div>
          <div class="gpt-actions" style="margin-top:8px;">
            <button class="gpt-btn" id="analysis_precheck_btn" type="button">分析预估</button>
          </div>
          <div id="analysis_precheck_box" class="gpt-hint" style="margin-top:8px;">分析预估：未计算</div>
        </div>
        <div class="gpt-section">
          <div class="gpt-section-title">历史报告分析</div>
          <div class="gpt-grid gpt-row" style="margin-top:0;">
            <div>
              <label>导入历史报告文件（任意格式）</label>
              <div class="file-picker">
                <label for="history_report_file" class="file-btn">{choose_file_text}</label>
                <span id="history_report_file_name" class="file-name">{no_file_text}</span>
              </div>
              <input id="history_report_file" class="file-real" type="file">
              <div class="gpt-hint">可上传历史 JSON/CSV/TXT/LOG 或其他格式文件，由 AI 尝试解析后分析。</div>
            </div>
            <div></div>
          </div>
        </div>
        <div class="gpt-actions">
          <button class="gpt-btn gpt-primary" id="analyze_btn" type="button">AI 分析</button>
          <button class="gpt-btn" id="stop_analysis_btn" type="button">{'Stop Analysis' if is_en else '停止分析'}</button>
          <button class="gpt-btn" id="save_analysis_btn" type="button">保存分析报告</button>
        </div>
        <div id="gpt_status" class="gpt-hint"></div>
        <div id="analysis_progress_box" class="analysis-progress" style="display:none;">
          <div id="analysis_progress_text" class="gpt-hint">进度: 0%</div>
          <div class="analysis-progress-bar"><div id="analysis_progress_fill" class="analysis-progress-fill"></div></div>
        </div>
        <div id="gpt_result">分析结果会显示在这里。</div>
      </div>
    </div>
  </div>
  <div id="prompt_editor_modal" class="modal-mask">
    <div class="modal-box">
      <div class="modal-head">
        <div id="prompt_editor_title" class="modal-title">编辑提示词</div>
        <button id="close_prompt_editor_btn" class="modal-close" type="button">关闭</button>
      </div>
      <div class="modal-body">
        <textarea id="prompt_editor_text"></textarea>
      </div>
      <div class="gpt-actions">
        <button class="gpt-btn gpt-primary" id="save_prompt_edit_btn" type="button" {modify_disabled}>保存修改</button>
        <button class="gpt-btn danger" id="delete_prompt_btn" type="button" {modify_disabled}>删除模板</button>
        <button class="gpt-btn" id="cancel_prompt_edit_btn" type="button">取消修改</button>
      </div>
      <div class="gpt-hint">删除仅对自定义模板生效；默认模板会保留。</div>
    </div>
  </div>
  <script>
    const currentLang = {json.dumps(lang)};
    const canModify = {str(can_modify).lower()};
    const jobId = {json.dumps(job_id)};
    const historyMode = {str(history_mode).lower()};
    const promptNameEn = {json.dumps(PROMPT_NAME_EN, ensure_ascii=False)};
    let taskPromptMap = {json.dumps(task_prompts, ensure_ascii=False)};
    let systemPromptMap = {json.dumps(system_prompts, ensure_ascii=False)};
    const stateEl = document.getElementById("state");
    const outputEl = document.getElementById("output");
    const reportEl = document.getElementById("reports");
    const apiKeyStateEl = document.getElementById("api_key_state");
    const hasChatgptKeySaved = {str(has_chatgpt_key).lower()};
    const hasDeepseekKeySaved = {str(has_deepseek_key).lower()};
    const hasGeminiKeySaved = {str(has_gemini_key).lower()};
    const hasNvidiaKeySaved = {str(has_nvidia_key).lower()};
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
    const geminiModelSelectEl = document.getElementById("gemini_model_select");
    const geminiModelCustomEl = document.getElementById("gemini_model_custom");
    const geminiCustomWrapEl = document.getElementById("gemini_custom_wrap");
    const nvidiaModelSelectEl = document.getElementById("nvidia_model_select");
    const nvidiaModelCustomEl = document.getElementById("nvidia_model_custom");
    const nvidiaCustomWrapEl = document.getElementById("nvidia_custom_wrap");
    const chatgptSettingsEl = document.getElementById("chatgpt_settings");
    const localSettingsEl = document.getElementById("local_settings");
    const deepseekSettingsEl = document.getElementById("deepseek_settings");
    const geminiSettingsEl = document.getElementById("gemini_settings");
    const nvidiaSettingsEl = document.getElementById("nvidia_settings");
    const systemPromptSelectEl = document.getElementById("system_prompt_select");
    const taskPromptSelectEl = document.getElementById("task_prompt_select");
    const promptFileEl = document.getElementById("prompt_file");
    const promptFileNameEl = document.getElementById("prompt_file_name");
    const promptKindSelectEl = document.getElementById("prompt_kind_select");
    const promptNameEl = document.getElementById("prompt_name");
    const historyReportFileEl = document.getElementById("history_report_file");
    const historyReportFileNameEl = document.getElementById("history_report_file_name");
    const systemPromptExtraEl = document.getElementById("system_prompt_extra");
    const customPromptEl = document.getElementById("custom_prompt");
    const gptStatusEl = document.getElementById("gpt_status");
    const llmTestResultEl = document.getElementById("llm_test_result");
    const gptResultEl = document.getElementById("gpt_result");
    const saveAnalysisBtnEl = document.getElementById("save_analysis_btn");
    const stopAnalysisBtnEl = document.getElementById("stop_analysis_btn");
    const batchedAnalysisEl = document.getElementById("batched_analysis");
    const analysisParallelismEl = document.getElementById("analysis_parallelism");
    const analysisRetriesEl = document.getElementById("analysis_retries");
    const largeReportModeEl = document.getElementById("large_report_mode");
    const largeReportChunkItemsEl = document.getElementById("large_report_chunk_items");
    const execFieldsCardEl = document.getElementById("exec_fields_card");
    const chunkItemsFieldEl = document.getElementById("chunk_items_field");
    const analysisProgressBoxEl = document.getElementById("analysis_progress_box");
    const analysisProgressTextEl = document.getElementById("analysis_progress_text");
    const analysisProgressFillEl = document.getElementById("analysis_progress_fill");
    const analysisPrecheckBtnEl = document.getElementById("analysis_precheck_btn");
    const analysisPrecheckBoxEl = document.getElementById("analysis_precheck_box");
    const aiReportStatusEl = document.getElementById("ai_report_status");
    let latestJobData = null;
    let activeAnalysisId = "";
    let analysisRunning = false;
    const promptEditorModalEl = document.getElementById("prompt_editor_modal");
    const promptEditorTitleEl = document.getElementById("prompt_editor_title");
    const promptEditorTextEl = document.getElementById("prompt_editor_text");
    const langToggleBtnEl = document.getElementById("lang_toggle_btn");

    function bindFileName(fileEl, nameEl, noFileLabel) {{
      if (!fileEl || !nameEl) return;
      const refresh = () => {{
        const f = (fileEl.files && fileEl.files.length > 0) ? fileEl.files[0] : null;
        nameEl.textContent = f ? f.name : noFileLabel;
      }};
      fileEl.addEventListener("change", refresh);
      refresh();
    }}

    bindFileName(promptFileEl, promptFileNameEl, {json.dumps(no_file_text)});
    bindFileName(historyReportFileEl, historyReportFileNameEl, {json.dumps(no_file_text)});

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
        links.push('<a href="{with_lang('/download', lang)}&name=' + encodeURIComponent(data.report_json) + '">下载本次 JSON 报告</a>');
      }}
      if (data.report_csv) {{
        links.push('<a href="{with_lang('/download', lang)}&name=' + encodeURIComponent(data.report_csv) + '">下载本次 CSV 报告</a>');
      }}
      reportEl.innerHTML = links.join("");
    }}

    function setGptStatus(msg) {{
      if (gptStatusEl) gptStatusEl.textContent = msg || "";
    }}

    function buildAnalysisMarkdown() {{
      const raw = String((gptResultEl && gptResultEl.textContent) || "").trim();
      if (!raw || raw === "分析结果会显示在这里。" || raw === "Analysis result will be shown here." || raw === "分析中..." || raw === "Analyzing...") {{
        return "";
      }}
      const provider = (providerEl && providerEl.value) ? String(providerEl.value) : "";
      const now = new Date();
      const ts = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0")
        + " " + String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0") + ":" + String(now.getSeconds()).padStart(2, "0");
      return "# AI Analysis Report\\n\\n"
        + "- Generated At: " + ts + "\\n"
        + "- Provider: " + provider + "\\n"
        + "- Job ID: " + String(jobId || "") + "\\n\\n"
        + "## Content\\n\\n"
        + raw + "\\n";
    }}

    function downloadTextFile(filename, content) {{
      const blob = new Blob([content], {{ type: "text/markdown;charset=utf-8" }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }}

    function currentSelectedModelText() {{
      const provider = (providerEl.value || "chatgpt").trim();
      if (provider === "local") {{
        const m = selectedModel(localModelSelectEl, localModelCustomEl) || "-";
        const base = (localBaseEl.value || "").trim();
        return "当前模型: 本地大模型 | " + m + (base ? (" | " + base) : "");
      }}
      if (provider === "deepseek") {{
        return "当前模型: DeepSeek | " + (selectedModel(deepseekModelSelectEl, deepseekModelCustomEl) || "-");
      }}
      if (provider === "gemini") {{
        return "当前模型: Gemini | " + (selectedModel(geminiModelSelectEl, geminiModelCustomEl) || "-");
      }}
      if (provider === "nvidia") {{
        return "当前模型: NVIDIA | " + (selectedModel(nvidiaModelSelectEl, nvidiaModelCustomEl) || "-");
      }}
      return "当前模型: ChatGPT | " + (selectedModel(chatgptModelSelectEl, chatgptModelCustomEl) || "-");
    }}

    function syncIdleModelStatus() {{
      if (!analysisRunning) {{
        setGptStatus(currentSelectedModelText());
      }}
    }}

    function setLlmTestResult(msg, ok) {{
      if (!llmTestResultEl) return;
      llmTestResultEl.textContent = msg || "";
      if (ok === true) {{
        llmTestResultEl.style.color = "#0b6e4f";
      }} else if (ok === false) {{
        llmTestResultEl.style.color = "#b91c1c";
      }} else {{
        llmTestResultEl.style.color = "#475569";
      }}
    }}

    function setAnalysisPrecheck(msg, level) {{
      if (!analysisPrecheckBoxEl) return;
      analysisPrecheckBoxEl.textContent = msg || "分析预估：未计算";
      if (level === "warn") {{
        analysisPrecheckBoxEl.style.color = "#b45309";
      }} else if (level === "error") {{
        analysisPrecheckBoxEl.style.color = "#b91c1c";
      }} else {{
        analysisPrecheckBoxEl.style.color = "#475569";
      }}
    }}

    function resetAnalysisPrecheck() {{
      setAnalysisPrecheck("分析预估：未计算", null);
    }}

    async function runAnalysisPrecheck() {{
      const file = historyReportFileEl.files && historyReportFileEl.files[0];
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const geminiModel = selectedModel(geminiModelSelectEl, geminiModelCustomEl);
      const nvidiaModel = selectedModel(nvidiaModelSelectEl, nvidiaModelCustomEl);
      const selectedSystemPrompt = systemPromptSelectEl.value || "";
      const selectedTaskPrompt = taskPromptSelectEl.value || "";
      const systemPromptExtra = (systemPromptExtraEl.value || "").trim();
      const customPrompt = (customPromptEl.value || "").trim();
      const batchedAnalysis = !!(batchedAnalysisEl && batchedAnalysisEl.checked);
      const analysisParallelism = analysisParallelismEl ? (analysisParallelismEl.value || "2").trim() : "2";
      const analysisRetries = analysisRetriesEl ? (analysisRetriesEl.value || "1").trim() : "1";
      const largeReportMode = !!(largeReportModeEl && largeReportModeEl.checked);
      const largeReportChunkItems = largeReportChunkItemsEl ? (largeReportChunkItemsEl.value || "4").trim() : "4";
      setAnalysisPrecheck("正在计算分析规模...", null);
      const form = new FormData();
      form.append("source", file ? "history" : "job");
      form.append("job_id", jobId);
      form.append("provider", provider);
      form.append("chatgpt_model", chatgptModel);
      form.append("local_base_url", localBaseUrl);
      form.append("local_model", localModel);
      form.append("deepseek_model", deepseekModel);
      form.append("gemini_model", geminiModel);
      form.append("nvidia_model", nvidiaModel);
      form.append("system_prompt_key", selectedSystemPrompt);
      form.append("prompt_key", selectedTaskPrompt);
      form.append("system_prompt_extra", systemPromptExtra);
      form.append("custom_prompt", customPrompt);
      form.append("batched_analysis", batchedAnalysis ? "1" : "0");
      form.append("analysis_parallelism", analysisParallelism);
      form.append("analysis_retries", analysisRetries);
      form.append("large_report_mode", largeReportMode ? "1" : "0");
      form.append("large_report_chunk_items", largeReportChunkItems);
      if (file) form.append("report_file", file);
      const resp = await fetch("/analysis_precheck", {{ method: "POST", body: form }});
      const data = await resp.json();
      if (!data || !data.ok) {{
        throw new Error((data && data.error) || "预估失败");
      }}
      const est = data.estimation || {{}};
      const warns = Array.isArray(est.warnings) ? est.warnings : [];
      const line = "分析预估：设备 " + String(est.device_count || 0)
        + " 台 | 预计调用 " + String(est.estimated_calls || 0)
        + " 次 | 预计 Token " + String(est.estimated_total_tokens || 0)
        + " | 预计耗时 " + String(est.estimated_seconds || 0) + "s";
      setAnalysisPrecheck(line + (warns.length ? (" | 提示: " + warns.join("；")) : ""), warns.length ? "warn" : null);
      return data;
    }}

    function setAiReportStatus(msg) {{
      if (aiReportStatusEl) aiReportStatusEl.textContent = msg || "AI 报告：待生成";
    }}

    function updateAnalysisProgress(visible, percent, text) {{
      if (analysisProgressBoxEl) analysisProgressBoxEl.style.display = visible ? "" : "none";
      if (analysisProgressTextEl) analysisProgressTextEl.textContent = text || ("进度: " + String(percent || 0) + "%");
      if (analysisProgressFillEl) analysisProgressFillEl.style.width = String(Math.max(0, Math.min(100, Number(percent || 0)))) + "%";
    }}

    function formatAnalysisDoneStatus(data) {{
      const thisTokens = (data.token_usage && Number(data.token_usage.total_tokens)) ? Number(data.token_usage.total_tokens) : 0;
      const totalTokens = Number(data.token_total || 0);
      const durationSec = Number(data.duration_seconds || 0);
      const durationInfo = durationSec > 0 ? (" | 总耗时: " + durationSec.toFixed(1) + "s") : "";
      const tokenInfo = durationInfo + " | 本次Token: " + thisTokens + " | 累计Token: " + totalTokens;
      if (data.provider_used === "local") {{
        return "分析完成。来源: LM Studio | " + (data.local_base_url || "") + " | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || "") + tokenInfo;
      }}
      if (data.provider_used === "deepseek") {{
        return "分析完成。来源: DeepSeek | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || "") + tokenInfo;
      }}
      if (data.provider_used === "gemini") {{
        return "分析完成。来源: Gemini | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || "") + tokenInfo;
      }}
      if (data.provider_used === "nvidia") {{
        return "分析完成。来源: NVIDIA | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || "") + tokenInfo;
      }}
      return "分析完成。来源: ChatGPT | " + (data.model_used || "") + " | 提示词: " + (data.prompt_source || "") + tokenInfo;
    }}

    async function sleepMs(ms) {{
      return await new Promise((resolve) => setTimeout(resolve, ms));
    }}

    async function pollBatchedAnalysis(analysisId) {{
      activeAnalysisId = analysisId;
      analysisRunning = true;
      let lastProgress = 0;
      updateAnalysisProgress(true, 0, "进度: 0%");
      while (activeAnalysisId === analysisId) {{
        let data = null;
        try {{
          const resp = await fetch("/analysis_status?id=" + encodeURIComponent(analysisId), {{ cache: "no-store" }});
          data = await resp.json();
        }} catch (e) {{
          setGptStatus("分批分析状态获取失败: " + e);
          updateAnalysisProgress(true, 0, "状态获取失败");
          await sleepMs(1500);
          continue;
        }}
        if (!data || data.ok === false) {{
          setGptStatus("分批分析失败: " + ((data && data.error) || "unknown"));
          gptResultEl.textContent = "分析失败: " + ((data && data.error) || "unknown");
          setAiReportStatus("AI 报告：待生成");
          updateAnalysisProgress(false, 0, "");
          analysisRunning = false;
          syncIdleModelStatus();
          return;
        }}
        const progress = Number(data.progress || 0);
        const done = Number(data.done_devices || 0);
        const total = Number(data.total_devices || 0);
        const inflight = Number(data.inflight_devices || 0);
        const inflightNames = Array.isArray(data.inflight_device_names) ? data.inflight_device_names : [];
        const bDone = Number(data.done_batches || 0);
        const bTotal = Number(data.total_batches || 0);
        const stage = String(data.stage || "");
        const elapsedSec = Number(data.elapsed_seconds || 0);
        let displayProgress = progress;
        if (displayProgress <= 0 && stage === "per_device" && inflight > 0) {{
          displayProgress = Math.max(1, Math.min(5, inflight));
        }}
        displayProgress = Math.max(lastProgress, displayProgress);
        lastProgress = displayProgress;
        const inflightText = inflightNames.length > 0
          ? ("，设备: " + inflightNames.join(", "))
          : "";
        let phaseText = "执行中";
        if (stage === "preparing") {{
          phaseText = "准备分析任务";
        }} else if (stage === "per_device") {{
          phaseText = "设备分析中";
        }} else if (stage === "summary") {{
          phaseText = "汇总分析中";
        }} else if (stage === "done") {{
          phaseText = "分析完成";
        }} else if (stage === "error") {{
          phaseText = "分析失败";
        }}
        const retryHint = String(data.message || "").includes("重试") ? (" | " + String(data.message || "")) : "";
        const msg = "阶段: " + phaseText
          + " | 设备总数 " + total
          + "，已完成 " + done
          + "，进行中 " + inflight + inflightText
          + " | 完成轮次 " + bDone + "/" + bTotal
          + (elapsedSec > 0 ? (" | 已耗时 " + elapsedSec.toFixed(1) + "s") : "")
          + retryHint;
        setGptStatus(msg);
        updateAnalysisProgress(true, displayProgress, "进度: " + displayProgress + "%");

        if (data.status === "done") {{
          gptResultEl.textContent = data.analysis || "(empty)";
          setAiReportStatus("AI 报告：报告完成");
          setGptStatus(formatAnalysisDoneStatus(data));
          lastProgress = 100;
          updateAnalysisProgress(true, 100, "进度: 100%（完成）");
          analysisRunning = false;
          return;
        }}
        if (data.status === "canceled") {{
          gptResultEl.textContent = data.analysis || ({json.dumps("Analysis canceled." if is_en else "分析已取消。")});
          setAiReportStatus({json.dumps("AI Report: Canceled" if is_en else "AI 报告：已取消")});
          setGptStatus({json.dumps("Analysis canceled." if is_en else "分析已取消。")});
          updateAnalysisProgress(true, 100, {json.dumps("Progress: 100% (Canceled)" if is_en else "进度: 100%（已取消）")});
          analysisRunning = false;
          return;
        }}
        if (data.status === "error") {{
          gptResultEl.textContent = "分析失败: " + (data.error || "unknown");
          setAiReportStatus("AI 报告：待生成");
          updateAnalysisProgress(false, 0, "");
          analysisRunning = false;
          syncIdleModelStatus();
          return;
        }}
        await sleepMs(1200);
      }}
    }}

    async function recoverActiveAnalysisIfAny() {{
      if (historyMode || !jobId || analysisRunning) return;
      try {{
        const resp = await fetch("/job_active_analysis?id=" + encodeURIComponent(jobId), {{ cache: "no-store" }});
        const data = await resp.json();
        if (!data || data.ok === false || !data.active || !data.analysis_id) {{
          return;
        }}
        const status = String(data.status || "");
        if (status === "running") {{
          setAiReportStatus({json.dumps("AI Report: Batched analysis running" if is_en else "AI 报告：分批分析中")});
          gptResultEl.textContent = {json.dumps("Detected an ongoing analysis task. Restoring progress..." if is_en else "检测到进行中的分析任务，正在恢复进度...")};
          await pollBatchedAnalysis(String(data.analysis_id));
        }}
      }} catch (_e) {{
      }}
    }}

    function displayPromptName(name) {{
      const key = String(name || "");
      if (currentLang === "en") {{
        return promptNameEn[key] || key;
      }}
      return key;
    }}

    function refreshPromptSelect(kind, prompts, selectedName) {{
      if (kind === "system") {{
        while (systemPromptSelectEl.firstChild) systemPromptSelectEl.removeChild(systemPromptSelectEl.firstChild);
        Object.keys(prompts || {{}}).forEach((k) => {{
          const opt = document.createElement("option");
          opt.value = k;
          opt.textContent = displayPromptName(k);
          systemPromptSelectEl.appendChild(opt);
        }});
        if (selectedName) systemPromptSelectEl.value = selectedName;
      }} else {{
        while (taskPromptSelectEl.firstChild) taskPromptSelectEl.removeChild(taskPromptSelectEl.firstChild);
        const emptyOpt = document.createElement("option");
        emptyOpt.value = "";
        emptyOpt.textContent = "不使用模板";
        taskPromptSelectEl.appendChild(emptyOpt);
        Object.keys(prompts || {{}}).forEach((k) => {{
          const opt = document.createElement("option");
          opt.value = k;
          opt.textContent = displayPromptName(k);
          taskPromptSelectEl.appendChild(opt);
        }});
        taskPromptSelectEl.value = selectedName || "";
      }}
    }}

    function openPromptEditor(kind) {{
      if (!canModify) {{
        window.alert("当前角色只读，无法修改模板。");
        return;
      }}
      const key = kind === "system" ? (systemPromptSelectEl.value || "").trim() : (taskPromptSelectEl.value || "").trim();
      if (!key) {{
        window.alert(kind === "system" ? "当前未选择系统模板。" : "当前未选择任务模板（不使用模板）。");
        return;
      }}
      const map = kind === "system" ? systemPromptMap : taskPromptMap;
      const content = (map && map[key]) ? String(map[key]) : "";
      if (!content) {{
        window.alert("当前模板无内容或不存在。");
        return;
      }}
      promptEditorModalEl.dataset.kind = kind;
      promptEditorModalEl.dataset.name = key;
      const displayName = displayPromptName(key);
      promptEditorTitleEl.textContent = (kind === "system" ? "编辑系统提示词: " : "编辑任务提示词: ") + displayName;
      promptEditorTextEl.value = content;
      promptEditorModalEl.style.display = "flex";
    }}

    function closePromptEditor() {{
      promptEditorModalEl.style.display = "none";
      promptEditorModalEl.dataset.kind = "";
      promptEditorModalEl.dataset.name = "";
      promptEditorTextEl.value = "";
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
      if (geminiCustomWrapEl && geminiModelSelectEl) {{
        geminiCustomWrapEl.style.display = geminiModelSelectEl.value === "__custom__" ? "block" : "none";
      }}
      if (nvidiaCustomWrapEl && nvidiaModelSelectEl) {{
        nvidiaCustomWrapEl.style.display = nvidiaModelSelectEl.value === "__custom__" ? "block" : "none";
      }}
    }}

    function refreshProviderUI() {{
      const provider = (providerEl.value || "chatgpt").trim();
      if (chatgptSettingsEl) chatgptSettingsEl.style.display = provider === "chatgpt" ? "grid" : "none";
      if (localSettingsEl) localSettingsEl.style.display = provider === "local" ? "grid" : "none";
      if (deepseekSettingsEl) deepseekSettingsEl.style.display = provider === "deepseek" ? "grid" : "none";
      if (geminiSettingsEl) geminiSettingsEl.style.display = provider === "gemini" ? "grid" : "none";
      if (nvidiaSettingsEl) nvidiaSettingsEl.style.display = provider === "nvidia" ? "grid" : "none";
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
          gemini: "https://www.gstatic.com/lamda/images/gemini_sparkle_aurora_33f86dc0c0257da337c63.svg",
          nvidia: "https://www.nvidia.com/favicon.ico",
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
        }} else if (provider === "gemini") {{
          setBrandIcon(iconMap.gemini, "Gemini", "Gemini", "#3b82f6", "GM");
          providerBrandInlineEl.title = "Gemini";
        }} else if (provider === "nvidia") {{
          setBrandIcon(iconMap.nvidia, "NVIDIA", "NVIDIA", "#76b900", "NV");
          providerBrandInlineEl.title = "NVIDIA";
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

    const reviewSystemBtnEl = document.getElementById("review_system_template_btn");
    const reviewTaskBtnEl = document.getElementById("review_task_template_btn");
    const closePromptEditorBtnEl = document.getElementById("close_prompt_editor_btn");
    const cancelPromptEditBtnEl = document.getElementById("cancel_prompt_edit_btn");
    const savePromptEditBtnEl = document.getElementById("save_prompt_edit_btn");
    const deletePromptBtnEl = document.getElementById("delete_prompt_btn");
    const saveLlmBtnEl = document.getElementById("save_llm_btn");
    const testLlmBtnEl = document.getElementById("test_llm_btn");
    const importPromptBtnEl = document.getElementById("import_prompt_btn");
    const importApiKeyBtnEl = document.getElementById("import_api_key_btn");
    const analyzeBtnEl = document.getElementById("analyze_btn");

    if (reviewSystemBtnEl) reviewSystemBtnEl.addEventListener("click", () => openPromptEditor("system"));
    if (reviewTaskBtnEl) reviewTaskBtnEl.addEventListener("click", () => openPromptEditor("task"));
    if (closePromptEditorBtnEl) closePromptEditorBtnEl.addEventListener("click", closePromptEditor);
    if (cancelPromptEditBtnEl) cancelPromptEditBtnEl.addEventListener("click", closePromptEditor);
    if (promptEditorModalEl) {{
      promptEditorModalEl.addEventListener("click", (e) => {{
        if (e.target === promptEditorModalEl) closePromptEditor();
      }});
    }}

    if (savePromptEditBtnEl) savePromptEditBtnEl.addEventListener("click", async () => {{
      if (!canModify) return;
      const kind = (promptEditorModalEl.dataset.kind || "").trim();
      const name = (promptEditorModalEl.dataset.name || "").trim();
      const text = (promptEditorTextEl.value || "").trim();
      if (!kind || !name) {{
        setGptStatus("未选择模板。");
        return;
      }}
      if (!text) {{
        setGptStatus("提示词内容不能为空。");
        return;
      }}
      const ok = window.confirm("确认保存修改吗？");
      if (!ok) return;
      try {{
        const data = await postForm("/update_prompt", {{
          prompt_kind: kind,
          prompt_name: name,
          prompt_text: text,
        }});
        if (!data.ok) {{
          setGptStatus("保存失败: " + (data.error || "unknown"));
          return;
        }}
        if (data.prompt_kind === "system") {{
          systemPromptMap = data.prompts || {{}};
          refreshPromptSelect("system", systemPromptMap, data.selected_prompt || name);
        }} else {{
          taskPromptMap = data.prompts || {{}};
          refreshPromptSelect("task", taskPromptMap, data.selected_prompt || name);
        }}
        closePromptEditor();
        setGptStatus("提示词修改已保存。");
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }}
    }});

    if (deletePromptBtnEl) deletePromptBtnEl.addEventListener("click", async () => {{
      if (!canModify) return;
      const kind = (promptEditorModalEl.dataset.kind || "").trim();
      const name = (promptEditorModalEl.dataset.name || "").trim();
      if (!kind || !name) {{
        setGptStatus("未选择模板。");
        return;
      }}
      const ok = window.confirm("确认删除模板【" + name + "】吗？");
      if (!ok) return;
      try {{
        const data = await postForm("/delete_prompt", {{
          prompt_kind: kind,
          prompt_name: name,
        }});
        if (!data.ok) {{
          setGptStatus("删除失败: " + (data.error || "unknown"));
          return;
        }}
        if (data.prompt_kind === "system") {{
          systemPromptMap = data.prompts || {{}};
          refreshPromptSelect("system", systemPromptMap, data.selected_prompt || "网络工程师-严格模式");
        }} else {{
          taskPromptMap = data.prompts || {{}};
          refreshPromptSelect("task", taskPromptMap, data.selected_prompt || "");
        }}
        closePromptEditor();
        setGptStatus("模板已删除。");
      }} catch (e) {{
        setGptStatus("删除失败: " + e);
      }}
    }});

    if (saveLlmBtnEl) saveLlmBtnEl.addEventListener("click", async () => {{
      if (!canModify) {{
        setGptStatus("当前角色只读，无法保存配置。");
        return;
      }}
      const saveBtn = document.getElementById("save_llm_btn");
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModelResolved = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const geminiModelResolved = selectedModel(geminiModelSelectEl, geminiModelCustomEl);
      const nvidiaModelResolved = selectedModel(nvidiaModelSelectEl, nvidiaModelCustomEl);
      const selectedSystemPrompt = systemPromptSelectEl.value || "";
      const selectedTaskPrompt = taskPromptSelectEl.value || "";
      const ok = window.confirm("确认保存当前模型配置吗？");
      if (!ok) return;
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
      if (provider === "gemini" && !geminiModelResolved) {{
        setGptStatus("Gemini 模式下请填写模型名称。");
        return;
      }}
      if (provider === "nvidia" && !nvidiaModelResolved) {{
        setGptStatus("NVIDIA 模式下请填写模型名称。");
        return;
      }}
      if (saveBtn) {{
        saveBtn.disabled = true;
        saveBtn.textContent = "保存中...";
      }}
      setGptStatus("正在保存配置...");
      try {{
        const data = await postForm("/save_gpt_key", {{
          provider: provider,
          chatgpt_model: chatgptModel,
          local_base_url: localBaseUrl,
          local_model: localModel,
          deepseek_model: deepseekModelResolved,
          gemini_model: geminiModelResolved,
          nvidia_model: nvidiaModelResolved,
          selected_system_prompt: selectedSystemPrompt,
          selected_task_prompt: selectedTaskPrompt,
        }});
        if (data.ok) {{
          setGptStatus("已保存模型配置：来源/模型/地址/提示词模板，下次会自动带出。");
          if (saveBtn) saveBtn.textContent = "已保存";
          window.alert("模型配置已保存。");
          setTimeout(() => {{
            if (saveBtn) saveBtn.textContent = "保存模型配置";
          }}, 1200);
        }} else {{
          setGptStatus("保存失败: " + (data.error || "unknown"));
        }}
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }} finally {{
        if (saveBtn) saveBtn.disabled = false;
      }}
    }});

    if (testLlmBtnEl) testLlmBtnEl.addEventListener("click", async () => {{
      const provider = (providerEl.value || "chatgpt").trim();
      const localBaseUrl = (localBaseEl.value || "").trim();
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const geminiModel = selectedModel(geminiModelSelectEl, geminiModelCustomEl);
      const nvidiaModel = selectedModel(nvidiaModelSelectEl, nvidiaModelCustomEl);
      setLlmTestResult("正在测试模型连接...", null);
      try {{
        const data = await postForm("/test_llm", {{
          provider: provider,
          local_base_url: localBaseUrl,
          deepseek_model: deepseekModel,
          gemini_model: geminiModel,
          nvidia_model: nvidiaModel,
        }});
        if (!data.ok) {{
          setLlmTestResult("连接失败: " + (data.error || "unknown"), false);
          return;
        }}
        const bal = String(data.token_balance_status || "").toLowerCase();
        if (bal === "insufficient") {{
          setLlmTestResult(data.message || "连接成功，但余额不足。", false);
        }} else if (bal === "available") {{
          setLlmTestResult(data.message || "连接测试成功。", true);
        }} else {{
          setLlmTestResult(data.message || "连接测试成功。", null);
        }}
      }} catch (e) {{
        setLlmTestResult("连接失败: " + e, false);
      }}
    }});

    if (analysisPrecheckBtnEl) {{
      analysisPrecheckBtnEl.addEventListener("click", async () => {{
        try {{
          await runAnalysisPrecheck();
        }} catch (e) {{
          setAnalysisPrecheck("分析预估失败: " + e, "error");
        }}
      }});
    }}

    if (saveAnalysisBtnEl) {{
      saveAnalysisBtnEl.addEventListener("click", async () => {{
        const content = buildAnalysisMarkdown();
        if (!content) {{
          setGptStatus("分析结果为空，无法保存。");
          return;
        }}
        const defaultName = "analysis_report_" + String(Date.now()) + ".md";
        try {{
          if (window.showSaveFilePicker) {{
            const handle = await window.showSaveFilePicker({{
              suggestedName: defaultName,
              types: [{{ description: "Markdown", accept: {{ "text/markdown": [".md"] }} }}],
            }});
            const writable = await handle.createWritable();
            await writable.write(content);
            await writable.close();
            setGptStatus("分析报告已保存。");
            return;
          }}
        }} catch (e) {{
          if (String((e && e.name) || "") === "AbortError") {{
            return;
          }}
          setGptStatus("保存失败: " + e);
          return;
        }}
        const nameInput = window.prompt("请输入文件名（默认 .md）", defaultName);
        if (nameInput === null) return;
        let filename = String(nameInput || "").trim() || defaultName;
        if (!/\\.md$/i.test(filename)) filename += ".md";
        try {{
          downloadTextFile(filename, content);
          setGptStatus("分析报告已保存。");
        }} catch (e) {{
          setGptStatus("保存失败: " + e);
        }}
      }});
    }}

    function refreshAnalysisOptionUI() {{
      const batched = !!(batchedAnalysisEl && batchedAnalysisEl.checked);
      if (execFieldsCardEl) execFieldsCardEl.classList.toggle("exec-disabled", !batched);
      if (analysisParallelismEl) analysisParallelismEl.disabled = !batched;
      if (analysisRetriesEl) analysisRetriesEl.disabled = !batched;
      const chunkEnabled = batched && !!(largeReportModeEl && largeReportModeEl.checked);
      if (largeReportChunkItemsEl) largeReportChunkItemsEl.disabled = !chunkEnabled;
      if (chunkItemsFieldEl) chunkItemsFieldEl.classList.toggle("exec-disabled", !chunkEnabled);
    }}
    if (batchedAnalysisEl) batchedAnalysisEl.addEventListener("change", refreshAnalysisOptionUI);
    if (largeReportModeEl) {{
      largeReportModeEl.addEventListener("change", () => {{
        if (largeReportModeEl.checked && batchedAnalysisEl && !batchedAnalysisEl.checked) {{
          batchedAnalysisEl.checked = true;
        }}
        refreshAnalysisOptionUI();
      }});
    }}
    [providerEl, chatgptModelSelectEl, chatgptModelCustomEl, localBaseEl, localModelSelectEl, localModelCustomEl, deepseekModelSelectEl, deepseekModelCustomEl, geminiModelSelectEl, geminiModelCustomEl, nvidiaModelSelectEl, nvidiaModelCustomEl, systemPromptSelectEl, taskPromptSelectEl, systemPromptExtraEl, customPromptEl, batchedAnalysisEl, analysisParallelismEl, analysisRetriesEl, largeReportModeEl, largeReportChunkItemsEl, historyReportFileEl].forEach((el) => {{
      if (el && el.addEventListener) {{
        const evt = (el.tagName === "INPUT" || el.tagName === "TEXTAREA") ? "input" : "change";
        el.addEventListener(evt, resetAnalysisPrecheck);
      }}
    }});

    if (importPromptBtnEl) importPromptBtnEl.addEventListener("click", async () => {{
      if (!canModify) {{
        setGptStatus("当前角色只读，无法导入提示词。");
        return;
      }}
      const file = promptFileEl.files && promptFileEl.files[0];
      if (!file) {{
        setGptStatus("请先选择提示词文件。");
        return;
      }}
      const promptKind = (promptKindSelectEl && promptKindSelectEl.value) ? String(promptKindSelectEl.value) : "task";
      const fallbackName = (file.name || "").replace(/\.[^/.]+$/, "");
      const name = ((promptNameEl.value || "").trim() || fallbackName).trim();
      const form = new FormData();
      form.append("prompt_kind", promptKind);
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
        if (data.prompt_kind === "system") {{
          const prompts = data.prompts || {{}};
          systemPromptMap = prompts;
          refreshPromptSelect("system", systemPromptMap, data.selected_prompt || name);
          setGptStatus("系统提示词导入成功。");
        }} else {{
          const prompts = data.prompts || {{}};
          taskPromptMap = prompts;
          refreshPromptSelect("task", taskPromptMap, data.selected_prompt || name);
          setGptStatus("任务提示词导入成功。");
        }}
      }} catch (e) {{
        setGptStatus("导入失败: " + e);
      }}
    }});

    if (importApiKeyBtnEl) importApiKeyBtnEl.addEventListener("click", async () => {{
      if (!canModify) {{
        setGptStatus("当前角色只读，无法保存 API Key。");
        return;
      }}
      const provider = (providerEl.value || "chatgpt").trim();
      if (provider === "local") {{
        setGptStatus("本地大模型不需要 API Key。");
        return;
      }}
      const existed = provider === "chatgpt"
        ? hasChatgptKeySaved
        : (provider === "deepseek" ? hasDeepseekKeySaved : (provider === "gemini" ? hasGeminiKeySaved : hasNvidiaKeySaved));
      if (existed) {{
        const ok = window.confirm("已存在 API Key，是否覆盖？");
        if (!ok) return;
      }}
      const providerLabel = provider === "chatgpt"
        ? "ChatGPT"
        : (provider === "deepseek" ? "DeepSeek" : (provider === "gemini" ? "Gemini" : "NVIDIA"));
      const key = window.prompt("请输入 " + providerLabel + " API Key:");
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
          apiKeyStateEl.textContent = "ChatGPT Key: " + (data.has_chatgpt_key ? "已保存" : "未保存")
            + " | DeepSeek Key: " + (data.has_deepseek_key ? "已保存" : "未保存")
            + " | Gemini Key: " + (data.has_gemini_key ? "已保存" : "未保存")
            + " | NVIDIA Key: " + (data.has_nvidia_key ? "已保存" : "未保存");
        }}
        setGptStatus(data.overwritten ? "API Key 已覆盖保存。" : "API Key 保存成功。");
      }} catch (e) {{
        setGptStatus("保存失败: " + e);
      }}
    }});

    if (analyzeBtnEl) analyzeBtnEl.addEventListener("click", async () => {{
      analysisRunning = true;
      const file = historyReportFileEl.files && historyReportFileEl.files[0];
      const provider = (providerEl.value || "chatgpt").trim();
      const chatgptModel = selectedModel(chatgptModelSelectEl, chatgptModelCustomEl);
      const localBaseUrl = (localBaseEl.value || "").trim();
      const localModel = selectedModel(localModelSelectEl, localModelCustomEl);
      const deepseekModel = selectedModel(deepseekModelSelectEl, deepseekModelCustomEl);
      const geminiModel = selectedModel(geminiModelSelectEl, geminiModelCustomEl);
      const nvidiaModel = selectedModel(nvidiaModelSelectEl, nvidiaModelCustomEl);
      const selectedSystemPrompt = systemPromptSelectEl.value || "";
      const selectedTaskPrompt = taskPromptSelectEl.value || "";
      const systemPromptExtra = (systemPromptExtraEl.value || "").trim();
      const customPrompt = (customPromptEl.value || "").trim();
      const batchedAnalysis = !!(batchedAnalysisEl && batchedAnalysisEl.checked);
      const analysisParallelism = analysisParallelismEl ? (analysisParallelismEl.value || "2").trim() : "2";
      const analysisRetries = analysisRetriesEl ? (analysisRetriesEl.value || "1").trim() : "1";
      const largeReportMode = !!(largeReportModeEl && largeReportModeEl.checked);
      const largeReportChunkItems = largeReportChunkItemsEl ? (largeReportChunkItemsEl.value || "4").trim() : "4";
      gptResultEl.textContent = "分析中...";
      setAiReportStatus("AI 报告：分析中");
      updateAnalysisProgress(false, 0, "");
      let precheckData = null;
      try {{
        precheckData = await runAnalysisPrecheck();
        const est = (precheckData && precheckData.estimation) ? precheckData.estimation : {{}};
        const maybeTooLarge = Number(est.estimated_total_tokens || 0) > 120000;
        if (maybeTooLarge) {{
          const ok = window.confirm("预计 Token 较高（" + String(est.estimated_total_tokens || 0) + "），可能导致失败或耗时较长。是否继续分析？");
          if (!ok) {{
            gptResultEl.textContent = "已取消分析。";
            setAiReportStatus("AI 报告：待生成");
            analysisRunning = false;
            syncIdleModelStatus();
            return;
          }}
        }}
      }} catch (e) {{
        setAnalysisPrecheck("分析预估失败: " + e, "error");
      }}
      try {{
        let data = null;
        if (file) {{
          setGptStatus("检测到历史报告，正在优先分析历史报告...");
          const form = new FormData();
          form.append("provider", provider);
          form.append("chatgpt_model", chatgptModel);
          form.append("local_base_url", localBaseUrl);
          form.append("local_model", localModel);
          form.append("deepseek_model", deepseekModel);
          form.append("gemini_model", geminiModel);
          form.append("nvidia_model", nvidiaModel);
          form.append("system_prompt_key", selectedSystemPrompt);
          form.append("prompt_key", selectedTaskPrompt);
          form.append("system_prompt_extra", systemPromptExtra);
          form.append("custom_prompt", customPrompt);
          form.append("batched_analysis", batchedAnalysis ? "1" : "0");
          form.append("analysis_parallelism", analysisParallelism);
          form.append("analysis_retries", analysisRetries);
          form.append("large_report_mode", largeReportMode ? "1" : "0");
          form.append("large_report_chunk_items", largeReportChunkItems);
          form.append("report_file", file);
          const resp = await fetch("/analyze_history_report", {{ method: "POST", body: form }});
          data = await resp.json();
          if (data && data.ok && data.async && data.analysis_id) {{
            updateAnalysisProgress(true, 1, "进度: 1%（准备任务）");
            gptResultEl.textContent = "分批分析已启动，请稍候...";
            setAiReportStatus("AI 报告：分批分析中");
            await pollBatchedAnalysis(String(data.analysis_id));
            return;
          }}
        }} else {{
          const hasCurrentReport = !!(latestJobData && latestJobData.status === "success" && latestJobData.report_json);
          if (!hasCurrentReport) {{
            gptResultEl.textContent = "分析失败: 无可用报告。";
            setGptStatus("未检测到可分析的 JSON 报告。请先运行巡检生成 JSON，或导入历史报告后再分析。");
            analysisRunning = false;
            syncIdleModelStatus();
            return;
          }}
          setGptStatus("未导入历史报告，正在分析本次巡检结果...");
          data = await postForm("/analyze_job", {{
            job_id: jobId,
            provider: provider,
            chatgpt_model: chatgptModel,
            local_base_url: localBaseUrl,
            local_model: localModel,
            deepseek_model: deepseekModel,
            gemini_model: geminiModel,
            nvidia_model: nvidiaModel,
            system_prompt_key: selectedSystemPrompt,
            prompt_key: selectedTaskPrompt,
            system_prompt_extra: systemPromptExtra,
            custom_prompt: customPrompt,
            batched_analysis: batchedAnalysis ? "1" : "0",
            analysis_parallelism: analysisParallelism,
            analysis_retries: analysisRetries,
            large_report_mode: largeReportMode ? "1" : "0",
            large_report_chunk_items: largeReportChunkItems,
          }});
          if (data && data.ok && data.async && data.analysis_id) {{
            updateAnalysisProgress(true, 1, "进度: 1%（准备任务）");
            gptResultEl.textContent = "分批分析已启动，请稍候...";
            setAiReportStatus("AI 报告：分批分析中");
            await pollBatchedAnalysis(String(data.analysis_id));
            return;
          }}
          if (batchedAnalysis) {{
            setGptStatus("未进入分批模式，已回退为单次分析。请确认未上传历史报告文件。");
          }}
          updateAnalysisProgress(false, 0, "");
        }}
        if (!data.ok) {{
          gptResultEl.textContent = "分析失败: " + (data.error || "unknown");
          setGptStatus("分析失败。");
          setAiReportStatus("AI 报告：待生成");
          updateAnalysisProgress(false, 0, "");
          analysisRunning = false;
          syncIdleModelStatus();
          return;
        }}
        gptResultEl.textContent = data.analysis || "(empty)";
        setAiReportStatus("AI 报告：报告完成");
        setGptStatus(formatAnalysisDoneStatus(data));
        analysisRunning = false;
      }} catch (e) {{
        gptResultEl.textContent = "分析失败: " + e;
        setGptStatus("分析失败。");
        setAiReportStatus("AI 报告：待生成");
        updateAnalysisProgress(false, 0, "");
        analysisRunning = false;
        syncIdleModelStatus();
      }}
    }});

    if (stopAnalysisBtnEl) stopAnalysisBtnEl.addEventListener("click", async () => {{
      const ok = window.confirm({json.dumps("Stop current AI analysis task?" if is_en else "确认停止当前 AI 分析任务吗？")});
      if (!ok) return;
      try {{
        const data = await postForm("/analysis_stop", {{
          analysis_id: activeAnalysisId || "",
          job_id: jobId || "",
        }});
        if (!data || !data.ok) {{
          setGptStatus({json.dumps("Stop failed: " if is_en else "停止失败: ")} + ((data && data.error) || "unknown"));
          return;
        }}
        if (data.analysis_id) {{
          activeAnalysisId = String(data.analysis_id);
        }}
        setGptStatus(data.message || {json.dumps("Stop requested. Waiting for current call to finish..." if is_en else "已请求停止分析，等待当前调用结束...")});
        setAiReportStatus({json.dumps("AI Report: Stopping" if is_en else "AI 报告：停止中")});
      }} catch (e) {{
        setGptStatus({json.dumps("Stop failed: " if is_en else "停止失败: ")} + e);
      }}
    }});

    if (providerEl) providerEl.addEventListener("change", refreshProviderUI);
    if (providerEl) providerEl.addEventListener("change", syncIdleModelStatus);
    if (chatgptModelSelectEl) chatgptModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (chatgptModelSelectEl) chatgptModelSelectEl.addEventListener("change", syncIdleModelStatus);
    if (localModelSelectEl) localModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (localModelSelectEl) localModelSelectEl.addEventListener("change", syncIdleModelStatus);
    if (deepseekModelSelectEl) deepseekModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (deepseekModelSelectEl) deepseekModelSelectEl.addEventListener("change", syncIdleModelStatus);
    if (geminiModelSelectEl) geminiModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (geminiModelSelectEl) geminiModelSelectEl.addEventListener("change", syncIdleModelStatus);
    if (nvidiaModelSelectEl) nvidiaModelSelectEl.addEventListener("change", refreshCustomModelVisibility);
    if (nvidiaModelSelectEl) nvidiaModelSelectEl.addEventListener("change", syncIdleModelStatus);
    if (localModelSelectEl) localModelSelectEl.addEventListener("change", refreshProviderUI);
    if (localModelCustomEl) localModelCustomEl.addEventListener("input", refreshProviderUI);
    if (localModelCustomEl) localModelCustomEl.addEventListener("input", syncIdleModelStatus);
    if (chatgptModelCustomEl) chatgptModelCustomEl.addEventListener("input", syncIdleModelStatus);
    if (deepseekModelCustomEl) deepseekModelCustomEl.addEventListener("input", syncIdleModelStatus);
    if (geminiModelCustomEl) geminiModelCustomEl.addEventListener("input", syncIdleModelStatus);
    if (nvidiaModelCustomEl) nvidiaModelCustomEl.addEventListener("input", syncIdleModelStatus);
    if (langToggleBtnEl) {{
      langToggleBtnEl.addEventListener("click", () => {{
        const target = currentLang === "zh" ? "en" : "zh";
        const historyFlag = historyMode ? "1" : "0";
        window.location.href = "/job?id=" + encodeURIComponent(jobId) + "&history=" + historyFlag + "&lang=" + encodeURIComponent(target);
      }});
    }}
    refreshProviderUI();
    refreshAnalysisOptionUI();
    syncIdleModelStatus();

    async function poll() {{
      if (historyMode) {{
        return;
      }}
      try {{
        const resp = await fetch("{with_lang('/job_status', lang)}&id=" + encodeURIComponent(jobId), {{ cache: "no-store" }});
        if (!resp.ok) {{
          setState("error", "-");
          outputEl.textContent = "状态获取失败: HTTP " + resp.status;
          return;
        }}
        const data = await resp.json();
        latestJobData = data;
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
    recoverActiveAnalysisIfAny();
    if (window.location.hash === "#ai-analysis") {{
      const aiEl = document.getElementById("ai-analysis");
      if (aiEl) aiEl.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }}
  </script>
</body>
</html>"""
    return localize_html_page(_html, lang)


def build_guide_html(lang: str = "zh") -> str:
    lang = normalize_lang(lang)
    version_line = f"Version: {DOC_VERSION}"
    version_rule = DOC_VERSION_RULE
    _html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HealthCheck 设计逻辑说明</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --card: #ffffff;
      --line: #d5dde7;
      --text: #0f172a;
      --muted: #475569;
      --brand: #0b6e4f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.6 "Helvetica Neue", "PingFang SC", sans-serif;
    }
    .wrap {
      max-width: 1240px;
      margin: 18px auto;
      padding: 0 14px;
    }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }
    .head h1 { margin: 0; font-size: 22px; }
    .version {
      margin: 6px 0 0;
      color: #334155;
      font-weight: 700;
      font-size: 13px;
    }
    .back {
      text-decoration: none;
      color: #fff;
      background: var(--brand);
      border-radius: 8px;
      padding: 8px 12px;
      font-weight: 700;
    }
    .layout {
      display: grid;
      grid-template-columns: 270px 1fr;
      gap: 12px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px;
    }
    .toc {
      position: sticky;
      top: 12px;
      max-height: calc(100vh - 26px);
      overflow: auto;
    }
    .toc h2 { margin: 0 0 8px; font-size: 16px; }
    .toc a {
      display: block;
      padding: 6px 8px;
      border-radius: 6px;
      color: #0f172a;
      text-decoration: none;
      font-weight: 600;
      margin-bottom: 4px;
    }
    .toc a:hover { background: #f1f5f9; }
    .content h2 {
      margin: 16px 0 8px;
      border-left: 4px solid var(--brand);
      padding-left: 8px;
      font-size: 18px;
    }
    .content p { margin: 6px 0; color: var(--muted); }
    .content ul { margin: 6px 0 10px 18px; color: var(--muted); }
    .code {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 10px;
      font-family: Menlo, Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
      color: #0f172a;
    }
    .note {
      border: 1px solid #bbf7d0;
      background: #f0fdf4;
      color: #166534;
      border-radius: 8px;
      padding: 8px 10px;
      margin-top: 8px;
    }
    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
      .toc { position: static; max-height: none; }
    }
    {build_app_header_css()}
  </style>
</head>
<body>
  {build_app_header_html(lang, "docs")}
  <div class="wrap">
    <div class="head">
      <div>
        <h1>HealthCheck 设计逻辑说明</h1>
        <div class="version">__DOC_VERSION__ | __DOC_RULE__</div>
      </div>
      <a class="back" href="{with_lang('/guide', lang)}">返回文档首页</a>
    </div>
    <div class="layout">
      <aside class="card toc">
        <h2>目录大纲</h2>
        <a href="#sec1">1. 业务目标</a>
        <a href="#sec2">2. 整体架构</a>
        <a href="#sec3">3. 页面业务流程</a>
        <a href="#sec4">4. 程序执行逻辑</a>
        <a href="#sec5">5. AI 分析设计</a>
        <a href="#sec6">6. 文件路径层次</a>
        <a href="#sec7">7. 部署依赖（新环境）</a>
        <a href="#sec8">8. 关键设计点</a>
        <a href="#sec9">9. 安全与运维建议</a>
      </aside>
      <main class="card content">
        <section id="sec1">
          <h2>1. 业务目标</h2>
          <p>该系统面向网络工程师，目标是把“多设备巡检、结构化报告输出、AI 诊断”打通为单一流程。</p>
          <ul>
            <li>批量巡检：多设备并发执行检查项和自定义命令。</li>
            <li>报告沉淀：输出 JSON/CSV 便于审计与对比。</li>
            <li>智能诊断：支持 ChatGPT、DeepSeek、Gemini、NVIDIA、本地大模型。</li>
          </ul>
        </section>
        <section id="sec2">
          <h2>2. 整体架构</h2>
          <p>架构由三层组成：Web 前端交互层、任务编排层、巡检执行层。</p>
          <ul>
            <li>前端交互层：表单输入、任务状态轮询、AI 分析触发。</li>
            <li>任务编排层：启动子进程、记录日志、解析报告文件名、回传状态。</li>
            <li>巡检执行层：SSH 登录设备、命令执行、重试与统计、报告写盘。</li>
          </ul>
        </section>
        <section id="sec3">
          <h2>3. 页面业务流程</h2>
          <p>首页提交任务后跳转任务状态页；任务完成后可下载报告或发起 AI 分析。</p>
          <ul>
            <li>首页：输入设备、选择检查项、导入 command_map、设置执行参数。</li>
            <li>检查项模板：保存“当前勾选检查项 + 自定义命令”为模板，支持 Review/编辑/删除。</li>
            <li>任务页：实时日志、状态标签、报告下载。</li>
            <li>AI 分析：优先历史报告，否则本次报告；无报告会提示先运行或导入。</li>
          </ul>
        </section>
        <section id="sec4">
          <h2>4. 程序执行逻辑</h2>
          <p>执行核心由 `app/healthcheck.py` 提供，Web 通过子进程调用并注入标准输入参数。</p>
          <ul>
            <li>设备清洗：去重、格式校验、无效地址过滤。</li>
            <li>命令来源：检查项映射命令 + 自定义命令顺序追加。</li>
            <li>并发策略：auto/serial/parallel + workers 控制。</li>
            <li>结果落盘：`output/reports/inspection_report_*.json|csv`。</li>
          </ul>
        </section>
        <section id="sec5">
          <h2>5. AI 分析设计</h2>
          <p>采用“双层提示词”以提高严谨性：系统提示词约束行为，任务提示词描述本次目标。</p>
          <ul>
            <li>系统提示词：强调证据链、禁止臆测、固定结构输出。</li>
            <li>任务提示词：聚焦接口/协议/资源等具体场景。</li>
            <li>补充输入：系统补充约束 + 任务补充要求。</li>
            <li>分批分析：每台设备单独提交分析并汇总，降低大报告失败率。</li>
            <li>默认采用分片与结构化摘要输入，避免超长上下文导致分析失败。</li>
            <li>模型调用策略统一：先发现模型列表，再做模型候选匹配与端点回退，提升可用性。</li>
            <li>Token 统计：展示本次与累计 token。</li>
          </ul>
        </section>
        <section id="sec6">
          <h2>6. 文件路径层次</h2>
          <div class="code">healthcheck/
app/                 # 核心程序（healthcheck.py / web_server.py / web_runner.py兼容入口）
config/              # command_map.yaml
data/                # devices.txt / intents.txt
docs/                # readme.md
output/reports/      # 巡检结果 JSON/CSV
runtime/tmp/         # 任务临时文件
state/               # gpt_config.json / token_stats.json
scripts/             # 启动脚本（如 start_web.sh）
prompts/
  system_default/    # 默认系统提示词
  system_custom/     # 自定义系统提示词
  task_default/      # 默认任务提示词
  task_custom/       # 自定义任务提示词</div>
        </section>
        <section id="sec7">
          <h2>7. 部署依赖（新环境）</h2>
          <p>在新环境部署前，请确保满足以下最小依赖：</p>
          <ul>
            <li>Python 3.9+（建议 3.10/3.11）。</li>
            <li>Python 包：`paramiko`、`PyYAML`。可选：`certifi`（修复部分 SSL 证书链问题）。</li>
            <li>系统能力：可执行 SSH（TCP/22）访问目标设备；可访问所选 AI 平台 API 域名。</li>
            <li>端口占用：Web 默认 `8080`，可通过环境变量 `HC_WEB_PORT` 修改。</li>
            <li>目录权限：对 `output/reports`、`runtime/tmp`、`state`、`prompts/*` 具备读写权限。</li>
            <li>配置文件：`config/command_map.yaml` 必须存在，或在页面上传临时 map 文件。</li>
            <li>模型配置：若用本地大模型（LM Studio），需保证 `base_url` 可连通且模型已加载。</li>
            <li>若云模型连接测试出现 CERTIFICATE_VERIFY_FAILED，建议通过 `OPENAI_CA_BUNDLE` 指定 certifi 证书链。</li>
          </ul>
          <div class="code"># 推荐安装方式
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install paramiko PyYAML certifi

# 或：使用依赖文件一条命令安装
python -m pip install -r requirements.txt

# 推荐启动（自动注入 OPENAI_CA_BUNDLE）
./scripts/start_web.sh</div>
        </section>
        <section id="sec8">
          <h2>8. 关键设计点</h2>
          <ul>
            <li>相对路径统一：避免绝对路径导致迁移失败。</li>
            <li>模板可维护：提示词文件化管理，支持导入、编辑、删除（删除需确认）。</li>
            <li>检查项模板化：支持将“勾选项 + 自定义命令”保存为模板并快速复用。</li>
            <li>日志可观测：任务状态页轮询输出，支持 debug 开关。</li>
            <li>结果可追溯：报告文件与 AI 分析均保留来源和模型信息。</li>
          </ul>
        </section>
        <section id="sec9">
          <h2>9. 安全与运维建议</h2>
          <ul>
            <li>API Key 仅保存在本机 `state/gpt_config.json`，请控制目录权限。</li>
            <li>建议将 `state/`、`output/reports/`、`runtime/tmp/` 排除在 Git 之外。</li>
            <li>生产环境建议固定系统提示词模板，减少输出漂移。</li>
          </ul>
          <div class="note">如需扩展企业级文档，可增加“版本记录”“变更审计”“故障案例库”章节并持续维护。</div>
        </section>
      </main>
    </div>
  </div>
</body>
</html>"""
    _html = _html.replace("{with_lang('/guide', lang)}", with_lang("/guide", lang))
    _html = _html.replace("{build_app_header_css()}", build_app_header_css())
    _html = _html.replace("{build_app_header_html(lang, \"docs\")}", build_app_header_html(lang, "docs"))
    _html = _html.replace("__DOC_VERSION__", version_line)
    _html = _html.replace("__DOC_RULE__", version_rule)
    return localize_html_page(_html, lang)


def build_guide_index_html(lang: str = "zh") -> str:
    lang = normalize_lang(lang)
    version_line = f"Version: {DOC_VERSION}"
    version_rule = DOC_VERSION_RULE
    _html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HealthCheck 文档中心</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --card: #ffffff;
      --line: #d5dde7;
      --text: #0f172a;
      --muted: #475569;
      --brand: #0b6e4f;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.6 "Helvetica Neue", "PingFang SC", sans-serif; }
    .wrap { max-width: 980px; margin: 24px auto; padding: 0 16px; }
    .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .head h1 { margin: 0; font-size: 24px; }
    .version {
      margin: 6px 0 0;
      color: #334155;
      font-weight: 700;
      font-size: 13px;
    }
    .back { text-decoration: none; color: #fff; background: var(--brand); border-radius: 8px; padding: 8px 12px; font-weight: 700; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
    .card h2 { margin: 0 0 8px; font-size: 18px; }
    .card p { margin: 0 0 10px; color: var(--muted); }
    .go { display: inline-block; text-decoration: none; color: #fff; background: var(--brand); border-radius: 8px; padding: 8px 12px; font-weight: 700; }
    @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
    {build_app_header_css()}
  </style>
</head>
<body>
  {build_app_header_html(lang, "docs")}
  <div class="wrap">
    <div class="head">
      <div>
        <h1>HealthCheck 文档中心</h1>
        <div class="version">__DOC_VERSION__ | __DOC_RULE__</div>
      </div>
      <a class="back" href="{with_lang('/', lang)}">返回首页</a>
    </div>
    <div class="grid">
      <div class="card">
        <h2>程序设计逻辑文档</h2>
        <p>面向开发与维护人员，说明业务设计思路、程序逻辑、目录层次、关键设计点。</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <a class="go" href="/guide/design?lang=zh">中文</a>
          <a class="go" href="/guide/design?lang=en">English</a>
        </div>
      </div>
      <div class="card">
        <h2>用户使用说明文档</h2>
        <p>面向操作人员，说明从首页配置、任务执行、报告下载到 AI 分析的完整使用流程。</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <a class="go" href="/guide/user?lang=zh">中文</a>
          <a class="go" href="/guide/user?lang=en">English</a>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
    _html = _html.replace("{with_lang('/', lang)}", with_lang("/", lang))
    _html = _html.replace("{build_app_header_css()}", build_app_header_css())
    _html = _html.replace("{build_app_header_html(lang, \"docs\")}", build_app_header_html(lang, "docs"))
    _html = _html.replace("__DOC_VERSION__", version_line)
    _html = _html.replace("__DOC_RULE__", version_rule)
    return localize_html_page(_html, lang)


def build_user_guide_html(lang: str = "zh") -> str:
    lang = normalize_lang(lang)
    version_line = f"Version: {DOC_VERSION}"
    version_rule = DOC_VERSION_RULE
    _html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HealthCheck 用户使用说明</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --card: #ffffff;
      --line: #d5dde7;
      --text: #0f172a;
      --muted: #475569;
      --brand: #0b6e4f;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.6 "Helvetica Neue", "PingFang SC", sans-serif; }
    .wrap { max-width: 1240px; margin: 18px auto; padding: 0 14px; }
    .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
    .head h1 { margin: 0; font-size: 22px; }
    .version {
      margin: 6px 0 0;
      color: #334155;
      font-weight: 700;
      font-size: 13px;
    }
    .back { text-decoration: none; color: #fff; background: var(--brand); border-radius: 8px; padding: 8px 12px; font-weight: 700; }
    .layout { display: grid; grid-template-columns: 270px 1fr; gap: 12px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
    .toc { position: sticky; top: 12px; max-height: calc(100vh - 26px); overflow: auto; }
    .toc h2 { margin: 0 0 8px; font-size: 16px; }
    .toc a { display: block; padding: 6px 8px; border-radius: 6px; color: #0f172a; text-decoration: none; font-weight: 600; margin-bottom: 4px; }
    .toc a:hover { background: #f1f5f9; }
    .content h2 { margin: 16px 0 8px; border-left: 4px solid var(--brand); padding-left: 8px; font-size: 18px; }
    .content p { margin: 6px 0; color: var(--muted); }
    .content ul { margin: 6px 0 10px 18px; color: var(--muted); }
    .code { border: 1px solid var(--line); border-radius: 8px; background: #f8fafc; padding: 10px; font-family: Menlo, Consolas, monospace; font-size: 12px; white-space: pre-wrap; color: #0f172a; }
    @media (max-width: 920px) { .layout { grid-template-columns: 1fr; } .toc { position: static; max-height: none; } }
    {build_app_header_css()}
  </style>
</head>
<body>
  {build_app_header_html(lang, "docs")}
  <div class="wrap">
    <div class="head">
      <div>
        <h1>HealthCheck 用户使用说明</h1>
        <div class="version">__DOC_VERSION__ | __DOC_RULE__</div>
      </div>
      <a class="back" href="{with_lang('/guide', lang)}">返回文档首页</a>
    </div>
    <div class="layout">
      <aside class="card toc">
        <h2>目录大纲</h2>
        <a href="#u1">1. 启动与访问</a>
        <a href="#u2">2. 首页巡检配置</a>
        <a href="#u3">3. 任务状态页</a>
        <a href="#u4">4. AI 分析流程</a>
        <a href="#u5">5. 提示词管理</a>
        <a href="#u6">6. 新环境部署依赖</a>
        <a href="#u7">7. 常见问题</a>
      </aside>
      <main class="card content">
        <section id="u1">
          <h2>1. 启动与访问</h2>
          <p>推荐使用 `./scripts/start_web.sh` 启动（自动注入证书链），默认访问 `http://127.0.0.1:8080`。</p>
          <div class="code">cd healthcheck
./scripts/start_web.sh

# 兼容入口（保留）
python3 web_runner.py</div>
        </section>
        <section id="u2">
          <h2>2. 首页巡检配置</h2>
          <ul>
            <li>输入 SSH 用户名/密码。</li>
            <li>填写设备地址（每行一个），或导入设备文件。</li>
            <li>勾选检查项，可同时填写自定义命令（按行执行）。</li>
            <li>可把“当前勾选检查项 + 自定义命令”保存为检查项模板，供下次一键复用。</li>
            <li>设置执行模式、并发 workers、重试次数、debug。</li>
            <li>点击“执行 Python 巡检脚本”。</li>
          </ul>
          <p>提示：首页支持本地记忆；可通过“清空已保存配置”重置。</p>
        </section>
        <section id="u3">
          <h2>3. 任务状态页</h2>
          <ul>
            <li>显示实时日志与任务状态（执行中/完成/失败）。</li>
            <li>任务成功后显示本次 JSON/CSV 报告下载。</li>
            <li>支持返回首页继续下一次巡检。</li>
          </ul>
        </section>
        <section id="u4">
          <h2>4. AI 分析流程</h2>
          <ul>
            <li>点击“AI 分析”后：若导入了历史报告，优先分析历史报告。</li>
            <li>未导入历史报告时，分析本次任务结果。</li>
            <li>若无历史报告且本次任务无可用报告，会提示先运行巡检或导入报告。</li>
            <li>启用分批模式时：每台设备单独提交分析，页面显示进度和批次。</li>
            <li>分片模式：单设备按检查项分片分析后汇总，适合大报告。</li>
            <li>普通模式：为避免模型上下文超限，会保留关键证据并做受控截断。</li>
            <li>云模型调用统一采用“模型发现 + 候选匹配 + 端点回退”策略，减少模型/端点差异导致的失败。</li>
            <li>分析完成后会显示本次 token 与累计 token。</li>
          </ul>
        </section>
        <section id="u5">
          <h2>5. 提示词管理</h2>
          <ul>
            <li>系统提示词：用于严格约束 AI 输出规范。</li>
            <li>任务提示词：用于定义本次分析重点。</li>
            <li>支持导入到“系统提示词/任务提示词”。</li>
            <li>支持 Review 后编辑、保存、删除（删除/保存均有确认）。</li>
          </ul>
        </section>
        <section id="u6">
          <h2>6. 新环境部署依赖</h2>
          <ul>
            <li>Python 3.9+。</li>
            <li>安装依赖：`pip install paramiko PyYAML certifi`。</li>
            <li>确保 `config/command_map.yaml` 存在（或页面上传）。</li>
            <li>确保可写目录：`output/reports`、`runtime/tmp`、`state`、`prompts/*`。</li>
            <li>确保网络连通：设备 SSH（22 端口）与所选 AI 服务 API 域名。</li>
            <li>本地大模型模式需保证 LM Studio 地址可访问且模型可用。</li>
            <li>若 DeepSeek/NVIDIA/Gemini/OpenAI 连接报证书错误，优先使用 `scripts/start_web.sh` 启动。</li>
          </ul>
          <div class="code">cd healthcheck
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install paramiko PyYAML certifi
# 或
python -m pip install -r requirements.txt
./scripts/start_web.sh</div>
        </section>
        <section id="u7">
          <h2>7. 常见问题</h2>
          <ul>
            <li>页面转圈不更新：可开启 debug 模式查看完整日志。</li>
            <li>连接测试失败：检查模型服务地址、API Key、网络连通性。</li>
            <li>保存模型配置无反馈：新版会弹确认框，并在保存成功后弹窗提示“模型配置已保存”。</li>
            <li>图标不显示：系统会自动回退到内置图标，不影响分析功能。</li>
          </ul>
        </section>
      </main>
    </div>
  </div>
</body>
</html>"""
    _html = _html.replace("{with_lang('/guide', lang)}", with_lang("/guide", lang))
    _html = _html.replace("{build_app_header_css()}", build_app_header_css())
    _html = _html.replace("{build_app_header_html(lang, \"docs\")}", build_app_header_html(lang, "docs"))
    _html = _html.replace("__DOC_VERSION__", version_line)
    _html = _html.replace("__DOC_RULE__", version_rule)
    return localize_html_page(_html, lang)


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
        "Jump mode direct/ssh/smc (default: direct):",
        "Jump host address:",
        "Jump host port (default: 22):",
        "Jump host username:",
        "Jump host password:",
        "SMC command template (default: smc server toc {jump_host}):",
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


def _format_ts(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return "-"


def build_tasks_page(lang: str = "zh", auth_username: str = "", auth_role: str = "user") -> str:
    lang = normalize_lang(lang)
    ensure_task_store()
    ensure_analysis_services()
    rows = TASK_STORE.list_tasks(300) if TASK_STORE else []
    h = ("任务ID", "创建时间", "状态", "AI 分析", "设备数", "报告", "操作")
    if lang == "en":
        h = ("Task ID", "Created At", "Status", "AI Analysis", "Devices", "Reports", "Actions")
    body_rows = []
    for row in rows:
        tid = str(row.get("task_id", "") or "")
        devices = row.get("devices", []) if isinstance(row.get("devices"), list) else []
        report_json = str(row.get("report_json", "") or "")
        report_csv = str(row.get("report_csv", "") or "")
        links = []
        if report_json:
            links.append(f'<a href="{with_lang("/download?name=" + report_json, lang)}">JSON</a>')
        if report_csv:
            links.append(f'<a href="{with_lang("/download?name=" + report_csv, lang)}">CSV</a>')
        if not links:
            links.append("-")
        ai_cell = "-"
        if ANALYSIS_STATUS_STORE:
            analysis_id = ANALYSIS_STATUS_STORE.find_analysis_id_by_job(tid, running_only=False)
            if analysis_id:
                ai_payload = ANALYSIS_STATUS_STORE.get_response_payload(analysis_id)
                ai_status = str(ai_payload.get("status", "unknown") or "unknown")
                if lang == "zh":
                    ai_label_map = {
                        "running": "分析中",
                        "done": "已完成",
                        "canceled": "已取消",
                        "error": "失败",
                    }
                    ai_label = ai_label_map.get(ai_status, ai_status)
                    if ai_status == "running":
                        ai_cell = f'{html.escape(ai_label)} | <a href="{with_lang("/tasks/" + tid + "#ai-analysis", lang)}">查看进度</a>'
                    else:
                        ai_cell = html.escape(ai_label)
                else:
                    ai_label_map = {
                        "running": "Running",
                        "done": "Done",
                        "canceled": "Canceled",
                        "error": "Failed",
                    }
                    ai_label = ai_label_map.get(ai_status, ai_status)
                    if ai_status == "running":
                        ai_cell = f'{html.escape(ai_label)} | <a href="{with_lang("/tasks/" + tid + "#ai-analysis", lang)}">View Progress</a>'
                    else:
                        ai_cell = html.escape(ai_label)
        action_text = "查看" if lang == "zh" else "View"
        body_rows.append(
            "<tr>"
            f"<td><a href=\"{with_lang('/tasks/' + tid, lang)}\">{html.escape(tid)}</a></td>"
            f"<td>{html.escape(_format_ts(float(row.get('created_at', 0.0) or 0.0)))}</td>"
            f"<td>{html.escape(str(row.get('status', 'unknown') or 'unknown'))}</td>"
            f"<td>{ai_cell}</td>"
            f"<td>{len(devices)}</td>"
            f"<td>{' | '.join(links)}</td>"
            f"<td><a href=\"{with_lang('/tasks/' + tid, lang)}\">{action_text}</a></td>"
            "</tr>"
        )
    page_css = """
    body { margin:0; background:#f6f8fb; color:#0f172a; font:14px/1.5 "Segoe UI","PingFang SC",sans-serif; }
    .wrap { max-width:980px; margin:28px auto; padding:0 16px; }
    .card { background:#fff; border:1px solid #d6dce3; border-radius:10px; padding:14px; }
    h2 { margin:0 0 10px; }
    table { width:100%; border-collapse:collapse; }
    th, td { border-bottom:1px solid #e2e8f0; text-align:left; padding:8px; }
    th { background:#f8fafc; }
    a { color:#0b6e4f; text-decoration:none; font-weight:600; }
    """
    title = "任务页面" if lang == "zh" else "Tasks"
    body_html = f"""
    {build_app_header_html(lang, "tasks")}
    <div class="wrap">
      <div class="card">
        <h2>{title}</h2>
        <table>
          <thead><tr><th>{h[0]}</th><th>{h[1]}</th><th>{h[2]}</th><th>{h[3]}</th><th>{h[4]}</th><th>{h[5]}</th><th>{h[6]}</th></tr></thead>
          <tbody>{''.join(body_rows) if body_rows else '<tr><td colspan="7">-</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    """
    return f"""<!DOCTYPE html>
<html lang="{ 'en' if lang == 'en' else 'zh-CN' }">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    {build_app_header_css()}
    {page_css}
  </style>
</head>
<body>
{body_html}
</body>
</html>
"""


def build_ai_settings_page(lang: str = "zh", auth_username: str = "", auth_role: str = "user", can_modify: bool = True) -> str:
    lang = normalize_lang(lang)
    is_en = lang == "en"
    cfg = load_gpt_config()
    provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
    if provider not in {"chatgpt", "deepseek", "gemini", "nvidia", "local"}:
        provider = "chatgpt"
    modify_disabled = "disabled" if not can_modify else ""

    saved_system_prompt_key = str(cfg.get("selected_system_prompt", "网络工程师-严格模式") or "")
    saved_task_prompt_key = str(cfg.get("selected_task_prompt", cfg.get("selected_prompt", "")) or "")
    saved_system_prompt_extra = str(cfg.get("system_prompt_extra", "") or "")
    saved_task_prompt_extra = str(cfg.get("task_prompt_extra", "") or "")

    has_chatgpt_key = bool((cfg.get("chatgpt_api_key") or "").strip())
    has_deepseek_key = bool((cfg.get("deepseek_api_key") or "").strip())
    has_gemini_key = bool((cfg.get("gemini_api_key") or "").strip())
    has_nvidia_key = bool((cfg.get("nvidia_api_key") or "").strip())

    chatgpt_model = str(cfg.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL)
    local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL)
    local_model = str(cfg.get("local_model", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL)
    deepseek_model = str(cfg.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL)
    gemini_model = str(cfg.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL)
    nvidia_model = str(cfg.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL)
    chatgpt_in_options = chatgpt_model in CHATGPT_MODEL_OPTIONS
    local_in_options = local_model in LOCAL_MODEL_OPTIONS
    deepseek_in_options = deepseek_model in DEEPSEEK_MODEL_OPTIONS
    gemini_in_options = gemini_model in GEMINI_MODEL_OPTIONS
    nvidia_in_options = nvidia_model in NVIDIA_MODEL_OPTIONS
    chatgpt_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if chatgpt_model == m else ""}>{html.escape(m)}</option>' for m in CHATGPT_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not chatgpt_in_options else ""}>{"Custom..." if is_en else "自定义..."}</option>']
    )
    local_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if local_model == m else ""}>{html.escape(m)}</option>' for m in LOCAL_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not local_in_options else ""}>{"Custom..." if is_en else "自定义..."}</option>']
    )
    deepseek_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if deepseek_model == m else ""}>{html.escape(m)}</option>' for m in DEEPSEEK_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not deepseek_in_options else ""}>{"Custom..." if is_en else "自定义..."}</option>']
    )
    gemini_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if gemini_model == m else ""}>{html.escape(m)}</option>' for m in GEMINI_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not gemini_in_options else ""}>{"Custom..." if is_en else "自定义..."}</option>']
    )
    nvidia_model_options = "".join(
        [f'<option value="{html.escape(m)}" {"selected" if nvidia_model == m else ""}>{html.escape(m)}</option>' for m in NVIDIA_MODEL_OPTIONS]
        + [f'<option value="__custom__" {"selected" if not nvidia_in_options else ""}>{"Custom..." if is_en else "自定义..."}</option>']
    )

    title = "AI 设置" if not is_en else "AI Settings"

    body_html = f"""
  {build_app_header_html(lang, "ai")}
  <div class="wrap">
    <div class="card ai-card">
      <div class="ai-head">
        <span id="provider_brand_inline" class="ai-brand"></span>
        <h2>{'AI 分析设置' if not is_en else 'AI Analysis Settings'}</h2>
      </div>
      <div class="ai-section">
        <div class="ai-section-title">{'大模型配置' if not is_en else 'LLM Configuration'}</div>
        <div class="ai-grid">
          <div>
            <label>{'大模型选择' if not is_en else 'Provider'}</label>
            <select id="provider_select">
              <option value="chatgpt" {"selected" if provider == "chatgpt" else ""}>ChatGPT</option>
              <option value="deepseek" {"selected" if provider == "deepseek" else ""}>DeepSeek</option>
              <option value="gemini" {"selected" if provider == "gemini" else ""}>Gemini</option>
              <option value="nvidia" {"selected" if provider == "nvidia" else ""}>NVIDIA</option>
              <option value="local" {"selected" if provider == "local" else ""}>{'本地大模型' if not is_en else 'Local Model'}</option>
            </select>
            <div id="llm_test_result" class="hint">{'模型连接测试结果将在此显示。' if not is_en else 'Connection test result will be shown here.'}</div>
          </div>
          <div>
            <label>{'API Key 管理' if not is_en else 'API Key Management'}</label>
            <div class="actions" style="margin-top:0;">
              <button class="btn" id="import_api_key_btn" type="button" {modify_disabled}>{'导入 API Key' if not is_en else 'Import API Key'}</button>
              <button class="btn" id="test_llm_btn" type="button">{'模型连接测试' if not is_en else 'Test Connection'}</button>
              <button class="btn" id="save_llm_btn" type="button" {modify_disabled}>{'保存模型配置' if not is_en else 'Save Config'}</button>
            </div>
            <div class="hint">{'用途：保存当前大模型来源、模型名、本地地址、已选提示词模板。下次打开页面自动带出。' if not is_en else 'Saves provider, model, local endpoint and selected prompts for next visit.'}</div>
            <div id="api_key_state" class="hint"></div>
          </div>
        </div>

        <div id="chatgpt_settings" class="ai-grid ai-row" {("style='display:none;'" if provider != "chatgpt" else "")}>
          <div>
            <label>ChatGPT {'模型' if not is_en else 'Model'}</label>
            <select id="chatgpt_model_select">{chatgpt_model_options}</select>
          </div>
          <div id="chatgpt_custom_wrap" {("style='display:none;'" if chatgpt_in_options else "")}>
            <label>{'自定义 ChatGPT 模型' if not is_en else 'Custom ChatGPT Model'}</label>
            <input id="chatgpt_model_custom" value="{html.escape('' if chatgpt_in_options else chatgpt_model)}" placeholder="gpt-4.1-mini">
          </div>
        </div>

        <div id="deepseek_settings" class="ai-grid ai-row" {("style='display:none;'" if provider != "deepseek" else "")}>
          <div>
            <label>DeepSeek {'模型' if not is_en else 'Model'}</label>
            <select id="deepseek_model_select">{deepseek_model_options}</select>
          </div>
          <div id="deepseek_custom_wrap" {("style='display:none;'" if deepseek_in_options else "")}>
            <label>{'自定义 DeepSeek 模型' if not is_en else 'Custom DeepSeek Model'}</label>
            <input id="deepseek_model_custom" value="{html.escape('' if deepseek_in_options else deepseek_model)}" placeholder="deepseek-chat">
          </div>
        </div>

        <div id="gemini_settings" class="ai-grid ai-row" {("style='display:none;'" if provider != "gemini" else "")}>
          <div>
            <label>Gemini {'模型' if not is_en else 'Model'}</label>
            <select id="gemini_model_select">{gemini_model_options}</select>
          </div>
          <div id="gemini_custom_wrap" {("style='display:none;'" if gemini_in_options else "")}>
            <label>{'自定义 Gemini 模型' if not is_en else 'Custom Gemini Model'}</label>
            <input id="gemini_model_custom" value="{html.escape('' if gemini_in_options else gemini_model)}" placeholder="gemini-2.0-flash">
          </div>
        </div>

        <div id="nvidia_settings" class="ai-grid ai-row" {("style='display:none;'" if provider != "nvidia" else "")}>
          <div>
            <label>NVIDIA {'模型' if not is_en else 'Model'}</label>
            <select id="nvidia_model_select">{nvidia_model_options}</select>
          </div>
          <div id="nvidia_custom_wrap" {("style='display:none;'" if nvidia_in_options else "")}>
            <label>{'自定义 NVIDIA 模型' if not is_en else 'Custom NVIDIA Model'}</label>
            <input id="nvidia_model_custom" value="{html.escape('' if nvidia_in_options else nvidia_model)}" placeholder="meta/llama-3.1-405b-instruct">
          </div>
        </div>

        <div id="local_settings" class="ai-grid ai-row" {("style='display:none;'" if provider != "local" else "")}>
          <div>
            <label>{'本地大模型地址' if not is_en else 'Local Model Endpoint'}</label>
            <input id="local_base_url" value="{html.escape(local_base_url)}" placeholder="http://127.0.0.1:1234">
          </div>
          <div>
            <label>{'本地大模型模型' if not is_en else 'Local Model Name'}</label>
            <select id="local_model_select">{local_model_options}</select>
          </div>
          <div id="local_custom_wrap" {("style='display:none;'" if local_in_options else "")}>
            <label>{'自定义本地模型' if not is_en else 'Custom Local Model'}</label>
            <input id="local_model_custom" value="{html.escape('' if local_in_options else local_model)}" placeholder="qwen/qwen3-coder-30b">
          </div>
        </div>
      </div>

      <div class="ai-section">
        <div class="ai-section-title">{'提示词设置' if not is_en else 'Prompt Settings'}</div>
        <div class="hint">{'提示词已迁移到任务页面中的 AI 分析设置。请到“任务页面”配置并执行分析。' if not is_en else 'Prompt settings were moved to Task page under AI Analysis Settings. Please configure prompts there.'}</div>
      </div>
    </div>
  </div>
"""

    page_css = """
    body { margin:0; background:#f6f8fb; color:#0f172a; font:14px/1.5 "Segoe UI","PingFang SC",sans-serif; }
    .wrap { max-width:980px; margin:28px auto; padding:0 16px; }
    .card { border:1px solid #d6dce3; border-radius:12px; padding:14px; background:#fff; }
    .ai-card { background:#f8fafc; }
    .ai-head { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
    .ai-head h2 { margin:0; font-size:40px; line-height:1.06; font-weight:800; letter-spacing:-0.02em; color:#0f172a; }
    .ai-brand { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px; flex:0 0 28px; }
    .ai-brand img { width:24px; height:24px; border-radius:4px; display:block; }
    .ai-brand svg { width:24px; height:24px; display:block; }
    .ai-section { border:1px solid #d7dee7; border-radius:10px; background:#fff; padding:12px; margin-top:12px; }
    .ai-section-title { font-weight:800; color:#1e293b; font-size:29px; line-height:1.08; margin:0 0 8px; letter-spacing:-0.02em; }
    .ai-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .ai-row { margin-top:12px; }
    .ai-card input[type=text], .ai-card input[type=password], .ai-card input[type=number], .ai-card select, .ai-card textarea {
      width:100%; max-width:none; border:1px solid #cbd5e1; border-radius:8px; padding:8px 10px; background:#fff; box-sizing:border-box;
    }
    .ai-card input[type=text], .ai-card input[type=password], .ai-card input[type=number], .ai-card select { min-height:42px; }
    .ai-card textarea { min-height:90px; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }
    .btn { border:1px solid #cbd5e1; border-radius:10px; background:#fff; color:#0f172a; padding:9px 14px; min-height:42px; font-weight:700; cursor:pointer; }
    .btn:disabled { opacity:0.6; cursor:not-allowed; }
    .hint { font-size:12px; color:#475569; margin-top:6px; white-space:pre-wrap; line-height:1.45; }
    @media (max-width: 920px) {
      .ai-grid { grid-template-columns:1fr; }
      .ai-head h2 { font-size:32px; }
      .ai-section-title { font-size:24px; }
    }
"""

    page_script = f"""
<script>
  function id(v) {{ return document.getElementById(v); }}
  function on(v, ev, fn) {{ const el = id(v); if (el) el.addEventListener(ev, fn); }}
  const UI_LANG = {json.dumps(lang)};
  const CAN_MODIFY = {str(can_modify).lower()};
  const SAVED_SYSTEM_PROMPT_KEY = {json.dumps(saved_system_prompt_key, ensure_ascii=False)};
  const SAVED_TASK_PROMPT_KEY = {json.dumps(saved_task_prompt_key, ensure_ascii=False)};
  const SAVED_SYSTEM_PROMPT_EXTRA = {json.dumps(saved_system_prompt_extra, ensure_ascii=False)};
  const SAVED_TASK_PROMPT_EXTRA = {json.dumps(saved_task_prompt_extra, ensure_ascii=False)};
  let savedKeyState = {{
    chatgpt: {str(has_chatgpt_key).lower()},
    deepseek: {str(has_deepseek_key).lower()},
    gemini: {str(has_gemini_key).lower()},
    nvidia: {str(has_nvidia_key).lower()}
  }};
  function selectedModel(selectEl, customEl) {{
    if (!selectEl) return "";
    const val = (selectEl.value || "").trim();
    if (val === "__custom__") return ((customEl && customEl.value) || "").trim();
    return val;
  }}
  function syncCustomModelInput(selectId, wrapId, inputId) {{
    const sel = id(selectId);
    const wrap = id(wrapId);
    if (!sel || !wrap) return;
    wrap.style.display = sel.value === "__custom__" ? "" : "none";
    if (sel.value !== "__custom__") {{
      const input = id(inputId);
      if (input) input.value = "";
    }}
  }}
  function updateApiKeyState() {{
    const savedTxt = UI_LANG === "en" ? "Saved" : "已保存";
    const missTxt = UI_LANG === "en" ? "Not saved" : "未保存";
    id("api_key_state").textContent = [
      "ChatGPT Key: " + (savedKeyState.chatgpt ? savedTxt : missTxt),
      "DeepSeek Key: " + (savedKeyState.deepseek ? savedTxt : missTxt),
      "Gemini Key: " + (savedKeyState.gemini ? savedTxt : missTxt),
      "NVIDIA Key: " + (savedKeyState.nvidia ? savedTxt : missTxt),
    ].join(" | ");
  }}
  function setLlmMsg(msg, ok) {{
    const el = id("llm_test_result");
    if (!el) return;
    el.textContent = msg || "";
    if (ok === true) el.style.color = "#0b6e4f";
    else if (ok === false) el.style.color = "#b91c1c";
    else el.style.color = "#475569";
  }}
  function getConfigFromUI() {{
    return {{
      provider: (id("provider_select")?.value || "chatgpt"),
      chatgpt_model: selectedModel(id("chatgpt_model_select"), id("chatgpt_model_custom")) || {json.dumps(DEFAULT_GPT_MODEL)},
      deepseek_model: selectedModel(id("deepseek_model_select"), id("deepseek_model_custom")) || {json.dumps(DEFAULT_DEEPSEEK_MODEL)},
      gemini_model: selectedModel(id("gemini_model_select"), id("gemini_model_custom")) || {json.dumps(DEFAULT_GEMINI_MODEL)},
      nvidia_model: selectedModel(id("nvidia_model_select"), id("nvidia_model_custom")) || {json.dumps(DEFAULT_NVIDIA_MODEL)},
      local_base_url: (id("local_base_url")?.value || {json.dumps(DEFAULT_LOCAL_BASE_URL)}).trim(),
      local_model: selectedModel(id("local_model_select"), id("local_model_custom")) || {json.dumps(DEFAULT_LOCAL_MODEL)},
      selected_system_prompt: SAVED_SYSTEM_PROMPT_KEY,
      selected_task_prompt: SAVED_TASK_PROMPT_KEY,
      system_prompt_extra: SAVED_SYSTEM_PROMPT_EXTRA,
      task_prompt_extra: SAVED_TASK_PROMPT_EXTRA,
    }};
  }}
  function updateProviderIcon() {{
    const providerEl = id("provider_select");
    const iconEl = id("provider_brand_inline");
    if (!providerEl || !iconEl) return;
    const p = (providerEl.value || "chatgpt").trim();
    const svgDataUri = (bg, txt) => {{
      const svg =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' +
        '<rect width="24" height="24" rx="6" fill="' + bg + '"/>' +
        '<text x="12" y="15" text-anchor="middle" font-size="9" font-family="Arial, sans-serif" fill="white">' + txt + '</text>' +
        '</svg>';
      return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
    }};
    const setBrand = (url, alt, title, fbBg, fbTxt) => {{
      const fb = svgDataUri(fbBg, fbTxt);
      iconEl.innerHTML = '<img src="' + url + '" alt="' + alt + '" title="' + title + '">';
      const img = iconEl.querySelector("img");
      if (img) {{
        img.onerror = () => {{ img.onerror = null; img.src = fb; }};
      }}
    }};
    if (p === "chatgpt") return setBrand("https://openai.com/favicon.ico", "OpenAI", "ChatGPT", "#10a37f", "OA");
    if (p === "deepseek") return setBrand("https://www.deepseek.com/favicon.ico", "DeepSeek", "DeepSeek", "#2563eb", "DS");
    if (p === "gemini") return setBrand("https://www.gstatic.com/lamda/images/gemini_sparkle_aurora_33f86dc0c0257da337c63.svg", "Gemini", "Gemini", "#3b82f6", "GM");
    if (p === "nvidia") return setBrand("https://www.nvidia.com/favicon.ico", "NVIDIA", "NVIDIA", "#76b900", "NV");
    setBrand("https://lmstudio.ai/favicon.ico", "Local", "Local LLM", "#334155", "LM");
  }}
  function updateProviderSection() {{
    const p = id("provider_select")?.value || "chatgpt";
    if (id("chatgpt_settings")) id("chatgpt_settings").style.display = p === "chatgpt" ? "" : "none";
    if (id("deepseek_settings")) id("deepseek_settings").style.display = p === "deepseek" ? "" : "none";
    if (id("gemini_settings")) id("gemini_settings").style.display = p === "gemini" ? "" : "none";
    if (id("nvidia_settings")) id("nvidia_settings").style.display = p === "nvidia" ? "" : "none";
    if (id("local_settings")) id("local_settings").style.display = p === "local" ? "" : "none";
    syncCustomModelInput("chatgpt_model_select", "chatgpt_custom_wrap", "chatgpt_model_custom");
    syncCustomModelInput("deepseek_model_select", "deepseek_custom_wrap", "deepseek_model_custom");
    syncCustomModelInput("gemini_model_select", "gemini_custom_wrap", "gemini_model_custom");
    syncCustomModelInput("nvidia_model_select", "nvidia_custom_wrap", "nvidia_model_custom");
    syncCustomModelInput("local_model_select", "local_custom_wrap", "local_model_custom");
    updateProviderIcon();
  }}
  async function postForm(url, formBody) {{
    const res = await fetch(url, {{
      method: "POST",
      headers: {{ "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }},
      body: new URLSearchParams(formBody)
    }});
    return await res.json();
  }}

  on("save_llm_btn", "click", async () => {{
    if (!CAN_MODIFY) {{ setLlmMsg(UI_LANG === "en" ? "Read-only role cannot save settings." : "当前角色只读，无法保存配置。", false); return; }}
    const cfg = getConfigFromUI();
    setLlmMsg(UI_LANG === "en" ? "Saving..." : "正在保存...", null);
    const data = await postForm("/save_gpt_key", cfg);
    if (!data.ok) {{ setLlmMsg((UI_LANG === "en" ? "Save failed: " : "保存失败: ") + (data.error || "unknown"), false); return; }}
    setLlmMsg(UI_LANG === "en" ? "Configuration saved." : "模型配置已保存。", true);
  }});

  on("test_llm_btn", "click", async () => {{
    const cfg = getConfigFromUI();
    const fd = new FormData();
    Object.entries(cfg).forEach(([k,v]) => fd.append(k, String(v == null ? "" : v)));
    setLlmMsg(UI_LANG === "en" ? "Testing connection..." : "连接测试中...", null);
    const res = await fetch("/test_llm", {{ method: "POST", body: fd }});
    const data = await res.json();
    if (!data.ok) {{ setLlmMsg((UI_LANG === "en" ? "Connection failed: " : "连接失败: ") + (data.error || "unknown"), false); return; }}
    const bal = String(data.token_balance_status || "").toLowerCase();
    if (bal === "insufficient") setLlmMsg(data.message || (UI_LANG === "en" ? "Connected but token insufficient." : "连接成功，但余额不足。"), false);
    else setLlmMsg(data.message || (UI_LANG === "en" ? "Connection test succeeded." : "连接测试成功。"), true);
  }});

  on("import_api_key_btn", "click", async () => {{
    if (!CAN_MODIFY) {{ setLlmMsg(UI_LANG === "en" ? "Read-only role cannot save API keys." : "当前角色只读，无法保存 API Key。", false); return; }}
    const provider = (id("provider_select")?.value || "chatgpt").trim();
    if (provider === "local") {{ setLlmMsg(UI_LANG === "en" ? "Local mode does not require API key." : "本地大模型不需要 API Key。", null); return; }}
    const label = provider === "chatgpt" ? "ChatGPT" : (provider === "deepseek" ? "DeepSeek" : (provider === "gemini" ? "Gemini" : "NVIDIA"));
    const key = window.prompt((UI_LANG === "en" ? "Enter " : "请输入 ") + label + " API Key:");
    if (!key || !key.trim()) return;
    const data = await postForm("/save_api_key", {{ provider: provider, api_key: key.trim() }});
    if (!data.ok) {{ setLlmMsg((UI_LANG === "en" ? "Save failed: " : "保存失败: ") + (data.error || "unknown"), false); return; }}
    savedKeyState.chatgpt = !!data.has_chatgpt_key;
    savedKeyState.deepseek = !!data.has_deepseek_key;
    savedKeyState.gemini = !!data.has_gemini_key;
    savedKeyState.nvidia = !!data.has_nvidia_key;
    updateApiKeyState();
    setLlmMsg(data.overwritten ? (UI_LANG === "en" ? "API key overwritten." : "API Key 已覆盖保存。") : (UI_LANG === "en" ? "API key saved." : "API Key 保存成功。"), true);
  }});

  on("provider_select", "change", updateProviderSection);
  on("chatgpt_model_select", "change", updateProviderSection);
  on("deepseek_model_select", "change", updateProviderSection);
  on("gemini_model_select", "change", updateProviderSection);
  on("nvidia_model_select", "change", updateProviderSection);
  on("local_model_select", "change", updateProviderSection);
  on("local_model_custom", "input", updateProviderSection);
  updateApiKeyState();
  updateProviderSection();
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="{ 'en' if is_en else 'zh-CN' }">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    {build_app_header_css()}
    {page_css}
  </style>
</head>
<body>
{body_html}
{page_script}
</body>
</html>
"""


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
    jump_enabled: bool = False,
    jump_mode: str = "direct",
    jump_host: str = "",
    jump_port: str = "22",
    jump_username: str = "",
    jump_password: str = "",
    smc_command: str = "smc server toc {jump_host}",
) -> str:
    ensure_task_store()
    job_id = uuid4().hex[:12]
    device_list = [x.strip() for x in str(devices or "").split(";") if x.strip()]
    custom_items = parse_ordered_items(custom_commands)
    checks_list = [x.strip() for x in list(selected or []) if str(x).strip()] + custom_items
    if TASK_STORE:
        try:
            TASK_STORE.create_task(
                job_id,
                username=username,
                devices=device_list,
                checks=checks_list,
                options={
                    "execution_mode": execution_mode,
                    "parallel_workers": parallel_workers,
                    "connect_retry": connect_retry,
                    "jump_enabled": bool(jump_enabled),
                    "jump_mode": jump_mode,
                    "jump_host": jump_host,
                    "jump_port": str(jump_port or "22"),
                    "debug_mode": bool(debug_mode),
                },
            )
        except Exception:
            pass
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
            checks_input = normalize_inline_input("\n".join(selected + custom_items))
            mode_value = jump_mode if jump_mode in {"direct", "ssh", "smc"} else "direct"
            stdin_lines = [
                username,
                password,
                map_tmp,
                device_input,
                checks_input,
                execution_mode,
                parallel_workers,
                connect_retry,
                mode_value,
            ]
            if mode_value == "ssh":
                stdin_lines.extend(
                    [
                        jump_host if jump_enabled else "",
                        str(jump_port if jump_enabled else "22"),
                        jump_username if jump_enabled else "",
                        jump_password if jump_enabled else "",
                    ]
                )
            elif mode_value == "smc":
                stdin_lines.extend(
                    [
                        jump_host if jump_enabled else "",
                        smc_command if jump_enabled else "smc server toc {jump_host}",
                    ]
                )
            stdin_lines.append("y" if debug_mode else "n")
            stdin_text = "\n".join(stdin_lines) + "\n"

            popen_kwargs = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "cwd": str(PROJECT_ROOT),
                "bufsize": 1,
                "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_kwargs["preexec_fn"] = os.setsid

            proc = subprocess.Popen([sys.executable, str(SCRIPT_PATH)], **popen_kwargs)
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
                    if TASK_STORE:
                        try:
                            TASK_STORE.update_task(
                                job_id,
                                status=JOBS[job_id]["status"],
                                exit_code=exit_code,
                                report_json=str(JOBS[job_id].get("report_json", "") or ""),
                                report_csv=str(JOBS[job_id].get("report_csv", "") or ""),
                                output_text=str(JOBS[job_id].get("output", "") or "")[-200000:],
                            )
                        except Exception:
                            pass
        except Exception as exc:
            _append(f"\n[web_runner_error] {exc}\n")
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["exit_code"] = -1
                    JOBS[job_id]["status"] = "error"
                    if TASK_STORE:
                        try:
                            TASK_STORE.update_task(
                                job_id,
                                status="error",
                                exit_code=-1,
                                output_text=str(JOBS[job_id].get("output", "") or "")[-200000:],
                            )
                        except Exception:
                            pass
        finally:
            if map_tmp and os.path.exists(map_tmp):
                os.remove(map_tmp)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return job_id


class Handler(BaseHTTPRequestHandler):
    def _parse_cookie(self) -> Dict[str, str]:
        raw = self.headers.get("Cookie", "") or ""
        out: Dict[str, str] = {}
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def _current_user(self) -> Dict:
        return {"username": "", "role": "user", "can_modify": True}

    def _redirect(self, path: str, set_cookie: str = "") -> None:
        self.send_response(303)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Location", path)
        self.end_headers()

    def _require_login(self, lang: str) -> Dict:
        return self._current_user()

    def _require_admin(self, lang: str) -> Dict:
        self._redirect(with_lang("/", lang))
        return {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        lang = normalize_lang((query.get("lang", [""])[0] or "").strip())
        if parsed.path == "/logout":
            self._redirect(with_lang("/", lang))
            return
        if parsed.path in {"/login", "/admin"}:
            self._redirect(with_lang("/", lang))
            return

        user = self._current_user()
        if parsed.path == "/":
            templates = merged_check_template_catalog()
            default_template = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")
            default_checks = (templates.get(default_template, {}).get("checks", DEFAULT_CHECKS) if isinstance(templates.get(default_template, {}), dict) else DEFAULT_CHECKS)[:3]
            self._respond_html(
                build_html(
                    default_form_values(),
                    default_checks,
                    "",
                    "",
                    lang=lang,
                    selected_template=default_template,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if parsed.path == "/tasks":
            self._respond_html(
                build_tasks_page(
                    lang=lang,
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if parsed.path.startswith("/tasks/"):
            task_id = (parsed.path.split("/tasks/", 1)[1] or "").strip()
            if not task_id:
                self.send_error(400, "Missing task id")
                return
            self._respond_html(
                build_job_html(
                    task_id,
                    history_mode=False,
                    lang=lang,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if parsed.path == "/ai/settings":
            self._respond_html(
                build_ai_settings_page(
                    lang=lang,
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                    can_modify=user_can_modify(user),
                )
            )
            return
        if parsed.path == "/guide":
            self._respond_html(build_guide_index_html(lang=lang))
            return
        if parsed.path == "/guide/design":
            self._respond_html(build_guide_html(lang=lang))
            return
        if parsed.path == "/guide/design-zh":
            self._respond_html(build_guide_html(lang="zh"))
            return
        if parsed.path == "/guide/design-en":
            self._respond_html(build_guide_html(lang="en"))
            return
        if parsed.path == "/guide/user":
            self._respond_html(build_user_guide_html(lang=lang))
            return
        if parsed.path == "/guide/user-zh":
            self._respond_html(build_user_guide_html(lang="zh"))
            return
        if parsed.path == "/guide/user-en":
            self._respond_html(build_user_guide_html(lang="en"))
            return
        if parsed.path == "/job":
            history_mode = (query.get("history", [""])[0] or "").strip() in {"1", "true", "yes", "on"}
            job_id = (query.get("id", [""])[0] or "").strip()
            if not job_id and not history_mode:
                self.send_error(400, "Missing job id")
                return
            self._respond_html(
                build_job_html(
                    job_id,
                    history_mode=history_mode,
                    lang=lang,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if parsed.path == "/job_status":
            self._serve_job_status(parsed.query, lang=lang)
            return
        if parsed.path == "/analysis_status":
            self._serve_analysis_status(parsed.query)
            return
        if parsed.path == "/job_active_analysis":
            self._serve_job_active_analysis(parsed.query)
            return
        if parsed.path == "/download":
            self._serve_download(parsed.query)
            return
        if parsed.path == "/download_ai":
            self._serve_ai_download(parsed.query)
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

    def _serve_ai_download(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        name = (query.get("name", [""])[0] or "").strip()
        if not is_safe_ai_report_name(name):
            self.send_error(400, "Invalid report name")
            return
        target = AI_REPORT_DIR / name
        if not target.is_file():
            self.send_error(404, "Not Found")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _serve_job_status(self, raw_query: str, lang: str = "zh") -> None:
        query = parse_qs(raw_query)
        job_id = (query.get("id", [""])[0] or "").strip()
        if not job_id:
            self.send_error(400, "Missing job id")
            return

        ensure_task_store()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                row = TASK_STORE.get_task(job_id) if TASK_STORE else None
                if row:
                    payload = {
                        "status": row.get("status", "unknown"),
                        "exit_code": row.get("exit_code"),
                        "output": row.get("output_text", "") or "",
                        "report_json": row.get("report_json", "") or "",
                        "report_csv": row.get("report_csv", "") or "",
                    }
                else:
                    payload = {
                        "status": "error",
                        "exit_code": -1,
                        "output": "Task not found or expired" if normalize_lang(lang) == "en" else "任务不存在或已过期",
                    }
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

    def _serve_analysis_status(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        analysis_id = (query.get("id", [""])[0] or "").strip()
        if not analysis_id:
            self.send_error(400, "Missing analysis id")
            return
        ensure_analysis_services()
        payload = ANALYSIS_STATUS_STORE.get_response_payload(analysis_id) if ANALYSIS_STATUS_STORE else {"ok": False, "error": "analysis status service unavailable"}
        self._respond_json(payload)

    def _serve_job_active_analysis(self, raw_query: str) -> None:
        query = parse_qs(raw_query)
        job_id = (query.get("id", [""])[0] or "").strip()
        if not job_id:
            self._respond_json({"ok": False, "error": "Missing job id"}, status=400)
            return
        ensure_analysis_services()
        if not ANALYSIS_STATUS_STORE:
            self._respond_json({"ok": False, "error": "analysis status service unavailable"}, status=500)
            return
        analysis_id = ANALYSIS_STATUS_STORE.find_analysis_id_by_job(job_id, running_only=True)
        if not analysis_id:
            self._respond_json({"ok": True, "active": False})
            return
        payload = ANALYSIS_STATUS_STORE.get_response_payload(analysis_id)
        payload["analysis_id"] = analysis_id
        payload["active"] = bool(payload.get("ok")) and str(payload.get("status", "")) == "running"
        self._respond_json(payload)

    def _handle_analysis_stop(self, form: cgi.FieldStorage) -> None:
        analysis_id = (form.getvalue("analysis_id") or "").strip()
        job_id = (form.getvalue("job_id") or "").strip()
        ensure_analysis_services()
        if not ANALYSIS_STATUS_STORE:
            self._respond_json({"ok": False, "error": "analysis status service unavailable"}, status=500)
            return
        if not analysis_id and job_id:
            analysis_id = ANALYSIS_STATUS_STORE.find_analysis_id_by_job(job_id, running_only=True)
        if not analysis_id:
            self._respond_json({"ok": False, "error": "No running analysis task"}, status=404)
            return
        stopped = ANALYSIS_STATUS_STORE.request_cancel(analysis_id)
        if not stopped:
            self._respond_json({"ok": False, "error": "Analysis task is not running", "analysis_id": analysis_id}, status=409)
            return
        self._respond_json(
            {
                "ok": True,
                "analysis_id": analysis_id,
                "message": "已请求停止分析，等待当前调用结束...",
            }
        )

    def _respond_json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_analysis_input(self, job: Dict) -> str:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            raise RuntimeError("analysis service unavailable")
        return ANALYSIS_SERVICE.build_analysis_input(job)

    def _llm_model_used(self, llm: Dict[str, str]) -> str:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            return ""
        return ANALYSIS_SERVICE.model_used(llm)

    def _run_llm_analysis(self, llm: Dict[str, str], report_text: str) -> Tuple[str, Dict]:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            raise RuntimeError("analysis service unavailable")
        return ANALYSIS_SERVICE.run_llm_analysis(llm, report_text)

    def _load_job_report_json(self, job: Dict) -> Dict:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            raise RuntimeError("analysis service unavailable")
        return ANALYSIS_SERVICE.load_job_report_json(job)

    def _start_batched_analysis(
        self,
        job_id: str,
        llm: Dict[str, str],
        batch_size: int = 5,
        analysis_parallelism: int = 2,
        analysis_retries: int = 1,
        report_data_override: Optional[Dict] = None,
        large_report_mode: bool = False,
        large_report_chunk_items: int = 4,
    ) -> str:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            raise RuntimeError("analysis service unavailable")
        return ANALYSIS_SERVICE.start_batched_analysis(
            job_id=job_id,
            llm=llm,
            batch_size=batch_size,
            analysis_parallelism=analysis_parallelism,
            analysis_retries=analysis_retries,
            report_data_override=report_data_override,
            large_report_mode=large_report_mode,
            large_report_chunk_items=large_report_chunk_items,
        )

    def _handle_save_gpt_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = "chatgpt"
        chatgpt_model = (form.getvalue("chatgpt_model") or DEFAULT_GPT_MODEL).strip()
        local_base_url = (form.getvalue("local_base_url") or DEFAULT_LOCAL_BASE_URL).strip()
        local_model = (form.getvalue("local_model") or DEFAULT_LOCAL_MODEL).strip()
        deepseek_model = (form.getvalue("deepseek_model") or DEFAULT_DEEPSEEK_MODEL).strip()
        gemini_model = (form.getvalue("gemini_model") or DEFAULT_GEMINI_MODEL).strip()
        nvidia_model = (form.getvalue("nvidia_model") or DEFAULT_NVIDIA_MODEL).strip()
        selected_system_prompt = (form.getvalue("selected_system_prompt") or "").strip()
        selected_task_prompt = (form.getvalue("selected_task_prompt") or "").strip()
        if provider == "chatgpt" and not chatgpt_model:
            self._respond_json({"ok": False, "error": "chatgpt_model required"}, status=400)
            return
        if provider == "local" and (not local_base_url or not local_model):
            self._respond_json({"ok": False, "error": "local_base_url/local_model required"}, status=400)
            return
        if provider == "deepseek" and not deepseek_model:
            self._respond_json({"ok": False, "error": "deepseek_model required"}, status=400)
            return
        if provider == "gemini" and not gemini_model:
            self._respond_json({"ok": False, "error": "gemini_model required"}, status=400)
            return
        if provider == "nvidia" and not nvidia_model:
            self._respond_json({"ok": False, "error": "nvidia_model required"}, status=400)
            return
        cfg = load_gpt_config()
        cfg["provider"] = provider
        cfg["chatgpt_model"] = chatgpt_model
        cfg["local_base_url"] = local_base_url
        cfg["local_model"] = local_model
        cfg["deepseek_model"] = deepseek_model
        cfg["gemini_model"] = gemini_model
        cfg["nvidia_model"] = nvidia_model
        cfg["selected_system_prompt"] = selected_system_prompt
        cfg["selected_task_prompt"] = selected_task_prompt
        save_gpt_config(cfg)
        self._respond_json({"ok": True})

    def _handle_save_api_key(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        api_key = (form.getvalue("api_key") or "").strip()
        if provider not in {"chatgpt", "deepseek", "gemini", "nvidia"}:
            self._respond_json({"ok": False, "error": "provider must be chatgpt/deepseek/gemini/nvidia"}, status=400)
            return
        if not api_key:
            self._respond_json({"ok": False, "error": "API Key is empty"}, status=400)
            return
        cfg = load_gpt_config()
        key_field_map = {
            "chatgpt": "chatgpt_api_key",
            "deepseek": "deepseek_api_key",
            "gemini": "gemini_api_key",
            "nvidia": "nvidia_api_key",
        }
        key_field = key_field_map[provider]
        overwritten = bool(str(cfg.get(key_field, "") or "").strip())
        cfg[key_field] = api_key
        save_gpt_config(cfg)
        self._respond_json(
            {
                "ok": True,
                "overwritten": overwritten,
                "has_chatgpt_key": bool(str(cfg.get("chatgpt_api_key", "") or "").strip()),
                "has_deepseek_key": bool(str(cfg.get("deepseek_api_key", "") or "").strip()),
                "has_gemini_key": bool(str(cfg.get("gemini_api_key", "") or "").strip()),
                "has_nvidia_key": bool(str(cfg.get("nvidia_api_key", "") or "").strip()),
            }
        )

    def _handle_import_prompt(self, form: cgi.FieldStorage) -> None:
        upload = form["prompt_file"] if "prompt_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Prompt file is required"}, status=400)
            return
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
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
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        if not write_prompt_file(target_dir, prompt_name, text):
            self._respond_json({"ok": False, "error": "提示词模板保存失败"}, status=500)
            return
        cfg["custom_prompts"] = {}
        save_gpt_config(cfg)
        prompts = merged_system_prompt_catalog() if prompt_kind == "system" else merged_task_prompt_catalog()
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": prompt_name,
            }
        )

    def _handle_update_prompt(self, form: cgi.FieldStorage) -> None:
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
        raw_name = (form.getvalue("prompt_name") or "").strip()
        prompt_name = sanitize_prompt_name(raw_name)
        prompt_text = (form.getvalue("prompt_text") or "").strip()
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        if not prompt_text:
            self._respond_json({"ok": False, "error": "Prompt text is empty"}, status=400)
            return
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        if not write_prompt_file(target_dir, prompt_name, prompt_text):
            self._respond_json({"ok": False, "error": "提示词保存失败"}, status=500)
            return
        prompts = prompt_catalog_by_kind(prompt_kind)
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": prompt_name,
            }
        )

    def _handle_delete_prompt(self, form: cgi.FieldStorage) -> None:
        prompt_kind = (form.getvalue("prompt_kind") or "task").strip().lower()
        if prompt_kind not in {"task", "system"}:
            prompt_kind = "task"
        raw_name = (form.getvalue("prompt_name") or "").strip()
        prompt_name = sanitize_prompt_name(raw_name)
        if not prompt_name:
            self._respond_json({"ok": False, "error": "Prompt name is empty"}, status=400)
            return
        target_dir = SYSTEM_CUSTOM_PROMPTS_DIR if prompt_kind == "system" else TASK_CUSTOM_PROMPTS_DIR
        target_file = target_dir / prompt_file_name(prompt_name)
        if not target_file.is_file():
            self._respond_json({"ok": False, "error": "仅可删除自定义模板，默认模板不可直接删除"}, status=400)
            return
        try:
            target_file.unlink()
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"删除失败: {exc}"}, status=500)
            return
        prompts = prompt_catalog_by_kind(prompt_kind)
        fallback_selected = "网络工程师-严格模式" if prompt_kind == "system" else ""
        self._respond_json(
            {
                "ok": True,
                "prompt_kind": prompt_kind,
                "prompts": prompts,
                "selected_prompt": fallback_selected if prompt_name not in prompts else prompt_name,
            }
        )

    def _handle_import_check_template(self, form: cgi.FieldStorage) -> None:
        upload = form["template_file"] if "template_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "Template file is required"}, status=400)
            return
        raw_name = (form.getvalue("template_name") or "").strip()
        if not raw_name:
            raw_name = Path(str(upload.filename)).stem
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可覆盖"}, status=400)
            return
        raw = upload.file.read()
        if not raw:
            self._respond_json({"ok": False, "error": "Template file is empty"}, status=400)
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="ignore")
        checks, commands = parse_check_template_text(text)
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "No valid checks in file"}, status=400)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_update_check_template(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可修改"}, status=400)
            return
        template_text = (form.getvalue("template_text") or "").strip()
        checks, commands = parse_check_template_text(template_text)
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "Template text is empty"}, status=400)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_save_check_template_from_selection(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可覆盖"}, status=400)
            return
        allow_overwrite = (form.getvalue("allow_overwrite") or "").strip().lower() in {"1", "true", "y", "yes", "on"}
        checks = parse_check_items(form.getvalue("checks_text") or "")
        commands = parse_ordered_items(form.getvalue("commands_text") or "")
        if not checks and not commands:
            self._respond_json({"ok": False, "error": "Template content is empty"}, status=400)
            return
        target_file = CHECK_CUSTOM_TEMPLATES_DIR / check_template_file_name(template_name)
        if target_file.is_file() and not allow_overwrite:
            self._respond_json({"ok": False, "error": "template_exists"}, status=409)
            return
        if not write_check_template_file(CHECK_CUSTOM_TEMPLATES_DIR, template_name, checks, commands):
            self._respond_json({"ok": False, "error": "检查项模板保存失败"}, status=500)
            return
        templates = merged_check_template_catalog()
        self._respond_json({"ok": True, "templates": templates, "selected_template": template_name})

    def _handle_delete_check_template(self, form: cgi.FieldStorage) -> None:
        raw_name = (form.getvalue("template_name") or "").strip()
        template_name = sanitize_prompt_name(raw_name)
        if not template_name:
            self._respond_json({"ok": False, "error": "Template name is empty"}, status=400)
            return
        if template_name == DEFAULT_CHECK_TEMPLATE_NAME:
            self._respond_json({"ok": False, "error": "默认全量模板不可删除"}, status=400)
            return
        target = CHECK_CUSTOM_TEMPLATES_DIR / check_template_file_name(template_name)
        if not target.is_file():
            self._respond_json({"ok": False, "error": "仅可删除自定义模板"}, status=400)
            return
        try:
            target.unlink()
        except Exception as exc:
            self._respond_json({"ok": False, "error": f"删除失败: {exc}"}, status=500)
            return
        templates = merged_check_template_catalog()
        fallback = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")
        self._respond_json({"ok": True, "templates": templates, "selected_template": fallback})

    def _handle_test_llm(self, form: cgi.FieldStorage) -> None:
        provider = (form.getvalue("provider") or "").strip().lower()
        local_base_url = (form.getvalue("local_base_url") or "").strip()
        deepseek_model = (form.getvalue("deepseek_model") or "").strip()
        gemini_model = (form.getvalue("gemini_model") or "").strip()
        nvidia_model = (form.getvalue("nvidia_model") or "").strip()
        cfg = load_gpt_config()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
                provider = "chatgpt"

        try:
            if provider == "local":
                if not local_base_url:
                    local_base_url = str(cfg.get("local_base_url", DEFAULT_LOCAL_BASE_URL) or DEFAULT_LOCAL_BASE_URL).strip()
                msg = test_local_lmstudio_connection(local_base_url)
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: N/A（本地模型）",
                        "provider_used": "local",
                        "token_balance_status": "n/a",
                        "token_balance_message": "N/A（本地模型）",
                    }
                )
                return

            if provider == "deepseek":
                api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "DeepSeek API Key not set"}, status=400)
                    return
                msg = test_deepseek_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="deepseek",
                    api_key=api_key,
                    model=(deepseek_model or str(cfg.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL) or DEFAULT_DEEPSEEK_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "deepseek",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            if provider == "gemini":
                api_key = str(cfg.get("gemini_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "Gemini API Key not set"}, status=400)
                    return
                msg = test_gemini_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="gemini",
                    api_key=api_key,
                    model=(gemini_model or str(cfg.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "gemini",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            if provider == "nvidia":
                api_key = str(cfg.get("nvidia_api_key", "") or "").strip()
                if not api_key:
                    self._respond_json({"ok": False, "error": "NVIDIA API Key not set"}, status=400)
                    return
                msg = test_nvidia_connection(api_key)
                bal_state, bal_msg = self._probe_cloud_token_balance(
                    provider="nvidia",
                    api_key=api_key,
                    model=(nvidia_model or str(cfg.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL).strip()),
                )
                self._respond_json(
                    {
                        "ok": True,
                        "message": f"{msg} | Token余额: {bal_msg}",
                        "provider_used": "nvidia",
                        "token_balance_status": bal_state,
                        "token_balance_message": bal_msg,
                    }
                )
                return

            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
            if not api_key:
                self._respond_json({"ok": False, "error": "ChatGPT API Key not set"}, status=400)
                return
            msg = test_openai_connection(api_key)
            bal_state, bal_msg = self._probe_cloud_token_balance(
                provider="chatgpt",
                api_key=api_key,
                model=str(cfg.get("chatgpt_model", DEFAULT_GPT_MODEL) or DEFAULT_GPT_MODEL).strip(),
            )
            self._respond_json(
                {
                    "ok": True,
                    "message": f"{msg} | Token余额: {bal_msg}",
                    "provider_used": "chatgpt",
                    "token_balance_status": bal_state,
                    "token_balance_message": bal_msg,
                }
            )
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)

    def _probe_cloud_token_balance(self, provider: str, api_key: str, model: str) -> Tuple[str, str]:
        ensure_analysis_services()
        if not ANALYSIS_SERVICE:
            return "unknown", "未知（analysis service unavailable）"
        return ANALYSIS_SERVICE.probe_cloud_token_balance(provider, api_key, model)

    def _resolve_llm_inputs_from_form(self, form: cgi.FieldStorage) -> Dict[str, str]:
        cfg = load_gpt_config()
        provider = (form.getvalue("provider") or "").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
            provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
            if provider not in {"chatgpt", "local", "deepseek", "gemini", "nvidia"}:
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
        gemini_model = (form.getvalue("gemini_model") or "").strip() or str(
            cfg.get("gemini_model", DEFAULT_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL
        ).strip()
        nvidia_model = (form.getvalue("nvidia_model") or "").strip() or str(
            cfg.get("nvidia_model", DEFAULT_NVIDIA_MODEL) or DEFAULT_NVIDIA_MODEL
        ).strip()
        if provider == "deepseek":
            api_key = str(cfg.get("deepseek_api_key", "") or "").strip()
        elif provider == "gemini":
            api_key = str(cfg.get("gemini_api_key", "") or "").strip()
        elif provider == "nvidia":
            api_key = str(cfg.get("nvidia_api_key", "") or "").strip()
        elif provider == "chatgpt":
            api_key = str(cfg.get("chatgpt_api_key", "") or "").strip()
        else:
            api_key = ""
        system_prompt_key = (form.getvalue("system_prompt_key") or "").strip()
        task_prompt_key = (form.getvalue("prompt_key") or "").strip()
        system_prompt_extra = (form.getvalue("system_prompt_extra") or "").strip()
        task_prompt_extra = (form.getvalue("custom_prompt") or "").strip()

        system_prompts = merged_system_prompt_catalog()
        task_prompts = merged_task_prompt_catalog()

        base_system_prompt = (
            system_prompts.get(system_prompt_key, "")
            if system_prompt_key
            else system_prompts.get("网络工程师-严格模式", "")
        )
        base_task_prompt = task_prompts.get(task_prompt_key, "") if task_prompt_key else ""

        if base_system_prompt and system_prompt_extra:
            system_prompt_text = f"{base_system_prompt}\n\n[Extra System Constraints]\n{system_prompt_extra}"
            system_prompt_source = f"system_template+extra:{system_prompt_key or '网络工程师-严格模式'}"
        elif base_system_prompt:
            system_prompt_text = base_system_prompt
            system_prompt_source = f"system_template:{system_prompt_key or '网络工程师-严格模式'}"
        elif system_prompt_extra:
            system_prompt_text = system_prompt_extra
            system_prompt_source = "system_extra_only"
        else:
            system_prompt_text = DEFAULT_SYSTEM_PROMPTS["网络工程师-严格模式"]
            system_prompt_source = "system_default:网络工程师-严格模式"

        if base_task_prompt and task_prompt_extra:
            task_prompt_text = f"{base_task_prompt}\n\n[Extra Task Requirements]\n{task_prompt_extra}"
            task_prompt_source = f"task_template+extra:{task_prompt_key}"
        elif base_task_prompt:
            task_prompt_text = base_task_prompt
            task_prompt_source = f"task_template:{task_prompt_key}"
        elif task_prompt_extra:
            task_prompt_text = task_prompt_extra
            task_prompt_source = "task_extra_only"
        else:
            task_prompt_text = DEFAULT_TASK_PROMPTS["基础巡检诊断"]
            task_prompt_source = "task_default:基础巡检诊断"
        return {
            "provider": provider,
            "api_key": api_key,
            "chatgpt_model": chatgpt_model,
            "local_base_url": local_base_url,
            "local_model": local_model,
            "deepseek_model": deepseek_model,
            "gemini_model": gemini_model,
            "nvidia_model": nvidia_model,
            "system_prompt_text": system_prompt_text,
            "task_prompt_text": task_prompt_text,
            "system_prompt_key": system_prompt_key or "网络工程师-严格模式",
            "task_prompt_key": task_prompt_key,
            "prompt_source": f"{system_prompt_source}; {task_prompt_source}",
        }

    def _parse_analysis_options(self, form: cgi.FieldStorage) -> Dict[str, int]:
        batched_analysis = (form.getvalue("batched_analysis") or "").strip().lower() in {"1", "true", "on", "yes"}
        large_report_mode = (form.getvalue("large_report_mode") or "").strip().lower() in {"1", "true", "on", "yes"}
        analysis_parallelism_raw = (form.getvalue("analysis_parallelism") or "2").strip()
        analysis_retries_raw = (form.getvalue("analysis_retries") or "1").strip()
        large_report_chunk_items_raw = (form.getvalue("large_report_chunk_items") or "4").strip()
        try:
            analysis_parallelism = max(1, min(8, int(analysis_parallelism_raw or "2")))
        except ValueError:
            analysis_parallelism = 2
        try:
            analysis_retries = max(0, min(3, int(analysis_retries_raw or "1")))
        except ValueError:
            analysis_retries = 1
        try:
            large_report_chunk_items = max(1, min(20, int(large_report_chunk_items_raw or "4")))
        except ValueError:
            large_report_chunk_items = 4
        if large_report_mode and not batched_analysis:
            batched_analysis = True
        batch_size = max(1, min(50, analysis_parallelism))
        return {
            "batched_analysis": 1 if batched_analysis else 0,
            "large_report_mode": 1 if large_report_mode else 0,
            "analysis_parallelism": analysis_parallelism,
            "analysis_retries": analysis_retries,
            "large_report_chunk_items": large_report_chunk_items,
            "batch_size": batch_size,
        }

    def _precheck_report_data_from_job(self, job_id: str) -> Dict:
        job = self._resolve_job_for_analysis(job_id)
        if not job:
            raise RuntimeError("job not found")
        return self._load_job_report_json(job)

    def _resolve_job_for_analysis(self, job_id: str) -> Optional[Dict]:
        task_id = str(job_id or "").strip()
        if not task_id:
            return None
        with JOBS_LOCK:
            job = JOBS.get(task_id)
        if isinstance(job, dict):
            return dict(job)
        ensure_task_store()
        row = TASK_STORE.get_task(task_id) if TASK_STORE else None
        if not row:
            return None
        return {
            "status": str(row.get("status", "unknown") or "unknown"),
            "exit_code": row.get("exit_code"),
            "output": str(row.get("output_text", "") or ""),
            "report_json": str(row.get("report_json", "") or ""),
            "report_csv": str(row.get("report_csv", "") or ""),
        }

    def _handle_analysis_precheck(self, form: cgi.FieldStorage) -> None:
        llm = self._resolve_llm_inputs_from_form(form)
        opts = self._parse_analysis_options(form)
        source = (form.getvalue("source") or "").strip().lower()
        report_data: Dict = {}
        source_desc = ""

        if source == "history":
            upload = form["report_file"] if "report_file" in form else None
            if upload is None or not getattr(upload, "filename", ""):
                self._respond_json({"ok": False, "error": "report_file is required for history precheck"}, status=400)
                return
            try:
                _filename, raw = read_uploaded_report_raw(upload)
                maybe = json.loads(decode_best_effort_text(raw))
                if not isinstance(maybe, dict):
                    raise RuntimeError("invalid JSON root")
                report_data = maybe
                source_desc = "history_json"
            except Exception as exc:
                self._respond_json({"ok": False, "error": f"历史报告预估仅支持结构化 JSON：{exc}"}, status=400)
                return
        else:
            job_id = (form.getvalue("job_id") or "").strip()
            if not job_id:
                self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
                return
            try:
                report_data = self._precheck_report_data_from_job(job_id)
                source_desc = "current_job_json"
            except Exception as exc:
                self._respond_json({"ok": False, "error": str(exc)}, status=400)
                return

        estimation = analysis_guard.estimate_analysis_plan(
            report_data=report_data,
            provider=llm.get("provider", "chatgpt"),
            batched=bool(opts["batched_analysis"]),
            parallelism=int(opts["analysis_parallelism"]),
            retries=int(opts["analysis_retries"]),
            large_report_mode=bool(opts["large_report_mode"]),
            large_report_chunk_items=int(opts["large_report_chunk_items"]),
            system_prompt_text=llm.get("system_prompt_text", ""),
            task_prompt_text=llm.get("task_prompt_text", ""),
        )
        self._respond_json(
            {
                "ok": True,
                "source": source_desc,
                "provider": llm.get("provider", "chatgpt"),
                "model_used": self._llm_model_used(llm),
                "estimation": estimation,
            }
        )

    def _handle_analyze_job(self, form: cgi.FieldStorage) -> None:
        job_id = (form.getvalue("job_id") or "").strip()
        llm = self._resolve_llm_inputs_from_form(form)
        opts = self._parse_analysis_options(form)
        batched_analysis = bool(opts["batched_analysis"])
        large_report_mode = bool(opts["large_report_mode"])
        analysis_parallelism = int(opts["analysis_parallelism"])
        analysis_retries = int(opts["analysis_retries"])
        large_report_chunk_items = int(opts["large_report_chunk_items"])
        batch_size = int(opts["batch_size"])
        cfg = load_gpt_config()
        cfg["selected_system_prompt"] = llm.get("system_prompt_key", "")
        cfg["selected_task_prompt"] = llm.get("task_prompt_key", "")
        save_gpt_config(cfg)

        if not job_id:
            self._respond_json({"ok": False, "error": "job_id is required"}, status=400)
            return
        job = self._resolve_job_for_analysis(job_id)
        if not job:
            self._respond_json({"ok": False, "error": "job not found"}, status=404)
            return

        if batched_analysis:
            report_data_override = None
            with JOBS_LOCK:
                in_memory_job = JOBS.get(job_id)
            if not in_memory_job:
                try:
                    report_data_override = self._load_job_report_json(job)
                except Exception as exc:
                    self._respond_json({"ok": False, "error": str(exc)}, status=400)
                    return
            analysis_id = self._start_batched_analysis(
                job_id,
                llm,
                batch_size=batch_size,
                analysis_parallelism=analysis_parallelism,
                analysis_retries=analysis_retries,
                report_data_override=report_data_override,
                large_report_mode=large_report_mode,
                large_report_chunk_items=large_report_chunk_items,
            )
            mode_desc = "分片模式" if large_report_mode else "标准分批模式"
            self._respond_json(
                {
                    "ok": True,
                    "async": True,
                    "analysis_id": analysis_id,
                    "message": (
                        f"已启动分批分析：{mode_desc}，AI并发={analysis_parallelism}，每设备分片数={large_report_chunk_items}，"
                        f"每轮设备数={batch_size}，"
                        f"重试={analysis_retries}"
                    ),
                }
            )
            return

        try:
            analysis_input = self._build_analysis_input(job)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=400)
            return
        started_at = time.time()
        try:
            analysis, usage = self._run_llm_analysis(llm, analysis_input)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        token_stats = add_token_usage(llm["provider"], int(usage.get("total_tokens", 0)))
        saved_name = save_ai_analysis_report(
            job_id,
            analysis_text=analysis,
            provider=llm["provider"],
            model=self._llm_model_used(llm),
            prompt_source=llm.get("prompt_source", ""),
            duration_seconds=max(0.0, time.time() - started_at),
            token_usage=usage,
            token_total=int(token_stats.get("total_tokens", 0)),
            source="task",
            status="done",
            error="",
        )
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": self._llm_model_used(llm),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
                "token_usage": usage,
                "token_total": int(token_stats.get("total_tokens", 0)),
                "duration_seconds": max(0.0, time.time() - started_at),
                "analysis_report_name": saved_name,
            }
        )

    def _handle_analyze_history_report(self, form: cgi.FieldStorage) -> None:
        upload = form["report_file"] if "report_file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            self._respond_json({"ok": False, "error": "report_file is required"}, status=400)
            return
        opts = self._parse_analysis_options(form)
        batched_analysis = bool(opts["batched_analysis"])
        large_report_mode = bool(opts["large_report_mode"])
        analysis_parallelism = int(opts["analysis_parallelism"])
        analysis_retries = int(opts["analysis_retries"])
        large_report_chunk_items = int(opts["large_report_chunk_items"])
        batch_size = int(opts["batch_size"])

        filename = ""
        raw = b""
        try:
            filename, raw = read_uploaded_report_raw(upload)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=400)
            return
        text = decode_best_effort_text(raw)
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
        ratio = (printable / len(text)) if text else 0.0
        if not text or ratio < 0.65:
            b64 = base64.b64encode(raw).decode("ascii")[:200000]
            report_text = f"文件名: {filename}\n文件内容可能是二进制格式，以下为 base64 片段（已截断）：\n{b64}"
        else:
            report_text = f"文件名: {filename}\n文件文本内容（可能已截断）：\n{text[:200000]}"

        llm = self._resolve_llm_inputs_from_form(form)
        cfg = load_gpt_config()
        cfg["selected_system_prompt"] = llm.get("system_prompt_key", "")
        cfg["selected_task_prompt"] = llm.get("task_prompt_key", "")
        save_gpt_config(cfg)

        if batched_analysis:
            try:
                raw_text = decode_best_effort_text(raw)
                report_data = json.loads(raw_text)
                devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
                if not isinstance(devices, list) or not devices:
                    raise RuntimeError("历史 JSON 报告中未找到 devices 列表")
                analysis_id = self._start_batched_analysis(
                    job_id="history_upload",
                    llm=llm,
                    batch_size=batch_size,
                    analysis_parallelism=analysis_parallelism,
                    analysis_retries=analysis_retries,
                    report_data_override=report_data,
                    large_report_mode=large_report_mode,
                    large_report_chunk_items=large_report_chunk_items,
                )
                mode_desc = "分片模式" if large_report_mode else "标准分批模式"
                self._respond_json(
                    {
                        "ok": True,
                        "async": True,
                        "analysis_id": analysis_id,
                        "message": (
                            f"历史 JSON 分批分析已启动：{mode_desc}，AI并发={analysis_parallelism}，"
                            f"每设备分片数={large_report_chunk_items}，每轮设备数={batch_size}，重试={analysis_retries}"
                        ),
                    }
                )
                return
            except Exception as exc:
                self._respond_json(
                    {
                        "ok": False,
                        "error": f"历史报告分批分析仅支持结构化 JSON 报告（含 devices），当前不满足: {exc}",
                    },
                    status=400,
                )
                return

        try:
            maybe_json = json.loads(decode_best_effort_text(raw))
            if isinstance(maybe_json, dict) and isinstance(maybe_json.get("devices", None), list):
                report_text = analysis_pipeline.build_whole_report_analysis_input(
                    maybe_json,
                    force_full=False,
                )
        except Exception:
            pass

        started_at = time.time()
        try:
            analysis, usage = self._run_llm_analysis(llm, report_text)
        except Exception as exc:
            self._respond_json({"ok": False, "error": str(exc)}, status=500)
            return
        token_stats = add_token_usage(llm["provider"], int(usage.get("total_tokens", 0)))
        saved_name = save_ai_analysis_report(
            "history",
            analysis_text=analysis,
            provider=llm["provider"],
            model=self._llm_model_used(llm),
            prompt_source=llm.get("prompt_source", ""),
            duration_seconds=max(0.0, time.time() - started_at),
            token_usage=usage,
            token_total=int(token_stats.get("total_tokens", 0)),
            source="history",
            status="done",
            error="",
        )
        self._respond_json(
            {
                "ok": True,
                "analysis": analysis,
                "provider_used": llm["provider"],
                "model_used": self._llm_model_used(llm),
                "local_base_url": llm["local_base_url"] if llm["provider"] == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
                "token_usage": usage,
                "token_total": int(token_stats.get("total_tokens", 0)),
                "duration_seconds": max(0.0, time.time() - started_at),
                "analysis_report_name": saved_name,
            }
        )

    def do_POST(self) -> None:
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ)
        if self.path in {"/login", "/admin/create_role", "/admin/create_user"}:
            self.send_error(404, "Not Found")
            return

        lang = normalize_lang((form.getvalue("lang") or "zh").strip())
        user = self._current_user()

        if self.path == "/save_gpt_key":
            self._handle_save_gpt_key(form)
            return
        if self.path == "/import_prompt":
            self._handle_import_prompt(form)
            return
        if self.path == "/update_prompt":
            self._handle_update_prompt(form)
            return
        if self.path == "/delete_prompt":
            self._handle_delete_prompt(form)
            return
        if self.path == "/import_check_template":
            self._handle_import_check_template(form)
            return
        if self.path == "/update_check_template":
            self._handle_update_check_template(form)
            return
        if self.path == "/delete_check_template":
            self._handle_delete_check_template(form)
            return
        if self.path == "/save_check_template_from_selection":
            self._handle_save_check_template_from_selection(form)
            return
        if self.path == "/save_api_key":
            self._handle_save_api_key(form)
            return
        if self.path == "/test_llm":
            self._handle_test_llm(form)
            return
        if self.path == "/analysis_precheck":
            self._handle_analysis_precheck(form)
            return
        if self.path == "/analysis_stop":
            self._handle_analysis_stop(form)
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
        lang = normalize_lang((form.getvalue("lang") or "zh").strip())
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
        jump_mode = (form.getvalue("jump_mode") or "direct").strip().lower()
        if jump_mode not in {"direct", "ssh", "smc"}:
            jump_mode = "direct"
        jump_enabled = jump_mode in {"ssh", "smc"}
        jump_host = (form.getvalue("jump_host") or "").strip()
        jump_port = (form.getvalue("jump_port") or "22").strip() or "22"
        try:
            jump_port = str(max(1, int(jump_port)))
        except ValueError:
            jump_port = "22"
        jump_username = (form.getvalue("jump_username") or "").strip()
        jump_password = (form.getvalue("jump_password") or "").strip()
        smc_command = (form.getvalue("smc_command") or "smc server toc {jump_host}").strip() or "smc server toc {jump_host}"
        custom_commands = (form.getvalue("custom_commands") or "").strip()
        debug_mode = (form.getvalue("debug_mode") or "").strip() in {"1", "true", "y", "yes", "on"}
        check_template_key = (form.getvalue("check_template_key") or DEFAULT_CHECK_TEMPLATE_NAME).strip()
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
            "jump_mode": jump_mode,
            "jump_host": jump_host,
            "jump_port": jump_port,
            "jump_username": jump_username,
            "jump_password": jump_password,
            "smc_command": smc_command,
            "debug_mode": "1" if debug_mode else "",
        }
        templates = merged_check_template_catalog()
        if check_template_key not in templates:
            check_template_key = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")

        if not username or not password:
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    "ERROR: 用户名和密码不能为空",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if not devices:
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    "ERROR: 请输入设备地址或导入设备文件",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if not selected and not parse_ordered_items(custom_commands):
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    "ERROR: 请至少选择一个检查项或输入一条自定义命令",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if jump_mode == "ssh" and (not jump_host or not jump_username or not jump_password):
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    "ERROR: SSH 跳板模式时，跳板机地址/用户名/密码不能为空",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return
        if jump_mode == "smc" and (not jump_host or not smc_command):
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    "ERROR: SMC 模式时，跳板机地址和 SMC 命令模板不能为空",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )
            return

        upload = form["command_map"] if "command_map" in form else None
        try:
            data = b""
            if upload is not None and getattr(upload, "filename", ""):
                data = upload.file.read()
                if not data:
                    self._respond_html(
                        build_html(
                            values,
                            selected,
                            "",
                            "ERROR: 上传的 command_map 文件为空",
                            lang=lang,
                            selected_template=check_template_key,
                            can_modify=user_can_modify(user),
                            auth_username=str(user.get("username", "")),
                            auth_role=str(user.get("role", "user")),
                        )
                    )
                    return
            else:
                default_map = COMMAND_MAP_PATH
                if not default_map.is_file():
                    self._respond_html(
                        build_html(
                            values,
                            selected,
                            "",
                            "ERROR: 默认 config/command_map.yaml 不存在，请上传文件",
                            lang=lang,
                            selected_template=check_template_key,
                            can_modify=user_can_modify(user),
                            auth_username=str(user.get("username", "")),
                            auth_role=str(user.get("role", "user")),
                        )
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
                jump_enabled=jump_enabled,
                jump_mode=jump_mode,
                jump_host=jump_host,
                jump_port=jump_port,
                jump_username=jump_username,
                jump_password=jump_password,
                smc_command=smc_command,
            )
            self.send_response(303)
            self.send_header("Location", with_lang(f"/job?id={job_id}", lang))
            self.end_headers()
        except Exception as exc:
            self._respond_html(
                build_html(
                    values,
                    selected,
                    "",
                    f"ERROR: {exc}",
                    lang=lang,
                    selected_template=check_template_key,
                    can_modify=user_can_modify(user),
                    auth_username=str(user.get("username", "")),
                    auth_role=str(user.get("role", "user")),
                )
            )

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
    initialize_default_check_templates()
    ensure_analysis_services()
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
