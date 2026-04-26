from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .state_store import (
    DEFAULT_CODEX_CLI_PATH,
    DEFAULT_CODEX_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GPT_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_NVIDIA_MODEL,
    DEFAULT_QWEN_BASE_URL,
    DEFAULT_QWEN_MODEL,
)

DEFAULT_SYSTEM_PROMPT = "You are a senior network log diagnosis assistant. Be rigorous and evidence-based."
QWEN_CN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _normalize_analysis_language(lang: str | None) -> str:
    return "en" if str(lang or "").strip().lower().startswith("en") else "zh"


def _localized_prompt_wrapper(lang: str) -> tuple[str, str, str]:
    if _normalize_analysis_language(lang) == "en":
        return "System Prompt", "Task Prompt", "Inspection Data"
    return "系统提示词", "任务提示词", "巡检数据"


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


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 120) -> dict[str, Any]:
    req = urlrequest.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
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
        with urlrequest.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
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


def _resolve_codex_cli(cli_path: str = "") -> str:
    raw = str(cli_path or "").strip()
    candidates: list[str] = []
    if raw:
        candidates.append(raw)
    env_path = str(os.environ.get("CODEX_CLI_PATH", "") or "").strip()
    if env_path and env_path not in candidates:
        candidates.append(env_path)
    auto = which("codex")
    if auto and auto not in candidates:
        candidates.append(auto)

    vscode_root = Path.home() / ".vscode" / "extensions"
    if vscode_root.exists():
        for path in sorted(vscode_root.glob("openai.chatgpt-*/bin/*/codex"), reverse=True):
            cand = str(path)
            if cand not in candidates:
                candidates.append(cand)

    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if p.exists():
            return cand
        # Bare command names like "codex" should only be accepted if actually resolvable in PATH.
        if p.name == cand and "/" not in cand:
            resolved = which(cand)
            if resolved:
                return resolved
    raise RuntimeError("codex CLI not found. Set codex_cli_path or ensure `codex` is in PATH")


def _extract_codex_usage(stderr_text: str) -> dict[str, int]:
    text = str(stderr_text or "")
    m = re.search(r"tokens used\s*([\d,]+)", text, flags=re.IGNORECASE)
    if not m:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    total = int(m.group(1).replace(",", "") or "0")
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": max(0, total)}


def _run_codex_local(
    cli_path: str,
    model: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    analysis_language: str = "en",
    timeout_sec: int = 240,
):
    cli = _resolve_codex_cli(cli_path)
    lang = _normalize_analysis_language(analysis_language)
    system_header, task_header, data_header = _localized_prompt_wrapper(lang)
    if lang == "en":
        preamble = (
            "You are running in non-interactive Codex CLI mode for network log diagnosis.\n"
            "Do not ask follow-up questions. Do not request tools. Provide the final analysis only.\n\n"
        )
    else:
        preamble = (
            "你正在以非交互模式运行 Codex CLI，用于网络日志诊断。\n"
            "不要追问，不要请求工具，只输出最终分析结果。\n\n"
        )
    prompt = (
        preamble
        + f"[{system_header}]\n{system_prompt or DEFAULT_SYSTEM_PROMPT}\n\n"
        + f"[{task_header}]\n{task_prompt}\n\n"
        + f"[{data_header}]\n{report_text}\n"
    )
    cmd = [
        cli,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(Path(__file__).resolve().parent.parent.parent),
    ]
    if str(model or "").strip():
        cmd.extend(["-m", str(model).strip()])
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as fh:
        out_path = fh.name
    cmd.extend(["-o", out_path, "-"])
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=max(30, int(timeout_sec or 240)),
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"codex exec failed (rc={proc.returncode}): {detail[:600]}")
        text = Path(out_path).read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError("empty analysis text")
        return text, _extract_codex_usage(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"codex exec timeout after {max(30, int(timeout_sec or 240))}s") from exc
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def test_codex_local_connection(model: str = DEFAULT_CODEX_MODEL, cli_path: str = DEFAULT_CODEX_CLI_PATH) -> str:
    cli = _resolve_codex_cli(cli_path)
    text, _usage = _run_codex_local(
        cli,
        model or DEFAULT_CODEX_MODEL,
        "Reply exactly with the requested text.",
        "Return exactly: CODEX_LOCAL_OK",
        "No inspection data.",
        timeout_sec=60,
    )
    if "CODEX_LOCAL_OK" not in text:
        raise RuntimeError(f"unexpected codex response: {text[:200]}")
    return f"Codex Local 连接成功，model={model or DEFAULT_CODEX_MODEL} cli={cli}"


