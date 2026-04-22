from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..ai.prompt_store import localized_prompt_catalog, localized_prompt_labels
from ..ai.state_store import load_gpt_config
from ..models import DeviceInput
from ..services.task_service import (
    build_payload,
    default_smc_command,
    parse_devices_from_text,
)

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=(Path(__file__).resolve().parent.parent / "templates").as_posix())


def _device_view_dict(device) -> dict:
    data = device.model_dump() if hasattr(device, "model_dump") else dict(device or {})
    target_dir = None
    for candidate in (data.get("filtered_log_path"), data.get("raw_log_path"), data.get("meta_path"), data.get("debug_log_path")):
        if candidate:
            target_dir = Path(candidate).parent
            break
    data["semantic_compact_exists"] = bool(target_dir and (target_dir / "semantic_compact.md").exists())
    data["semantic_index_exists"] = bool(target_dir and (target_dir / "semantic_index.json").exists())
    return data


def _no_cache(resp: HTMLResponse):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _no_cache(templates.TemplateResponse("index.html", {"request": request, "error": None}))


@router.post("/tasks/create", response_class=HTMLResponse)
async def create_task(
    request: Request,
    lang: str = Form("zh"),
    mode: str = Form("single"),
    jump_mode: str = Form("direct"),
    device_ip: str = Form(""),
    port: int = Form(22),
    vendor_hint: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    batch_text: str = Form(""),
    default_username: str = Form(""),
    default_password: str = Form(""),
    start_time: str = Form(...),
    end_time: str = Form(...),
    context_lines: int = Form(3),
    concurrency: int = Form(10),
    per_device_timeout: int = Form(60),
    debug_mode: str = Form(""),
    sql_query_mode: str = Form(""),
    sql_only_mode: str = Form(""),
    db_host: str = Form(""),
    db_port: int = Form(0),
    db_user: str = Form(""),
    db_password: str = Form(""),
    db_name: str = Form(""),
    jump_host: str = Form(""),
    jump_port: int = Form(22),
    smc_command: str = Form(""),
):
    try:
        devices: list[DeviceInput] = []
        require_credentials = (sql_only_mode != "on") and str(jump_mode or "").strip().lower() != "smc_pam_nd"
        if mode == "single":
            if require_credentials and not (device_ip.strip() and username.strip() and password.strip()):
                raise ValueError("Single mode requires device_ip, username, password")
            if not device_ip.strip():
                raise ValueError("Single mode requires device_ip")

            final_smc_cmd = smc_command.strip() or default_smc_command(jump_mode, jump_host.strip() or None)
            devices.append(
                DeviceInput(
                    device_ip=device_ip.strip(),
                    device_port=port,
                    username=username.strip(),
                    password=password,
                    vendor_hint=vendor_hint.strip() or None,
                    jump_mode=jump_mode,
                    jump_host=jump_host.strip() or None,
                    jump_port=jump_port,
                    smc_command=final_smc_cmd,
                )
            )
        else:
            global_user = default_username.strip() or None
            global_pass = default_password or None
            final_smc_cmd = smc_command.strip() or default_smc_command(jump_mode, jump_host.strip() or None)

            if batch_text.strip():
                devices.extend(
                    parse_devices_from_text(
                        raw_text=batch_text,
                        default_username=global_user,
                        default_password=global_pass,
                        default_jump_mode=jump_mode,
                        sql_only_mode=(sql_only_mode == "on"),
                        jump_host=jump_host.strip() or None,
                        jump_port=jump_port,
                        smc_command=final_smc_cmd,
                        vendor_hint=vendor_hint.strip() or None,
                    )
                )
            if not devices:
                raise ValueError("Batch mode requires device list text")

        payload = build_payload(
            {
                "start_time": start_time,
                "end_time": end_time,
                "context_lines": context_lines,
                "concurrency": concurrency,
                "per_device_timeout": per_device_timeout,
                "default_username": default_username,
                "default_password": default_password,
                "jump_mode": jump_mode,
                "jump_host": jump_host,
                "jump_port": jump_port,
                "smc_command": smc_command,
                "debug_mode": debug_mode == "on",
                "sql_query_mode": sql_query_mode == "on",
                "sql_only_mode": sql_only_mode == "on",
                "db_host": db_host,
                "db_port": db_port,
                "db_user": db_user,
                "db_password": db_password,
                "db_name": db_name,
            },
            devices,
        )
        task = request.app.state.task_manager.create_task(payload)
        lang_norm = "en" if str(lang or "").strip().lower().startswith("en") else "zh"
        return RedirectResponse(url=f"/tasks/{task.task_id}?lang={lang_norm}", status_code=303)
    except Exception as exc:
            return _no_cache(templates.TemplateResponse("index.html", {"request": request, "error": str(exc)}, status_code=400))


@router.get("/tasks", response_class=HTMLResponse)
async def list_tasks_page(request: Request):
    tasks = request.app.state.task_manager.list_tasks()
    return _no_cache(templates.TemplateResponse("tasks.html", {"request": request, "tasks": tasks}))


@router.post("/tasks/delete")
async def delete_tasks_form(
    request: Request,
    lang: str = Form("zh"),
    task_ids: list[str] = Form(default=[]),
):
    ids = [str(x or "").strip() for x in (task_ids or []) if str(x or "").strip()]
    dedup_ids: list[str] = []
    seen: set[str] = set()
    for tid in ids:
        if tid in seen:
            continue
        seen.add(tid)
        dedup_ids.append(tid)
    if dedup_ids:
        request.app.state.task_manager.delete_tasks(dedup_ids)
    lang_norm = "en" if str(lang or "").strip().lower().startswith("en") else "zh"
    return RedirectResponse(url=f"/tasks?lang={lang_norm}", status_code=303)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: str, request: Request):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        return _no_cache(templates.TemplateResponse(
            "task_detail.html",
            {"request": request, "task": None, "error": "Task not found"},
            status_code=404,
        ))
    lang = str(request.query_params.get("lang", "zh") or "zh")
    cfg = load_gpt_config()
    system_prompts = localized_prompt_catalog("system", lang)
    task_prompts = localized_prompt_catalog("task", lang)
    system_prompt_labels = localized_prompt_labels("system", lang)
    task_prompt_labels = localized_prompt_labels("task", lang)
    return _no_cache(templates.TemplateResponse(
        "task_detail.html",
        {
            "request": request,
            "task": task,
            "task_devices_view": [_device_view_dict(d) for d in task.devices],
            "task_devices_json": [_device_view_dict(d) for d in task.devices],
            "error": None,
            "system_prompts": system_prompts,
            "task_prompts": task_prompts,
            "system_prompt_labels": system_prompt_labels,
            "task_prompt_labels": task_prompt_labels,
            "selected_system_prompt": str(cfg.get("selected_system_prompt", "") or ""),
            "selected_task_prompt": str(cfg.get("selected_task_prompt", "") or ""),
        },
    ))


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return _no_cache(templates.TemplateResponse("help.html", {"request": request}))
