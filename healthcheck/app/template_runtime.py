#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
from typing import Dict, Any

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def render_python_f_template(template_name: str, scope: Dict[str, Any]) -> str:
    """Render a trusted template file as Python f-string using current scope.

    Used for incremental migration from inline f-strings to file-based templates
    while keeping runtime behavior unchanged.
    """
    path = TEMPLATE_DIR / template_name
    text = path.read_text(encoding="utf-8")
    safe_scope: Dict[str, Any] = {k: v for k, v in (scope or {}).items() if k != "__builtins__"}
    safe_scope["__builtins__"] = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "len": len,
        "min": min,
        "max": max,
        "sum": sum,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "sorted": sorted,
        "range": range,
        "enumerate": enumerate,
        "iter": iter,
        "next": next,
        "any": any,
        "all": all,
    }
    return eval("f'''" + text + "'''", safe_scope, {})
