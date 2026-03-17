from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from ..models import DeviceInput
from .models import (
    CommandExecution,
    DiagnosisRound,
    DiagnosisSessionCreate,
    DiagnosisSessionRecord,
    PlannedCommand,
    SessionDevicePublic,
)


def _now_s() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_focus_goals(goals: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for g in goals or []:
        text = str(g or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:240])
    return out


def _normalize_session_status_value(status: str | None) -> str:
    raw = str(status or "").strip().lower()
    if raw == "need_next_round":
        return "ready_for_next_probe"
    return raw or "draft"


class DiagnosisSessionManager:
    """Session state holder for NetDiag.

    - Public records are safe for API responses.
    - Device credentials are kept in a private in-memory map.
    """

    def __init__(self, output_root: str = "./output/netdiag_sessions") -> None:
        self._lock = threading.Lock()
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._state_file = self.output_root / "_session_state.json"
        self._sessions: dict[str, DiagnosisSessionRecord] = {}
        self._device_inputs: dict[str, list[DeviceInput]] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            sessions_raw = payload.get("sessions") if isinstance(payload, dict) else {}
            device_inputs_raw = payload.get("device_inputs") if isinstance(payload, dict) else {}
            if not isinstance(sessions_raw, dict):
                sessions_raw = {}
            if not isinstance(device_inputs_raw, dict):
                device_inputs_raw = {}

            loaded_sessions: dict[str, DiagnosisSessionRecord] = {}
            loaded_inputs: dict[str, list[DeviceInput]] = {}

            for sid, row in sessions_raw.items():
                try:
                    normalized_row = dict(row or {})
                    normalized_row["status"] = _normalize_session_status_value(normalized_row.get("status"))
                    rec = DiagnosisSessionRecord(**normalized_row)
                    rec.status = _normalize_session_status_value(getattr(rec, "status", "draft"))  # type: ignore[assignment]
                    loaded_sessions[str(sid)] = rec
                except Exception:
                    continue
            for sid, rows in device_inputs_raw.items():
                if not isinstance(rows, list):
                    continue
                parsed: list[DeviceInput] = []
                for item in rows:
                    try:
                        parsed.append(DeviceInput(**dict(item or {})))
                    except Exception:
                        continue
                loaded_inputs[str(sid)] = parsed

            with self._lock:
                self._sessions = loaded_sessions
                self._device_inputs = loaded_inputs
        except Exception:
            # Keep empty in-memory state if persisted state is invalid.
            return

    def _persist_state_unlocked(self) -> None:
        try:
            payload = {
                "saved_at": _now_s(),
                "sessions": {sid: row.model_dump(mode="json") for sid, row in self._sessions.items()},
                "device_inputs": {
                    sid: [dev.model_dump(mode="json") for dev in rows]
                    for sid, rows in self._device_inputs.items()
                },
            }
            tmp = self._state_file.with_name(self._state_file.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._state_file)
        except Exception:
            # Persistence errors should not break diagnosis flow.
            return

    def _session_dir(self, session_id: str) -> Path:
        d = self.output_root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def create_session(self, payload: DiagnosisSessionCreate) -> DiagnosisSessionRecord:
        if payload.fault_window.end_at < payload.fault_window.start_at:
            raise ValueError("fault_window.end_at must be >= fault_window.start_at")

        session_id = uuid.uuid4().hex[:12]
        now = _now_s()

        public_devices: list[SessionDevicePublic] = []
        private_devices: list[DeviceInput] = []
        for idx, d in enumerate(payload.devices):
            did = (d.device_id or f"dev-{idx + 1}").strip()
            if not did:
                did = f"dev-{idx + 1}"
            vendor_hint = (d.vendor_hint or "").strip().lower() or None
            if vendor_hint not in {"cisco", "arista", "huawei", "paloalto", "unknown", None}:
                vendor_hint = None
            public_devices.append(
                SessionDevicePublic(
                    device_id=did,
                    device_ip=d.device_ip,
                    device_port=d.device_port,
                    username=d.username,
                    vendor_hint=vendor_hint,
                    jump_mode=d.jump_mode,
                    jump_host=d.jump_host,
                    jump_port=d.jump_port,
                    smc_command=d.smc_command,
                )
            )
            private_devices.append(
                DeviceInput(
                    device_ip=d.device_ip,
                    device_port=d.device_port,
                    username=d.username,
                    password=d.password,
                    vendor_hint=vendor_hint,
                    jump_mode=d.jump_mode,
                    jump_host=d.jump_host,
                    jump_port=d.jump_port,
                    smc_command=d.smc_command,
                    device_name=did,
                )
            )

        record = DiagnosisSessionRecord(
            session_id=session_id,
            question=payload.question,
            fault_window=payload.fault_window,
            status="draft",
            context_lines=max(1, int(payload.context_lines)),
            per_device_timeout=max(10, int(payload.per_device_timeout)),
            devices=public_devices,
            focus_goals=_normalize_focus_goals(payload.focus_goals),
            focus_lock=bool(payload.focus_lock),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[session_id] = record
            self._device_inputs[session_id] = private_devices
            self._persist_state_unlocked()
        self._session_dir(session_id)
        return record

    def list_sessions(self) -> list[DiagnosisSessionRecord]:
        with self._lock:
            # Order by latest activity first so recently stopped/resumed sessions
            # appear in "History Sessions" immediately after state transitions.
            return sorted(
                self._sessions.values(),
                key=lambda x: (str(x.updated_at or ""), str(x.created_at or "")),
                reverse=True,
            )

    def get_session(self, session_id: str) -> DiagnosisSessionRecord | None:
        with self._lock:
            return self._sessions.get(session_id)

    def get_device_inputs(self, session_id: str) -> list[DeviceInput]:
        with self._lock:
            return list(self._device_inputs.get(session_id, []))

    def set_status(self, session_id: str, status: str) -> DiagnosisSessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.status = _normalize_session_status_value(status)  # type: ignore[assignment]
            record.updated_at = _now_s()
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return record

    def set_last_error(self, session_id: str, message: str) -> DiagnosisSessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.last_error = str(message or "")
            record.updated_at = _now_s()
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return record

    def set_time_calibration(self, session_id: str, items: list[dict]) -> DiagnosisSessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.time_calibration = list(items or [])
            record.updated_at = _now_s()
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return record

    def set_focus_goals(self, session_id: str, goals: list[str]) -> DiagnosisSessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.focus_goals = _normalize_focus_goals(goals)
            record.updated_at = _now_s()
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return record

    def append_focus_goals(self, session_id: str, goals: list[str]) -> DiagnosisSessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            merged = _normalize_focus_goals([*record.focus_goals, *(goals or [])])
            record.focus_goals = merged
            record.updated_at = _now_s()
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return record

    def append_round(
        self,
        session_id: str,
        planner_summary: str,
        planner_raw_output: str,
        commands: list[PlannedCommand],
        target_probe: dict | None = None,
        evidence_overview: dict | None = None,
        focus_review: dict | None = None,
        hypotheses: list[dict] | None = None,
        known_issue_hits: list[dict] | None = None,
        stop_decision: dict | None = None,
    ) -> DiagnosisRound | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            no = len(record.rounds) + 1
            now = _now_s()
            rnd = DiagnosisRound(
                round_no=no,
                status="waiting_approval",
                planner_summary=planner_summary,
                planner_raw_output=planner_raw_output,
                target_probe=dict(target_probe or {}),
                evidence_overview=dict(evidence_overview or {}),
                commands=commands,
                hypotheses=list(hypotheses or []),
                known_issue_hits=list(known_issue_hits or []),
                stop_decision=dict(stop_decision or {}),
                focus_review=dict(focus_review or {}),
                approved=False,
                created_at=now,
                updated_at=now,
            )
            record.rounds.append(rnd)
            record.updated_at = now
            self._sessions[session_id] = record
            self._persist_state_unlocked()
            return rnd

    def get_round(self, session_id: str, round_no: int) -> DiagnosisRound | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            for rnd in record.rounds:
                if rnd.round_no == int(round_no):
                    return rnd
            return None

    def approve_round(self, session_id: str, round_no: int, approved: bool) -> DiagnosisRound | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            for idx, rnd in enumerate(record.rounds):
                if rnd.round_no != int(round_no):
                    continue
                rnd.approved = bool(approved)
                for cidx, cmd in enumerate(rnd.commands):
                    cmd.approved = bool(approved)
                    rnd.commands[cidx] = cmd
                # Approval only marks commands as approved; execution starts in execute endpoint.
                rnd.status = "waiting_approval"
                rnd.updated_at = _now_s()
                record.rounds[idx] = rnd
                record.updated_at = rnd.updated_at
                self._sessions[session_id] = record
                self._persist_state_unlocked()
                return rnd
            return None

    def set_round_executions(self, session_id: str, round_no: int, executions: list[CommandExecution], status: str = "analyzing") -> DiagnosisRound | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            for idx, rnd in enumerate(record.rounds):
                if rnd.round_no != int(round_no):
                    continue
                rnd.executions = list(executions or [])
                rnd.status = status  # type: ignore[assignment]
                rnd.updated_at = _now_s()
                record.rounds[idx] = rnd
                record.updated_at = rnd.updated_at
                self._sessions[session_id] = record
                self._persist_state_unlocked()
                return rnd
            return None

    def set_round_analysis(
        self,
        session_id: str,
        round_no: int,
        analysis_result: str,
        status: str = "completed",
        focus_review: dict | None = None,
        hypotheses: list[dict] | None = None,
        known_issue_hits: list[dict] | None = None,
        stop_decision: dict | None = None,
        evidence_overview: dict | None = None,
        evidence_signals: list[dict] | None = None,
        retrospective: dict | None = None,
    ) -> DiagnosisRound | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            for idx, rnd in enumerate(record.rounds):
                if rnd.round_no != int(round_no):
                    continue
                rnd.analysis_result = str(analysis_result or "")
                if focus_review is not None:
                    rnd.focus_review = dict(focus_review or {})
                if hypotheses is not None:
                    rnd.hypotheses = list(hypotheses or [])
                if known_issue_hits is not None:
                    rnd.known_issue_hits = list(known_issue_hits or [])
                if stop_decision is not None:
                    rnd.stop_decision = dict(stop_decision or {})
                if evidence_overview is not None:
                    rnd.evidence_overview = dict(evidence_overview or {})
                if evidence_signals is not None:
                    rnd.evidence_signals = list(evidence_signals or [])
                if retrospective is not None:
                    rnd.retrospective = dict(retrospective or {})
                rnd.status = status  # type: ignore[assignment]
                rnd.updated_at = _now_s()
                record.rounds[idx] = rnd
                record.updated_at = rnd.updated_at
                self._sessions[session_id] = record
                self._persist_state_unlocked()
                return rnd
            return None
