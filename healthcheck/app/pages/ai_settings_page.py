#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from app.web_server import *  # noqa: F401,F403
from app.template_runtime import render_python_f_template

def build_ai_settings_page(lang: str = "zh", auth_username: str = "", auth_role: str = "user", can_modify: bool = True) -> str:
    lang = normalize_lang(lang)
    is_en = lang == "en"
    cfg = load_gpt_config()
    task_prompts = merged_task_prompt_catalog()
    system_prompts = merged_system_prompt_catalog()
    provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
    if provider not in {"chatgpt", "deepseek", "gemini", "nvidia", "local"}:
        provider = "chatgpt"
    modify_disabled = "disabled" if not can_modify else ""

    saved_system_prompt_key = str(cfg.get("selected_system_prompt", "网络工程师-严格模式") or "")
    saved_task_prompt_key = str(cfg.get("selected_task_prompt", cfg.get("selected_prompt", "")) or "")
    saved_system_prompt_extra = str(cfg.get("system_prompt_extra", "") or "")
    saved_task_prompt_extra = str(cfg.get("task_prompt_extra", "") or "")
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
        saved_system_prompt_key = system_equiv.get(saved_system_prompt_key, saved_system_prompt_key)
        saved_task_prompt_key = task_equiv.get(saved_task_prompt_key, saved_task_prompt_key)
    system_prompt_options = "".join(
        [
            f'<option value="{html.escape(name)}" {"selected" if saved_system_prompt_key == name else ""}>{html.escape(display_prompt_name(name, lang))}</option>'
            for name in system_prompts.keys()
        ]
    )
    task_prompt_options = "".join(
        [
            f'<option value="" {"selected" if not saved_task_prompt_key else ""}>{"No Template" if is_en else "不使用模板"}</option>'
        ]
        + [
            f'<option value="{html.escape(name)}" {"selected" if saved_task_prompt_key == name else ""}>{html.escape(display_prompt_name(name, lang))}</option>'
            for name in task_prompts.keys()
        ]
    )

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

    body_html = render_html_template(
        "pages/ai_settings_body.html",
        {
            "AI_ANALYSIS_SETTINGS_TITLE": "AI 分析设置" if not is_en else "AI Analysis Settings",
            "LLM_CONFIG_TITLE": "大模型配置" if not is_en else "LLM Configuration",
            "PROVIDER_LABEL": "大模型选择" if not is_en else "Provider",
            "PROVIDER_CHATGPT_SELECTED": "selected" if provider == "chatgpt" else "",
            "PROVIDER_DEEPSEEK_SELECTED": "selected" if provider == "deepseek" else "",
            "PROVIDER_GEMINI_SELECTED": "selected" if provider == "gemini" else "",
            "PROVIDER_NVIDIA_SELECTED": "selected" if provider == "nvidia" else "",
            "PROVIDER_LOCAL_SELECTED": "selected" if provider == "local" else "",
            "LOCAL_MODEL_OPTION_LABEL": "本地大模型" if not is_en else "Local Model",
            "LLM_TEST_HINT": "模型连接测试结果将在此显示。" if not is_en else "Connection test result will be shown here.",
            "API_KEY_MGMT_LABEL": "API Key 管理" if not is_en else "API Key Management",
            "MODIFY_DISABLED_ATTR": modify_disabled,
            "IMPORT_API_KEY_TEXT": "导入 API Key" if not is_en else "Import API Key",
            "MODEL_CONNECTION_TEST_TEXT": "模型连接测试" if not is_en else "Test Connection",
            "SAVE_CONFIG_TEXT": "保存模型配置" if not is_en else "Save Config",
            "CONFIG_USAGE_HINT": "用途：保存当前大模型来源、模型名、本地地址、已选提示词模板。下次打开页面自动带出。" if not is_en else "Saves provider, model, local endpoint and selected prompts for next visit.",
            "MODEL_LABEL": "模型" if not is_en else "Model",
            "CHATGPT_MODEL_OPTIONS": chatgpt_model_options,
            "CHATGPT_SETTINGS_STYLE": "style='display:none;'" if provider != "chatgpt" else "",
            "CHATGPT_CUSTOM_STYLE": "style='display:none;'" if chatgpt_in_options else "",
            "CUSTOM_CHATGPT_MODEL_LABEL": "自定义 ChatGPT 模型" if not is_en else "Custom ChatGPT Model",
            "CHATGPT_CUSTOM_VALUE": html.escape("" if chatgpt_in_options else chatgpt_model),
            "DEEPSEEK_MODEL_OPTIONS": deepseek_model_options,
            "DEEPSEEK_SETTINGS_STYLE": "style='display:none;'" if provider != "deepseek" else "",
            "DEEPSEEK_CUSTOM_STYLE": "style='display:none;'" if deepseek_in_options else "",
            "CUSTOM_DEEPSEEK_MODEL_LABEL": "自定义 DeepSeek 模型" if not is_en else "Custom DeepSeek Model",
            "DEEPSEEK_CUSTOM_VALUE": html.escape("" if deepseek_in_options else deepseek_model),
            "GEMINI_MODEL_OPTIONS": gemini_model_options,
            "GEMINI_SETTINGS_STYLE": "style='display:none;'" if provider != "gemini" else "",
            "GEMINI_CUSTOM_STYLE": "style='display:none;'" if gemini_in_options else "",
            "CUSTOM_GEMINI_MODEL_LABEL": "自定义 Gemini 模型" if not is_en else "Custom Gemini Model",
            "GEMINI_CUSTOM_VALUE": html.escape("" if gemini_in_options else gemini_model),
            "NVIDIA_MODEL_OPTIONS": nvidia_model_options,
            "NVIDIA_SETTINGS_STYLE": "style='display:none;'" if provider != "nvidia" else "",
            "NVIDIA_CUSTOM_STYLE": "style='display:none;'" if nvidia_in_options else "",
            "CUSTOM_NVIDIA_MODEL_LABEL": "自定义 NVIDIA 模型" if not is_en else "Custom NVIDIA Model",
            "NVIDIA_CUSTOM_VALUE": html.escape("" if nvidia_in_options else nvidia_model),
            "LOCAL_SETTINGS_STYLE": "style='display:none;'" if provider != "local" else "",
            "LOCAL_ENDPOINT_LABEL": "本地大模型地址" if not is_en else "Local Model Endpoint",
            "LOCAL_BASE_URL": html.escape(local_base_url),
            "LOCAL_MODEL_NAME_LABEL": "本地大模型模型" if not is_en else "Local Model Name",
            "LOCAL_MODEL_OPTIONS": local_model_options,
            "LOCAL_CUSTOM_STYLE": "style='display:none;'" if local_in_options else "",
            "CUSTOM_LOCAL_MODEL_LABEL": "自定义本地模型" if not is_en else "Custom Local Model",
            "LOCAL_CUSTOM_VALUE": html.escape("" if local_in_options else local_model),
            "PROMPT_SETTINGS_TITLE": "提示词设置" if not is_en else "Prompt Settings",
            "SYSTEM_PROMPT_TEMPLATE_LABEL": "系统提示词模板（严格约束）" if not is_en else "System Prompt Template (Strict)",
            "SYSTEM_PROMPT_OPTIONS": system_prompt_options,
            "SYSTEM_TEMPLATE_REVIEW_LABEL": "系统模板查看" if not is_en else "System Template Review",
            "REVIEW_SYSTEM_PROMPT_TEXT": "Review 系统提示词" if not is_en else "Review System Prompt",
            "TASK_PROMPT_TEMPLATE_LABEL": "任务提示词模板" if not is_en else "Task Prompt Template",
            "TASK_PROMPT_OPTIONS": task_prompt_options,
            "TEMPLATE_REVIEW_LABEL": "模板查看" if not is_en else "Template Review",
            "REVIEW_TASK_PROMPT_TEXT": "Review 任务提示词" if not is_en else "Review Task Prompt",
            "PROMPT_MGMT_OPTIONAL": "提示词管理（可选）" if not is_en else "Prompt Management (Optional)",
            "IMPORT_PROMPT_FILE_LABEL": "导入提示词文件（.txt）" if not is_en else "Import Prompt File (.txt)",
            "CHOOSE_FILE_TEXT": "选择文件" if not is_en else "Choose File",
            "NO_FILE_TEXT": "未选择文件" if not is_en else "No file chosen",
            "IMPORT_TO_LABEL": "导入到" if not is_en else "Import To",
            "TASK_PROMPT_TEXT": "任务提示词" if not is_en else "Task Prompt",
            "SYSTEM_PROMPT_TEXT": "系统提示词" if not is_en else "System Prompt",
            "IMPORT_NAME_LABEL": "导入时命名（可选）" if not is_en else "Name on Import (Optional)",
            "IMPORT_NAME_PLACEHOLDER": "例如：核心链路专项诊断（不填自动用文件名）" if not is_en else "e.g. Core Link Diagnosis (optional)",
            "IMPORT_PROMPT_TEXT": "导入提示词" if not is_en else "Import Prompt",
            "EDIT_PROMPT_TITLE": "编辑提示词" if not is_en else "Edit Prompt",
            "CLOSE_TEXT": "关闭" if not is_en else "Close",
            "SAVE_CHANGES_TEXT": "保存修改" if not is_en else "Save Changes",
            "DELETE_TEMPLATE_TEXT": "删除模板" if not is_en else "Delete Template",
            "CANCEL_EDIT_TEXT": "取消修改" if not is_en else "Cancel Edit",
        },
    )

    page_css = render_html_template("pages/ai_settings.css", {})

    _scope = dict(globals())
    _scope.update(locals())
    page_script = render_python_f_template("pages/ai_settings_script.pyf.html", _scope)


    page_html = render_base_page(
        lang=lang,
        title=title,
        header_html=build_app_header_html(lang, "ai"),
        page_body=body_html,
        page_css=page_css,
        page_script=page_script,
    )
    return render_html_template("ai_settings.html", {"CONTENT": page_html})

