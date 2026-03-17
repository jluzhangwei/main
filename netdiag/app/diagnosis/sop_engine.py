from __future__ import annotations

import math
import re
import uuid
from typing import Any

from .intent_catalog import allowed_intents_for_profile


DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "link": (
        "link",
        "interface",
        "port",
        "flap",
        "up/down",
        "抖动",
        "链路",
        "丢包",
        "drop",
        "crc",
        "discard",
    ),
    "routing": (
        "route",
        "routing",
        "bgp",
        "ospf",
        "neighbor",
        "adjacency",
        "路由",
        "邻居",
        "会话",
    ),
    "resource": (
        "cpu",
        "memory",
        "resource",
        "latency",
        "delay",
        "slow",
        "时延",
        "高负载",
        "拥塞",
    ),
    "firewall": (
        "firewall",
        "session",
        "security",
        "policy",
        "nat",
        "防火墙",
        "会话表",
        "安全策略",
    ),
    "clock": (
        "clock",
        "time",
        "timezone",
        "ntp",
        "timestamp",
        "时间",
        "时区",
        "时钟",
    ),
}

DOMAIN_HYPOTHESIS_TEMPLATES: dict[str, str] = {
    "link": "Interface instability or physical/optic issue",
    "routing": "Routing protocol/session instability",
    "resource": "Device resource pressure impacting forwarding/control-plane",
    "firewall": "Firewall/session-policy path constraint",
    "clock": "Time skew affecting event correlation",
}

DOMAIN_INTENT_PIPELINE: dict[str, list[str]] = {
    "clock": ["clock_check", "version_check", "system_log_recent"],
    "link": ["interface_summary", "interface_errors", "system_log_recent", "cpu_health"],
    "routing": ["routing_summary", "ospf_neighbor", "bgp_summary", "system_log_recent"],
    "resource": ["cpu_health", "memory_health", "system_log_recent", "interface_summary"],
    "firewall": ["pan_session_stats", "interface_summary", "system_log_recent", "cpu_health"],
}

HYPOTHESIS_SIGNALS: dict[str, dict[str, tuple[str, ...]]] = {
    "link": {
        "positive": ("flap", "down", "crc", "input error", "drops", "discard", "line protocol down", "接口down", "丢包"),
        "negative": ("0 error", "no error", "up up", "stable", "无错误"),
    },
    "routing": {
        "positive": ("bgp down", "idle", "ospf down", "neighbor down", "route withdraw", "邻居断开"),
        "negative": ("established", "full", "stable", "邻居正常"),
    },
    "resource": {
        "positive": ("cpu utilization", "cpu%", "memory utilization", "high cpu", "out of memory", "资源告警"),
        "negative": ("cpu 1 minute", "normal", "idle", "资源正常"),
    },
    "firewall": {
        "positive": ("session exhausted", "policy deny", "drop", "threat", "会话不足", "策略拒绝"),
        "negative": ("allow", "normal", "session available"),
    },
    "clock": {
        "positive": ("clock is unsynchronized", "ntp unsynced", "time drift", "时间偏差"),
        "negative": ("clock synchronized", "ntp synchronized", "时间正常"),
    },
}

INTENT_SIGNAL_HINTS: dict[str, tuple[str, ...]] = {
    "system_log_recent": ("log", "日志", "event", "告警", "stp", "mstp", "shutdown"),
    "interface_summary": ("interface", "port", "接口", "端口", "admin", "shutdown", "config", "配置", "up", "down"),
    "interface_errors": ("crc", "error", "discard", "drop", "丢包", "错误", "计数"),
    "clock_check": ("clock", "time", "ntp", "时钟", "时间"),
    "cpu_health": ("cpu", "memory", "resource", "资源"),
    "memory_health": ("memory", "resource", "资源"),
    "routing_summary": ("route", "routing", "路由"),
    "ospf_neighbor": ("ospf", "neighbor", "邻居"),
    "bgp_summary": ("bgp", "neighbor", "邻居"),
}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _clamp01(v: float) -> float:
    return max(0.01, min(0.99, float(v)))


