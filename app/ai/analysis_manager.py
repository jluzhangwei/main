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
from .prompt_runtime import (
    build_llm_input_from_config,
    compact_runtime_prompt,
    final_runtime_prompt,
    lang_text,
    normalize_analysis_language,
)
from .semantic_compression import build_semantic_package, normalize_strategy
from .state_store import add_token_usage, load_gpt_config


class AIAnalysisManager:
    def __init__(self, output_root: str = "./output") -> None:
        self.output_root = Path(output_root)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_latest: dict[str, str] = {}
        self._async_tasks: dict[str, asyncio.Task[Any]] = {}
        self._summary_device_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _lang(self, cfg: dict[str, Any] | None = None) -> str:
        return normalize_analysis_language((cfg or {}).get("analysis_language", "zh"))

    def _lang_text(self, lang: str, zh: str, en: str) -> str:
        return lang_text(lang, zh, en)

    def _set_progress(self, task: dict[str, Any], percent: int, text: str) -> None:
        task["progress_percent"] = max(0, min(100, int(percent)))
        task["progress_text"] = str(text or "")
        task["updated_at"] = self._now()

    def _check_cancelled(self, task: dict[str, Any], stage: str = "已停止") -> None:
        if bool(task.get("_cancel_requested")):
            raise asyncio.CancelledError(str(stage or "已停止"))

    def _task_analysis_dir(self, task_id: str) -> Path:
        p = self.output_root / task_id / "ai_reports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _is_device_output_dir(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        if path.name == "ai_reports":
            return False
        return any((path / name).exists() for name in ("meta.json", "filtered.log", "raw.log", "filtered_sql.log", "raw_sql.log"))

    def _normalize_device_ids(self, task_id: str, device_ids: list[str] | None = None) -> list[str]:
        task_dir = self.output_root / task_id
        if not task_dir.exists():
            return []
        existing = [p.name for p in sorted([x for x in task_dir.iterdir() if self._is_device_output_dir(x)])]
        if not device_ids:
            return existing
        wanted = {str(x or "").strip() for x in device_ids if str(x or "").strip()}
        return [name for name in existing if name in wanted]

    def _read_device_meta(self, task_id: str, device_id: str) -> dict[str, Any]:
        meta_path = self.output_root / task_id / device_id / "meta.json"
        if not meta_path.exists():
            return {}
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _read_task_summary_devices(self, task_id: str) -> dict[str, dict[str, Any]]:
        if task_id in self._summary_device_cache:
            return self._summary_device_cache[task_id]
        summary_path = self.output_root / task_id / "summary.json"
        mapping: dict[str, dict[str, Any]] = {}
        if summary_path.exists():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                for item in list((data or {}).get("devices") or []):
                    if not isinstance(item, dict):
                        continue
                    device_id = str(item.get("device_id") or "").strip()
                    if device_id:
                        mapping[device_id] = item
            except Exception:
                mapping = {}
        self._summary_device_cache[task_id] = mapping
        return mapping

    def _device_identity(self, task_id: str, device_id: str, fallback_ip: str = "") -> dict[str, str]:
        summary_item = self._read_task_summary_devices(task_id).get(device_id, {})
        meta_data = self._read_device_meta(task_id, device_id)
        device_name = str(
            summary_item.get("device_name")
            or meta_data.get("device_name")
            or (((meta_data.get("sql_device_meta") or {}) if isinstance(meta_data.get("sql_device_meta"), dict) else {}).get("device_name"))
            or ""
        ).strip()
        device_ip = str(
            summary_item.get("device_ip")
            or meta_data.get("device_ip")
            or meta_data.get("ip")
            or fallback_ip
            or ""
        ).strip()
        vendor = str(
            summary_item.get("vendor")
            or meta_data.get("vendor")
            or (((meta_data.get("sql_device_meta") or {}) if isinstance(meta_data.get("sql_device_meta"), dict) else {}).get("vendor"))
            or ""
        ).strip()
        parts = []
        if device_name:
            parts.append(device_name)
        if device_ip:
            parts.append(device_ip)
        parts.append(device_id)
        display = " | ".join([x for x in parts if x])
        short_display = device_name or device_ip or device_id
        return {
            "device_id": device_id,
            "device_name": device_name,
            "device_ip": device_ip,
            "vendor": vendor,
            "display": display or device_id,
            "short_display": short_display or device_id,
        }

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
        lang = self._lang(task)
        default_title = "# AI Analysis Report" if str(lang).lower().startswith("en") else "# AI 分析报告"
        md_body = result_text if result_text.startswith("#") else f"{default_title}\n\n{result_text}"
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
        return build_llm_input_from_config(cfg)

    def _compression_strategy(self, cfg: dict[str, Any] | None = None) -> str:
        raw = (cfg or {}).get("text_compression_strategy", "")
        if not raw and "text_compression_enabled" in (cfg or {}):
            raw = "group_repeats" if str((cfg or {}).get("text_compression_enabled", 0)).strip().lower() in {"1", "true", "yes", "on", "checked"} else "off"
        return normalize_strategy(raw)

    def _sql_log_inclusion_mode(self, cfg: dict[str, Any] | None = None) -> str:
        raw = str((cfg or {}).get("sql_log_inclusion_mode", "final_only") or "final_only").strip().lower()
        if raw not in {"final_only", "with_sql_filtered", "with_sql_filtered_force", "with_sql_raw_and_filtered"}:
            return "final_only"
        return raw

    def _parse_runtime_summary_json(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else raw
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
        try:
            payload = json.loads(candidate)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _compact_global_summary_input(self, device_summaries: list[dict[str, Any]]) -> str:
        compact_devices: list[dict[str, Any]] = []
        for item in device_summaries:
            compact_devices.append(
                {
                    "device_id": item.get("device_id", ""),
                    "device_name": item.get("device_name", ""),
                    "device_ip": item.get("device_ip", ""),
                    "device_label": item.get("device_label", ""),
                    "verdict": item.get("verdict", ""),
                    "anomalies": list(item.get("anomalies", []) or [])[:4],
                    "evidence": list(item.get("evidence", []) or [])[:5],
                    "actions": list(item.get("actions", []) or [])[:3],
                }
            )
        return json.dumps({"devices": compact_devices}, ensure_ascii=False, indent=2)

    def _render_device_summary_appendix(self, device_summaries: list[dict[str, Any]], *, lang: str) -> str:
        is_en = str(lang).lower().startswith("en")
        lines: list[str] = ["# Device Summaries" if is_en else "# 设备摘要"]
        for item in device_summaries:
            lines.append("")
            label = str(item.get('device_label') or item.get('device_name') or item.get('device_ip') or item.get('device_id') or '-')
            lines.append(f"## {label}")
            verdict = str(item.get("verdict", "") or "").strip()
            if verdict:
                lines.append(f"- {'Verdict' if is_en else '结论'}: {verdict}")
            anomalies = list(item.get("anomalies", []) or [])
            if anomalies:
                lines.append(f"- {'Anomalies' if is_en else '异常'}:")
                for anomaly in anomalies[:6]:
                    if isinstance(anomaly, dict):
                        severity = anomaly.get("severity", "")
                        event = anomaly.get("event", "")
                        obj = anomaly.get("object", "")
                        time_range = anomaly.get("time_range", "")
                        reason = anomaly.get("reason", "")
                        parts = [x for x in [severity, event, obj, time_range, reason] if str(x or "").strip()]
                        lines.append(f"  - {' | '.join(str(x) for x in parts)}")
                    else:
                        lines.append(f"  - {anomaly}")
            evidence = list(item.get("evidence", []) or [])
            if evidence:
                lines.append(f"- {'Evidence' if is_en else '证据'}:")
                for entry in evidence[:6]:
                    lines.append(f"  - {entry}")
            actions = list(item.get("actions", []) or [])
            if actions:
                lines.append(f"- {'Actions' if is_en else '建议'}:")
                for entry in actions[:5]:
                    lines.append(f"  - {entry}")
            fallback = str(item.get("raw_summary", "") or "").strip()
            if not verdict and fallback:
                lines.append("```text")
                lines.append(self._shrink_text(fallback, max_chars=1200))
                lines.append("```")
        return "\n".join(lines).strip()

    def _sql_sections(
        self,
        *,
        log_text: str,
        sql_filtered_path: Path,
        sql_raw_path: Path,
        sql_log_inclusion_mode: str,
        analysis_time_start: str = "",
        analysis_time_end: str = "",
    ) -> tuple[list[str], list[str]]:
        sections: list[str] = []
        attached: list[str] = []
        include_filtered = sql_log_inclusion_mode in {
            "with_sql_filtered",
            "with_sql_filtered_force",
            "with_sql_raw_and_filtered",
        }
        force_filtered = sql_log_inclusion_mode in {"with_sql_filtered_force", "with_sql_raw_and_filtered"}
        if include_filtered and sql_filtered_path.exists():
            sql_filtered_text = self._filter_log_text_by_time_range(
                sql_filtered_path.read_text(encoding="utf-8"),
                analysis_time_start,
                analysis_time_end,
            )
            if sql_filtered_text and (force_filtered or sql_filtered_text != log_text):
                sections.append(f"## {sql_filtered_path.name}\n" + sql_filtered_text)
                attached.append(sql_filtered_path.name)
        if sql_log_inclusion_mode == "with_sql_raw_and_filtered" and sql_raw_path.exists():
            sql_raw_text = self._filter_log_text_by_time_range(
                sql_raw_path.read_text(encoding="utf-8"),
                analysis_time_start,
                analysis_time_end,
            )
            if sql_raw_text:
                sections.append(f"## {sql_raw_path.name}\n" + sql_raw_text)
                attached.append(sql_raw_path.name)
        return sections, attached

    def _ai_meta_payload(self, meta_data: dict[str, Any], *, source_name: str) -> dict[str, Any]:
        keep_keys = [
            "device_name",
            "vendor",
            "os_family",
            "model",
            "version",
            "log_source",
            "sql_only_mode",
            "sql_query_mode",
            "reference_time",
            "device_time",
            "offset_seconds",
            "device_start",
            "device_end",
            "log_time_min",
            "log_time_max",
            "hits_count",
            "blocks_count",
        ]
        out: dict[str, Any] = {k: meta_data.get(k) for k in keep_keys if k in meta_data}
        sql_meta = meta_data.get("sql_device_meta")
        if isinstance(sql_meta, dict):
            out["sql_device_meta"] = {
                "device_name": sql_meta.get("device_name"),
                "vendor": sql_meta.get("vendor"),
                "table": sql_meta.get("table"),
            }
        out["evidence_source"] = source_name
        return out

    def _build_device_report_text(
        self,
        task_id: str,
        device_id: str,
        compression_strategy: str = "template_vars",
        sql_log_inclusion_mode: str = "final_only",
        analysis_time_start: str = "",
        analysis_time_end: str = "",
        persist_artifacts: bool = True,
        return_details: bool = False,
    ) -> str:
        dev_dir = self.output_root / task_id / device_id
        identity = self._device_identity(task_id, device_id)
        sections = [f"# Device {identity.get('display', device_id)}"]
        attached_sql_sections: list[str] = []
        meta_path = dev_dir / "meta.json"
        filtered_path = dev_dir / "filtered.log"
        raw_path = dev_dir / "raw.log"
        source_path = filtered_path if filtered_path.exists() else raw_path
        meta_data = self._read_device_meta(task_id, device_id)
        sql_filtered_path = dev_dir / "filtered_sql.log"
        sql_raw_path = dev_dir / "raw_sql.log"
        if meta_path.exists():
            meta_payload = self._ai_meta_payload(meta_data, source_name=source_path.name if source_path.exists() else "")
            if analysis_time_start or analysis_time_end:
                meta_payload["analysis_time_window"] = {
                    "start": str(analysis_time_start or "").strip(),
                    "end": str(analysis_time_end or "").strip(),
                }
            sections.append(
                "## device_meta\n"
                + json.dumps(
                    meta_payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        if source_path.exists():
            log_text = self._filter_log_text_by_time_range(
                source_path.read_text(encoding="utf-8"),
                analysis_time_start,
                analysis_time_end,
            )
            if compression_strategy != "off":
                package = build_semantic_package(
                    log_text,
                    source_name=source_path.name,
                    device_id=device_id,
                    strategy=compression_strategy,
                    vendor=str(meta_data.get("vendor") or ""),
                    os_family=str(meta_data.get("os_family") or ""),
                )
                package_markdown = str(package.get("markdown", "") or "")
                compression_is_smaller = len(package_markdown.strip()) < len(log_text.strip())
                if package.get("used") and compression_is_smaller:
                    if persist_artifacts:
                        (dev_dir / "semantic_index.json").write_text(
                            json.dumps(package.get("index", {}), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        (dev_dir / "semantic_compact.md").write_text(
                            package_markdown,
                            encoding="utf-8",
                        )
                    sections.append("## semantic_compact.md\n" + package_markdown)
                    sections.append(
                        "## compression_stats\n" + json.dumps(package.get("stats", {}), ensure_ascii=False, indent=2)
                    )
                    sql_sections, attached_sql_sections = self._sql_sections(
                        log_text=log_text,
                        sql_filtered_path=sql_filtered_path,
                        sql_raw_path=sql_raw_path,
                        sql_log_inclusion_mode=sql_log_inclusion_mode,
                        analysis_time_start=analysis_time_start,
                        analysis_time_end=analysis_time_end,
                    )
                    if attached_sql_sections:
                        sections.append(
                            "## sql_attachment_notice\n"
                            + json.dumps({"attached_sql_sections": attached_sql_sections}, ensure_ascii=False, indent=2)
                        )
                    sections.extend(sql_sections)
                    sections.append(
                        f"## evidence_notice\nOriginal evidence remains unchanged in `{source_path.name}` on disk."
                    )
                    text = "\n\n".join(sections)
                    if return_details:
                        return {
                            "text": text,
                            "source_name": source_path.name,
                            "compression_used": True,
                            "attached_sql_sections": attached_sql_sections,
                        }
                    return text
            sections.append(f"## {source_path.name}\n" + log_text)
            sql_sections, attached_sql_sections = self._sql_sections(
                log_text=log_text,
                sql_filtered_path=sql_filtered_path,
                sql_raw_path=sql_raw_path,
                sql_log_inclusion_mode=sql_log_inclusion_mode,
                analysis_time_start=analysis_time_start,
                analysis_time_end=analysis_time_end,
            )
            if attached_sql_sections:
                sections.append(
                    "## sql_attachment_notice\n"
                    + json.dumps({"attached_sql_sections": attached_sql_sections}, ensure_ascii=False, indent=2)
                )
            sections.extend(sql_sections)
        else:
            sections.append("## notice\nno log file found")
        text = "\n\n".join(sections)
        if return_details:
            return {
                "text": text,
                "source_name": source_path.name if source_path.exists() else "",
                "compression_used": False,
                "attached_sql_sections": attached_sql_sections,
            }
        return text

    def _collect_task_report_text(
        self,
        task_id: str,
        compression_strategy: str = "template_vars",
        sql_log_inclusion_mode: str = "final_only",
        analysis_time_start: str = "",
        analysis_time_end: str = "",
        persist_artifacts: bool = True,
        device_ids: list[str] | None = None,
    ) -> str:
        task_dir = self.output_root / task_id
        summary_path = task_dir / "summary.json"
        if not summary_path.exists():
            raise RuntimeError("summary.json not found")
        summary_text = summary_path.read_text(encoding="utf-8")
        selected = self._normalize_device_ids(task_id, device_ids)
        summary = summary_text
        if selected:
            try:
                summary_data = json.loads(summary_text)
                if isinstance(summary_data, dict) and isinstance(summary_data.get("devices"), list):
                    selected_set = set(selected)
                    summary_data["devices"] = [
                        item
                        for item in summary_data.get("devices", [])
                        if str((item or {}).get("device_id", "") or "") in selected_set
                    ]
                    summary_data["progress_total"] = len(summary_data["devices"])
                    summary_data["progress_done"] = len(summary_data["devices"])
                    summary = json.dumps(summary_data, ensure_ascii=False, indent=2)
            except Exception:
                summary = summary_text

        sections = [f"# Task Summary\n{summary}\n"]
        selected = set(selected)
        for dev_dir in sorted([p for p in task_dir.iterdir() if p.is_dir()]):
            if selected and dev_dir.name not in selected:
                continue
            sections.append(
                "\n"
                + self._build_device_report_text(
                    task_id,
                    dev_dir.name,
                    compression_strategy=compression_strategy,
                    sql_log_inclusion_mode=sql_log_inclusion_mode,
                    analysis_time_start=analysis_time_start,
                    analysis_time_end=analysis_time_end,
                    persist_artifacts=persist_artifacts,
                )
                + "\n"
            )
        return "\n".join(sections)

    def _device_text(
        self,
        task_id: str,
        device_id: str,
        compression_strategy: str = "template_vars",
        sql_log_inclusion_mode: str = "final_only",
        analysis_time_start: str = "",
        analysis_time_end: str = "",
        max_chars: int = 24000,
    ) -> str:
        text = self._build_device_report_text(
            task_id,
            device_id,
            compression_strategy=compression_strategy,
            sql_log_inclusion_mode=sql_log_inclusion_mode,
            analysis_time_start=analysis_time_start,
            analysis_time_end=analysis_time_end,
            persist_artifacts=True,
        )
        return self._shrink_text(text, max_chars=max_chars)

    def _shrink_text(self, text: str, max_chars: int = 24000) -> str:
        if len(text) <= max_chars:
            return text
        head = text[: max_chars // 2]
        tail = text[-(max_chars // 2) :]
        return head + "\n\n...[TRUNCATED]...\n\n" + tail

    def _parse_analysis_time_value(self, value: str) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None

    def _parse_log_line_dt(self, line: str) -> datetime | None:
        text = str(line or "")
        m = re.search(r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[ T](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})", text)
        if m:
            try:
                return datetime(
                    int(m.group("y")),
                    int(m.group("mo")),
                    int(m.group("d")),
                    int(m.group("h")),
                    int(m.group("mi")),
                    int(m.group("s")),
                )
            except Exception:
                pass
        for p in self._TS_PATTERNS:
            m = p.search(text)
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

    def _filter_log_text_by_time_range(self, text: str, analysis_time_start: str = "", analysis_time_end: str = "") -> str:
        start_dt = self._parse_analysis_time_value(analysis_time_start)
        end_dt = self._parse_analysis_time_value(analysis_time_end)
        if start_dt is None and end_dt is None:
            return text
        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in str(text or "").splitlines():
            ts = self._parse_log_line_dt(line)
            if ts is not None:
                if current:
                    entries.append(current)
                current = {"timestamp": ts, "lines": [line]}
            elif current:
                current["lines"].append(line)
            else:
                current = {"timestamp": None, "lines": [line]}
        if current:
            entries.append(current)
        filtered_lines: list[str] = []
        for entry in entries:
            ts = entry.get("timestamp")
            if ts is None:
                filtered_lines.extend(entry.get("lines") or [])
                continue
            if start_dt is not None and ts < start_dt:
                continue
            if end_dt is not None and ts > end_dt:
                continue
            filtered_lines.extend(entry.get("lines") or [])
        return "\n".join(filtered_lines)

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

    def _soft_chunk_limit(self, *, provider: str, compression_strategy: str, max_tokens_per_chunk: int) -> int:
        soft_limit = max(800, int(max_tokens_per_chunk or 4500))
        if compression_strategy not in {"factor_time", "template_vars"}:
            return soft_limit
        if provider in {"deepseek", "chatgpt", "qwen", "gemini", "nvidia"}:
            return max(soft_limit, 24000)
        if provider in {"codex_local", "local"}:
            return max(soft_limit, 12000)
        return soft_limit

    def _effective_fragmentation(
        self,
        *,
        requested_fragmented: bool,
        report_text: str,
        provider: str,
        compression_strategy: str,
        max_tokens_per_chunk: int,
    ) -> bool:
        if not requested_fragmented:
            return False
        est_tokens = max(1, int(len(report_text) / 4))
        soft_limit = self._soft_chunk_limit(
            provider=provider,
            compression_strategy=compression_strategy,
            max_tokens_per_chunk=max_tokens_per_chunk,
        )
        return est_tokens > soft_limit

    def build_preview(
        self,
        task_id: str,
        cfg: dict[str, Any],
        *,
        selected_device_ids: list[str] | None = None,
        preview_device_id: str | None = None,
    ) -> dict[str, Any]:
        llm_base = self._build_llm_input(cfg)
        analysis_language = llm_base.get("analysis_language", "zh")
        batched = bool(int(cfg.get("batched_analysis", 0) or 0))
        fragmented = bool(int(cfg.get("fragmented_analysis", 0) or 0))
        compression_strategy = self._compression_strategy(cfg)
        sql_log_inclusion_mode = self._sql_log_inclusion_mode(cfg)
        analysis_time_start = str(cfg.get("analysis_time_start", "") or "").strip()
        analysis_time_end = str(cfg.get("analysis_time_end", "") or "").strip()
        max_tokens_per_chunk = max(800, int(cfg.get("max_tokens_per_chunk", 4500) or 4500))
        max_chunks_per_device = max(
            1,
            int(cfg.get("max_chunks_per_device", cfg.get("large_report_chunk_items", 12)) or 12),
        )
        chunk_strategy = str(cfg.get("chunk_strategy", "hybrid") or "hybrid").strip().lower()
        if chunk_strategy not in {"hybrid", "time", "event"}:
            chunk_strategy = "hybrid"
        selected_ids = self._normalize_device_ids(task_id, selected_device_ids)
        if not selected_ids:
            raise RuntimeError("no selected devices")
        preview_id = str(preview_device_id or "").strip()
        if not preview_id or preview_id not in selected_ids:
            preview_id = selected_ids[0]

        units: list[dict[str, Any]] = []
        if not batched:
            report_text = self._collect_task_report_text(
                task_id,
                compression_strategy=compression_strategy,
                sql_log_inclusion_mode=sql_log_inclusion_mode,
                analysis_time_start=analysis_time_start,
                analysis_time_end=analysis_time_end,
                persist_artifacts=False,
                device_ids=selected_ids,
            )
            units.append(
                {
                    "scope": "task_combined",
                    "title": f"Task combined payload ({len(selected_ids)} devices)",
                    "task_prompt_text": llm_base.get("task_prompt_text", ""),
                    "report_text": report_text,
                    "estimated_tokens": max(1, int(len(report_text) / 4)),
                }
            )
        else:
            soft_limit = self._soft_chunk_limit(
                provider=llm_base.get("provider", ""),
                compression_strategy=compression_strategy,
                max_tokens_per_chunk=max_tokens_per_chunk,
            )
            preview_identity = self._device_identity(task_id, preview_id)
            device_details = self._build_device_report_text(
                task_id,
                preview_id,
                compression_strategy=compression_strategy,
                sql_log_inclusion_mode=sql_log_inclusion_mode,
                analysis_time_start=analysis_time_start,
                analysis_time_end=analysis_time_end,
                persist_artifacts=False,
                return_details=True,
            )
            device_text = self._shrink_text(
                str(device_details.get("text", "") or ""),
                max_chars=max(24000, soft_limit * 4 + 2000),
            )
            attached_sql_sections = list(device_details.get("attached_sql_sections", []) or [])
            effective_fragmented = self._effective_fragmentation(
                requested_fragmented=fragmented,
                report_text=device_text,
                provider=llm_base.get("provider", ""),
                compression_strategy=compression_strategy,
                max_tokens_per_chunk=max_tokens_per_chunk,
            )
            if effective_fragmented:
                chunks = self._split_chunks(
                    device_text,
                    max_tokens_per_chunk=max_tokens_per_chunk,
                    max_chunks_per_device=max_chunks_per_device,
                    chunk_strategy=chunk_strategy,
                )
                for idx, chunk in enumerate(chunks):
                    units.append(
                        {
                            "scope": "device_chunk",
                            "title": f"{preview_identity.get('short_display', preview_id)} chunk {idx+1}/{len(chunks)}",
                            "task_prompt_text": compact_runtime_prompt(
                                llm_base.get("task_prompt_text", ""),
                                lang=analysis_language,
                                scope="device_chunk",
                                device_label=preview_identity.get("display", preview_id),
                                device_ip=preview_identity.get("device_ip", preview_id),
                                device_id=preview_id,
                                chunk_index=idx + 1,
                                chunk_total=len(chunks),
                            ),
                            "report_text": chunk,
                            "estimated_tokens": max(1, int(len(chunk) / 4)),
                            "attached_sql_sections": attached_sql_sections,
                        }
                    )
                if len(chunks) > 1:
                    merged_text = "\n\n".join([f"### 分片{i+1}\n{chunk}" for i, chunk in enumerate(chunks)])
                    units.append(
                        {
                            "scope": "device_summary",
                            "title": f"{preview_identity.get('short_display', preview_id)} summary merge payload",
                            "task_prompt_text": compact_runtime_prompt(
                                llm_base.get("task_prompt_text", ""),
                                lang=analysis_language,
                                scope="device_summary",
                                device_label=preview_identity.get("display", preview_id),
                                device_ip=preview_identity.get("device_ip", preview_id),
                                device_id=preview_id,
                            ),
                            "report_text": merged_text,
                            "estimated_tokens": max(1, int(len(merged_text) / 4)),
                            "attached_sql_sections": attached_sql_sections,
                        }
                    )
            else:
                units.append(
                    {
                        "scope": "device_single",
                        "title": f"{preview_identity.get('short_display', preview_id)} device payload",
                        "task_prompt_text": compact_runtime_prompt(
                            llm_base.get("task_prompt_text", ""),
                            lang=analysis_language,
                            scope="device_single",
                            device_label=preview_identity.get("display", preview_id),
                            device_ip=preview_identity.get("device_ip", preview_id),
                            device_id=preview_id,
                        ),
                        "report_text": device_text,
                        "estimated_tokens": max(1, int(len(device_text) / 4)),
                        "attached_sql_sections": attached_sql_sections,
                    }
                )
        return {
            "provider": llm_base.get("provider", ""),
            "model_used": model_used(llm_base),
            "system_prompt_text": llm_base.get("system_prompt_text", ""),
            "selected_device_ids": selected_ids,
            "preview_device_id": preview_id,
            "batched_analysis": batched,
            "fragmented_analysis": fragmented,
            "compression_strategy": compression_strategy,
            "sql_log_inclusion_mode": sql_log_inclusion_mode,
            "analysis_time_start": analysis_time_start,
            "analysis_time_end": analysis_time_end,
            "units": units,
        }

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

    def start(self, task_id: str, devices: list[dict[str, str]] | None = None, cfg_override: dict[str, Any] | None = None) -> str:
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
            "_cfg_override": dict(cfg_override or {}),
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
        runner = asyncio.create_task(self._run(analysis_id, task_id))
        self._async_tasks[analysis_id] = runner
        return analysis_id

    def stop_task(self, task_id: str) -> dict[str, Any] | None:
        active = self.get_active_by_task(task_id)
        if not active:
            return None
        analysis_id = str(active.get("analysis_id", "") or "")
        if not analysis_id:
            return None
        task = self._tasks.get(analysis_id)
        if not task:
            return None
        task["_cancel_requested"] = True
        task["status"] = "failed"
        task["error"] = "analysis stopped by user"
        task["device_running"] = 0
        self._set_progress(
            task,
            max(1, int(task.get("progress_percent", 0) or 1)),
            self._format_healthcheck_style_progress(task, "已停止"),
        )
        runner = self._async_tasks.get(analysis_id)
        if runner and not runner.done():
            runner.cancel()
        self._persist_analysis_snapshot(task)
        return {"analysis_id": analysis_id, **{k: v for k, v in task.items() if not str(k).startswith("_")}}

    async def _run(self, analysis_id: str, task_id: str) -> None:
        task = self._tasks[analysis_id]
        try:
            cfg = load_gpt_config()
            cfg.update(dict(task.get("_cfg_override") or {}))
            llm_base = self._build_llm_input(cfg)
            provider = llm_base["provider"]
            analysis_language = llm_base.get("analysis_language", "zh")
            task["provider_used"] = provider
            task["model_used"] = model_used(llm_base)
            task["analysis_language"] = analysis_language
            task["updated_at"] = self._now()

            batched = bool(int(cfg.get("batched_analysis", 0) or 0))
            fragmented = bool(int(cfg.get("fragmented_analysis", 0) or 0))
            compression_strategy = self._compression_strategy(cfg)
            sql_log_inclusion_mode = self._sql_log_inclusion_mode(cfg)
            analysis_time_start = str(cfg.get("analysis_time_start", "") or "").strip()
            analysis_time_end = str(cfg.get("analysis_time_end", "") or "").strip()
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
                    for i, p in enumerate(sorted([x for x in task_dir.iterdir() if self._is_device_output_dir(x)]))
                ]
            device_total = len(devices) if devices else 1
            task["device_total"] = device_total
            task["rounds_total"] = max(1, math.ceil(device_total / parallelism))

            # Build real unit plan.
            device_plans: list[dict[str, Any]] = []
            total_units = 0
            if batched and devices:
                for d in devices:
                    self._check_cancelled(task)
                    soft_limit = self._soft_chunk_limit(
                        provider=provider,
                        compression_strategy=compression_strategy,
                        max_tokens_per_chunk=max_tokens_per_chunk,
                    )
                    dtext = self._device_text(
                        task_id,
                        str(d.get("device_id") or ""),
                        compression_strategy=compression_strategy,
                        sql_log_inclusion_mode=sql_log_inclusion_mode,
                        analysis_time_start=analysis_time_start,
                        analysis_time_end=analysis_time_end,
                        max_chars=max(24000, soft_limit * 4 + 2000),
                    )
                    effective_fragmented = self._effective_fragmentation(
                        requested_fragmented=fragmented,
                        report_text=dtext,
                        provider=provider,
                        compression_strategy=compression_strategy,
                        max_tokens_per_chunk=max_tokens_per_chunk,
                    )
                    chunks = (
                        self._split_chunks(
                            dtext,
                            max_tokens_per_chunk=max_tokens_per_chunk,
                            max_chunks_per_device=max_chunks_per_device,
                            chunk_strategy=chunk_strategy,
                        )
                        if effective_fragmented
                        else [dtext]
                    )
                    plan = {"device": d, "text": dtext, "chunks": chunks, "fragmented": effective_fragmented}
                    device_plans.append(plan)
                    if effective_fragmented:
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
            device_summaries: list[dict[str, Any]] = []

            async def update_progress(stage: str) -> None:
                nonlocal running_ips
                self._check_cancelled(task, stage)
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
                self._check_cancelled(task)
                self._set_progress(task, 20, self._format_healthcheck_style_progress(task, "汇总任务日志"))
                report_text = self._collect_task_report_text(
                    task_id,
                    compression_strategy=compression_strategy,
                    sql_log_inclusion_mode=sql_log_inclusion_mode,
                    analysis_time_start=analysis_time_start,
                    analysis_time_end=analysis_time_end,
                )
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
                    self._check_cancelled(task)
                    d = plan["device"]
                    d_id = str(d.get("device_id") or "")
                    d_ip = str(d.get("ip") or "-")
                    d_identity = self._device_identity(task_id, d_id, fallback_ip=d_ip)
                    d_idx = int(d.get("index") or 1)
                    chunks: list[str] = plan["chunks"]
                    plan_fragmented = bool(plan.get("fragmented"))
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
                            self._check_cancelled(task)
                            chunk_results: list[str] = [""] * max(1, len(chunks))

                            async def analyze_one_chunk(i: int, chunk: str) -> tuple[int, str, int]:
                                llm = dict(llm_base)
                                llm["task_prompt_text"] = compact_runtime_prompt(
                                    llm_base.get("task_prompt_text", ""),
                                    lang=analysis_language,
                                    scope="device_chunk",
                                    device_label=d_identity.get("display", d_ip),
                                    device_ip=d_identity.get("device_ip", d_ip),
                                    device_id=d_id,
                                    chunk_index=i + 1,
                                    chunk_total=len(chunks),
                                )
                                self._check_cancelled(task)
                                timeout_sec = self._estimate_timeout_sec(chunk, floor=60, ceiling=max_call_timeout)
                                task["_active_call_started"] = time.monotonic()
                                task["_active_call_timeout_sec"] = float(timeout_sec)
                                text, usage = await self._call_llm_with_retry(
                                    llm, chunk, retries, timeout_sec=timeout_sec
                                )
                                token_used = int((usage or {}).get("total_tokens", 0) or 0)
                                return i, text, token_used

                            if plan_fragmented and len(chunks) > 1 and chunk_parallelism > 1:
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
                                    self._check_cancelled(task)
                                    i2, text, token_used = await analyze_one_chunk(i, chunk)
                                    task["_active_call_started"] = 0.0
                                    task["_active_call_timeout_sec"] = 0.0
                                    chunk_results[i2] = text
                                    async with lock:
                                        total_token_used += token_used
                                        task["_unit_done"] += 1
                                        task["_chunk_done_by_ip"][d_ip] = int(task["_chunk_done_by_ip"].get(d_ip, 0) or 0) + 1
                                        await update_progress("设备分析中")

                            if plan_fragmented and len(chunks) > 1:
                                self._check_cancelled(task)
                                # summarize chunk results per device
                                llm = dict(llm_base)
                                llm["task_prompt_text"] = compact_runtime_prompt(
                                    llm_base.get("task_prompt_text", ""),
                                    lang=analysis_language,
                                    scope="device_summary",
                                    device_label=d_identity.get("display", d_ip),
                                    device_ip=d_identity.get("device_ip", d_ip),
                                    device_id=d_id,
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
                                parsed = self._parse_runtime_summary_json(summary)
                                device_summaries.append(
                                    {
                                        "device_id": d_id,
                                        "device_name": d_identity.get("device_name", ""),
                                        "device_ip": d_identity.get("device_ip", d_ip),
                                        "device_label": d_identity.get("display", d_ip),
                                        "verdict": (parsed or {}).get("verdict", ""),
                                        "anomalies": list((parsed or {}).get("anomalies", []) or []),
                                        "evidence": list((parsed or {}).get("evidence", []) or []),
                                        "actions": list((parsed or {}).get("actions", []) or []),
                                        "raw_summary": summary,
                                    }
                                )
                            else:
                                # Use single chunk result directly (or non-fragmented mode).
                                summary = chunk_results[0] if chunk_results else ""
                                async with lock:
                                    task["device_done"] += 1
                                    await update_progress("设备分析中")
                                parsed = self._parse_runtime_summary_json(summary)
                                device_summaries.append(
                                    {
                                        "device_id": d_id,
                                        "device_name": d_identity.get("device_name", ""),
                                        "device_ip": d_identity.get("device_ip", d_ip),
                                        "device_label": d_identity.get("display", d_ip),
                                        "verdict": (parsed or {}).get("verdict", ""),
                                        "anomalies": list((parsed or {}).get("anomalies", []) or []),
                                        "evidence": list((parsed or {}).get("evidence", []) or []),
                                        "actions": list((parsed or {}).get("actions", []) or []),
                                        "raw_summary": summary,
                                    }
                                )
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
                self._check_cancelled(task)
                self._set_progress(task, min(99, int((task["_unit_done"] / task["_unit_total"]) * 100)), self._format_healthcheck_style_progress(task, "全局汇总中"))
                global_report = self._compact_global_summary_input(device_summaries)
                llm = dict(llm_base)
                llm["task_prompt_text"] = final_runtime_prompt(llm_base.get("task_prompt_text", ""), lang=analysis_language)
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
                summary_title = "# Global Summary" if str(analysis_language).lower().startswith("en") else "# 全局汇总"
                task["result"] = summary_title + "\n" + global_summary + "\n\n" + self._render_device_summary_appendix(device_summaries, lang=analysis_language)

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
        except asyncio.CancelledError as exc:
            task["status"] = "failed"
            task["error"] = str(exc).strip() or "analysis stopped by user"
            task["device_running"] = 0
            self._set_progress(
                task,
                max(1, int(task.get("progress_percent", 0) or 1)),
                self._format_healthcheck_style_progress(task, "已停止"),
            )
            self._persist_analysis_snapshot(task)
            self._persist_analysis_history(task)
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
        finally:
            self._async_tasks.pop(analysis_id, None)

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
