#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from app import docs_service
from app.pages.ai_settings_page import build_ai_settings_page
from app.pages.index_page import build_html
from app.pages.task_detail_page import build_job_html
from app.pages.tasks_page import build_tasks_page
from app.web_server import (
    DEFAULT_CHECK_TEMPLATE_NAME,
    merged_check_template_catalog,
    default_form_values,
    user_can_modify,
)


def render_home(lang: str, user: dict) -> str:
    templates = merged_check_template_catalog()
    default_template = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")
    default_checks = (templates.get(default_template, {}).get("checks", []) if isinstance(templates.get(default_template, {}), dict) else [])[:3]
    return build_html(
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


def render_home_with_state(
    *,
    lang: str,
    user: dict,
    values: dict,
    selected_checks: list,
    status: str = "",
    output_text: str = "",
    selected_template: str = "",
) -> str:
    templates = merged_check_template_catalog()
    target_template = selected_template or DEFAULT_CHECK_TEMPLATE_NAME
    if target_template not in templates:
        target_template = DEFAULT_CHECK_TEMPLATE_NAME if DEFAULT_CHECK_TEMPLATE_NAME in templates else next(iter(templates.keys()), "")
    return build_html(
        values,
        selected_checks,
        output_text,
        status,
        lang=lang,
        selected_template=target_template,
        can_modify=user_can_modify(user),
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
    )


def render_tasks(lang: str, user: dict) -> str:
    return build_tasks_page(
        lang=lang,
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
    )


def render_task_detail(task_id: str, lang: str, user: dict) -> str:
    return build_job_html(
        task_id,
        history_mode=False,
        lang=lang,
        can_modify=user_can_modify(user),
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
    )


def render_ai_settings(lang: str, user: dict) -> str:
    return build_ai_settings_page(
        lang=lang,
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
        can_modify=user_can_modify(user),
    )


def render_user_guide(lang: str, user: dict, doc_version: str, doc_version_rule: str) -> str:
    return docs_service.build_user_guide_html(
        lang=lang,
        doc_version=doc_version,
        doc_version_rule=doc_version_rule,
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
    )


def render_history_job(job_id: str, lang: str, user: dict) -> str:
    return build_job_html(
        job_id,
        history_mode=True,
        lang=lang,
        can_modify=user_can_modify(user),
        auth_username=str(user.get("username", "")),
        auth_role=str(user.get("role", "user")),
    )
