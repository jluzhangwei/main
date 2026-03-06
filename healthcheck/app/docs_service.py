#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
from typing import Optional


def normalize_lang(value: str) -> str:
    v = str(value or "").strip().lower()
    if v.startswith("en"):
        return "en"
    return "zh"


def with_lang(path: str, lang: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}lang={normalize_lang(lang)}"


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "docs" / "templates"


def _load_template(name: str, lang: str) -> str:
    p = _TEMPLATE_DIR / normalize_lang(lang) / name
    if not p.is_file():
        p = _TEMPLATE_DIR / "zh" / name
    return p.read_text(encoding="utf-8")


def _inject_common(
    text: str,
    lang: str,
    doc_version: Optional[str],
    doc_version_rule: Optional[str],
    page_path: str,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    version_line = f"Version: {doc_version or 'V2.3'}"
    version_rule = doc_version_rule or "大改动升主版本（如 V2.0），小更新升次版本（如 V1.14 -> V1.15）"
    user_text = (auth_username or "").strip()
    role_text = (auth_role or "user").strip() or "user"
    lang_label = "EN" if normalize_lang(lang) == "zh" else "中"
    target_lang = "en" if normalize_lang(lang) == "zh" else "zh"
    top_parts = []
    if user_text:
        if role_text == "admin":
            top_parts.append(
                f'<a class="app-top-link" href="{with_lang("/admin", lang)}" title="User" '
                f'style="display:inline-flex;align-items:center;justify-content:center;text-decoration:none;width:auto;'
                f'height:34px;line-height:1;margin:0;border-radius:8px;padding:0 10px;font-size:12px;'
                f'border:1px solid #384152;background:#111827;color:#e5e7eb;">{user_text}({role_text})</a>'
            )
        else:
            top_parts.append(
                f'<span class="app-top-link" title="User" '
                f'style="display:inline-flex;align-items:center;justify-content:center;text-decoration:none;width:auto;'
                f'height:34px;line-height:1;margin:0;border-radius:8px;padding:0 10px;font-size:12px;'
                f'border:1px solid #384152;background:#111827;color:#e5e7eb;">{user_text}({role_text})</span>'
            )
    top_parts.append(
        f'<a class="app-top-link" href="{with_lang("/guide", lang)}" title="View Docs" '
        f'style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;'
        f'min-width:34px;max-width:34px;flex:0 0 34px;box-sizing:border-box;'
        f'border:1px solid #8c96a8;border-radius:999px;margin:0;padding:0;line-height:1;">?</a>'
    )
    top_parts.append(
        f'<a class="app-top-link" href="{with_lang(page_path, target_lang)}" title="Switch Language" '
        f'style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;'
        f'min-width:34px;max-width:34px;flex:0 0 34px;box-sizing:border-box;'
        f'border:1px solid #8c96a8;border-radius:999px;margin:0;padding:0;line-height:1;">{lang_label}</a>'
    )
    top_parts.append(
        f'<a class="app-top-link" href="{with_lang("/logout", lang)}" title="Sign Out">{"退出" if normalize_lang(lang) == "zh" else "Logout"}</a>'
    )
    menu_parts = [
        f'<a href="{with_lang("/", lang)}">{"创建任务" if normalize_lang(lang) == "zh" else "Create Task"}</a>',
        f'<a href="{with_lang("/tasks", lang)}">{"任务页面" if normalize_lang(lang) == "zh" else "Tasks"}</a>',
        f'<a href="{with_lang("/ai/settings", lang)}">{"AI 设置" if normalize_lang(lang) == "zh" else "AI Settings"}</a>',
    ]
    if role_text == "admin":
        menu_parts.append(f'<a href="{with_lang("/admin", lang)}">{"用户管理" if normalize_lang(lang) == "zh" else "Admin"}</a>')
    out = text
    out = out.replace("__DOC_VERSION__", version_line)
    out = out.replace("__DOC_RULE__", version_rule)
    out = out.replace("__GUIDE_BACK__", with_lang("/guide", lang))
    out = out.replace("__HOME_BACK__", with_lang("/", lang))
    out = out.replace("__TASKS_BACK__", with_lang("/tasks", lang))
    out = out.replace("__AI_SETTINGS_BACK__", with_lang("/ai/settings", lang))
    out = out.replace("__TOP_LINKS__", "".join(top_parts))
    out = out.replace("__MAIN_MENU__", "".join(menu_parts))
    return out


def build_guide_html(
    lang: str = "zh",
    doc_version: Optional[str] = None,
    doc_version_rule: Optional[str] = None,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    lang = normalize_lang(lang)
    tmpl = _load_template("guide_design.html", lang)
    return _inject_common(tmpl, lang, doc_version, doc_version_rule, "/guide/design", auth_username, auth_role)


def build_guide_index_html(
    lang: str = "zh",
    doc_version: Optional[str] = None,
    doc_version_rule: Optional[str] = None,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    lang = normalize_lang(lang)
    tmpl = _load_template("guide_index.html", lang)
    return _inject_common(tmpl, lang, doc_version, doc_version_rule, "/guide", auth_username, auth_role)


def build_user_guide_html(
    lang: str = "zh",
    doc_version: Optional[str] = None,
    doc_version_rule: Optional[str] = None,
    auth_username: str = "",
    auth_role: str = "user",
) -> str:
    lang = normalize_lang(lang)
    tmpl = _load_template("guide_user.html", lang)
    return _inject_common(tmpl, lang, doc_version, doc_version_rule, "/guide/user", auth_username, auth_role)