def _pick_expected_signal_for_intent(intent: str, probe: dict[str, Any]) -> str:
    intent_key = str(intent or "").strip()
    if not intent_key:
        return ""
    evidence = [
        *[str(x).strip() for x in (probe.get("expected_evidence") or []) if str(x).strip()],
        *[str(x).strip() for x in (probe.get("expected_signals") or []) if str(x).strip()],
    ]
    if not evidence:
        return ""
    hints = [str(x).strip().lower() for x in INTENT_SIGNAL_HINTS.get(intent_key, ()) if str(x).strip()]
    if not hints:
        return evidence[0]
    for signal in evidence:
        low = signal.lower()
        if any(h in low for h in hints):
            return signal
    return evidence[0]


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z0-9_/-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    return {t.strip().lower() for t in tokens if t.strip()}


def derive_domains(question: str, focus_goals: list[str] | None = None) -> list[dict[str, Any]]:
    text = f"{question}\n" + "\n".join(focus_goals or [])
    low = text.lower()
    scored: list[tuple[str, float]] = []
    for domain, words in DOMAIN_KEYWORDS.items():
        hit = sum(1 for w in words if w.lower() in low)
        if hit > 0:
            scored.append((domain, float(hit)))
    if not scored:
        scored = [("link", 1.0), ("routing", 0.8), ("resource", 0.6)]
    scored.sort(key=lambda x: (-x[1], x[0]))
    max_score = scored[0][1] if scored else 1.0
    out = []
    for name, raw in scored[:4]:
        out.append({"domain": name, "score": round(raw / max_score, 3)})
    return out


