#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from typing import Dict, List


def extract_key_lines(output_full: str, command: str) -> List[str]:
    text = str(output_full or "")
    if not text:
        return []
    cmd = str(command or "").lower()
    patterns: List[re.Pattern] = []
    if "interface" in cmd:
        patterns.extend(
            [
                re.compile(r"last\s+\d+\s+seconds.*(rate|packets/sec)", re.IGNORECASE),
                re.compile(r"\b(input|output)\b.*\b(errors?|drops?|discard|crc|ignored)\b", re.IGNORECASE),
                re.compile(r"\b(errors?|drops?|discard|crc|ignored)\b", re.IGNORECASE),
                re.compile(r"\bcurrent state\b", re.IGNORECASE),
            ]
        )
    else:
        patterns.extend(
            [
                re.compile(r"\b(error|failed|timeout|denied|down|alarm|drop|crc)\b", re.IGNORECASE),
            ]
        )
    picked: List[str] = []
    seen: set = set()
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        for p in patterns:
            if p.search(raw):
                if raw not in seen:
                    seen.add(raw)
                    picked.append(raw)
                break
        if len(picked) >= 30:
            break
    return picked


def build_device_analysis_input(
    report_data: Dict,
    device_data: Dict,
    max_chars: int = 110000,
    force_full: bool = False,
) -> str:
    summary = report_data.get("summary", {}) if isinstance(report_data, dict) else {}
    base_device = dict(device_data or {})
    if force_full:
        payload = {
            "generated_at": report_data.get("generated_at", ""),
            "summary": summary,
            "device_report": base_device,
        }
        return "单设备巡检JSON（结构化，完整原文）：\n" + json.dumps(payload, ensure_ascii=False)

    def _build_with_cap(excerpt_cap: int, include_excerpt: bool) -> Dict:
        device = {
            "device": base_device.get("device", ""),
            "profile_id": base_device.get("profile_id", ""),
            "connected": bool(base_device.get("connected", False)),
            "elapsed_sec": base_device.get("elapsed_sec", 0),
            "stats": base_device.get("stats", {}),
            "items": [],
        }
        for item in (base_device.get("items", []) if isinstance(base_device, dict) else []):
            row = {
                "item": item.get("item", ""),
                "success": bool(item.get("success", False)),
                "elapsed_sec": item.get("elapsed_sec", 0),
                "attempt_count": item.get("attempt_count", 0),
                "attempts": [],
            }
            for attempt in item.get("attempts", []) if isinstance(item, dict) else []:
                output_full = str(attempt.get("output_full", "") or "")
                command = str(attempt.get("command", "") or "")
                attempt_row = {
                    "command": command,
                    "success": bool(attempt.get("success", False)),
                    "exit_status": attempt.get("exit_status", 0),
                    "error_first_line": attempt.get("error_first_line", ""),
                    "output_preview": attempt.get("output_preview", ""),
                    "key_lines": extract_key_lines(output_full, command),
                }
                if include_excerpt:
                    attempt_row["output_excerpt"] = _excerpt_text(output_full, excerpt_cap)
                row["attempts"].append(attempt_row)
            device["items"].append(row)
        return {
            "generated_at": report_data.get("generated_at", ""),
            "summary": summary,
            "device_report": device,
        }

    for cap in (10000, 6000, 4000, 2500, 1500, 800):
        payload = _build_with_cap(cap, True)
        txt = json.dumps(payload, ensure_ascii=False)
        if len(txt) <= max_chars:
            return f"单设备巡检JSON（结构化，已截断原文，output_excerpt_cap={cap}）：\n{txt}"
    payload = _build_with_cap(0, False)
    txt = json.dumps(payload, ensure_ascii=False)
    return "单设备巡检JSON（结构化，仅关键证据；原文过大已省略）：\n" + txt


