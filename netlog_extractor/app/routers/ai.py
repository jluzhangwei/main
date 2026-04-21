from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..ai.prompt_store import (
    delete_custom_prompt,
    is_custom_prompt,
    localized_prompt_catalog,
    localized_prompt_labels,
    merged_system_prompt_catalog,
    merged_task_prompt_catalog,
    save_custom_prompt,
)
from ..ai.state_store import load_gpt_config, load_token_stats, save_gpt_config
from ..ai.llm_client import (
    detect_qwen_endpoint,
    test_deepseek_connection,
    test_gemini_connection,
    test_local_lmstudio_connection,
    test_nvidia_connection,
    test_openai_connection,
)

router = APIRouter(tags=["ai"])
templates = Jinja2Templates(directory=(Path(__file__).resolve().parent.parent / "templates").as_posix())

CHATGPT_MODEL_OPTIONS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "o1",
    "o1-mini",
    "o3",
    "o3-mini",
]
DEEPSEEK_MODEL_OPTIONS = [
    "deepseek-chat",
    "deepseek-reasoner",
]
QWEN_MODEL_OPTIONS = [
    "qwen-plus",
    "qwen-turbo",
    "qwen-max",
    "qwen-long",
]
GEMINI_MODEL_OPTIONS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
NVIDIA_MODEL_OPTIONS = [
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-405b-instruct",
    "mistralai/mixtral-8x7b-instruct-v0.1",
    "nvidia/llama-3.1-nemotron-70b-instruct",
]
LOCAL_MODEL_OPTIONS = [
    "deepseek-r1-distill-llama-70b",
    "deepseek-r1-distill-qwen-32b",
    "gemma-2-27b-it",
    "llama-3.1-70b-instruct",
    "llama-3.1-8b-instruct",
    "mistral-large-instruct",
    "qwen/qwen2.5-72b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "qwen/qwen3-coder-30b",
]


def _to_bool(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "checked"} else 0


def _to_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        num = int(value)
    except Exception:
        num = default
    return max(low, min(high, num))


