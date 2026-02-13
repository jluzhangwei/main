#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import ssl
import time
from typing import Dict
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.1-70b-instruct"
MODEL_DISCOVERY_TTL_SEC = 600
MODEL_CANDIDATE_LIMIT = 6
_MODEL_LIST_CACHE = {}
DEFAULT_SYSTEM_PROMPT = "You are a senior network operations assistant. Be rigorous, evidence-based, and actionable."


def extract_token_usage(payload: Dict) -> Dict[str, int]:
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None else usage.get("input_tokens", 0)
    completion_tokens = usage.get("completion_tokens") if usage.get("completion_tokens") is not None else usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens")
    try:
        p = int(prompt_tokens or 0)
    except Exception:
        p = 0
    try:
        c = int(completion_tokens or 0)
    except Exception:
        c = 0
    if total_tokens is None:
        t = p + c
    else:
        try:
            t = int(total_tokens or 0)
        except Exception:
            t = p + c
    return {"prompt_tokens": max(0, p), "completion_tokens": max(0, c), "total_tokens": max(0, t)}


def parse_openai_response_text(payload: Dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload.get("output_text", "").strip():
        return payload["output_text"].strip()

    outputs = payload.get("output", [])
    if isinstance(outputs, list):
        texts = []
        for item in outputs:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str) and t.strip():
                        texts.append(t.strip())
        if texts:
            return "\n\n".join(texts)
    return ""


def build_openai_ssl_context() -> ssl.SSLContext:
    no_verify = os.environ.get("OPENAI_SSL_NO_VERIFY", "").strip() in {"1", "true", "yes", "on"}
    ssl_ctx = ssl.create_default_context()
    if no_verify:
        return ssl._create_unverified_context()  # nosec B323
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl_ctx


