from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..models import DeviceInput
from ..services.task_service import (
    build_payload,
    parse_devices_from_text,
)

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=(Path(__file__).resolve().parent.parent / "templates").as_posix())


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "error": None})


@router.post("/tasks/create", response_class=HTMLResponse)
async def create_task(
    request: Request,
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
    jump_host: str = Form(""),
    jump_port: int = Form(22),
    smc_command: str = Form(""),
):
    try:
        devices: list[DeviceInput] = []
        if mode == "single":
            if not (device_ip.strip() and username.strip() and password.strip()):
                raise ValueError("Single mode requires device_ip, username, password")

            final_smc_cmd = smc_command.strip() or (f"smc server toc {jump_host.strip()}" if jump_mode == "smc" else None)
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
            final_smc_cmd = smc_command.strip() or (f"smc server toc {jump_host.strip()}" if jump_mode == "smc" else None)

            if batch_text.strip():
                devices.extend(
                    parse_devices_from_text(
                        raw_text=batch_text,
                        default_username=global_user,
                        default_password=global_pass,
                        default_jump_mode=jump_mode,
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
            },
            devices,
        )
        task = request.app.state.task_manager.create_task(payload)
        return RedirectResponse(url=f"/tasks/{task.task_id}", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("index.html", {"request": request, "error": str(exc)}, status_code=400)


@router.get("/tasks", response_class=HTMLResponse)
async def list_tasks_page(request: Request):
    tasks = request.app.state.task_manager.list_tasks()
    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": tasks})


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: str, request: Request):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        return templates.TemplateResponse(
            "task_detail.html",
            {"request": request, "task": None, "error": "Task not found"},
            status_code=404,
        )
    return templates.TemplateResponse("task_detail.html", {"request": request, "task": task, "error": None})


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse("help.html", {"request": request})
