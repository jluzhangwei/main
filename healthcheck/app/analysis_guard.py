#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
from typing import Dict, List

from app import analysis_pipeline


def _to_tokens(chars: int) -> int:
    return max(1, int(max(0, chars) / 4))


def _provider_avg_latency_seconds(provider: str) -> float:
    p = str(provider or "").strip().lower()
    if p == "local":
        return 12.0
    if p == "nvidia":
        return 8.0
    if p in {"deepseek", "gemini"}:
        return 7.0
    return 6.0


def estimate_analysis_plan(
    report_data: Dict,
    provider: str,
    batched: bool,
    parallelism: int,
    retries: int,
    large_report_mode: bool,
    large_report_chunk_items: int,
    system_prompt_text: str = "",
    task_prompt_text: str = "",
) -> Dict:
    devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
    if not isinstance(devices, list):
        devices = []
    device_count = len(devices)
    prompt_overhead = len(str(system_prompt_text or "")) + len(str(task_prompt_text or ""))
    parallel = max(1, int(parallelism or 1))
    retry_n = max(0, int(retries or 0))
    chunk_n = max(1, int(large_report_chunk_items or 1))

    calls = 0
    prompt_tokens = 0
    mode = "single"
    warnings: List[str] = []

    if not devices:
        raw = str(report_data or "")
        calls = 1
        prompt_tokens = _to_tokens(len(raw) + prompt_overhead)
        warnings.append("报告中未检测到结构化 devices，按单次分析估算。")
    elif not batched:
        mode = "single"
        payload = analysis_pipeline.build_whole_report_analysis_input(report_data, force_full=False)
        calls = 1
        prompt_tokens = _to_tokens(len(payload) + prompt_overhead)
    else:
        mode = "batched"
        per_device_calls = 0
        for dev in devices:
            if large_report_mode:
                chunks = analysis_pipeline.build_device_chunk_inputs(
                    report_data,
                    dev if isinstance(dev, dict) else {},
                    chunk_count=chunk_n,
                    force_full=False,
                )
                per_device_calls += max(1, len(chunks)) + 1  # chunks + per-device summary
                for chunk_text in chunks:
                    prompt_tokens += _to_tokens(len(chunk_text) + prompt_overhead)
                dev_summary_input = analysis_pipeline.build_device_chunk_summary_input(
                    report_data,
                    dev if isinstance(dev, dict) else {},
                    [],
                    force_full=False,
                )
                prompt_tokens += _to_tokens(len(dev_summary_input) + prompt_overhead)
            else:
                per_device_calls += 1
                one = analysis_pipeline.build_device_analysis_input(
                    report_data,
                    dev if isinstance(dev, dict) else {},
                    force_full=False,
                )
                prompt_tokens += _to_tokens(len(one) + prompt_overhead)
        summary_input = analysis_pipeline.build_batched_summary_input(report_data, [], force_full=False)
        prompt_tokens += _to_tokens(len(summary_input) + prompt_overhead)
        calls = per_device_calls + 1

    effective_calls = calls * (1 + retry_n)
    completion_tokens = max(800, int(max(1, calls) * 1100))
    total_tokens = prompt_tokens + completion_tokens
    estimated_seconds = int((effective_calls / float(parallel)) * _provider_avg_latency_seconds(provider))

    if device_count > 0 and not batched and device_count > 1:
        warnings.append("当前是全量单次分析，多设备大报告可能触发模型窗口限制。")
    if total_tokens > 120000:
        warnings.append("估算 Token 较高，建议开启分批/分片或减少检查项。")
    if large_report_mode and not batched:
        warnings.append("分片模式依赖分批模式，当前估算已按分批启用处理。")

    return {
        "mode": mode,
        "device_count": device_count,
        "estimated_calls": int(max(1, calls)),
        "estimated_prompt_tokens": int(max(1, prompt_tokens)),
        "estimated_completion_tokens": int(max(1, completion_tokens)),
        "estimated_total_tokens": int(max(1, total_tokens)),
        "estimated_seconds": int(max(1, estimated_seconds)),
        "warnings": warnings,
    }


def extract_device_mentions(text: str) -> List[str]:
    raw = str(text or "")
    ip_hits = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)
    out: List[str] = []
    for hit in ip_hits:
        if hit not in out:
            out.append(hit)
    return out


def patch_summary_full_coverage(summary_text: str, expected_devices: List[str]) -> Dict:
    base = str(summary_text or "")
    expected = [str(d).strip() for d in (expected_devices or []) if str(d).strip()]
    mentioned = set(extract_device_mentions(base))
    missing = [d for d in expected if d not in mentioned]
    if not missing:
        return {"summary_text": base, "missing_devices": []}
    patch = (
        "\n\n## 全设备逐台结论补全（程序自动补齐）\n"
        "| 设备 IP | 风险等级 | 是否上榜 TopN | 关键依据 |\n"
        "| --- | --- | --- | --- |\n"
        + "\n".join(
            [
                f"| {d} | 待复核 | 否 | 汇总模型输出中未覆盖该设备，建议复核该设备逐台分析结果。 |"
                for d in missing
            ]
        )
    )
    return {"summary_text": base + patch, "missing_devices": missing}