def build_batched_summary_input(report_data: Dict, per_device_results: List[Dict], force_full: bool = False) -> str:
    summary = report_data.get("summary", {}) if isinstance(report_data, dict) else {}
    devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
    brief = []
    for item in per_device_results:
        analysis_text = str(item.get("analysis", "") or "")
        if force_full:
            analysis_value = analysis_text
        else:
            analysis_value = _excerpt_text(analysis_text, 3000)
        brief.append(
            {
                "device": item.get("device", ""),
                "analysis": analysis_value,
                "token_usage": item.get("token_usage", {}),
            }
        )
    payload = {
        "mode": "per-device batched analysis",
        "generated_at": report_data.get("generated_at", ""),
        "summary": summary,
        "device_count": len(devices) if isinstance(devices, list) else 0,
        "per_device_analysis": brief,
    }
    return "分批单设备分析结果汇总JSON：\n" + json.dumps(payload, ensure_ascii=False)


def _excerpt_text(text: str, cap: int) -> str:
    raw = str(text or "")
    if cap <= 0:
        return ""
    if len(raw) <= cap:
        return raw
    return raw[: max(0, cap - 24)] + "\n...[truncated]..."


def _build_whole_payload(report_data: Dict, output_cap: int, include_excerpt: bool) -> Dict:
    summary = report_data.get("summary", {}) if isinstance(report_data, dict) else {}
    devices = report_data.get("devices", []) if isinstance(report_data, dict) else []
    out_devices: List[Dict] = []
    for device in devices if isinstance(devices, list) else []:
        dev_row: Dict = {
            "device": device.get("device", ""),
            "profile_id": device.get("profile_id", ""),
            "connected": bool(device.get("connected", False)),
            "elapsed_sec": device.get("elapsed_sec", 0),
            "stats": device.get("stats", {}),
            "items": [],
        }
        for item in device.get("items", []) if isinstance(device, dict) else []:
            item_row: Dict = {
                "item": item.get("item", ""),
                "success": bool(item.get("success", False)),
                "elapsed_sec": item.get("elapsed_sec", 0),
                "attempt_count": item.get("attempt_count", 0),
                "attempts": [],
            }
            for attempt in item.get("attempts", []) if isinstance(item, dict) else []:
                full = str(attempt.get("output_full", "") or "")
                cmd = str(attempt.get("command", "") or "")
                attempt_row: Dict = {
                    "command": cmd,
                    "success": bool(attempt.get("success", False)),
                    "exit_status": attempt.get("exit_status", 0),
                    "error_first_line": attempt.get("error_first_line", ""),
                    "output_preview": attempt.get("output_preview", ""),
                    "key_lines": extract_key_lines(full, cmd),
                }
                if include_excerpt:
                    attempt_row["output_excerpt"] = _excerpt_text(full, output_cap)
                item_row["attempts"].append(attempt_row)
            dev_row["items"].append(item_row)
        out_devices.append(dev_row)
    return {
        "generated_at": report_data.get("generated_at", ""),
        "summary": summary,
        "device_count": len(out_devices),
        "instruction": "必须覆盖全部设备逐台分析，禁止只分析第一台设备。",
        "devices": out_devices,
    }


def build_whole_report_analysis_input(report_data: Dict, max_chars: int = 260000, force_full: bool = False) -> str:
    """Build multi-device analysis input with bounded size, preserving full device coverage."""
    if force_full:
        return "多设备巡检JSON（结构化，完整原文）：\n" + json.dumps(report_data, ensure_ascii=False)
    for cap in (6000, 4000, 2500, 1500, 800):
        payload = _build_whole_payload(report_data, output_cap=cap, include_excerpt=True)
        txt = json.dumps(payload, ensure_ascii=False)
        if len(txt) <= max_chars:
            return f"多设备巡检JSON（结构化，已包含关键证据与截断原文，output_excerpt_cap={cap}）：\n{txt}"
    payload = _build_whole_payload(report_data, output_cap=0, include_excerpt=False)
    txt = json.dumps(payload, ensure_ascii=False)
    return "多设备巡检JSON（结构化，仅关键证据；原文过大已省略）：\n" + txt


def _normalize_item_for_analysis(item: Dict, excerpt_cap: int, include_excerpt: bool, force_full: bool) -> Dict:
    row = {
        "item": item.get("item", ""),
        "success": bool(item.get("success", False)),
        "elapsed_sec": item.get("elapsed_sec", 0),
        "attempt_count": item.get("attempt_count", 0),
        "attempts": [],
    }
    for attempt in item.get("attempts", []) if isinstance(item, dict) else []:
        if force_full:
            row["attempts"].append(dict(attempt))
            continue
        full = str(attempt.get("output_full", "") or "")
        cmd = str(attempt.get("command", "") or "")
        attempt_row: Dict = {
            "command": cmd,
            "success": bool(attempt.get("success", False)),
            "exit_status": attempt.get("exit_status", 0),
            "error_first_line": attempt.get("error_first_line", ""),
            "output_preview": attempt.get("output_preview", ""),
            "key_lines": extract_key_lines(full, cmd),
        }
        if include_excerpt:
            attempt_row["output_excerpt"] = _excerpt_text(full, excerpt_cap)
        row["attempts"].append(attempt_row)
    return row


