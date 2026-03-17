from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..ai.llm_client import model_used, run_analysis
from ..ai.prompt_store import merged_system_prompt_catalog, merged_task_prompt_catalog
from ..ai.state_store import load_gpt_config
from ..diagnosis.intent_catalog import (
    INTENT_DESCRIPTIONS,
    allowed_intents_for_profile,
    command_for_intent,
    default_plan_for_profile,
    normalize_profile,
)
from ..diagnosis.evidence_parser import extract_round_evidence, format_evidence_brief
from ..diagnosis.case_store import NetdiagCaseStore
from ..diagnosis.config_store import NetdiagConfigStore
from ..diagnosis.duel_store import NetdiagDuelStore
from ..diagnosis.known_issue_store import NetdiagKnownIssueStore
from ..diagnosis.learning_store import NetdiagLearningStore
from ..diagnosis.models import CommandExecution, DiagnosisSessionCreate, PlannedCommand
from ..diagnosis.policy import has_placeholder_token, is_read_only_command
from ..diagnosis.state_store import NetdiagStateStore
from ..diagnosis.sop_engine import (
    DOMAIN_INTENT_PIPELINE,
    build_retrospective,
    build_stop_decision,
    derive_domains,
    propose_sop_steps,
    rank_hypotheses,
    score_hypotheses,
    seed_hypotheses,
)
from ..integrations.connection_store import NetdiagConnectionStore
from ..integrations.zabbix_client import ZabbixClient, ZabbixConfig
from ..integrations.zabbix_store import NetdiagZabbixStore
from ..services.command_service import run_read_only_commands
from ..services.device_service import run_device_collection

router = APIRouter(tags=["netdiag"])
templates = Jinja2Templates(directory=(Path(__file__).resolve().parent.parent / "templates").as_posix())

STALE_PLANNING_RECOVER_SEC = 120
STALE_EXECUTING_RECOVER_SEC = 240
STALE_ANALYZING_RECOVER_SEC = 75
PLAN_DEFAULT_TIMEOUT_SEC = 60
PLAN_MAX_TIMEOUT_SEC = 240
ANALYZE_DEFAULT_TIMEOUT_SEC = 50
ANALYZE_MAX_TIMEOUT_SEC = 240
ANALYZE_DEFAULT_CHUNK_MAX = 4
ANALYZE_DEFAULT_CHUNK_SIZE = 1000
ANALYZE_DEFAULT_CHUNK_OVERLAP = 80
ANALYZE_HARD_TOTAL_SEC = 50
ANALYZE_EXTERNAL_TIMEOUT_SEC = 6
ANALYZE_MIN_LLM_TIMEOUT_SEC = 8
ANALYZE_REPORT_CHAR_LIMIT = 16000


class _UserStopRequested(RuntimeError):
    pass


def _no_cache(resp: HTMLResponse) -> HTMLResponse:
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _provider_api_key(cfg: dict[str, Any], provider: str) -> str:
    if provider == "chatgpt":
        return str(cfg.get("chatgpt_api_key") or "")
    if provider == "deepseek":
        return str(cfg.get("deepseek_api_key") or "")
    if provider == "qwen":
        return str(cfg.get("qwen_api_key") or "")
    if provider == "gemini":
        return str(cfg.get("gemini_api_key") or "")
    if provider == "nvidia":
        return str(cfg.get("nvidia_api_key") or "")
    return ""


def _stop_registry(request: Request) -> dict[str, dict[str, Any]]:
    raw = getattr(request.app.state, "diag_stop_registry", None)
    if isinstance(raw, dict):
        return raw
    reg: dict[str, dict[str, Any]] = {}
    request.app.state.diag_stop_registry = reg
    return reg


def _set_stop_requested(request: Request, session_id: str, reason: str = "") -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    info = {
        "requested": True,
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": str(reason or "").strip() or "manual stop",
    }
    _stop_registry(request)[sid] = info
    return info


