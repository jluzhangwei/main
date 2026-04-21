from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .llm_client import model_used, run_analysis
from .prompt_store import merged_system_prompt_catalog, merged_task_prompt_catalog
from .state_store import add_token_usage, load_gpt_config


class AIAnalysisManager:
    def __init__(self, output_root: str = "./netlog_extractor/output") -> None:
        self.output_root = Path(output_root)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_latest: dict[str, str] = {}

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _set_progress(self, task: dict[str, Any], percent: int, text: str) -> None:
        task["progress_percent"] = max(0, min(100, int(percent)))
        task["progress_text"] = str(text or "")
        task["updated_at"] = self._now()

    def _task_analysis_dir(self, task_id: str) -> Path:
        p = self.output_root / task_id / "ai_reports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _persist_analysis_snapshot(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("task_id", "") or "").strip()
        analysis_id = str(task.get("analysis_id", "") or "").strip()
        if not task_id or not analysis_id:
            return
        data = {k: v for k, v in task.items() if not str(k).startswith("_")}
        out_dir = self._task_analysis_dir(task_id)
        latest_json = out_dir / "latest.json"
        latest_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_id = out_dir / "latest_analysis_id.txt"
        latest_id.write_text(analysis_id, encoding="utf-8")
        # Per-analysis json snapshot
        item_json = out_dir / f"analysis_{analysis_id}.json"
        item_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist_analysis_history(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("task_id", "") or "").strip()
        analysis_id = str(task.get("analysis_id", "") or "").strip()
        if not task_id or not analysis_id:
            return
        out_dir = self._task_analysis_dir(task_id)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_provider = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(task.get("provider_used", "") or "-"))[:40]
        safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(task.get("model_used", "") or "-"))[:70]
        base = f"analysis_{ts}_{analysis_id}_{safe_provider}_{safe_model}"
        md_path = out_dir / f"{base}.md"
        json_path = out_dir / f"{base}.json"
        result_text = str(task.get("result", "") or "")
        md_body = result_text if result_text.startswith("#") else f"# AI Analysis Report\n\n{result_text}"
        md_path.write_text(md_body, encoding="utf-8")
        data = {k: v for k, v in task.items() if not str(k).startswith("_")}
        data["history_markdown"] = md_path.name
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_history(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        out_dir = self._task_analysis_dir(task_id)
        items: list[dict[str, Any]] = []
        for p in sorted(out_dir.glob("analysis_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name in {"latest.json"}:
                continue
            if re.fullmatch(r"analysis_[0-9a-f]{12}\.json", p.name):
                # Skip mutable per-analysis snapshot; history should show immutable timestamped records.
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            analysis_id = str(data.get("analysis_id", "") or "")
            created_at = str(data.get("created_at", "") or "")
            status = str(data.get("status", "") or "")
            provider = str(data.get("provider_used", "") or "")
            model = str(data.get("model_used", "") or "")
            md_name = str(data.get("history_markdown", "") or "")
            md_path = (out_dir / md_name) if md_name else None
            items.append(
                {
                    "analysis_id": analysis_id,
                    "created_at": created_at,
                    "status": status,
                    "provider_used": provider,
                    "model_used": model,
                    "json_file": p.name,
                    "markdown_file": md_name if (md_path and md_path.exists()) else "",
                }
            )
            if len(items) >= max(1, int(limit)):
                break
        return items

    def _format_healthcheck_style_progress(self, task: dict[str, Any], stage: str) -> str:
        started = float(task.get("_started_mono", time.monotonic()))
        elapsed = max(0.0, time.monotonic() - started)
        total = int(task.get("device_total", 1) or 1)
        done = int(task.get("device_done", 0) or 0)
        running = int(task.get("device_running", 0) or 0)
        ip = str(task.get("current_device_ip", "-") or "-")
        running_ips = task.get("running_device_ips", [])
        running_display = ip
        if isinstance(running_ips, list) and running_ips:
            chunk_total_map = task.get("_chunk_total_by_ip", {})
            chunk_done_map = task.get("_chunk_done_by_ip", {})
            def _fmt_ip(x: Any) -> str:
                sx = str(x or "-")
                total_chunks = int(chunk_total_map.get(sx, 0) or 0) if isinstance(chunk_total_map, dict) else 0
                done_chunks = int(chunk_done_map.get(sx, 0) or 0) if isinstance(chunk_done_map, dict) else 0
                if total_chunks > 1:
                    return f"{sx}({min(done_chunks, total_chunks)}/{total_chunks})"
                return sx
            if len(running_ips) == 1:
                running_display = _fmt_ip(running_ips[0])
            else:
                running_display = ",".join(_fmt_ip(x) for x in running_ips[:4])
                if len(running_ips) > 4:
                    running_display += f"...(+{len(running_ips)-4})"
        rounds_done = int(task.get("rounds_done", 0) or 0)
        rounds_total = int(task.get("rounds_total", 1) or 1)
        device_index = int(task.get("current_device_index", 1) or 1)
        unit_done = int(task.get("_unit_done", 0) or 0)
        unit_total = int(task.get("_unit_total", 1) or 1)
        active_note = ""
        call_started = float(task.get("_active_call_started", 0.0) or 0.0)
        call_timeout = float(task.get("_active_call_timeout_sec", 0.0) or 0.0)
        if call_started > 0 and call_timeout > 0:
            call_elapsed = max(0.0, time.monotonic() - call_started)
            active_note = f" | 当前调用 {call_elapsed:.1f}s/{call_timeout:.0f}s"
        # Global summary is task-level work; avoid showing stale per-device fields.
        if stage == "全局汇总中":
            return (
                f"阶段: {stage} | 设备总数 {total}，已完成 {done}，进行中 {running} | "
                f"设备批次 {rounds_done}/{rounds_total} | 调用单元 {unit_done}/{unit_total} | "
                f"已耗时 {elapsed:.1f}s{active_note}"
            )
        return (
            f"阶段: {stage} | 设备总数 {total}，已完成 {done}，进行中 {running}，设备: {running_display} | "
            f"设备批次 {rounds_done}/{rounds_total} | 调用单元 {unit_done}/{unit_total} | "
            f"已耗时 {elapsed:.1f}s | 设备 {device_index}{active_note}"
        )

    def _build_llm_input(self, cfg: dict[str, Any]) -> dict[str, str]:
        provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
        system_prompts = merged_system_prompt_catalog()
        task_prompts = merged_task_prompt_catalog()

        system_key = str(cfg.get("selected_system_prompt") or "网络日志诊断专家-严格模式")
        task_key = str(cfg.get("selected_task_prompt") or "日志异常诊断-标准版")
        system_base = system_prompts.get(system_key, next(iter(system_prompts.values()), ""))
        task_base = task_prompts.get(task_key, next(iter(task_prompts.values()), ""))

        system_extra = str(cfg.get("system_prompt_extra") or "").strip()
        task_extra = str(cfg.get("task_prompt_extra") or "").strip()

        system_text = system_base + ("\n\n[Extra System Constraints]\n" + system_extra if system_extra else "")
        task_text = task_base + ("\n\n[Extra Task Requirements]\n" + task_extra if task_extra else "")

        api_key = ""
        if provider == "chatgpt":
            api_key = str(cfg.get("chatgpt_api_key") or "")
        elif provider == "deepseek":
            api_key = str(cfg.get("deepseek_api_key") or "")
        elif provider == "qwen":
            api_key = str(cfg.get("qwen_api_key") or "")
        elif provider == "gemini":
            api_key = str(cfg.get("gemini_api_key") or "")
        elif provider == "nvidia":
            api_key = str(cfg.get("nvidia_api_key") or "")

        return {
            "provider": provider,
            "api_key": api_key,
            "chatgpt_model": str(cfg.get("chatgpt_model") or ""),
            "local_base_url": str(cfg.get("local_base_url") or ""),
            "local_model": str(cfg.get("local_model") or ""),
            "deepseek_model": str(cfg.get("deepseek_model") or ""),
            "qwen_model": str(cfg.get("qwen_model") or ""),
            "qwen_base_url": str(cfg.get("qwen_base_url") or ""),
            "gemini_model": str(cfg.get("gemini_model") or ""),
            "nvidia_model": str(cfg.get("nvidia_model") or ""),
            "system_prompt_text": system_text,
            "task_prompt_text": task_text,
            "system_prompt_key": system_key,
            "task_prompt_key": task_key,
        }

    def _collect_task_report_text(self, task_id: str) -> str:
        task_dir = self.output_root / task_id
        summary_path = task_dir / "summary.json"
        if not summary_path.exists():
            raise RuntimeError("summary.json not found")
        summary = summary_path.read_text(encoding="utf-8")

        sections = [f"# Task Summary\n{summary}\n"]
        for dev_dir in sorted([p for p in task_dir.iterdir() if p.is_dir()]):
            filtered_path = dev_dir / "filtered.log"
            raw_path = dev_dir / "raw.log"
            meta_path = dev_dir / "meta.json"
            sections.append(f"\n## Device {dev_dir.name}\n")
            if meta_path.exists():
                sections.append("### meta.json\n" + meta_path.read_text(encoding="utf-8") + "\n")
            if filtered_path.exists():
                sections.append("### filtered.log\n" + filtered_path.read_text(encoding="utf-8") + "\n")
            elif raw_path.exists():
                sections.append("### raw.log\n" + raw_path.read_text(encoding="utf-8") + "\n")
        return "\n".join(sections)

    def _device_text(self, task_id: str, device_id: str) -> str:
        dev_dir = self.output_root / task_id / device_id
        sections = [f"# Device {device_id}"]
        meta = dev_dir / "meta.json"
        filtered_path = dev_dir / "filtered.log"
        raw_path = dev_dir / "raw.log"
        if meta.exists():
            sections.append("## meta.json\n" + meta.read_text(encoding="utf-8"))
        if filtered_path.exists():
            sections.append("## filtered.log\n" + filtered_path.read_text(encoding="utf-8"))
        elif raw_path.exists():
            sections.append("## raw.log\n" + raw_path.read_text(encoding="utf-8"))
        else:
            sections.append("## notice\nno log file found")
        text = "\n\n".join(sections)
        return self._shrink_text(text)

    def _shrink_text(self, text: str, max_chars: int = 24000) -> str:
        if len(text) <= max_chars:
            return text
        head = text[: max_chars // 2]
        tail = text[-(max_chars // 2) :]
        return head + "\n\n...[TRUNCATED]...\n\n" + tail

    def _split_chunks(
        self,
        text: str,
        max_tokens_per_chunk: int,
        max_chunks_per_device: int,
        chunk_strategy: str,
    ) -> list[str]:
        lines = text.splitlines()
        if len(lines) <= 80:
            return [text]
        budget_chars = max(1200, int(max_tokens_per_chunk * 4))
        chunks: list[str] = []

        def _pack_lines(input_lines: list[str], local_budget: int) -> list[str]:
            out: list[str] = []
            current: list[str] = []
            current_chars = 0

            def flush() -> None:
                nonlocal current, current_chars
                if current:
                    part = "\n".join(current).strip()
                    if part:
                        out.append(self._shrink_text(part, max_chars=max(3000, local_budget + 1200)))
                current = []
                current_chars = 0

            for line in input_lines:
                line_len = len(line) + 1
                if current and current_chars + line_len > local_budget:
                    flush()
                current.append(line)
                current_chars += line_len
            flush()
            return out

        def _parse_line_dt(line: str) -> datetime | None:
            for p in self._TS_PATTERNS:
                m = p.search(line)
                if not m:
                    continue
                try:
                    y = int(m.groupdict().get("y") or datetime.now().year)
                    mon = self._MON.get(str(m.group("m")).lower(), 0)
                    if not mon:
                        continue
                    d = int(m.group("d"))
                    hh = int(m.group("h"))
                    mm = int(m.group("mi"))
                    ss = int(m.group("s"))
                    return datetime(y, mon, d, hh, mm, ss)
                except Exception:
                    continue
            return None

        def _event_key(line: str) -> str:
            m = re.search(r"%%\d+[A-Z]+/\d+/([A-Z0-9_]+)", line)
            if m:
                return m.group(1)
            m = re.search(r"%[A-Z0-9_-]+-[A-Z0-9_-]+-\d+-([A-Z0-9_]+)", line)
            if m:
                return m.group(1)
            m = re.search(r"\b([A-Za-z][A-Za-z0-9_-]{2,20}):", line)
            if m:
                return m.group(1).lower()
            return "misc"

        strategy = (chunk_strategy or "hybrid").strip().lower()
        if strategy not in {"hybrid", "time", "event"}:
            strategy = "hybrid"

        if strategy == "time":
            window_sec = 15 * 60
            buckets: list[list[str]] = []
            current_bucket: list[str] = []
            bucket_start: datetime | None = None
            for line in lines:
                dt = _parse_line_dt(line)
                if dt is None:
                    current_bucket.append(line)
                    continue
                if bucket_start is None:
                    bucket_start = dt
                    current_bucket.append(line)
                    continue
                if abs((dt - bucket_start).total_seconds()) > window_sec and current_bucket:
                    buckets.append(current_bucket)
                    current_bucket = [line]
                    bucket_start = dt
                else:
                    current_bucket.append(line)
            if current_bucket:
                buckets.append(current_bucket)
            for b in buckets:
                chunks.extend(_pack_lines(b, budget_chars))
        elif strategy == "event":
            grouped: dict[str, list[str]] = {}
            order: list[str] = []
            for line in lines:
                k = _event_key(line)
                if k not in grouped:
                    grouped[k] = []
                    order.append(k)
                grouped[k].append(line)
            for k in order:
                chunks.extend(_pack_lines(grouped[k], budget_chars))
        else:
            # hybrid: time-aware packing, but force flush on major event-key switch to keep semantics focused.
            current: list[str] = []
            cur_chars = 0
            last_key = ""
            last_dt: datetime | None = None
            for line in lines:
                key = _event_key(line)
                dt = _parse_line_dt(line)
                line_len = len(line) + 1
                key_switch = bool(last_key and key != last_key and cur_chars > int(budget_chars * 0.45))
                time_jump = bool(
                    last_dt is not None and dt is not None and abs((dt - last_dt).total_seconds()) > (20 * 60)
                )
                if current and (cur_chars + line_len > budget_chars or key_switch or time_jump):
                    chunks.extend(_pack_lines(current, budget_chars))
                    current = []
                    cur_chars = 0
                current.append(line)
                cur_chars += line_len
                last_key = key
                if dt is not None:
                    last_dt = dt
            if current:
                chunks.extend(_pack_lines(current, budget_chars))

        if len(chunks) > max_chunks_per_device:
            chunks = chunks[:max_chunks_per_device]
        if not chunks:
            chunks = [self._shrink_text(text, max_chars=max(3000, budget_chars + 1200))]
        return chunks

    def _estimate_timeout_sec(self, text: str, floor: int = 60, ceiling: int = 240) -> int:
        # Roughly 1 token ~= 4 chars; scale timeout by payload size to avoid tiny logs waiting 180s.
        tokens = max(1, int(len(text) / 4))
        # Empirical budget: base 18s + 1s per ~220 tokens, then clamp.
        est = int(18 + tokens / 220)
        return max(floor, min(ceiling, est))

    async def _call_llm_with_retry(
        self,
        llm: dict[str, str],
        report_text: str,
        retries: int,
        timeout_sec: int = 180,
    ) -> tuple[str, dict[str, int]]:
        last_exc: Exception | None = None
        for i in range(retries + 1):
            try:
                return await asyncio.wait_for(asyncio.to_thread(run_analysis, llm, report_text), timeout=timeout_sec)
            except asyncio.TimeoutError:
                last_exc = RuntimeError(f"LLM call timeout after {timeout_sec}s (attempt {i+1}/{retries+1})")
            except Exception as exc:
                last_exc = exc
                if i < retries:
                    await asyncio.sleep(0.8)
        if last_exc is None:
            raise RuntimeError("llm analysis failed")
        msg = str(last_exc).strip() or repr(last_exc)
        raise RuntimeError(msg)

    def start(self, task_id: str, devices: list[dict[str, str]] | None = None) -> str:
        active = self.get_active_by_task(task_id)
        if active:
            return active["analysis_id"]
        analysis_id = uuid.uuid4().hex[:12]
        device_list = list(devices or [])
        total = len(device_list) if device_list else 1
        self._tasks[analysis_id] = {
            "analysis_id": analysis_id,
            "task_id": task_id,
            "status": "running",
            "created_at": self._now(),
            "updated_at": self._now(),
            "result": "",
            "error": "",
            "provider_used": "",
            "model_used": "",
            "token_stats": {},
            "progress_percent": 0,
            "progress_text": "",
            "device_total": total,
            "device_done": 0,
            "device_running": 0,
            "current_device_ip": (device_list[0].get("ip") if device_list else "-") or "-",
            "current_device_index": 1,
            "rounds_done": 0,
            "rounds_total": 1,
            "running_device_ips": [],
            "_started_mono": time.monotonic(),
            "_devices": device_list,
            "_chunk_total_by_ip": {},
            "_chunk_done_by_ip": {},
        }
        self._task_latest[task_id] = analysis_id
        self._set_progress(
            self._tasks[analysis_id],
            1,
            self._format_healthcheck_style_progress(self._tasks[analysis_id], "准备中"),
        )
        self._persist_analysis_snapshot(self._tasks[analysis_id])
        asyncio.create_task(self._run(analysis_id, task_id))
        return analysis_id

    async def _run(self, analysis_id: str, task_id: str) -> None:
        task = self._tasks[analysis_id]
        try:
            cfg = load_gpt_config()
            llm_base = self._build_llm_input(cfg)
            provider = llm_base["provider"]
            task["provider_used"] = provider
            task["model_used"] = model_used(llm_base)
            task["updated_at"] = self._now()

            batched = bool(int(cfg.get("batched_analysis", 0) or 0))
            fragmented = bool(int(cfg.get("fragmented_analysis", 0) or 0))
            parallelism = max(1, int(cfg.get("analysis_parallelism", 2) or 2))
            retries = max(0, int(cfg.get("analysis_retries", 1) or 1))
            max_tokens_per_chunk = max(800, int(cfg.get("max_tokens_per_chunk", 4500) or 4500))
            max_chunks_per_device = max(
                1,
                int(
                    cfg.get(
                        "max_chunks_per_device",
                        cfg.get("large_report_chunk_items", 12),
                    )
                    or 12
                ),
            )
            chunk_strategy = str(cfg.get("chunk_strategy", "hybrid") or "hybrid").strip().lower()
            if chunk_strategy not in {"hybrid", "time", "event"}:
                chunk_strategy = "hybrid"
            chunk_parallelism = max(1, int(cfg.get("chunk_parallelism", 1) or 1))
            max_call_timeout = max(60, min(600, int(cfg.get("llm_call_timeout_sec", 240) or 240)))

            devices = task.get("_devices", []) or []
            if not devices:
                # fallback from output dirs
                task_dir = self.output_root / task_id
                devices = [
                    {"device_id": p.name, "ip": p.name, "index": i + 1}
                    for i, p in enumerate(sorted([x for x in task_dir.iterdir() if x.is_dir()]))
                ]
            device_total = len(devices) if devices else 1
            task["device_total"] = device_total
            task["rounds_total"] = max(1, math.ceil(device_total / parallelism))

            # Build real unit plan.
            device_plans: list[dict[str, Any]] = []
            total_units = 0
            if batched and devices:
                for d in devices:
                    dtext = self._device_text(task_id, str(d.get("device_id") or ""))
                    chunks = (
                        self._split_chunks(
                            dtext,
                            max_tokens_per_chunk=max_tokens_per_chunk,
                            max_chunks_per_device=max_chunks_per_device,
                            chunk_strategy=chunk_strategy,
                        )
                        if fragmented
                        else [dtext]
                    )
                    plan = {"device": d, "text": dtext, "chunks": chunks}
                    device_plans.append(plan)
                    if fragmented:
                        # Only do an extra per-device summary call when there are multiple chunks.
                        total_units += len(chunks) + (1 if len(chunks) > 1 else 0)
                    else:
                        total_units += 1  # one call per device
                total_units += 1  # global summary
            else:
                total_units = 1
            task["_unit_total"] = max(1, total_units)
            task["_unit_done"] = 0

            lock = asyncio.Lock()
            running_ips: set[str] = set()
            total_token_used = 0
            device_summaries: list[str] = []

            async def update_progress(stage: str) -> None:
                nonlocal running_ips
                unit_done = int(task.get("_unit_done", 0) or 0)
                unit_total = int(task.get("_unit_total", 1) or 1)
                active_boost = 0.0
                if int(task.get("device_running", 0) or 0) > 0:
                    call_started = float(task.get("_active_call_started", 0.0) or 0.0)
                    timeout_sec = float(task.get("_active_call_timeout_sec", 180.0) or 180.0)
                    if call_started > 0:
                        elapsed_call = max(0.0, time.monotonic() - call_started)
                        # Keep growing across the whole timeout window, avoiding long flat plateaus.
                        # Reserve 0.02 so this unit never falsely appears complete before response returns.
                        active_boost = min(0.98, (elapsed_call / max(20.0, timeout_sec)) * 0.98)
                    else:
                        active_boost = 0.08
                effective_done = min(float(unit_total), float(unit_done) + active_boost)
                percent = min(99, int((effective_done / unit_total) * 100)) if unit_total > 0 else 0
                if int(task.get("device_running", 0) or 0) > 0 and percent <= 0:
                    percent = 1
                task["device_running"] = len(running_ips)
                task["running_device_ips"] = sorted(list(running_ips))
                task["rounds_done"] = min(task["rounds_total"], task["device_done"] // parallelism)
                self._set_progress(task, percent, self._format_healthcheck_style_progress(task, stage))

            if not batched or not device_plans:
                self._set_progress(task, 20, self._format_healthcheck_style_progress(task, "汇总任务日志"))
                report_text = self._collect_task_report_text(task_id)
                self._set_progress(task, 55, self._format_healthcheck_style_progress(task, "设备分析中"))
                timeout_sec = self._estimate_timeout_sec(report_text, floor=60, ceiling=max_call_timeout)
                task["_active_call_started"] = time.monotonic()
                task["_active_call_timeout_sec"] = float(timeout_sec)
                analysis, usage = await self._call_llm_with_retry(
                    llm_base, report_text, retries, timeout_sec=timeout_sec
                )
                task["_active_call_started"] = 0.0
                task["_active_call_timeout_sec"] = 0.0
                total_token_used += int((usage or {}).get("total_tokens", 0) or 0)
                task["_unit_done"] = 1
                task["device_done"] = task["device_total"]
                task["device_running"] = 0
                task["rounds_done"] = task["rounds_total"]
                task["result"] = analysis
            else:
                sem = asyncio.Semaphore(parallelism)

                async def ticker(stop_evt: asyncio.Event, stage: str) -> None:
                    while not stop_evt.is_set():
                        async with lock:
                            await update_progress(stage)
                        await asyncio.sleep(0.8)

                async def run_device(plan: dict[str, Any]) -> None:
                    nonlocal total_token_used
                    d = plan["device"]
                    d_id = str(d.get("device_id") or "")
                    d_ip = str(d.get("ip") or "-")
                    d_idx = int(d.get("index") or 1)
                    chunks: list[str] = plan["chunks"]
                    async with sem:
                        stop_evt = asyncio.Event()
                        tick_task = asyncio.create_task(ticker(stop_evt, "设备分析中"))
                        async with lock:
                            running_ips.add(d_ip)
                            task["current_device_ip"] = d_ip
                            task["current_device_index"] = d_idx
                            task["_chunk_total_by_ip"][d_ip] = len(chunks)
                            task["_chunk_done_by_ip"][d_ip] = 0
                            await update_progress("设备分析中")
                        try:
                            chunk_results: list[str] = [""] * max(1, len(chunks))

                            async def analyze_one_chunk(i: int, chunk: str) -> tuple[int, str, int]:
                                llm = dict(llm_base)
                                llm["task_prompt_text"] = (
                                    llm_base.get("task_prompt_text", "")
                                    + f"\n\n[分片任务] 设备 {d_ip} ({d_id}) 分片 {i+1}/{len(chunks)}，请给出该分片结论。"
                                )
                                timeout_sec = self._estimate_timeout_sec(chunk, floor=60, ceiling=max_call_timeout)
                                task["_active_call_started"] = time.monotonic()
                                task["_active_call_timeout_sec"] = float(timeout_sec)
                                text, usage = await self._call_llm_with_retry(
                                    llm, chunk, retries, timeout_sec=timeout_sec
                                )
                                token_used = int((usage or {}).get("total_tokens", 0) or 0)
                                return i, text, token_used

                            if fragmented and len(chunks) > 1 and chunk_parallelism > 1:
                                inner_sem = asyncio.Semaphore(min(chunk_parallelism, len(chunks)))

                                async def run_with_sem(i: int, chunk: str) -> tuple[int, str, int]:
                                    async with inner_sem:
                                        return await analyze_one_chunk(i, chunk)

                                results = await asyncio.gather(
                                    *(run_with_sem(i, chunk) for i, chunk in enumerate(chunks))
                                )
                                task["_active_call_started"] = 0.0
                                task["_active_call_timeout_sec"] = 0.0
                                for i, text, token_used in results:
                                    chunk_results[i] = text
                                    async with lock:
                                        total_token_used += token_used
                                        task["_unit_done"] += 1
                                        task["_chunk_done_by_ip"][d_ip] = int(task["_chunk_done_by_ip"].get(d_ip, 0) or 0) + 1
                                        await update_progress("设备分析中")
                            else:
                                for i, chunk in enumerate(chunks):
                                    i2, text, token_used = await analyze_one_chunk(i, chunk)
                                    task["_active_call_started"] = 0.0
                                    task["_active_call_timeout_sec"] = 0.0
                                    chunk_results[i2] = text
                                    async with lock:
                                        total_token_used += token_used
                                        task["_unit_done"] += 1
                                        task["_chunk_done_by_ip"][d_ip] = int(task["_chunk_done_by_ip"].get(d_ip, 0) or 0) + 1
                                        await update_progress("设备分析中")

                            if fragmented and len(chunks) > 1:
                                # summarize chunk results per device
                                llm = dict(llm_base)
                                llm["task_prompt_text"] = (
                                    llm_base.get("task_prompt_text", "")
                                    + f"\n\n[设备汇总] 请汇总设备 {d_ip} ({d_id}) 各分片结论。"
                                )
                                merged_text = "\n\n".join(
                                    [f"### 分片{i+1}\n{t}" for i, t in enumerate(chunk_results)]
                                )
                                timeout_sec = self._estimate_timeout_sec(merged_text, floor=60, ceiling=max_call_timeout)
                                task["_active_call_started"] = time.monotonic()
                                task["_active_call_timeout_sec"] = float(timeout_sec)
                                summary, usage = await self._call_llm_with_retry(
                                    llm, merged_text, retries, timeout_sec=timeout_sec
                                )
                                task["_active_call_started"] = 0.0
                                task["_active_call_timeout_sec"] = 0.0
                                async with lock:
                                    total_token_used += int((usage or {}).get("total_tokens", 0) or 0)
                                    task["_unit_done"] += 1
                                    task["device_done"] += 1
                                    await update_progress("设备分析中")
                                device_summaries.append(f"## 设备 {d_ip} ({d_id})\n{summary}")
                            else:
                                # Use single chunk result directly (or non-fragmented mode).
                                summary = chunk_results[0] if chunk_results else ""
                                async with lock:
                                    task["device_done"] += 1
                                    await update_progress("设备分析中")
                                device_summaries.append(f"## 设备 {d_ip} ({d_id})\n{summary}")
                        finally:
                            async with lock:
                                running_ips.discard(d_ip)
                                task["_chunk_total_by_ip"].pop(d_ip, None)
                                task["_chunk_done_by_ip"].pop(d_ip, None)
                                if running_ips:
                                    current = next(iter(running_ips))
                                    task["current_device_ip"] = current
                                    for dev in devices:
                                        if str(dev.get("ip") or "-") == current:
                                            task["current_device_index"] = int(dev.get("index") or 1)
                                            break
                                await update_progress("设备分析中")
                            stop_evt.set()
                            await tick_task

                await asyncio.gather(*(run_device(p) for p in device_plans))

                # final global summary
                self._set_progress(task, min(99, int((task["_unit_done"] / task["_unit_total"]) * 100)), self._format_healthcheck_style_progress(task, "全局汇总中"))
                global_report = "\n\n".join(device_summaries)
                llm = dict(llm_base)
                llm["task_prompt_text"] = llm_base.get("task_prompt_text", "") + "\n\n[全局汇总] 请汇总所有设备结论并给出处置优先级。"
                timeout_sec = self._estimate_timeout_sec(global_report, floor=60, ceiling=max_call_timeout)
                task["_active_call_started"] = time.monotonic()
                task["_active_call_timeout_sec"] = float(timeout_sec)
                global_summary, usage = await self._call_llm_with_retry(
                    llm, self._shrink_text(global_report), retries, timeout_sec=timeout_sec
                )
                task["_active_call_started"] = 0.0
                task["_active_call_timeout_sec"] = 0.0
                total_token_used += int((usage or {}).get("total_tokens", 0) or 0)
                task["_unit_done"] += 1
                task["device_done"] = task["device_total"]
                task["device_running"] = 0
                task["rounds_done"] = task["rounds_total"]
                task["result"] = "# 全局汇总\n" + global_summary + "\n\n" + "\n\n".join(device_summaries)

            tokens = add_token_usage(provider, int(total_token_used))
            task["status"] = "success"
            task["token_stats"] = tokens
            self._set_progress(task, 100, self._format_healthcheck_style_progress(task, "完成"))
            self._persist_analysis_snapshot(task)
            self._persist_analysis_history(task)

            out_path = self.output_root / task_id / "analysis.json"
            out_path.write_text(
                json.dumps({k: v for k, v in task.items() if not str(k).startswith("_")}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            task["status"] = "failed"
            reason = str(exc).strip() or repr(exc)
            task["error"] = reason
            task["device_running"] = 0
            self._set_progress(
                task,
                max(1, int(task.get("progress_percent", 0) or 1)),
                self._format_healthcheck_style_progress(task, "失败"),
            )
            self._persist_analysis_snapshot(task)
            self._persist_analysis_history(task)

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        data = self._tasks.get(analysis_id)
        if not data:
            return None
        return {k: v for k, v in data.items() if not str(k).startswith("_")}

    def get_active_by_task(self, task_id: str) -> dict[str, Any] | None:
        # In-memory running task first.
        for aid, data in self._tasks.items():
            if str(data.get("task_id", "") or "") != str(task_id):
                continue
            if str(data.get("status", "") or "") == "running":
                return {"analysis_id": aid, **{k: v for k, v in data.items() if not str(k).startswith("_")}}
        return None

    def get_latest_by_task(self, task_id: str) -> dict[str, Any] | None:
        # Prefer in-memory latest for this task.
        latest_id = self._task_latest.get(str(task_id))
        if latest_id and latest_id in self._tasks:
            data = self._tasks[latest_id]
            return {"analysis_id": latest_id, **{k: v for k, v in data.items() if not str(k).startswith("_")}}
        # Fallback to persisted latest snapshot.
        latest_json = self._task_analysis_dir(task_id) / "latest.json"
        if not latest_json.exists():
            return None
        try:
            data = json.loads(latest_json.read_text(encoding="utf-8"))
        except Exception:
            return None
        aid = str(data.get("analysis_id", "") or "")
        if aid:
            self._task_latest[str(task_id)] = aid
        return {"analysis_id": aid, **data}
    _TS_PATTERNS = [
        # 2026 Feb 18 14:01:27.855
        re.compile(r"(?P<y>\d{4})\s+(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
        # Feb  3 2026 23:09:25
        re.compile(r"(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<y>\d{4})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
        # Feb 19 00:10:47 (year not present)
        re.compile(r"(?P<m>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"),
    ]
    _MON = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
