from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.models.schemas import (
    AutomationLevel,
    CommandExecution,
    CommandStatus,
    DeviceProtocol,
    DeviceTarget,
    Evidence,
    ExportResponse,
    IncidentSummary,
    JobCommandResult,
    JobCommandStatus,
    JobEvent,
    JobMode,
    JobSummaryTurn,
    JobTimelineResponse,
    Message,
    OperationMode,
    RunKind,
    RunListResponse,
    RunResponse,
    RunStatus,
    RunTimelineResponse,
    ServiceTraceResponse,
    ServiceTraceStep,
    Session,
    SessionStatus,
    TimelineResponse,
    now_utc,
)
from app.services.exporter import export_timeline_markdown


class UnifiedRunService:
    NATIVE_TRACE_EVENT_TYPES = {
        "user_input",
        "context_snapshot",
        "ai_context_submit",
        "llm_request",
        "llm_response",
        "llm_plan",
        "llm_status",
        "plan_decision",
        "plan_parse",
        "loop_control",
        "policy_decision",
        "capability_decision",
        "scope_decision",
        "evidence_parse",
        "command_execution",
        "command_confirm_execution",
        "llm_final",
        "session_control",
        "session_adapter",
        "orchestrator_error",
        "sop_candidates_generated",
        "sop_referenced_by_ai",
        "sop_reference_outcome",
    }

    def __init__(self, store, orchestrator, orchestrator_v2, sop_archive=None):
        self.store = store
        self.orchestrator = orchestrator
        self.orchestrator_v2 = orchestrator_v2
        self.sop_archive = sop_archive

    @staticmethod
    def single_run_id(session_id: str) -> str:
        return f"run_s:{str(session_id or '').strip()}"

    @staticmethod
    def multi_run_id(job_id: str) -> str:
        return f"run_m:{str(job_id or '').strip()}"

    @staticmethod
    def history_session_id_for_multi(job_id: str) -> str:
        return f"v2job:{str(job_id or '').strip()}"

    @staticmethod
    def parse_run_id(run_id: str) -> tuple[RunKind, str]:
        raw = str(run_id or "").strip()
        if raw.startswith("run_s:"):
            return RunKind.single, raw[6:].strip()
        if raw.startswith("run_m:"):
            return RunKind.multi, raw[6:].strip()
        raise KeyError(run_id)

    @staticmethod
    def _enum_value(value: object) -> str:
        return str(getattr(value, "value", value) or "").strip()

    def _job_status_to_run_status(self, value: object) -> RunStatus:
        raw = self._enum_value(value).lower()
        if raw == "queued":
            return RunStatus.open
        if raw == "executing":
            return RunStatus.running
        if raw in {status.value for status in RunStatus}:
            return RunStatus(raw)
        return RunStatus.running

    def _job_mode_to_operation_mode(self, mode: object) -> OperationMode:
        raw = self._enum_value(mode).lower()
        if raw == JobMode.inspection.value:
            return OperationMode.query
        if raw == JobMode.repair.value:
            return OperationMode.config
        return OperationMode.diagnosis

    def _single_pending_leader_command_ids(self, session_id: str) -> list[str]:
        pending = [item for item in self.store.list_commands(session_id) if item.status == CommandStatus.pending_confirm]
        pending.sort(key=lambda item: int(item.step_no or 0))
        results: list[str] = []
        seen_batch: set[str] = set()
        for item in pending:
            batch_id = str(item.batch_id or "").strip()
            if batch_id:
                if batch_id in seen_batch:
                    continue
                seen_batch.add(batch_id)
            results.append(item.id)
        return results

    def _latest_session_problem(self, session_id: str) -> str | None:
        for message in reversed(self.store.list_messages(session_id)):
            if message.role == "user" and message.content.strip():
                return message.content.strip()
        return None

    def _latest_session_updated_at(self, session_id: str):
        values = []
        session = self.store.get_session(session_id)
        values.append(session.created_at)
        values.extend([item.created_at for item in self.store.list_messages(session_id)])
        values.extend([item.completed_at or item.created_at for item in self.store.list_commands(session_id)])
        values.extend([item.created_at for item in self.store.list_evidence(session_id)])
        values.extend([item.completed_at or item.started_at for item in self.store.list_trace_steps(session_id)])
        summary = self.store.get_summary(session_id)
        if summary is not None:
            values.append(summary.created_at)
        values = [item for item in values if item is not None]
        return max(values) if values else session.created_at

    def _single_effective_summary(self, session_id: str) -> IncidentSummary | None:
        summary = self.store.get_summary(session_id)
        if summary is not None:
            return summary
        commands = self.store.list_commands(session_id)
        pending = any(item.status == CommandStatus.pending_confirm for item in commands)
        rejected = [item for item in commands if item.status == CommandStatus.rejected]
        if pending or not rejected:
            return None
        if session_id in self.orchestrator._running_sessions:
            return None
        return IncidentSummary(
            session_id=session_id,
            mode="unavailable",
            root_cause="高风险命令已被人工拒绝，本次变更演练未继续执行。",
            impact_scope="未执行高风险变更，设备运行状态保持不变。",
            recommendation="如需继续，请在确认变更窗口与目标接口后重新发起任务，或改用只读排查模式。",
            confidence=1.0,
        )

    def _single_run_status(self, session_id: str) -> RunStatus:
        latest_message = self.store.list_messages(session_id)[-1] if self.store.list_messages(session_id) else None
        if latest_message and latest_message.role == "system" and "手动停止" in latest_message.content:
            return RunStatus.cancelled
        if self._single_pending_leader_command_ids(session_id):
            return RunStatus.waiting_approval
        summary = self._single_effective_summary(session_id)
        if summary is not None:
            if summary.mode in {"error", "unavailable"}:
                return RunStatus.failed
            return RunStatus.completed
        if session_id in self.orchestrator._running_sessions:
            return RunStatus.running
        return RunStatus.open

    def _single_run_phase(self, session_id: str) -> str | None:
        status = self._single_run_status(session_id)
        if status == RunStatus.waiting_approval:
            return "approve"
        if status in {RunStatus.completed, RunStatus.failed, RunStatus.cancelled}:
            return "conclude"
        steps = self.store.list_trace_steps(session_id)
        if not steps:
            return "collect"
        step_type = str(steps[-1].step_type or "").strip()
        mapping = {
            "user_input": "collect",
            "llm_request": "plan",
            "llm_response": "analyze",
            "llm_plan": "plan",
            "plan_decision": "plan",
            "plan_parse": "plan",
            "policy_decision": "execute",
            "capability_decision": "execute",
            "scope_decision": "execute",
            "command_execution": "execute",
            "command_confirm_execution": "execute",
            "evidence_parse": "analyze",
            "llm_final": "conclude",
            "session_control": "execute",
            "session_adapter": "collect",
        }
        return mapping.get(step_type, "plan")

    def build_single_run_response(self, session_id: str) -> RunResponse:
        session = self.store.get_session(session_id)
        pending_actions = len(self._single_pending_leader_command_ids(session_id))
        sop_extracted, sop_draft_count, sop_published_count = self._sop_counts(session.id)
        primary_sop_id = self._primary_sop_id(session.id)
        return RunResponse(
            id=self.single_run_id(session.id),
            source_id=session.id,
            kind=RunKind.single,
            name=session.device.name,
            protocol=session.device.protocol,
            problem=self._latest_session_problem(session.id),
            status=self._single_run_status(session.id),
            phase=self._single_run_phase(session.id),
            automation_level=session.automation_level,
            operation_mode=session.operation_mode,
            sop_enabled=session.sop_enabled,
            created_at=session.created_at,
            updated_at=self._latest_session_updated_at(session.id),
            device_count=1,
            device_hosts=[session.device.host],
            pending_actions=pending_actions,
            sop_extracted=sop_extracted,
            sop_draft_count=sop_draft_count,
            sop_published_count=sop_published_count,
            primary_sop_id=primary_sop_id,
        )

    def build_multi_run_response_from_job(self, job) -> RunResponse:
        sop_extracted, sop_draft_count, sop_published_count = self._sop_counts(job.id)
        primary_sop_id = self._primary_sop_id(job.id)
        return RunResponse(
            id=self.multi_run_id(job.id),
            source_id=job.id,
            kind=RunKind.multi,
            name=job.name,
            protocol=None,
            problem=job.problem,
            status=self._job_status_to_run_status(job.status),
            phase=self._enum_value(job.phase),
            automation_level=AutomationLevel.assisted,
            operation_mode=self._job_mode_to_operation_mode(job.mode),
            sop_enabled=job.sop_enabled,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            device_count=job.device_count,
            device_hosts=[],
            pending_actions=job.pending_action_groups,
            sop_extracted=sop_extracted,
            sop_draft_count=sop_draft_count,
            sop_published_count=sop_published_count,
            primary_sop_id=primary_sop_id,
        )

    def build_multi_run_response(self, timeline: JobTimelineResponse) -> RunResponse:
        hosts = [str(item.host or "").strip() for item in timeline.job.devices if str(item.host or "").strip()]
        pending_actions = len([item for item in timeline.job.action_groups if self._enum_value(item.status) == "pending_approval"])
        sop_extracted, sop_draft_count, sop_published_count = self._sop_counts(timeline.job.id)
        primary_sop_id = self._primary_sop_id(timeline.job.id)
        return RunResponse(
            id=self.multi_run_id(timeline.job.id),
            source_id=timeline.job.id,
            kind=RunKind.multi,
            name=timeline.job.name,
            protocol=(timeline.job.devices[0].protocol if timeline.job.devices else None),
            problem=timeline.job.problem,
            status=self._job_status_to_run_status(timeline.job.status),
            phase=self._enum_value(timeline.job.phase),
            automation_level=AutomationLevel.assisted,
            operation_mode=self._job_mode_to_operation_mode(timeline.job.mode),
            sop_enabled=timeline.job.sop_enabled,
            created_at=timeline.job.created_at,
            updated_at=timeline.job.updated_at,
            started_at=timeline.job.started_at,
            completed_at=timeline.job.completed_at,
            device_count=len(timeline.job.devices),
            device_hosts=hosts,
            pending_actions=pending_actions,
            sop_extracted=sop_extracted,
            sop_draft_count=sop_draft_count,
            sop_published_count=sop_published_count,
            primary_sop_id=primary_sop_id,
        )

    def _sop_counts(self, source_id: str) -> tuple[bool, int, int]:
        if self.sop_archive is None:
            return False, 0, 0
        try:
            return self.sop_archive.source_run_counts(source_id)
        except Exception:
            return False, 0, 0

    def _primary_sop_id(self, source_id: str) -> str | None:
        if self.sop_archive is None:
            return None
        try:
            return self.sop_archive.primary_record_id_for_source_run(source_id)
        except Exception:
            return None

    async def list_runs(self, kind: RunKind | None = None, offset: int = 0, limit: int = 100) -> RunListResponse:
        rows: list[RunResponse] = []
        if kind in {None, RunKind.single}:
            for session in self.store.list_sessions():
                rows.append(self.build_single_run_response(session.id))
        if kind in {None, RunKind.multi}:
            jobs, _ = await self.orchestrator_v2.list_jobs(offset=0, limit=500)
            rows.extend(self.build_multi_run_response_from_job(job) for job in jobs)
        rows.sort(key=lambda item: item.created_at, reverse=True)
        sliced = rows[offset: offset + limit]
        return RunListResponse(total=len(rows), items=sliced)

    async def get_run(self, run_id: str) -> RunResponse:
        kind, source_id = self.parse_run_id(run_id)
        if kind == RunKind.single:
            if source_id not in self.store.sessions:
                raise KeyError(run_id)
            return self.build_single_run_response(source_id)
        job = await self.orchestrator_v2.get_job(source_id)
        return self.build_multi_run_response_from_job(job)

    async def get_timeline(self, run_id: str) -> RunTimelineResponse:
        kind, source_id = self.parse_run_id(run_id)
        if kind == RunKind.single:
            if source_id not in self.store.sessions:
                raise KeyError(run_id)
            timeline = self.store.get_timeline(source_id)
            run = self.build_single_run_response(source_id)
            timeline.summary = self._single_effective_summary(source_id)
            timeline.session.status = (
                SessionStatus.closed
                if run.status in {RunStatus.completed, RunStatus.failed, RunStatus.cancelled}
                else SessionStatus.open
            )
            service_trace = self.store.get_service_trace(source_id)
            return RunTimelineResponse(
                run=run,
                payload=timeline.model_dump(mode="json"),
                trace=[item.model_dump(mode="json") for item in service_trace.steps],
                timeline=timeline,
                service_trace=service_trace,
            )

        raw_timeline = await self.orchestrator_v2.get_timeline(source_id)
        run = self.build_multi_run_response(raw_timeline)
        timeline = self._normalize_multi_timeline(raw_timeline, run)
        service_trace = self._normalize_multi_trace(raw_timeline)
        return RunTimelineResponse(
            run=run,
            payload=raw_timeline.model_dump(mode="json"),
            trace=[item.model_dump(mode="json") for item in service_trace.steps],
            timeline=timeline,
            service_trace=service_trace,
        )

    async def get_service_trace(self, run_id: str) -> ServiceTraceResponse:
        timeline = await self.get_timeline(run_id)
        return timeline.service_trace

    async def list_trace_steps_since(self, run_id: str, from_seq: int = 0) -> tuple[list[ServiceTraceStep], RunStatus]:
        timeline = await self.get_timeline(run_id)
        rows = [item for item in timeline.service_trace.steps if int(item.seq_no or 0) > int(from_seq or 0)]
        return rows, timeline.run.status

    async def export_timeline_markdown(self, run_id: str) -> ExportResponse:
        timeline = await self.get_timeline(run_id)
        export = export_timeline_markdown(timeline.timeline)
        export.filename = f"run-{timeline.run.source_id}.md"
        return export

    def _normalize_multi_timeline(self, raw: JobTimelineResponse, run: RunResponse) -> TimelineResponse:
        synthetic_session_id = self.history_session_id_for_multi(raw.job.id)
        hosts = [str(item.host or "").strip() for item in raw.job.devices if str(item.host or "").strip()]
        protocol = raw.job.devices[0].protocol if raw.job.devices else DeviceProtocol.ssh
        version_signature = raw.job.devices[0].version_signature if raw.job.devices else None
        session = Session(
            id=synthetic_session_id,
            device=DeviceTarget(
                host=", ".join(hosts) or "-",
                name=run.name or f"多设备协同({len(hosts) or len(raw.job.devices)})",
                protocol=protocol,
                version_signature=version_signature,
            ),
            automation_level=run.automation_level,
            operation_mode=run.operation_mode,
            issue_scope=[],
            status=SessionStatus.closed if self._enum_value(raw.job.status) in {"completed", "failed", "cancelled"} else SessionStatus.open,
            created_at=raw.job.created_at,
        )
        return TimelineResponse(
            session=session,
            messages=self._build_multi_messages(raw, synthetic_session_id),
            commands=self._map_multi_commands(raw, synthetic_session_id),
            evidences=self._map_multi_evidences(raw, synthetic_session_id),
            summary=self._build_multi_summary(raw, synthetic_session_id),
        )

    def _build_multi_messages(self, raw: JobTimelineResponse, session_id: str) -> list[Message]:
        messages: list[Message] = []
        request_history = list(getattr(raw.job, "request_history", []) or [])
        summary_history = list(getattr(raw.job, "summary_history", []) or [])
        for index, item in enumerate(request_history, start=1):
            if isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                created_at = item.get("created_at") or raw.job.created_at
            else:
                content = str(getattr(item, "content", "") or "").strip()
                created_at = getattr(item, "created_at", None) or raw.job.created_at
            if not content:
                continue
            messages.append(
                Message(
                    id=f"runmsg:user:{raw.job.id}:{index}",
                    session_id=session_id,
                    role="user",
                    content=content,
                    created_at=created_at,
                )
            )
            if index <= len(summary_history):
                summary_turn = summary_history[index - 1]
                if isinstance(summary_turn, dict):
                    summary_payload = summary_turn.get("summary") or {}
                    summary_created_at = summary_turn.get("created_at") or raw.job.updated_at or raw.job.created_at
                else:
                    summary_payload = getattr(summary_turn, "summary", None)
                    summary_created_at = getattr(summary_turn, "created_at", None) or raw.job.updated_at or raw.job.created_at
                rendered = self._render_summary_brief(self._multi_summary_from_payload(raw.job.id, summary_payload, session_id))
                if rendered:
                    messages.append(
                        Message(
                            id=f"runmsg:assistant:{raw.job.id}:{index}",
                            session_id=session_id,
                            role="assistant",
                            content=rendered,
                            created_at=summary_created_at,
                        )
                    )
        problem = str(raw.job.problem or "").strip()
        if problem and (not messages or messages[-1].content != problem):
            messages.append(
                Message(
                    id=f"runmsg:user:{raw.job.id}:latest",
                    session_id=session_id,
                    role="user",
                    content=problem,
                    created_at=raw.job.updated_at or raw.job.created_at,
                )
            )
        summary = self._build_multi_summary(raw, session_id)
        if summary:
            messages.append(
                Message(
                    id=f"runmsg:assistant:{raw.job.id}",
                    session_id=session_id,
                    role="assistant",
                    content=self._render_summary_brief(summary),
                    created_at=raw.job.completed_at or raw.job.updated_at or raw.job.created_at,
                )
            )
        return messages

    def _multi_summary_from_payload(self, job_id: str, payload: Any, session_id: str) -> IncidentSummary:
        if isinstance(payload, IncidentSummary):
            return payload
        if isinstance(payload, dict):
            normalized = dict(payload)
        else:
            normalized = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else {}
        return IncidentSummary(
            session_id=session_id,
            mode="config" if str(normalized.get("query_result") or "").strip() else "diagnosis",
            root_cause=str(normalized.get("root_cause") or normalized.get("summary") or "").strip() or "历史总结",
            impact_scope=str(normalized.get("impact_scope") or "-").strip() or "-",
            recommendation=str(normalized.get("recommendation") or "-").strip() or "-",
            query_result=str(normalized.get("query_result") or "").strip() or None,
            confidence=float(normalized.get("confidence") or 0.0) if normalized.get("confidence") is not None else None,
            created_at=normalized.get("created_at") or now_utc(),
        )

    def _map_multi_evidences(self, raw: JobTimelineResponse, session_id: str) -> list[Evidence]:
        rows: list[Evidence] = []
        for item in raw.job.evidences or []:
            rows.append(
                Evidence(
                    id=f"v2ev:{item.id}",
                    session_id=session_id,
                    command_id=f"v2cmd:{item.command_id}",
                    category=item.category,
                    raw_output=item.raw_output,
                    parsed_data=item.parsed_data,
                    conclusion=item.conclusion,
                    created_at=item.created_at,
                )
            )
        return rows

    def _map_multi_commands(self, raw: JobTimelineResponse, session_id: str) -> list[CommandExecution]:
        device_host_by_id = {
            str(device.id): str(device.host or device.id or "-").strip() or "-"
            for device in raw.job.devices or []
        }
        rows: list[CommandExecution] = []
        base_commands = sorted(
            list(raw.job.command_results or []),
            key=lambda item: (int(item.step_no or 0), item.created_at),
        )
        for item in base_commands:
            host_label = device_host_by_id.get(str(item.device_id), str(item.device_id or "-"))
            rows.append(
                CommandExecution(
                    id=f"v2cmd:{item.id}",
                    session_id=session_id,
                    step_no=int(item.step_no or 0),
                    title=f"[{host_label}] {item.title}",
                    command=item.command,
                    adapter_type=self._safe_device_protocol(raw.job.devices, item.device_id),
                    risk_level=item.risk_level,
                    status=self._map_job_command_status(item.status),
                    requires_confirmation=False,
                    output=item.output,
                    error=item.error,
                    created_at=item.created_at,
                    started_at=item.started_at,
                    completed_at=item.completed_at,
                    duration_ms=item.duration_ms,
                    original_command=item.command,
                    effective_command=item.effective_command,
                    capability_state=item.capability_state,
                    capability_reason=item.capability_reason,
                    constraint_source=item.constraint_source,
                    constraint_reason=item.constraint_reason,
                )
            )
        pending_groups = [item for item in (raw.job.action_groups or []) if self._enum_value(item.status) == "pending_approval"]
        if pending_groups:
            max_step = max([int(item.step_no or 0) for item in rows] or [0])
            batch_id = f"v2pending:{raw.job.id}"
            for index, group in enumerate(pending_groups, start=1):
                host_label = device_host_by_id.get(str(group.device_id), str(group.device_id or "-"))
                rows.append(
                    CommandExecution(
                        id=f"v2ag:{group.id}",
                        session_id=session_id,
                        step_no=max_step + index,
                        title=f"[{host_label}] {group.title}",
                        command=" ; ".join(group.commands),
                        adapter_type=self._safe_device_protocol(raw.job.devices, group.device_id),
                        risk_level=group.risk_level,
                        status=CommandStatus.pending_confirm,
                        requires_confirmation=True,
                        error=f"命令组待确认后执行（{len(group.commands)} 条）",
                        created_at=group.created_at,
                        batch_id=batch_id,
                        batch_index=index,
                        batch_total=len(pending_groups),
                        constraint_source="v2_group_approval",
                        constraint_reason="Action group pending approval",
                    )
                )
        rows.sort(key=lambda item: int(item.step_no or 0))
        return rows

    def _safe_device_protocol(self, devices: Iterable[Any], device_id: str) -> DeviceProtocol:
        for item in devices:
            if str(getattr(item, "id", "")) == str(device_id):
                return getattr(item, "protocol", DeviceProtocol.ssh) or DeviceProtocol.ssh
        return DeviceProtocol.ssh

    def _map_job_command_status(self, status: object) -> CommandStatus:
        raw = self._enum_value(status).lower()
        if raw in {state.value for state in CommandStatus}:
            return CommandStatus(raw)
        if raw == JobCommandStatus.queued.value:
            return CommandStatus.queued
        return CommandStatus.failed

    def _build_multi_summary(self, raw: JobTimelineResponse, session_id: str) -> IncidentSummary | None:
        mode = self._enum_value(raw.job.mode)
        rca = raw.job.rca_result
        if self._enum_value(raw.job.status) in {"failed", "cancelled"}:
            error_text = str(getattr(raw.job, "error", "") or "").strip()
            return IncidentSummary(
                session_id=session_id,
                mode="error",
                root_cause=error_text or "多设备协同执行失败",
                impact_scope="未能完成所有设备的证据采集与汇总。",
                recommendation="请检查设备连接、账号权限与任务参数后重试。",
                confidence=0.0,
            )
        if not rca:
            return None
        confidence = float(rca.confidence) if rca.confidence is not None else None
        if mode == JobMode.inspection.value:
            query_text = str(rca.root_cause or rca.summary or "").strip() or "多设备查询已完成"
            impact_text = str(rca.impact_scope or f"共采集设备 {len(raw.job.devices)} 台").strip()
            recommendation = str(rca.recommendation or "已完成").strip() or "已完成"
            return IncidentSummary(
                session_id=session_id,
                mode="query",
                root_cause=query_text,
                impact_scope=impact_text,
                recommendation=recommendation,
                query_result=query_text,
                follow_up_action=recommendation,
                confidence=confidence,
            )
        if mode == JobMode.repair.value:
            result_text = str(rca.root_cause or rca.summary or "").strip() or "多设备修复流程已执行完成"
            impact_text = str(rca.impact_scope or f"涉及设备 {len(raw.job.devices)} 台").strip()
            recommendation = str(rca.recommendation or "已完成").strip() or "已完成"
            return IncidentSummary(
                session_id=session_id,
                mode="config",
                root_cause=result_text,
                impact_scope=impact_text,
                recommendation=recommendation,
                query_result=result_text,
                follow_up_action=recommendation,
                confidence=confidence,
            )
        return IncidentSummary(
            session_id=session_id,
            mode="diagnosis",
            root_cause=str(rca.root_cause or rca.summary or "").strip() or "未形成明确根因",
            impact_scope=str(rca.impact_scope or f"涉及设备 {len(raw.job.devices)} 台").strip(),
            recommendation=str(rca.recommendation or "请补充更多证据后重试。").strip(),
            confidence=confidence,
            evidence_refs=[],
        )

    def _render_summary_brief(self, summary: IncidentSummary) -> str:
        parts = []
        if summary.mode == "query" and summary.query_result:
            parts.append(str(summary.query_result).strip())
        elif summary.mode == "config" and summary.query_result:
            parts.append(f"配置完成。结果: {str(summary.query_result).strip()}")
        else:
            parts.append(f"诊断完成。根因判断: {str(summary.root_cause).strip()}")
        impact = str(summary.impact_scope or "").strip()
        recommendation = str(summary.recommendation or "").strip()
        if impact:
            parts.append(f"影响范围: {impact}")
        if recommendation:
            parts.append(f"建议: {recommendation}")
        return "。".join(part for part in parts if part).strip("。")

    def _normalize_multi_trace(self, raw: JobTimelineResponse) -> ServiceTraceResponse:
        session_id = self.history_session_id_for_multi(raw.job.id)
        steps: list[ServiceTraceStep] = []
        events = sorted(list(raw.events or []), key=lambda item: int(item.seq_no or 0))
        native_command_ids: set[str] = set()
        if events:
            for event in events:
                payload = event.payload if isinstance(event.payload, dict) else {}
                trace_step_type = str(payload.get("trace_step_type") or event.event_type or "").strip()
                if trace_step_type == "command_execution":
                    command_id = str(payload.get("command_id") or "").strip()
                    if command_id:
                        native_command_ids.add(command_id)
        if events:
            for event in events:
                native = self._build_native_trace_step(event, session_id)
                if native is not None:
                    steps.append(native)
                    continue
                steps.extend(self._build_legacy_trace_steps(event, raw, session_id, native_command_ids=native_command_ids))
        else:
            problem = str(raw.job.problem or "").strip()
            if problem:
                steps.append(
                    ServiceTraceStep(
                        id=f"runtrace:{session_id}:user",
                        session_id=session_id,
                        seq_no=1,
                        step_type="user_input",
                        title="接收用户请求",
                        status="succeeded",
                        started_at=raw.job.created_at,
                        completed_at=raw.job.created_at,
                        duration_ms=0,
                        detail=problem[:280],
                        detail_payload={"user_input": problem, "source": "job.problem"},
                    )
                )
            for idx, command in enumerate(sorted(raw.job.command_results or [], key=lambda item: int(item.step_no or 0)), start=1):
                steps.append(
                    ServiceTraceStep(
                        id=f"runtrace:{session_id}:cmd:{command.id}",
                        session_id=session_id,
                        seq_no=10 + idx,
                        step_type="command_execution",
                        title=f"执行命令 #{command.step_no}: {command.title}",
                        status=self._enum_value(command.status) or "succeeded",
                        started_at=command.started_at or command.created_at,
                        completed_at=command.completed_at or command.started_at or command.created_at,
                        duration_ms=command.duration_ms or 0,
                        command_id=f"v2cmd:{command.id}",
                        detail=command.command[:280],
                        detail_payload={"command": self._build_trace_command_payload(command)},
                    )
                )
        summary = self._build_multi_summary(raw, session_id)
        if summary and not any(step.step_type == "llm_final" for step in steps):
            ended_at = raw.job.completed_at or raw.job.updated_at or raw.job.created_at
            steps.append(
                ServiceTraceStep(
                    id=f"runtrace:{session_id}:summary",
                    session_id=session_id,
                    seq_no=max([int(item.seq_no or 0) for item in steps] or [0]) + 1,
                    step_type="llm_final",
                    title="任务总结输出",
                    status="failed" if self._enum_value(raw.job.status) in {"failed", "cancelled"} else "succeeded",
                    started_at=ended_at,
                    completed_at=ended_at,
                    duration_ms=0,
                    detail=self._render_summary_brief(summary)[:280],
                    detail_payload={"final_summary": summary.model_dump(mode="json")},
                )
            )
        steps = self._finalize_instant_running_steps(self._dedupe_steps(self._sort_trace_steps(steps)), raw)
        return ServiceTraceResponse(session_id=session_id, steps=steps)

    def _finalize_instant_running_steps(
        self,
        steps: list[ServiceTraceStep],
        raw: JobTimelineResponse,
    ) -> list[ServiceTraceStep]:
        if self._enum_value(raw.job.status) not in {"completed", "failed", "cancelled"}:
            return steps
        finalized: list[ServiceTraceStep] = []
        for step in steps:
            if (
                step.status == "running"
                and step.step_type == "session_control"
                and (
                    step.title.startswith("阶段切换：")
                    or step.title.endswith("开始设备采集")
                    or step.title.endswith("开始执行命令组")
                )
            ):
                detail_payload = dict(step.detail_payload or {})
                if detail_payload.get("status") == "running":
                    detail_payload["status"] = "succeeded"
                detail = step.detail
                if step.title.startswith("阶段切换：") and detail:
                    detail = detail.replace("status=running", "status=succeeded")
                finalized.append(step.model_copy(update={"status": "succeeded", "detail": detail, "detail_payload": detail_payload}))
                continue
            finalized.append(step)
        return finalized

    def _build_native_trace_step(self, event: JobEvent, session_id: str) -> ServiceTraceStep | None:
        event_type = str(event.event_type or "").strip()
        payload = event.payload if isinstance(event.payload, dict) else {}
        trace_step_type = str(payload.get("trace_step_type") or event_type or "").strip()
        if trace_step_type not in self.NATIVE_TRACE_EVENT_TYPES:
            return None
        detail_payload = payload.get("detail_payload") if isinstance(payload.get("detail_payload"), dict) else {
            key: value
            for key, value in payload.items()
            if key not in {"trace_step_type", "title", "status", "detail", "started_at", "completed_at", "duration_ms", "command_id", "step_no", "device_id", "device_host"}
        }
        if not detail_payload:
            detail_payload = {"event_payload": payload}
        return ServiceTraceStep(
            id=f"runtrace:{session_id}:evt:{event.id}",
            session_id=session_id,
            seq_no=int(event.seq_no or 0),
            step_type=trace_step_type,
            title=str(payload.get("title") or trace_step_type),
            status=str(payload.get("status") or "succeeded"),
            started_at=payload.get("started_at") or event.created_at,
            completed_at=payload.get("completed_at") or event.created_at,
            duration_ms=int(payload.get("duration_ms") or 0),
            command_id=str(payload.get("command_id") or "").strip() or None,
            detail=str(payload.get("detail") or "").strip() or None,
            detail_payload=detail_payload,
        )

    def _build_legacy_trace_steps(
        self,
        event: JobEvent,
        raw: JobTimelineResponse,
        session_id: str,
        *,
        native_command_ids: set[str] | None = None,
    ) -> list[ServiceTraceStep]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        seq_base = int(event.seq_no or 0) * 10
        command_results = {str(item.id): item for item in raw.job.command_results or []}
        device_map = {
            str(device.id): {"host": str(device.host or device.id or "-"), "name": device.name}
            for device in raw.job.devices or []
        }
        command_id = str(payload.get("command_id") or "").strip()
        command = command_results.get(command_id)
        device_id = str(payload.get("device_id") or (command.device_id if command else "") or "").strip()
        device_host = device_map.get(device_id, {}).get("host") or str(payload.get("host") or payload.get("device_host") or device_id or "-")
        created_at = event.created_at or raw.job.updated_at or raw.job.created_at
        event_type = str(event.event_type or "")
        native_command_ids = native_command_ids or set()
        if event_type == "job_created":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title="创建多设备协同", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"devices={payload.get('device_count', len(raw.job.devices))}; mode={payload.get('mode', raw.job.mode)}", detail_payload=payload)]
        if event_type == "phase_changed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"阶段切换：{self._format_phase_label(str(payload.get('phase') or ''))}", status=str(payload.get('status') or 'succeeded'), started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"phase={payload.get('phase', '')}; status={payload.get('status', '')}", detail_payload=payload)]
        if event_type == "device_collect_started":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"[{device_host}] 开始设备采集", status="running", started_at=created_at, completed_at=created_at, duration_ms=0, detail="phase=collect", detail_payload=payload)]
        if event_type == "device_collect_completed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"[{device_host}] 设备采集完成", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail="phase=collect", detail_payload=payload)]
        if event_type == "device_collect_failed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"[{device_host}] 设备采集失败", status="failed", started_at=created_at, completed_at=created_at, duration_ms=0, detail=str(payload.get('error') or 'device_collect_failed')[:280], detail_payload=payload)]
        if event_type == "command_started" and command is not None:
            if command_id in native_command_ids:
                return []
            return [self._build_synthetic_command_trace_step(session_id, seq_base, command, device_host, "running")]
        if event_type in {"command_completed", "command_failed"} and command is not None:
            if command_id in native_command_ids:
                return []
            status = self._enum_value(command.status) or ("failed" if event_type == "command_failed" else "succeeded")
            return [self._build_synthetic_command_trace_step(session_id, seq_base, command, device_host, status)]
        if event_type == "command_blocked":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="policy_decision", title="命令被策略阻断", status="blocked", started_at=created_at, completed_at=created_at, duration_ms=0, command_id=f"v2cmd:{command.id}" if command else None, detail=str(payload.get('reason') or 'blocked')[:280], detail_payload={**payload, "command": self._build_trace_command_payload(command) if command else None})]
        if event_type == "correlate_completed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="plan_decision", title="多设备关联分析完成", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"clusters={payload.get('cluster_count', 0)}; incidents={payload.get('incident_count', 0)}; root_device={payload.get('root_device_id', '-')}", detail_payload=payload)]
        if event_type == "llm_rca_refined":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="llm_response", title="AI RCA 精炼回复", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail=str(payload.get('summary') or '')[:220], detail_payload={"ai_response_parsed": {"summary": payload.get('summary'), "confidence": payload.get('confidence'), "root_device_id": payload.get('root_device_id')}, "llm": {"parsed_response": payload}})]
        if event_type == "plan_completed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="plan_decision", title="修复计划生成完成", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"action_groups={payload.get('action_group_count', 0)}; pending={payload.get('pending_approval', 0)}; auto_approved={payload.get('auto_approved', 0)}", detail_payload=payload)]
        if event_type == "action_group_started":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"[{device_host}] 开始执行命令组", status="running", started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"command_count={payload.get('command_count', 0)}", detail_payload=payload)]
        if event_type == "action_group_completed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title=f"[{device_host}] 命令组执行完成", status=str(payload.get('status') or 'succeeded'), started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"status={payload.get('status', '')}", detail_payload=payload)]
        if event_type == "job_completed":
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title="多设备协同完成", status="succeeded", started_at=created_at, completed_at=created_at, duration_ms=0, detail=f"mode={payload.get('mode', raw.job.mode)}", detail_payload=payload)]
        if event_type in {"job_failed", "job_cancelled"}:
            return [ServiceTraceStep(id=f"runtrace:{session_id}:legacy:{event.id}", session_id=session_id, seq_no=seq_base, step_type="session_control", title="多设备协同失败" if event_type == "job_failed" else "多设备协同已取消", status="failed" if event_type == "job_failed" else "stopped", started_at=created_at, completed_at=created_at, duration_ms=0, detail=str(payload.get('error') or payload.get('reason') or event_type)[:280], detail_payload=payload)]
        return []

    def _build_synthetic_command_trace_step(self, session_id: str, seq_no: int, command: JobCommandResult, device_host: str, status: str) -> ServiceTraceStep:
        return ServiceTraceStep(
            id=f"runtrace:{session_id}:cmd:{command.id}:{status}",
            session_id=session_id,
            seq_no=seq_no,
            step_type="command_execution",
            title=f"执行命令 #{command.step_no}: [{device_host}] {command.title}",
            status=status,
            started_at=command.started_at or command.created_at,
            completed_at=command.completed_at or command.started_at or command.created_at,
            duration_ms=command.duration_ms or 0,
            command_id=f"v2cmd:{command.id}",
            detail=command.command[:280],
            detail_payload={"command": self._build_trace_command_payload(command)},
        )

    def _build_trace_command_payload(self, command: JobCommandResult) -> dict[str, Any]:
        return {
            "id": f"v2cmd:{command.id}",
            "step_no": command.step_no,
            "title": command.title,
            "command": command.command,
            "original_command": command.command,
            "effective_command": command.effective_command or command.command,
            "status": self._enum_value(command.status),
            "risk_level": self._enum_value(command.risk_level),
            "requires_confirmation": False,
            "error": command.error or "",
            "output": command.output or "",
            "constraint_source": command.constraint_source or "",
            "constraint_reason": command.constraint_reason or "",
            "capability_state": command.capability_state or "",
            "capability_reason": command.capability_reason or "",
        }

    def _format_phase_label(self, phase: str) -> str:
        labels = {
            "collect": "采集",
            "correlate": "关联",
            "plan": "规划",
            "approve": "审批",
            "execute": "执行",
            "analyze": "分析",
            "conclude": "总结",
        }
        return labels.get(phase, phase or "-")

    def _sort_trace_steps(self, steps: list[ServiceTraceStep]) -> list[ServiceTraceStep]:
        def sort_key(item: ServiceTraceStep):
            started = item.started_at
            if isinstance(started, datetime):
                ts = started.timestamp()
            else:
                try:
                    ts = datetime.fromisoformat(str(started).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0
            return (int(item.seq_no or 0), ts, str(item.id))

        return sorted(steps, key=sort_key)

    def _dedupe_steps(self, steps: list[ServiceTraceStep]) -> list[ServiceTraceStep]:
        seen: set[str] = set()
        rows: list[ServiceTraceStep] = []
        for step in steps:
            key = f"{step.id}::{step.step_type}::{step.seq_no}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(step)
        return rows
