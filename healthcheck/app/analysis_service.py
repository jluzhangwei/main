#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import concurrent.futures
import json
import math
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, MutableMapping, Optional
from uuid import uuid4

from app import analysis_guard, analysis_pipeline
from app.llm_adapter import LLMAdapter
from app.status_service import AnalysisStatusStore


class AnalysisService:
    def __init__(
        self,
        jobs: MutableMapping[str, Dict],
        jobs_lock: threading.Lock,
        report_dir: Path,
        is_safe_report_name: Callable[[str], bool],
        add_token_usage: Callable[[str, int], Dict],
        status_store: AnalysisStatusStore,
        llm_adapter: LLMAdapter,
        persist_callback: Optional[Callable[[Dict], str]] = None,
    ):
        self._jobs = jobs
        self._jobs_lock = jobs_lock
        self._report_dir = report_dir
        self._is_safe_report_name = is_safe_report_name
        self._add_token_usage = add_token_usage
        self._status = status_store
        self._llm = llm_adapter
        self._persist_callback = persist_callback

    def model_used(self, llm: Dict[str, str]) -> str:
        return self._llm.model_used(llm)

    def run_llm_analysis(self, llm: Dict[str, str], report_text: str):
        return self._llm.run_analysis(llm, report_text)

    def probe_cloud_token_balance(self, provider: str, api_key: str, model: str):
        return self._llm.probe_cloud_token_balance(provider, api_key, model)

    def load_job_report_json(self, job: Dict) -> Dict:
        report_name = str(job.get("report_json", "") or "")
        if not report_name or not self._is_safe_report_name(report_name):
            raise RuntimeError("未找到可用于 AI 分析的 JSON 报告。")
        report_path = self._report_dir / report_name
        if not report_path.is_file():
            raise RuntimeError("JSON 报告文件不存在。")
        try:
            return json.loads(report_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as exc:
            raise RuntimeError(f"JSON 报告解析失败: {exc}") from exc

    def build_analysis_input(self, job: Dict) -> str:
        report_name = str(job.get("report_json", "") or "")
        if report_name and self._is_safe_report_name(report_name):
            report_path = self._report_dir / report_name
            if report_path.is_file():
                try:
                    report_data = json.loads(report_path.read_text(encoding="utf-8", errors="ignore"))
                    if isinstance(report_data, dict) and isinstance(report_data.get("devices"), list):
                        return analysis_pipeline.build_whole_report_analysis_input(
                            report_data,
                            force_full=False,
                        )
                    report_text = json.dumps(report_data, ensure_ascii=False)
                    return f"结构化报告JSON（完整）：\n{report_text}"
                except Exception:
                    report_text = report_path.read_text(encoding="utf-8", errors="ignore")
                    return f"结构化报告JSON（完整）：\n{report_text}"
        raise RuntimeError("未找到可用于 AI 分析的 JSON 报告，请先运行巡检并生成 JSON 报告。")

    def start_batched_analysis(
        self,
        job_id: str,
        llm: Dict[str, str],
        batch_size: int = 5,
        analysis_parallelism: int = 2,
        analysis_retries: int = 1,
        report_data_override: Optional[Dict] = None,
        large_report_mode: bool = False,
        large_report_chunk_items: int = 4,
    ) -> str:
        analysis_id = uuid4().hex[:12]
        self._status.create(
            analysis_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "准备分批分析...",
                "progress": 0,
                "start_ts": time.time(),
                "elapsed_seconds": 0.0,
                "duration_seconds": 0.0,
                "job_id": job_id,
                "total_devices": 0,
                "done_devices": 0,
                "started_devices": 0,
                "inflight_devices": 0,
                "inflight_device_names": [],
                "total_batches": 0,
                "done_batches": 0,
                "result": "",
                "error": "",
                "token_usage": {"total_tokens": 0},
                "token_total": 0,
                "provider_used": llm.get("provider", "chatgpt"),
                "model_used": self.model_used(llm),
                "local_base_url": llm.get("local_base_url", "") if llm.get("provider") == "local" else "",
                "prompt_source": llm.get("prompt_source", ""),
                "analysis_parallelism": max(1, int(analysis_parallelism or 1)),
                "analysis_retries": max(0, int(analysis_retries or 0)),
                "cancel_requested": False,
            },
        )

        def _update(**kwargs: Dict) -> None:
            self._status.update(analysis_id, **kwargs)

        def _worker() -> None:
            try:
                start_ts_local = time.time()
                if self._status.is_cancel_requested(analysis_id):
                    _update(
                        status="canceled",
                        stage="canceled",
                        message="分析已取消",
                        progress=100,
                        duration_seconds=max(0.0, time.time() - start_ts_local),
                    )
                    if self._persist_callback:
                        report_name = self._persist_callback(
                            {
                                "analysis_id": analysis_id,
                                "job_id": job_id,
                                "status": "canceled",
                                "analysis": "分析已取消",
                                "error": "",
                                "provider_used": llm.get("provider", "chatgpt"),
                                "model_used": self.model_used(llm),
                                "prompt_source": llm.get("prompt_source", ""),
                                "token_usage": {"total_tokens": 0},
                                "token_total": 0,
                                "duration_seconds": max(0.0, time.time() - start_ts_local),
                            }
                        )
                        if report_name:
                            _update(analysis_report_name=report_name)
                    return
                with self._jobs_lock:
                    job = self._jobs.get(job_id)
                if report_data_override is not None:
                    report_data = report_data_override
                else:
                    if not job:
                        raise RuntimeError("job not found")
                    report_data = self.load_job_report_json(job)
                devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
                if not isinstance(devices, list) or not devices:
                    raise RuntimeError("报告中没有可分析的设备数据")

                size = max(1, int(batch_size or 5))
                size = min(size, 50)
                total_devices = len(devices)
                total_batches = math.ceil(total_devices / size)
                parallelism = max(1, min(8, int(analysis_parallelism or 1)))
                retries = max(0, min(3, int(analysis_retries or 0)))
                _update(
                    stage="per_device",
                    message=f"开始分批分析，共 {total_devices} 台设备，{total_batches} 批，并发={parallelism}，重试={retries}。",
                    total_devices=total_devices,
                    total_batches=total_batches,
                )
                results: List[Dict] = []
                failed_results: List[Dict] = []
                total_tokens_used = 0
                done_devices = 0
                started_devices = 0
                inflight_devices = 0
                inflight_names: List[str] = []
                progress_lock = threading.Lock()
                completed_chunk_units = 0
                total_chunk_units = max(1, total_devices * max(1, int(large_report_chunk_items or 1))) if large_report_mode else 0

                def _calc_progress() -> int:
                    if large_report_mode:
                        chunk_ratio = completed_chunk_units / max(1, total_chunk_units)
                        device_ratio = done_devices / max(1, total_devices)
                        return min(90, max(1, int(10 + chunk_ratio * 72 + device_ratio * 8)))
                    return min(90, int((done_devices / max(1, total_devices)) * 90))

                def _analyze_device(dev: Dict, total_devices_local: int, done_snapshot: int) -> Dict:
                    nonlocal completed_chunk_units
                    device_name = str(dev.get("device", "unknown"))
                    usage_local = {}
                    total_tokens_local = 0
                    for attempt_idx in range(retries + 1):
                        if self._status.is_cancel_requested(analysis_id):
                            return {
                                "device": device_name,
                                "analysis": f"[设备分析已取消] {device_name}",
                                "token_usage": usage_local or {},
                                "used_tokens": total_tokens_local,
                                "error": "canceled",
                            }
                        try:
                            if large_report_mode:
                                chunk_inputs = analysis_pipeline.build_device_chunk_inputs(
                                    report_data,
                                    dev,
                                    chunk_count=large_report_chunk_items,
                                    force_full=False,
                                )
                                chunk_results: List[Dict] = []
                                for chunk_idx, chunk_input in enumerate(chunk_inputs, start=1):
                                    if self._status.is_cancel_requested(analysis_id):
                                        return {
                                            "device": device_name,
                                            "analysis": f"[设备分析已取消] {device_name}",
                                            "token_usage": usage_local or {},
                                            "used_tokens": total_tokens_local,
                                            "error": "canceled",
                                        }
                                    _update(
                                        message=(
                                            f"分析设备 {device_name} ({done_snapshot + 1}/{total_devices_local}) "
                                            f"分片 {chunk_idx}/{len(chunk_inputs)} ..."
                                        )
                                    )
                                    chunk_analysis, chunk_usage = self.run_llm_analysis(llm, chunk_input)
                                    with progress_lock:
                                        completed_chunk_units += 1
                                        progress_now = _calc_progress()
                                    _update(progress=progress_now)
                                    total_tokens_local += int((chunk_usage or {}).get("total_tokens", 0) or 0)
                                    chunk_results.append(
                                        {
                                            "chunk_index": chunk_idx,
                                            "chunk_count": len(chunk_inputs),
                                            "analysis": chunk_analysis,
                                            "token_usage": chunk_usage or {},
                                        }
                                    )
                                device_summary_input = analysis_pipeline.build_device_chunk_summary_input(
                                    report_data,
                                    dev,
                                    chunk_results,
                                    force_full=False,
                                )
                                analysis_local, usage_local = self.run_llm_analysis(llm, device_summary_input)
                            else:
                                device_input = analysis_pipeline.build_device_analysis_input(
                                    report_data,
                                    dev,
                                    force_full=False,
                                )
                                analysis_local, usage_local = self.run_llm_analysis(llm, device_input)
                            total_tokens_local += int((usage_local or {}).get("total_tokens", 0) or 0)
                            return {
                                "device": device_name,
                                "analysis": analysis_local,
                                "token_usage": usage_local or {},
                                "used_tokens": total_tokens_local,
                                "error": "",
                            }
                        except Exception as exc:
                            if attempt_idx >= retries:
                                return {
                                    "device": device_name,
                                    "analysis": f"[设备分析失败] {device_name}: {exc}",
                                    "token_usage": usage_local or {},
                                    "used_tokens": total_tokens_local,
                                    "error": str(exc),
                                }
                            _update(
                                message=(
                                    f"设备 {device_name} 分析失败，重试 {attempt_idx + 1}/{retries} ..."
                                )
                            )
                            time.sleep(min(2.5, 0.8 * (attempt_idx + 1)))

                for batch_idx in range(total_batches):
                    if self._status.is_cancel_requested(analysis_id):
                        break
                    start = batch_idx * size
                    end = min(total_devices, start + size)
                    batch_devices = devices[start:end]
                    _update(
                        done_batches=batch_idx,
                        message=f"批次 {batch_idx + 1}/{total_batches} 分析中（并发={parallelism}）...",
                    )
                    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
                        future_map = {}
                        for dev in batch_devices:
                            future = executor.submit(_analyze_device, dev, total_devices, done_devices)
                            future_map[future] = dev
                            started_devices += 1
                            inflight_devices += 1
                            device_name = str((dev or {}).get("device", "unknown"))
                            if device_name not in inflight_names:
                                inflight_names.append(device_name)
                            start_progress = min(15, int((started_devices / max(1, total_devices)) * 15))
                            _update(
                                started_devices=started_devices,
                                inflight_devices=inflight_devices,
                                inflight_device_names=inflight_names[:4],
                                progress=start_progress,
                            )
                        for future in concurrent.futures.as_completed(future_map):
                            row = future.result()
                            total_tokens_used += int(row.get("used_tokens", 0) or 0)
                            done_devices += 1
                            inflight_devices = max(0, inflight_devices - 1)
                            done_name = str(row.get("device", "") or "")
                            inflight_names = [n for n in inflight_names if n != done_name]
                            if row.get("error"):
                                failed_results.append({"device": row.get("device", ""), "error": row.get("error", "")})
                            results.append(
                                {
                                    "device": row.get("device", ""),
                                    "analysis": row.get("analysis", ""),
                                    "token_usage": row.get("token_usage", {}) or {},
                                }
                            )
                            with progress_lock:
                                progress = _calc_progress()
                            _update(
                                done_devices=done_devices,
                                inflight_devices=inflight_devices,
                                inflight_device_names=inflight_names[:4],
                                progress=progress,
                            )
                    _update(done_batches=batch_idx + 1)

                if self._status.is_cancel_requested(analysis_id):
                    _update(
                        status="canceled",
                        stage="canceled",
                        message=f"分析已取消（已完成 {done_devices}/{total_devices} 台设备）",
                        progress=100,
                        duration_seconds=max(0.0, time.time() - start_ts_local),
                    )
                    if self._persist_callback:
                        report_name = self._persist_callback(
                            {
                                "analysis_id": analysis_id,
                                "job_id": job_id,
                                "status": "canceled",
                                "analysis": "",
                                "error": "",
                                "provider_used": llm.get("provider", "chatgpt"),
                                "model_used": self.model_used(llm),
                                "prompt_source": llm.get("prompt_source", ""),
                                "token_usage": {"total_tokens": total_tokens_used},
                                "token_total": 0,
                                "duration_seconds": max(0.0, time.time() - start_ts_local),
                            }
                        )
                        if report_name:
                            _update(analysis_report_name=report_name)
                    return

                expected_devices: List[str] = []
                for dev_row in devices:
                    if not isinstance(dev_row, dict):
                        continue
                    dname = str(dev_row.get("device", "") or "").strip()
                    if dname and dname not in expected_devices:
                        expected_devices.append(dname)
                got_devices = set()
                for row in results:
                    dname = str(row.get("device", "") or "").strip()
                    if dname:
                        got_devices.add(dname)
                missing_rows = [d for d in expected_devices if d not in got_devices]
                for d in missing_rows:
                    results.append(
                        {
                            "device": d,
                            "analysis": f"[设备分析失败] {d}: 该设备未生成分析结果（线程执行异常或被提前中断）。",
                            "token_usage": {},
                        }
                    )
                    failed_results.append({"device": d, "error": "missing_analysis_result"})

                _update(stage="summary", message="正在汇总分析...", progress=92)
                summary_input = analysis_pipeline.build_batched_summary_input(
                    report_data,
                    results,
                    force_full=False,
                )
                try:
                    summary_analysis, summary_usage = self.run_llm_analysis(llm, summary_input)
                    total_tokens_used += int((summary_usage or {}).get("total_tokens", 0) or 0)
                except Exception as sum_exc:
                    summary_analysis = f"[汇总分析失败] {sum_exc}"

                patched = analysis_guard.patch_summary_full_coverage(summary_analysis, expected_devices)
                summary_analysis = str(patched.get("summary_text", summary_analysis) or summary_analysis)

                final_text_parts = ["# 逐设备分析结果"]
                for item in results:
                    final_text_parts.append(f"\n## {item.get('device', '')}\n{item.get('analysis', '')}")
                if failed_results:
                    final_text_parts.append("\n# 分析失败设备")
                    for fr in failed_results:
                        final_text_parts.append(f"- {fr.get('device', '')}: {fr.get('error', '')}")
                final_text_parts.append("\n# 汇总分析")
                final_text_parts.append(summary_analysis or "")
                final_text = "\n".join(final_text_parts).strip()

                token_stats = self._add_token_usage(llm["provider"], total_tokens_used)
                report_name_done = ""
                if self._persist_callback:
                    report_name_done = self._persist_callback(
                        {
                            "analysis_id": analysis_id,
                            "job_id": job_id,
                            "status": "done",
                            "analysis": final_text,
                            "error": "",
                            "provider_used": llm.get("provider", "chatgpt"),
                            "model_used": self.model_used(llm),
                            "prompt_source": llm.get("prompt_source", ""),
                            "token_usage": {"total_tokens": total_tokens_used},
                            "token_total": int(token_stats.get("total_tokens", 0)),
                            "duration_seconds": max(0.0, time.time() - start_ts_local),
                        }
                    ) or ""
                _update(
                    status="done",
                    stage="done",
                    message=f"分批分析完成（成功 {len(results) - len(failed_results)} / 总计 {len(results)}）",
                    progress=100,
                    duration_seconds=max(0.0, time.time() - start_ts_local),
                    result=final_text,
                    token_usage={"total_tokens": total_tokens_used},
                    token_total=int(token_stats.get("total_tokens", 0)),
                    analysis_report_name=report_name_done,
                )
            except Exception as exc:
                _update(
                    status="error",
                    stage="error",
                    message="分批分析失败",
                    error=str(exc),
                    duration_seconds=max(0.0, time.time() - start_ts_local),
                )
                if self._persist_callback:
                    report_name = self._persist_callback(
                        {
                            "analysis_id": analysis_id,
                            "job_id": job_id,
                            "status": "error",
                            "analysis": "",
                            "error": str(exc),
                            "provider_used": llm.get("provider", "chatgpt"),
                            "model_used": self.model_used(llm),
                            "prompt_source": llm.get("prompt_source", ""),
                            "token_usage": {"total_tokens": 0},
                            "token_total": 0,
                            "duration_seconds": max(0.0, time.time() - start_ts_local),
                        }
                    )
                    if report_name:
                        _update(analysis_report_name=report_name)

        threading.Thread(target=_worker, daemon=True).start()
        return analysis_id
