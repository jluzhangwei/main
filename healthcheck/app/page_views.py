#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Facade for page builders. Each page is isolated in app/pages/*.py."""

from app.pages.index_page import build_html
from app.pages.task_detail_page import build_job_html
from app.pages.tasks_page import build_tasks_page
from app.pages.ai_settings_page import build_ai_settings_page

__all__ = [
    "build_html",
    "build_job_html",
    "build_tasks_page",
    "build_ai_settings_page",
]
