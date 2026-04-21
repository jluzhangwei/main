from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..services.sql_log_service import test_log_server_connection

router = APIRouter(prefix="/api", tags=["api"])
OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output"


def _get_task_or_404(request: Request, task_id: str):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _get_device_or_404(task, device_id: str):
    target = next((d for d in task.devices if d.device_id == device_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="device not found")
    return target


def _device_output_dir(target) -> Path:
    for candidate in (target.filtered_log_path, target.raw_log_path, target.meta_path, target.debug_log_path):
        if candidate:
            return Path(candidate).parent
    raise HTTPException(status_code=404, detail="device output not ready")


@router.get("/tasks")
async def list_tasks_api(request: Request):
    tasks = request.app.state.task_manager.list_tasks()
    return [t.model_dump() for t in tasks]


@router.get("/tasks/{task_id}")
async def get_task_api(task_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    return task.model_dump()


@router.get("/tasks/{task_id}/summary.json")
async def summary_json(task_id: str, request: Request):
    _get_task_or_404(request, task_id)
    path = OUTPUT_ROOT / task_id / "summary.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="summary not ready")
    return FileResponse(path, media_type="application/json", filename=f"summary-{task_id}.json")


@router.get("/tasks/{task_id}/devices/{device_id}/log")
async def download_device_log(task_id: str, device_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    target = _get_device_or_404(task, device_id)
    if not target.filtered_log_path:
        raise HTTPException(status_code=404, detail="filtered log not ready")
    path = Path(target.filtered_log_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="log file missing")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-filtered.log")


@router.get("/tasks/{task_id}/devices/{device_id}/sql/raw")
async def download_device_sql_raw(task_id: str, device_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    target = _get_device_or_404(task, device_id)
    path = _device_output_dir(target) / "raw_sql.log"
    if not path.exists():
        raise HTTPException(status_code=404, detail="sql raw log not ready")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-raw-sql.log")


@router.get("/tasks/{task_id}/devices/{device_id}/device-filtered")
async def download_device_filtered_device_log(task_id: str, device_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    target = _get_device_or_404(task, device_id)
    path = _device_output_dir(target) / "filtered_device.log"
    if not path.exists():
        raise HTTPException(status_code=404, detail="device filtered log not ready")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-filtered-device.log")


@router.get("/tasks/{task_id}/devices/{device_id}/sql/filtered")
async def download_device_sql_filtered(task_id: str, device_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    target = _get_device_or_404(task, device_id)
    path = _device_output_dir(target) / "filtered_sql.log"
    if not path.exists():
        raise HTTPException(status_code=404, detail="sql filtered log not ready")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-filtered-sql.log")


@router.get("/tasks/{task_id}/devices/{device_id}/debug")
async def download_device_debug(task_id: str, device_id: str, request: Request):
    task = _get_task_or_404(request, task_id)
    target = next((d for d in task.devices if d.device_id == device_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="device not found")
    if not target.debug_log_path:
        raise HTTPException(status_code=404, detail="debug log not ready")
    path = Path(target.debug_log_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="debug log file missing")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-debug.log")


@router.get("/tasks/{task_id}/download.zip")
async def download_zip(task_id: str, request: Request):
    _get_task_or_404(request, task_id)
    root = OUTPUT_ROOT / task_id
    if not root.exists():
        raise HTTPException(status_code=404, detail="task output not found")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(root))
    zip_buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{task_id}.zip"'}
    return StreamingResponse(zip_buf, media_type="application/zip", headers=headers)


@router.post("/tasks/delete")
async def delete_tasks_api(request: Request, payload: dict = Body(default={})):
    ids = payload.get("task_ids", [])
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="task_ids must be a list")
    ids = [str(x or "").strip() for x in ids if str(x or "").strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="task_ids is empty")
    result = request.app.state.task_manager.delete_tasks(ids)
    return {"ok": True, **result}


@router.post("/sql/log-server/test")
async def test_sql_log_server_api(payload: dict = Body(default={})):
    try:
        return test_log_server_connection(
            db_host=(payload.get("db_host") or None),
            db_port=int(payload.get("db_port") or 0) or None,
            db_user=(payload.get("db_user") or None),
            db_password=(payload.get("db_password") if "db_password" in payload else None),
            db_name=(payload.get("db_name") or None),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
