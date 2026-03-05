#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading
import time
from typing import Dict, List, MutableMapping


class AnalysisStatusStore:
    def __init__(self, tasks: MutableMapping[str, Dict], lock: threading.Lock):
        self._tasks = tasks
        self._lock = lock

    def create(self, analysis_id: str, payload: Dict) -> None:
        with self._lock:
            self._tasks[analysis_id] = dict(payload or {})

    def update(self, analysis_id: str, **kwargs: Dict) -> None:
        with self._lock:
            task = self._tasks.get(analysis_id)
            if not task:
                return
            start_ts = float(task.get("start_ts", time.time()) or time.time())
            elapsed = max(0.0, time.time() - start_ts)
            kwargs.setdefault("elapsed_seconds", elapsed)
            if "progress" in kwargs:
                try:
                    new_progress = int(kwargs.get("progress", 0) or 0)
                except Exception:
                    new_progress = 0
                old_progress = int(task.get("progress", 0) or 0)
                kwargs["progress"] = max(old_progress, new_progress)
            task.update(kwargs)

    def get_task(self, analysis_id: str) -> Dict:
        with self._lock:
            task = self._tasks.get(analysis_id)
            return dict(task or {})

    def get_response_payload(self, analysis_id: str) -> Dict:
        with self._lock:
            task = self._tasks.get(analysis_id)
            if not task:
                return {"ok": False, "error": "analysis task not found"}
            return {
                "ok": True,
                "status": task.get("status", "error"),
                "stage": task.get("stage", ""),
                "message": task.get("message", ""),
                "progress": int(task.get("progress", 0) or 0),
                "elapsed_seconds": float(task.get("elapsed_seconds", 0.0) or 0.0),
                "duration_seconds": float(task.get("duration_seconds", 0.0) or 0.0),
                "total_devices": int(task.get("total_devices", 0) or 0),
                "done_devices": int(task.get("done_devices", 0) or 0),
                "started_devices": int(task.get("started_devices", 0) or 0),
                "inflight_devices": int(task.get("inflight_devices", 0) or 0),
                "inflight_device_names": list(task.get("inflight_device_names", []) or []),
                "total_batches": int(task.get("total_batches", 0) or 0),
                "done_batches": int(task.get("done_batches", 0) or 0),
                "analysis": task.get("result", ""),
                "error": task.get("error", ""),
                "provider_used": task.get("provider_used", ""),
                "model_used": task.get("model_used", ""),
                "local_base_url": task.get("local_base_url", ""),
                "prompt_source": task.get("prompt_source", ""),
                "token_usage": task.get("token_usage", {"total_tokens": 0}),
                "token_total": int(task.get("token_total", 0) or 0),
            }

    def request_cancel(self, analysis_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(analysis_id)
            if not task:
                return False
            if str(task.get("status", "")) in {"done", "error", "canceled"}:
                return False
            task["cancel_requested"] = True
            task["message"] = "已请求停止分析，等待当前调用完成..."
            return True

    def is_cancel_requested(self, analysis_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(analysis_id)
            if not task:
                return False
            return bool(task.get("cancel_requested", False))

    def find_analysis_id_by_job(self, job_id: str, running_only: bool = True) -> str:
        target = str(job_id or "").strip()
        if not target:
            return ""
        matched_id = ""
        matched_ts = -1.0
        with self._lock:
            for aid, task in self._tasks.items():
                if str(task.get("job_id", "") or "").strip() != target:
                    continue
                status = str(task.get("status", "") or "")
                if running_only and status not in {"running"}:
                    continue
                start_ts = float(task.get("start_ts", 0.0) or 0.0)
                if start_ts >= matched_ts:
                    matched_ts = start_ts
                    matched_id = str(aid)
        return matched_id

    def exists(self, analysis_id: str) -> bool:
        with self._lock:
            return analysis_id in self._tasks

    def inflight_names(self, analysis_id: str) -> List[str]:
        with self._lock:
            task = self._tasks.get(analysis_id, {})
            return list(task.get("inflight_device_names", []) or [])
