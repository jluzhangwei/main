from __future__ import annotations

import json
import os
import ssl
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .state_store import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GPT_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_NVIDIA_MODEL,
    DEFAULT_QWEN_BASE_URL,
    DEFAULT_QWEN_MODEL,
)

DEFAULT_SYSTEM_PROMPT = "You are a senior network device fault-diagnosis assistant. Be rigorous, evidence-based, and avoid speculation."
QWEN_CN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _normalize_api_key(api_key: str) -> str:
    key = str(api_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key.split(" ", 1)[1].strip()
    return key


def _ssl_context() -> ssl.SSLContext:
    no_verify = os.environ.get("OPENAI_SSL_NO_VERIFY", "").strip().lower() in {"1", "true", "yes", "on"}
    if no_verify:
        return ssl._create_unverified_context()  # nosec B323

    # Priority: OPENAI_CA_BUNDLE > REQUESTS_CA_BUNDLE > SSL_CERT_FILE.
    ca_bundle = (
        os.environ.get("OPENAI_CA_BUNDLE", "").strip()
        or os.environ.get("REQUESTS_CA_BUNDLE", "").strip()
        or os.environ.get("SSL_CERT_FILE", "").strip()
    )
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)

    # Default to system trust store, optional certifi fallback.
    prefer = os.environ.get("OPENAI_SSL_TRUST_STORE", "").strip().lower()
    if prefer in {"", "system"}:
        return ssl.create_default_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _certifi_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _is_cert_verify_error(exc: Exception) -> bool:
    low = str(exc or "").lower()
    return ("certificate verify failed" in low) or ("certificateverifyfailed" in low)


def _urlopen_with_ssl_fallback(req: urlrequest.Request, timeout: int):
    primary = _ssl_context()
    try:
        return urlrequest.urlopen(req, timeout=timeout, context=primary)
    except Exception as exc:
        if not _is_cert_verify_error(exc):
            raise
        alt = _certifi_ssl_context()
        if alt is None:
            raise RuntimeError(
                f"request failed with certificate verification error: {exc}; "
                "install certifi or set OPENAI_SSL_NO_VERIFY=1 for controlled environments"
            ) from exc
        return urlrequest.urlopen(req, timeout=timeout, context=alt)


def _extract_chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    if isinstance(choices, list) and choices:
        c0 = choices[0] if isinstance(choices[0], dict) else {}
        msg = c0.get("message", {}) if isinstance(c0, dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _extract_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    p = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    c = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    t = int(usage.get("total_tokens", p + c) or (p + c))
    return {"prompt_tokens": max(0, p), "completion_tokens": max(0, c), "total_tokens": max(0, t)}


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 45) -> dict[str, Any]:
    req = urlrequest.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with _urlopen_with_ssl_fallback(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"request failed: {exc}") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"response parse failed: {exc}") from exc