def _extract_chat_choice_text(payload: Dict) -> str:
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    if isinstance(choices, list) and choices:
        c0 = choices[0] if isinstance(choices[0], dict) else {}
        msg = c0.get("message", {}) if isinstance(c0, dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _compose_user_text(task_prompt: str, report_text: str) -> str:
    return f"Task Requirements:\n{task_prompt}\n\nInspection Data:\n{report_text}"


def _list_openai_models(api_key: str) -> list:
    req = urlrequest.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            mid = str(item.get("id", "") or "").strip()
            if mid:
                out.append(mid)
    return out


def _list_deepseek_models(api_key: str) -> list:
    req = urlrequest.Request("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            mid = str(item.get("id", "") or "").strip()
            if mid:
                out.append(mid)
    return out


def _list_gemini_models(api_key: str) -> list:
    req = urlrequest.Request(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}", method="GET")
    with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    models = payload.get("models", []) if isinstance(payload, dict) else []
    if not isinstance(models, list):
        return []
    out = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        if name.startswith("models/"):
            out.append(name.split("/", 1)[1])
        else:
            out.append(name)
    return out


def _build_model_candidates(requested_model: str, available_models: list, default_model: str = "") -> list:
    req = str(requested_model or "").strip()
    if not req:
        req = str(default_model or "").strip()
    available = [str(x).strip() for x in (available_models or []) if str(x).strip()]
    out = []
    seen = set()

    def add(v: str) -> None:
        vv = str(v or "").strip()
        if not vv or vv in seen:
            return
        seen.add(vv)
        out.append(vv)

    add(req)
    if req.startswith("meta/"):
        add("nvidia/" + req.split("/", 1)[1])
    if req.startswith("nvidia/"):
        add("meta/" + req.split("/", 1)[1])
    if req.startswith("models/"):
        add(req.split("/", 1)[1])
    else:
        add("models/" + req)

    req_lower = req.lower()
    req_tokens = [t for t in re.split(r"[^a-z0-9]+", req_lower) if t]
    major_tokens = [t for t in req_tokens if t in {"llama", "qwen", "mixtral", "nemotron", "mistral", "instruct", "gpt", "gemini"} or t.endswith("b")]

    for m in available:
        ml = m.lower()
        if ml == req_lower:
            add(m)
    for m in available:
        ml = m.lower()
        if req_lower and (req_lower in ml or ml in req_lower):
            add(m)
    if major_tokens:
        for m in available:
            ml = m.lower()
            if all(tok in ml for tok in major_tokens[:3]):
                add(m)
    if available:
        add(available[0])
    candidates = out or ([req] if req else ([default_model] if default_model else []))
    return candidates[:MODEL_CANDIDATE_LIMIT]


def _cache_key(provider: str, api_key: str) -> str:
    key_tail = str(api_key or "")[-8:]
    return f"{provider}:{key_tail}"


def _is_model_discovery_enabled() -> bool:
    flag = os.environ.get("HC_LLM_DISABLE_MODEL_DISCOVERY", "").strip().lower()
    return flag not in {"1", "true", "yes", "on"}


def _get_cached_model_list(provider: str, api_key: str, fetcher):
    now = time.time()
    k = _cache_key(provider, api_key)
    item = _MODEL_LIST_CACHE.get(k)
    if isinstance(item, dict):
        ts = float(item.get("ts", 0) or 0)
        if now - ts < MODEL_DISCOVERY_TTL_SEC:
            data = item.get("data", [])
            return data if isinstance(data, list) else []
    data = fetcher()
    if not isinstance(data, list):
        data = []
    _MODEL_LIST_CACHE[k] = {"ts": now, "data": data}
    return data


def call_openai_analysis(api_key: str, system_prompt: str, task_prompt: str, report_text: str, model: str = DEFAULT_GPT_MODEL) -> tuple:
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    user_text = _compose_user_text(task_prompt, report_text)
    available_models = []
    if _is_model_discovery_enabled():
        try:
            available_models = _get_cached_model_list("openai", api_key, lambda: _list_openai_models(api_key))
        except Exception:
            available_models = []
    model_candidates = _build_model_candidates(model, available_models, DEFAULT_GPT_MODEL)
    endpoints = [
        ("responses", "https://api.openai.com/v1/responses"),
        ("chat", "https://api.openai.com/v1/chat/completions"),
    ]
    tried = []
    last_error = ""

    for candidate_model in model_candidates:
        for endpoint_kind, url in endpoints:
            if endpoint_kind == "responses":
                body = {
                    "model": candidate_model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
                        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
                    ],
                }
            else:
                body = {
                    "model": candidate_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_text},
                        {"role": "user", "content": user_text},
                    ],
                }
            tried.append(f"{url}::{candidate_model}")
            req = urlrequest.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except ssl.SSLCertVerificationError as exc:
                raise RuntimeError(
                    "SSL certificate verify failed. "
                    "请先执行: pip3 install certifi；"
                    "macOS 可再执行: /Applications/Python\\ 3.9/Install\\ Certificates.command"
                ) from exc
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail[:300]}"
                if exc.code in {400, 404}:
                    continue
                raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:400]}") from exc
            except Exception as exc:
                raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

            try:
                payload = json.loads(raw)
            except Exception as exc:
                raise RuntimeError(f"OpenAI API response parse failed: {exc}") from exc

            if endpoint_kind == "responses":
                text = parse_openai_response_text(payload)
            else:
                text = _extract_chat_choice_text(payload)
            if text:
                return text, extract_token_usage(payload)
            last_error = "empty analysis text"
            continue

    raise RuntimeError(
        "OpenAI API analysis failed after fallback. "
        f"tried={tried}; last_error={last_error or 'unknown'}; "
        f"requested_model={model}; available_models_sample={available_models[:12]}"
    )


def test_openai_connection(api_key: str) -> str:
    req = urlrequest.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    ssl_ctx = build_openai_ssl_context()
    try:
        with urlrequest.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "SSL certificate verify failed. "
            "请先执行: pip3 install certifi；"
            "macOS 可再执行: /Applications/Python\\ 3.9/Install\\ Certificates.command"
        ) from exc
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
        return f"OpenAI 连接成功，models={count}"
    except Exception:
        return "OpenAI 连接成功"


