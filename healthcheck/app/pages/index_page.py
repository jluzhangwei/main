#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from app.web_server import *  # noqa: F401,F403
from app.template_runtime import render_python_f_template

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

    _scope = dict(globals())
    _scope.update(locals())
    _html = render_python_f_template("pages/index_full.pyf.html", _scope)
    page_html = localize_html_page(_html, lang)
    return render_html_template("index.html", {"CONTENT": page_html})