def _get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    req = urlrequest.Request(url, headers=(headers or {}), method="GET")
    try:
        with _urlopen_with_ssl_fallback(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        return payload
    except Exception as exc:
        raise RuntimeError(f"response parse failed: {exc}") from exc


def test_openai_connection(api_key: str) -> str:
    key = _normalize_api_key(api_key)
    payload = _get_json("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
    return f"ChatGPT 连接成功，models={count}"


def test_deepseek_connection(api_key: str) -> str:
    key = _normalize_api_key(api_key)
    payload = _get_json("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
    return f"DeepSeek 连接成功，models={count}"


def test_qwen_connection(api_key: str, base_url: str = DEFAULT_QWEN_BASE_URL) -> str:
    key = _normalize_api_key(api_key)
    base = (base_url or DEFAULT_QWEN_BASE_URL).strip().rstrip("/")
    payload = _get_json(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
    return f"QWEN 连接成功，models={count}"


def detect_qwen_endpoint(api_key: str, preferred_base_url: str = "") -> tuple[str, str, str]:
    pref = (preferred_base_url or "").strip().rstrip("/")
    candidates: list[tuple[str, str]] = [("intl", QWEN_INTL_BASE_URL), ("cn", QWEN_CN_BASE_URL)]
    if pref:
        candidates = sorted(candidates, key=lambda x: 0 if x[1] == pref else 1)
    errors: list[str] = []
    for region, base in candidates:
        try:
            msg = test_qwen_connection(api_key, base)
            return region, base, msg
        except Exception as exc:
            errors.append(f"{region}:{exc}")
    raise RuntimeError("QWEN endpoint auto-detect failed: " + " | ".join(errors))


def test_gemini_connection(api_key: str) -> str:
    payload = _get_json(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}")
    count = len(payload.get("models", [])) if isinstance(payload.get("models", []), list) else 0
    return f"Gemini 连接成功，models={count}"


def test_nvidia_connection(api_key: str) -> str:
    key = _normalize_api_key(api_key)
    payload = _get_json("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
    return f"NVIDIA 连接成功，models={count}"


def test_local_lmstudio_connection(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("local_base_url not set")
    payload = _get_json(base + "/v1/models")
    count = len(payload.get("data", [])) if isinstance(payload.get("data", []), list) else 0
    return f"LM Studio 连接成功，models={count}"


def _run_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    timeout_sec: int = 45,
):
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    user_text = f"Task Requirements:\n{task_prompt}\n\nInspection Data:\n{report_text}"
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
    }
    key = _normalize_api_key(api_key)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = _post_json(
        base_url.rstrip("/") + "/chat/completions",
        body,
        headers=headers,
        timeout=max(5, min(int(timeout_sec or 45), 600)),
    )
    text = _extract_chat_text(payload)
    if not text:
        raise RuntimeError("empty analysis text")
    return text, _extract_usage(payload)


def run_analysis(llm: dict[str, str], report_text: str, request_timeout_sec: int | None = None):
    provider = llm.get("provider", "chatgpt")
    system_prompt = llm.get("system_prompt_text", "")
    task_prompt = llm.get("task_prompt_text", "")
    timeout_sec = max(5, min(int(request_timeout_sec or 45), 300))

    if provider == "local":
        base = (llm.get("local_base_url") or "").rstrip("/") + "/v1"
        model = llm.get("local_model") or DEFAULT_LOCAL_MODEL
        return _run_openai_compatible(base, "", model, system_prompt, task_prompt, report_text, timeout_sec=timeout_sec)
    if provider == "deepseek":
        key = llm.get("api_key", "")
        if not key:
            raise RuntimeError("DeepSeek API Key not set")
        model = llm.get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL
        return _run_openai_compatible("https://api.deepseek.com/v1", key, model, system_prompt, task_prompt, report_text, timeout_sec=timeout_sec)
    if provider == "qwen":
        key = _normalize_api_key(llm.get("api_key", ""))
        if not key:
            raise RuntimeError("QWEN API Key not set")
        model = llm.get("qwen_model") or DEFAULT_QWEN_MODEL
        base_url = (llm.get("qwen_base_url") or DEFAULT_QWEN_BASE_URL).strip().rstrip("/")
        candidates = [base_url]
        for alt in (QWEN_INTL_BASE_URL, QWEN_CN_BASE_URL):
            if alt not in candidates:
                candidates.append(alt)
        last_exc: Exception | None = None
        deadline = time.monotonic() + float(timeout_sec)
        for base in candidates:
            remain = max(5, int(deadline - time.monotonic()))
            if remain <= 1:
                raise RuntimeError("QWEN call timeout budget exhausted")
            try:
                return _run_openai_compatible(
                    base,
                    key,
                    model,
                    system_prompt,
                    task_prompt,
                    report_text,
                    timeout_sec=remain,
                )
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"QWEN call failed on all endpoints, last error: {last_exc}")
    if provider == "nvidia":
        key = llm.get("api_key", "")
        if not key:
            raise RuntimeError("NVIDIA API Key not set")
        model = llm.get("nvidia_model") or DEFAULT_NVIDIA_MODEL
        return _run_openai_compatible(
            "https://integrate.api.nvidia.com/v1",
            key,
            model,
            system_prompt,
            task_prompt,
            report_text,
            timeout_sec=timeout_sec,
        )
    if provider == "gemini":
        key = llm.get("api_key", "")
        if not key:
            raise RuntimeError("Gemini API Key not set")
        model = llm.get("gemini_model") or DEFAULT_GEMINI_MODEL
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
        user_text = f"Task Requirements:\n{task_prompt}\n\nInspection Data:\n{report_text}"
        body = {
            "contents": [{"parts": [{"text": user_text}]}],
            "systemInstruction": {"parts": [{"text": system_text}]},
            "generationConfig": {"temperature": 0.2},
        }
        payload = _post_json(url, body, timeout=timeout_sec)
        cands = payload.get("candidates", []) if isinstance(payload, dict) else []
        if isinstance(cands, list) and cands:
            parts = cands[0].get("content", {}).get("parts", [])
            if isinstance(parts, list):
                text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
                if text:
                    return text, _extract_usage(payload)
        raise RuntimeError("empty analysis text")

    key = llm.get("api_key", "")
    if not key:
        raise RuntimeError("ChatGPT API Key not set")
    model = llm.get("chatgpt_model") or DEFAULT_GPT_MODEL
    return _run_openai_compatible(
        "https://api.openai.com/v1",
        key,
        model,
        system_prompt,
        task_prompt,
        report_text,
        timeout_sec=timeout_sec,
    )


def model_used(llm: dict[str, str]) -> str:
    provider = llm.get("provider", "chatgpt")
    if provider == "local":
        return llm.get("local_model", "")
    if provider == "deepseek":
        return llm.get("deepseek_model", "")
    if provider == "qwen":
        return llm.get("qwen_model", "")
    if provider == "gemini":
        return llm.get("gemini_model", "")
    if provider == "nvidia":
        return llm.get("nvidia_model", "")
    return llm.get("chatgpt_model", "")
