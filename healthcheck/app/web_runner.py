#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Compatibility shim.

Primary implementation moved to `app/web_server.py`.
This file is kept so existing commands (`python app/web_runner.py`) keep working.
"""

from app.web_server import *  # noqa: F401,F403


if __name__ == "__main__":
    run_server()
