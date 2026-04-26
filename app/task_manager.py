from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import TaskDB
from .models import DeviceInput, DeviceResult, TaskCreatePayload, TaskRecord
from .services.device_service import run_device_collection


def _now_s() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class TaskManager:
    def __init__(self, db: TaskDB, output_root: str = "./output") -> None:
        self.db = db
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._running: dict[str, asyncio.Task] = {}
        self._runtime_devices: dict[str, list[dict[str, Any]]] = {}
        self._runtime_sql_configs: dict[str, dict[str, Any]] = {}

    def create_task(self, payload: TaskCreatePayload) -> TaskRecord:
        task_id = uuid.uuid4().hex[:12]
        now = _now_s()
        devices = [
            DeviceResult(
                device_id=f"dev-{i+1}",
                device_ip=d.device_ip,
                device_name=d.device_name,
            )
            for i, d in enumerate(payload.devices)
        ]

        params = {
            "start_time": payload.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": payload.end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "context_lines": payload.context_lines,
            "concurrency": payload.concurrency,
            "per_device_timeout": payload.per_device_timeout,
            "default_jump_mode": payload.default_jump_mode,
            "jump_host": payload.jump_host,
            "jump_port": payload.jump_port,
            "smc_command": payload.smc_command,
            "debug_mode": payload.debug_mode,
            "sql_query_mode": payload.sql_query_mode,
            "sql_only_mode": payload.sql_only_mode,
            "db_host": payload.db_host,
            "db_port": payload.db_port,
            "db_user": payload.db_user,
            "db_name": payload.db_name,
        }
        self._runtime_devices[task_id] = [d.model_dump() for d in payload.devices]
        self._runtime_sql_configs[task_id] = {
            "db_host": payload.db_host,
            "db_port": payload.db_port,
            "db_user": payload.db_user,
            "db_password": payload.db_password,
            "db_name": payload.db_name,
        }
        task = TaskRecord(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status="pending",
            progress_total=len(payload.devices),
            progress_done=0,
            params=params,
            devices=devices,
        )
        self.db.upsert_task(task)
        self._running[task_id] = asyncio.create_task(self._run_task(task_id))
        return task

    async def _run_task(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        if task is None:
            return
        task.status = "running"
        task.updated_at = _now_s()
        self.db.upsert_task(task)

        params = task.params
        runtime_raw = self._runtime_devices.get(task_id, [])
        runtime_devices = [DeviceInput(**d) for d in runtime_raw]
        if not runtime_devices:
            task.status = "failed"
            task.updated_at = _now_s()
            self.db.upsert_task(task)
            return
        user_start = datetime.strptime(params["start_time"], "%Y-%m-%d %H:%M:%S")
        user_end = datetime.strptime(params["end_time"], "%Y-%m-%d %H:%M:%S")
        context_lines = int(params["context_lines"])
        timeout = int(params["per_device_timeout"])
        concurrency = max(1, int(params["concurrency"]))
        debug_mode = bool(params.get("debug_mode", False))
        sql_query_mode = bool(params.get("sql_query_mode", False))
        sql_only_mode = bool(params.get("sql_only_mode", False))
        sql_cfg = self._runtime_sql_configs.get(task_id, {})

        sem = asyncio.Semaphore(concurrency)
        out_dir = self.output_root / task_id
        out_dir.mkdir(parents=True, exist_ok=True)

        async def run_one(index: int, device: DeviceInput) -> None:
            nonlocal task
            dev = task.devices[index]
            dev.status = "running"
            task.updated_at = _now_s()
            self.db.upsert_task(task)

            async with sem:
                try:
                    device_dir = out_dir / dev.device_id
                    result = await run_device_collection(
                        device=device,
                        output_dir=device_dir,
                        user_start=user_start,
                        user_end=user_end,
                        context_lines=context_lines,
                        per_device_timeout=timeout,
                        debug_mode=debug_mode,
                        sql_query_mode=sql_query_mode,
                        sql_only_mode=sql_only_mode,
                        db_host=sql_cfg.get("db_host"),
                        db_port=sql_cfg.get("db_port"),
                        db_user=sql_cfg.get("db_user"),
                        db_password=sql_cfg.get("db_password"),
                        db_name=sql_cfg.get("db_name"),
                    )
                    dev.status = "success"
                    dev.reason = None
                    dev.device_name = result.get("device_name") or dev.device_name
                    dev.log_source = result.get("log_source")
                    dev.vendor = result["vendor"]
                    dev.os_family = result["os_family"]
                    dev.model = result.get("model")
                    dev.version = result.get("version")
                    dev.reference_time = result.get("reference_time")
                    dev.device_time = result.get("device_time")
                    dev.offset_seconds = result.get("offset_seconds")
                    dev.hits_count = result.get("hits_count", 0)
                    dev.blocks_count = result.get("blocks_count", 0)
                    dev.raw_log_path = result.get("raw_log_path")
                    dev.filtered_log_path = result.get("filtered_log_path")
                    dev.meta_path = result.get("meta_path")
                    dev.debug_log_path = result.get("debug_log_path")
                except Exception as exc:
                    dev.status = "failed"
                    reason = str(exc).strip()
                    if not reason:
                        if isinstance(exc, asyncio.TimeoutError):
                            reason = f"Device collection timeout after {timeout}s"
                        else:
                            reason = exc.__class__.__name__
                    dev.reason = reason
                finally:
                    task.progress_done += 1
                    task.updated_at = _now_s()
                    self.db.upsert_task(task)

        await asyncio.gather(*(run_one(i, d) for i, d in enumerate(runtime_devices)))

        success_count = sum(1 for d in task.devices if d.status == "success")
        if success_count == len(task.devices):
            task.status = "success"
        elif success_count == 0:
            task.status = "failed"
        else:
            task.status = "partial_failed"
        task.updated_at = _now_s()

        summary = {
            "task_id": task.task_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "status": task.status,
            "progress_total": task.progress_total,
            "progress_done": task.progress_done,
            "devices": [d.model_dump() for d in task.devices],
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        self.db.upsert_task(task)
        self._runtime_devices.pop(task_id, None)
        self._runtime_sql_configs.pop(task_id, None)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.db.get_task(task_id)

    def list_tasks(self) -> list[TaskRecord]:
        return self.db.list_tasks()

    def delete_tasks(self, task_ids: list[str]) -> dict[str, Any]:
        ids = [str(t or "").strip() for t in (task_ids or []) if str(t or "").strip()]
        delete_candidates: list[str] = []
        skipped_running: list[str] = []
        removed_files = 0
        for tid in ids:
            task = self.db.get_task(tid)
            if not task:
                continue
            if task.status in {"running", "pending"}:
                skipped_running.append(tid)
                continue
            out_dir = self.output_root / tid
            if out_dir.exists():
                try:
                    count_before = sum(1 for p in out_dir.rglob("*") if p.is_file())
                except Exception:
                    count_before = 0
                shutil.rmtree(out_dir, ignore_errors=True)
                removed_files += count_before
            self._runtime_devices.pop(tid, None)
            self._runtime_sql_configs.pop(tid, None)
            self._running.pop(tid, None)
            delete_candidates.append(tid)
        if delete_candidates and hasattr(self.db, "delete_tasks_exact"):
            deleted_ids = self.db.delete_tasks_exact(delete_candidates)
        else:
            self.db.delete_tasks(delete_candidates) if delete_candidates else 0
            deleted_ids = [tid for tid in delete_candidates if self.db.get_task(tid) is None]
        deleted_count = len(deleted_ids)
        return {
            "requested": len(ids),
            "deleted": int(deleted_count),
            "deleted_ids": deleted_ids,
            "skipped_running": skipped_running,
            "removed_files": int(removed_files),
        }