def _run_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    task_prompt: str,
    report_text: str,
    analysis_language: str = "en",
):
    system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    lang = _normalize_analysis_language(analysis_language)
    if lang == "en":
        user_text = f"Task Requirements:\n{task_prompt}\n\nInspection Data:\n{report_text}"
    else:
        user_text = f"任务要求：\n{task_prompt}\n\n巡检数据：\n{report_text}"
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
    payload = _post_json(base_url.rstrip("/") + "/chat/completions", body, headers=headers)
    text = _extract_chat_text(payload)
    if not text:
        raise RuntimeError("empty analysis text")
    return text, _extract_usage(payload)


def run_analysis(llm: dict[str, str], report_text: str):
    provider = llm.get("provider", "chatgpt")
    system_prompt = llm.get("system_prompt_text", "")
    task_prompt = llm.get("task_prompt_text", "")
    analysis_language = _normalize_analysis_language(llm.get("analysis_language", "zh"))

    if provider == "local":
        base = (llm.get("local_base_url") or "").rstrip("/") + "/v1"
        model = llm.get("local_model") or DEFAULT_LOCAL_MODEL
        return _run_openai_compatible(base, "", model, system_prompt, task_prompt, report_text, analysis_language)
    if provider == "codex_local":
        model = llm.get("codex_model") or DEFAULT_CODEX_MODEL
        cli_path = llm.get("codex_cli_path") or DEFAULT_CODEX_CLI_PATH
        timeout_sec = int(str(llm.get("llm_call_timeout_sec") or "240") or "240")
        return _run_codex_local(
            cli_path,
            model,
            system_prompt,
            task_prompt,
            report_text,
            analysis_language=analysis_language,
            timeout_sec=timeout_sec,
        )
    if provider == "deepseek":
        key = llm.get("api_key", "")
        if not key:
            raise RuntimeError("DeepSeek API Key not set")
        model = llm.get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL
        return _run_openai_compatible(
            "https://api.deepseek.com/v1", key, model, system_prompt, task_prompt, report_text, analysis_language
        )
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
        for base in candidates:
            try:
                return _run_openai_compatible(
                    base,
                    key,
                    model,
                    system_prompt,
                    task_prompt,
                    report_text,
                    analysis_language,
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
            "https://integrate.api.nvidia.com/v1", key, model, system_prompt, task_prompt, report_text, analysis_language
        )
    if provider == "gemini":
        key = llm.get("api_key", "")
        if not key:
            raise RuntimeError("Gemini API Key not set")
        model = llm.get("gemini_model") or DEFAULT_GEMINI_MODEL
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        system_text = system_prompt or DEFAULT_SYSTEM_PROMPT
        if analysis_language == "en":
            user_text = f"Task Requirements:\n{task_prompt}\n\nInspection Data:\n{report_text}"
        else:
            user_text = f"任务要求：\n{task_prompt}\n\n巡检数据：\n{report_text}"
        body = {
            "contents": [{"parts": [{"text": user_text}]}],
            "systemInstruction": {"parts": [{"text": system_text}]},
            "generationConfig": {"temperature": 0.2},
        }
        payload = _post_json(url, body)
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
        "https://api.openai.com/v1", key, model, system_prompt, task_prompt, report_text, analysis_language
    )


def model_used(llm: dict[str, str]) -> str:
    provider = llm.get("provider", "chatgpt")
    if provider == "local":
        return llm.get("local_model", "")
    if provider == "codex_local":
        return llm.get("codex_model", "")
    if provider == "deepseek":
        return llm.get("deepseek_model", "")
    if provider == "qwen":
        return llm.get("qwen_model", "")
    if provider == "gemini":
        return llm.get("gemini_model", "")
    if provider == "nvidia":
        return llm.get("nvidia_model", "")
    return llm.get("chatgpt_model", "")
