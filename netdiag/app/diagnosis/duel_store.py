from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def _to_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    text = str(v or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;|]+", text)
    return [p.strip() for p in parts if p.strip()]


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9_/-]{2,}|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    return {w.strip() for w in words if w.strip()}


LAB_FAULT_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "lab_link_flap",
        "name": "Interface flap injection",
        "domain": "link",
        "vendor_profiles": ["cisco_iosxe", "cisco_nxos", "arista_eos", "huawei_vrp"],
        "severity": "medium",
        "reference_root_cause": "access/uplink interface instability",
        "root_cause_keywords": ["flap", "interface", "link", "crc", "error"],
        "expected_signals": ["interface_flap", "interface_error_spike", "packet_loss_indicator"],
        "focus_goals": ["链路抖动", "丢包"],
        "inject_actions": [
            {
                "action_id": "shutdown_cycle",
                "description": "Toggle an interface to create controlled flap.",
                "commands": ["interface {interface}", "shutdown", "no shutdown"],
                "rollback_commands": ["interface {interface}", "no shutdown"],
                "hold_sec": 20,
            }
        ],
    },
    {
        "template_id": "lab_bgp_neighbor_down",
        "name": "BGP neighbor down injection",
        "domain": "routing",
        "vendor_profiles": ["cisco_iosxe", "cisco_nxos", "arista_eos", "huawei_vrp", "paloalto_panos"],
        "severity": "high",
        "reference_root_cause": "BGP peer session instability",
        "root_cause_keywords": ["bgp", "neighbor", "idle", "session", "routing"],
        "expected_signals": ["bgp_peer_down", "routing_session_unstable", "route_withdraw"],
        "focus_goals": ["路由收敛", "BGP 邻居"],
        "inject_actions": [
            {
                "action_id": "peer_shutdown",
                "description": "Temporarily shutdown a BGP neighbor session.",
                "commands": ["router bgp {asn}", "neighbor {peer_ip} shutdown"],
                "rollback_commands": ["router bgp {asn}", "no neighbor {peer_ip} shutdown"],
                "hold_sec": 30,
            }
        ],
    },
    {
        "template_id": "lab_acl_block",
        "name": "ACL deny path injection",
        "domain": "firewall",
        "vendor_profiles": ["cisco_iosxe", "cisco_nxos", "arista_eos", "huawei_vrp", "paloalto_panos"],
        "severity": "high",
        "reference_root_cause": "security policy/ACL deny on critical flow",
        "root_cause_keywords": ["acl", "deny", "policy", "firewall", "session"],
        "expected_signals": ["policy_deny_spike", "session_drop_increase", "flow_unreachable"],
        "focus_goals": ["策略命中", "会话丢弃"],
        "inject_actions": [
            {
                "action_id": "deny_rule_insert",
                "description": "Insert temporary deny rule for a test flow.",
                "commands": ["ip access-list extended {acl_name}", "deny ip host {src_ip} host {dst_ip}"],
                "rollback_commands": ["ip access-list extended {acl_name}", "no deny ip host {src_ip} host {dst_ip}"],
                "hold_sec": 45,
            }
        ],
    },
    {
        "template_id": "lab_cpu_stress_policy",
        "name": "Control-plane CPU pressure injection",
        "domain": "resource",
        "vendor_profiles": ["cisco_iosxe", "cisco_nxos", "arista_eos", "huawei_vrp"],
        "severity": "medium",
        "reference_root_cause": "control-plane resource pressure",
        "root_cause_keywords": ["cpu", "resource", "high load", "latency", "control-plane"],
        "expected_signals": ["cpu_high", "queue_delay", "protocol_keepalive_delay"],
        "focus_goals": ["CPU 高负载", "控制面健康"],
        "inject_actions": [
            {
                "action_id": "rate_limit_disable",
                "description": "Temporarily relax a protection/rate-limit policy in lab to increase load.",
                "commands": ["control-plane", "service-policy input {policy_name}"],
                "rollback_commands": ["control-plane", "no service-policy input {policy_name}"],
                "hold_sec": 40,
            }
        ],
    },
]


class NetdiagDuelStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._save(self._default())

    def _default(self) -> dict[str, Any]:
        return {"schema_version": 1, "duels": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("duels", [])
                return payload
        except Exception:
            pass
        return self._default()

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_templates(self, vendor_profile: str = "", domain: str = "") -> list[dict[str, Any]]:
        vp = str(vendor_profile or "").strip().lower()
        dom = str(domain or "").strip().lower()
        out: list[dict[str, Any]] = []
        for t in LAB_FAULT_TEMPLATES:
            row = dict(t)
            if vp:
                profiles = [str(x).strip().lower() for x in row.get("vendor_profiles", []) if str(x).strip()]
                if profiles and vp not in profiles:
                    continue
            if dom and str(row.get("domain") or "").strip().lower() != dom:
                continue
            out.append(row)
        return out

    def _template_by_id(self, template_id: str) -> dict[str, Any]:
        tid = str(template_id or "").strip()
        if not tid:
            raise ValueError("template_id is required")
        for t in LAB_FAULT_TEMPLATES:
            if str(t.get("template_id") or "").strip() == tid:
                return dict(t)
        raise ValueError(f"template not found: {tid}")

    def _render_action_commands(self, commands: list[str], variables: dict[str, str]) -> list[str]:
        out: list[str] = []
        for cmd in commands:
            text = str(cmd or "")
            placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", text)
            rendered = text
            for key in placeholders:
                val = str(variables.get(key, "")).strip()
                if not val:
                    raise ValueError(f"missing variable: {key}")
                rendered = rendered.replace("{" + key + "}", val)
            out.append(rendered)
        return out

    def create_duel(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = payload or {}
        env_tag = str(row.get("environment_tag") or "").strip().lower()
        if env_tag not in {"lab", "test", "sandbox"}:
            raise ValueError("environment_tag must be lab/test/sandbox")
        mode = str(row.get("mode") or "simulated").strip().lower()
        if mode not in {"simulated", "manual"}:
            raise ValueError("mode must be simulated/manual")

        tpl = self._template_by_id(str(row.get("template_id") or ""))
        variables_raw = row.get("variables") if isinstance(row.get("variables"), dict) else {}
        variables = {str(k).strip(): str(v).strip() for k, v in variables_raw.items() if str(k).strip()}
        target_devices = _to_list(row.get("target_devices"))
        if not target_devices:
            raise ValueError("target_devices is required")

        now = _now_iso()
        duel_id = uuid.uuid4().hex[:14]
        plan: list[dict[str, Any]] = []
        for step in tpl.get("inject_actions", []) or []:
            if not isinstance(step, dict):
                continue
            commands = self._render_action_commands(step.get("commands", []) or [], variables)
            rollback_commands = self._render_action_commands(step.get("rollback_commands", []) or [], variables)
            plan.append(
                {
                    "action_id": str(step.get("action_id") or ""),
                    "description": str(step.get("description") or ""),
                    "commands": commands,
                    "rollback_commands": rollback_commands,
                    "hold_sec": max(1, _safe_int(step.get("hold_sec"), 30)),
                }
            )

        out = {
            "duel_id": duel_id,
            "title": str(row.get("title") or tpl.get("name") or "").strip() or tpl.get("template_id"),
            "environment_tag": env_tag,
            "mode": mode,
            "status": "created",
            "template_id": str(tpl.get("template_id") or ""),
            "template_name": str(tpl.get("name") or ""),
            "domain": str(tpl.get("domain") or ""),
            "vendor_profiles": list(tpl.get("vendor_profiles") or []),
            "severity": str(tpl.get("severity") or "medium"),
            "reference_root_cause": str(tpl.get("reference_root_cause") or ""),
            "root_cause_keywords": list(tpl.get("root_cause_keywords") or []),
            "expected_signals": list(tpl.get("expected_signals") or []),
            "focus_goals": list(tpl.get("focus_goals") or []),
            "target_devices": target_devices,
            "variables": variables,
            "inject_plan": plan,
            "inject_result": {},
            "blue_session_id": "",
            "blue_round_no": 0,
            "judge_result": {},
            "case_result": {},
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("duels", []) if isinstance(x, dict)]
            rows.append(out)
            data["duels"] = rows
            self._save(data)
        return out

    def list_duels(self, status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        st = str(status or "").strip().lower()
        with self._lock:
            rows = [x for x in self._load().get("duels", []) if isinstance(x, dict)]
        if st:
            rows = [x for x in rows if str(x.get("status") or "").strip().lower() == st]
        rows.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        return rows[: max(1, min(int(limit), 5000))]

    def get_duel(self, duel_id: str) -> dict[str, Any] | None:
        did = str(duel_id or "").strip()
        if not did:
            return None
        with self._lock:
            for row in self._load().get("duels", []):
                if not isinstance(row, dict):
                    continue
                if str(row.get("duel_id") or "") == did:
                    return row
        return None

    def delete_duel(self, duel_id: str) -> bool:
        did = str(duel_id or "").strip()
        if not did:
            return False
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("duels", []) if isinstance(x, dict)]
            nxt = [x for x in rows if str(x.get("duel_id") or "") != did]
            changed = len(nxt) != len(rows)
            if changed:
                data["duels"] = nxt
                self._save(data)
            return changed

    def _update(self, duel_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        did = str(duel_id or "").strip()
        if not did:
            return None
        with self._lock:
            data = self._load()
            rows = [x for x in data.get("duels", []) if isinstance(x, dict)]
            for idx, row in enumerate(rows):
                if str(row.get("duel_id") or "") != did:
                    continue
                updated = dict(row)
                updated.update(patch or {})
                updated["updated_at"] = _now_iso()
                rows[idx] = updated
                data["duels"] = rows
                self._save(data)
                return updated
        return None

    def set_inject_result(self, duel_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        row = self.get_duel(duel_id)
        if not row:
            return None
        mode = str(row.get("mode") or "simulated").strip().lower()
        if mode == "simulated":
            detail = {
                "ok": True,
                "mode": "simulated",
                "detail": str((payload or {}).get("detail") or "simulated fault injected"),
                "at": _now_iso(),
            }
        else:
            detail = {
                "ok": bool((payload or {}).get("ok", False)),
                "mode": "manual",
                "detail": str((payload or {}).get("detail") or "manual injection confirmed"),
                "operator": str((payload or {}).get("operator") or "").strip(),
                "at": _now_iso(),
            }
        status = "injected" if bool(detail.get("ok")) else "failed"
        return self._update(duel_id, {"inject_result": detail, "status": status})

    def bind_blue_session(self, duel_id: str, session_id: str, round_no: int = 0) -> dict[str, Any] | None:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        return self._update(
            duel_id,
            {
                "blue_session_id": sid,
                "blue_round_no": max(0, int(round_no)),
                "status": "diagnosing",
            },
        )

    def judge_duel(self, duel_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        row = self.get_duel(duel_id)
        if not row:
            return None
        body = payload or {}
        predicted_domain = str(body.get("predicted_domain") or "").strip().lower()
        predicted_root_cause = str(body.get("predicted_root_cause") or "").strip()
        evidence_signals = [str(x).strip().lower() for x in _to_list(body.get("evidence_signals"))]
        confidence = max(0.0, min(1.0, _safe_float(body.get("confidence"), 0.0)))
        recovery_verified = bool(body.get("recovery_verified", False))
        expected_signals = [str(x).strip().lower() for x in row.get("expected_signals", []) if str(x).strip()]
        root_tokens = _tokenize(predicted_root_cause)
        ref_tokens = _tokenize(str(row.get("reference_root_cause") or "") + " " + " ".join(row.get("root_cause_keywords", []) or []))

        score = 0.0
        reasons: list[str] = []
        if predicted_domain and predicted_domain == str(row.get("domain") or "").strip().lower():
            score += 40.0
            reasons.append("domain matched")
        root_overlap = sorted(root_tokens.intersection(ref_tokens))
        if root_overlap:
            score += min(30.0, 8.0 + (5.0 * len(root_overlap)))
            reasons.append("root_cause tokens matched: " + ",".join(root_overlap[:6]))
        sig_overlap = sorted(set(evidence_signals).intersection(set(expected_signals)))
        if sig_overlap:
            score += min(20.0, 6.0 + (4.0 * len(sig_overlap)))
            reasons.append("signal overlap: " + ",".join(sig_overlap[:6]))
        if confidence >= 0.7:
            score += 6.0
            reasons.append(f"confidence={confidence:.2f}")
        if recovery_verified:
            score += 4.0
            reasons.append("recovery verified")
        verdict = "pass" if score >= 70.0 else "fail"

        judge_result = {
            "score": round(max(0.0, min(100.0, score)), 2),
            "verdict": verdict,
            "predicted_domain": predicted_domain,
            "predicted_root_cause": predicted_root_cause,
            "confidence": confidence,
            "evidence_signals": evidence_signals,
            "signal_overlap": sig_overlap,
            "root_token_overlap": root_overlap,
            "recovery_verified": recovery_verified,
            "reason": "; ".join(reasons) if reasons else "insufficient aligned evidence",
            "evaluated_at": _now_iso(),
        }
        status = "judged" if verdict == "pass" else "diagnosing"
        return self._update(duel_id, {"judge_result": judge_result, "status": status})

    def mark_rolled_back(self, duel_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = payload or {}
        result = {
            "ok": bool(body.get("ok", True)),
            "detail": str(body.get("detail") or "rollback completed"),
            "operator": str(body.get("operator") or "").strip(),
            "at": _now_iso(),
        }
        return self._update(duel_id, {"rollback_result": result, "status": "rolled_back" if result["ok"] else "failed"})

    def set_case_result(self, duel_id: str, case_result: dict[str, Any]) -> dict[str, Any] | None:
        row = self._update(duel_id, {"case_result": dict(case_result or {})})
        if not row:
            return None
        if bool(case_result.get("ok")):
            row = self._update(duel_id, {"status": "promoted"})
        return row