def _merge_cfg(cfg: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    def keep_or_update(key: str) -> str:
        if key not in incoming:
            return str(cfg.get(key, "") or "")
        raw = incoming.get(key)
        text = str(raw or "").strip()
        if not text:
            return str(cfg.get(key, "") or "")
        return text

    cfg.update(
        {
            "provider": str(incoming.get("provider", cfg.get("provider", "chatgpt")) or "chatgpt"),
            "chatgpt_api_key": keep_or_update("chatgpt_api_key"),
            "deepseek_api_key": keep_or_update("deepseek_api_key"),
            "qwen_api_key": keep_or_update("qwen_api_key"),
            "gemini_api_key": keep_or_update("gemini_api_key"),
            "nvidia_api_key": keep_or_update("nvidia_api_key"),
            "chatgpt_model": str(incoming.get("chatgpt_model", cfg.get("chatgpt_model", "")) or ""),
            "local_base_url": str(incoming.get("local_base_url", cfg.get("local_base_url", "")) or ""),
            "local_model": str(incoming.get("local_model", cfg.get("local_model", "")) or ""),
            "deepseek_model": str(incoming.get("deepseek_model", cfg.get("deepseek_model", "")) or ""),
            "qwen_model": str(incoming.get("qwen_model", cfg.get("qwen_model", "")) or ""),
            "qwen_base_url": str(incoming.get("qwen_base_url", cfg.get("qwen_base_url", "")) or ""),
            "gemini_model": str(incoming.get("gemini_model", cfg.get("gemini_model", "")) or ""),
            "nvidia_model": str(incoming.get("nvidia_model", cfg.get("nvidia_model", "")) or ""),
            "selected_system_prompt": str(
                incoming.get("selected_system_prompt", cfg.get("selected_system_prompt", "")) or ""
            ),
            "selected_task_prompt": str(incoming.get("selected_task_prompt", cfg.get("selected_task_prompt", "")) or ""),
            "system_prompt_extra": str(incoming.get("system_prompt_extra", cfg.get("system_prompt_extra", "")) or ""),
            "task_prompt_extra": str(incoming.get("task_prompt_extra", cfg.get("task_prompt_extra", "")) or ""),
            "batched_analysis": _to_bool(incoming.get("batched_analysis", cfg.get("batched_analysis", 0))),
            "fragmented_analysis": _to_bool(incoming.get("fragmented_analysis", cfg.get("fragmented_analysis", 0))),
            "analysis_parallelism": _to_int(
                incoming.get("analysis_parallelism", cfg.get("analysis_parallelism", 2)),
                default=2,
                low=1,
                high=8,
            ),
            "analysis_retries": _to_int(
                incoming.get("analysis_retries", cfg.get("analysis_retries", 1)),
                default=1,
                low=0,
                high=3,
            ),
            "large_report_chunk_items": _to_int(
                incoming.get("large_report_chunk_items", cfg.get("large_report_chunk_items", 4)),
                default=4,
                low=1,
                high=20,
            ),
            "max_tokens_per_chunk": _to_int(
                incoming.get("max_tokens_per_chunk", cfg.get("max_tokens_per_chunk", 4500)),
                default=4500,
                low=800,
                high=12000,
            ),
            "max_chunks_per_device": _to_int(
                incoming.get("max_chunks_per_device", cfg.get("max_chunks_per_device", 12)),
                default=12,
                low=1,
                high=60,
            ),
            "chunk_strategy": str(incoming.get("chunk_strategy", cfg.get("chunk_strategy", "hybrid")) or "hybrid").strip().lower(),
            "chunk_parallelism": _to_int(
                incoming.get("chunk_parallelism", cfg.get("chunk_parallelism", 1)),
                default=1,
                low=1,
                high=8,
            ),
            "llm_call_timeout_sec": _to_int(
                incoming.get("llm_call_timeout_sec", cfg.get("llm_call_timeout_sec", 240)),
                default=240,
                low=60,
                high=600,
            ),
        }
    )
    if cfg["chunk_strategy"] not in {"hybrid", "time", "event"}:
        cfg["chunk_strategy"] = "hybrid"
    # Backward compatibility: keep old field synced.
    cfg["large_report_chunk_items"] = int(cfg.get("max_chunks_per_device", cfg.get("large_report_chunk_items", 12)))
    return cfg


def _redact_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    out["chatgpt_api_key"] = ""
    out["deepseek_api_key"] = ""
    out["qwen_api_key"] = ""
    out["gemini_api_key"] = ""
    out["nvidia_api_key"] = ""
    return out


def _has_keys(cfg: dict[str, Any]) -> dict[str, bool]:
    return {
        "chatgpt": bool(str(cfg.get("chatgpt_api_key", "") or "").strip()),
        "deepseek": bool(str(cfg.get("deepseek_api_key", "") or "").strip()),
        "qwen": bool(str(cfg.get("qwen_api_key", "") or "").strip()),
        "gemini": bool(str(cfg.get("gemini_api_key", "") or "").strip()),
        "nvidia": bool(str(cfg.get("nvidia_api_key", "") or "").strip()),
    }


def _estimate_precheck(task: Any, task_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    device_count = len(getattr(task, "devices", []) or [])
    if device_count <= 0:
        device_count = 1
    batched = bool(_to_bool((payload or {}).get("batched_analysis", 0)))
    fragmented = bool(_to_bool((payload or {}).get("fragmented_analysis", 0)))
    analysis_parallelism = _to_int((payload or {}).get("analysis_parallelism", 2), default=2, low=1, high=8)
    chunk_parallelism = _to_int((payload or {}).get("chunk_parallelism", 1), default=1, low=1, high=8)
    analysis_retries = _to_int((payload or {}).get("analysis_retries", 1), default=1, low=0, high=3)
    max_tokens_per_chunk = _to_int((payload or {}).get("max_tokens_per_chunk", 4500), default=4500, low=800, high=12000)
    max_chunks_per_device = _to_int(
        (payload or {}).get("max_chunks_per_device", (payload or {}).get("large_report_chunk_items", 12)),
        default=12,
        low=1,
        high=60,
    )
    report_text = ""
    try:
        report_text = request.app.state.ai_manager._collect_task_report_text(task_id)
    except Exception:
        # Fallback for unfinished tasks.
        report_text = f"task_id={task_id}, device_count={device_count}"
    estimated_tokens = max(200, int(len(report_text) / 4))

    if not batched:
        estimated_calls = 1
    elif fragmented:
        avg_tokens_per_device = max(1200, int(estimated_tokens / max(1, device_count)))
        est_chunks = max(1, min(max_chunks_per_device, math.ceil(avg_tokens_per_device / max_tokens_per_chunk)))
        per_device_calls = est_chunks + (1 if est_chunks > 1 else 0)
        estimated_calls = device_count * per_device_calls + 1
    else:
        # Per-device analysis + global summary.
        estimated_calls = device_count + 1

    # Time estimate should reflect device-level parallelism.
    # Device work can run in parallel; global summary is serial.
    if not batched:
        base_seconds = 8
    else:
        device_calls_total = max(0, estimated_calls - 1)  # minus global summary call
        avg_calls_per_device = device_calls_total / max(1, device_count)
        seconds_per_call = 8.0
        rounds = math.ceil(device_count / max(1, analysis_parallelism))
        effective_parallel = max(1, analysis_parallelism * (chunk_parallelism if fragmented else 1))
        parallel_gain = max(1.0, effective_parallel / max(1, analysis_parallelism))
        base_seconds = rounds * avg_calls_per_device * seconds_per_call + seconds_per_call
        base_seconds = base_seconds / parallel_gain

    retry_factor = 1.0 + (0.18 * max(0, analysis_retries))
    estimated_seconds = max(8, int(base_seconds * retry_factor))

    return {
        "device_count": device_count,
        "estimated_calls": estimated_calls,
        "estimated_total_tokens": estimated_tokens,
        "estimated_seconds": estimated_seconds,
    }


@router.get("/ai/settings", response_class=HTMLResponse)
async def ai_settings_page(request: Request):
    lang = str(request.query_params.get("lang", "zh") or "zh")
    cfg = load_gpt_config()
    stats = load_token_stats()
    has_keys = _has_keys(cfg)
    system_prompts = localized_prompt_catalog("system", lang)
    task_prompts = localized_prompt_catalog("task", lang)
    system_prompt_labels = localized_prompt_labels("system", lang)
    task_prompt_labels = localized_prompt_labels("task", lang)
    return templates.TemplateResponse(
        "ai_settings.html",
        {
            "request": request,
            "cfg": _redact_cfg(cfg),
            "has_keys": has_keys,
            "stats": stats,
            "system_prompts": system_prompts,
            "task_prompts": task_prompts,
            "system_prompt_labels": system_prompt_labels,
            "task_prompt_labels": task_prompt_labels,
            "chatgpt_model_options": CHATGPT_MODEL_OPTIONS,
            "deepseek_model_options": DEEPSEEK_MODEL_OPTIONS,
            "qwen_model_options": QWEN_MODEL_OPTIONS,
            "gemini_model_options": GEMINI_MODEL_OPTIONS,
            "nvidia_model_options": NVIDIA_MODEL_OPTIONS,
            "local_model_options": LOCAL_MODEL_OPTIONS,
            "error": None,
            "saved": False,
        },
    )


@router.post("/ai/settings", response_class=HTMLResponse)
async def ai_settings_save(
    request: Request,
    provider: str = Form("chatgpt"),
    chatgpt_api_key: str = Form(""),
    deepseek_api_key: str = Form(""),
    qwen_api_key: str = Form(""),
    gemini_api_key: str = Form(""),
    nvidia_api_key: str = Form(""),
    chatgpt_model: str = Form(""),
    local_base_url: str = Form(""),
    local_model: str = Form(""),
    deepseek_model: str = Form(""),
    qwen_model: str = Form(""),
    gemini_model: str = Form(""),
    nvidia_model: str = Form(""),
    selected_system_prompt: str = Form(""),
    selected_task_prompt: str = Form(""),
    system_prompt_extra: str = Form(""),
    task_prompt_extra: str = Form(""),
    batched_analysis: str = Form(""),
    fragmented_analysis: str = Form(""),
    analysis_parallelism: int = Form(2),
    large_report_chunk_items: int = Form(4),
    analysis_retries: int = Form(1),
):
    lang = str(request.query_params.get("lang", "zh") or "zh")
    cfg = load_gpt_config()
    cfg = _merge_cfg(
        cfg,
        {
            "provider": provider,
            "chatgpt_api_key": chatgpt_api_key,
            "deepseek_api_key": deepseek_api_key,
            "qwen_api_key": qwen_api_key,
            "gemini_api_key": gemini_api_key,
            "nvidia_api_key": nvidia_api_key,
            "chatgpt_model": chatgpt_model,
            "local_base_url": local_base_url,
            "local_model": local_model,
            "deepseek_model": deepseek_model,
            "qwen_model": qwen_model,
            "gemini_model": gemini_model,
            "nvidia_model": nvidia_model,
            "selected_system_prompt": selected_system_prompt,
            "selected_task_prompt": selected_task_prompt,
            "system_prompt_extra": system_prompt_extra,
            "task_prompt_extra": task_prompt_extra,
            "batched_analysis": batched_analysis,
            "fragmented_analysis": fragmented_analysis,
            "analysis_parallelism": analysis_parallelism,
            "large_report_chunk_items": large_report_chunk_items,
            "analysis_retries": analysis_retries,
        },
    )
    save_gpt_config(cfg)
    has_keys = _has_keys(cfg)
    system_prompts = localized_prompt_catalog("system", lang)
    task_prompts = localized_prompt_catalog("task", lang)
    system_prompt_labels = localized_prompt_labels("system", lang)
    task_prompt_labels = localized_prompt_labels("task", lang)

    return templates.TemplateResponse(
        "ai_settings.html",
        {
            "request": request,
            "cfg": _redact_cfg(cfg),
            "has_keys": has_keys,
            "stats": load_token_stats(),
            "system_prompts": system_prompts,
            "task_prompts": task_prompts,
            "system_prompt_labels": system_prompt_labels,
            "task_prompt_labels": task_prompt_labels,
            "chatgpt_model_options": CHATGPT_MODEL_OPTIONS,
            "deepseek_model_options": DEEPSEEK_MODEL_OPTIONS,
            "qwen_model_options": QWEN_MODEL_OPTIONS,
            "gemini_model_options": GEMINI_MODEL_OPTIONS,
            "nvidia_model_options": NVIDIA_MODEL_OPTIONS,
            "local_model_options": LOCAL_MODEL_OPTIONS,
            "error": None,
            "saved": True,
        },
    )


@router.get("/api/ai/settings")
async def ai_settings_api():
    cfg = load_gpt_config()
    return {
        "ok": True,
        "cfg": _redact_cfg(cfg),
        "has_keys": _has_keys(cfg),
        "stats": load_token_stats(),
        "model_options": {
            "chatgpt": CHATGPT_MODEL_OPTIONS,
            "deepseek": DEEPSEEK_MODEL_OPTIONS,
            "qwen": QWEN_MODEL_OPTIONS,
            "gemini": GEMINI_MODEL_OPTIONS,
            "nvidia": NVIDIA_MODEL_OPTIONS,
            "local": LOCAL_MODEL_OPTIONS,
        },
        "system_prompts": list(merged_system_prompt_catalog().keys()),
        "task_prompts": list(merged_task_prompt_catalog().keys()),
    }


@router.post("/api/ai/settings")
async def ai_settings_save_api(payload: dict[str, Any] = Body(default={})):
    cfg = _merge_cfg(load_gpt_config(), payload or {})
    save_gpt_config(cfg)
    return {"ok": True, "cfg": _redact_cfg(cfg), "has_keys": _has_keys(cfg)}


@router.post("/api/tasks/{task_id}/analysis/start")
async def start_analysis(task_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if isinstance(payload, dict) and payload:
        cfg = _merge_cfg(load_gpt_config(), payload)
        save_gpt_config(cfg)
    device_items: list[dict[str, str]] = []
    for i, d in enumerate(getattr(task, "devices", []) or []):
        ip = str(getattr(d, "device_ip", "") or "").strip()
        device_id = str(getattr(d, "device_id", "") or "").strip()
        if not device_id:
            device_id = f"dev-{i+1}"
        device_items.append({"device_id": device_id, "ip": ip or device_id, "index": i + 1})
    analysis_id = request.app.state.ai_manager.start(task_id, devices=device_items)
    return {"ok": True, "analysis_id": analysis_id}


@router.post("/api/tasks/{task_id}/analysis/precheck")
async def analysis_precheck(task_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    task = request.app.state.task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        estimation = _estimate_precheck(task, task_id, request, payload or {})
        line = (
            f"设备 {estimation['device_count']} 台 | 预计调用 {estimation['estimated_calls']} 次 | "
            f"预计 Token {estimation['estimated_total_tokens']} | 预计耗时 {estimation['estimated_seconds']}s"
        )
        return {"ok": True, "estimation": estimation, "line": line}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@router.get("/api/analysis/{analysis_id}")
async def get_analysis(analysis_id: str, request: Request):
    data = request.app.state.ai_manager.get(analysis_id)
    if not data:
        raise HTTPException(status_code=404, detail="analysis not found")
    return data


@router.get("/api/tasks/{task_id}/analysis/active")
async def get_task_active_analysis(task_id: str, request: Request):
    payload = request.app.state.ai_manager.get_active_by_task(task_id)
    if not payload:
        return {"ok": True, "active": False}
    return {"ok": True, "active": True, "analysis_id": payload.get("analysis_id", ""), "status": payload}


@router.get("/api/tasks/{task_id}/analysis/latest")
async def get_task_latest_analysis(task_id: str, request: Request):
    payload = request.app.state.ai_manager.get_latest_by_task(task_id)
    if not payload:
        return {"ok": True, "found": False}
    return {"ok": True, "found": True, "analysis_id": payload.get("analysis_id", ""), "status": payload}


@router.get("/api/tasks/{task_id}/analysis/history")
async def get_task_analysis_history(task_id: str, request: Request, limit: int = 12):
    items = request.app.state.ai_manager.list_history(task_id, limit=max(1, min(int(limit), 100)))
    return {"ok": True, "items": items}


@router.get("/api/tasks/{task_id}/analysis/history/{file_name}")
async def download_task_analysis_history(task_id: str, file_name: str, request: Request):
    safe_name = Path(file_name).name
    if safe_name != file_name or not safe_name.startswith("analysis_"):
        raise HTTPException(status_code=400, detail="invalid file")
    path = request.app.state.ai_manager.output_root / task_id / "ai_reports" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    media = "text/markdown; charset=utf-8" if safe_name.endswith(".md") else "application/json"
    return FileResponse(path.as_posix(), media_type=media, filename=safe_name)


@router.post("/api/ai/test_connection")
async def test_connection(
    provider: str = Form("chatgpt"),
    chatgpt_api_key: str = Form(""),
    deepseek_api_key: str = Form(""),
    qwen_api_key: str = Form(""),
    gemini_api_key: str = Form(""),
    nvidia_api_key: str = Form(""),
    chatgpt_model: str = Form(""),
    local_base_url: str = Form(""),
    local_model: str = Form(""),
    deepseek_model: str = Form(""),
    qwen_model: str = Form(""),
    gemini_model: str = Form(""),
    nvidia_model: str = Form(""),
):
    cfg = load_gpt_config()
    provider = (provider or "").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
        if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
            provider = "chatgpt"

    if not (local_base_url or "").strip():
        local_base_url = str(cfg.get("local_base_url", "") or "")
    if not (chatgpt_model or "").strip():
        chatgpt_model = str(cfg.get("chatgpt_model", "") or "")
    if not (deepseek_model or "").strip():
        deepseek_model = str(cfg.get("deepseek_model", "") or "")
    if not (qwen_model or "").strip():
        qwen_model = str(cfg.get("qwen_model", "") or "")
    if not (gemini_model or "").strip():
        gemini_model = str(cfg.get("gemini_model", "") or "")
    if not (nvidia_model or "").strip():
        nvidia_model = str(cfg.get("nvidia_model", "") or "")

    # Prefer provided key; fallback to saved key.
    saved_cfg = load_gpt_config()
    if provider == "chatgpt" and not (chatgpt_api_key or "").strip():
        chatgpt_api_key = str(saved_cfg.get("chatgpt_api_key", "") or "")
    elif provider == "deepseek" and not (deepseek_api_key or "").strip():
        deepseek_api_key = str(saved_cfg.get("deepseek_api_key", "") or "")
    elif provider == "qwen" and not (qwen_api_key or "").strip():
        qwen_api_key = str(saved_cfg.get("qwen_api_key", "") or "")
    elif provider == "gemini" and not (gemini_api_key or "").strip():
        gemini_api_key = str(saved_cfg.get("gemini_api_key", "") or "")
    elif provider == "nvidia" and not (nvidia_api_key or "").strip():
        nvidia_api_key = str(saved_cfg.get("nvidia_api_key", "") or "")
    try:
        if provider == "local":
            msg = await asyncio.to_thread(test_local_lmstudio_connection, local_base_url)
            return {
                "ok": True,
                "message": f"{msg} | Token余额: N/A（本地模型）",
                "provider_used": "local",
                "token_balance_status": "n/a",
                "token_balance_message": "N/A（本地模型）",
            }
        if provider == "deepseek":
            if not (deepseek_api_key or "").strip():
                return JSONResponse(status_code=400, content={"ok": False, "error": "DeepSeek API Key not set"})
            msg = await asyncio.to_thread(test_deepseek_connection, deepseek_api_key)
            return {
                "ok": True,
                "message": f"{msg} | Token余额: 未知",
                "provider_used": "deepseek",
                "token_balance_status": "unknown",
                "token_balance_message": "未知",
            }
        if provider == "qwen":
            if not (qwen_api_key or "").strip():
                return JSONResponse(status_code=400, content={"ok": False, "error": "QWEN API Key not set"})
            preferred = str(saved_cfg.get("qwen_base_url", "") or "")
            region, base, msg = await asyncio.to_thread(detect_qwen_endpoint, qwen_api_key, preferred)
            saved_cfg["qwen_base_url"] = base
            save_gpt_config(saved_cfg)
            return {
                "ok": True,
                "message": f"{msg} | 区域: {region.upper()} | Endpoint: {base} | Token余额: 未知",
                "provider_used": "qwen",
                "endpoint_region": region,
                "endpoint_base_url": base,
                "token_balance_status": "unknown",
                "token_balance_message": "未知",
            }
        if provider == "gemini":
            if not (gemini_api_key or "").strip():
                return JSONResponse(status_code=400, content={"ok": False, "error": "Gemini API Key not set"})
            msg = await asyncio.to_thread(test_gemini_connection, gemini_api_key)
            return {
                "ok": True,
                "message": f"{msg} | Token余额: 未知",
                "provider_used": "gemini",
                "token_balance_status": "unknown",
                "token_balance_message": "未知",
            }
        if provider == "nvidia":
            if not (nvidia_api_key or "").strip():
                return JSONResponse(status_code=400, content={"ok": False, "error": "NVIDIA API Key not set"})
            msg = await asyncio.to_thread(test_nvidia_connection, nvidia_api_key)
            return {
                "ok": True,
                "message": f"{msg} | Token余额: 未知",
                "provider_used": "nvidia",
                "token_balance_status": "unknown",
                "token_balance_message": "未知",
            }

        if not (chatgpt_api_key or "").strip():
            return JSONResponse(status_code=400, content={"ok": False, "error": "ChatGPT API Key not set"})
        msg = await asyncio.to_thread(test_openai_connection, chatgpt_api_key)
        return {
            "ok": True,
            "message": f"{msg} | Token余额: 未知",
            "provider_used": "chatgpt",
            "token_balance_status": "unknown",
            "token_balance_message": "未知",
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@router.get("/api/ai/prompt_preview")
async def prompt_preview(kind: str, name: str):
    kind = (kind or "").strip().lower()
    name = (name or "").strip()
    catalog = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    text = catalog.get(name, "")
    if not text:
        raise HTTPException(status_code=404, detail="prompt not found")
    return {"ok": True, "name": name, "kind": kind, "text": text}


@router.post("/api/ai/import_prompt")
async def import_prompt(kind: str = Form("task"), name: str = Form(""), prompt_file: UploadFile = File(...)):
    raw = await prompt_file.read()
    text = raw.decode("utf-8", errors="replace").strip()
    prompt_name = (name or "").strip() or Path(prompt_file.filename or "prompt").stem
    try:
        final_name = save_custom_prompt(kind, prompt_name, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "name": final_name, "kind": kind}


@router.post("/api/ai/prompt_update")
async def prompt_update(payload: dict[str, Any] = Body(default={})):
    kind = str((payload or {}).get("kind", "") or "").strip().lower()
    name = str((payload or {}).get("name", "") or "").strip()
    text = str((payload or {}).get("text", "") or "").strip()
    try:
        final_name = save_custom_prompt(kind, name, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prompts = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    return {"ok": True, "kind": kind, "name": final_name, "prompts": prompts, "is_custom": is_custom_prompt(kind, final_name)}


@router.post("/api/ai/prompt_delete")
async def prompt_delete(payload: dict[str, Any] = Body(default={})):
    kind = str((payload or {}).get("kind", "") or "").strip().lower()
    name = str((payload or {}).get("name", "") or "").strip()
    try:
        deleted = delete_custom_prompt(kind, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=400, detail="Only custom prompts can be deleted")
    prompts = merged_system_prompt_catalog() if kind == "system" else merged_task_prompt_catalog()
    return {"ok": True, "kind": kind, "name": name, "prompts": prompts}