def call_deepseek_analysis(api_key: str, system_prompt: str, task_prompt: str, report_text: str, model: str = DEFAULT_DEEPSEEK_MODEL) -> tuple:
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    user_text = _compose_user_text(task_prompt, report_text)
    available_models = []
    if _is_model_discovery_enabled():
        try:
            available_models = _get_cached_model_list("deepseek", api_key, lambda: _list_deepseek_models(api_key))
        except Exception:
            available_models = []
    model_candidates = _build_model_candidates(model, available_models, DEFAULT_DEEPSEEK_MODEL)
    endpoints = [
        "https://api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com/chat/completions",
    ]
    tried = []
    last_error = ""
    for candidate_model in model_candidates:
        for url in endpoints:
            body = {
                "model": candidate_model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
            }
            tried.append(f"{url}::{candidate_model}")
            req = urlrequest.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail[:300]}"
                if exc.code in {400, 404}:
                    continue
                raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail[:400]}") from exc
            except Exception as exc:
                raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            text = _extract_chat_choice_text(payload if isinstance(payload, dict) else {})
            if text:
                return text, extract_token_usage(payload if isinstance(payload, dict) else {})
            last_error = "empty analysis text"
            continue

    raise RuntimeError(
        "DeepSeek API analysis failed after fallback. "
        f"tried={tried}; last_error={last_error or 'unknown'}; "
        f"requested_model={model}; available_models_sample={available_models[:12]}"
    )


def test_deepseek_connection(api_key: str) -> str:
    req = urlrequest.Request("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
        return f"DeepSeek 连接成功，models={count}"
    except Exception:
        return "DeepSeek 连接成功"


def _normalize_gemini_model(model: str) -> str:
    m = (model or "").strip()
    if not m:
        m = DEFAULT_GEMINI_MODEL
    if m.startswith("models/"):
        return m
    return f"models/{m}"


def _extract_gemini_usage(payload: Dict) -> Dict[str, int]:
    usage = payload.get("usageMetadata", {})
    if not isinstance(usage, dict):
        usage = {}
    try:
        p = int(usage.get("promptTokenCount", 0) or 0)
    except Exception:
        p = 0
    try:
        c = int(usage.get("candidatesTokenCount", 0) or 0)
    except Exception:
        c = 0
    try:
        t = int(usage.get("totalTokenCount", p + c) or (p + c))
    except Exception:
        t = p + c
    return {"prompt_tokens": max(0, p), "completion_tokens": max(0, c), "total_tokens": max(0, t)}


def call_gemini_analysis(api_key: str, system_prompt: str, task_prompt: str, report_text: str, model: str = DEFAULT_GEMINI_MODEL) -> tuple:
    user_text = _compose_user_text(task_prompt, report_text)
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    available_models = []
    if _is_model_discovery_enabled():
        try:
            available_models = _get_cached_model_list("gemini", api_key, lambda: _list_gemini_models(api_key))
        except Exception:
            available_models = []
    requested = model.split("/", 1)[1] if str(model).startswith("models/") else str(model or "")
    model_candidates = _build_model_candidates(requested, available_models, DEFAULT_GEMINI_MODEL)
    api_versions = ["v1beta", "v1"]
    tried = []
    last_error = ""

    for candidate in model_candidates:
        model_path = candidate if str(candidate).startswith("models/") else f"models/{candidate}"
        for version in api_versions:
            body = {
                "contents": [{"parts": [{"text": user_text}]}],
                "systemInstruction": {"parts": [{"text": system_text}]},
                "generationConfig": {"temperature": 0.2},
            }
            url = f"https://generativelanguage.googleapis.com/{version}/{model_path}:generateContent?key={api_key}"
            tried.append(url)
            req = urlrequest.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail[:300]}"
                if exc.code in {400, 404}:
                    continue
                raise RuntimeError(f"Gemini API HTTP {exc.code}: {detail[:400]}") from exc
            except Exception as exc:
                raise RuntimeError(f"Gemini API request failed: {exc}") from exc

            try:
                payload = json.loads(raw)
            except Exception as exc:
                raise RuntimeError(f"Gemini API response parse failed: {exc}") from exc

            candidates = payload.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                c0 = candidates[0] if isinstance(candidates[0], dict) else {}
                content = c0.get("content", {}) if isinstance(c0, dict) else {}
                parts = content.get("parts", []) if isinstance(content, dict) else []
                texts = []
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict):
                            txt = part.get("text", "")
                            if isinstance(txt, str) and txt.strip():
                                texts.append(txt.strip())
                if texts:
                    return "\n\n".join(texts), _extract_gemini_usage(payload)
            last_error = "empty analysis text"
            continue

    raise RuntimeError(
        "Gemini API analysis failed after fallback. "
        f"tried={tried}; last_error={last_error or 'unknown'}; "
        f"requested_model={model}; available_models_sample={available_models[:12]}"
    )


