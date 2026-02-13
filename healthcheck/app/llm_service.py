#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import ssl
from typing import Dict
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_GPT_MODEL = "gpt-4.1-mini"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


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


def call_openai_analysis(api_key: str, system_prompt: str, task_prompt: str, report_text: str, model: str = DEFAULT_GPT_MODEL) -> tuple:
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt or "你是资深网络运维专家，输出要结构化、可落地。"}]},
            {"role": "user", "content": [{"type": "input_text", "text": f"任务要求：\n{task_prompt}\n\n巡检数据：\n{report_text}"}]},
        ],
    }
    req = urlrequest.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    ssl_ctx = build_openai_ssl_context()

    try:
        with urlrequest.urlopen(req, timeout=120, context=ssl_ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            "SSL certificate verify failed. "
            "请先执行: pip3 install certifi；"
            "macOS 可再执行: /Applications/Python\\ 3.9/Install\\ Certificates.command"
        ) from exc
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:400]}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"OpenAI API response parse failed: {exc}") from exc

    text = parse_openai_response_text(payload)
    if not text:
        raise RuntimeError("OpenAI API returned empty analysis text")
    return text, extract_token_usage(payload)


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
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt or "你是资深网络运维专家，输出要结构化、可落地。"},
            {"role": "user", "content": f"任务要求：\n{task_prompt}\n\n巡检数据：\n{report_text}"},
        ],
    }
    req = urlrequest.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=120, context=build_openai_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc}") from exc
    try:
        payload = json.loads(raw)
        choices = payload.get("choices", [])
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, str) and content.strip():
                return content.strip(), extract_token_usage(payload)
    except Exception:
        pass
    raise RuntimeError("DeepSeek API returned empty analysis text")


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
            {"role": "system", "content": system_prompt or "你是资深网络运维专家，输出要结构化、可落地。"},
            {"role": "user", "content": f"任务要求：\n{task_prompt}\n\n巡检数据：\n{report_text}"},
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
