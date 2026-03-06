#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from app.web_server import *  # noqa: F401,F403
from app.template_runtime import render_python_f_template

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
    def _parse_ai_report_meta(path: Path) -> Dict[str, str]:
        out = {
            "status": "-",
            "provider": "-",
            "model": "-",
            "prompt_source": "-",
            "content": "",
        }
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return out
        for line in raw.splitlines()[:40]:
            if line.startswith("- Status:"):
                out["status"] = line.split(":", 1)[1].strip() or "-"
            elif line.startswith("- Provider:"):
                out["provider"] = line.split(":", 1)[1].strip() or "-"
            elif line.startswith("- Model:"):
                out["model"] = line.split(":", 1)[1].strip() or "-"
            elif line.startswith("- Prompt Source:"):
                out["prompt_source"] = line.split(":", 1)[1].strip() or "-"
        marker = "\n## Content\n"
        idx = raw.find(marker)
        if idx >= 0:
            out["content"] = raw[idx + len(marker):].strip()
        return out

    ai_report_items = list_ai_report_files(job_id, limit=20) if (job_id and not history_mode) else []
    gpt_result_init_text = "分析结果会显示在这里。" if lang == "zh" else "Analysis result will be shown here."
    if ai_report_items:
        latest_meta = _parse_ai_report_meta(ai_report_items[0])
        latest_text = (latest_meta.get("content", "") or "").strip()
        if latest_text:
            gpt_result_init_text = latest_text
        row_html = []
        for p in ai_report_items:
            meta = _parse_ai_report_meta(p)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
            row_html.append(
                "<div class=\"ai-history-row\">"
                f"<span class=\"ai-history-file\">{html.escape(p.stem)}</span>"
                f"<span class=\"ai-history-sep\">|</span>{html.escape(mtime)}"
                f"<span class=\"ai-history-sep\">|</span>{html.escape(meta.get('status', '-') or '-')}"
                f"<span class=\"ai-history-sep\">|</span>{html.escape(meta.get('provider', '-') or '-')}"
                f"<span class=\"ai-history-sep\">|</span>{html.escape(meta.get('model', '-') or '-')}"
                f"<span class=\"ai-history-sep\">|</span>"
                f"<a href=\"{with_lang('/download_ai?name=' + p.name, lang)}\">.md</a>"
                "</div>"
            )
        ai_report_history_html = (
            "<div id=\"ai_reports\" class=\"ai-history-block\">"
            f"<div class=\"ai-history-title\">{'Analysis History' if lang == 'en' else '分析历史'}</div>"
            + "".join(row_html)
            + "</div>"
        )
    else:
        ai_report_history_html = (
            "<div id=\"ai_reports\" class=\"ai-history-block\">"
            f"<div class=\"ai-history-title\">{'Analysis History' if lang == 'en' else '分析历史'}</div>"
            f"<div class=\"ai-history-empty\">{'No history yet.' if lang == 'en' else '暂无历史记录。'}</div>"
            "</div>"
        )
    output_init_text = "请在页面底部上传历史报告文件并点击 AI 分析。" if history_mode else "正在启动任务，请稍候..."
    modify_disabled = "" if can_modify else "disabled"
    _scope = dict(globals())
    _scope.update(locals())
    _html = render_python_f_template("pages/task_detail_full.pyf.html", _scope)
    page_html = localize_html_page(_html, lang)
    return render_html_template("task_detail.html", {"CONTENT": page_html})