def seed_hypotheses(
    *,
    question: str,
    focus_goals: list[str] | None = None,
    known_issue_hits: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    domains = derive_domains(question, focus_goals)
    out: list[dict[str, Any]] = []
    for idx, d in enumerate(domains, start=1):
        name = str(d.get("domain") or "link")
        base = _safe_float(d.get("score"), 0.4)
        score = round(_clamp01(0.30 + (0.35 * base)), 4)
        out.append(
            {
                "hypothesis_id": f"hyp-{idx}-{uuid.uuid4().hex[:6]}",
                "title": DOMAIN_HYPOTHESIS_TEMPLATES.get(name, "Network fault candidate"),
                "domain": name,
                "source": "heuristic",
                "score": score,
                "confidence": round(score, 4),
                "status": "possible",
                "evidence_for": [],
                "evidence_against": [],
                "next_intents": list(DOMAIN_INTENT_PIPELINE.get(name, [])),
            }
        )

    for item in known_issue_hits or []:
        issue_id = str(item.get("issue_id") or "").strip()
        if not issue_id:
            continue
        title = str(item.get("title") or "").strip() or f"Known issue {issue_id}"
        diag_intents = item.get("diag_intents") if isinstance(item.get("diag_intents"), list) else []
        issue_score = _clamp01(0.50 + (_safe_float(item.get("score"), 0.0) / 10.0))
        out.append(
            {
                "hypothesis_id": f"ki-{issue_id}",
                "title": title,
                "domain": str(item.get("domain") or "known_issue"),
                "source": "known_issue",
                "issue_id": issue_id,
                "score": round(issue_score, 4),
                "confidence": round(issue_score, 4),
                "status": "possible",
                "evidence_for": [f"Matched known issue: {issue_id}"],
                "evidence_against": [],
                "next_intents": [str(x).strip() for x in diag_intents if str(x).strip()],
            }
        )
    return rank_hypotheses(out)


def rank_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hypotheses:
        if not isinstance(h, dict):
            continue
        hid = str(h.get("hypothesis_id") or "").strip()
        if not hid:
            hid = f"hyp-{uuid.uuid4().hex[:8]}"
        if hid in seen:
            continue
        seen.add(hid)
        row = dict(h)
        score = _clamp01(_safe_float(row.get("score"), 0.3))
        row["score"] = round(score, 4)
        row["confidence"] = round(score, 4)
        if score >= 0.80:
            row["status"] = "likely"
        elif score >= 0.50:
            row["status"] = "possible"
        else:
            row["status"] = "weak"
        if not isinstance(row.get("evidence_for"), list):
            row["evidence_for"] = []
        if not isinstance(row.get("evidence_against"), list):
            row["evidence_against"] = []
        if not isinstance(row.get("next_intents"), list):
            row["next_intents"] = []
        items.append(row)
    items.sort(key=lambda x: (-_safe_float(x.get("score"), 0.0), str(x.get("title") or "")))
    return items


def score_hypotheses(
    hypotheses: list[dict[str, Any]],
    *,
    evidence_text: str,
    known_issue_hits: list[dict[str, Any]] | None = None,
    round_no: int = 1,
    evidence_signals: list[dict[str, Any]] | None = None,
    command_health: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    low = str(evidence_text or "").lower()
    updated: list[dict[str, Any]] = []
    boosts: dict[str, float] = {}
    for hit in known_issue_hits or []:
        iid = str(hit.get("issue_id") or "").strip()
        if iid:
            boosts[iid] = _safe_float(hit.get("score"), 0.0) / 12.0

    sig_rows = [x for x in (evidence_signals or []) if isinstance(x, dict)]
    health = command_health if isinstance(command_health, dict) else {}
    total_cmd = max(0, int(_safe_float(health.get("total"), 0.0)))
    valid_cmd = max(0, int(_safe_float(health.get("valid_output"), 0.0)))
    valid_rate = (valid_cmd / total_cmd) if total_cmd > 0 else 1.0

    for raw in hypotheses:
        h = dict(raw or {})
        score = _safe_float(h.get("score"), 0.3)
        domain = str(h.get("domain") or "").strip().lower()
        signals = HYPOTHESIS_SIGNALS.get(domain, {})
        pos = signals.get("positive", ())
        neg = signals.get("negative", ())

        pos_hits = [w for w in pos if w and w.lower() in low]
        neg_hits = [w for w in neg if w and w.lower() in low]
        if pos_hits:
            score += min(0.28, 0.07 * len(pos_hits))
            for p in pos_hits[:3]:
                h.setdefault("evidence_for", []).append(f"signal+ {p}")
        if neg_hits:
            score -= min(0.25, 0.08 * len(neg_hits))
            for n in neg_hits[:3]:
                h.setdefault("evidence_against", []).append(f"signal- {n}")

        issue_id = str(h.get("issue_id") or "").strip()
        if issue_id and issue_id in boosts:
            score += min(0.22, boosts[issue_id])
            h.setdefault("evidence_for", []).append(f"known_issue_boost {issue_id}")

        for sig in sig_rows:
            sig_domain = str(sig.get("domain") or "global").strip().lower()
            if sig_domain not in {"global", domain}:
                continue
            polarity = str(sig.get("polarity") or "positive").strip().lower()
            weight = max(0.0, min(0.24, _safe_float(sig.get("weight"), 0.0)))
            name = str(sig.get("signal") or "signal").strip() or "signal"
            detail = str(sig.get("detail") or "").strip()
            if polarity == "negative":
                score -= weight
                h.setdefault("evidence_against", []).append(f"evidence- {name} {detail}".strip())
            else:
                score += weight
                h.setdefault("evidence_for", []).append(f"evidence+ {name} {detail}".strip())

        # Penalize convergence confidence if command validity is low.
        if total_cmd > 0 and valid_rate < 0.60:
            penalty = min(0.16, (0.60 - valid_rate) * 0.32)
            score -= penalty
            h.setdefault("evidence_against", []).append(f"low_command_valid_rate {valid_rate:.2f}")
        elif total_cmd > 0 and valid_rate >= 0.90:
            score += 0.02
            h.setdefault("evidence_for", []).append(f"high_command_valid_rate {valid_rate:.2f}")

        # Small annealing: later rounds should converge.
        score += min(0.05, 0.01 * max(0, int(round_no) - 1))

        h["score"] = round(_clamp01(score), 4)
        updated.append(h)
    return rank_hypotheses(updated)


def propose_sop_steps(
    *,
    round_no: int,
    profile_map: dict[str, str],
    hypotheses: list[dict[str, Any]],
    max_steps: int,
    target_probe: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cap = max(1, min(int(max_steps), 12))
    if not profile_map:
        profile_map = {"*": "unknown"}

    ranked = rank_hypotheses(hypotheses)
    probe = dict(target_probe or {}) if isinstance(target_probe, dict) else {}
    # Agent mode: prioritize the top hypothesis first, only expand to the
    # second candidate when later rounds or wider budget justify it.
    hypothesis_budget = 1
    if int(round_no) >= 3 and cap >= 3:
        hypothesis_budget = 2
    top = ranked[:hypothesis_budget]
    intents_by_priority: list[str] = []
    preferred_intents = [str(i).strip() for i in (probe.get("preferred_intents") or []) if str(i).strip()]
    if cap <= 2:
        preferred_intents = preferred_intents[:2]
    for intent in preferred_intents:
        if intent not in intents_by_priority:
            intents_by_priority.append(intent)
    for h in top:
        next_intents = [str(i).strip() for i in (h.get("next_intents", []) or []) if str(i).strip()]
        if cap <= 2:
            next_intents = next_intents[:2]
        for i in next_intents:
            ii = str(i).strip()
            if ii and ii not in intents_by_priority:
                intents_by_priority.append(ii)
        domain = str(h.get("domain") or "").strip().lower()
        fallback_domain_intents = list(DOMAIN_INTENT_PIPELINE.get(domain, []))
        if cap <= 2:
            fallback_domain_intents = fallback_domain_intents[:2]
        for i in fallback_domain_intents:
            if i not in intents_by_priority:
                intents_by_priority.append(i)

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for did, profile in profile_map.items():
        allowed = set(allowed_intents_for_profile(profile))
        for intent in intents_by_priority:
            if intent not in allowed:
                continue
            key = (did, intent)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "device_id": did,
                    "intent": intent,
                    "reason": (
                        f"SOP round-{round_no} from target_probe"
                        if intent in preferred_intents
                        else f"SOP round-{round_no} from top hypotheses"
                    ),
                    "expected_signal": _pick_expected_signal_for_intent(intent, probe),
                    "source": "sop_engine",
                }
            )
            if len(out) >= cap:
                return out
    return out


def build_stop_decision(
    hypotheses: list[dict[str, Any]],
    *,
    round_no: int,
    max_rounds: int = 6,
) -> dict[str, Any]:
    ranked = rank_hypotheses(hypotheses)
    if not ranked:
        return {
            "recommend_conclude": False,
            "reason": "No hypothesis yet",
            "top_hypothesis": None,
            "confidence": 0.0,
            "next_action": "collect_more_evidence",
        }

    top = ranked[0]
    top_score = _safe_float(top.get("score"), 0.0)
    second_score = _safe_float(ranked[1].get("score"), 0.0) if len(ranked) > 1 else 0.0
    gap = top_score - second_score
    certainty = _clamp01(1.0 / (1.0 + math.exp(-8.0 * (top_score - 0.5))))
    recommend = bool(top_score >= 0.82 and gap >= 0.10) or int(round_no) >= int(max_rounds)
    reason = (
        f"top_score={top_score:.3f}, gap={gap:.3f}"
        if not recommend
        else f"Converged: top_score={top_score:.3f}, gap={gap:.3f}"
    )
    if int(round_no) >= int(max_rounds):
        reason = f"Reached max rounds {max_rounds}, force summarize with best available evidence"

    return {
        "recommend_conclude": recommend,
        "reason": reason,
        "top_hypothesis": {
            "hypothesis_id": top.get("hypothesis_id"),
            "title": top.get("title"),
            "domain": top.get("domain"),
            "source": top.get("source"),
        },
        "confidence": round(certainty, 4),
        "next_action": "conclude_with_verification" if recommend else "next_round_targeted_checks",
    }


def build_retrospective(
    *,
    round_no: int,
    executions: list[dict[str, Any]],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(executions or [])
    ok = sum(1 for x in (executions or []) if str(x.get("status") or "").lower() == "success")
    before_rank = rank_hypotheses(before)
    after_rank = rank_hypotheses(after)
    before_top = _safe_float(before_rank[0].get("score"), 0.0) if before_rank else 0.0
    after_top = _safe_float(after_rank[0].get("score"), 0.0) if after_rank else 0.0
    delta = round(after_top - before_top, 4)
    return {
        "round_no": int(round_no),
        "executed_commands": total,
        "execution_success_rate": round((ok / total), 4) if total > 0 else 0.0,
        "top_hypothesis_score_delta": delta,
        "learning_note": (
            "Evidence strengthened the leading hypothesis"
            if delta > 0.04
            else "Hypothesis not converging; narrow scope or switch domain checks"
        ),
    }