def _clear_stop_requested(request: Request, session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    _stop_registry(request).pop(sid, None)


def _stop_request_info(request: Request, session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    raw = _stop_registry(request).get(sid)
    return dict(raw) if isinstance(raw, dict) else {}


def _is_stop_requested(request: Request, session_id: str) -> bool:
    return bool(_stop_request_info(request, session_id))


def _assert_not_stopped(request: Request, session_id: str, endpoint: str) -> None:
    info = _stop_request_info(request, session_id)
    if not info:
        return
    reason = str(info.get("reason") or "manual stop").strip() or "manual stop"
    raise HTTPException(
        status_code=409,
        detail=f"{endpoint} blocked: session is paused by emergency stop ({reason}).",
    )


def _raise_if_stop_requested(request: Request, session_id: str, stage: str = "") -> None:
    info = _stop_request_info(request, session_id)
    if not info:
        return
    reason = str(info.get("reason") or "manual stop").strip() or "manual stop"
    stage_text = f" at {stage}" if stage else ""
    raise _UserStopRequested(f"stop requested{stage_text}: {reason}")


def _provider_model_key(provider: str) -> str:
    p = str(provider or "").strip().lower()
    if p == "chatgpt":
        return "chatgpt_model"
    if p == "deepseek":
        return "deepseek_model"
    if p == "qwen":
        return "qwen_model"
    if p == "gemini":
        return "gemini_model"
    if p == "nvidia":
        return "nvidia_model"
    if p == "local":
        return "local_model"
    return "chatgpt_model"


def _provider_label(provider: str) -> str:
    p = str(provider or "").strip().lower()
    if p == "chatgpt":
        return "ChatGPT"
    if p == "deepseek":
        return "DeepSeek"
    if p == "qwen":
        return "QWEN"
    if p == "gemini":
        return "Gemini"
    if p == "nvidia":
        return "NVIDIA"
    if p == "local":
        return "Local"
    return p or "unknown"


def _llm_input_readiness(llm_input: dict[str, str] | None) -> tuple[bool, str]:
    row = llm_input if isinstance(llm_input, dict) else {}
    provider = str(row.get("provider") or "").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        return False, "unsupported provider"
    if provider == "local":
        base = str(row.get("local_base_url") or "").strip()
        if not base:
            return False, "local_base_url not set"
        return True, ""
    api_key = str(row.get("api_key") or "").strip()
    if not api_key:
        return False, f"{_provider_label(provider)} API Key not set"
    return True, ""


def _resolve_llm_runtime_inputs(
    llm_primary: dict[str, str] | None,
    llm_failover: dict[str, str] | None,
) -> tuple[dict[str, str], dict[str, str] | None, dict[str, Any]]:
    primary = dict(llm_primary or {})
    failover = dict(llm_failover or {}) if isinstance(llm_failover, dict) else None

    p_ok, p_err = _llm_input_readiness(primary)
    f_ok, f_err = _llm_input_readiness(failover) if failover else (False, "")

    diag: dict[str, Any] = {
        "primary_provider": str(primary.get("provider") or "").strip().lower(),
        "primary_model": str(model_used(primary) or "").strip(),
        "primary_ready": bool(p_ok),
        "primary_error": str(p_err or "").strip(),
        "failover_provider": str((failover or {}).get("provider") or "").strip().lower(),
        "failover_model": str(model_used(failover or {}) or "").strip(),
        "failover_ready": bool(f_ok),
        "failover_error": str(f_err or "").strip(),
        "switched_to_failover": False,
        "no_ready_model": False,
        "unavailable_reason": "",
    }

    if p_ok:
        if failover and not f_ok:
            failover = None
        return primary, failover, diag

    if failover and f_ok:
        diag["switched_to_failover"] = True
        diag["unavailable_reason"] = (
            f"primary {_provider_label(diag.get('primary_provider') or '')} unavailable: {p_err}; "
            f"switched to failover {_provider_label(diag.get('failover_provider') or '')}"
        )
        return failover, None, diag

    diag["no_ready_model"] = True
    reasons: list[str] = []
    if p_err:
        reasons.append(f"primary {_provider_label(diag.get('primary_provider') or '')}: {p_err}")
    if failover:
        reasons.append(f"failover {_provider_label(diag.get('failover_provider') or '')}: {f_err or 'unavailable'}")
    diag["unavailable_reason"] = "; ".join(reasons) if reasons else "no available model route"
    return primary, None, diag


def _sanitize_llm_selector_item(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    provider = str(raw.get("provider") or "").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        return {}
    model = str(raw.get("model") or "").strip()
    if not model:
        provider_model_key = _provider_model_key(provider)
        model = str(raw.get(provider_model_key) or "").strip()
    row: dict[str, str] = {"provider": provider, "model": model}
    if provider == "qwen":
        qwen_base_url = str(raw.get("qwen_base_url") or "").strip()
        if qwen_base_url:
            row["qwen_base_url"] = qwen_base_url
    if provider == "local":
        local_base_url = str(raw.get("local_base_url") or "").strip()
        if local_base_url:
            row["local_base_url"] = local_base_url
    return row


def _resolve_llm_route(payload: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    body = payload if isinstance(payload, dict) else {}
    raw = body.get("llm_route")
    if not isinstance(raw, dict):
        return {}
    primary = _sanitize_llm_selector_item(raw.get("primary"))
    failover = _sanitize_llm_selector_item(raw.get("failover"))
    out: dict[str, dict[str, str]] = {}
    if primary:
        out["primary"] = primary
    if failover:
        out["failover"] = failover
    return out


def _llm_signature(llm_input: dict[str, str] | None) -> str:
    row = llm_input if isinstance(llm_input, dict) else {}
    provider = str(row.get("provider") or "").strip().lower()
    model = str(model_used(row) or "").strip()
    if not provider:
        return ""
    return f"{provider}:{model}"


def _build_llm_input(selector: dict[str, str] | None = None) -> dict[str, str]:
    cfg = load_gpt_config()
    provider = str(cfg.get("provider", "chatgpt") or "chatgpt").strip().lower()
    if provider not in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        provider = "chatgpt"

    selector_row = selector if isinstance(selector, dict) else {}
    selector_provider = str(selector_row.get("provider") or "").strip().lower()
    if selector_provider in {"chatgpt", "local", "deepseek", "qwen", "gemini", "nvidia"}:
        provider = selector_provider

    system_prompts = merged_system_prompt_catalog()
    task_prompts = merged_task_prompt_catalog()
    sys_key = str(cfg.get("selected_system_prompt") or "网络设备故障诊断专家-严格模式")
    task_key = str(cfg.get("selected_task_prompt") or "网络设备故障诊断-标准版")
    sys_text = system_prompts.get(sys_key, next(iter(system_prompts.values()), ""))
    task_text = task_prompts.get(task_key, next(iter(task_prompts.values()), ""))

    extra_sys = str(cfg.get("system_prompt_extra") or "").strip()
    extra_task = str(cfg.get("task_prompt_extra") or "").strip()
    if extra_sys:
        sys_text += "\n\n[Extra System Constraints]\n" + extra_sys
    if extra_task:
        task_text += "\n\n[Extra Task Requirements]\n" + extra_task

    out = {
        "provider": provider,
        "api_key": _provider_api_key(cfg, provider),
        "chatgpt_model": str(cfg.get("chatgpt_model") or ""),
        "local_base_url": str(cfg.get("local_base_url") or ""),
        "local_model": str(cfg.get("local_model") or ""),
        "deepseek_model": str(cfg.get("deepseek_model") or ""),
        "qwen_model": str(cfg.get("qwen_model") or ""),
        "qwen_base_url": str(cfg.get("qwen_base_url") or ""),
        "gemini_model": str(cfg.get("gemini_model") or ""),
        "nvidia_model": str(cfg.get("nvidia_model") or ""),
        "system_prompt_text": sys_text,
        "task_prompt_text": task_text,
    }
    selector_model = str(selector_row.get("model") or "").strip()
    if selector_model:
        out[_provider_model_key(provider)] = selector_model
    if provider == "qwen":
        qwen_base_url = str(selector_row.get("qwen_base_url") or "").strip()
        if qwen_base_url:
            out["qwen_base_url"] = qwen_base_url
    if provider == "local":
        local_base_url = str(selector_row.get("local_base_url") or "").strip()
        if local_base_url:
            out["local_base_url"] = local_base_url
    return out


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        pass

    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.S | re.I)
    if m:
        try:
            v = json.loads(m.group(1))
            return v if isinstance(v, dict) else {}
        except Exception:
            pass

    m = re.search(r"(\{[\s\S]*\})", raw)
    if m:
        try:
            v = json.loads(m.group(1))
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def _device_profile_map(session: Any) -> dict[str, str]:
    calibration = getattr(session, "time_calibration", []) or []
    out: dict[str, str] = {}
    for item in calibration:
        if not isinstance(item, dict):
            continue
        did = str(item.get("device_id") or "").strip()
        if not did:
            continue
        profile = normalize_profile(
            str(item.get("vendor") or "unknown"),
            str(item.get("os_family") or ""),
        )
        out[did] = profile
    if out:
        return out
    for d in getattr(session, "devices", []) or []:
        did = str(getattr(d, "device_id", "") or "").strip()
        if not did:
            continue
        hint = str(getattr(d, "vendor_hint", "") or "").strip().lower()
        out[did] = normalize_profile(hint or "unknown", "")
    return out


def _device_version_map(session: Any) -> dict[str, str]:
    calibration = getattr(session, "time_calibration", []) or []
    out: dict[str, str] = {}
    for item in calibration:
        if not isinstance(item, dict):
            continue
        did = str(item.get("device_id") or "").strip()
        if not did:
            continue
        out[did] = str(item.get("version") or "").strip()
    return out


def _resolve_intent_command(
    *,
    intent: str,
    profile: str,
    version: str = "",
    learning_store: NetdiagLearningStore | None = None,
) -> tuple[str | None, str]:
    if learning_store is not None:
        try:
            cmd = learning_store.resolve_command(intent=intent, profile=profile, version=version)
            if cmd:
                return cmd, "library"
        except Exception:
            pass
    return command_for_intent(intent, profile), "default"


def _infer_intent_from_command(command: str, profile: str) -> str:
    cmd = str(command or "").strip().lower()
    if not cmd:
        return ""
    for intent in allowed_intents_for_profile(profile):
        mapped = str(command_for_intent(intent, profile) or "").strip().lower()
        if mapped and mapped == cmd:
            return intent
    for intent in INTENT_DESCRIPTIONS:
        mapped = str(command_for_intent(intent, profile) or "").strip().lower()
        if mapped and mapped == cmd:
            return intent
    return "manual_command"


def _read_text_safe(path: str, limit: int = 12000) -> str:
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""
    try:
        return Path(raw_path).read_text(encoding="utf-8")[: max(0, int(limit))]
    except Exception:
        return ""


def _read_text_tail_safe(path: str, limit: int = 6000) -> str:
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""
    cap = max(200, min(int(limit), 120000))
    try:
        text = Path(raw_path).read_text(encoding="utf-8")
        return text[-cap:]
    except Exception:
        return ""


def _require_known_issue_store(request: Request) -> NetdiagKnownIssueStore:
    store = getattr(request.app.state, "known_issue_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="known issue store unavailable")
    return store


def _require_case_store(request: Request) -> NetdiagCaseStore:
    store = getattr(request.app.state, "case_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="case store unavailable")
    return store


def _require_duel_store(request: Request) -> NetdiagDuelStore:
    store = getattr(request.app.state, "duel_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="duel store unavailable")
    return store


def _query_issue_hits(
    *,
    issue_store: NetdiagKnownIssueStore | None,
    profile_map: dict[str, str],
    version_map: dict[str, str],
    query_text: str,
    evidence_text: str = "",
    limit_per_device: int = 6,
) -> list[dict[str, Any]]:
    if issue_store is None:
        return []

    merged: dict[str, dict[str, Any]] = {}
    for did, profile in (profile_map or {"*": "unknown"}).items():
        version = str(version_map.get(did, "") or "")
        try:
            hits = issue_store.search(
                profile=profile,
                version=version,
                query_text=query_text,
                evidence_text=evidence_text,
                limit=limit_per_device,
            )
        except Exception:
            hits = []
        for hit in hits:
            iid = str(hit.get("issue_id") or "").strip()
            if not iid:
                continue
            row = dict(hit)
            row["device_id"] = did
            row["device_ids"] = [did]
            if iid not in merged:
                merged[iid] = row
                continue
            cur = merged[iid]
            # Keep best-scored row for primary fields.
            if float(row.get("score") or 0.0) > float(cur.get("score") or 0.0):
                keep = row
            else:
                keep = cur
            device_ids = sorted(
                {
                    *[str(x).strip() for x in cur.get("device_ids", []) if str(x).strip()],
                    *[str(x).strip() for x in row.get("device_ids", []) if str(x).strip()],
                }
            )
            match_terms = sorted(
                {
                    *[str(x).strip() for x in cur.get("matched_terms", []) if str(x).strip()],
                    *[str(x).strip() for x in row.get("matched_terms", []) if str(x).strip()],
                }
            )[:12]
            match_patterns = sorted(
                {
                    *[str(x).strip() for x in cur.get("matched_patterns", []) if str(x).strip()],
                    *[str(x).strip() for x in row.get("matched_patterns", []) if str(x).strip()],
                }
            )[:12]
            match_reasons = sorted(
                {
                    *[str(x).strip() for x in cur.get("match_reasons", []) if str(x).strip()],
                    *[str(x).strip() for x in row.get("match_reasons", []) if str(x).strip()],
                }
            )[:12]
            merged[iid] = {
                **keep,
                "device_ids": device_ids,
                "matched_terms": match_terms,
                "matched_patterns": match_patterns,
                "match_reasons": match_reasons,
                "explain": "; ".join(match_reasons[:4]) if match_reasons else str(keep.get("explain") or ""),
            }
    out = list(merged.values())
    out.sort(key=lambda x: (-float(x.get("score") or 0.0), str(x.get("issue_id") or "")))
    return out[:10]


def _sop_context_block(
    *,
    hypotheses: list[dict[str, Any]],
    issue_hits: list[dict[str, Any]],
    case_hits: list[dict[str, Any]],
    sop_steps: list[dict[str, Any]],
) -> str:
    hyp_lines = [
        f"- {h.get('hypothesis_id')}: {h.get('title')} score={h.get('score')} source={h.get('source')}"
        for h in (hypotheses or [])[:6]
    ]
    issue_lines = [_format_known_issue_hit(x) for x in (issue_hits or [])[:6]]
    case_lines = [_format_case_hit(x) for x in (case_hits or [])[:6]]
    step_lines = [
        f"- device_id={s.get('device_id')} intent={s.get('intent')} reason={s.get('reason')}"
        for s in (sop_steps or [])[:12]
    ]
    return (
        "\n[SOP Hypotheses]\n"
        + ("\n".join(hyp_lines) if hyp_lines else "- none")
        + "\n\n[Known Issue Hints]\n"
        + ("\n".join(issue_lines) if issue_lines else "- none")
        + "\n\n[Case Library Hints]\n"
        + ("\n".join(case_lines) if case_lines else "- none")
        + "\n\n[SOP Recommended Steps]\n"
        + ("\n".join(step_lines) if step_lines else "- none")
    )


def _format_known_issue_hit(hit: dict[str, Any]) -> str:
    iid = str(hit.get("issue_id") or "").strip() or "-"
    title = str(hit.get("title") or "").strip() or "-"
    score = float(hit.get("score") or 0.0)
    devices = ",".join(str(x) for x in (hit.get("device_ids") or [hit.get("device_id") or "-"]) if str(x).strip())
    reasons = [str(x).strip() for x in (hit.get("match_reasons") or []) if str(x).strip()]
    terms = [str(x).strip() for x in (hit.get("matched_terms") or []) if str(x).strip()]
    patterns = [str(x).strip() for x in (hit.get("matched_patterns") or []) if str(x).strip()]
    reason_text = "; ".join(reasons[:3]) if reasons else str(hit.get("explain") or "")
    suffix: list[str] = []
    if terms:
        suffix.append("terms=" + ",".join(terms[:5]))
    if patterns:
        suffix.append("patterns=" + " | ".join(patterns[:3]))
    if reason_text:
        suffix.append(reason_text)
    extra = " ; ".join(suffix)
    return f"- {iid} score={score:.2f} device={devices} title={title}" + (f" ; {extra}" if extra else "")


def _known_issue_hints_block(issue_hits: list[dict[str, Any]]) -> str:
    if not issue_hits:
        return "- none"
    return "\n".join(_format_known_issue_hit(x) for x in issue_hits[:6])


def _to_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;|]+", text)
    return [p.strip() for p in parts if p.strip()]


def _query_case_hits(
    *,
    case_store: NetdiagCaseStore | None,
    profile_map: dict[str, str],
    query_text: str,
    domains: list[str] | None = None,
    evidence_text: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    if case_store is None:
        return []
    profiles = sorted({str(x).strip().lower() for x in profile_map.values() if str(x).strip()})
    doms = sorted({str(x).strip().lower() for x in (domains or []) if str(x).strip()})
    qlow = str(query_text or "").strip().lower()
    allow_lab_cases = any(k in qlow for k in ("lab", "实验", "演练", "对抗", "模拟"))
    try:
        rows = case_store.search(
            query_text=query_text,
            profiles=profiles,
            domains=doms,
            evidence_text=evidence_text,
            limit=limit,
        )
        if allow_lab_cases:
            return rows
        filtered: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip().lower()
            tags = [str(x).strip().lower() for x in (row.get("tags") or []) if str(x).strip()]
            source = str(row.get("source") or "").strip().lower()
            if title.startswith("[lab]"):
                continue
            if "lab_duel" in tags or "lab" in tags:
                continue
            if source in {"lab", "duel", "simulation"}:
                continue
            filtered.append(row)
        return filtered
    except Exception:
        return []


def _format_case_hit(hit: dict[str, Any]) -> str:
    cid = str(hit.get("case_id") or "").strip() or "-"
    title = str(hit.get("title") or "").strip() or "-"
    score = _safe_float(hit.get("score"), 0.0)
    profiles = [str(x).strip() for x in (hit.get("vendor_profiles") or []) if str(x).strip()]
    domains = [str(x).strip() for x in (hit.get("domains") or []) if str(x).strip()]
    reasons = [str(x).strip() for x in (hit.get("match_reasons") or []) if str(x).strip()]
    suffix: list[str] = []
    if profiles:
        suffix.append("profiles=" + ",".join(profiles[:4]))
    if domains:
        suffix.append("domains=" + ",".join(domains[:4]))
    if reasons:
        suffix.append("; ".join(reasons[:3]))
    extra = " ; ".join(suffix)
    return f"- {cid} score={score:.2f} title={title}" + (f" ; {extra}" if extra else "")


def _case_hints_block(case_hits: list[dict[str, Any]]) -> str:
    if not case_hits:
        return "- none"
    return "\n".join(_format_case_hit(x) for x in case_hits[:6])


def _case_hits_to_issue_like(case_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hit in (case_hits or [])[:8]:
        cid = str(hit.get("case_id") or "").strip()
        if not cid:
            continue
        domains = [str(x).strip().lower() for x in (hit.get("domains") or []) if str(x).strip()]
        out.append(
            {
                "issue_id": f"CASE-{cid}",
                "title": str(hit.get("title") or f"Case {cid}"),
                "domain": domains[0] if domains else "known_issue",
                # Keep case priors low, so direct command evidence remains primary.
                "score": round(min(1.2, _safe_float(hit.get("score"), 0.0) * 0.20), 4),
                "diag_intents": [],
            }
        )
    return out


def _case_hits_to_signals(case_hits: list[dict[str, Any]], max_signals: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hit in (case_hits or [])[:8]:
        cid = str(hit.get("case_id") or "").strip()
        if not cid:
            continue
        title = str(hit.get("title") or "").strip()
        doms = [str(x).strip().lower() for x in (hit.get("domains") or []) if str(x).strip()]
        if not doms:
            doms = ["global"]
        base = min(0.05, 0.015 + (_safe_float(hit.get("score"), 0.0) * 0.004))
        each = max(0.01, min(0.04, base / max(1, len(doms))))
        for dom in doms:
            out.append(
                {
                    "device_id": "*",
                    "vendor": "case_library",
                    "command": "case.search",
                    "domain": dom,
                    "polarity": "positive",
                    "signal": "case_prior_match",
                    "weight": round(each, 4),
                    "detail": f"{cid} {title}".strip()[:240],
                }
            )
            if len(out) >= max(1, min(int(max_signals), 48)):
                return out
    return out


def _collect_direct_evidence_hints(signals: list[dict[str, Any]]) -> list[str]:
    rows = [x for x in (signals or []) if isinstance(x, dict)]
    if not rows:
        return []
    strong_names = {
        "interface_admin_down_present",
        "interface_admin_shutdown_event",
        "interface_admin_shutdown_detail",
        "huawei_interface_shutdown_event",
        "huawei_shutdown_recovery_flap_pattern",
        "huawei_interface_admin_down",
    }
    hints: list[str] = []
    for row in rows:
        name = str(row.get("signal") or "").strip()
        if name not in strong_names:
            continue
        polarity = str(row.get("polarity") or "positive").strip().lower()
        if polarity != "positive":
            continue
        detail = str(row.get("detail") or "").strip()
        hints.append(f"{name}: {detail}" if detail else name)
    # Keep concise and stable.
    uniq = sorted(set(hints))
    return uniq[:8]


def _direct_evidence_should_conclude(
    *,
    parsed_evidence: dict[str, Any],
    direct_evidence_hints: list[str],
    focus_review: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    hints = [str(x or "").strip() for x in (direct_evidence_hints or []) if str(x or "").strip()]
    if not hints:
        return False, "no_direct_hints"
    if focus_review and list(focus_review.get("uncovered") or []):
        return False, "focus_goals_uncovered"

    health = dict(parsed_evidence.get("command_health") or {})
    total = max(1, _safe_int(health.get("total"), 0))
    valid = _safe_int(health.get("valid_output"), 0)
    error_output = _safe_int(health.get("error_output"), 0)
    valid_rate = float(valid) / float(total)
    if valid_rate < 0.60:
        return False, f"valid_rate={valid_rate:.2f}<0.60"
    if error_output > max(1, total // 2):
        return False, f"error_output={error_output} too_high"

    strong_names = {
        "interface_admin_shutdown_event",
        "huawei_interface_shutdown_event",
        "interface_admin_down_present",
        "huawei_interface_admin_down",
    }
    hit_names: set[str] = set()
    for hint in hints:
        name = str(hint.split(":", 1)[0] or "").strip().lower()
        if name in strong_names:
            hit_names.add(name)
    if not hit_names:
        return False, "no_strong_shutdown_signal"
    return True, "direct_evidence_converged:" + ",".join(sorted(hit_names))


def _inject_direct_evidence_hypothesis(
    *,
    hypotheses: list[dict[str, Any]],
    parsed_evidence: dict[str, Any],
    direct_evidence_hints: list[str],
) -> list[dict[str, Any]]:
    base = [dict(x) for x in (hypotheses or []) if isinstance(x, dict)]
    ok, reason = _direct_evidence_should_conclude(
        parsed_evidence=parsed_evidence,
        direct_evidence_hints=direct_evidence_hints,
        focus_review=None,
    )
    if not ok:
        return base
    top_boost = 0.93
    matched = False
    for row in base:
        dom = str(row.get("domain") or "").strip().lower()
        title = str(row.get("title") or "").strip().lower()
        if dom == "link" or "interface" in title or "port" in title:
            row["score"] = max(_safe_float(row.get("score"), 0.0), top_boost)
            row["confidence"] = max(_safe_float(row.get("confidence", row.get("score")), 0.0), top_boost)
            row["status"] = "likely"
            evid_for = [str(x) for x in (row.get("evidence_for") or []) if str(x).strip()]
            evid_for.append(f"direct_evidence_boost {reason}")
            row["evidence_for"] = evid_for[-20:]
            matched = True
            break
    if not matched:
        base.append(
            {
                "hypothesis_id": "hyp-direct-link-evidence",
                "title": "Interface administratively down / shutdown direct evidence",
                "domain": "link",
                "source": "direct_evidence",
                "score": top_boost,
                "confidence": top_boost,
                "status": "likely",
                "evidence_for": [f"direct_evidence_boost {reason}"],
                "evidence_against": [],
                "next_intents": ["interface_summary", "system_log_recent", "clock_check"],
            }
        )
    return rank_hypotheses(base)


def _analysis_fast_path_enabled(payload: dict[str, Any] | None) -> bool:
    raw = (payload or {}).get("analysis_fast_path")
    if isinstance(raw, dict):
        return _to_bool(raw.get("enabled", True), True)
    if raw is None:
        return True
    return _to_bool(raw, True)


def _should_use_analysis_fast_path(
    *,
    parsed_evidence: dict[str, Any],
    direct_evidence_hints: list[str],
) -> tuple[bool, str]:
    health = dict(parsed_evidence.get("command_health") or {})
    total = max(1, _safe_int(health.get("total"), 0))
    valid = _safe_int(health.get("valid_output"), 0)
    error_output = _safe_int(health.get("error_output"), 0)
    valid_rate = float(valid) / float(total)
    if valid_rate < 0.5:
        return False, f"valid_rate={valid_rate:.2f}<0.50"
    if error_output > max(1, total // 3):
        return False, f"error_output={error_output} too high"
    domain_delta = dict(parsed_evidence.get("domain_delta") or {})
    max_abs_delta = 0.0
    for value in domain_delta.values():
        max_abs_delta = max(max_abs_delta, abs(_safe_float(value, 0.0)))
    if direct_evidence_hints:
        return True, f"direct_hints={len(direct_evidence_hints)}"
    if max_abs_delta >= 0.72:
        return True, f"max_abs_domain_delta={max_abs_delta:.2f}"
    return False, f"no direct hints and weak domain delta ({max_abs_delta:.2f})"


def _first_non_empty_line(text: str, limit: int = 160) -> str:
    for line in str(text or "").splitlines():
        line = str(line or "").strip()
        if line:
            return line[: max(20, int(limit))]
    return "-"


def _extract_interface_like(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    m = re.search(
        r"\b("
        r"Ethernet\d+(?:/\d+){1,3}"
        r"|GigabitEthernet\d+(?:/\d+){1,3}"
        r"|XGigabitEthernet\d+(?:/\d+){1,3}"
        r"|TenGigabitEthernet\d+(?:/\d+){1,3}"
        r"|Eth\d+(?:/\d+){1,3}"
        r"|GE\d+(?:/\d+){1,3}"
        r"|Gi\d+(?:/\d+){1,3}"
        r"|Te\d+(?:/\d+){1,3}"
        r"|Port-?channel\d+"
        r"|ae\d+"
        r")\b",
        raw,
        flags=re.I,
    )
    return str(m.group(1) or "").strip() if m else ""


def _fastpath_user_reason(reason: str) -> str:
    raw = str(reason or "").strip().lower()
    if raw.startswith("direct_hints="):
        return "direct_evidence_hit"
    if raw.startswith("max_abs_domain_delta="):
        return "domain_signal_converged"
    if not raw:
        return "-"
    return raw[:64]


def _human_hypothesis_line(
    *,
    top_domain: str,
    direct_evidence_hints: list[str],
    exec_records: list[dict[str, Any]],
    confidence: float,
    valid_rate: float,
) -> str:
    top_domain = str(top_domain or "").strip().lower() or "global"
    pool: list[str] = [str(x or "") for x in (direct_evidence_hints or [])]
    for row in (exec_records or [])[:8]:
        if not isinstance(row, dict):
            continue
        pool.append(str(row.get("command") or ""))
        pool.append(str(row.get("output_text") or ""))
    combined = "\n".join(pool)
    low = combined.lower()
    interface = _extract_interface_like(combined)
    iface_suffix = f"（{interface}）" if interface else ""

    if any(x in low for x in ("shutdown", "administratively down", "admin down", "管理关闭", "huawei_interface_admin_down")):
        hypothesis = f"接口被管理性关闭{iface_suffix}，导致端口 down。"
    elif any(x in low for x in ("crc", "fcs", "input error", "output error", "discard", "drop")):
        hypothesis = f"接口可能存在物理层或链路质量异常{iface_suffix}（误码/丢弃信号）。"
    elif top_domain == "clock":
        hypothesis = "设备时间存在偏移风险，可能影响日志与故障时间窗对齐。"
    elif top_domain == "routing":
        hypothesis = "路由协议或邻居会话存在不稳定风险，需结合路由会话状态复核。"
    elif top_domain == "resource":
        hypothesis = "设备资源（CPU/内存）存在异常风险，可能引发转发/协议抖动。"
    elif top_domain == "security":
        hypothesis = "安全策略/会话处理可能异常，需复核策略命中与会话状态。"
    else:
        hypothesis = "当前证据指向链路/接口域异常，建议继续补采关键只读命令。"

    return (
        f"- 假设: {hypothesis}\n"
        f"- 置信度={confidence:.3f}（证据有效回显率={valid_rate:.2%}）"
    )


def _build_fastpath_analysis_text(
    *,
    session: Any,
    round_no: int,
    parsed_evidence: dict[str, Any],
    exec_records: list[dict[str, Any]],
    direct_evidence_hints: list[str],
    reason: str,
) -> str:
    health = dict(parsed_evidence.get("command_health") or {})
    total = max(1, _safe_int(health.get("total"), 0))
    valid = _safe_int(health.get("valid_output"), 0)
    valid_rate = float(valid) / float(total)
    domain_delta = {
        str(k): _safe_float(v, 0.0)
        for k, v in dict(parsed_evidence.get("domain_delta") or {}).items()
        if str(k).strip()
    }
    top_domain = "global"
    top_delta = 0.0
    for dom, delta in domain_delta.items():
        if abs(delta) > abs(top_delta):
            top_domain, top_delta = dom, delta
    confidence = min(0.99, max(0.55, 0.55 + min(0.38, abs(top_delta) * 0.45)))

    judgement = "证据仍需补充，本轮仅形成初步结论。"
    if direct_evidence_hints:
        judgement = "已发现直接证据，可形成高置信度阶段结论。"
    elif abs(top_delta) >= 0.72:
        judgement = "结构化信号已明显收敛，可先按高置信度方向推进。"

    evidence_lines: list[str] = []
    for row in (exec_records or [])[:8]:
        did = str(row.get("device_id") or "-")
        cmd = str(row.get("command") or "-")
        status = str(row.get("status") or "-")
        snippet = _first_non_empty_line(str(row.get("output_text") or ""))
        evidence_lines.append(
            f"- device={did} time={session.fault_window.start_at}~{session.fault_window.end_at} "
            f"command={cmd} status={status} snippet={snippet}"
        )
    for hint in direct_evidence_hints[:6]:
        evidence_lines.append(f"- direct_hint={hint}")
    if not evidence_lines:
        evidence_lines.append("- 证据不足：未读到可解析回显。")

    hypothesis_line = _human_hypothesis_line(
        top_domain=top_domain,
        direct_evidence_hints=direct_evidence_hints,
        exec_records=exec_records,
        confidence=confidence,
        valid_rate=valid_rate,
    )

    action_lines = [
        "- 先核对本轮结论涉及接口/协议的当前状态，再执行下一轮最小补采。",
        "- 严格只用 show/display/dis 命令，避免重复采集同一证据。",
        "- 若用户补充新现象（设备/时间窗/业务影响），优先更新诊断方向后再继续。",
    ]
    next_cmds = [
        "- *: show clock / display clock",
        "- *: show logging / display logbuffer",
        "- *: show interface brief / display interface brief",
    ]
    if direct_evidence_hints:
        next_cmds.insert(0, "- *: show interface <port> / display interface <port>（复核直接证据端口）")
    time_lines = [
        f"- 故障时间窗: {session.fault_window.start_at} ~ {session.fault_window.end_at} ({session.fault_window.timezone})",
        f"- Baseline 时间偏移已用于校准；本轮 fast-path 依据={_fastpath_user_reason(reason)}",
    ]

    return (
        "### 1) 当前判定\n"
        + judgement
        + "\n\n### 2) 证据链（每条包含 device/time/command/snippet）\n"
        + "\n".join(evidence_lines)
        + "\n\n### 3) 根因假设与置信度\n"
        + hypothesis_line
        + "\n\n### 4) 建议后续操作\n"
        + "\n".join(action_lines)
        + "\n\n### 5) 下一轮建议命令（仅 show/display/dis）\n"
        + "\n".join(next_cmds)
        + "\n\n### 6) 时间维度校验（故障时间窗 vs 设备时间/时区）\n"
        + "\n".join(time_lines)
        + f"\n\n[FastPath]\n- enabled=true reason={_fastpath_user_reason(reason)} round={round_no}"
    )


def _deterministic_analysis_fallback(
    *,
    error_message: str,
    parsed_evidence: dict[str, Any],
    issue_hits: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
) -> str:
    ranked = rank_hypotheses(hypotheses or [])
    top = ranked[0] if ranked else {}
    lines = [
        f"AI analyze failed: request failed: {error_message}",
        "",
        "[Deterministic Analyzer Fallback]",
    ]
    if top:
        lines.append(
            "TopHypothesis: "
            f"{top.get('title')} ({top.get('domain')}) "
            f"score={top.get('score')} confidence={top.get('confidence')}"
        )
        ev_for = [str(x) for x in (top.get("evidence_for") or []) if str(x).strip()]
        ev_against = [str(x) for x in (top.get("evidence_against") or []) if str(x).strip()]
        if ev_for:
            lines.append("EvidenceFor: " + " | ".join(ev_for[:6]))
        if ev_against:
            lines.append("EvidenceAgainst: " + " | ".join(ev_against[:6]))
        next_intents = [str(x).strip() for x in (top.get("next_intents") or []) if str(x).strip()]
        if next_intents:
            lines.append("SuggestedNextIntents: " + ", ".join(next_intents[:6]))

    health = dict(parsed_evidence.get("command_health") or {})
    if health:
        total = int(health.get("total") or 0)
        valid = int(health.get("valid_output") or 0)
        err_out = int(health.get("error_output") or 0)
        lines.append(f"CommandHealth: valid={valid}/{total}, error_output={err_out}")
    summary_lines = [str(x) for x in (parsed_evidence.get("summary_lines") or []) if str(x).strip()]
    if summary_lines:
        lines.append("EvidenceSummary: " + " || ".join(summary_lines[:5]))

    if issue_hits:
        lines.append("[KnownIssueHints]")
        lines.append(_known_issue_hints_block(issue_hits))
    return "\n".join(lines)


def _analysis_quality_reasons(text: str) -> list[str]:
    raw = str(text or "").strip()
    low = raw.lower()
    reasons: list[str] = []
    if not raw:
        return ["empty_analysis"]
    if len(raw) < 160:
        reasons.append("analysis_too_short")
    if _looks_incomplete_llm_text(raw):
        reasons.append("analysis_incomplete_or_truncated")

    required_tokens = {
        "missing_judgement_section": ("当前判定", "current diagnosis", "结论"),
        "missing_evidence_chain_section": ("证据链", "evidence chain"),
        "missing_confidence_section": ("置信度", "confidence"),
        "missing_next_action_section": ("建议后续操作", "处置建议", "next action", "mitigation"),
        "missing_next_commands_section": ("下一轮建议命令", "下一步诊断动作", "next diagnostic command"),
        "missing_time_validation_section": ("时间维度", "故障时间窗", "time calibration", "timezone"),
    }
    for reason, terms in required_tokens.items():
        if not any(t.lower() in low for t in terms):
            reasons.append(reason)

    has_device = bool(re.search(r"(device[_=\- ]|dev-\d+|设备)", raw, flags=re.I))
    has_time = bool(re.search(r"(\d{4}-\d{2}-\d{2}|time|时间|timezone|offset)", raw, flags=re.I))
    has_command = bool(re.search(r"\b(show|display|dis)\b|命令", raw, flags=re.I))
    if not (has_device and has_time and has_command):
        reasons.append("missing_traceable_evidence_fields")

    strong_claim = any(x in low for x in ("未发现", "无关联", "状态稳定", "no related", "no issue", "stable"))
    if strong_claim and ("证据不足" not in raw and "insufficient evidence" not in low):
        if "证据链" not in raw and "evidence chain" not in low:
            reasons.append("strong_claim_without_evidence_disclaimer")
    return reasons


def _suggest_next_readonly_commands(
    *,
    top_hypothesis: dict[str, Any],
    profile_map: dict[str, str],
    version_map: dict[str, str],
    learning_store: NetdiagLearningStore | None = None,
    limit: int = 8,
) -> list[str]:
    intents = [str(x).strip() for x in (top_hypothesis.get("next_intents") or []) if str(x).strip()]
    if not intents:
        for profile in sorted({str(v or "").strip() for v in profile_map.values() if str(v or "").strip()}):
            intents.extend(allowed_intents_for_profile(profile)[:2])
    out: list[str] = []
    seen: set[str] = set()
    for did, profile in (profile_map or {"*": "unknown"}).items():
        for intent in intents:
            cmd, _source = _resolve_intent_command(
                intent=intent,
                profile=profile or "unknown",
                version=str(version_map.get(did, "") or ""),
                learning_store=learning_store,
            )
            cmd = str(cmd or "").strip()
            if not cmd or not is_read_only_command(cmd):
                continue
            key = f"{did}|{cmd.lower()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(f"- {did}: {cmd}  # {intent}")
            if len(out) >= max(1, min(int(limit), 20)):
                return out
    return out


def _build_structured_analysis_repair(
    *,
    session: Any,
    round_no: int,
    quality_reasons: list[str],
    original_text: str,
    exec_records: list[dict[str, Any]],
    parsed_evidence: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    stop_decision: dict[str, Any],
    issue_hits: list[dict[str, Any]],
    case_hits: list[dict[str, Any]],
    focus_review: dict[str, Any],
    profile_map: dict[str, str],
    version_map: dict[str, str],
    learning_store: NetdiagLearningStore | None = None,
) -> str:
    ranked = rank_hypotheses(hypotheses or [])
    top = ranked[0] if ranked else {}
    summary_lines = [str(x).strip() for x in (parsed_evidence.get("summary_lines") or []) if str(x).strip()]
    health = dict(parsed_evidence.get("command_health") or {})
    total = max(1, int(health.get("total") or 0))
    valid = int(health.get("valid_output") or 0)
    valid_rate = float(valid) / float(total)
    low_evidence = (len(summary_lines) < 2) or (valid_rate < 0.35)

    judgement = "已有初步证据，但未形成闭环，建议继续下一轮验证。"
    if low_evidence:
        judgement = "证据不足，需补充采集，当前不建议收敛到最终根因。"
    elif bool(stop_decision.get("recommend_conclude")):
        judgement = "证据链较完整，可收敛到当前头号假设并进入验证/处置。"

    evidence_lines: list[str] = []
    for row in (exec_records or [])[:10]:
        did = str(row.get("device_id") or "-")
        cmd = str(row.get("command") or "-")
        status = str(row.get("status") or "-")
        output_text = str(row.get("output_text") or "")
        sample = ""
        for ln in output_text.splitlines():
            t = str(ln).strip()
            if t:
                sample = t
                break
        sample = sample[:120] if sample else "-"
        evidence_lines.append(f"- device={did} time={session.fault_window.start_at}~{session.fault_window.end_at} command={cmd} status={status} snippet={sample}")
    if not evidence_lines:
        evidence_lines.append("- 证据不足：本轮未获取到可解析的命令回显。")
    if summary_lines:
        evidence_lines.extend([f"- parser={x}" for x in summary_lines[:6]])

    conf = _safe_float(top.get("confidence"), _safe_float(top.get("score"), 0.0))
    hypothesis_title = str(top.get("title") or "Undetermined")
    hypothesis_domain = str(top.get("domain") or "global")
    confidence_line = f"- 假设: {hypothesis_title} ({hypothesis_domain}), 置信度={conf:.3f}"
    if low_evidence:
        confidence_line += "，但证据强度不足，需继续采集。"

    action_lines = [
        "- 仅执行只读 show/display/dis 命令补充证据。",
        "- 对照故障时间窗复核：设备时钟偏移、日志时间戳、监控时间戳是否一致。",
        "- 优先处理高风险信号，再回到收敛判断。",
    ]
    if bool(stop_decision.get("recommend_conclude")) and not low_evidence:
        action_lines.insert(0, "- 可进入收敛处置：先止血，再执行验证命令确认恢复。")

    next_cmds = _suggest_next_readonly_commands(
        top_hypothesis=top,
        profile_map=profile_map,
        version_map=version_map,
        learning_store=learning_store,
        limit=8,
    )
    if not next_cmds:
        next_cmds = ["- *: show clock", "- *: show logging"]

    cal_lines: list[str] = []
    for item in (getattr(session, "time_calibration", []) or [])[:8]:
        if not isinstance(item, dict):
            continue
        cal_lines.append(
            f"- device={item.get('device_id')} timezone={session.fault_window.timezone} "
            f"offset_seconds={item.get('offset_seconds')} log_range=[{item.get('log_time_min')} ~ {item.get('log_time_max')}]"
        )
    if not cal_lines:
        cal_lines.append("- 基线时间校准记录不足，建议先执行 show clock / display clock 获取设备时间。")

    focus_lines = []
    if isinstance(focus_review, dict):
        cov = [str(x) for x in (focus_review.get("covered") or []) if str(x).strip()]
        uncov = [str(x) for x in (focus_review.get("uncovered") or []) if str(x).strip()]
        focus_lines.append(f"- 覆盖: {', '.join(cov) if cov else '-'}")
        focus_lines.append(f"- 未覆盖: {', '.join(uncov) if uncov else '-'}")
    if not focus_lines:
        focus_lines.append("- FocusLock 信息不足，下一轮请补充诊断方向。")

    known_brief = _known_issue_hints_block(issue_hits) if issue_hits else "- none"
    case_brief = _case_hints_block(case_hits) if case_hits else "- none"
    reasons_txt = ", ".join(quality_reasons) if quality_reasons else "-"
    original_excerpt = str(original_text or "").strip()[:1200]

    return (
        "### 1) 当前判定\n"
        f"{judgement}\n\n"
        "### 2) 证据链（设备/时间/命令/片段）\n"
        + "\n".join(evidence_lines)
        + "\n\n### 3) 根因假设与置信度\n"
        + confidence_line
        + "\n\n### 4) 建议后续操作\n"
        + "\n".join(action_lines)
        + "\n\n### 5) 下一轮建议命令（仅 show/display/dis）\n"
        + "\n".join(next_cmds)
        + "\n\n### 6) 时间维度校验\n"
        + f"- 故障时间窗: {session.fault_window.start_at} ~ {session.fault_window.end_at} ({session.fault_window.timezone})\n"
        + "\n".join(cal_lines)
        + "\n\n### 7) FocusLock 覆盖\n"
        + "\n".join(focus_lines)
        + "\n\n[KnownIssueHints]\n"
        + known_brief
        + "\n\n[CaseLibraryHints]\n"
        + case_brief
        + "\n\n[QualityGate]\n"
        + f"- repaired=true reasons={reasons_txt}\n"
        + ("- original_excerpt=\n" + original_excerpt if original_excerpt else "- original_excerpt=-")
        + f"\n\n[Round]\n- round_no={round_no}"
    )


def _intent_prompt_block(profile_map: dict[str, str]) -> str:
    lines = []
    for did, profile in profile_map.items():
        intents = ", ".join(allowed_intents_for_profile(profile))
        lines.append(f"- device_id={did}, profile={profile}, intents=[{intents}]")
    intent_desc = "\n".join(f"- {k}: {v}" for k, v in INTENT_DESCRIPTIONS.items())
    profile_text = "\n".join(lines) if lines else "- device_id=*, profile=unknown"
    return (
        "\n[DeviceProfiles]\n"
        + profile_text
        + "\n\n[IntentCatalog]\n"
        + intent_desc
    )


def _fallback_commands(
    profile_map: dict[str, str],
    max_commands: int,
    learning_store: NetdiagLearningStore | None = None,
    version_map: dict[str, str] | None = None,
) -> list[PlannedCommand]:
    out: list[PlannedCommand] = []
    seen: set[tuple[str, str]] = set()
    version_map = version_map or {}
    if not profile_map:
        profile_map = {"*": "unknown"}
    per_device_cap = max(1, min(max_commands, 12))
    for did, profile in profile_map.items():
        for item in default_plan_for_profile(profile, max_commands=per_device_cap):
            intent = str(item.get("intent") or "").strip()
            resolved, _source = _resolve_intent_command(
                intent=intent,
                profile=profile,
                version=str(version_map.get(did, "") or ""),
                learning_store=learning_store,
            )
            cmd = str(resolved or item.get("command") or "").strip()
            if not cmd or not is_read_only_command(cmd):
                continue
            key = (did if did else "*", cmd.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                PlannedCommand(
                    command_id=uuid.uuid4().hex[:12],
                    device_id=did if did else "*",
                    intent=intent,
                    command=cmd,
                    reason=str(item.get("reason") or ""),
                    expected_signal=str(item.get("expected_signal") or ""),
                    risk_level="read_only",
                    requires_approval=True,
                    approved=False,
                )
            )
            if len(out) >= max(1, min(max_commands, 12)):
                return out
    return out


def _dedupe_planned_commands(commands: list[PlannedCommand], max_commands: int) -> list[PlannedCommand]:
    out: list[PlannedCommand] = []
    seen: set[tuple[str, str]] = set()
    for c in commands:
        key = (str(c.device_id or "*"), str(c.command or "").strip().lower())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= max(1, min(max_commands, 12)):
            break
    return out


def _normalize_cmd_key(device_id: str | None, command: str | None) -> tuple[str, str]:
    did = str(device_id or "*").strip() or "*"
    cmd = str(command or "").strip().lower()
    return did, cmd


def _is_execution_reusable(ex: CommandExecution | dict[str, Any] | None) -> bool:
    if ex is None:
        return False
    if isinstance(ex, CommandExecution):
        status = str(ex.status or "").strip().lower()
        has_output = bool(str(ex.output_file or "").strip())
    else:
        status = str(ex.get("status") or "").strip().lower()
        has_output = bool(str(ex.get("output_file") or "").strip())
    if status in {"success", "error_output"}:
        return True
    return has_output


def _history_collected_command_keys(session: Any) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    rounds = getattr(session, "rounds", []) or []
    for rnd in rounds:
        executions = getattr(rnd, "executions", []) or []
        for row in executions:
            if not _is_execution_reusable(row):
                continue
            did = ""
            cmd = ""
            if isinstance(row, CommandExecution):
                did, cmd = _normalize_cmd_key(row.device_id, row.command)
            elif isinstance(row, dict):
                did, cmd = _normalize_cmd_key(row.get("device_id"), row.get("command"))
            if cmd:
                keys.add((did, cmd))
    return keys


def _history_intent_keys(session: Any) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    rounds = getattr(session, "rounds", []) or []
    for rnd in rounds:
        cmds = getattr(rnd, "commands", []) or []
        for row in cmds:
            did = ""
            intent = ""
            if isinstance(row, PlannedCommand):
                did = str(row.device_id or "*").strip() or "*"
                intent = str(row.intent or "").strip().lower()
            elif isinstance(row, dict):
                did = str(row.get("device_id") or "*").strip() or "*"
                intent = str(row.get("intent") or "").strip().lower()
            if intent:
                keys.add((did, intent))
    return keys


def _is_historic_command_repeat(device_id: str, command: str, history_keys: set[tuple[str, str]]) -> bool:
    did, cmd = _normalize_cmd_key(device_id, command)
    if not cmd:
        return False
    if (did, cmd) in history_keys:
        return True
    if did != "*" and ("*", cmd) in history_keys:
        return True
    if did == "*":
        return any(c == cmd for _d, c in history_keys)
    return False


def _history_execution_cache(
    session: Any,
    *,
    include_current_round: bool = True,
    max_round_no: int | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    rounds = list(getattr(session, "rounds", []) or [])
    for rnd in rounds:
        rno = int(getattr(rnd, "round_no", 0) or 0)
        if max_round_no is not None and rno > int(max_round_no):
            continue
        if not include_current_round and max_round_no is not None and rno == int(max_round_no):
            continue
        executions = getattr(rnd, "executions", []) or []
        for row in executions:
            if isinstance(row, CommandExecution):
                key = _normalize_cmd_key(row.device_id, row.command)
                status = str(row.status or "").strip().lower()
                output_file = str(row.output_file or "").strip()
                error = str(row.error or "").strip()
                duration = float(row.duration_sec or 0.0)
                command_id = str(row.command_id or "").strip()
            elif isinstance(row, dict):
                key = _normalize_cmd_key(row.get("device_id"), row.get("command"))
                status = str(row.get("status") or "").strip().lower()
                output_file = str(row.get("output_file") or "").strip()
                error = str(row.get("error") or "").strip()
                duration = float(row.get("duration_sec") or 0.0)
                command_id = str(row.get("command_id") or "").strip()
            else:
                continue
            if not key[1]:
                continue
            if not _is_execution_reusable(row):
                continue
            prev = out.get(key)
            if prev and int(prev.get("round_no") or 0) > rno:
                continue
            out[key] = {
                "round_no": rno,
                "status": status or "success",
                "output_file": output_file or None,
                "error": error or None,
                "duration_sec": duration,
                "command_id": command_id or None,
            }
    return out


def _recent_rounds_context(session: Any, limit_rounds: int = 3) -> str:
    rounds = list(getattr(session, "rounds", []) or [])
    if not rounds:
        return ""
    picked = rounds[-max(1, int(limit_rounds)) :]
    lines: list[str] = []
    for rnd in picked:
        rno = int(getattr(rnd, "round_no", 0) or 0)
        rstatus = str(getattr(rnd, "status", "") or "").strip() or "-"
        stop = getattr(rnd, "stop_decision", {}) or {}
        hyps = getattr(rnd, "hypotheses", []) or []
        top = hyps[0] if hyps and isinstance(hyps[0], dict) else {}
        cmds = getattr(rnd, "commands", []) or []
        cmd_text: list[str] = []
        for c in cmds[:5]:
            if isinstance(c, PlannedCommand):
                cmd_text.append(f"[{c.device_id}] {c.command}")
            elif isinstance(c, dict):
                cmd_text.append(f"[{c.get('device_id') or '*'}] {c.get('command') or ''}")
        execs = getattr(rnd, "executions", []) or []
        ex_total = len(execs)
        ex_reused = 0
        ex_ok = 0
        ex_fail = 0
        ex_text: list[str] = []
        for ex in execs[:6]:
            status = ""
            reused = False
            if isinstance(ex, CommandExecution):
                status = str(ex.status or "").strip().lower()
                reused = bool(ex.reused)
                ex_cmd = str(ex.command or "").strip()
                ex_did = str(ex.device_id or "*").strip() or "*"
            elif isinstance(ex, dict):
                status = str(ex.get("status") or "").strip().lower()
                reused = bool(ex.get("reused"))
                ex_cmd = str(ex.get("command") or "").strip()
                ex_did = str(ex.get("device_id") or "*").strip() or "*"
            else:
                continue
            if status in {"success", "error_output"}:
                ex_ok += 1
            elif status:
                ex_fail += 1
            if reused:
                ex_reused += 1
            if ex_cmd:
                ex_text.append(f"[{ex_did}] {ex_cmd} ({status or '-'}){' [reused]' if reused else ''}")
        lines.append(
            f"- round={rno} status={rstatus} top={top.get('title')}({top.get('domain')}) score={top.get('score')} "
            f"conclude={stop.get('recommend_conclude')} reason={stop.get('reason')}"
        )
        if cmd_text:
            lines.append("  commands: " + " | ".join(cmd_text))
        if ex_total:
            lines.append(f"  executions: total={ex_total} ok={ex_ok} failed={ex_fail} reused={ex_reused}")
            if ex_text:
                lines.append("  samples: " + " | ".join(ex_text))
    return "\n".join(lines)


def _need_baseline_recheck(session: Any) -> bool:
    text = "\n".join(
        [
            str(getattr(session, "question", "") or ""),
            "\n".join(str(x or "") for x in (getattr(session, "focus_goals", []) or [])),
        ]
    ).lower()
    if any(
        kw in text
        for kw in (
            "clock",
            "timezone",
            "ntp",
            "time sync",
            "time skew",
            "time drift",
            "clock drift",
            "clock skew",
            "version",
            "cpu",
            "memory",
            "resource",
            "时钟",
            "时区",
            "版本",
            "cpu",
            "内存",
            "资源",
        )
    ):
        return True
    return False


def _enforce_progressive_plan(
    *,
    commands: list[PlannedCommand],
    session: Any,
    profile_map: dict[str, str],
    version_map: dict[str, str] | None,
    max_commands: int,
    learning_store: NetdiagLearningStore | None = None,
) -> list[PlannedCommand]:
    base = _dedupe_planned_commands(commands, max_commands=max_commands)
    rounds = list(getattr(session, "rounds", []) or [])
    baseline_ok = any(
        isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "success"
        for item in (getattr(session, "time_calibration", []) or [])
    )
    blocked_baseline_intents = {"clock_check", "version_check", "cpu_health"}
    avoid_baseline_intents = baseline_ok and not _need_baseline_recheck(session)

    history_cmds = _history_collected_command_keys(session)
    history_intents = _history_intent_keys(session)

    fresh: list[PlannedCommand] = []
    repeated: list[PlannedCommand] = []
    baseline_like: list[PlannedCommand] = []
    for c in base:
        intent = str(c.intent or "").strip().lower()
        if avoid_baseline_intents and intent in blocked_baseline_intents:
            baseline_like.append(c)
            continue
        if _is_historic_command_repeat(str(c.device_id or "*"), str(c.command or ""), history_cmds):
            repeated.append(c)
        else:
            fresh.append(c)

    out: list[PlannedCommand] = list(fresh)
    if not out and repeated:
        # Keep one explicit recheck when no new path is available.
        first = repeated[0]
        reason = str(first.reason or "").strip()
        first.reason = f"{reason} [recheck-no-new-path]".strip()
        out.append(first)

    current_keys = {(str(x.device_id or "*"), str(x.command or "").strip().lower()) for x in out}
    version_map = version_map or {}
    for did, profile in (profile_map or {"*": "unknown"}).items():
        if len(out) >= max(1, min(max_commands, 12)):
            break
        candidates: list[tuple[int, PlannedCommand]] = []
        for item in default_plan_for_profile(profile, max_commands=12):
            intent = str(item.get("intent") or "").strip()
            if avoid_baseline_intents and intent.lower() in blocked_baseline_intents:
                continue
            command, _source = _resolve_intent_command(
                intent=intent,
                profile=profile,
                version=str(version_map.get(did, "") or ""),
                learning_store=learning_store,
            )
            cmd = str(command or item.get("command") or "").strip()
            if not cmd or not is_read_only_command(cmd):
                continue
            key = (did if did else "*", cmd.lower())
            if key in current_keys:
                continue
            if _is_historic_command_repeat(key[0], key[1], history_cmds):
                continue
            seen_intent = (key[0], intent.lower()) in history_intents or ("*", intent.lower()) in history_intents
            reason = str(item.get("reason") or "progressive diversify checks")
            if seen_intent:
                reason = f"{reason} [alt-command-same-intent]"
                prio = 1
            else:
                reason = f"{reason} [new-intent]"
                prio = 0
            candidates.append(
                (
                    prio,
                    PlannedCommand(
                        command_id=uuid.uuid4().hex[:12],
                        device_id=key[0],
                        intent=intent,
                        command=cmd,
                        reason=reason,
                        expected_signal=str(item.get("expected_signal") or INTENT_DESCRIPTIONS.get(intent, "")),
                        risk_level="read_only",
                        requires_approval=True,
                        approved=False,
                    ),
                )
            )
        candidates.sort(key=lambda x: x[0])
        for _prio, row in candidates:
            if len(out) >= max(1, min(max_commands, 12)):
                break
            key = (str(row.device_id or "*"), str(row.command or "").strip().lower())
            if key in current_keys:
                continue
            current_keys.add(key)
            out.append(row)

    if not out:
        candidates = [x for x in base if not (avoid_baseline_intents and str(x.intent or "").strip().lower() in blocked_baseline_intents)]
        if candidates:
            out = candidates[:1]
        elif repeated:
            out = repeated[:1]
        else:
            out = (baseline_like[:1] or base[:1])
    return _dedupe_planned_commands(out, max_commands=max_commands)


def _minimal_probe_budget(
    *,
    session: Any,
    profile_map: dict[str, str],
    hypotheses: list[dict[str, Any]] | None,
    requested_max: int,
    follow_up: str = "",
    target_probe: dict[str, Any] | None = None,
) -> int:
    cap = max(1, min(int(requested_max or 3), 12))
    rounds = list(getattr(session, "rounds", []) or [])
    round_no = len(rounds) + 1
    ranked = rank_hypotheses(list(hypotheses or []))
    top = ranked[0] if ranked else {}
    top_domain = str(top.get("domain") or "").strip().lower()
    probe = _normalize_target_probe(target_probe)

    # Default to the smallest useful probe set.
    budget = 2 if round_no <= 1 else 2
    if bool(probe.get("stop_if_matched")):
        budget = 1
    if str(follow_up or "").strip():
        budget = max(budget, 3)
    if len(profile_map or {}) > 1:
        budget = max(budget, 3)
    if top_domain in {"routing", "firewall"}:
        budget = max(budget, 3)
    if _need_baseline_recheck(session):
        budget = max(budget, 3)
    if bool(probe.get("stop_if_matched")) and len(profile_map or {}) <= 1 and top_domain not in {"routing", "firewall"}:
        budget = min(budget, 1)
    return min(cap, budget)


def _normalize_target_probe(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    title = str(raw.get("title") or raw.get("focus") or raw.get("hint") or "").strip()
    if title:
        out["title"] = title
    domain = str(raw.get("domain") or "").strip().lower()
    if domain:
        out["domain"] = domain
    reason = str(raw.get("reason") or raw.get("why") or "").strip()
    if reason:
        out["reason"] = reason
    hint = str(raw.get("hint") or "").strip()
    if hint:
        out["hint"] = hint
    source = str(raw.get("source") or "").strip()
    if source:
        out["source"] = source
    hypothesis_id = str(raw.get("hypothesis_id") or "").strip()
    if hypothesis_id:
        out["hypothesis_id"] = hypothesis_id
    if "stop_if_matched" in raw:
        out["stop_if_matched"] = bool(raw.get("stop_if_matched"))
    stop_reason = str(raw.get("stop_reason") or "").strip()
    if stop_reason:
        out["stop_reason"] = stop_reason
    preferred_scope = str(raw.get("preferred_scope") or "").strip()
    if preferred_scope:
        out["preferred_scope"] = preferred_scope
    uncovered = [str(x).strip() for x in (raw.get("uncovered") or []) if str(x).strip()]
    if uncovered:
        out["uncovered"] = uncovered[:6]
    preferred_intents = [str(x).strip() for x in (raw.get("preferred_intents") or []) if str(x).strip()]
    if preferred_intents:
        out["preferred_intents"] = preferred_intents[:6]
    expected_signals = [str(x).strip() for x in (raw.get("expected_signals") or []) if str(x).strip()]
    if expected_signals:
        out["expected_signals"] = expected_signals[:6]
    expected_evidence = [str(x).strip() for x in (raw.get("expected_evidence") or []) if str(x).strip()]
    if expected_evidence:
        out["expected_evidence"] = expected_evidence[:6]
    return out


def _target_probe_text(target_probe: dict[str, Any] | None) -> str:
    probe = _normalize_target_probe(target_probe)
    if not probe:
        return ""
    title = str(probe.get("title") or "").strip()
    reason = str(probe.get("reason") or "").strip()
    uncovered = [str(x).strip() for x in (probe.get("uncovered") or []) if str(x).strip()]
    hint = str(probe.get("hint") or "").strip()
    preferred_intents = [str(x).strip() for x in (probe.get("preferred_intents") or []) if str(x).strip()]
    expected_signals = [str(x).strip() for x in (probe.get("expected_signals") or []) if str(x).strip()]
    expected_evidence = [str(x).strip() for x in (probe.get("expected_evidence") or []) if str(x).strip()]
    stop_reason = str(probe.get("stop_reason") or "").strip()
    parts: list[str] = []
    if title:
        parts.append(f"目标验证: {title}")
    if reason:
        parts.append(f"原因: {reason}")
    if uncovered:
        parts.append("未覆盖项: " + ", ".join(uncovered[:4]))
    if preferred_intents:
        parts.append("优先意图: " + ", ".join(preferred_intents[:4]))
    if expected_signals:
        parts.append("期望信号: " + ", ".join(expected_signals[:3]))
    if expected_evidence:
        parts.append("期望证据: " + ", ".join(expected_evidence[:3]))
    if stop_reason:
        parts.append("命中后动作: " + stop_reason)
    if hint:
        parts.append(hint)
    return "；".join(parts)


def _target_probe_focus_goals(target_probe: dict[str, Any] | None) -> list[str]:
    probe = _normalize_target_probe(target_probe)
    if not probe:
        return []
    return _normalize_focus_goals(
        [
            str(probe.get("title") or "").strip(),
            *[str(x).strip() for x in (probe.get("uncovered") or []) if str(x).strip()],
            *[str(x).strip() for x in (probe.get("expected_signals") or []) if str(x).strip()],
            *[str(x).strip() for x in (probe.get("expected_evidence") or []) if str(x).strip()],
        ]
    )


def _apply_target_probe_to_hypotheses(
    hypotheses: list[dict[str, Any]] | None,
    target_probe: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    probe = _normalize_target_probe(target_probe)
    ranked = rank_hypotheses(list(hypotheses or []))
    if not probe:
        return ranked
    domain = str(probe.get("domain") or "").strip().lower()
    title = str(probe.get("title") or "").strip().lower()
    preferred_intents = [str(x).strip() for x in (probe.get("preferred_intents") or []) if str(x).strip()]
    updated: list[dict[str, Any]] = []
    matched = False
    for raw in ranked:
        row = dict(raw or {})
        score = _safe_float(row.get("score"), _safe_float(row.get("confidence"), 0.0))
        row_domain = str(row.get("domain") or "").strip().lower()
        row_title = str(row.get("title") or "").strip().lower()
        if domain and row_domain == domain:
            score += 0.18
            row.setdefault("evidence_for", []).append(f"target_probe_domain {domain}")
            matched = True
        if title and row_title and (title in row_title or row_title in title):
            score += 0.12
            row.setdefault("evidence_for", []).append(f"target_probe_title {title}")
            matched = True
        if preferred_intents:
            merged_intents = []
            for intent in [*preferred_intents, *[str(x).strip() for x in (row.get("next_intents") or []) if str(x).strip()]]:
                if intent and intent not in merged_intents:
                    merged_intents.append(intent)
            row["next_intents"] = merged_intents[:8]
        row["score"] = round(min(0.99, max(0.0, score)), 4)
        row["confidence"] = round(min(0.99, max(_safe_float(row.get("confidence"), score), score)), 4)
        updated.append(row)
    if not matched:
        updated.append(
            {
                "hypothesis_id": "target_probe_focus",
                "title": str(probe.get("title") or "Targeted verification").strip(),
                "domain": str(probe.get("domain") or "global").strip().lower() or "global",
                "source": str(probe.get("source") or "target_probe").strip() or "target_probe",
                "score": 0.68,
                "confidence": 0.68,
                "next_intents": preferred_intents[:8],
                "evidence_for": [f"target_probe_injected {_target_probe_text(probe)}".strip()],
                "evidence_against": [],
            }
        )
    return rank_hypotheses(updated)


def _build_round_conclusion_block(
    *,
    hypotheses: list[dict[str, Any]],
    stop_decision: dict[str, Any],
    focus_review: dict[str, Any],
    retrospective: dict[str, Any],
) -> str:
    ranked = rank_hypotheses(hypotheses or [])
    top = ranked[0] if ranked else {}
    title = str(top.get("title") or "Undetermined").strip() or "Undetermined"
    domain = str(top.get("domain") or "global").strip() or "global"
    confidence = _safe_float(top.get("confidence"), _safe_float(top.get("score"), 0.0))
    recommend = bool((stop_decision or {}).get("recommend_conclude"))
    reason = str((stop_decision or {}).get("reason") or "").strip() or "no reason"
    next_action = str((stop_decision or {}).get("next_action") or ("conclude_with_verification" if recommend else "next_round_targeted_checks")).strip()
    uncovered = [str(x).strip() for x in ((focus_review or {}).get("uncovered") or []) if str(x).strip()]
    score_delta = _safe_float((retrospective or {}).get("top_hypothesis_score_delta"), 0.0)
    success_rate = _safe_float((retrospective or {}).get("execution_success_rate"), 0.0)

    if recommend:
        judgement = f"已发现足够证据，可收敛到头号假设“{title}”。"
    elif uncovered:
        judgement = f"当前尚未收敛，仍有未覆盖关注点：{', '.join(uncovered[:4])}。"
    else:
        judgement = f"当前尚未收敛，下一轮继续验证头号假设“{title}”。"

    return (
        "[Round Conclusion]\n"
        f"- 当前判定: {judgement}\n"
        f"- 头号假设: {title} [{domain}]\n"
        f"- 置信度: {confidence:.3f}\n"
        f"- 下一步: {next_action}\n"
        f"- 原因: {reason}\n"
        f"- 执行概况: success_rate={success_rate:.2f} score_delta={score_delta:+.3f}"
    )


def _baseline_summary_text(session: Any) -> str:
    items = getattr(session, "time_calibration", []) or []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.append(
            (
                f"device={item.get('device_id')} ip={item.get('device_ip')} status={item.get('status')} "
                f"vendor={item.get('vendor')} os_family={item.get('os_family')} "
                f"model={item.get('model')} version={item.get('version')} "
                f"offset={item.get('offset_seconds')} "
                f"device_window=[{item.get('device_start')} ~ {item.get('device_end')}] "
                f"hits={item.get('hits_count')} log_range=[{item.get('log_time_min')} ~ {item.get('log_time_max')}]"
            )
        )
    return "\n".join(lines)


def _normalize_focus_goals(goals: list[str] | None) -> list[str]:
    rows: list[str] = []
    for g in goals or []:
        rows.extend(_split_goal_text(g))
    return _dedupe_texts(rows, limit=24)


def _focus_terms(goal: str) -> list[str]:
    goal_text = str(goal or "")
    tokens = re.findall(r"[A-Za-z0-9_/-]{3,}|[\u4e00-\u9fff]{2,}", goal_text)
    stop_words = {
        "please",
        "check",
        "diagnose",
        "issue",
        "network",
        "device",
        "fault",
        "用户",
        "故障",
        "诊断",
        "设备",
        "网络",
        "继续",
        "核查",
    }
    out: list[str] = []
    for t in tokens:
        key = t.casefold()
        if key in stop_words:
            continue
        if len(key) < 2:
            continue
        out.append(key)
        if "_" in key:
            for part in [p.strip().casefold() for p in key.split("_") if p.strip()]:
                if len(part) >= 2 and part not in stop_words and part not in out:
                    out.append(part)

    # Semantic alias expansion keeps focus coverage practical across CN/EN wording.
    low = goal_text.casefold()
    alias: list[str] = []
    if any(x in low for x in ("抖动", "flap", "up/down", "链路")):
        alias.extend(["interface", "brief", "log", "logging", "logbuffer"])
    if any(x in low for x in ("丢包", "loss", "drop", "drops")):
        alias.extend(["drop", "error", "errors", "counter", "interface", "log"])
    if any(x in low for x in ("配置", "config", "configuration")):
        alias.extend(["configuration", "current-configuration", "interface", "commit"])
    if any(x in low for x in ("shutdown", "admin", "administrative", "管理关闭")):
        alias.extend(["shutdown", "admin", "administratively", "down", "interface"])
    if any(x in low for x in ("日志", "log", "logging", "logbuffer")):
        alias.extend(["log", "logging", "logbuffer", "buffer"])
    if any(x in low for x in ("stp", "mstp", "阻塞", "blocking")):
        alias.extend(["stp", "mstp", "blocking"])
    if any(x in low for x in ("时延", "latency", "delay")):
        alias.extend(["cpu", "resource", "interface", "session", "routing"])
    if any(x in low for x in ("路由", "routing", "ospf", "bgp")):
        alias.extend(["route", "routing", "ospf", "bgp"])

    for t in alias:
        k = t.casefold()
        if k not in out:
            out.append(k)
    return out


def _build_focus_lock_block(focus_goals: list[str]) -> str:
    goals = _normalize_focus_goals(focus_goals)
    if not goals:
        return "\n[FocusLock]\n(no explicit focus goals)"
    rows = [f"- {g}" for g in goals]
    return "\n[FocusLock Goals]\n" + "\n".join(rows)


def _focus_review(goals: list[str], evidence_text: str) -> dict[str, Any]:
    normalized = _normalize_focus_goals(goals)
    text = str(evidence_text or "").casefold()
    covered: list[str] = []
    uncovered: list[str] = []
    for g in normalized:
        terms = _focus_terms(g)
        if not terms:
            uncovered.append(g)
            continue
        ok = any(t in text for t in terms)
        (covered if ok else uncovered).append(g)
    total = len(normalized)
    coverage = (len(covered) / total) if total > 0 else 1.0
    return {
        "goals": normalized,
        "covered": covered,
        "uncovered": uncovered,
        "coverage_ratio": round(coverage, 3),
    }


def _focus_review_from_commands(goals: list[str], commands: list[PlannedCommand]) -> dict[str, Any]:
    chunks: list[str] = []
    for c in commands:
        chunks.append(f"{c.command}\n{c.reason}\n{c.expected_signal}")
    return _focus_review(goals, "\n".join(chunks))


def _expected_signal_review(
    target_probe: dict[str, Any] | None,
    evidence_text: str,
    validation_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = _normalize_validation_task(validation_task)
    probe = _normalize_target_probe(task.get("current_probe")) or _normalize_target_probe(target_probe)
    expected = sorted(
        set(
            [
                *[str(x).strip() for x in (task.get("expected_signals") or []) if str(x).strip()],
                *[str(x).strip() for x in (task.get("expected_evidence") or []) if str(x).strip()],
                *[str(x).strip() for x in (probe.get("expected_signals") or []) if str(x).strip()],
                *[str(x).strip() for x in (probe.get("expected_evidence") or []) if str(x).strip()],
            ]
        )
    )
    if not expected:
        return {"expected_signals": [], "matched": [], "unmatched": [], "coverage_ratio": 1.0}
    text = str(evidence_text or "").casefold()
    matched: list[str] = []
    unmatched: list[str] = []
    for signal in expected:
        terms = _focus_terms(signal)
        ok = any(str(t).strip() and str(t).casefold() in text for t in terms)
        (matched if ok else unmatched).append(signal)
    total = len(expected)
    coverage = (len(matched) / total) if total > 0 else 1.0
    return {
        "expected_signals": expected,
        "matched": matched,
        "unmatched": unmatched,
        "coverage_ratio": round(coverage, 3),
    }


def _apply_expected_signal_stop_decision(
    stop_decision: dict[str, Any] | None,
    *,
    target_probe: dict[str, Any] | None,
    expected_signal_review: dict[str, Any] | None,
    focus_review: dict[str, Any] | None,
    validation_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stop = dict(stop_decision or {})
    task = _normalize_validation_task(validation_task)
    probe = (
        _normalize_target_probe(task.get("next_probe"))
        or _normalize_target_probe(task.get("current_probe"))
        or _normalize_target_probe(target_probe)
    )
    review = dict(expected_signal_review or {})
    expected = [str(x).strip() for x in (review.get("expected_signals") or task.get("expected_signals") or []) if str(x).strip()]
    if not expected:
        return stop
    uncovered_focus = [
        str(x).strip()
        for x in (((focus_review or {}).get("uncovered") or task.get("uncovered_goals") or []))
        if str(x).strip()
    ]
    coverage = _safe_float(review.get("coverage_ratio"), 0.0)
    matched = [str(x).strip() for x in (review.get("matched") or []) if str(x).strip()]
    unmatched = [str(x).strip() for x in (review.get("unmatched") or []) if str(x).strip()]
    stop_reason = str(task.get("stop_reason") or probe.get("stop_reason") or "").strip() or "conclude_with_verification"
    stop_if_matched = bool(task.get("stop_if_matched") or probe.get("stop_if_matched"))
    if coverage >= 0.999 and not uncovered_focus:
        stop["recommend_conclude"] = True
        stop["next_action"] = stop_reason if stop_if_matched else "conclude_with_verification"
        stop["reason"] = (
            f"expected_signals_fully_matched coverage={coverage:.3f}"
            + (f" stop_reason={stop_reason}" if stop_if_matched else "")
        )
        stop["confidence"] = max(_safe_float(stop.get("confidence"), 0.0), 0.9)
        return stop
    if matched and not bool(stop.get("recommend_conclude")):
        stop["reason"] = (
            f"expected_signals_partial_match coverage={coverage:.3f} "
            f"matched={len(matched)} unmatched={len(unmatched)}"
        )
        stop["confidence"] = max(_safe_float(stop.get("confidence"), 0.0), min(0.84, 0.55 + 0.25 * coverage))
    return stop


def _infer_next_probe_intents(expected_items: list[str], domain: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def push(name: str) -> None:
        key = str(name or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(key)

    for item in expected_items:
        low = str(item or "").strip().lower()
        if not low:
            continue
        if any(x in low for x in ("log", "日志", "stp", "mstp", "event", "告警", "shutdown")):
            push("system_log_recent")
        if any(x in low for x in ("interface", "port", "接口", "端口", "admin", "config", "配置", "shutdown", "up", "down")):
            push("interface_summary")
        if any(x in low for x in ("crc", "error", "discard", "drop", "丢包", "错误", "计数")):
            push("interface_errors")
        if any(x in low for x in ("clock", "time", "ntp", "时钟", "时间")):
            push("clock_check")
        if any(x in low for x in ("cpu", "memory", "resource", "资源")):
            push("cpu_health")
        if any(x in low for x in ("route", "routing", "路由")):
            push("routing_summary")
        if any(x in low for x in ("ospf", "neighbor", "邻居")):
            push("ospf_neighbor")
        if "bgp" in low:
            push("bgp_summary")

    for name in DOMAIN_INTENT_PIPELINE.get(str(domain or "").strip().lower(), []):
        push(name)
    return out[:6]


def _derive_next_target_probe(
    *,
    target_probe: dict[str, Any] | None,
    hypotheses: list[dict[str, Any]] | None,
    stop_decision: dict[str, Any] | None,
    focus_review: dict[str, Any] | None,
    expected_signal_review: dict[str, Any] | None,
    validation_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stop = dict(stop_decision or {})
    if bool(stop.get("recommend_conclude")):
        return {}
    task = _normalize_validation_task(validation_task)
    probe = _normalize_target_probe(task.get("current_probe")) or _normalize_target_probe(target_probe)
    hyps = list(hypotheses or [])
    top_stop = stop.get("top_hypothesis") if isinstance(stop.get("top_hypothesis"), dict) else {}
    top_id = str((top_stop or {}).get("hypothesis_id") or "").strip()
    top_title = str((top_stop or {}).get("title") or "").strip()
    top = next(
        (
            row for row in hyps
            if isinstance(row, dict)
            and (
                (top_id and str(row.get("hypothesis_id") or "").strip() == top_id)
                or (top_title and str(row.get("title") or "").strip() == top_title)
            )
        ),
        hyps[0] if hyps else {},
    )
    focus = dict(focus_review or {})
    review = dict(expected_signal_review or {})
    uncovered = [str(x).strip() for x in (task.get("uncovered_goals") or focus.get("uncovered") or []) if str(x).strip()]
    unmatched = [str(x).strip() for x in (task.get("unmatched_signals") or review.get("unmatched") or []) if str(x).strip()]
    expected_items = (unmatched[:4] or uncovered[:4])
    merged_next_intents = [str(x).strip() for x in (task.get("preferred_intents") or top.get("next_intents") or []) if str(x).strip()]
    inferred = _infer_next_probe_intents(expected_items, str(top.get("domain") or probe.get("domain") or "").strip().lower())
    preferred_intents: list[str] = []
    seen: set[str] = set()
    for item in [*inferred, *merged_next_intents]:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        preferred_intents.append(key)
    hint = ""
    if unmatched:
        hint = "补齐未命中的预期信号: " + " / ".join(unmatched[:3])
    elif uncovered:
        hint = "优先补齐未覆盖目标: " + " / ".join(uncovered[:3])
    return _normalize_target_probe(
        {
            "title": str((top or {}).get("title") or probe.get("title") or "continue targeted probe").strip(),
            "domain": str((top or {}).get("domain") or probe.get("domain") or "").strip().lower(),
            "reason": str(stop.get("reason") or probe.get("reason") or "").strip(),
            "hypothesis_id": str((top or {}).get("hypothesis_id") or probe.get("hypothesis_id") or "").strip(),
            "stop_if_matched": True,
            "stop_reason": str(task.get("stop_reason") or probe.get("stop_reason") or "conclude_with_verification").strip() or "conclude_with_verification",
            "preferred_scope": str(task.get("preferred_scope") or probe.get("preferred_scope") or "related_commands").strip() or "related_commands",
            "uncovered": uncovered,
            "preferred_intents": preferred_intents,
            "expected_signals": expected_items,
            "expected_evidence": expected_items,
            "source": "analyze_continue_probe",
            "hint": hint,
        }
    )


def _build_validation_task(
    *,
    target_probe: dict[str, Any] | None,
    next_target_probe: dict[str, Any] | None,
    expected_signal_review: dict[str, Any] | None,
    focus_review: dict[str, Any] | None,
    stop_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    current_probe = _normalize_target_probe(target_probe)
    next_probe = _normalize_target_probe(next_target_probe)
    review = dict(expected_signal_review or {})
    focus = dict(focus_review or {})
    stop = dict(stop_decision or {})
    if not current_probe and not next_probe and not review and not focus and not stop:
        return {}
    current_signals = [str(x).strip() for x in (current_probe.get("expected_signals") or []) if str(x).strip()]
    current_evidence = [str(x).strip() for x in (current_probe.get("expected_evidence") or []) if str(x).strip()]
    matched = [str(x).strip() for x in (review.get("matched") or []) if str(x).strip()]
    unmatched = [str(x).strip() for x in (review.get("unmatched") or []) if str(x).strip()]
    covered = [str(x).strip() for x in (focus.get("covered") or []) if str(x).strip()]
    uncovered = [str(x).strip() for x in (focus.get("uncovered") or []) if str(x).strip()]
    return {
        "current_probe": current_probe,
        "next_probe": next_probe,
        "preferred_scope": str(next_probe.get("preferred_scope") or current_probe.get("preferred_scope") or "").strip(),
        "preferred_intents": [
            str(x).strip()
            for x in (next_probe.get("preferred_intents") or current_probe.get("preferred_intents") or [])
            if str(x).strip()
        ],
        "expected_signals": current_signals,
        "expected_evidence": current_evidence,
        "matched_signals": matched,
        "unmatched_signals": unmatched,
        "coverage_ratio": review.get("coverage_ratio"),
        "covered_goals": covered,
        "uncovered_goals": uncovered,
        "stop_if_matched": bool(next_probe.get("stop_if_matched") or current_probe.get("stop_if_matched")),
        "stop_reason": str(next_probe.get("stop_reason") or current_probe.get("stop_reason") or "").strip(),
        "next_action": str(stop.get("next_action") or "").strip(),
        "confidence": stop.get("confidence"),
        "reason": str(stop.get("reason") or "").strip(),
    }


def _normalize_validation_task(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    current_probe = _normalize_target_probe(raw.get("current_probe"))
    next_probe = _normalize_target_probe(raw.get("next_probe"))
    return {
        "current_probe": current_probe,
        "next_probe": next_probe,
        "preferred_scope": str(raw.get("preferred_scope") or "").strip(),
        "preferred_intents": [str(x).strip() for x in (raw.get("preferred_intents") or []) if str(x).strip()],
        "expected_signals": [str(x).strip() for x in (raw.get("expected_signals") or []) if str(x).strip()],
        "expected_evidence": [str(x).strip() for x in (raw.get("expected_evidence") or []) if str(x).strip()],
        "matched_signals": [str(x).strip() for x in (raw.get("matched_signals") or []) if str(x).strip()],
        "unmatched_signals": [str(x).strip() for x in (raw.get("unmatched_signals") or []) if str(x).strip()],
        "coverage_ratio": raw.get("coverage_ratio"),
        "covered_goals": [str(x).strip() for x in (raw.get("covered_goals") or []) if str(x).strip()],
        "uncovered_goals": [str(x).strip() for x in (raw.get("uncovered_goals") or []) if str(x).strip()],
        "stop_if_matched": bool(raw.get("stop_if_matched")),
        "stop_reason": str(raw.get("stop_reason") or "").strip(),
        "next_action": str(raw.get("next_action") or "").strip(),
        "confidence": raw.get("confidence"),
        "reason": str(raw.get("reason") or "").strip(),
    }


def _merge_validation_task_context(
    validation_task: dict[str, Any] | None,
    *,
    current_probe: dict[str, Any] | None = None,
    next_probe: dict[str, Any] | None = None,
    expected_signal_review: dict[str, Any] | None = None,
    focus_review: dict[str, Any] | None = None,
    stop_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = _normalize_validation_task(validation_task)
    cur = _normalize_target_probe(current_probe)
    nxt = _normalize_target_probe(next_probe)
    review = dict(expected_signal_review or {})
    focus = dict(focus_review or {})
    stop = dict(stop_decision or {})
    if not task and not cur and not nxt and not review and not focus and not stop:
        return {}
    merged = {
        **task,
        "current_probe": task.get("current_probe") or cur,
        "next_probe": task.get("next_probe") or nxt,
        "preferred_scope": str(task.get("preferred_scope") or nxt.get("preferred_scope") or cur.get("preferred_scope") or "").strip(),
        "preferred_intents": list(task.get("preferred_intents") or nxt.get("preferred_intents") or cur.get("preferred_intents") or []),
        "expected_signals": list(task.get("expected_signals") or cur.get("expected_signals") or []),
        "expected_evidence": list(task.get("expected_evidence") or cur.get("expected_evidence") or []),
        "matched_signals": list(task.get("matched_signals") or review.get("matched") or []),
        "unmatched_signals": list(task.get("unmatched_signals") or review.get("unmatched") or []),
        "coverage_ratio": task.get("coverage_ratio") if task.get("coverage_ratio") is not None else review.get("coverage_ratio"),
        "covered_goals": list(task.get("covered_goals") or focus.get("covered") or []),
        "uncovered_goals": list(task.get("uncovered_goals") or focus.get("uncovered") or []),
        "stop_if_matched": bool(
            task.get("stop_if_matched")
            if task.get("stop_if_matched") is True
            else (nxt.get("stop_if_matched") or cur.get("stop_if_matched"))
        ),
        "stop_reason": str(task.get("stop_reason") or nxt.get("stop_reason") or cur.get("stop_reason") or "").strip(),
        "next_action": str(task.get("next_action") or stop.get("next_action") or "").strip(),
        "confidence": task.get("confidence") if task.get("confidence") is not None else stop.get("confidence"),
        "reason": str(task.get("reason") or stop.get("reason") or "").strip(),
    }
    return _normalize_validation_task(merged)


def _validation_task_to_target_probe(validation_task: dict[str, Any] | None) -> dict[str, Any]:
    task = _normalize_validation_task(validation_task)
    next_probe = _normalize_target_probe(task.get("next_probe"))
    if next_probe:
        return next_probe
    return _normalize_target_probe(task.get("current_probe"))


def _load_round_evidence_text(base_root: Path, session_id: str, round_no: int) -> str:
    base = base_root / session_id / f"round_{round_no}"
    chunks: list[str] = []
    if not base.exists():
        return ""
    total = 0
    per_file_limit = 1600
    total_limit = 64000
    for p in sorted(base.rglob("*.txt")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        block = f"## {p.name}\n{text[:per_file_limit]}"
        if total + len(block) > total_limit:
            remain = max(0, total_limit - total)
            if remain > 80:
                chunks.append(block[:remain])
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)


def _looks_incomplete_llm_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    if len(raw) < 120:
        return True
    if raw.count("```") % 2 == 1:
        return True
    tail = raw.splitlines()[-1].strip()
    if re.fullmatch(r"(\d+\.)|[-*]|#+|[A-Za-z\u4e00-\u9fff _-]+[:：]", tail):
        return True
    # Common abrupt ending pattern in numbered sections.
    if re.search(r"(证据链|evidence chain)\s*[:：]?\s*1\.\s*$", raw, flags=re.I):
        return True
    return False


async def _run_llm_text_with_retry(
    llm_input: dict[str, str],
    report_text: str,
    timeout_sec: int,
    attempts: int = 1,
    shrink_on_retry: bool = True,
    strict_text_check: bool = False,
    failover_llm_input: dict[str, str] | None = None,
    failover_attempts: int = 1,
) -> tuple[str, str]:
    tries = max(1, min(int(attempts), 4))
    timeout = max(8, min(int(timeout_sec), 480))
    base_text = str(report_text or "")
    last_error = ""
    primary_error = ""

    for idx in range(tries):
        text = base_text
        if idx > 0 and shrink_on_retry:
            keep = max(1200, int(len(base_text) * (0.7**idx)))
            text = base_text[:keep]
        try:
            out_text, _usage = await asyncio.to_thread(
                run_analysis,
                llm_input,
                text,
                timeout,
            )
            if str(out_text or "").strip():
                if strict_text_check and _looks_incomplete_llm_text(str(out_text)):
                    last_error = "model response looks incomplete/truncated"
                    continue
                return str(out_text), ""
            last_error = "empty response from model"
        except Exception as exc:
            last_error = str(exc)
        await asyncio.sleep(min(1.5, 0.4 * (idx + 1)))
    primary_error = str(last_error or "")

    failover_row = failover_llm_input if isinstance(failover_llm_input, dict) else None
    if failover_row and _llm_signature(failover_row) and _llm_signature(failover_row) != _llm_signature(llm_input):
        fo_tries = max(1, min(int(failover_attempts), 2))
        for idx in range(fo_tries):
            text = base_text
            if idx > 0 and shrink_on_retry:
                keep = max(1200, int(len(base_text) * (0.7**idx)))
                text = base_text[:keep]
            try:
                out_text, _usage = await asyncio.to_thread(
                    run_analysis,
                    failover_row,
                    text,
                    timeout,
                )
                if str(out_text or "").strip():
                    if strict_text_check and _looks_incomplete_llm_text(str(out_text)):
                        last_error = "failover response looks incomplete/truncated"
                        continue
                    return str(out_text), ""
                last_error = "empty response from failover model"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(min(1.2, 0.35 * (idx + 1)))
        if primary_error:
            return "", f"primary={primary_error}; failover={last_error or 'unknown error'}"

    return "", last_error


async def _run_planner_llm(
    question: str,
    session: Any,
    max_commands: int,
    ai_timeout_sec: int = 120,
    ai_retries: int = 1,
    focus_goals: list[str] | None = None,
    learning_store: NetdiagLearningStore | None = None,
    version_map: dict[str, str] | None = None,
    sop_hypotheses: list[dict[str, Any]] | None = None,
    sop_steps: list[dict[str, Any]] | None = None,
    issue_hits: list[dict[str, Any]] | None = None,
    case_hits: list[dict[str, Any]] | None = None,
    llm_primary: dict[str, str] | None = None,
    llm_failover: dict[str, str] | None = None,
) -> tuple[str, list[PlannedCommand], str]:
    max_commands = max(1, min(max_commands, 12))
    profile_map = _device_profile_map(session)
    version_map = version_map or _device_version_map(session)

    llm = dict(llm_primary or _build_llm_input())
    base_prompt = llm.get("task_prompt_text", "")
    llm["task_prompt_text"] = (
        base_prompt
        + "\n\n[NetDiag Planner Task]"
        + "\n先基于设备画像选择 intent，再映射到可执行命令。"
        + "\n命令必须是 show/display/dis，只读。禁止配置、重启、删除。"
        + "\n若过去轮次已执行过同一命令，除非用于验证状态变化，否则避免重复。"
        + "\n必须输出 JSON 对象，优先使用 intent 步骤："
        + '\n{"planner_summary":"...","steps":[{"device_id":"dev-1","intent":"clock_check","reason":"..."}],"commands":[]}'
        + f"\nsteps+commands 总数不超过 {max_commands}。"
        + _intent_prompt_block(profile_map)
        + _build_focus_lock_block(focus_goals or [])
        + _sop_context_block(
            hypotheses=sop_hypotheses or [],
            issue_hits=issue_hits or [],
            case_hits=case_hits or [],
            sop_steps=sop_steps or [],
        )
        + "\n要求：本轮命令应尽量覆盖 FocusLock Goals；如无法覆盖必须在 planner_summary 说明原因。"
    )

    latest_round_context = ""
    rounds = getattr(session, "rounds", []) or []
    if rounds:
        try:
            last = rounds[-1]
            if getattr(last, "analysis_result", ""):
                latest_round_context = f"\n\nPreviousRoundAnalysis:\n{str(last.analysis_result)[:4800]}"
        except Exception:
            latest_round_context = ""
    recent_rounds_context = _recent_rounds_context(session, limit_rounds=2)
    if len(recent_rounds_context) > 6000:
        recent_rounds_context = recent_rounds_context[:6000]

    report_text = (
        f"Question:\n{question}\n\n"
        f"FaultWindow:\n{session.fault_window.start_at} ~ {session.fault_window.end_at} ({session.fault_window.timezone})\n\n"
        f"Baseline:\n{_baseline_summary_text(session)}"
        + _build_focus_lock_block(focus_goals or [])
        + ("\n\n[RecentRounds]\n" + recent_rounds_context if recent_rounds_context else "")
        + "\n"
        f"{latest_round_context}"
    )
    if len(report_text) > 18000:
        report_text = report_text[:18000]

    raw_text, llm_err = await _run_llm_text_with_retry(
        llm_input=llm,
        report_text=report_text,
        timeout_sec=max(20, min(int(ai_timeout_sec), 360)),
        attempts=ai_retries,
        shrink_on_retry=True,
        failover_llm_input=llm_failover,
        failover_attempts=1,
    )

    parsed = _extract_json_obj(raw_text)
    fallback_summary = "AI planner fallback"
    if llm_err:
        fallback_summary = f"AI planner fallback: {llm_err}"
    summary = str(parsed.get("planner_summary", "") or "").strip() or fallback_summary

    commands: list[PlannedCommand] = []

    # First path: intent-driven deterministic mapping
    raw_steps = parsed.get("steps", []) if isinstance(parsed, dict) else []
    if isinstance(raw_steps, list):
        for item in raw_steps[:max_commands]:
            if not isinstance(item, dict):
                continue
            did = str(item.get("device_id", "*") or "*").strip() or "*"
            intent = str(item.get("intent", "") or "").strip()
            profile = profile_map.get(did)
            if not profile and did == "*" and profile_map:
                profile = next(iter(profile_map.values()))
            if not profile:
                profile = "unknown"
            command, source = _resolve_intent_command(
                intent=intent,
                profile=profile,
                version=str(version_map.get(did, "") or ""),
                learning_store=learning_store,
            )
            if not command or not is_read_only_command(command):
                continue
            reason = str(item.get("reason", "") or INTENT_DESCRIPTIONS.get(intent, ""))
            if source == "library":
                reason = f"{reason} [library]"
            commands.append(
                PlannedCommand(
                    command_id=uuid.uuid4().hex[:12],
                    device_id=did,
                    intent=intent,
                    command=command,
                    reason=reason,
                    expected_signal=str(item.get("expected_signal") or INTENT_DESCRIPTIONS.get(intent, "")),
                    risk_level="read_only",
                    requires_approval=True,
                    approved=False,
                )
            )

    # Second path: backward-compatible direct command mode
    raw_commands = parsed.get("commands", []) if isinstance(parsed, dict) else []
    if isinstance(raw_commands, list) and len(commands) < max_commands:
        for item in raw_commands:
            if len(commands) >= max_commands:
                break
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", "") or "").strip()
            if not command or has_placeholder_token(command) or not is_read_only_command(command):
                continue
            did = str(item.get("device_id", "*") or "*")
            profile = profile_map.get(did)
            if not profile and did == "*" and profile_map:
                profile = next(iter(profile_map.values()))
            intent = str(item.get("intent") or "").strip() or _infer_intent_from_command(command, profile or "unknown")
            commands.append(
                PlannedCommand(
                    command_id=uuid.uuid4().hex[:12],
                    device_id=did,
                    intent=intent,
                    command=command,
                    reason=str(item.get("reason", "") or ""),
                    expected_signal=str(item.get("expected_signal", "") or ""),
                    risk_level="read_only",
                    requires_approval=True,
                    approved=False,
                )
            )

    if commands:
        return (
            summary,
            _enforce_progressive_plan(
                commands=commands,
                session=session,
                profile_map=profile_map,
                version_map=version_map,
                max_commands=max_commands,
                learning_store=learning_store,
            ),
            raw_text,
        )

    # Third path: SOP deterministic steps from hypothesis engine.
    sop_commands = _build_commands_from_sop_steps(
        session=session,
        profile_map=profile_map,
        version_map=version_map,
        sop_steps=sop_steps,
        max_commands=max_commands,
        learning_store=learning_store,
    )
    if sop_commands:
        return (summary, sop_commands, raw_text)

    commands = _fallback_commands(
        profile_map,
        max_commands=max_commands,
        learning_store=learning_store,
        version_map=version_map,
    )
    return (
        summary,
        _enforce_progressive_plan(
            commands=commands,
            session=session,
            profile_map=profile_map,
            version_map=version_map,
            max_commands=max_commands,
            learning_store=learning_store,
        ),
        raw_text,
    )


def _build_commands_from_sop_steps(
    *,
    session: Any,
    profile_map: dict[str, str],
    version_map: dict[str, str] | None,
    sop_steps: list[dict[str, Any]] | None,
    max_commands: int,
    learning_store: NetdiagLearningStore | None = None,
) -> list[PlannedCommand]:
    version_map = version_map or {}
    commands: list[PlannedCommand] = []
    rows = list(sop_steps or [])
    if rows:
        for item in rows:
            if len(commands) >= max(1, min(max_commands, 12)):
                break
            if not isinstance(item, dict):
                continue
            did = str(item.get("device_id") or "*").strip() or "*"
            intent = str(item.get("intent") or "").strip()
            if not intent:
                continue
            profile = profile_map.get(did)
            if not profile and did == "*" and profile_map:
                profile = next(iter(profile_map.values()))
            if not profile:
                profile = "unknown"
            command, source = _resolve_intent_command(
                intent=intent,
                profile=profile,
                version=str(version_map.get(did, "") or ""),
                learning_store=learning_store,
            )
            if not command or not is_read_only_command(command):
                continue
            reason = str(item.get("reason") or "SOP targeted step")
            if source == "library":
                reason = f"{reason} [library]"
            commands.append(
                PlannedCommand(
                    command_id=uuid.uuid4().hex[:12],
                    device_id=did,
                    intent=intent,
                    command=command,
                    reason=reason,
                    expected_signal=str(item.get("expected_signal") or INTENT_DESCRIPTIONS.get(intent, "")),
                    risk_level="read_only",
                    requires_approval=True,
                    approved=False,
                )
            )
    if not commands:
        commands = _fallback_commands(
            profile_map,
            max_commands=max_commands,
            learning_store=learning_store,
            version_map=version_map,
        )
    if not commands:
        return []
    return _enforce_progressive_plan(
        commands=commands,
        session=session,
        profile_map=profile_map,
        version_map=version_map,
        max_commands=max_commands,
        learning_store=learning_store,
    )


def _require_learning_store(request: Request) -> NetdiagLearningStore:
    store = getattr(request.app.state, "learning_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="learning store unavailable")
    return store


def _require_zabbix_store(request: Request) -> NetdiagZabbixStore:
    store = getattr(request.app.state, "zabbix_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="zabbix store unavailable")
    return store


def _require_connection_store(request: Request) -> NetdiagConnectionStore:
    store = getattr(request.app.state, "connection_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="connection store unavailable")
    return store


def _require_state_store(request: Request) -> NetdiagStateStore:
    store = getattr(request.app.state, "state_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="state store unavailable")
    return store


def _require_config_store(request: Request) -> NetdiagConfigStore:
    store = getattr(request.app.state, "config_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="config store unavailable")
    return store


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _to_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return bool(default)
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = float(default)
    if n < 0.0:
        return 0.0
    if n > 1.0:
        return 1.0
    return round(n, 3)


def _valid_ipv4(ip: str) -> bool:
    text = str(ip or "").strip()
    if not text:
        return False
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(x) for x in parts]
    except Exception:
        return False
    return all(0 <= x <= 255 for x in nums)


def _normalize_dialog_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=\d)\s*[。．｡﹒·]\s*(?=\d)", ".", text)
    text = text.replace("：", ":").replace("，", ",").replace("；", ";")
    return text


def _extract_first_ipv4(text: str) -> str:
    source = _normalize_dialog_text(text)
    for m in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", str(source or "")):
        if _valid_ipv4(m):
            return m
    return ""


def _split_goal_text(raw: Any) -> list[str]:
    def _split_long_cn_goal(text: str) -> list[str]:
        s = str(text or "").strip()
        if not s:
            return []
        if re.search(r"[,，;；\n|、]", s):
            return [s]
        if not re.search(r"[\u4e00-\u9fff]", s) or len(s) < 14:
            return [s]
        parts = [x.strip() for x in re.split(r"(?=(?:确认|定位|检查|排查|查看|分析|验证|关注|聚焦|重点))", s) if str(x or "").strip()]
        if len(parts) >= 2:
            cleaned = [x for x in parts if len(x) >= 2]
            if cleaned:
                return cleaned
        return [s]

    rows: list[str] = []
    if isinstance(raw, list):
        source = [str(x or "").strip() for x in raw]
    else:
        source = [x.strip() for x in re.split(r"[,，;；\n|、]+", str(raw or ""))]
    for item in source:
        if not item:
            continue
        rows.extend(_split_long_cn_goal(item))
    return [x for x in rows if x]


def _dedupe_texts(rows: list[str], limit: int = 8) -> list[str]:
    out: list[str] = []
    for item in rows:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        text = text[:120]
        key = text.casefold()
        skip = False
        replaced = False
        for idx, old in enumerate(out):
            old_key = old.casefold()
            if key == old_key:
                skip = True
                break
            min_len = min(len(key), len(old_key))
            if min_len >= 6 and (key in old_key or old_key in key):
                if key in old_key and (len(key) + 2) < len(old_key):
                    out[idx] = text
                    replaced = True
                skip = True
                break
        if skip and not replaced:
            continue
        if not skip:
            out.append(text)
        if len(out) >= max(1, min(int(limit), 30)):
            break
    return out


def _guess_focus_goals(text: str) -> list[str]:
    raw = str(text or "")
    goals: list[str] = []
    for m in re.finditer(r"(?:重点(?:检查|看|排查)?|focus\s*on|focus|排查方向|诊断方向|方向)\s*[:：]?\s*([^\n\r]+)", raw, flags=re.I):
        goals.extend(_split_goal_text(m.group(1)))

    low = raw.casefold()
    if "mstp" in low or "stp" in low:
        goals.append("MSTP/STP")
    if "crc" in low or "fcs" in low:
        goals.append("CRC/FCS")
    if "丢包" in raw or "drop" in low or "loss" in low:
        goals.append("丢包/Drop")
    if "up down" in low or "up/down" in low or "抖动" in raw or "flap" in low:
        goals.append("端口抖动/Flap")
    return _dedupe_texts(goals, limit=8)


def _guess_question(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    tagged = re.search(r"(?:问题描述|故障描述|问题|现象|issue|problem)\s*[:：]\s*([^\n\r]+)", raw, flags=re.I)
    if tagged and str(tagged.group(1) or "").strip():
        return str(tagged.group(1)).strip()[:500]
    block = re.search(r"(?:^|\n)\s*(?:问题描述|故障描述|问题|现象|issue|problem)\s*[:：]?\s*([\s\S]+)", raw, flags=re.I)
    if block and str(block.group(1) or "").strip():
        body = str(block.group(1) or "").strip()
        body = re.split(
            r"(?:\n|\r)\s*(?:本轮诊断方向|诊断方向|排查方向|方向|focus\s*goals?|focus|goals?|direction)\s*[:：]?",
            body,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if body:
            first_line = body.splitlines()[0].strip()
            if first_line:
                return first_line[:500]
    cleaned = re.split(
        r"(?:本轮诊断方向|诊断方向|排查方向|方向|focus\s*goals?|focus|goals?|direction)\s*[:：]",
        raw,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    return (cleaned or raw)[:500]


def _normalize_user_time_text(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    return (
        s.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("点", ":")
        .replace("时", ":")
        .replace("分", ":")
        .replace("秒", "")
        .replace("上午", " AM")
        .replace("下午", " PM")
        .replace("/", "-")
    )


def _to_tz(dt: datetime, timezone_name: str) -> datetime:
    tz_name = str(timezone_name or "Asia/Singapore").strip() or "Asia/Singapore"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Singapore")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _parse_user_datetime(raw: Any, timezone_name: str = "Asia/Singapore") -> datetime | None:
    s = _normalize_user_time_text(raw)
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()

    # ISO-like first.
    iso_candidate = s
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}(?: \d{1,2}:\d{2}(?::\d{2})?)?$", iso_candidate):
        iso_candidate = iso_candidate.replace(" ", "T")
    try:
        dt_iso = datetime.fromisoformat(iso_candidate.replace("Z", "+00:00"))
        return _to_tz(dt_iso, timezone_name)
    except Exception:
        pass

    plain = s.replace(",", "")
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d-%Y %H:%M:%S",
        "%m-%d-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%b %d %Y %H:%M:%S",
        "%b %d %Y %H:%M",
        "%B %d %Y %H:%M:%S",
        "%B %d %Y %H:%M",
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
        "%d %B %Y %H:%M:%S",
        "%d %B %Y %H:%M",
        "%b %d %Y %I:%M:%S %p",
        "%b %d %Y %I:%M %p",
        "%B %d %Y %I:%M:%S %p",
        "%B %d %Y %I:%M %p",
        "%d %b %Y %I:%M:%S %p",
        "%d %b %Y %I:%M %p",
        "%d %B %Y %I:%M:%S %p",
        "%d %B %Y %I:%M %p",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(plain, fmt)
            return _to_tz(dt, timezone_name)
        except Exception:
            continue
    return None


def _extract_time_candidates(text: str, timezone_name: str = "Asia/Singapore") -> list[datetime]:
    s = str(text or "")
    regs = [
        re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?\b", re.I),
        re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}[ T]\d{1,2}:\d{2}(?::\d{2})?\b", re.I),
        re.compile(
            r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?\s+\d{1,2},?\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)?\b",
            re.I,
        ),
        re.compile(
            r"\b\d{1,2}\s+(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\.?,?\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)?\b",
            re.I,
        ),
        re.compile(r"\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}[点时:]\d{1,2}(?:分)?(?::\d{1,2})?(?:秒)?"),
        re.compile(r"\d{4}年\d{1,2}月\d{1,2}日"),
    ]
    rows: list[tuple[int, str]] = []
    for re_obj in regs:
        for m in re_obj.finditer(s):
            rows.append((int(m.start()), str(m.group(0) or "")))
    rows.sort(key=lambda x: x[0])

    out: list[datetime] = []
    seen: set[int] = set()
    for _idx, txt in rows:
        dt = _parse_user_datetime(txt, timezone_name)
        if dt is None:
            continue
        stamp = int(dt.timestamp())
        if stamp in seen:
            continue
        seen.add(stamp)
        out.append(dt)
        if len(out) >= 6:
            break
    return out


def _fmt_local_iso(dt: datetime, timezone_name: str = "Asia/Singapore") -> str:
    local = _to_tz(dt, timezone_name)
    return local.replace(tzinfo=None).isoformat(timespec="seconds")


def _window_from_points(points: list[datetime]) -> tuple[str, str]:
    if len(points) >= 2:
        a = points[0]
        b = points[1]
        if b < a:
            a, b = b, a
        return _fmt_local_iso(a), _fmt_local_iso(b)
    if len(points) == 1:
        end = points[0]
        start = end - timedelta(minutes=30)
        return _fmt_local_iso(start), _fmt_local_iso(end)
    return "", ""


def _window_from_relative_text(text: str, timezone_name: str = "Asia/Singapore") -> tuple[str, str]:
    raw = str(text or "")
    if not raw:
        return "", ""
    tz_name = str(timezone_name or "Asia/Singapore").strip() or "Asia/Singapore"
    now = _to_tz(datetime.now(), tz_name)
    low = raw.casefold()

    rel = re.search(r"(?:过去|最近|last)\s*(\d+)\s*(分钟|小时|天|minutes?|hours?|days?)", low, flags=re.I)
    if rel:
        n = max(1, int(rel.group(1) or 1))
        unit = str(rel.group(2) or "").lower()
        delta = timedelta(minutes=n)
        if "hour" in unit or "小" in unit:
            delta = timedelta(hours=n)
        elif "day" in unit or "天" in unit:
            delta = timedelta(days=n)
        return _fmt_local_iso(now - delta, tz_name), _fmt_local_iso(now, tz_name)

    if re.search(r"(昨天|昨日|yesterday)\s*(?:到|至|~|～|-|—|–|to)\s*(今天|今日|现在|当前|now|today)", raw, flags=re.I) or (
        re.search(r"(昨天|昨日|yesterday)", raw, flags=re.I)
        and re.search(r"(今天|今日|today|now|现在|当前)", raw, flags=re.I)
    ):
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return _fmt_local_iso(start, tz_name), _fmt_local_iso(now, tz_name)

    if re.search(r"(今天|今日|today)\s*(?:到|至|~|～|-|—|–|to)\s*(现在|当前|now)", raw, flags=re.I):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return _fmt_local_iso(start, tz_name), _fmt_local_iso(now, tz_name)

    if re.search(r"(前天|day\s*before\s*yesterday)", raw, flags=re.I):
        start = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=0)
        return _fmt_local_iso(start, tz_name), _fmt_local_iso(end, tz_name)

    if re.search(r"(昨天|昨日|yesterday)", raw, flags=re.I):
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=0)
        return _fmt_local_iso(start, tz_name), _fmt_local_iso(end, tz_name)

    if re.search(r"(今天|今日|today)", raw, flags=re.I):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return _fmt_local_iso(start, tz_name), _fmt_local_iso(now, tz_name)

    return "", ""


def _ensure_session_fault_window(body: dict[str, Any], default_hours: int = 24) -> dict[str, Any]:
    row = dict(body or {})
    fw = dict(row.get("fault_window") or {}) if isinstance(row.get("fault_window"), dict) else {}
    timezone_name = str(
        fw.get("timezone")
        or row.get("timezone")
        or "Asia/Singapore"
    ).strip() or "Asia/Singapore"
    start_raw = str(fw.get("start_at") or row.get("start_at") or "").strip()
    end_raw = str(fw.get("end_at") or row.get("end_at") or "").strip()
    if start_raw and end_raw:
        fw["start_at"] = start_raw
        fw["end_at"] = end_raw
        fw["timezone"] = timezone_name
        row["fault_window"] = fw
        return row

    now = _to_tz(datetime.now(), timezone_name)
    hours = max(1, int(default_hours or 24))
    default_start = now - timedelta(hours=hours)
    fw["start_at"] = start_raw or _fmt_local_iso(default_start, timezone_name)
    fw["end_at"] = end_raw or _fmt_local_iso(now, timezone_name)
    fw["timezone"] = timezone_name
    row["fault_window"] = fw
    return row


def _heuristic_intent_parse(text: str, timezone_name: str = "Asia/Singapore") -> dict[str, Any]:
    raw = _normalize_dialog_text(text)
    ip = _extract_first_ipv4(raw)
    points = _extract_time_candidates(raw, timezone_name=timezone_name)
    start_at, end_at = _window_from_points(points)
    rel_window = False
    if not start_at or not end_at:
        rel_start, rel_end = _window_from_relative_text(raw, timezone_name=timezone_name)
        if rel_start and rel_end:
            start_at, end_at = rel_start, rel_end
            rel_window = True
    focus_goals = _guess_focus_goals(raw)
    question = _guess_question(raw)
    time_conf = 0.0
    if len(points) >= 2:
        time_conf = 0.92
    elif len(points) == 1:
        time_conf = 0.72
    elif rel_window:
        time_conf = 0.84
    confidence = {
        "device_ip": 0.98 if ip else 0.0,
        "time_window": time_conf,
        "question": 0.82 if question else 0.0,
        "focus_goals": 0.74 if focus_goals else 0.28,
    }
    return {
        "device_ip": ip,
        "fault_start": start_at,
        "fault_end": end_at,
        "timezone": str(timezone_name or "Asia/Singapore"),
        "question": question,
        "focus_goals": focus_goals,
        "follow_up": str(text or "").strip(),
        "confidence": confidence,
    }


async def _llm_intent_parse(
    text: str,
    timezone_name: str = "Asia/Singapore",
    llm_primary: dict[str, str] | None = None,
    llm_failover: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str, str]:
    llm = dict(llm_primary or _build_llm_input())
    base_prompt = llm.get("task_prompt_text", "")
    llm["task_prompt_text"] = (
        base_prompt
        + "\n\n[NetDiag Intent Parser]"
        + "\n你只做自然语言结构化提取，不做诊断，不输出命令。"
        + "\n必须只输出一个 JSON 对象，不要 markdown 包裹。"
        + '\nJSON schema: {"device_ip":"","fault_start":"","fault_end":"","timezone":"Asia/Singapore","question":"","focus_goals":[],"confidence":{"device_ip":0.0,"time_window":0.0,"question":0.0,"focus_goals":0.0},"notes":""}'
        + "\n时间统一输出为 YYYY-MM-DDTHH:MM:SS。"
        + "\n如果只识别到一个故障时间点，可只填一个时间字段并在 notes 说明。"
        + "\n无法确定的字段留空。"
    )
    report = f"timezone_default={timezone_name}\n\nUserInput:\n{_normalize_dialog_text(text)}"
    raw_text, err_text = await _run_llm_text_with_retry(
        llm_input=llm,
        report_text=report,
        timeout_sec=12,
        attempts=1,
        shrink_on_retry=False,
        strict_text_check=False,
        failover_llm_input=llm_failover,
        failover_attempts=1,
    )
    parsed = _extract_json_obj(raw_text)
    return parsed, raw_text, err_text


def _pick_first_value(data: dict[str, Any], keys: list[str]) -> str:
    for k in keys:
        if k in data:
            v = str(data.get(k) or "").strip()
            if v:
                return v
    return ""


def _normalize_intent_parse_result(
    *,
    text: str,
    timezone_name: str,
    heuristic: dict[str, Any],
    llm_row: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    out = dict(heuristic or {})
    source = "heuristic"
    row = dict(llm_row or {})
    if not row:
        return out, source

    source = "llm+heuristic"
    if isinstance(row.get("fault_window"), dict):
        fw = dict(row.get("fault_window") or {})
        for key in ("fault_start", "start_at", "start", "fault_time_start"):
            if key in fw and key not in row:
                row[key] = fw.get(key)
        for key in ("fault_end", "end_at", "end", "fault_time_end"):
            if key in fw and key not in row:
                row[key] = fw.get(key)
        if "timezone" in fw and "timezone" not in row:
            row["timezone"] = fw.get("timezone")

    llm_ip = _pick_first_value(row, ["device_ip", "ip", "device", "host", "device_host"])
    if _valid_ipv4(llm_ip):
        out["device_ip"] = llm_ip

    llm_question = _pick_first_value(row, ["question", "problem", "symptom", "issue"])
    if llm_question:
        out["question"] = llm_question[:500]

    tz = _pick_first_value(row, ["timezone", "tz"])
    if tz:
        out["timezone"] = tz
    else:
        out["timezone"] = str(timezone_name or out.get("timezone") or "Asia/Singapore")

    goals_from_llm: list[str] = []
    for key in ("focus_goals", "focus", "goals", "direction"):
        if key in row:
            goals_from_llm.extend(_split_goal_text(row.get(key)))
    if goals_from_llm:
        out["focus_goals"] = _dedupe_texts([*goals_from_llm, *_split_goal_text(out.get("focus_goals", []))], limit=8)

    start_raw = _pick_first_value(row, ["fault_start", "start_at", "start", "fault_time_start"])
    end_raw = _pick_first_value(row, ["fault_end", "end_at", "end", "fault_time_end"])
    one_raw = _pick_first_value(row, ["fault_time", "time_point", "event_time"])
    dt_start = _parse_user_datetime(start_raw, out.get("timezone") or timezone_name) if start_raw else None
    dt_end = _parse_user_datetime(end_raw, out.get("timezone") or timezone_name) if end_raw else None
    if dt_start is None and dt_end is None and one_raw:
        dt_one = _parse_user_datetime(one_raw, out.get("timezone") or timezone_name)
        if dt_one is not None:
            dt_start = dt_one - timedelta(minutes=30)
            dt_end = dt_one
    elif dt_start is not None and dt_end is None:
        dt_end = dt_start
        dt_start = dt_start - timedelta(minutes=30)
    elif dt_end is not None and dt_start is None:
        dt_start = dt_end - timedelta(minutes=30)
    if dt_start is not None and dt_end is not None and dt_end < dt_start:
        dt_start, dt_end = dt_end, dt_start
    if dt_start is not None:
        out["fault_start"] = _fmt_local_iso(dt_start, out.get("timezone") or timezone_name)
    if dt_end is not None:
        out["fault_end"] = _fmt_local_iso(dt_end, out.get("timezone") or timezone_name)

    llm_conf = row.get("confidence", {})
    conf_out = dict(out.get("confidence") or {})
    if isinstance(llm_conf, dict):
        for k in ("device_ip", "time_window", "question", "focus_goals"):
            if k in llm_conf:
                conf_out[k] = _clamp_confidence(llm_conf.get(k), conf_out.get(k, 0.0))
    out["confidence"] = {
        "device_ip": _clamp_confidence(conf_out.get("device_ip"), 0.0),
        "time_window": _clamp_confidence(conf_out.get("time_window"), 0.0),
        "question": _clamp_confidence(conf_out.get("question"), 0.0),
        "focus_goals": _clamp_confidence(conf_out.get("focus_goals"), 0.0),
    }
    if not str(out.get("question") or "").strip():
        out["question"] = _guess_question(text)
    out["focus_goals"] = _dedupe_texts(_split_goal_text(out.get("focus_goals", [])), limit=8)
    out["follow_up"] = str(text or "").strip()
    return out, source


def _resolve_reference_policy(payload: dict[str, Any] | None) -> dict[str, bool]:
    defaults = {
        "enabled": True,
        "known_issues": True,
        "case_library": True,
        "sop_library": True,
        "command_library": True,
    }
    raw = (payload or {}).get("reference_policy")
    if not isinstance(raw, dict):
        return dict(defaults)
    enabled = _to_bool(raw.get("enabled", True), True)
    resolved = {"enabled": enabled}
    for k in ("known_issues", "case_library", "sop_library", "command_library"):
        resolved[k] = _to_bool(raw.get(k, True), True) if enabled else False
    return resolved


def _split_text_chunks(text: str, chunk_size: int = 1400, overlap: int = 120) -> list[str]:
    raw = str(text or "")
    if not raw:
        return []
    size = max(400, min(int(chunk_size), 8000))
    ov = max(0, min(int(overlap), size // 3))
    if len(raw) <= size:
        return [raw]
    out: list[str] = []
    start = 0
    while start < len(raw):
        end = min(len(raw), start + size)
        seg = raw[start:end]
        if seg:
            out.append(seg)
        if end >= len(raw):
            break
        start = max(end - ov, start + 1)
    return out


def _chunk_signal_score(text: str) -> float:
    low = str(text or "").lower()
    score = 0.0
    keywords = (
        "error",
        "failed",
        "fail",
        "down",
        "drop",
        "deny",
        "timeout",
        "reset",
        "flap",
        "crc",
        "discard",
        "alarm",
        "exception",
    )
    for kw in keywords:
        if kw in low:
            score += 1.6
    score += min(3.0, float(low.count("%")))
    score += min(2.0, float(low.count("critical")))
    score += min(2.0, float(low.count("warning")))
    return round(score, 3)


def _build_chunked_execution_context(
    exec_records: list[dict[str, Any]],
    *,
    max_chunks: int = 6,
    chunk_size: int = 1400,
    overlap: int = 120,
) -> tuple[str, dict[str, Any]]:
    recs = [x for x in (exec_records or []) if isinstance(x, dict)]
    max_keep = max(1, min(int(max_chunks), 24))
    candidates: list[dict[str, Any]] = []
    per_cmd_best: dict[str, dict[str, Any]] = {}
    total_chunks = 0
    for row in recs:
        device_id = str(row.get("device_id") or "-")
        command = str(row.get("command") or "-")
        status = str(row.get("status") or "-")
        output_text = str(row.get("output_text") or "")
        chunks = _split_text_chunks(output_text, chunk_size=chunk_size, overlap=overlap)
        total_chunks += len(chunks)
        cmd_key = f"{device_id}|{command}"
        for idx, seg in enumerate(chunks, start=1):
            item = {
                "device_id": device_id,
                "command": command,
                "status": status,
                "chunk_idx": idx,
                "chunk_total": len(chunks),
                "score": _chunk_signal_score(seg),
                "text": seg,
                "cmd_key": cmd_key,
            }
            candidates.append(item)
            prev = per_cmd_best.get(cmd_key)
            if prev is None or float(item["score"]) > float(prev["score"]):
                per_cmd_best[cmd_key] = item

    if not candidates:
        return "", {"enabled": True, "selected_chunks": 0, "total_chunks": 0, "chunk_size": chunk_size, "max_chunks": max_keep}

    chosen: list[dict[str, Any]] = []
    # Keep one strongest chunk per command first, then fill globally by score.
    for item in per_cmd_best.values():
        chosen.append(item)
        if len(chosen) >= max_keep:
            break
    if len(chosen) < max_keep:
        chosen_keys = {id(x) for x in chosen}
        rest = sorted(candidates, key=lambda x: float(x.get("score", 0.0)), reverse=True)
        for item in rest:
            if id(item) in chosen_keys:
                continue
            chosen.append(item)
            if len(chosen) >= max_keep:
                break
    chosen = sorted(chosen[:max_keep], key=lambda x: (x.get("device_id", ""), x.get("command", ""), int(x.get("chunk_idx", 0))))

    lines: list[str] = []
    for no, item in enumerate(chosen, start=1):
        cmd_show = str(item.get("command") or "-")
        if len(cmd_show) > 120:
            cmd_show = cmd_show[:117] + "..."
        head = (
            f"[{no}] device={item.get('device_id')} status={item.get('status')} "
            f"chunk={item.get('chunk_idx')}/{item.get('chunk_total')} score={item.get('score')} "
            f"command={cmd_show}"
        )
        lines.append(f"{head}\n{item.get('text')}")

    return (
        "\n\n----- chunk split -----\n\n".join(lines),
        {
            "enabled": True,
            "selected_chunks": len(chosen),
            "total_chunks": total_chunks,
            "chunk_size": max(400, min(int(chunk_size), 8000)),
            "overlap": max(0, min(int(overlap), max(400, min(int(chunk_size), 8000)) // 3)),
            "max_chunks": max_keep,
        },
    )


def _stale_seconds(updated_at: Any) -> float:
    text = str(updated_at or "").strip()
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace(" ", "T").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return max(0.0, (datetime.now() - dt).total_seconds())
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return 0.0


def _has_baseline_success(session: Any) -> bool:
    items = list(getattr(session, "time_calibration", []) or [])
    for row in items:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").strip().lower() == "success":
            return True
    return False


def _latest_round_or_none(session: Any) -> Any:
    rounds = list(getattr(session, "rounds", []) or [])
    if not rounds:
        return None
    return rounds[-1]


def _round_value(round_obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(round_obj, dict):
        return round_obj.get(key, default)
    return getattr(round_obj, key, default)


def _round_validation_task(round_obj: Any) -> dict[str, Any]:
    if round_obj is None:
        return {}
    evidence = _round_value(round_obj, "evidence_overview", {}) or {}
    persisted = _normalize_validation_task((evidence or {}).get("validation_task"))
    target_probe = _normalize_target_probe(_round_value(round_obj, "target_probe", {}) or {})
    next_target_probe = _normalize_target_probe((evidence or {}).get("next_target_probe"))
    review = dict((evidence or {}).get("expected_signal_review") or {})
    focus_review = _round_value(round_obj, "focus_review", {}) or {}
    stop = _round_value(round_obj, "stop_decision", {}) or {}
    if persisted:
        persisted = _merge_validation_task_context(
            persisted,
            current_probe=target_probe,
            next_probe=next_target_probe,
            expected_signal_review=review,
            focus_review=focus_review,
            stop_decision=stop,
        )
        if persisted.get("next_probe"):
            return persisted
        if not next_target_probe and not _round_ready_to_conclude(round_obj):
            hypotheses = list(_round_value(round_obj, "hypotheses", []) or [])
            next_target_probe = _derive_next_target_probe(
                target_probe=(persisted.get("current_probe") or target_probe),
                hypotheses=hypotheses,
                stop_decision=stop,
                focus_review=focus_review,
                expected_signal_review=review,
            )
        return _merge_validation_task_context(
            {
                **persisted,
                "next_probe": (next_target_probe or persisted.get("current_probe") or {}),
            },
            current_probe=target_probe,
            next_probe=next_target_probe,
            expected_signal_review=review,
            focus_review=focus_review,
            stop_decision=stop,
        )
    if not next_target_probe and not _round_ready_to_conclude(round_obj):
        hypotheses = list(_round_value(round_obj, "hypotheses", []) or [])
        next_target_probe = _derive_next_target_probe(
            target_probe=target_probe,
            hypotheses=hypotheses,
            stop_decision=stop,
            focus_review=focus_review,
            expected_signal_review=review,
        )
    return _merge_validation_task_context(
        _build_validation_task(
            target_probe=target_probe,
            next_target_probe=next_target_probe,
            expected_signal_review=review,
            focus_review=focus_review,
            stop_decision=stop,
        ),
        current_probe=target_probe,
        next_probe=next_target_probe,
        expected_signal_review=review,
        focus_review=focus_review,
        stop_decision=stop,
    )


def _round_dump_with_validation_task(round_obj: Any) -> dict[str, Any]:
    if hasattr(round_obj, "model_dump"):
        row = round_obj.model_dump()
    elif isinstance(round_obj, dict):
        row = dict(round_obj or {})
    elif hasattr(round_obj, "__dict__"):
        row = dict(vars(round_obj) or {})
    else:
        row = {}
    evidence = dict(row.get("evidence_overview") or {})
    task = _round_validation_task(round_obj)
    next_probe = _validation_task_to_target_probe(task) if not _round_ready_to_conclude(round_obj) else {}
    evidence["validation_task"] = task
    evidence["next_target_probe"] = next_probe
    row["evidence_overview"] = evidence
    return row


def _session_next_target_probe(session: Any) -> dict[str, Any]:
    rnd = _latest_round_or_none(session)
    if rnd is None:
        return {}
    if _round_ready_to_conclude(rnd):
        return {}
    return _validation_task_to_target_probe(_round_validation_task(rnd))


def _session_validation_task(session: Any) -> dict[str, Any]:
    rnd = _latest_round_or_none(session)
    if rnd is None:
        return {}
    return _round_validation_task(rnd)


def _session_continue_probe_payload(session: Any) -> dict[str, Any]:
    task = _session_validation_task(session)
    probe = _validation_task_to_target_probe(task)
    return {
        "target_probe": probe,
        "validation_task": task,
    }


def _round_ready_to_conclude(round_obj: Any) -> bool:
    stop = _round_value(round_obj, "stop_decision", {}) or {}
    if bool(stop.get("recommend_conclude")):
        return True
    next_action = str(stop.get("next_action") or "").strip().lower()
    if next_action in {"conclude", "conclude_with_verification", "verified_conclude"}:
        return True
    return False


def _ui_action_name(action: str) -> str:
    raw = str(action or "").strip().lower()
    if raw == "next_round":
        return "continue_probe"
    return raw


def _canonical_workflow_action(action: str) -> str:
    raw = str(action or "").strip().lower()
    if raw == "next_round":
        return "continue_probe"
    return raw


def _ui_status_name(status: str) -> str:
    raw = str(status or "").strip().lower()
    mapping = {
        "need_next_round": "ready_for_next_probe",
        "aborted": "stopped",
    }
    return mapping.get(raw, raw)


def _session_ui_status(session: Any, next_info: dict[str, Any] | None = None) -> str:
    if session is None:
        return ""
    raw_status = str(getattr(session, "status", "") or "").strip().lower()
    nxt = dict(next_info or {})
    action = _canonical_workflow_action(str(nxt.get("raw_action") or nxt.get("action") or "").strip().lower())
    if action == "continue_probe":
        return "ready_for_next_probe"
    return _ui_status_name(raw_status)


def _ready_for_next_probe_status() -> str:
    return "ready_for_next_probe"


def _next_workflow_action(session: Any) -> dict[str, Any]:
    if session is None:
        action = "create"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "session missing", "round_no": 0, "target_probe": {}}

    status = str(getattr(session, "status", "") or "").strip().lower()
    if status in {"calibrating_time", "planning", "executing", "analyzing"}:
        action = "wait"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": f"session status={status}", "round_no": 0, "target_probe": {}}
    if status == "concluded":
        action = "none"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "session concluded", "round_no": 0, "target_probe": {}}
    if not _has_baseline_success(session):
        action = "baseline"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "baseline not collected", "round_no": 0, "target_probe": {}}

    rnd = _latest_round_or_none(session)
    if rnd is None:
        action = "plan"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "no round yet", "round_no": 1, "target_probe": {}}

    rno = int(getattr(rnd, "round_no", 0) or 0)
    rstatus = str(getattr(rnd, "status", "") or "").strip().lower()
    approved = bool(getattr(rnd, "approved", False))
    executions = list(getattr(rnd, "executions", []) or [])
    has_exec = len(executions) > 0
    analysis_text = str(getattr(rnd, "analysis_result", "") or "").strip()
    has_analysis = bool(analysis_text)
    stop = getattr(rnd, "stop_decision", {}) or {}
    recommend_conclude = _round_ready_to_conclude(rnd)

    if rstatus == "planning":
        action = "wait"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "round planning", "round_no": rno, "target_probe": {}}
    if rstatus == "waiting_approval":
        if not approved:
            action = "approve"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "round not approved", "round_no": rno, "target_probe": {}}
        if not has_exec:
            action = "execute"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "approved and no outputs", "round_no": rno, "target_probe": {}}
        if not has_analysis:
            action = "analyze"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "outputs ready but no analysis", "round_no": rno, "target_probe": {}}
    if rstatus == "executing":
        action = "wait"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "round executing", "round_no": rno, "target_probe": {}}
    if rstatus == "analyzing":
        if status == "analyzing":
            action = "wait"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "round analyzing", "round_no": rno, "target_probe": {}}
        if has_exec and not has_analysis:
            action = "analyze"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "outputs ready but no analysis", "round_no": rno, "target_probe": {}}
    if rstatus in {"completed", "failed"} or has_analysis:
        if recommend_conclude and status != "concluded":
            action = "conclude"
            return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "stop decision recommends conclude", "round_no": rno, "target_probe": {}}
        action = "continue_probe"
        payload = _session_continue_probe_payload(session)
        return {
            "action": _ui_action_name(action),
            "raw_action": action,
            "legacy_action": "next_round",
            "ui_action": _ui_action_name(action),
            "reason": "round finished, continue",
            "round_no": rno,
            "target_probe": payload.get("target_probe") or {},
            "validation_task": payload.get("validation_task") or {},
        }
    if has_exec and not has_analysis:
        action = "analyze"
        return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "outputs ready", "round_no": rno, "target_probe": {}}
    action = "plan"
    return {"action": _ui_action_name(action), "raw_action": action, "ui_action": _ui_action_name(action), "reason": "default fallback", "round_no": max(1, rno + 1), "target_probe": {}}


def _assert_action_allowed(session: Any, allowed_actions: set[str], endpoint_name: str) -> dict[str, Any]:
    nxt = _next_workflow_action(session)
    action = _canonical_workflow_action(str(nxt.get("raw_action") or nxt.get("action") or "").strip().lower())
    allowed = {_canonical_workflow_action(str(x).strip().lower()) for x in (allowed_actions or set())}
    if action in allowed:
        return nxt
    if action == "wait":
        return nxt
    raise HTTPException(
        status_code=409,
        detail=(
            f"{endpoint_name} blocked: next action should be '{action or '-'}' "
            f"(allowed={sorted(list(allowed_actions))})."
        ),
    )


def _session_dump_with_next_action(session: Any) -> dict[str, Any]:
    row = session.model_dump() if hasattr(session, "model_dump") else dict(session or {})
    continue_payload = _session_continue_probe_payload(session)
    rounds = list(_round_value(session, "rounds", []) or [])
    row["rounds"] = [_round_dump_with_validation_task(rnd) for rnd in rounds]
    next_info = _next_workflow_action(session)
    row["ui_status"] = _session_ui_status(session, next_info)
    row["next_target_probe"] = continue_payload.get("target_probe") or {}
    row["validation_task"] = continue_payload.get("validation_task") or {}
    row["next_action"] = next_info
    return row


def _round_response_payload(round_obj: Any) -> dict[str, Any] | None:
    if round_obj is None:
        return None
    return _round_dump_with_validation_task(round_obj)


def _normalize_session_status_for_ui(manager: Any, session: Any) -> Any:
    """
    Backward-compatible normalization:
    old sessions may stay at `planning` right after baseline (no rounds yet).
    Treat this as `ready_for_next_probe` to avoid UI deadlock.
    """
    if session is None:
        return None
    try:
        status = str(getattr(session, "status", "") or "").strip().lower()
        rounds = list(getattr(session, "rounds", []) or [])
        baseline = list(getattr(session, "time_calibration", []) or [])
        stale_sec = _stale_seconds(getattr(session, "updated_at", ""))
        sid = str(getattr(session, "session_id", "") or "")

        # Legacy stale planning state: only recover when planning is old enough.
        if (
            status == "planning"
            and baseline
            and not rounds
            and stale_sec >= STALE_PLANNING_RECOVER_SEC
            and sid
        ):
            fixed = manager.set_status(sid, _ready_for_next_probe_status())
            if fixed is not None:
                return fixed

        if status == "planning" and rounds and stale_sec >= STALE_PLANNING_RECOVER_SEC and sid:
            last = rounds[-1]
            rstatus = str(getattr(last, "status", "") or "").strip().lower()
            if rstatus in {"waiting_approval", "executing"}:
                fixed = manager.set_status(sid, rstatus)
                if fixed is not None:
                    return fixed
            if rstatus == "analyzing":
                fixed = manager.set_status(sid, _ready_for_next_probe_status())
                if fixed is not None:
                    return fixed
            if rstatus in {"completed", "failed"}:
                fixed = manager.set_status(sid, _ready_for_next_probe_status())
                if fixed is not None:
                    return fixed

        # Legacy stuck state: approved round was marked executing before execute endpoint was called.
        if status == "executing" and rounds:
            last = rounds[-1]
            rstatus = str(getattr(last, "status", "") or "").strip().lower()
            approved = bool(getattr(last, "approved", False))
            executions = list(getattr(last, "executions", []) or [])
            if rstatus == "executing" and approved and not executions and stale_sec >= STALE_EXECUTING_RECOVER_SEC:
                rno = int(getattr(last, "round_no", 0) or 0)
                if sid and rno > 0:
                    manager.approve_round(sid, rno, approved=True)
                    fixed = manager.set_status(sid, "waiting_approval")
                    if fixed is not None:
                        return fixed

        # Stuck analyze fallback:
        # 1) session=analyzing and stale too long
        # 2) session already left analyzing, but round still analyzing with empty result
        if rounds:
            last = rounds[-1]
            rstatus = str(getattr(last, "status", "") or "").strip().lower()
            analysis_text = str(getattr(last, "analysis_result", "") or "").strip()
            if rstatus == "analyzing" and not analysis_text:
                soft_recover = (status != "analyzing") and (stale_sec >= STALE_ANALYZING_RECOVER_SEC)
                hard_recover = (status == "analyzing") and (stale_sec >= max(120, STALE_ANALYZING_RECOVER_SEC * 2))
                if soft_recover or hard_recover:
                    rno = int(getattr(last, "round_no", 0) or 0)
                    if sid and rno > 0:
                        reason = "orphan_round_analyzing" if soft_recover else "analyze_timeout"
                        fail_msg = (
                            f"Analyze step {reason} was auto-recovered. "
                            "Please retry AI analyze."
                        )
                        try:
                            manager.set_round_analysis(
                                sid,
                                rno,
                                analysis_result=fail_msg,
                                status="failed",
                            )
                        except Exception:
                            pass
                        manager.set_last_error(sid, fail_msg)
                        fixed = manager.set_status(sid, _ready_for_next_probe_status())
                        if fixed is not None:
                            return fixed
    except Exception:
        return session
    return session


def _parse_time_to_epoch(value: Any, timezone_name: str) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("time value is required")
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(str(timezone_name or "UTC")))
    return int(dt.astimezone(timezone.utc).timestamp())


def _clock_to_iso(clock: Any, timezone_name: str = "UTC") -> str:
    ts = _safe_int(clock, 0)
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        local = dt_utc.astimezone(ZoneInfo(str(timezone_name or "UTC")))
        return local.isoformat()
    except Exception:
        return dt_utc.isoformat()


def _zabbix_cfg_from_payload(base_cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_cfg or {})
    row = payload or {}
    for key in ("base_url", "username", "password", "api_token", "ca_bundle"):
        if key in row:
            merged[key] = str(row.get(key) or "").strip() if key != "password" else str(row.get(key) or "")
    if "verify_ssl" in row:
        merged["verify_ssl"] = bool(row.get("verify_ssl"))
    if "request_timeout_sec" in row:
        merged["request_timeout_sec"] = max(5, min(120, _safe_int(row.get("request_timeout_sec"), 30)))
    return merged


def _with_session_connection_defaults(raw_payload: dict[str, Any], conn_cfg: dict[str, Any]) -> dict[str, Any]:
    body = dict(raw_payload or {})
    devices = body.get("devices")
    if not isinstance(devices, list):
        return body
    mode_default = str((conn_cfg or {}).get("smc_jump_mode") or "smc").strip().lower()
    mode_default = "smc" if mode_default == "smc" else "direct"
    host_default = str((conn_cfg or {}).get("smc_jump_host") or "").strip()
    port_default = max(1, min(65535, _safe_int((conn_cfg or {}).get("smc_jump_port"), 22)))
    cmd_default = str((conn_cfg or {}).get("smc_command") or "").strip()
    if not cmd_default:
        cmd_default = "smc server toc {jump_host}"

    patched: list[dict[str, Any]] = []
    for item in devices:
        if not isinstance(item, dict):
            patched.append(item)
            continue
        row = dict(item)
        mode_raw = str(row.get("jump_mode") or "").strip().lower()
        if not mode_raw:
            row["jump_mode"] = mode_default
        if str(row.get("jump_mode") or "").strip().lower() == "smc":
            if not str(row.get("jump_host") or "").strip() and host_default:
                row["jump_host"] = host_default
            if not row.get("jump_port"):
                row["jump_port"] = port_default
            if not str(row.get("smc_command") or "").strip():
                row["smc_command"] = cmd_default
        patched.append(row)
    body["devices"] = patched
    return body


def _config_command_for_profile(profile: str) -> str:
    p = str(profile or "unknown").strip().lower()
    if p == "huawei_vrp":
        return "display current-configuration"
    if p == "paloalto_panos":
        return "show config running"
    return "show running-config"


def _config_diff_signals(diff_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    low = str(diff_text or "").lower()
    if not low.strip():
        return [], []
    signals: list[dict[str, Any]] = []
    summary: list[str] = []

    def add(domain: str, signal: str, weight: float, detail: str) -> None:
        signals.append(
            {
                "device_id": "*",
                "vendor": "config",
                "command": "config.diff",
                "domain": domain,
                "polarity": "positive",
                "signal": signal,
                "weight": round(max(0.02, min(0.28, float(weight))), 4),
                "detail": detail[:280],
            }
        )
        summary.append(f"{domain}: {signal} {detail}")

    if any(x in low for x in ("router bgp", "neighbor", "address-family", "ospf", "route-policy")):
        add("routing", "config_routing_changed", 0.14, "routing stanza changed")
    if any(x in low for x in ("interface", "port-channel", "mtu", "speed", "duplex", "switchport")):
        add("link", "config_interface_changed", 0.12, "interface stanza changed")
    if any(x in low for x in ("ntp", "clock timezone", "timezone", "time-zone")):
        add("clock", "config_clock_changed", 0.10, "time/ntp related config changed")
    if any(x in low for x in ("access-list", "acl", "security-policy", "policy", "zone", "firewall", "nat ")):
        add("firewall", "config_policy_changed", 0.12, "policy/security stanza changed")
    if any(x in low for x in ("cpu-threshold", "process", "resource", "memory")):
        add("resource", "config_resource_control_changed", 0.08, "resource control stanza changed")
    return signals, summary[:20]


def _metric_domain(item_row: dict[str, Any]) -> str:
    text = f"{item_row.get('key_', '')} {item_row.get('name', '')}".lower()
    if any(x in text for x in ("cpu", "memory", "load", "util")):
        return "resource"
    if any(x in text for x in ("bgp", "ospf", "route", "neighbor", "session_up")):
        return "routing"
    if any(x in text for x in ("firewall", "policy", "session", "threat", "deny")):
        return "firewall"
    if any(x in text for x in ("clock", "ntp", "time", "drift", "offset")):
        return "clock"
    return "link"


def _build_zabbix_signals(
    *,
    items: list[dict[str, Any]],
    points: list[dict[str, Any]],
    use_trend: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_item: dict[str, list[float]] = {}
    for p in points:
        if not isinstance(p, dict):
            continue
        iid = str(p.get("itemid") or "").strip()
        if not iid:
            continue
        val_raw = p.get("value_avg") if bool(use_trend) else p.get("value")
        val = _safe_float(val_raw, float("nan"))
        if val != val:  # NaN
            continue
        by_item.setdefault(iid, []).append(val)

    item_map = {str(x.get("itemid") or "").strip(): x for x in items if isinstance(x, dict)}
    signals: list[dict[str, Any]] = []
    summary: list[str] = []

    for iid, values in by_item.items():
        if not values:
            continue
        meta = item_map.get(iid, {"itemid": iid, "name": iid, "key_": iid})
        domain = _metric_domain(meta)
        avg = sum(values) / len(values)
        peak = max(values)
        floor = min(values)
        key = str(meta.get("key_") or "").lower()
        name = str(meta.get("name") or "")
        metric_name = f"{name} ({key})" if key else name

        def _append(polarity: str, signal: str, weight: float, detail: str) -> None:
            signals.append(
                {
                    "device_id": "*",
                    "vendor": "zabbix",
                    "command": "zabbix.history",
                    "domain": domain,
                    "polarity": polarity,
                    "signal": signal,
                    "weight": round(max(0.02, min(0.30, float(weight))), 4),
                    "detail": detail[:280],
                }
            )

        if domain == "resource":
            if peak >= 85:
                _append("positive", "zabbix_high_resource_util", 0.18 + min(0.12, (peak - 85) * 0.01), f"{metric_name} peak={peak:.2f} avg={avg:.2f}")
            elif peak <= 45:
                _append("negative", "zabbix_resource_healthy", 0.10, f"{metric_name} peak={peak:.2f} avg={avg:.2f}")
        elif domain == "link":
            if any(x in key for x in ("loss", "drop")) and avg >= 1.0:
                _append("positive", "zabbix_packet_loss_indicator", 0.16 + min(0.10, avg * 0.02), f"{metric_name} avg={avg:.2f}")
            elif any(x in key for x in ("error", "crc", "discard")) and peak > 0:
                _append("positive", "zabbix_interface_error_indicator", 0.14 + min(0.10, peak * 0.01), f"{metric_name} peak={peak:.2f}")
        elif domain == "routing":
            if any(x in key for x in ("bgp", "ospf", "neighbor", "session")) and floor <= 0:
                _append("positive", "zabbix_routing_session_drop", 0.18, f"{metric_name} min={floor:.2f}")
            elif any(x in key for x in ("bgp", "ospf", "neighbor", "session")) and floor >= 1:
                _append("negative", "zabbix_routing_session_stable", 0.10, f"{metric_name} min={floor:.2f}")
        elif domain == "clock":
            if any(x in key for x in ("offset", "drift")) and peak >= 3:
                _append("positive", "zabbix_clock_offset_high", 0.16, f"{metric_name} peak={peak:.2f}")
            elif any(x in key for x in ("offset", "drift")) and peak < 1:
                _append("negative", "zabbix_clock_offset_low", 0.08, f"{metric_name} peak={peak:.2f}")
        elif domain == "firewall":
            if any(x in key for x in ("session", "deny", "drop")) and peak >= 80:
                _append("positive", "zabbix_firewall_pressure", 0.16, f"{metric_name} peak={peak:.2f}")

        summary.append(f"{domain}: {metric_name} avg={avg:.2f} min={floor:.2f} max={peak:.2f} points={len(values)}")

    summary.sort()
    return signals, summary[:30]


def _query_zabbix_history_data(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    tz = str(body.get("timezone") or "Asia/Singapore").strip() or "Asia/Singapore"
    try:
        time_from = _parse_time_to_epoch(body.get("start_at"), tz)
        time_till = _parse_time_to_epoch(body.get("end_at"), tz)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid time range: {exc}") from exc
    if time_till < time_from:
        raise HTTPException(status_code=400, detail="end_at must be >= start_at")

    store = _require_zabbix_store(request)
    base_cfg = store.get(masked=False)
    cfg_dict = _zabbix_cfg_from_payload(base_cfg, body.get("zabbix", {}) if isinstance(body.get("zabbix"), dict) else {})
    cfg = ZabbixConfig.from_dict(cfg_dict)
    try:
        cfg.validate()
        client = ZabbixClient(cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid zabbix config: {exc}") from exc

    hostids = [str(x).strip() for x in (body.get("hostids") or []) if str(x).strip()]
    hostid = str(body.get("hostid") or "").strip()
    if hostid:
        hostids.append(hostid)
    host_keyword = str(body.get("host") or "").strip()
    hosts: list[dict[str, Any]] = []
    if hostids:
        hosts = [{"hostid": x} for x in hostids]
    elif host_keyword:
        try:
            hosts = client.host_get(keyword=host_keyword, limit=max(1, min(_safe_int(body.get("host_limit"), 20), 200)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"host lookup failed: {exc}") from exc
        hostids = [str(x.get("hostid") or "").strip() for x in hosts if str(x.get("hostid") or "").strip()]
    else:
        raise HTTPException(status_code=400, detail="host or hostid is required")
    if not hostids:
        raise HTTPException(status_code=404, detail="no zabbix hosts matched")

    itemids = [str(x).strip() for x in (body.get("itemids") or []) if str(x).strip()]
    itemid = str(body.get("itemid") or "").strip()
    if itemid:
        itemids.append(itemid)
    key_filter = str(body.get("item_key") or "").strip()
    name_filter = str(body.get("item_name") or "").strip()
    value_type = body.get("value_type")
    items: list[dict[str, Any]] = []
    if itemids:
        items = [{"itemid": x} for x in itemids]
    else:
        try:
            items = client.item_get(
                hostids=hostids,
                key_filter=key_filter,
                name_filter=name_filter,
                value_type=(int(value_type) if value_type is not None else None),
                limit=max(1, min(_safe_int(body.get("item_limit"), 50), 500)),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"item lookup failed: {exc}") from exc
        itemids = [str(x.get("itemid") or "").strip() for x in items if str(x.get("itemid") or "").strip()]
    if not itemids:
        raise HTTPException(status_code=404, detail="no zabbix items matched")

    limit = max(1, min(_safe_int(body.get("limit"), 1000), 5000))
    window_sec = max(0, int(time_till) - int(time_from))
    use_trend = body.get("use_trend")
    if use_trend is None:
        use_trend = window_sec >= 86400 * 7
    source = "trend.get" if bool(use_trend) else "history.get"

    try:
        if bool(use_trend):
            rows = client.trend_get(itemids=itemids, time_from=time_from, time_till=time_till, limit=limit)
        else:
            vt = _safe_int(value_type, 0)
            rows = client.history_get(itemids=itemids, time_from=time_from, time_till=time_till, value_type=vt, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"history query failed: {exc}") from exc

    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clock = _safe_int(row.get("clock"), 0)
        point = {
            "clock": clock,
            "time": _clock_to_iso(clock, tz),
            "itemid": str(row.get("itemid") or ""),
        }
        if bool(use_trend):
            point["value_avg"] = row.get("value_avg")
            point["value_min"] = row.get("value_min")
            point["value_max"] = row.get("value_max")
            point["num"] = row.get("num")
        else:
            point["value"] = row.get("value")
            point["ns"] = row.get("ns")
        points.append(point)

    signals, signal_summary = _build_zabbix_signals(items=items, points=points, use_trend=bool(use_trend))
    return {
        "ok": True,
        "source": source,
        "window": {
            "start_at": _clock_to_iso(time_from, tz),
            "end_at": _clock_to_iso(time_till, tz),
            "timezone": tz,
            "duration_sec": window_sec,
        },
        "hosts": hosts[:20],
        "items": items[:50],
        "points": points,
        "points_count": len(points),
        "signals": signals,
        "signal_summary": signal_summary,
    }


@router.get("/netdiag", response_class=HTMLResponse)
async def netdiag_home(request: Request):
    return _no_cache(
        templates.TemplateResponse(
            "netdiag_home.html",
            {
                "request": request,
                "sessions": request.app.state.diag_session_manager.list_sessions(),
            },
        )
    )


@router.get("/netdiag/control", response_class=HTMLResponse)
async def netdiag_control(request: Request):
    return _no_cache(
        templates.TemplateResponse(
            "netdiag_control.html",
            {
                "request": request,
            },
        )
    )


@router.get("/netdiag/sessions", response_class=HTMLResponse)
async def netdiag_sessions_page(request: Request):
    return _no_cache(
        templates.TemplateResponse(
            "netdiag_sessions.html",
            {
                "request": request,
            },
        )
    )


@router.get("/netdiag/learning", response_class=HTMLResponse)
async def netdiag_learning(request: Request):
    store = _require_learning_store(request)
    issue_store = _require_known_issue_store(request)
    case_store = _require_case_store(request)
    return _no_cache(
        templates.TemplateResponse(
            "netdiag_learning.html",
            {
                "request": request,
                "summary": store.summary(),
                "rules": store.list_library(enabled_only=False)[:200],
                "issues": issue_store.list_issues(enabled_only=False)[:200],
                "cases": case_store.list_cases(enabled_only=False, limit=200),
            },
        )
    )


@router.get("/netdiag/lab", response_class=HTMLResponse)
async def netdiag_lab(request: Request):
    duel_store = _require_duel_store(request)
    return _no_cache(
        templates.TemplateResponse(
            "netdiag_lab.html",
            {
                "request": request,
                "templates_list": duel_store.list_templates(),
                "duels": duel_store.list_duels(limit=120),
            },
        )
    )


@router.get("/api/netdiag/zabbix/config")
async def zabbix_get_config(request: Request):
    store = _require_zabbix_store(request)
    return {"ok": True, "config": store.get(masked=True)}


@router.post("/api/netdiag/zabbix/config")
async def zabbix_set_config(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_zabbix_store(request)
    row = store.update(payload or {})
    return {"ok": True, "config": row}


@router.post("/api/netdiag/zabbix/test")
async def zabbix_test(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_zabbix_store(request)
    base_cfg = store.get(masked=False)
    cfg_dict = _zabbix_cfg_from_payload(base_cfg, payload or {})
    cfg = ZabbixConfig.from_dict(cfg_dict)
    try:
        cfg.validate()
        client = ZabbixClient(cfg)
        detail = client.ping()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"zabbix test failed: {exc}") from exc
    return {"ok": True, "detail": detail}


@router.post("/api/netdiag/zabbix/history")
async def zabbix_history(request: Request, payload: dict[str, Any] = Body(default={})):
    return _query_zabbix_history_data(request, payload or {})


@router.get("/api/netdiag/connection/config")
async def connection_get_config(request: Request):
    conn_store = _require_connection_store(request)
    zbx_store = _require_zabbix_store(request)
    return {
        "ok": True,
        "config": conn_store.get(),
        "zabbix": zbx_store.get(masked=True),
    }


@router.post("/api/netdiag/connection/config")
async def connection_set_config(request: Request, payload: dict[str, Any] = Body(default={})):
    body = payload or {}
    conn_store = _require_connection_store(request)
    zbx_store = _require_zabbix_store(request)
    conn_patch = body.get("config") if isinstance(body.get("config"), dict) else body
    zbx_patch = body.get("zabbix") if isinstance(body.get("zabbix"), dict) else {}

    conn_row = conn_store.update(conn_patch if isinstance(conn_patch, dict) else {})

    # Keep existing password/token when user doesn't provide new value.
    zbx_apply = dict(zbx_patch)
    if "password" in zbx_apply and not str(zbx_apply.get("password") or "").strip():
        zbx_apply.pop("password", None)
    if "api_token" in zbx_apply and not str(zbx_apply.get("api_token") or "").strip():
        zbx_apply.pop("api_token", None)
    zbx_row = zbx_store.update(zbx_apply if isinstance(zbx_apply, dict) else {})
    return {"ok": True, "config": conn_row, "zabbix": zbx_row}


@router.post("/api/netdiag/llm/route/check")
async def check_llm_route_availability(payload: dict[str, Any] = Body(default={})):
    body = payload or {}
    llm_route = _resolve_llm_route(body)
    llm_primary_raw = _build_llm_input(llm_route.get("primary") or None)
    llm_failover_raw = _build_llm_input(llm_route.get("failover") or None) if llm_route.get("failover") else None
    llm_primary, llm_failover, route_diag = _resolve_llm_runtime_inputs(llm_primary_raw, llm_failover_raw)

    def _row_status(row: dict[str, str] | None) -> dict[str, Any]:
        base = row if isinstance(row, dict) else {}
        ok, reason = _llm_input_readiness(base)
        return {
            "provider": str(base.get("provider") or "").strip().lower(),
            "model": str(model_used(base) or "").strip(),
            "ready": bool(ok),
            "reason": str(reason or "").strip(),
        }

    primary_status = _row_status(llm_primary_raw)
    failover_status = _row_status(llm_failover_raw) if llm_failover_raw else None

    runtime_reason = str(route_diag.get("unavailable_reason") or "").strip()
    if bool(route_diag.get("no_ready_model")):
        message = runtime_reason or "no available model route"
    elif bool(route_diag.get("switched_to_failover")):
        message = runtime_reason or "primary unavailable, switched to failover"
    else:
        message = "model route ready"

    return {
        "ok": True,
        "message": message,
        "primary": primary_status,
        "failover": failover_status,
        "runtime": {
            "primary_provider": str(llm_primary.get("provider") or "").strip().lower(),
            "primary_model": str(model_used(llm_primary) or "").strip(),
            "failover_provider": (str(llm_failover.get("provider") or "").strip().lower() if llm_failover else ""),
            "failover_model": (str(model_used(llm_failover) or "").strip() if llm_failover else ""),
            "switched_to_failover": bool(route_diag.get("switched_to_failover")),
            "no_ready_model": bool(route_diag.get("no_ready_model")),
            "reason": runtime_reason,
        },
        "route_diag": route_diag,
    }


@router.post("/api/netdiag/intent/parse")
async def parse_dialogue_intent(request: Request, payload: dict[str, Any] = Body(default={})):
    body = payload or {}
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    timezone_name = str(body.get("timezone") or "Asia/Singapore").strip() or "Asia/Singapore"
    use_llm = _to_bool(body.get("use_llm", True), True)
    llm_route = _resolve_llm_route(body)
    llm_primary = _build_llm_input(llm_route.get("primary") or None)
    llm_failover = _build_llm_input(llm_route.get("failover") or None) if llm_route.get("failover") else None
    llm_primary, llm_failover, route_diag = _resolve_llm_runtime_inputs(llm_primary, llm_failover)

    heuristic = _heuristic_intent_parse(text, timezone_name=timezone_name)
    llm_obj: dict[str, Any] = {}
    llm_raw = ""
    llm_error = ""
    source = "heuristic"
    provider = ""
    model = ""

    if use_llm:
        llm_input = llm_primary
        provider = str(llm_input.get("provider") or "").strip()
        model = str(model_used(llm_input) or "").strip()
        if bool(route_diag.get("no_ready_model")):
            llm_error = str(route_diag.get("unavailable_reason") or "no available model route")
            llm_obj = {}
        else:
            try:
                llm_obj, llm_raw, llm_error = await _llm_intent_parse(
                    text,
                    timezone_name=timezone_name,
                    llm_primary=llm_primary,
                    llm_failover=llm_failover,
                )
            except Exception as exc:
                llm_error = str(exc)
                llm_obj = {}
        normalized, source = _normalize_intent_parse_result(
            text=text,
            timezone_name=timezone_name,
            heuristic=heuristic,
            llm_row=llm_obj,
        )
    else:
        normalized = dict(heuristic)

    missing: list[str] = []
    if not str(normalized.get("question") or "").strip():
        missing.append("question")
    if not _valid_ipv4(str(normalized.get("device_ip") or "").strip()):
        missing.append("device_ip")
    if not str(normalized.get("fault_start") or "").strip():
        missing.append("fault_start")
    if not str(normalized.get("fault_end") or "").strip():
        missing.append("fault_end")

    confidence = dict(normalized.get("confidence") or {})
    normalized["confidence"] = {
        "device_ip": _clamp_confidence(confidence.get("device_ip"), 0.0),
        "time_window": _clamp_confidence(confidence.get("time_window"), 0.0),
        "question": _clamp_confidence(confidence.get("question"), 0.0),
        "focus_goals": _clamp_confidence(confidence.get("focus_goals"), 0.0),
    }
    normalized["timezone"] = str(normalized.get("timezone") or timezone_name or "Asia/Singapore")
    normalized["focus_goals"] = _dedupe_texts(_split_goal_text(normalized.get("focus_goals", [])), limit=8)
    normalized["question"] = str(normalized.get("question") or "").strip()[:500]
    normalized["follow_up"] = str(normalized.get("follow_up") or text).strip()

    llm_used = use_llm and bool(llm_obj)
    if source == "heuristic" and use_llm:
        source = "heuristic_fallback"
    return {
        "ok": True,
        "source": source,
        "missing": missing,
        "parsed": normalized,
        "llm": {
            "requested": bool(use_llm),
            "used": bool(llm_used),
            "provider": provider,
            "model": model,
            "failover_provider": (str(llm_failover.get("provider") or "").strip() if llm_failover else ""),
            "failover_model": (str(model_used(llm_failover) or "").strip() if llm_failover else ""),
            "route_diag": route_diag,
            "error": llm_error,
            "raw_preview": str(llm_raw or "")[:800],
        },
    }


@router.post("/api/netdiag/state/query")
async def query_device_state(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_state_store(request)
    body = payload or {}
    device_id = str(body.get("device_id") or "").strip()
    domain = str(body.get("domain") or "").strip().lower()
    key = str(body.get("key") or "").strip().lower()
    ts_from = body.get("ts_from")
    ts_till = body.get("ts_till")
    limit = max(1, min(_safe_int(body.get("limit"), 500), 10000))
    newest_first = bool(body.get("newest_first", False))
    points = store.query_points(
        device_id=device_id,
        domain=domain,
        key=key,
        ts_from=(_safe_int(ts_from) if ts_from is not None else None),
        ts_till=(_safe_int(ts_till) if ts_till is not None else None),
        limit=limit,
        newest_first=newest_first,
    )
    return {"ok": True, "points": points, "count": len(points)}


@router.get("/api/netdiag/state/{device_id}")
async def latest_device_state(device_id: str, request: Request, domain: str = "", key: str = "", limit: int = 120):
    store = _require_state_store(request)
    points = store.query_points(
        device_id=str(device_id or "").strip(),
        domain=str(domain or "").strip().lower(),
        key=str(key or "").strip().lower(),
        limit=max(1, min(int(limit), 5000)),
        newest_first=True,
    )
    return {"ok": True, "device_id": device_id, "points": points, "count": len(points)}


@router.post("/api/netdiag/state/ingest")
async def ingest_device_state(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_state_store(request)
    points = payload.get("points", [])
    if not isinstance(points, list):
        raise HTTPException(status_code=400, detail="points must be a list")
    result = store.append_points(points)
    return {"ok": True, "result": result}


@router.post("/api/netdiag/config/snapshot")
async def config_snapshot(request: Request, payload: dict[str, Any] = Body(default={})):
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    cfg_store = _require_config_store(request)
    device_inputs = manager.get_device_inputs(session_id)
    by_did = {str(d.device_name or "").strip(): d for d in device_inputs if str(d.device_name or "").strip()}
    profile_map = _device_profile_map(session)
    did_target = str(body.get("device_id") or "").strip()
    if did_target:
        targets = [did_target]
    else:
        targets = [str(d.device_id or "").strip() for d in (session.devices or []) if str(d.device_id or "").strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="no devices in session")

    snapshots: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    timeout_sec = max(20, min(_safe_int(body.get("timeout_sec"), int(session.per_device_timeout or 90)), 300))
    for did in targets:
        device = by_did.get(did)
        if device is None:
            failed.append({"device_id": did, "error": "device credentials not found"})
            continue
        profile = str(profile_map.get(did) or "unknown")
        command = _config_command_for_profile(profile)
        run_dir = manager.output_root / session_id / "config_snapshots" / did
        try:
            run_res = await run_read_only_commands(
                device=device,
                commands=[command],
                output_dir=run_dir,
                timeout_per_command=timeout_sec,
                debug_mode=True,
            )
        except Exception as exc:
            failed.append({"device_id": did, "error": f"snapshot execution failed: {exc}"})
            continue
        row = (run_res[0] if run_res else {}) if isinstance(run_res, list) else {}
        status = str(row.get("status") or "")
        out_file = str(row.get("output_file") or "").strip()
        if status != "success" or not out_file:
            failed.append({"device_id": did, "error": str(row.get("error") or f"command status={status}")})
            continue
        try:
            snap = cfg_store.add_snapshot(
                {
                    "device_id": did,
                    "session_id": session_id,
                    "profile": profile,
                    "command": command,
                    "file_path": out_file,
                    "source": "netdiag_snapshot",
                    "tags": [f"session:{session_id}", f"profile:{profile}"],
                }
            )
            snapshots.append(snap)
        except Exception as exc:
            failed.append({"device_id": did, "error": f"snapshot store failed: {exc}"})
    return {"ok": len(snapshots) > 0, "snapshots": snapshots, "failed": failed}


@router.get("/api/netdiag/config/{device_id}/history")
async def config_history(device_id: str, request: Request, session_id: str = "", limit: int = 50):
    store = _require_config_store(request)
    items = store.list_snapshots(
        device_id=str(device_id or "").strip(),
        session_id=str(session_id or "").strip(),
        limit=max(1, min(int(limit), 5000)),
    )
    return {"ok": True, "device_id": device_id, "items": items, "count": len(items)}


@router.post("/api/netdiag/config/diff")
async def config_diff(request: Request, payload: dict[str, Any] = Body(default={})):
    body = payload or {}
    a = str(body.get("snapshot_id_a") or "").strip()
    b = str(body.get("snapshot_id_b") or "").strip()
    if not a or not b:
        raise HTTPException(status_code=400, detail="snapshot_id_a and snapshot_id_b are required")
    store = _require_config_store(request)
    try:
        diff = store.diff_snapshots(
            a,
            b,
            context=max(0, min(_safe_int(body.get("context"), 3), 12)),
            max_lines=max(50, min(_safe_int(body.get("max_lines"), 2000), 12000)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"config diff failed: {exc}") from exc
    sigs, summary = _config_diff_signals(str(diff.get("diff_text") or ""))
    return {"ok": True, "diff": diff, "signals": sigs, "summary": summary}


@router.get("/api/netdiag/sessions")
async def list_diag_sessions(request: Request):
    manager = request.app.state.diag_session_manager
    sessions = manager.list_sessions()
    fixed = [_normalize_session_status_for_ui(manager, s) for s in sessions]
    return {"ok": True, "items": [_session_dump_with_next_action(s) for s in fixed]}


@router.post("/api/netdiag/sessions")
async def create_diag_session(request: Request, payload: dict[str, Any] = Body(default={})):
    try:
        conn_store = getattr(request.app.state, "connection_store", None)
        conn_cfg = conn_store.get() if isinstance(conn_store, NetdiagConnectionStore) else {}
        body = _with_session_connection_defaults(payload or {}, conn_cfg)
        body = _ensure_session_fault_window(body, default_hours=24)
        req = DiagnosisSessionCreate(**body)
        session = request.app.state.diag_session_manager.create_session(req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid payload: {exc}") from exc
    return {"ok": True, "session": _session_dump_with_next_action(session)}


@router.get("/api/netdiag/sessions/{session_id}")
async def get_diag_session(session_id: str, request: Request):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    return {"ok": True, "session": _session_dump_with_next_action(session)}


@router.post("/api/netdiag/sessions/{session_id}/stop")
async def stop_diag_session(session_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    reason = str((payload or {}).get("reason") or "").strip() or "manual stop from UI"
    info = _set_stop_requested(request, session_id, reason=reason)

    latest = _latest_round_or_none(session)
    if latest is not None:
        rno = int(getattr(latest, "round_no", 0) or 0)
        rstatus = str(getattr(latest, "status", "") or "").strip().lower()
        if rno > 0 and rstatus in {"planning", "waiting_approval", "executing", "analyzing"}:
            manager.set_round_executions(
                session_id,
                rno,
                executions=list(getattr(latest, "executions", []) or []),
                status="failed",
            )
    manager.set_last_error(session_id, f"stop requested by user: {reason}")
    session = manager.set_status(session_id, "aborted")
    session = _normalize_session_status_for_ui(manager, session)
    return {
        "ok": True,
        "stopped": True,
        "session_id": session_id,
        "stop_info": info,
        "session": (_session_dump_with_next_action(session) if session else None),
    }


@router.post("/api/netdiag/sessions/{session_id}/resume")
async def resume_diag_session(session_id: str, request: Request):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    _clear_stop_requested(request, session_id)
    status = str(getattr(session, "status", "") or "").strip().lower()
    if status in {"aborted", "failed"}:
        next_status = _ready_for_next_probe_status() if _has_baseline_success(session) else "draft"
        manager.set_status(session_id, next_status)
    session = manager.get_session(session_id)
    session = _normalize_session_status_for_ui(manager, session)
    return {
        "ok": True,
        "resumed": True,
        "session_id": session_id,
        "session": (_session_dump_with_next_action(session) if session else None),
    }


@router.get("/api/netdiag/sessions/{session_id}/next_action")
async def get_diag_session_next_action(session_id: str, request: Request):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    return {"ok": True, "session_id": session_id, "next": _next_workflow_action(session)}


@router.get("/api/netdiag/sessions/{session_id}/rounds/{round_no}/outputs")
async def get_round_outputs(session_id: str, round_no: int, request: Request, tail_chars: int = 5000):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    rnd = manager.get_round(session_id, round_no)
    if not rnd:
        raise HTTPException(status_code=404, detail="round not found")

    tail_limit = max(300, min(int(tail_chars), 20000))
    rows: list[dict[str, Any]] = []
    for ex in rnd.executions or []:
        if not isinstance(ex, CommandExecution):
            continue
        rows.append(
            {
                "round_no": int(round_no),
                "command_id": ex.command_id,
                "device_id": ex.device_id,
                "command": ex.command,
                "status": ex.status,
                "reused": bool(ex.reused),
                "reused_from_round": ex.reused_from_round,
                "reused_from_command_id": ex.reused_from_command_id,
                "error": ex.error,
                "duration_sec": ex.duration_sec,
                "output_file": ex.output_file,
                "output_tail": _read_text_tail_safe(ex.output_file or "", limit=tail_limit),
            }
        )
    return {"ok": True, "session_id": session_id, "round_no": round_no, "items": rows}


@router.get("/api/netdiag/sessions/{session_id}/outputs")
async def get_session_outputs(
    session_id: str,
    request: Request,
    tail_chars: int = 5000,
    max_items: int = 300,
):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    tail_limit = max(300, min(int(tail_chars), 20000))
    item_cap = max(50, min(int(max_items), 1200))
    rows: list[dict[str, Any]] = []
    rounds = list(getattr(session, "rounds", []) or [])
    for rnd in rounds:
        rno = int(getattr(rnd, "round_no", 0) or 0)
        for ex in (getattr(rnd, "executions", []) or []):
            if not isinstance(ex, CommandExecution):
                continue
            rows.append(
                {
                    "round_no": rno,
                    "command_id": ex.command_id,
                    "device_id": ex.device_id,
                    "command": ex.command,
                    "status": ex.status,
                    "reused": bool(ex.reused),
                    "reused_from_round": ex.reused_from_round,
                    "reused_from_command_id": ex.reused_from_command_id,
                    "error": ex.error,
                    "duration_sec": ex.duration_sec,
                    "output_file": ex.output_file,
                    "output_tail": _read_text_tail_safe(ex.output_file or "", limit=tail_limit),
                }
            )
    total_items = len(rows)
    if total_items > item_cap:
        rows = rows[-item_cap:]
    return {
        "ok": True,
        "session_id": session_id,
        "rounds": len(rounds),
        "total_items": total_items,
        "truncated": bool(total_items > item_cap),
        "items": rows,
    }


@router.get("/api/netdiag/sessions/{session_id}/sop_state")
async def get_sop_state(session_id: str, request: Request):
    session = request.app.state.diag_session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    rounds = getattr(session, "rounds", []) or []
    latest = rounds[-1] if rounds else None
    return {
        "ok": True,
        "session_id": session_id,
        "rounds": len(rounds),
        "latest_hypotheses": (latest.hypotheses if latest else []),
        "latest_stop_decision": (latest.stop_decision if latest else {}),
        "latest_known_issue_hits": (latest.known_issue_hits if latest else []),
    }


@router.get("/api/netdiag/learning/summary")
async def learning_summary(request: Request):
    store = _require_learning_store(request)
    return {"ok": True, "summary": store.summary()}


@router.get("/api/netdiag/learning/library")
async def learning_library(request: Request, enabled_only: bool = False):
    store = _require_learning_store(request)
    return {"ok": True, "items": store.list_library(enabled_only=enabled_only)}


@router.post("/api/netdiag/learning/library/upsert")
async def learning_library_upsert(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_learning_store(request)
    try:
        row = store.upsert_rule(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "rule": row}


@router.post("/api/netdiag/learning/library/import_csv")
async def learning_library_import_csv(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_learning_store(request)
    csv_text = str((payload or {}).get("csv_text") or "")
    if not csv_text.strip():
        raise HTTPException(status_code=400, detail="csv_text is required")
    source = str((payload or {}).get("source") or "official").strip() or "official"
    replace_existing = bool((payload or {}).get("replace_existing", False))
    try:
        rows = store.parse_csv_text(csv_text)
        result = store.import_rows(rows, source=source, replace_existing=replace_existing)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"import failed: {exc}") from exc
    return {"ok": True, "rows": len(rows), "result": result}


@router.post("/api/netdiag/learning/library/import_json")
async def learning_library_import_json(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_learning_store(request)
    rows = (payload or {}).get("rows", [])
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows must be a non-empty list")
    source = str((payload or {}).get("source") or "official").strip() or "official"
    replace_existing = bool((payload or {}).get("replace_existing", False))
    try:
        result = store.import_rows(rows, source=source, replace_existing=replace_existing)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"import failed: {exc}") from exc
    return {"ok": True, "rows": len(rows), "result": result}


@router.post("/api/netdiag/learning/library/{rule_id}/enabled")
async def learning_library_set_enabled(
    rule_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default={}),
):
    store = _require_learning_store(request)
    enabled = bool((payload or {}).get("enabled", True))
    row = store.set_rule_enabled(rule_id, enabled=enabled)
    if not row:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"ok": True, "rule": row}


@router.delete("/api/netdiag/learning/library/{rule_id}")
async def learning_library_delete(rule_id: str, request: Request):
    store = _require_learning_store(request)
    deleted = store.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"ok": True, "deleted": True}


@router.get("/api/netdiag/issues/library")
async def known_issue_library(request: Request, enabled_only: bool = False):
    store = _require_known_issue_store(request)
    return {"ok": True, "items": store.list_issues(enabled_only=enabled_only)}


@router.post("/api/netdiag/issues/library/upsert")
async def known_issue_upsert(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_known_issue_store(request)
    try:
        row = store.upsert_issue(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "issue": row}


@router.post("/api/netdiag/issues/library/import_csv")
async def known_issue_import_csv(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_known_issue_store(request)
    csv_text = str((payload or {}).get("csv_text") or "")
    if not csv_text.strip():
        raise HTTPException(status_code=400, detail="csv_text is required")
    source = str((payload or {}).get("source") or "noc").strip() or "noc"
    replace_existing = bool((payload or {}).get("replace_existing", False))
    try:
        rows = store.parse_csv_text(csv_text)
        result = store.import_rows(rows, source=source, replace_existing=replace_existing)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"import failed: {exc}") from exc
    return {"ok": True, "rows": len(rows), "result": result}


@router.post("/api/netdiag/issues/library/import_json")
async def known_issue_import_json(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_known_issue_store(request)
    rows = (payload or {}).get("rows", [])
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rows must be a non-empty list")
    source = str((payload or {}).get("source") or "noc").strip() or "noc"
    replace_existing = bool((payload or {}).get("replace_existing", False))
    try:
        result = store.import_rows(rows, source=source, replace_existing=replace_existing)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"import failed: {exc}") from exc
    return {"ok": True, "rows": len(rows), "result": result}


@router.post("/api/netdiag/issues/library/{issue_id}/enabled")
async def known_issue_set_enabled(
    issue_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default={}),
):
    store = _require_known_issue_store(request)
    enabled = bool((payload or {}).get("enabled", True))
    row = store.set_issue_enabled(issue_id, enabled=enabled)
    if not row:
        raise HTTPException(status_code=404, detail="issue not found")
    return {"ok": True, "issue": row}


@router.delete("/api/netdiag/issues/library/{issue_id}")
async def known_issue_delete(issue_id: str, request: Request):
    store = _require_known_issue_store(request)
    deleted = store.delete_issue(issue_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="issue not found")
    return {"ok": True, "deleted": True}


@router.post("/api/netdiag/issues/search")
async def known_issue_search(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_known_issue_store(request)
    profile = str((payload or {}).get("profile") or "unknown").strip().lower()
    version = str((payload or {}).get("version") or "").strip()
    query_text = str((payload or {}).get("query_text") or "").strip()
    evidence_text = str((payload or {}).get("evidence_text") or "").strip()
    limit = int((payload or {}).get("limit") or 8)
    hits = store.search(
        profile=profile,
        version=version,
        query_text=query_text,
        evidence_text=evidence_text,
        limit=limit,
    )
    return {"ok": True, "items": hits}


@router.get("/api/netdiag/cases/library")
async def case_library(request: Request, enabled_only: bool = False, limit: int = 200):
    store = _require_case_store(request)
    return {"ok": True, "items": store.list_cases(enabled_only=enabled_only, limit=max(1, min(int(limit), 5000)))}


@router.post("/api/netdiag/cases/library/upsert")
async def case_upsert(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_case_store(request)
    try:
        row = store.upsert_case(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "case": row}


@router.post("/api/netdiag/cases/library/{case_id}/enabled")
async def case_set_enabled(
    case_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default={}),
):
    store = _require_case_store(request)
    enabled = bool((payload or {}).get("enabled", True))
    row = store.set_case_enabled(case_id, enabled=enabled)
    if not row:
        raise HTTPException(status_code=404, detail="case not found")
    return {"ok": True, "case": row}


@router.delete("/api/netdiag/cases/library/{case_id}")
async def case_delete(case_id: str, request: Request):
    store = _require_case_store(request)
    deleted = store.delete_case(case_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="case not found")
    return {"ok": True, "deleted": True}


@router.post("/api/netdiag/cases/search")
async def case_search(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_case_store(request)
    body = payload or {}
    profiles = _to_text_list(body.get("profiles"))
    profile = str(body.get("profile") or "").strip().lower()
    if profile and profile not in [str(x).strip().lower() for x in profiles]:
        profiles.append(profile)
    domains = _to_text_list(body.get("domains"))
    domain = str(body.get("domain") or "").strip().lower()
    if domain and domain not in [str(x).strip().lower() for x in domains]:
        domains.append(domain)
    query_text = str(body.get("query_text") or "").strip()
    evidence_text = str(body.get("evidence_text") or "").strip()
    limit = max(1, min(_safe_int(body.get("limit"), 8), 50))
    hits = store.search(
        query_text=query_text,
        profiles=profiles,
        domains=domains,
        evidence_text=evidence_text,
        limit=limit,
    )
    return {"ok": True, "items": hits, "signals": _case_hits_to_signals(hits, max_signals=12)}


@router.post("/api/netdiag/cases/from_session/{session_id}")
async def case_from_session(session_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    rounds = list(getattr(session, "rounds", []) or [])
    if not rounds:
        raise HTTPException(status_code=400, detail="session has no rounds")

    body = payload or {}
    round_no = _safe_int(body.get("round_no"), 0)
    target = None
    if round_no > 0:
        target = manager.get_round(session_id, round_no)
        if not target:
            raise HTTPException(status_code=404, detail="round not found")
    else:
        target = next((r for r in reversed(rounds) if str(r.analysis_result or "").strip()), rounds[-1])
    if target is None:
        raise HTTPException(status_code=400, detail="unable to resolve source round")

    ranked_h = rank_hypotheses(list(target.hypotheses or []))
    top = ranked_h[0] if ranked_h else {}
    root_cause = str(body.get("root_cause") or "").strip() or str(top.get("title") or "").strip() or "Undetermined"
    title = str(body.get("title") or "").strip() or (str(top.get("title") or "").strip() or f"Case from session {session_id}")

    profile_map = _device_profile_map(session)
    profiles = sorted({str(x).strip().lower() for x in profile_map.values() if str(x).strip()})
    domains = _to_text_list(body.get("domains"))
    if not domains:
        domains = [str(h.get("domain") or "").strip().lower() for h in ranked_h[:3] if str(h.get("domain") or "").strip()]
    if not domains:
        domains = [str(x.get("domain") or "").strip().lower() for x in derive_domains(session.question, session.focus_goals) if str(x.get("domain") or "").strip()]

    resolution_steps = _to_text_list(body.get("resolution_steps"))
    if not resolution_steps:
        resolution_steps = [f"{c.device_id}: {c.command}" for c in (target.commands or []) if str(c.command or "").strip()][:20]

    verify_commands = _to_text_list(body.get("verify_commands"))
    if not verify_commands:
        seen_cmd: set[str] = set()
        for c in (target.commands or []):
            cmd = str(c.command or "").strip()
            if not cmd or cmd in seen_cmd:
                continue
            seen_cmd.add(cmd)
            verify_commands.append(cmd)
        verify_commands = verify_commands[:20]

    evidence_signals = _to_text_list(body.get("evidence_signals"))
    if not evidence_signals:
        sig_names = []
        for sig in (target.evidence_signals or []):
            if not isinstance(sig, dict):
                continue
            name = str(sig.get("signal") or "").strip()
            if name:
                sig_names.append(name)
        evidence_signals = sorted({x for x in sig_names if x})[:24]

    focus_goals = _to_text_list(body.get("focus_goals")) or list(session.focus_goals or [])
    question = str(body.get("question") or "").strip() or str(session.question or "")
    confidence = _safe_float(body.get("confidence"), _safe_float(top.get("score"), 0.7))
    priority = _safe_int(body.get("priority"), 120)
    tags = _to_text_list(body.get("tags"))
    tags = _normalize_focus_goals([*tags, f"session:{session_id}", f"round:{target.round_no}"])

    store = _require_case_store(request)
    try:
        row = store.upsert_case(
            {
                "case_id": str(body.get("case_id") or "").strip() or None,
                "title": title,
                "question": question,
                "focus_goals": focus_goals,
                "vendor_profiles": profiles,
                "domains": domains,
                "root_cause": root_cause,
                "resolution_steps": resolution_steps,
                "verify_commands": verify_commands,
                "evidence_signals": evidence_signals,
                "source_session_id": session_id,
                "source_round_no": int(target.round_no),
                "confidence": confidence,
                "priority": priority,
                "tags": tags,
                "enabled": bool(body.get("enabled", True)),
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"case build failed: {exc}") from exc
    return {"ok": True, "case": row, "session_id": session_id, "round_no": int(target.round_no)}


@router.get("/api/netdiag/lab/templates")
async def lab_templates(request: Request, vendor_profile: str = "", domain: str = ""):
    store = _require_duel_store(request)
    return {
        "ok": True,
        "items": store.list_templates(vendor_profile=str(vendor_profile or ""), domain=str(domain or "")),
    }


@router.get("/api/netdiag/lab/duels")
async def lab_list_duels(request: Request, status: str = "", limit: int = 200):
    store = _require_duel_store(request)
    return {
        "ok": True,
        "items": store.list_duels(status=str(status or ""), limit=max(1, min(int(limit), 5000))),
    }


@router.get("/api/netdiag/lab/duels/{duel_id}")
async def lab_get_duel(duel_id: str, request: Request):
    store = _require_duel_store(request)
    row = store.get_duel(duel_id)
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "duel": row}


@router.post("/api/netdiag/lab/duels")
async def lab_create_duel(request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_duel_store(request)
    body = payload or {}
    # Hard guardrail: this system does not execute fault injection commands on live devices.
    mode = str(body.get("mode") or "simulated").strip().lower()
    if mode != "simulated":
        raise HTTPException(status_code=400, detail="only simulated mode is allowed in this build")
    try:
        row = store.create_duel(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "duel": row}


@router.delete("/api/netdiag/lab/duels/{duel_id}")
async def lab_delete_duel(duel_id: str, request: Request):
    store = _require_duel_store(request)
    if not store.delete_duel(duel_id):
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "deleted": True}


@router.post("/api/netdiag/lab/duels/{duel_id}/inject")
async def lab_inject_duel(duel_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_duel_store(request)
    row = store.get_duel(duel_id)
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")
    if str(row.get("mode") or "").strip().lower() != "simulated":
        raise HTTPException(status_code=400, detail="only simulated injection is allowed")
    out = store.set_inject_result(
        duel_id,
        {
            "detail": str((payload or {}).get("detail") or "simulated fault injected (no device command executed)"),
            "ok": True,
        },
    )
    if not out:
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "duel": out}


@router.post("/api/netdiag/lab/duels/{duel_id}/bind_blue_session")
async def lab_bind_blue_session(duel_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_duel_store(request)
    manager = request.app.state.diag_session_manager
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not manager.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    round_no = max(0, _safe_int((payload or {}).get("round_no"), 0))
    try:
        row = store.bind_blue_session(duel_id, session_id=session_id, round_no=round_no)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "duel": row}


@router.post("/api/netdiag/lab/duels/{duel_id}/judge")
async def lab_judge_duel(duel_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_duel_store(request)
    manager = request.app.state.diag_session_manager
    row = store.get_duel(duel_id)
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")

    body = dict(payload or {})
    if not str(body.get("predicted_domain") or "").strip():
        sid = str(row.get("blue_session_id") or "").strip()
        if sid:
            session = manager.get_session(sid)
            if session:
                round_no = _safe_int(body.get("round_no"), _safe_int(row.get("blue_round_no"), 0))
                rnd = manager.get_round(sid, round_no) if round_no > 0 else None
                if rnd is None and getattr(session, "rounds", None):
                    rounds = list(getattr(session, "rounds", []) or [])
                    rnd = next((r for r in reversed(rounds) if str(r.analysis_result or "").strip()), rounds[-1] if rounds else None)
                if rnd is not None:
                    ranked = rank_hypotheses(list(rnd.hypotheses or []))
                    top = ranked[0] if ranked else {}
                    if top and not str(body.get("predicted_domain") or "").strip():
                        body["predicted_domain"] = str(top.get("domain") or "")
                    if top and not str(body.get("predicted_root_cause") or "").strip():
                        body["predicted_root_cause"] = str(top.get("title") or "")
                    if "confidence" not in body and top:
                        body["confidence"] = _safe_float(top.get("score"), 0.0)
                    if not body.get("evidence_signals"):
                        sigs = []
                        for sig in (rnd.evidence_signals or []):
                            if not isinstance(sig, dict):
                                continue
                            name = str(sig.get("signal") or "").strip()
                            if name:
                                sigs.append(name)
                        body["evidence_signals"] = sorted(set(sigs))
                    if "recovery_verified" not in body:
                        body["recovery_verified"] = bool((rnd.stop_decision or {}).get("recommend_conclude", False))
    judged = store.judge_duel(duel_id, body)
    if not judged:
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "duel": judged, "judge_result": judged.get("judge_result", {})}


@router.post("/api/netdiag/lab/duels/{duel_id}/rollback")
async def lab_rollback_duel(duel_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    store = _require_duel_store(request)
    row = store.mark_rolled_back(duel_id, payload or {})
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")
    return {"ok": True, "duel": row}


@router.post("/api/netdiag/lab/duels/{duel_id}/promote_case")
async def lab_promote_case(duel_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    duel_store = _require_duel_store(request)
    case_store = _require_case_store(request)
    manager = request.app.state.diag_session_manager
    row = duel_store.get_duel(duel_id)
    if not row:
        raise HTTPException(status_code=404, detail="duel not found")

    body = payload or {}
    judge = dict(row.get("judge_result") or {})
    force = bool(body.get("force", False))
    if not force and str(judge.get("verdict") or "").strip().lower() != "pass":
        raise HTTPException(status_code=400, detail="duel verdict is not pass; set force=true to override")

    sid = str(row.get("blue_session_id") or "").strip()
    source_round = _safe_int(row.get("blue_round_no"), 0)
    session = manager.get_session(sid) if sid else None
    source_hypothesis = {}
    evidence_signal_names: list[str] = []
    verify_commands: list[str] = []
    if session:
        rnd = manager.get_round(sid, source_round) if source_round > 0 else None
        if rnd is None and (getattr(session, "rounds", None) or []):
            rounds = list(getattr(session, "rounds", []) or [])
            rnd = next((r for r in reversed(rounds) if str(r.analysis_result or "").strip()), rounds[-1])
        if rnd is not None:
            source_round = int(rnd.round_no)
            ranked = rank_hypotheses(list(rnd.hypotheses or []))
            source_hypothesis = ranked[0] if ranked else {}
            for sig in (rnd.evidence_signals or []):
                if not isinstance(sig, dict):
                    continue
                name = str(sig.get("signal") or "").strip()
                if name:
                    evidence_signal_names.append(name)
            for cmd in (rnd.commands or []):
                c = str(cmd.command or "").strip()
                if c:
                    verify_commands.append(c)

    root_cause = str(body.get("root_cause") or "").strip()
    if not root_cause:
        root_cause = str(judge.get("predicted_root_cause") or "").strip()
    if not root_cause:
        root_cause = str(row.get("reference_root_cause") or "").strip() or "lab simulated fault"

    case_title = str(body.get("title") or "").strip() or f"[LAB] {str(row.get('template_name') or row.get('template_id') or duel_id)}"
    domains = _to_text_list(body.get("domains"))
    if not domains:
        domains = [str(judge.get("predicted_domain") or "").strip().lower()]
    if not domains:
        domains = [str(row.get("domain") or "").strip().lower()]
    profiles = _to_text_list(body.get("vendor_profiles")) or list(row.get("vendor_profiles") or [])
    resolution_steps = _to_text_list(body.get("resolution_steps"))
    if not resolution_steps:
        resolution_steps = [str(x.get("description") or "").strip() for x in (row.get("inject_plan") or []) if str(x.get("description") or "").strip()]
    if not resolution_steps:
        resolution_steps = ["Rollback simulated fault template"]
    if not verify_commands:
        verify_commands = [str(x) for x in _to_text_list(body.get("verify_commands")) if str(x).strip()]
    if not verify_commands:
        verify_commands = ["show clock", "show logging", "show interface brief"]
    evidence_signals = sorted(set(evidence_signal_names or _to_text_list(body.get("evidence_signals")) or list(judge.get("evidence_signals") or []) or list(row.get("expected_signals") or [])))
    confidence = _safe_float(body.get("confidence"), _safe_float(judge.get("score"), 0.0) / 100.0)
    confidence = max(0.0, min(1.0, confidence))
    priority = max(1, min(_safe_int(body.get("priority"), 90), 999))

    case_question = str(body.get("question") or "").strip()
    if not case_question:
        case_question = str(session.question).strip() if session else f"lab duel {duel_id}"
    try:
        case_row = case_store.upsert_case(
            {
                "title": case_title,
                "question": case_question,
                "focus_goals": _to_text_list(body.get("focus_goals")) or list(row.get("focus_goals") or []),
                "vendor_profiles": profiles,
                "domains": domains,
                "root_cause": root_cause,
                "resolution_steps": resolution_steps,
                "verify_commands": verify_commands,
                "evidence_signals": evidence_signals,
                "source_session_id": sid,
                "source_round_no": source_round,
                "confidence": confidence,
                "priority": priority,
                "tags": _normalize_focus_goals(
                    [
                        *_to_text_list(body.get("tags")),
                        "lab_duel",
                        f"duel:{duel_id}",
                        f"template:{str(row.get('template_id') or '')}",
                    ]
                ),
                "enabled": bool(body.get("enabled", True)),
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"promote case failed: {exc}") from exc

    duel = duel_store.set_case_result(
        duel_id,
        {
            "ok": True,
            "case_id": str(case_row.get("case_id") or ""),
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"ok": True, "case": case_row, "duel": duel}


@router.post("/api/netdiag/sessions/{session_id}/baseline_collect")
async def baseline_collect(session_id: str, request: Request):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    _assert_not_stopped(request, session_id, "baseline_collect")

    # Baseline is one-time per session by default.
    existing_items = list(getattr(session, "time_calibration", []) or [])
    if any(str(x.get("status") or "").strip().lower() == "success" for x in existing_items):
        if str(getattr(session, "status", "") or "").strip().lower() not in {"concluded", "failed"}:
            manager.set_status(session_id, _ready_for_next_probe_status())
            session = manager.get_session(session_id) or session
        return {
            "ok": True,
            "baseline_reused": True,
            "items": existing_items,
            "session": (_session_dump_with_next_action(session) if session else None),
            "message": "baseline already collected",
        }

    nxt = _assert_action_allowed(session, {"baseline"}, "baseline_collect")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {
            "ok": True,
            "busy": True,
            "message": "baseline is already running",
            "session": _session_dump_with_next_action(session),
        }

    devices = manager.get_device_inputs(session_id)
    if not devices:
        raise HTTPException(status_code=400, detail="no devices in session")

    manager.set_status(session_id, "calibrating_time")
    baseline_dir = manager.output_root / session_id / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    try:
        for idx, device in enumerate(devices):
            _raise_if_stop_requested(request, session_id, stage="baseline_collect")
            device_public = session.devices[idx]
            device_dir = baseline_dir / device_public.device_id
            try:
                result = await run_device_collection(
                    device=device,
                    output_dir=device_dir,
                    user_start=session.fault_window.start_at,
                    user_end=session.fault_window.end_at,
                    context_lines=int(session.context_lines),
                    per_device_timeout=int(session.per_device_timeout),
                    debug_mode=True,
                )
                _raise_if_stop_requested(request, session_id, stage="baseline_collect")
                meta = {}
                try:
                    meta = json.loads(Path(result.get("meta_path", "")).read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                items.append(
                    {
                        "device_id": device_public.device_id,
                        "device_ip": device_public.device_ip,
                        "status": "success",
                        "vendor": result.get("vendor", "unknown"),
                        "os_family": result.get("os_family", "unknown"),
                        "model": result.get("model"),
                        "version": result.get("version"),
                        "offset_seconds": result.get("offset_seconds"),
                        "device_start": meta.get("device_start"),
                        "device_end": meta.get("device_end"),
                        "hits_count": result.get("hits_count", 0),
                        "log_time_min": meta.get("log_time_min"),
                        "log_time_max": meta.get("log_time_max"),
                        "reference_time": result.get("reference_time"),
                        "device_time": result.get("device_time"),
                        "raw_log_path": result.get("raw_log_path"),
                        "filtered_log_path": result.get("filtered_log_path"),
                        "meta_path": result.get("meta_path"),
                        "debug_log_path": result.get("debug_log_path"),
                    }
                )
            except Exception as exc:
                items.append(
                    {
                        "device_id": device_public.device_id,
                        "device_ip": device_public.device_ip,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
    except _UserStopRequested as exc:
        if items:
            manager.set_time_calibration(session_id, items)
        manager.set_last_error(session_id, str(exc))
        manager.set_status(session_id, "aborted")
        session = manager.get_session(session_id)
        session = _normalize_session_status_for_ui(manager, session)
        return {
            "ok": False,
            "stopped": True,
            "items": items,
            "session": (_session_dump_with_next_action(session) if session else None),
            "message": str(exc),
        }
    except asyncio.CancelledError:
        cancel_msg = "Baseline request cancelled/interrupted. You can retry baseline safely."
        manager.set_last_error(session_id, cancel_msg)
        if any(str(x.get("status") or "").strip().lower() == "success" for x in (session.time_calibration or [])):
            manager.set_status(session_id, _ready_for_next_probe_status())
        else:
            manager.set_status(session_id, "draft")
        raise

    manager.set_time_calibration(session_id, items)
    ok_cnt = sum(1 for x in items if x.get("status") == "success")
    if ok_cnt <= 0:
        manager.set_status(session_id, "failed")
        manager.set_last_error(session_id, "all devices baseline collection failed")
    else:
        manager.set_status(session_id, _ready_for_next_probe_status())
    session = manager.get_session(session_id)
    session = _normalize_session_status_for_ui(manager, session)
    return {"ok": ok_cnt > 0, "items": items, "session": (_session_dump_with_next_action(session) if session else None)}


@router.post("/api/netdiag/sessions/{session_id}/rounds/plan")
async def plan_round(session_id: str, request: Request, payload: dict[str, Any] = Body(default={})):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    _assert_not_stopped(request, session_id, "plan_round")
    if not _has_baseline_success(session):
        raise HTTPException(status_code=400, detail="baseline not collected")

    force_replan = bool((payload or {}).get("force_replan", False))
    latest = _latest_round_or_none(session)
    if latest is not None and not force_replan:
        latest_status = str(getattr(latest, "status", "") or "").strip().lower()
        if latest_status == "waiting_approval":
            return {
                "ok": True,
                "plan_reused": True,
                "round": _round_response_payload(latest),
                "session_id": session_id,
                "message": "existing planned round is waiting approval",
            }
        if latest_status in {"planning", "executing", "analyzing"}:
            raise HTTPException(status_code=409, detail=f"plan blocked: latest round is {latest_status}")

    nxt = _assert_action_allowed(session, {"plan", "continue_probe"}, "plan_round")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {
            "ok": True,
            "busy": True,
            "message": "planner is already running",
            "session_id": session_id,
        }

    requested_max_commands = int((payload or {}).get("max_commands", 6) or 6)
    ai_timeout_sec = max(
        20,
        min(
            int((payload or {}).get("ai_timeout_sec", PLAN_DEFAULT_TIMEOUT_SEC) or PLAN_DEFAULT_TIMEOUT_SEC),
            PLAN_MAX_TIMEOUT_SEC,
        ),
    )
    ai_retries = int((payload or {}).get("ai_retries", 1) or 1)
    llm_route = _resolve_llm_route(payload)
    llm_primary = _build_llm_input(llm_route.get("primary") or None)
    llm_failover = _build_llm_input(llm_route.get("failover") or None) if llm_route.get("failover") else None
    llm_primary, llm_failover, planner_route_diag = _resolve_llm_runtime_inputs(llm_primary, llm_failover)
    ref_policy = _resolve_reference_policy(payload)
    use_known_issues = bool(ref_policy.get("known_issues", True))
    use_case_library = bool(ref_policy.get("case_library", True))
    use_sop_library = bool(ref_policy.get("sop_library", True))
    use_command_library = bool(ref_policy.get("command_library", True))
    t_plan_total = time.perf_counter()
    plan_perf: dict[str, Any] = {}
    manager.set_status(session_id, "planning")
    follow_up = str((payload or {}).get("follow_up", "") or "").strip()
    validation_task = _normalize_validation_task((payload or {}).get("validation_task"))
    target_probe = _validation_task_to_target_probe(validation_task) or _normalize_target_probe((payload or {}).get("target_probe"))
    target_probe_text = _target_probe_text(target_probe)
    effective_follow_up = follow_up or target_probe_text
    focus_goals_new = _normalize_focus_goals((payload or {}).get("focus_goals", []))
    if follow_up and bool((payload or {}).get("follow_up_as_focus", False)):
        focus_goals_new = _normalize_focus_goals([*focus_goals_new, follow_up])
    elif target_probe:
        focus_goals_new = _normalize_focus_goals([*focus_goals_new, *_target_probe_focus_goals(target_probe)])
    if bool((payload or {}).get("replace_focus_goals", False)):
        if focus_goals_new:
            manager.set_focus_goals(session_id, focus_goals_new)
    elif focus_goals_new:
        manager.append_focus_goals(session_id, focus_goals_new)
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    profile_map = _device_profile_map(session)
    version_map = _device_version_map(session)
    t_reference_lookup = time.perf_counter()
    issue_store = getattr(request.app.state, "known_issue_store", None) if use_known_issues else None
    case_store = getattr(request.app.state, "case_store", None) if use_case_library else None
    case_query_text = f"{session.question}\n{effective_follow_up}\n" + "\n".join(session.focus_goals)
    known_issue_hits = (
        _query_issue_hits(
            issue_store=issue_store,
            profile_map=profile_map,
            version_map=version_map,
            query_text=case_query_text,
            evidence_text="",
        )
        if use_known_issues
        else []
    )
    derived_domains = [str(x.get("domain") or "").strip().lower() for x in derive_domains(session.question, session.focus_goals)]
    case_hits = (
        _query_case_hits(
            case_store=case_store if isinstance(case_store, NetdiagCaseStore) else None,
            profile_map=profile_map,
            query_text=case_query_text,
            domains=derived_domains,
            evidence_text="",
            limit=8,
        )
        if use_case_library
        else []
    )
    case_priors = _case_hits_to_issue_like(case_hits)
    score_priors = [*known_issue_hits, *case_priors]
    case_signals = _case_hits_to_signals(case_hits, max_signals=10)
    plan_perf["reference_lookup_ms"] = round((time.perf_counter() - t_reference_lookup) * 1000, 1)
    t_hypothesis = time.perf_counter()
    prev_hypotheses = []
    if getattr(session, "rounds", None):
        try:
            prev_hypotheses = list(session.rounds[-1].hypotheses or [])
        except Exception:
            prev_hypotheses = []
    if prev_hypotheses:
        hypotheses = _apply_target_probe_to_hypotheses(prev_hypotheses, target_probe)
    else:
        hypotheses = _apply_target_probe_to_hypotheses(
            seed_hypotheses(
                question=session.question if not effective_follow_up else f"{session.question}\n{effective_follow_up}",
                focus_goals=session.focus_goals,
                known_issue_hits=score_priors,
            ),
            target_probe,
        )
    max_commands = _minimal_probe_budget(
        session=session,
        profile_map=profile_map,
        hypotheses=hypotheses,
        requested_max=requested_max_commands,
        follow_up=follow_up,
        target_probe=target_probe,
    )
    sop_steps = (
        propose_sop_steps(
            round_no=(len(getattr(session, "rounds", []) or []) + 1),
            profile_map=profile_map,
            hypotheses=hypotheses,
            max_steps=max_commands,
            target_probe=target_probe,
        )
        if use_sop_library
        else []
    )
    plan_perf["hypothesis_sop_ms"] = round((time.perf_counter() - t_hypothesis) * 1000, 1)

    fast_plan_enabled = bool((payload or {}).get("fast_plan_enabled", True))
    fast_plan_first_round_only = bool((payload or {}).get("fast_plan_first_round_only", False))
    is_first_round = len(getattr(session, "rounds", []) or []) == 0
    use_fast_plan = bool(
        fast_plan_enabled
        and use_sop_library
        and not follow_up
        and (is_first_round or not fast_plan_first_round_only)
    )

    effective_question = session.question if not effective_follow_up else (
        f"{session.question}\n\n"
        + ("[User Follow-up]\n" if follow_up else "[Target Probe]\n")
        + effective_follow_up
    )
    summary = ""
    commands: list[PlannedCommand] = []
    raw_output = ""
    if use_fast_plan:
        t_fast = time.perf_counter()
        _raise_if_stop_requested(request, session_id, stage="plan_before_fast")
        commands = _build_commands_from_sop_steps(
            session=session,
            profile_map=profile_map,
            version_map=version_map,
            sop_steps=sop_steps,
            max_commands=max_commands,
            learning_store=(getattr(request.app.state, "learning_store", None) if use_command_library else None),
        )
        _raise_if_stop_requested(request, session_id, stage="plan_after_fast")
        top = (hypotheses or [{}])[0] if hypotheses else {}
        summary = f"Fast deterministic planner used: top_hypothesis={top.get('title', '-')} minimal_probe_budget={max_commands}"
        raw_output = json.dumps(
            {
                "engine": "fast_deterministic",
                "reason": ("target_probe" if target_probe else "no_follow_up"),
                "summary": summary,
                "sop_steps": sop_steps,
                "minimal_probe_budget": max_commands,
                "target_probe": target_probe,
            },
            ensure_ascii=False,
        )
        plan_perf["planner_llm_ms"] = 0.0
        plan_perf["planner_fast_ms"] = round((time.perf_counter() - t_fast) * 1000, 1)
        plan_perf["planner_engine"] = {
            "mode": "fast_deterministic",
            "primary_provider": str(llm_primary.get("provider") or "").strip(),
            "primary_model": str(model_used(llm_primary) or "").strip(),
            "failover_provider": (str(llm_failover.get("provider") or "").strip() if llm_failover else ""),
            "failover_model": (str(model_used(llm_failover) or "").strip() if llm_failover else ""),
            "route_diag": planner_route_diag,
        }
    else:
        planner_mode = "llm"
        planner_reason = ""
        t_planner_llm = time.perf_counter()
        if bool(planner_route_diag.get("no_ready_model")):
            planner_mode = "deterministic_no_model"
            planner_reason = str(planner_route_diag.get("unavailable_reason") or "no available model route")
        else:
            try:
                _raise_if_stop_requested(request, session_id, stage="plan_before_llm")
                planner_deadline_sec = max(12, min(int(ai_timeout_sec) + 6, 75))
                summary, commands, raw_output = await asyncio.wait_for(
                    _run_planner_llm(
                        effective_question,
                        session,
                        max_commands=max_commands,
                        ai_timeout_sec=ai_timeout_sec,
                        ai_retries=ai_retries,
                        focus_goals=session.focus_goals,
                        learning_store=(getattr(request.app.state, "learning_store", None) if use_command_library else None),
                        version_map=version_map,
                        sop_hypotheses=(hypotheses if use_sop_library else []),
                        sop_steps=sop_steps,
                        issue_hits=(known_issue_hits if use_known_issues else []),
                        case_hits=(case_hits if use_case_library else []),
                        llm_primary=llm_primary,
                        llm_failover=llm_failover,
                    ),
                    timeout=planner_deadline_sec,
                )
                _raise_if_stop_requested(request, session_id, stage="plan_after_llm")
            except _UserStopRequested as exc:
                manager.set_last_error(session_id, str(exc))
                manager.set_status(session_id, "aborted")
                session = manager.get_session(session_id)
                session = _normalize_session_status_for_ui(manager, session)
                return {
                    "ok": False,
                    "stopped": True,
                    "message": str(exc),
                    "session_id": session_id,
                    "session": (_session_dump_with_next_action(session) if session else None),
                }
            except asyncio.TimeoutError:
                planner_mode = "deterministic_timeout"
                planner_reason = f"planner timeout>{planner_deadline_sec}s"
            except asyncio.CancelledError:
                cancel_msg = "Plan request cancelled/interrupted. Please click Next Step or Generate Plan to retry."
                manager.set_last_error(session_id, cancel_msg)
                manager.set_status(session_id, _ready_for_next_probe_status())
                raise
            except Exception as exc:
                planner_mode = "deterministic_error"
                planner_reason = f"planner error: {exc}"

        if planner_mode != "llm":
            commands = _build_commands_from_sop_steps(
                session=session,
                profile_map=profile_map,
                version_map=version_map,
                sop_steps=sop_steps,
                max_commands=max_commands,
                learning_store=(getattr(request.app.state, "learning_store", None) if use_command_library else None),
            )
            if not commands:
                commands = _fallback_commands(
                    profile_map,
                    max_commands=max_commands,
                    learning_store=(getattr(request.app.state, "learning_store", None) if use_command_library else None),
                    version_map=version_map,
                )
            top = (hypotheses or [{}])[0] if hypotheses else {}
            summary = (
                f"Planner deterministic fallback: {planner_reason}; "
                f"top_hypothesis={top.get('title', '-')} minimal_probe_budget={max_commands}"
            )
            raw_output = json.dumps(
                {
                    "engine": planner_mode,
                    "reason": planner_reason,
                    "summary": summary,
                    "sop_steps": sop_steps,
                    "minimal_probe_budget": max_commands,
                    "target_probe": target_probe,
                },
                ensure_ascii=False,
            )

        plan_perf["planner_llm_ms"] = round((time.perf_counter() - t_planner_llm) * 1000, 1)
        plan_perf["planner_engine"] = {
            "mode": planner_mode,
            "reason": planner_reason,
            "primary_provider": str(llm_primary.get("provider") or "").strip(),
            "primary_model": str(model_used(llm_primary) or "").strip(),
            "failover_provider": (str(llm_failover.get("provider") or "").strip() if llm_failover else ""),
            "failover_model": (str(model_used(llm_failover) or "").strip() if llm_failover else ""),
            "route_diag": planner_route_diag,
        }
    planned_hypotheses = score_hypotheses(
        hypotheses,
        evidence_text="\n".join(f"{c.intent}\n{c.command}\n{c.reason}" for c in commands),
        known_issue_hits=score_priors,
        round_no=(len(getattr(session, "rounds", []) or []) + 1),
        evidence_signals=case_signals,
    )
    _assert_not_stopped(request, session_id, "plan_round")
    stop_decision = build_stop_decision(planned_hypotheses, round_no=(len(getattr(session, "rounds", []) or []) + 1))
    focus_review = _focus_review_from_commands(session.focus_goals, commands)
    validation_task = _merge_validation_task_context(
        validation_task,
        current_probe=target_probe,
        next_probe=_validation_task_to_target_probe(validation_task),
    )
    rnd = manager.append_round(
        session_id,
        planner_summary=summary,
        planner_raw_output=raw_output,
        target_probe=target_probe,
        evidence_overview={
            "validation_task": validation_task,
            "next_target_probe": _validation_task_to_target_probe(validation_task),
        },
        commands=commands,
        focus_review=focus_review,
        hypotheses=planned_hypotheses,
        known_issue_hits=known_issue_hits,
        stop_decision=stop_decision,
    )
    if not rnd:
        raise HTTPException(status_code=500, detail="failed to create round")
    manager.set_status(session_id, "waiting_approval")
    plan_perf["total_ms"] = round((time.perf_counter() - t_plan_total) * 1000, 1)
    plan_perf["requested_max_commands"] = int(requested_max_commands)
    plan_perf["effective_max_commands"] = int(max_commands)
    plan_perf["target_probe"] = target_probe
    plan_perf["validation_task"] = validation_task
    return {
        "ok": True,
        "round": _round_response_payload(rnd),
        "session_id": session_id,
        "case_hits": case_hits[:8],
        "reference_policy": ref_policy,
        "performance": plan_perf,
    }


@router.post("/api/netdiag/sessions/{session_id}/rounds/{round_no}/approve")
async def approve_round(session_id: str, round_no: int, request: Request, payload: dict[str, Any] = Body(default={})):
    approved = bool((payload or {}).get("approved", True))
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    _assert_not_stopped(request, session_id, "approve_round")
    current = manager.get_round(session_id, round_no)
    if not current:
        raise HTTPException(status_code=404, detail="session/round not found")
    current_status = str(getattr(current, "status", "") or "").strip().lower()
    if approved and bool(getattr(current, "approved", False)) and current_status in {
        "waiting_approval",
        "executing",
        "analyzing",
        "completed",
        "failed",
    }:
        manager.set_status(session_id, "waiting_approval")
        return {"ok": True, "already_approved": True, "round": _round_response_payload(current), "message": "round already approved"}

    nxt = _assert_action_allowed(session, {"approve"}, "approve_round")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {
            "ok": True,
            "busy": True,
            "message": "step is running, approval deferred",
            "round": _round_response_payload(current),
        }
    rnd = manager.approve_round(session_id, round_no, approved=approved)
    if not rnd:
        raise HTTPException(status_code=404, detail="session/round not found")
    manager.set_status(session_id, "waiting_approval")
    return {"ok": True, "round": _round_response_payload(rnd)}


@router.post("/api/netdiag/sessions/{session_id}/rounds/{round_no}/execute")
async def execute_round(session_id: str, round_no: int, request: Request):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    _assert_not_stopped(request, session_id, "execute_round")

    rnd = manager.get_round(session_id, round_no)
    if not rnd:
        raise HTTPException(status_code=404, detail="round not found")
    rstatus_now = str(getattr(rnd, "status", "") or "").strip().lower()
    existing_exec = list(getattr(rnd, "executions", []) or [])
    if existing_exec:
        executed_count = 0
        reused_count = 0
        for row in existing_exec:
            if isinstance(row, dict):
                reused = bool(row.get("reused"))
            else:
                reused = bool(getattr(row, "reused", False))
            if reused:
                reused_count += 1
            else:
                executed_count += 1
        if rstatus_now == "executing" and str(getattr(session, "status", "") or "").strip().lower() == "executing":
            return {
                "ok": True,
                "busy": True,
                "message": "execute already running",
                "round": _round_response_payload(rnd),
                "execution_summary": {
                    "executed_count": int(executed_count),
                    "reused_count": int(reused_count),
                    "total_count": int(len(existing_exec)),
                },
            }
        return {
            "ok": True,
            "execution_reused": True,
            "message": "round already has command outputs",
            "round": _round_response_payload(rnd),
            "execution_summary": {
                "executed_count": int(executed_count),
                "reused_count": int(reused_count),
                "total_count": int(len(existing_exec)),
            },
        }
    if not rnd.approved:
        raise HTTPException(status_code=400, detail="round not approved")
    nxt = _assert_action_allowed(session, {"execute"}, "execute_round")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {"ok": True, "busy": True, "message": "step is running, execute deferred", "round": _round_response_payload(rnd)}

    device_inputs = manager.get_device_inputs(session_id)
    device_by_id = {d.device_name or f"dev-{i+1}": d for i, d in enumerate(device_inputs)}
    public_device_ids = [d.device_id for d in session.devices]
    profile_map = _device_profile_map(session)
    learning_store = getattr(request.app.state, "learning_store", None)

    cmd_map: dict[str, list[PlannedCommand]] = {did: [] for did in public_device_ids}
    for cmd in rnd.commands:
        if not is_read_only_command(cmd.command):
            raise HTTPException(status_code=400, detail=f"policy blocked command: {cmd.command}")
        if cmd.device_id == "*":
            for did in public_device_ids:
                cmd_map[did].append(cmd)
        else:
            if cmd.device_id not in cmd_map:
                raise HTTPException(status_code=400, detail=f"unknown device_id in command: {cmd.device_id}")
            cmd_map[cmd.device_id].append(cmd)
    # Per-device dedupe before execution (e.g. wildcard + explicit command overlap).
    for did in list(cmd_map.keys()):
        uniq: list[PlannedCommand] = []
        seen_cmds: set[str] = set()
        for c in cmd_map.get(did, []) or []:
            _, key_cmd = _normalize_cmd_key(did, c.command)
            if not key_cmd or key_cmd in seen_cmds:
                continue
            seen_cmds.add(key_cmd)
            uniq.append(c)
        cmd_map[did] = uniq

    executions: list[CommandExecution] = []
    executed_count = 0
    reused_count = 0
    history_exec_cache = _history_execution_cache(
        session,
        include_current_round=True,
        max_round_no=round_no,
    )
    manager.set_status(session_id, "executing")
    manager.set_round_executions(
        session_id,
        round_no,
        executions=list(getattr(rnd, "executions", []) or []),
        status="executing",
    )

    try:
        for did in public_device_ids:
            _raise_if_stop_requested(request, session_id, stage="execute_round")
            if did not in device_by_id:
                continue
            cmds = cmd_map.get(did, [])
            if not cmds:
                continue
            pending_cmds: list[PlannedCommand] = []
            for c in cmds:
                cmd_key = _normalize_cmd_key(did, c.command)
                cached = history_exec_cache.get(cmd_key)
                if cached:
                    cached_status = str(cached.get("status") or "success").strip().lower()
                    reuse_status = cached_status if cached_status in {"success", "error_output"} else "success"
                    reused_count += 1
                    executions.append(
                        CommandExecution(
                            command_id=(c.command_id or uuid.uuid4().hex[:12]),
                            device_id=did,
                            command=str(c.command or ""),
                            status=reuse_status,
                            reused=True,
                            reused_from_round=(int(cached.get("round_no") or 0) or None),
                            reused_from_command_id=(str(cached.get("command_id") or "").strip() or None),
                            output_file=cached.get("output_file"),
                            error=(cached.get("error") if reuse_status != "success" else None),
                            duration_sec=0.0,
                        )
                    )
                    continue
                pending_cmds.append(c)
            if not pending_cmds:
                continue
            run_dir = manager.output_root / session_id / f"round_{round_no}" / did
            _raise_if_stop_requested(request, session_id, stage="execute_before_device_commands")
            run_res = await run_read_only_commands(
                device=device_by_id[did],
                commands=[c.command for c in pending_cmds],
                output_dir=run_dir,
                timeout_per_command=min(180, max(20, int(session.per_device_timeout))),
                debug_mode=True,
            )
            executed_count += len(pending_cmds)
            for idx, row in enumerate(run_res):
                cmd_ref = pending_cmds[idx] if idx < len(pending_cmds) else None
                out_file = str(row.get("output_file") or "") or None
                command = str(row.get("command") or "")
                status = str(row.get("status") or "failed")
                err = str(row.get("error") or "") or None
                ex_row = CommandExecution(
                    command_id=(cmd_ref.command_id if cmd_ref else uuid.uuid4().hex[:12]),
                    device_id=did,
                    command=command,
                    status=status,
                    reused=False,
                    output_file=out_file,
                    error=err,
                    duration_sec=float(row.get("duration_sec") or 0.0),
                )
                executions.append(ex_row)
                if _is_execution_reusable(ex_row):
                    history_exec_cache[_normalize_cmd_key(did, command)] = {
                        "round_no": round_no,
                        "status": status,
                        "output_file": out_file,
                        "error": err,
                        "duration_sec": float(row.get("duration_sec") or 0.0),
                        "command_id": ex_row.command_id,
                    }
                if learning_store is not None:
                    intent = str((cmd_ref.intent if cmd_ref else "") or "").strip()
                    if not intent:
                        intent = _infer_intent_from_command(command, profile_map.get(did, "unknown"))
                    try:
                        learning_store.record_execution_event(
                            session_id=session_id,
                            round_no=round_no,
                            device_id=did,
                            profile=profile_map.get(did, "unknown"),
                            intent=intent or "manual_command",
                            command=command,
                            status=status,
                            output_text=_read_text_safe(out_file or "", limit=12000),
                            error_text=err or "",
                        )
                    except Exception:
                        pass
        _raise_if_stop_requested(request, session_id, stage="execute_before_commit")
    except _UserStopRequested as exc:
        manager.set_round_executions(session_id, round_no, executions=executions, status="failed")
        manager.set_last_error(session_id, str(exc))
        manager.set_status(session_id, "aborted")
        stopped_round = manager.get_round(session_id, round_no)
        return {
            "ok": False,
            "stopped": True,
            "message": str(exc),
            "round": _round_response_payload(stopped_round),
            "execution_summary": {
                "executed_count": int(executed_count),
                "reused_count": int(reused_count),
                "total_count": int(len(executions)),
            },
        }
    except asyncio.CancelledError:
        cancel_msg = "Execute request cancelled/interrupted. Partial outputs (if any) were saved."
        try:
            manager.set_round_executions(session_id, round_no, executions=executions, status="failed")
        except Exception:
            pass
        manager.set_last_error(session_id, cancel_msg)
        manager.set_status(session_id, _ready_for_next_probe_status())
        raise

    rnd = manager.set_round_executions(session_id, round_no, executions=executions, status="analyzing")
    # Round enters "analyzing" stage, but no AI call is running yet; keep session unblocked for manual next step.
    manager.set_status(session_id, _ready_for_next_probe_status())
    return {
        "ok": True,
        "round": _round_response_payload(rnd),
        "execution_summary": {
            "executed_count": int(executed_count),
            "reused_count": int(reused_count),
            "total_count": int(len(executions)),
        },
    }


@router.post("/api/netdiag/sessions/{session_id}/rounds/{round_no}/analyze")
async def analyze_round(session_id: str, round_no: int, request: Request, payload: dict[str, Any] = Body(default={})):
    manager = request.app.state.diag_session_manager
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    session = _normalize_session_status_for_ui(manager, session)
    _assert_not_stopped(request, session_id, "analyze_round")
    rnd = manager.get_round(session_id, round_no)
    if not rnd:
        raise HTTPException(status_code=404, detail="round not found")
    rstatus_now = str(getattr(rnd, "status", "") or "").strip().lower()
    analysis_now = str(getattr(rnd, "analysis_result", "") or "").strip()
    executions_now = list(getattr(rnd, "executions", []) or [])
    if analysis_now and rstatus_now == "completed":
        return {"ok": True, "analysis_reused": True, "message": "analysis already completed", "round": _round_response_payload(rnd)}
    if str(getattr(session, "status", "") or "").strip().lower() == "analyzing" and rstatus_now == "analyzing" and not analysis_now:
        return {"ok": True, "busy": True, "message": "analyze already running", "round": _round_response_payload(rnd)}
    if not executions_now:
        raise HTTPException(status_code=400, detail="no execution outputs to analyze")
    nxt = _assert_action_allowed(session, {"analyze"}, "analyze_round")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {"ok": True, "busy": True, "message": "step is running, analyze deferred", "round": _round_response_payload(rnd)}

    t_total = time.perf_counter()
    perf: dict[str, Any] = {}
    time_budget_sec = max(
        20,
        min(
            _safe_int((payload or {}).get("max_total_sec"), ANALYZE_HARD_TOTAL_SEC),
            ANALYZE_HARD_TOTAL_SEC,
        ),
    )
    external_timeout_sec = max(
        2,
        min(
            _safe_int((payload or {}).get("external_signal_timeout_sec"), ANALYZE_EXTERNAL_TIMEOUT_SEC),
            20,
        ),
    )
    perf["time_budget_sec"] = time_budget_sec

    def _elapsed_sec() -> float:
        return max(0.0, float(time.perf_counter() - t_total))

    def _remaining_sec(reserve_sec: float = 0.0) -> float:
        return max(0.0, float(time_budget_sec) - _elapsed_sec() - max(0.0, float(reserve_sec)))

    manager.set_status(session_id, "analyzing")
    manager.set_round_executions(
        session_id,
        round_no,
        executions=list(getattr(rnd, "executions", []) or []),
        status="analyzing",
    )
    if _is_stop_requested(request, session_id):
        msg = "stop requested at analyze_start"
        manager.set_round_executions(
            session_id,
            round_no,
            executions=list(getattr(rnd, "executions", []) or []),
            status="failed",
        )
        manager.set_last_error(session_id, msg)
        manager.set_status(session_id, "aborted")
        stopped_round = manager.get_round(session_id, round_no)
        return {"ok": False, "stopped": True, "message": msg, "round": _round_response_payload(stopped_round)}
    ref_policy = _resolve_reference_policy(payload)
    use_known_issues = bool(ref_policy.get("known_issues", True))
    use_case_library = bool(ref_policy.get("case_library", True))
    use_sop_library = bool(ref_policy.get("sop_library", True))
    use_command_library = bool(ref_policy.get("command_library", True))
    llm_route = _resolve_llm_route(payload)
    llm_primary = _build_llm_input(llm_route.get("primary") or None)
    llm_failover = _build_llm_input(llm_route.get("failover") or None) if llm_route.get("failover") else None
    profile_map = _device_profile_map(session)
    version_map = _device_version_map(session)
    llm = dict(llm_primary)
    t_exec_records = time.perf_counter()
    evidence = _load_round_evidence_text(manager.output_root, session_id, round_no)
    exec_records: list[dict[str, Any]] = []
    for ex in rnd.executions or []:
        if not isinstance(ex, CommandExecution):
            continue
        exec_records.append(
            {
                "device_id": ex.device_id,
                "command": ex.command,
                "status": ex.status,
                "error": ex.error or "",
                "output_text": _read_text_safe(ex.output_file or "", limit=30000),
            }
        )
    perf["collect_exec_ms"] = round((time.perf_counter() - t_exec_records) * 1000, 1)

    t_parse_evidence = time.perf_counter()
    parsed_evidence = extract_round_evidence(executions=exec_records, profile_map=profile_map)
    evidence_brief = format_evidence_brief(parsed_evidence, max_signals=12)
    perf["parse_evidence_ms"] = round((time.perf_counter() - t_parse_evidence) * 1000, 1)
    chunk_cfg = (payload or {}).get("evidence_chunking")
    chunk_enabled = True
    chunk_max = ANALYZE_DEFAULT_CHUNK_MAX
    chunk_size = ANALYZE_DEFAULT_CHUNK_SIZE
    chunk_overlap = ANALYZE_DEFAULT_CHUNK_OVERLAP
    if isinstance(chunk_cfg, dict):
        chunk_enabled = bool(chunk_cfg.get("enabled", True))
        chunk_max = max(1, min(_safe_int(chunk_cfg.get("max_chunks"), ANALYZE_DEFAULT_CHUNK_MAX), 24))
        chunk_size = max(400, min(_safe_int(chunk_cfg.get("chunk_size"), ANALYZE_DEFAULT_CHUNK_SIZE), 8000))
        chunk_overlap = max(0, min(_safe_int(chunk_cfg.get("overlap"), ANALYZE_DEFAULT_CHUNK_OVERLAP), chunk_size // 3))
    chunk_context = ""
    chunk_meta: dict[str, Any] = {"enabled": chunk_enabled, "selected_chunks": 0, "total_chunks": 0}
    t_chunk_build = time.perf_counter()
    if chunk_enabled:
        chunk_context, chunk_meta = _build_chunked_execution_context(
            exec_records,
            max_chunks=chunk_max,
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        )
    perf["chunk_build_ms"] = round((time.perf_counter() - t_chunk_build) * 1000, 1)
    state_store = getattr(request.app.state, "state_store", None)
    zabbix_brief = ""
    zabbix_result: dict[str, Any] = {}
    zabbix_signals: list[dict[str, Any]] = []
    config_brief = ""
    config_diff_result: dict[str, Any] = {}
    config_signals: list[dict[str, Any]] = []
    t_external_signals = time.perf_counter()
    zabbix_payload = (payload or {}).get("zabbix_history")
    if not isinstance(zabbix_payload, dict):
        conn_store = getattr(request.app.state, "connection_store", None)
        if isinstance(conn_store, NetdiagConnectionStore):
            conn_cfg = conn_store.get()
            default_host = str(conn_cfg.get("zabbix_default_host") or "").strip()
            default_item = str(conn_cfg.get("zabbix_default_item_key") or "").strip()
            if default_host and default_item:
                zabbix_payload = {
                    "host": default_host,
                    "item_key": default_item,
                    "start_at": session.fault_window.start_at.isoformat(),
                    "end_at": session.fault_window.end_at.isoformat(),
                    "timezone": str(conn_cfg.get("zabbix_default_timezone") or session.fault_window.timezone or "Asia/Singapore"),
                    "limit": max(10, min(5000, _safe_int(conn_cfg.get("zabbix_default_limit"), 600))),
                }
                trend_mode = str(conn_cfg.get("zabbix_default_use_trend") or "auto").strip().lower()
                if trend_mode == "true":
                    zabbix_payload["use_trend"] = True
                elif trend_mode == "false":
                    zabbix_payload["use_trend"] = False
    if isinstance(zabbix_payload, dict):
        query = dict(zabbix_payload)
        query.setdefault("start_at", session.fault_window.start_at.isoformat())
        query.setdefault("end_at", session.fault_window.end_at.isoformat())
        query.setdefault("timezone", session.fault_window.timezone)
        remain_for_external = _remaining_sec(reserve_sec=18.0)
        if remain_for_external < 2.5:
            zabbix_result = {"ok": False, "skipped": True, "error": "skipped: time budget low"}
            zabbix_brief = "zabbix_query_skipped: time budget low"
        else:
            ext_timeout = max(2, min(int(remain_for_external), int(external_timeout_sec)))
            try:
                zabbix_result = await asyncio.wait_for(
                    asyncio.to_thread(_query_zabbix_history_data, request, query),
                    timeout=ext_timeout,
                )
                zabbix_signals = [x for x in (zabbix_result.get("signals") or []) if isinstance(x, dict)]
                z_summary = [str(x) for x in (zabbix_result.get("signal_summary") or []) if str(x).strip()]
                zabbix_brief = "\n".join(z_summary[:12]) if z_summary else f"points_count={zabbix_result.get('points_count', 0)}"
            except asyncio.TimeoutError:
                zabbix_result = {"ok": False, "skipped": True, "error": f"timeout({ext_timeout}s)"}
                zabbix_brief = f"zabbix_query_timeout: {ext_timeout}s"
            except HTTPException as exc:
                zabbix_result = {"ok": False, "error": exc.detail}
                zabbix_brief = f"zabbix_query_error: {exc.detail}"
            except Exception as exc:
                zabbix_result = {"ok": False, "error": str(exc)}
                zabbix_brief = f"zabbix_query_error: {exc}"

    config_payload = (payload or {}).get("config_diff")
    if isinstance(config_payload, dict):
        sid_a = str(config_payload.get("snapshot_id_a") or "").strip()
        sid_b = str(config_payload.get("snapshot_id_b") or "").strip()
        if sid_a and sid_b:
            remain_for_cfg = _remaining_sec(reserve_sec=14.0)
            if remain_for_cfg < 1.5:
                config_diff_result = {
                    "ok": False,
                    "skipped": True,
                    "error": "skipped: time budget low",
                    "snapshot_id_a": sid_a,
                    "snapshot_id_b": sid_b,
                }
                config_brief = "config_diff_skipped: time budget low"
            else:
                cfg_store = getattr(request.app.state, "config_store", None)
                if isinstance(cfg_store, NetdiagConfigStore):
                    try:
                        diff = cfg_store.diff_snapshots(
                            sid_a,
                            sid_b,
                            context=max(0, min(_safe_int(config_payload.get("context"), 3), 12)),
                            max_lines=max(50, min(_safe_int(config_payload.get("max_lines"), 1500), 12000)),
                        )
                        config_signals, cfg_summary = _config_diff_signals(str(diff.get("diff_text") or ""))
                        config_brief = (
                            "\n".join(cfg_summary[:12])
                            if cfg_summary
                            else f"changed_add={diff.get('changed_lines_add')} changed_del={diff.get('changed_lines_del')}"
                        )
                        config_diff_result = {
                            "ok": True,
                            "snapshot_id_a": sid_a,
                            "snapshot_id_b": sid_b,
                            "changed_lines_add": diff.get("changed_lines_add"),
                            "changed_lines_del": diff.get("changed_lines_del"),
                            "summary": cfg_summary[:20],
                        }
                    except Exception as exc:
                        config_diff_result = {"ok": False, "error": str(exc), "snapshot_id_a": sid_a, "snapshot_id_b": sid_b}
                        config_brief = f"config_diff_error: {exc}"
                else:
                    config_diff_result = {"ok": False, "error": "config store unavailable"}
                    config_brief = "config_diff_error: config store unavailable"
        else:
            config_diff_result = {"ok": False, "error": "snapshot_id_a and snapshot_id_b are required"}
            config_brief = "config_diff_error: snapshot ids missing"
    perf["external_signals_ms"] = round((time.perf_counter() - t_external_signals) * 1000, 1)

    combined_signals_base = [*list(parsed_evidence.get("signals") or []), *zabbix_signals, *config_signals]
    merged_domain_delta: dict[str, float] = dict(parsed_evidence.get("domain_delta") or {})
    for sig in [*zabbix_signals, *config_signals]:
        dom = str(sig.get("domain") or "global").strip().lower() or "global"
        pol = str(sig.get("polarity") or "positive").strip().lower()
        wt = _safe_float(sig.get("weight"), 0.0)
        merged_domain_delta[dom] = _safe_float(merged_domain_delta.get(dom), 0.0) + (wt if pol == "positive" else -wt)
    merged_domain_delta = {k: round(v, 4) for k, v in merged_domain_delta.items()}
    direct_evidence_hints = _collect_direct_evidence_hints(combined_signals_base)

    state_ingest_result: dict[str, Any] = {}
    historical_compare: list[dict[str, Any]] = []
    dids = [str(x.device_id or "").strip() for x in (session.devices or []) if str(x.device_id or "").strip()]
    if not dids:
        dids = ["*"]
    t_state_store = time.perf_counter()
    if isinstance(state_store, NetdiagStateStore):
        # Compare first, then ingest current round points.
        for did in dids:
            for dom, cur_delta in merged_domain_delta.items():
                cmp = state_store.baseline_compare(
                    device_id=did,
                    domain=str(dom or "global"),
                    key="domain_delta",
                    current_value=float(cur_delta),
                    history_limit=120,
                )
                if int(cmp.get("history_count") or 0) > 0:
                    historical_compare.append(cmp)

        now_ts = int(datetime.now(timezone.utc).timestamp())
        state_points: list[dict[str, Any]] = []
        health = dict(parsed_evidence.get("command_health") or {})
        total = max(1, _safe_int(health.get("total"), 0))
        valid_rate = _safe_float(health.get("valid_output"), 0.0) / total
        for did in dids:
            state_points.append(
                {
                    "ts": now_ts,
                    "device_id": did,
                    "session_id": session_id,
                    "round_no": round_no,
                    "domain": "global",
                    "key": "command_valid_rate",
                    "value": valid_rate,
                    "unit": "ratio",
                    "source": "round_evidence",
                    "tags": [f"session:{session_id}", f"round:{round_no}"],
                }
            )
            for dom, cur_delta in merged_domain_delta.items():
                state_points.append(
                    {
                        "ts": now_ts,
                        "device_id": did,
                        "session_id": session_id,
                        "round_no": round_no,
                        "domain": str(dom or "global"),
                        "key": "domain_delta",
                        "value": float(cur_delta),
                        "unit": "score",
                        "source": "round_evidence",
                        "tags": [f"session:{session_id}", f"round:{round_no}"],
                    }
                )
        for sig in [*zabbix_signals, *config_signals]:
            dom = str(sig.get("domain") or "global").strip().lower() or "global"
            pol = str(sig.get("polarity") or "positive").strip().lower()
            signed = _safe_float(sig.get("weight"), 0.0) * (1.0 if pol == "positive" else -1.0)
            device_id = str(sig.get("device_id") or "*").strip() or "*"
            source_name = str(sig.get("vendor") or "external").strip().lower()
            for did in (dids if device_id == "*" else [device_id]):
                state_points.append(
                    {
                        "ts": now_ts,
                        "device_id": did,
                        "session_id": session_id,
                        "round_no": round_no,
                        "domain": dom,
                        "key": "external_signal_score",
                        "value": signed,
                        "unit": "score",
                        "source": source_name,
                        "tags": [str(sig.get("signal") or "").strip()],
                    }
                )
        try:
            state_ingest_result = state_store.append_points(state_points)
        except Exception as exc:
            state_ingest_result = {"added": 0, "error": str(exc)}
    perf["state_store_ms"] = round((time.perf_counter() - t_state_store) * 1000, 1)
    evidence_for_match = (
        f"{evidence}\n\n[EvidenceParser]\n{evidence_brief}\n\n"
        + (f"[ZabbixReview]\n{zabbix_brief}\n\n" if zabbix_brief else "")
        + (f"[ConfigDiffReview]\n{config_brief}\n\n" if config_brief else "")
    )
    # Keep reference matching cheap: large text hurts retrieval speed but adds little value.
    if len(evidence_for_match) > 12000:
        evidence_for_match = evidence_for_match[:12000]

    t_reference_lookup = time.perf_counter()
    issue_store = getattr(request.app.state, "known_issue_store", None) if use_known_issues else None
    case_store = getattr(request.app.state, "case_store", None) if use_case_library else None
    issue_hits: list[dict[str, Any]] = []
    case_hits: list[dict[str, Any]] = []
    reference_budget_skipped = False
    reference_budget_sec = _remaining_sec(reserve_sec=12.0)
    if reference_budget_sec < 3.0:
        reference_budget_skipped = True
        issue_brief = "skipped by time budget"
        case_brief = "skipped by time budget"
    else:
        issue_hits = (
            _query_issue_hits(
                issue_store=issue_store,
                profile_map=profile_map,
                version_map=version_map,
                query_text=f"{session.question}\n" + "\n".join(session.focus_goals),
                evidence_text=evidence_for_match,
            )
            if use_known_issues
            else []
        )
        issue_brief = _known_issue_hints_block(issue_hits) if use_known_issues else "disabled by reference policy"
        ranked_domains = [
            str(k).strip().lower()
            for k, v in sorted(merged_domain_delta.items(), key=lambda kv: (-abs(_safe_float(kv[1])), str(kv[0])))
            if abs(_safe_float(v, 0.0)) >= 0.03 and str(k).strip()
        ][:4]
        if not ranked_domains:
            ranked_domains = [str(x.get("domain") or "").strip().lower() for x in derive_domains(session.question, session.focus_goals)]
        case_hits = (
            _query_case_hits(
                case_store=case_store if isinstance(case_store, NetdiagCaseStore) else None,
                profile_map=profile_map,
                query_text=f"{session.question}\n" + "\n".join(session.focus_goals),
                domains=ranked_domains,
                evidence_text=evidence_for_match,
                limit=8,
            )
            if use_case_library
            else []
        )
        case_brief = _case_hints_block(case_hits) if use_case_library else "disabled by reference policy"
    case_priors = _case_hits_to_issue_like(case_hits)
    score_priors = [*issue_hits, *case_priors]
    case_signals = _case_hits_to_signals(case_hits, max_signals=10)
    combined_signals = [*combined_signals_base, *case_signals]
    perf["reference_lookup_ms"] = round((time.perf_counter() - t_reference_lookup) * 1000, 1)
    perf["reference_budget_sec"] = round(reference_budget_sec, 2)
    perf["reference_budget_skipped"] = bool(reference_budget_skipped)
    learning_store = getattr(request.app.state, "learning_store", None) if use_command_library else None
    llm["task_prompt_text"] = (
        llm.get("task_prompt_text", "")
        + "\n\n[NetDiag Judge Task]"
        + "\n你必须输出完整结构，且每个部分都必须有内容，不得留空或只写标题："
        + "\n### 1) 当前判定"
        + "\n### 2) 证据链（每条必须包含 device/time/command/snippet）"
        + "\n### 3) 根因假设与置信度"
        + "\n### 4) 建议后续操作"
        + "\n### 5) 下一轮建议命令（仅 show/display/dis）"
        + "\n### 6) 时间维度校验（故障时间窗 vs 设备时间/时区）"
        + "\n若证据不足，必须明确写：证据不足，需补充采集。"
        + "\n禁止角色漂移为“变更评审”或其他非网络故障诊断角色。"
        + "\n禁止在证据不足时下“无关联/稳定/无故障”强结论。"
        + "\n必须以 Baseline offset/device_range 作为时间比对主依据；不要仅凭 clock 命令里的时区字符串推翻已校准时间窗。"
        + "\n若关键事件时间落在 Baseline 的 device_window 内，必须判定“时间窗已覆盖”，禁止再输出“时间不匹配/无关”结论。"
        + "\n直接证据优先：当存在接口管理状态/日志根因直接证据时，应优先给出证据驱动结论，再决定是否补采。"
        + _build_focus_lock_block(session.focus_goals)
        + "\n要求：明确说明每个 FocusLock Goal 是否已覆盖，未覆盖的给出下一轮补充命令方向。"
    )

    report_text = (
        f"Question:\n{session.question}\n\n"
        f"FaultWindow:\n{session.fault_window.start_at} ~ {session.fault_window.end_at} ({session.fault_window.timezone})\n\n"
        f"Baseline:\n{_baseline_summary_text(session)}\n\n"
        + _build_focus_lock_block(session.focus_goals)
        + "\n\n[KnownIssueHints]\n"
        + (issue_brief if issue_brief else "- none")
        + "\n\n[CaseLibraryHints]\n"
        + (case_brief if case_brief else "- none")
        + "\n\n[StructuredEvidenceSignals]\n"
        + (evidence_brief if evidence_brief else "- none")
        + ("\n\n[ChunkedDeviceOutputEvidence]\n" + chunk_context if chunk_context else "")
        + ("\n\n[ZabbixReview]\n" + zabbix_brief if zabbix_brief else "")
        + ("\n\n[ConfigDiffReview]\n" + config_brief if config_brief else "")
        + (
            "\n\n[DirectEvidenceHints]\n"
            + "\n".join(f"- {x}" for x in direct_evidence_hints)
            if direct_evidence_hints
            else ""
        )
        + ("\n\n[RoundEvidenceExcerpt]\n" + evidence[:3200] if (not chunk_context and evidence) else "")
    )
    if len(report_text) > ANALYZE_REPORT_CHAR_LIMIT:
        report_text = report_text[:ANALYZE_REPORT_CHAR_LIMIT]
    perf["prompt_chars"] = {"task_prompt": len(str(llm.get("task_prompt_text") or "")), "report": len(report_text)}
    requested_ai_timeout_sec = max(
        8,
        min(
            int((payload or {}).get("ai_timeout_sec", ANALYZE_DEFAULT_TIMEOUT_SEC) or ANALYZE_DEFAULT_TIMEOUT_SEC),
            ANALYZE_MAX_TIMEOUT_SEC,
        ),
    )
    ai_retries = max(1, min(int((payload or {}).get("ai_retries", 1) or 1), 3))
    fast_path_enabled = _analysis_fast_path_enabled(payload)
    fast_path_ok, fast_path_reason = _should_use_analysis_fast_path(
        parsed_evidence=parsed_evidence,
        direct_evidence_hints=direct_evidence_hints,
    )
    llm_mode = "model"
    analysis_text = ""
    err = ""
    ai_failed = False
    t_llm = time.perf_counter()
    remaining_for_llm = _remaining_sec(reserve_sec=5.0)
    effective_ai_timeout_sec = max(
        ANALYZE_MIN_LLM_TIMEOUT_SEC,
        min(
            int(max(0.0, remaining_for_llm)),
            int(requested_ai_timeout_sec),
            int(ANALYZE_HARD_TOTAL_SEC),
        ),
    )
    if fast_path_enabled and fast_path_ok:
        llm_mode = "fast_path"
        analysis_text = _build_fastpath_analysis_text(
            session=session,
            round_no=round_no,
            parsed_evidence=parsed_evidence,
            exec_records=exec_records,
            direct_evidence_hints=direct_evidence_hints,
            reason=fast_path_reason,
        )
    elif remaining_for_llm < float(ANALYZE_MIN_LLM_TIMEOUT_SEC):
        ai_failed = True
        llm_mode = "time_budget_fallback"
        err = (
            f"time budget low: elapsed={_elapsed_sec():.1f}s "
            f"remaining={max(0.0, remaining_for_llm):.1f}s"
        )
        analysis_text = f"AI analyze fallback: {err}"
    else:
        if _is_stop_requested(request, session_id):
            msg = "stop requested before llm call"
            manager.set_round_executions(
                session_id,
                round_no,
                executions=list(getattr(rnd, "executions", []) or []),
                status="failed",
            )
            manager.set_last_error(session_id, msg)
            manager.set_status(session_id, "aborted")
            stopped_round = manager.get_round(session_id, round_no)
            return {"ok": False, "stopped": True, "message": msg, "round": _round_response_payload(stopped_round)}
        try:
            # Hard-cap AI response latency to keep UX deterministic.
            llm_deadline_sec = max(ANALYZE_MIN_LLM_TIMEOUT_SEC, int(effective_ai_timeout_sec) + 6)
            analysis_text, err = await asyncio.wait_for(
                _run_llm_text_with_retry(
                    llm_input=llm,
                    report_text=report_text,
                    timeout_sec=effective_ai_timeout_sec,
                    attempts=ai_retries,
                    shrink_on_retry=True,
                    strict_text_check=True,
                    failover_llm_input=llm_failover,
                    failover_attempts=1,
                ),
                timeout=llm_deadline_sec,
            )
        except asyncio.TimeoutError:
            ai_failed = True
            llm_mode = "model_timeout"
            err = (
                f"model no feedback within {max(ANALYZE_MIN_LLM_TIMEOUT_SEC, int(effective_ai_timeout_sec))}s "
                f"(hard deadline {llm_deadline_sec}s)"
            )
            analysis_text = f"AI analyze failed: request timeout: {err}"
        except asyncio.CancelledError:
            cancel_msg = "Analyze request cancelled/interrupted. Please retry AI analyze."
            try:
                manager.set_round_analysis(
                    session_id,
                    round_no,
                    analysis_result=cancel_msg,
                    status="failed",
                )
            except Exception:
                pass
            manager.set_last_error(session_id, cancel_msg)
            manager.set_status(session_id, _ready_for_next_probe_status())
            raise
        if not analysis_text:
            ai_failed = True
            llm_mode = "model_failed"
            msg = err or "unknown error"
            analysis_text = f"AI analyze failed: request failed: {msg}"
    if _is_stop_requested(request, session_id):
        msg = "stop requested after llm call"
        manager.set_round_executions(
            session_id,
            round_no,
            executions=list(getattr(rnd, "executions", []) or []),
            status="failed",
        )
        manager.set_last_error(session_id, msg)
        manager.set_status(session_id, "aborted")
        stopped_round = manager.get_round(session_id, round_no)
        return {"ok": False, "stopped": True, "message": msg, "round": _round_response_payload(stopped_round)}
    perf["llm_ms"] = round((time.perf_counter() - t_llm) * 1000, 1)
    perf["analysis_engine"] = {
        "mode": llm_mode,
        "fast_path_enabled": fast_path_enabled,
        "fast_path_decision": fast_path_reason,
        "requested_timeout_sec": requested_ai_timeout_sec,
        "effective_timeout_sec": effective_ai_timeout_sec,
        "retries": ai_retries,
        "primary_provider": str(llm_primary.get("provider") or "").strip(),
        "primary_model": str(model_used(llm_primary) or "").strip(),
        "failover_provider": (str(llm_failover.get("provider") or "").strip() if llm_failover else ""),
        "failover_model": (str(model_used(llm_failover) or "").strip() if llm_failover else ""),
    }

    cmd_text = "\n".join(f"{c.command}\n{c.reason}" for c in (rnd.commands or []))
    base_hypotheses = list(rnd.hypotheses or [])
    if not base_hypotheses and (getattr(session, "rounds", None) or []):
        try:
            prev = session.rounds[max(0, int(round_no) - 2)]
            base_hypotheses = list(prev.hypotheses or [])
        except Exception:
            base_hypotheses = []
    if not base_hypotheses:
        base_hypotheses = seed_hypotheses(
            question=session.question,
            focus_goals=session.focus_goals,
            known_issue_hits=score_priors,
        )
    updated_hypotheses = score_hypotheses(
        base_hypotheses,
        evidence_text=f"{analysis_text}\n{evidence}\n{cmd_text}",
        known_issue_hits=score_priors,
        round_no=round_no,
        evidence_signals=combined_signals,
        command_health=dict(parsed_evidence.get("command_health") or {}),
    )
    updated_hypotheses = _inject_direct_evidence_hypothesis(
        hypotheses=updated_hypotheses,
        parsed_evidence=parsed_evidence,
        direct_evidence_hints=direct_evidence_hints,
    )
    if ai_failed:
        analysis_text = _deterministic_analysis_fallback(
            error_message=(err or "unknown error"),
            parsed_evidence=parsed_evidence,
            issue_hits=issue_hits,
            hypotheses=updated_hypotheses,
        )
    stop_decision = build_stop_decision(
        updated_hypotheses,
        round_no=round_no,
        max_rounds=int((payload or {}).get("max_rounds", 6) or 6),
    )
    retrospective = build_retrospective(
        round_no=round_no,
        executions=[x.model_dump() for x in (rnd.executions or [])],
        before=base_hypotheses,
        after=updated_hypotheses,
    )

    if evidence_brief:
        analysis_text = f"{analysis_text}\n\n[Evidence Parser]\n{evidence_brief}"
    if chunk_context:
        analysis_text = (
            f"{analysis_text}\n\n[Chunked Upload]\n"
            f"selected_chunks={chunk_meta.get('selected_chunks', 0)} "
            f"total_chunks={chunk_meta.get('total_chunks', 0)} "
            f"chunk_size={chunk_meta.get('chunk_size', chunk_size)} "
            f"max_chunks={chunk_meta.get('max_chunks', chunk_max)}"
        )
    if zabbix_brief:
        analysis_text = f"{analysis_text}\n\n[Zabbix Review]\n{zabbix_brief}"
    if config_brief:
        analysis_text = f"{analysis_text}\n\n[Config Diff Review]\n{config_brief}"
    if historical_compare:
        lines = []
        for row in historical_compare[:12]:
            lines.append(
                f"{row.get('device_id')}/{row.get('domain')}: "
                f"cur={row.get('current')} median={row.get('baseline_median')} "
                f"delta={row.get('delta_vs_median')} significant={row.get('is_significant')}"
            )
        analysis_text = f"{analysis_text}\n\n[Historical Baseline Compare]\n" + "\n".join(lines)
    if use_known_issues and issue_hits:
        analysis_text = f"{analysis_text}\n\n[Known Issue Hit Explain]\n{_known_issue_hints_block(issue_hits)}"
    if use_case_library and case_hits:
        analysis_text = f"{analysis_text}\n\n[Case Library Hit Explain]\n{_case_hints_block(case_hits)}"

    target_probe = _normalize_target_probe(getattr(rnd, "target_probe", {}) or {})
    persisted_validation_task = _round_validation_task(rnd)
    focus_review = _focus_review(session.focus_goals, f"{analysis_text}\n{cmd_text}\n{evidence_brief}")
    expected_signal_review = _expected_signal_review(
        target_probe,
        f"{analysis_text}\n{cmd_text}\n{evidence_brief}\n" + "\n".join(direct_evidence_hints),
        validation_task=persisted_validation_task,
    )
    direct_recommend, direct_reason = _direct_evidence_should_conclude(
        parsed_evidence=parsed_evidence,
        direct_evidence_hints=direct_evidence_hints,
        focus_review=focus_review,
    )
    if direct_recommend and not bool(stop_decision.get("recommend_conclude")):
        stop_decision = {
            **dict(stop_decision or {}),
            "recommend_conclude": True,
            "reason": direct_reason,
            "next_action": "conclude_with_verification",
            "confidence": max(_safe_float(stop_decision.get("confidence"), 0.0), 0.92),
        }
    stop_decision = _apply_expected_signal_stop_decision(
        stop_decision,
        target_probe=target_probe,
        expected_signal_review=expected_signal_review,
        focus_review=focus_review,
        validation_task=persisted_validation_task,
    )
    if bool(session.focus_lock) and focus_review.get("uncovered"):
        uncovered = ", ".join(str(x) for x in focus_review.get("uncovered", []))
        analysis_text = (
            f"{analysis_text}\n\n[FocusLock]\n"
            f"Uncovered goals: {uncovered}\n"
            "请下一轮优先补齐未覆盖目标。"
        )
    if target_probe and list(expected_signal_review.get("expected_signals") or []):
        matched = ", ".join(str(x) for x in (expected_signal_review.get("matched") or [])[:6]) or "-"
        unmatched = ", ".join(str(x) for x in (expected_signal_review.get("unmatched") or [])[:6]) or "-"
        analysis_text = (
            f"{analysis_text}\n\n[Expected Signals]\n"
            f"Matched: {matched}\n"
            f"Unmatched: {unmatched}\n"
            f"Coverage: {expected_signal_review.get('coverage_ratio')}"
        )
    top_h = (updated_hypotheses or [{}])[0] if updated_hypotheses else {}
    if use_sop_library:
        analysis_text = (
            f"{analysis_text}\n\n[SOP Decision]\n"
            f"TopHypothesis: {top_h.get('title')} ({top_h.get('domain')}) score={top_h.get('score')}\n"
            f"RecommendConclude: {stop_decision.get('recommend_conclude')} reason={stop_decision.get('reason')}\n"
            f"Retrospective: success_rate={retrospective.get('execution_success_rate')} score_delta={retrospective.get('top_hypothesis_score_delta')}"
        )
    else:
        analysis_text = f"{analysis_text}\n\n[SOP Decision]\ndisabled by reference policy"
    next_target_probe = _derive_next_target_probe(
        target_probe=target_probe,
        hypotheses=updated_hypotheses,
        stop_decision=stop_decision,
        focus_review=focus_review,
        expected_signal_review=expected_signal_review,
        validation_task=persisted_validation_task,
    )
    validation_task = _merge_validation_task_context(
        _build_validation_task(
            target_probe=target_probe,
            next_target_probe=next_target_probe,
            expected_signal_review=expected_signal_review,
            focus_review=focus_review,
            stop_decision=stop_decision,
        ),
        current_probe=target_probe,
        next_probe=next_target_probe,
        expected_signal_review=expected_signal_review,
        focus_review=focus_review,
        stop_decision=stop_decision,
    )
    quality_reasons = _analysis_quality_reasons(analysis_text)
    if quality_reasons:
        analysis_text = _build_structured_analysis_repair(
            session=session,
            round_no=round_no,
            quality_reasons=quality_reasons,
            original_text=analysis_text,
            exec_records=exec_records,
            parsed_evidence=parsed_evidence,
            hypotheses=updated_hypotheses,
            stop_decision=stop_decision,
            issue_hits=issue_hits,
            case_hits=case_hits,
            focus_review=focus_review,
            profile_map=profile_map,
            version_map=version_map,
            learning_store=learning_store if isinstance(learning_store, NetdiagLearningStore) else None,
        )
        focus_review = _focus_review(session.focus_goals, f"{analysis_text}\n{cmd_text}\n{evidence_brief}")
        next_target_probe = _derive_next_target_probe(
            target_probe=target_probe,
            hypotheses=updated_hypotheses,
            stop_decision=stop_decision,
            focus_review=focus_review,
            expected_signal_review=expected_signal_review,
            validation_task=persisted_validation_task,
        )
        validation_task = _merge_validation_task_context(
            _build_validation_task(
                target_probe=target_probe,
                next_target_probe=next_target_probe,
                expected_signal_review=expected_signal_review,
                focus_review=focus_review,
                stop_decision=stop_decision,
            ),
            current_probe=target_probe,
            next_probe=next_target_probe,
            expected_signal_review=expected_signal_review,
            focus_review=focus_review,
            stop_decision=stop_decision,
        )

    analysis_text = (
        f"{analysis_text}\n\n"
        + _build_round_conclusion_block(
            hypotheses=updated_hypotheses,
            stop_decision=stop_decision,
            focus_review=focus_review,
            retrospective=retrospective,
        )
    )

    perf["total_ms"] = round((time.perf_counter() - t_total) * 1000, 1)
    perf_lines = [f"- {k}={v}" for k, v in perf.items() if k != "prompt_chars" and k != "analysis_engine"]
    perf_lines.append(
        "- analysis_engine="
        + json.dumps(perf.get("analysis_engine", {}), ensure_ascii=False, separators=(",", ":"))
    )
    perf_lines.append(
        "- prompt_chars="
        + json.dumps(perf.get("prompt_chars", {}), ensure_ascii=False, separators=(",", ":"))
    )
    analysis_text = f"{analysis_text}\n\n[Performance]\n" + "\n".join(perf_lines)
    if _is_stop_requested(request, session_id):
        msg = "stop requested before analysis commit"
        manager.set_round_executions(
            session_id,
            round_no,
            executions=list(getattr(rnd, "executions", []) or []),
            status="failed",
        )
        manager.set_last_error(session_id, msg)
        manager.set_status(session_id, "aborted")
        stopped_round = manager.get_round(session_id, round_no)
        return {"ok": False, "stopped": True, "message": msg, "round": (stopped_round.model_dump() if stopped_round else None)}

    rnd = manager.set_round_analysis(
        session_id,
        round_no,
        analysis_result=analysis_text,
        status="completed",
        focus_review=focus_review,
        hypotheses=updated_hypotheses,
        known_issue_hits=issue_hits,
        stop_decision=stop_decision,
        evidence_overview={
            "domain_delta": parsed_evidence.get("domain_delta", {}),
            "merged_domain_delta": merged_domain_delta,
            "command_health": parsed_evidence.get("command_health", {}),
            "summary_lines": parsed_evidence.get("summary_lines", []),
            "chunked_upload": chunk_meta,
            "historical_compare": historical_compare[:30],
            "state_ingest": state_ingest_result,
            "zabbix": {
                "source": zabbix_result.get("source"),
                "points_count": zabbix_result.get("points_count", 0),
                "signal_summary": zabbix_result.get("signal_summary", []),
                "query_ok": bool(zabbix_result.get("ok", False)) if zabbix_result else False,
                "error": zabbix_result.get("error") if zabbix_result else None,
            },
            "config_diff": config_diff_result,
            "case_hits": case_hits[:10],
            "reference_policy": ref_policy,
            "target_probe": target_probe,
            "next_target_probe": next_target_probe,
            "validation_task": validation_task,
            "expected_signal_review": expected_signal_review,
            "performance": perf,
        },
        evidence_signals=combined_signals,
        retrospective=retrospective,
    )
    auto_conclude = bool((payload or {}).get("auto_conclude_if_confident", False))
    if auto_conclude and bool(stop_decision.get("recommend_conclude")):
        manager.set_status(session_id, "concluded")
    else:
        manager.set_status(session_id, _ready_for_next_probe_status())
    return {
        "ok": True,
        "round": _round_response_payload(rnd),
        "stop_decision": stop_decision,
        "retrospective": retrospective,
        "case_hits": case_hits[:8],
        "reference_policy": ref_policy,
        "performance": perf,
    }


@router.post("/api/netdiag/sessions/{session_id}/conclude")
async def conclude_session(session_id: str, request: Request):
    manager = request.app.state.diag_session_manager
    current = manager.get_session(session_id)
    if not current:
        raise HTTPException(status_code=404, detail="session not found")
    current = _normalize_session_status_for_ui(manager, current)
    if str(getattr(current, "status", "") or "").strip().lower() == "concluded":
        return {"ok": True, "already_concluded": True, "session": _session_dump_with_next_action(current)}
    nxt = _assert_action_allowed(current, {"conclude"}, "conclude_session")
    if str(nxt.get("action") or "").strip().lower() == "wait":
        return {"ok": True, "busy": True, "message": "step is running, conclude deferred", "session": _session_dump_with_next_action(current)}
    session = manager.set_status(session_id, "concluded")
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session": _session_dump_with_next_action(session)}
