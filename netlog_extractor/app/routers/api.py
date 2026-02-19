from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/api", tags=["api"])
OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output"


def _get_task_or_404(request: Request, task_id: str):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


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
    target = next((d for d in task.devices if d.device_id == device_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="device not found")
    if not target.filtered_log_path:
        raise HTTPException(status_code=404, detail="filtered log not ready")
    path = Path(target.filtered_log_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="log file missing")
    return FileResponse(path, media_type="text/plain", filename=f"{task_id}-{device_id}-filtered.log")


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