def test_gemini_connection(api_key: str) -> str:
    req = urlrequest.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gemini API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("models", [])) if isinstance(payload.get("models", []), list) else 0
        return f"Gemini 连接成功，models={count}"
    except Exception:
        return "Gemini 连接成功"


def call_nvidia_analysis(api_key: str, system_prompt: str, task_prompt: str, report_text: str, model: str = DEFAULT_NVIDIA_MODEL) -> tuple:
    resolved_model = (model or DEFAULT_NVIDIA_MODEL).strip()
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    user_text = _compose_user_text(task_prompt, report_text)
    endpoints = [
        (
            "https://integrate.api.nvidia.com/v1/chat/completions",
            "chat",
        ),
        (
            "https://integrate.api.nvidia.com/v1/completions",
            "completion",
        ),
        (
            "https://integrate.api.nvidia.com/v1beta/chat/completions",
            "chat",
        ),
    ]
    available_models = []
    if _is_model_discovery_enabled():
        try:
            available_models = _get_cached_model_list("nvidia", api_key, lambda: _list_nvidia_models(api_key))
        except Exception:
            available_models = []
    model_candidates = _build_model_candidates(resolved_model, available_models, DEFAULT_NVIDIA_MODEL)

    last_http_error = ""
    tried = []
    for candidate_model in model_candidates:
        for url, mode in endpoints:
            if mode == "completion":
                body = {
                    "model": candidate_model,
                    "temperature": 0.2,
                    "prompt": f"{system_text}\n\n{user_text}",
                }
            else:
                body = {
                    "model": candidate_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_text},
                        {"role": "user", "content": user_text},
                    ],
                }
            tried.append(f"{url}::{candidate_model}")
            req = urlrequest.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_http_error = f"HTTP {exc.code}: {detail[:300]}"
                if exc.code in {400, 404}:
                    continue
                raise RuntimeError(f"NVIDIA API HTTP {exc.code}: {detail[:400]}") from exc
            except Exception as exc:
                raise RuntimeError(f"NVIDIA API request failed: {exc}") from exc

            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            choices = payload.get("choices", []) if isinstance(payload, dict) else []
            if isinstance(choices, list) and choices:
                if mode == "completion":
                    txt = choices[0].get("text", "") if isinstance(choices[0], dict) else ""
                    if isinstance(txt, str) and txt.strip():
                        return txt.strip(), extract_token_usage(payload if isinstance(payload, dict) else {})
                else:
                    msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                    if isinstance(content, str) and content.strip():
                        return content.strip(), extract_token_usage(payload if isinstance(payload, dict) else {})
            # Successful HTTP but empty content: try next endpoint/candidate.
            last_http_error = "empty analysis text"
            continue

    raise RuntimeError(
        "NVIDIA API analysis failed after endpoint fallback. "
        f"tried={tried}; last_error={last_http_error or 'unknown'}; "
        f"requested_model={resolved_model}; available_models_sample={available_models[:12]}"
    )


def _list_nvidia_models(api_key: str) -> list:
    req = urlrequest.Request(
        "https://integrate.api.nvidia.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
    models = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "") or "").strip()
        if model_id:
            models.append(model_id)
    return models


def test_nvidia_connection(api_key: str) -> str:
    req = urlrequest.Request(
        "https://integrate.api.nvidia.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=30, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NVIDIA API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"NVIDIA API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
        return f"NVIDIA 连接成功，models={count}"
    except Exception:
        return "NVIDIA 连接成功"


def call_local_lmstudio_analysis(base_url: str, model: str, system_prompt: str, task_prompt: str, report_text: str) -> tuple:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("LM Studio base_url is empty")
    if not model.strip():
        raise RuntimeError("LM Studio model is empty")

    body = {
        "model": model.strip(),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": _compose_user_text(task_prompt, report_text)},
        ],
    }
    req = urlrequest.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio API HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"LM Studio API request failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"LM Studio response parse failed: {exc}") from exc

    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content.strip():
            return content.strip(), extract_token_usage(payload)
    raise RuntimeError("LM Studio returned empty analysis text")


def test_local_lmstudio_connection(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("LM Studio base_url is empty")
    req = urlrequest.Request(f"{base}/v1/models", method="GET")
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio API HTTP {exc.code}: {detail[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"LM Studio API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        data = payload.get("data", [])
        count = len(data) if isinstance(data, list) else 0
        return f"LM Studio 连接成功，models={count}"
    except Exception:
        return "LM Studio 连接成功"