def build_device_chunk_inputs(
    report_data: Dict,
    device_data: Dict,
    chunk_size_items: int = 4,
    force_full: bool = False,
    max_chars: int = 100000,
) -> List[str]:
    items = device_data.get("items", []) if isinstance(device_data, dict) else []
    if not isinstance(items, list) or not items:
        return [build_device_analysis_input(report_data, device_data, force_full=force_full)]
    size = max(1, int(chunk_size_items or 4))
    chunks: List[str] = []
    total_chunks = (len(items) + size - 1) // size
    for idx in range(total_chunks):
        start = idx * size
        end = min(len(items), start + size)
        item_slice = items[start:end]
        if force_full:
            excerpt_caps = [0]
        else:
            excerpt_caps = [8000, 5000, 3000, 1500, 800, 0]
        selected_text = ""
        for cap in excerpt_caps:
            include_excerpt = cap > 0
            normalized_items = [
                _normalize_item_for_analysis(i, excerpt_cap=cap, include_excerpt=include_excerpt, force_full=False)
                for i in item_slice
            ]
            if force_full:
                normalized_items = [dict(i) for i in item_slice]
            payload = {
                "generated_at": report_data.get("generated_at", ""),
                "summary": report_data.get("summary", {}),
                "device": {
                    "device": device_data.get("device", ""),
                    "profile_id": device_data.get("profile_id", ""),
                    "connected": bool(device_data.get("connected", False)),
                    "elapsed_sec": device_data.get("elapsed_sec", 0),
                    "stats": device_data.get("stats", {}),
                },
                "chunk_meta": {
                    "chunk_index": idx + 1,
                    "chunk_count": total_chunks,
                    "item_start": start + 1,
                    "item_end": end,
                    "item_total": len(items),
                },
                "items": normalized_items,
                "instruction": "请仅分析当前分片，并给出分片结论、证据链和风险等级。",
            }
            prefix = (
                f"单设备分片巡检JSON（完整原文，chunk {idx + 1}/{total_chunks}）：\n"
                if force_full
                else f"单设备分片巡检JSON（受控输入，chunk {idx + 1}/{total_chunks}，excerpt_cap={cap}）：\n"
            )
            txt = prefix + json.dumps(payload, ensure_ascii=False)
            if force_full or len(txt) <= max_chars:
                selected_text = txt
                break
        if not selected_text:
            selected_text = build_device_analysis_input(report_data, device_data, force_full=force_full)
        chunks.append(selected_text)
    return chunks


def build_device_chunk_summary_input(
    report_data: Dict,
    device_data: Dict,
    chunk_results: List[Dict],
    force_full: bool = False,
) -> str:
    brief = []
    for item in chunk_results:
        text = str(item.get("analysis", "") or "")
        brief.append(
            {
                "chunk_index": item.get("chunk_index", 0),
                "chunk_count": item.get("chunk_count", 0),
                "analysis": text if force_full else _excerpt_text(text, 3500),
                "token_usage": item.get("token_usage", {}),
            }
        )
    payload = {
        "generated_at": report_data.get("generated_at", ""),
        "summary": report_data.get("summary", {}),
        "device": {
            "device": device_data.get("device", ""),
            "profile_id": device_data.get("profile_id", ""),
            "connected": bool(device_data.get("connected", False)),
            "elapsed_sec": device_data.get("elapsed_sec", 0),
            "stats": device_data.get("stats", {}),
        },
        "chunk_count": len(brief),
        "chunk_results": brief,
        "instruction": "请汇总所有分片结论，输出该设备最终诊断，必须覆盖异常、证据链、根因判断、修复建议。",
    }
    return "单设备分片汇总JSON：\n" + json.dumps(payload, ensure_ascii=False)
