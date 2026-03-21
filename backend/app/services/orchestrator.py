from __future__ import annotations

import json
import os
import re
import time
from typing import AsyncIterator, Literal

from app.models.schemas import (
    AutomationLevel,
    CommandExecution,
    CommandPolicy,
    CommandStatus,
    ConfirmCommandRequest,
    ConfirmCommandResponse,
    Evidence,
    EventEnvelope,
    IncidentSummary,
    Message,
    RiskLevel,
    ServiceTraceStep,
    make_id,
    now_utc,
)
from app.services.command_policy import CommandPolicyEngine
from app.services.adapters import build_adapter
from app.services.deepseek_diagnoser import DeepSeekDiagnoser
from app.services.parsers import parse_command_output
from app.services.risk_engine import RiskEngine
from app.services.store import InMemoryStore


class ConversationOrchestrator:
    def __init__(self, store: InMemoryStore, *, allow_simulation: bool = True):
        self.store = store
        self.risk_engine = RiskEngine()
        self.command_policy_engine = CommandPolicyEngine()
        self.deepseek_diagnoser = DeepSeekDiagnoser()
        self.allow_simulation = allow_simulation
        self.max_autonomous_steps = int(os.getenv("AUTONOMOUS_MAX_STEPS", "8"))
        self._session_adapters: dict[str, object] = {}
        self._trace_perf_started: dict[str, float] = {}

    def prompt_runtime_policy(self) -> dict[str, str]:
        return {
            "runtime_session_header_template": (
                "会话ID: {session_id}\\n"
                "当前设备厂商: {vendor}\\n"
                "接入协议: {protocol}\\n"
                "会话模式: {operation_mode}\\n"
                "请只基于当前会话上下文继续诊断，禁止引用或臆测其他会话。\\n"
                "用户问题: {user_content}"
            ),
            "runtime_command_result_template": (
                "执行结果\\n"
                "- step: {step_no}\\n"
                "- title: {title}\\n"
                "- command: {command}\\n"
                "- status: {status}\\n"
                "- error: {error(optional)}\\n"
                "- output: {output(optional)}"
            ),
            "runtime_finalization_prompt_template": (
                "请基于当前会话已有证据直接给出最终结论。"
                "只允许输出decision=final，不要再输出run_command。"
                "如果用户是信息查询任务，请返回mode=query和query_result；"
                "如果用户是配置任务，请返回mode=config和query_result；"
                "如果是故障诊断任务，请返回mode=diagnosis和根因/影响/建议。"
            ),
            "runtime_batch_confirm_policy": (
                "当批量命令同时包含“可直接执行命令”和“待确认命令”时："
                "系统会先执行确认前的前置命令，再将后续命令作为同一批次待确认；"
                "用户确认一次即可执行整批命令。"
            ),
            "runtime_output_compaction_policy": (
                "运行期输出压缩策略："
                "优先使用过滤/匹配命令减少回显（include/exclude/begin/section/count/match/grep/regex）；"
                "先摘要后细查，避免一次抓取全量配置；"
                "每轮命令建议2-5条并确保目标明确。"
            ),
        }

    async def stream_message(self, session_id: str, user_content: str) -> AsyncIterator[str]:
        try:
            session = self.store.get_session(session_id)
            user_trace = self._trace_start(
                session_id=session_id,
                step_type="user_input",
                title="接收用户请求",
                detail=user_content[:220],
            )
            preferred_mode = self._preferred_mode_from_session(session.operation_mode)
            self.store.clear_summary(session_id)
            if not self.store.list_commands(session_id):
                self.store.reset_ai_context(session_id)

            user_message = Message(session_id=session_id, role="user", content=user_content)
            self.store.add_message(user_message)
            self._trace_finish(user_trace, status="succeeded")
            yield self._sse("message_ack", {"message": user_message.model_dump(mode="json")})

            if not self.deepseek_diagnoser.enabled:
                unavailable_trace = self._trace_start(
                    session_id=session_id,
                    step_type="llm_status",
                    title="检查 LLM 可用性",
                )
                summary = self._build_llm_unavailable_summary(session_id)
                self.store.set_summary(summary)
                self._trace_finish(unavailable_trace, status="failed", detail="LLM disabled")
                assistant_message = Message(
                    session_id=session_id,
                    role="assistant",
                    content="当前 LLM 服务不可用，无法执行有效诊断。请恢复模型服务后重试。",
                )
                self.store.add_message(assistant_message)
                yield self._sse(
                    "final_summary",
                    {"message": assistant_message.model_dump(mode="json"), "summary": summary.model_dump(mode="json")},
                )
                return

            async for event in self._run_autonomous_loop(session_id, user_content, preferred_mode):
                yield event

            if self._has_pending_confirmation(session_id):
                # Keep the session in "waiting confirmation" state.
                # Do not force a final summary yet, otherwise AI output will look disconnected
                # from the execution pipeline.
                return

            summary = self.store.get_summary(session_id)
            if summary is None:
                final_trace = self._trace_start(
                    session_id=session_id,
                    step_type="llm_final",
                    title="请求最终总结",
                )
                summary = await self._request_final_from_context(session_id, preferred_mode=preferred_mode)
                if summary is None:
                    summary = self._build_no_conclusion_summary(session_id)
                    self._trace_finish(final_trace, status="failed", detail="LLM未返回final")
                else:
                    self._trace_finish(final_trace, status="succeeded")
                self.store.set_summary(summary)

            assistant_message = Message(
                session_id=session_id,
                role="assistant",
                content=self._render_final_message(summary),
            )
            self.store.add_message(assistant_message)

            yield self._sse(
                "final_summary",
                {"message": assistant_message.model_dump(mode="json"), "summary": summary.model_dump(mode="json")},
            )
        except Exception as exc:  # pragma: no cover - defensive stream guard
            failed_trace = self._trace_start(
                session_id=session_id,
                step_type="orchestrator_error",
                title="会话执行异常",
            )
            summary = self._build_execution_failure_summary(session_id, str(exc))
            self.store.set_summary(summary)
            self._trace_finish(failed_trace, status="failed", detail=str(exc)[:240])
            assistant_message = Message(
                session_id=session_id,
                role="assistant",
                content=self._render_final_message(summary, interrupted=True),
            )
            self.store.add_message(assistant_message)
            yield self._sse(
                "final_summary",
                {"message": assistant_message.model_dump(mode="json"), "summary": summary.model_dump(mode="json")},
            )

    async def _run_autonomous_loop(
        self,
        session_id: str,
        user_content: str,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> AsyncIterator[str]:
        self._sync_risk_policy()
        session = self.store.get_session(session_id)
        self._append_user_problem_to_ai_context(
            session_id,
            session.device.vendor,
            session.device.protocol.value,
            user_content,
            str(getattr(session.operation_mode, "value", session.operation_mode)),
        )
        step_no = len(self.store.list_commands(session_id))

        if step_no == 0:
            bootstrap_command = self._bootstrap_command_for_vendor(session.device.vendor)
            bootstrap = CommandExecution(
                session_id=session_id,
                step_no=1,
                title="基础信息采集",
                command=bootstrap_command,
                adapter_type=session.device.protocol,
                risk_level=self.risk_engine.decide(bootstrap_command, session.automation_level).risk_level,
                requires_confirmation=False,
                status=CommandStatus.queued,
            )
            async for event in self._execute_with_policy(session, bootstrap):
                yield event
            self._append_command_result_to_ai_context(session_id, bootstrap)
            if bootstrap.status == CommandStatus.failed:
                self.store.set_summary(self._build_execution_failure_summary(session_id, bootstrap.error or "设备执行失败"))
                return
            step_no = 1

        for iteration in range(1, self.max_autonomous_steps + 1):
            commands = self.store.list_commands(session_id)
            evidences = self.store.list_evidence(session_id)
            ai_context = self.store.list_ai_context(session_id)
            llm_trace = self._trace_start(
                session_id=session_id,
                step_type="llm_plan",
                title=f"LLM 规划第 {iteration} 轮",
            )
            plan = await self.deepseek_diagnoser.propose_next_step(
                session=session,
                user_problem=user_content,
                commands=commands,
                evidences=evidences,
                iteration=iteration,
                max_iterations=self.max_autonomous_steps,
                conversation_history=ai_context,
            )

            if not plan:
                self._trace_finish(llm_trace, status="failed", detail="LLM未返回可解析计划")
                break
            self._trace_finish(
                llm_trace,
                status="succeeded",
                detail=f"decision={str(plan.get('decision', ''))}",
            )
            self.store.append_ai_context(session_id, "assistant", json.dumps(plan, ensure_ascii=False))

            if plan.get("decision") == "final":
                summary = self._summary_from_plan(session_id, plan, preferred_mode=preferred_mode)
                if summary:
                    self.store.set_summary(summary)
                break

            plan_commands = self._extract_plan_commands(plan, next_step_no=step_no + 1)
            if not plan_commands:
                break

            plan_batch_id = make_id() if ("commands" in plan or len(plan_commands) > 1) else None
            plan_batch_total = len(plan_commands) if plan_batch_id else None
            batch_commands: list[CommandExecution] = []
            for index, (title, next_command) in enumerate(plan_commands, start=1):
                step_no += 1
                decision = self.risk_engine.decide(next_command, session.automation_level)
                batch_commands.append(
                    CommandExecution(
                        session_id=session_id,
                        step_no=step_no,
                        title=title,
                        command=next_command,
                        adapter_type=session.device.protocol,
                        risk_level=decision.risk_level,
                        requires_confirmation=decision.requires_confirmation,
                        status=CommandStatus.queued,
                        batch_id=plan_batch_id,
                        batch_index=index if plan_batch_id else None,
                        batch_total=plan_batch_total,
                    )
                )

            blocked_or_pending = False
            if len(batch_commands) > 1:
                async for event in self._execute_batch_with_policy(session, batch_commands):
                    if "command_blocked" in event or "command_pending_confirmation" in event:
                        blocked_or_pending = True
                    yield event
            else:
                async for event in self._execute_with_policy(session, batch_commands[0]):
                    if "command_blocked" in event or "command_pending_confirmation" in event:
                        blocked_or_pending = True
                    yield event

            for command in batch_commands:
                self._append_command_result_to_ai_context(session_id, command)

            if blocked_or_pending:
                break
            if any(command.status == CommandStatus.failed for command in batch_commands):
                # Keep autonomous loop alive: feed execution failure back to AI so it can self-correct.
                continue

    async def _execute_with_policy(
        self,
        session,
        command: CommandExecution,
    ) -> AsyncIterator[str]:
        execution_trace = self._trace_start(
            session_id=command.session_id,
            step_type="command_execution",
            title=f"执行命令 #{command.step_no}: {command.title}",
            command_id=command.id,
            detail=command.command[:260],
        )
        policy = self.store.get_command_policy()
        decision = self._decide_execution_action(session, command.command, policy)
        command.risk_level = decision["risk_level"]
        command.requires_confirmation = decision["action"] == "confirm"

        if decision["action"] == "blocked":
            command.status = CommandStatus.blocked
            command.error = decision["reason"]
            self.store.add_command(command)
            command.completed_at = now_utc()
            command.duration_ms = 0
            self._trace_finish(execution_trace, status="blocked", detail=decision["reason"])
            yield self._sse(
                "command_blocked",
                {
                    "command": command.model_dump(mode="json"),
                    "reason": decision["reason"],
                },
            )
            return

        if decision["action"] == "confirm":
            self._trace_finish(execution_trace, status="pending_confirm", detail=decision["reason"])
            yield self._mark_pending_confirmation(command, decision["reason"])
            return

        self.store.add_command(command)
        try:
            adapter = await self._get_session_adapter(session)
            await self._execute_and_record(adapter, command)
            self._trace_finish(
                execution_trace,
                status=command.status.value,
                detail=(command.error or command.output or "")[:300],
            )
        except Exception as exc:
            command.status = CommandStatus.failed
            command.error = str(exc)
            command.completed_at = now_utc()
            if command.started_at:
                command.duration_ms = max(
                    0,
                    int((command.completed_at - command.started_at).total_seconds() * 1000),
                )
            self.store.update_command(command)
            self._trace_finish(execution_trace, status="failed", detail=str(exc)[:300])
            await self._drop_session_adapter(session.id)
        yield self._sse("command_completed", {"command": command.model_dump(mode="json")})

    async def _execute_batch_with_policy(
        self,
        session,
        commands: list[CommandExecution],
    ) -> AsyncIterator[str]:
        if not commands:
            return

        execution_trace = self._trace_start(
            session_id=commands[0].session_id,
            step_type="command_execution",
            title=f"执行批量命令 ({len(commands)} 条)",
            detail=" ; ".join(command.command for command in commands)[:260],
        )

        policy = self.store.get_command_policy()
        blocked_reasons: dict[str, str] = {}
        decisions: list[dict[str, object]] = []
        first_confirm_idx: int | None = None
        confirm_reason = "批量命令包含待确认项，请确认后继续执行后续命令。"

        for idx, command in enumerate(commands):
            decision = self._decide_execution_action(session, command.command, policy)
            decisions.append(decision)
            command.risk_level = decision["risk_level"]
            command.requires_confirmation = decision["action"] == "confirm"

            if decision["action"] == "blocked":
                blocked_reasons[command.id] = decision["reason"]
                continue
            if decision["action"] == "confirm":
                if first_confirm_idx is None:
                    first_confirm_idx = idx
                    confirm_reason = str(decision["reason"] or confirm_reason)

        if blocked_reasons:
            first_blocked = next((item for item in commands if item.id in blocked_reasons), commands[0])
            batch_reason = (
                f"Batch blocked because command is blocked: {first_blocked.command[:120]}"
            )
            for command in commands:
                command.status = CommandStatus.blocked
                command.error = blocked_reasons.get(command.id, batch_reason)
                command.completed_at = now_utc()
                command.duration_ms = 0
                self.store.add_command(command)
                yield self._sse(
                    "command_blocked",
                    {
                        "command": command.model_dump(mode="json"),
                        "reason": command.error,
                    },
                )
            self._trace_finish(execution_trace, status="blocked", detail=batch_reason[:300])
            return

        if first_confirm_idx is not None:
            precheck_commands = commands[:first_confirm_idx]
            pending_commands = commands[first_confirm_idx:]
            adapter = None

            if precheck_commands:
                for command in precheck_commands:
                    self.store.add_command(command)

                try:
                    adapter = await self._get_session_adapter(session)
                    for command in precheck_commands:
                        await self._execute_and_record(adapter, command)
                        yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
                        if command.status == CommandStatus.failed:
                            self._trace_finish(execution_trace, status="failed", detail=(command.error or "")[:300])
                            return
                except Exception as exc:
                    failed_at = now_utc()
                    for command in precheck_commands:
                        if command.status in {CommandStatus.succeeded, CommandStatus.failed}:
                            continue
                        command.status = CommandStatus.failed
                        command.error = str(exc)
                        command.completed_at = failed_at
                        if command.started_at:
                            command.duration_ms = max(0, int((failed_at - command.started_at).total_seconds() * 1000))
                        self.store.update_command(command)
                        yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
                    self._trace_finish(execution_trace, status="failed", detail=str(exc)[:300])
                    await self._drop_session_adapter(session.id)
                    return

            reason = confirm_reason or "批量命令包含未命中可执行规则的命令，请确认后整批执行。"
            self._trace_finish(execution_trace, status="pending_confirm", detail=reason[:300])
            for idx, command in enumerate(pending_commands):
                pending_reason = reason if idx == 0 else "批量命令等待统一确认"
                yield self._mark_pending_confirmation(command, pending_reason)
            return

        for command in commands:
            self.store.add_command(command)

        try:
            adapter = await self._get_session_adapter(session)
            await self._execute_batch_and_record(adapter, commands)
        except Exception as exc:
            failed_at = now_utc()
            for command in commands:
                command.status = CommandStatus.failed
                command.error = str(exc)
                command.completed_at = failed_at
                if command.started_at:
                    command.duration_ms = max(0, int((failed_at - command.started_at).total_seconds() * 1000))
                self.store.update_command(command)
            self._trace_finish(execution_trace, status="failed", detail=str(exc)[:300])
            await self._drop_session_adapter(session.id)
        else:
            first_error = next((command.error for command in commands if command.status == CommandStatus.failed), "")
            if first_error:
                self._trace_finish(execution_trace, status="failed", detail=first_error[:300])
            else:
                output_brief = next((command.output for command in commands if command.output), "")
                self._trace_finish(execution_trace, status="succeeded", detail=(output_brief or "")[:300])

        for command in commands:
            yield self._sse("command_completed", {"command": command.model_dump(mode="json")})

    def _decide_execution_action(self, session, command_text: str, policy: CommandPolicy) -> dict[str, object]:
        self._sync_risk_policy()
        policy_decision = self.command_policy_engine.evaluate(command_text, policy)
        risk_decision = self.risk_engine.decide(command_text, session.automation_level)
        risk_level = risk_decision.risk_level

        # 1) Explicit block-rules always win.
        if policy_decision.result == "blocked":
            return {"action": "blocked", "reason": policy_decision.reason, "risk_level": risk_level}

        # 2) Mode/risk baseline gate (includes hard-block and read-only baseline).
        if not risk_decision.allowed:
            return {"action": "blocked", "reason": risk_decision.reason, "risk_level": risk_level}

        # 3) Full-auto executes all non-hard-block commands.
        if session.automation_level == AutomationLevel.full_auto:
            return {"action": "allow", "reason": "Full-auto executes non-hard-block commands.", "risk_level": risk_level}

        # 4) Explicit executable rules can bypass assisted-mode high-risk confirmation.
        if policy_decision.result == "allowed":
            return {"action": "allow", "reason": policy_decision.reason, "risk_level": risk_level}

        # 5) In assisted mode, non-whitelisted high-risk commands need confirmation.
        if session.automation_level == AutomationLevel.assisted and risk_level == RiskLevel.high:
            return {"action": "confirm", "reason": "半自动模式下高风险命令需要人工确认。", "risk_level": risk_level}

        # 6) Any unmatched command in non-full-auto mode requires operator confirmation.
        if policy_decision.result == "needs_confirmation":
            return {"action": "confirm", "reason": policy_decision.reason, "risk_level": risk_level}

        return {"action": "allow", "reason": "Command allowed.", "risk_level": risk_level}

    def _sync_risk_policy(self) -> None:
        policy = self.store.get_risk_policy()
        self.risk_engine.update_policy(
            high_risk_patterns=policy.high_risk_patterns,
            medium_risk_patterns=policy.medium_risk_patterns,
        )

    async def _execute_batch_and_record(self, adapter, commands: list[CommandExecution]) -> None:
        started = now_utc()
        for command in commands:
            command.status = CommandStatus.running
            command.started_at = started
            command.completed_at = None
            command.duration_ms = None
            self.store.update_command(command)

        merged_command = " ; ".join(command.command for command in commands)
        try:
            output = await adapter.run_command(merged_command)
            completed = now_utc()
            duration_ms = max(0, int((completed - started).total_seconds() * 1000))
            for command in commands:
                category, parsed_data, conclusion = parse_command_output(command.command, output)
                parsed_device_name = str(parsed_data.get("device_name") or "").strip() if isinstance(parsed_data, dict) else ""
                if parsed_device_name:
                    self.store.update_session_device_name(command.session_id, parsed_device_name)
                command.output = output
                command.status = CommandStatus.succeeded
                command.completed_at = completed
                command.duration_ms = duration_ms
                self.store.update_command(command)

                evidence = Evidence(
                    session_id=command.session_id,
                    command_id=command.id,
                    category=category,
                    raw_output=output,
                    parsed_data=parsed_data,
                    conclusion=conclusion,
                )
                self.store.add_evidence(evidence)
        except Exception as exc:  # pragma: no cover
            completed = now_utc()
            duration_ms = max(0, int((completed - started).total_seconds() * 1000))
            for command in commands:
                command.status = CommandStatus.failed
                command.error = str(exc)
                command.completed_at = completed
                command.duration_ms = duration_ms
                self.store.update_command(command)

    def _bootstrap_command_for_vendor(self, vendor: str) -> str:
        normalized = (vendor or "").strip().lower()
        if "huawei" in normalized:
            return "display version"
        return "show version"

    def _extract_plan_commands(self, plan: dict, *, next_step_no: int) -> list[tuple[str, str]]:
        base_title = str(plan.get("title", "")).strip()
        raw_commands = plan.get("commands")
        parsed: list[tuple[str, str]] = []

        if isinstance(raw_commands, list):
            for idx, item in enumerate(raw_commands, start=1):
                command_text = ""
                title = ""
                if isinstance(item, str):
                    command_text = item.strip()
                elif isinstance(item, dict):
                    command_text = str(item.get("command", "")).strip()
                    title = str(item.get("title", "")).strip()

                if not command_text:
                    continue

                expanded_commands = self._split_compound_commands(command_text)
                for sub_idx, sub_command in enumerate(expanded_commands, start=1):
                    current_title = title
                    if not current_title:
                        if base_title and len(raw_commands) > 1:
                            current_title = f"{base_title}#{idx}"
                        else:
                            current_title = base_title or f"AI诊断步骤{next_step_no + len(parsed)}"
                    if len(expanded_commands) > 1:
                        current_title = f"{current_title}.{sub_idx}"
                    parsed.append((current_title, sub_command))

        if parsed:
            return parsed

        single_command = str(plan.get("command", "")).strip()
        if not single_command:
            return []
        title = base_title or f"AI诊断步骤{next_step_no}"
        expanded_single = self._split_compound_commands(single_command)
        if len(expanded_single) <= 1:
            return [(title, single_command)]
        return [(f"{title}.{idx}", cmd) for idx, cmd in enumerate(expanded_single, start=1)]

    def _split_compound_commands(self, command_text: str) -> list[str]:
        text = (command_text or "").strip()
        if not text:
            return []

        chunks: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if ";" in line:
                for piece in line.split(";"):
                    piece = piece.strip()
                    if piece:
                        chunks.append(piece)
            else:
                chunks.append(line)

        return chunks or [text]

    def _summary_from_plan(
        self,
        session_id: str,
        plan: dict,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> IncidentSummary | None:
        mode = str(plan.get("mode") or plan.get("task_type") or "diagnosis").strip().lower()
        if mode not in {"diagnosis", "query", "config", "unavailable", "error"}:
            mode = "diagnosis"
        if preferred_mode in {"diagnosis", "query", "config"} and mode in {"diagnosis", "query", "config"}:
            mode = preferred_mode

        query_result = str(plan.get("query_result") or plan.get("answer") or plan.get("result") or "").strip()
        follow_up_action = str(plan.get("follow_up_action") or plan.get("recommendation") or "").strip()

        root_cause = str(plan.get("root_cause", "")).strip()
        impact_scope = str(plan.get("impact_scope", "")).strip()
        recommendation = str(plan.get("recommendation", "")).strip()

        if mode in {"query", "config"}:
            if not query_result:
                query_result = root_cause or impact_scope or recommendation
            if not query_result:
                return None
            if not follow_up_action:
                follow_up_action = "如需继续，请提供下一步目标。"
            root_cause = query_result
            impact_scope = impact_scope or ("信息查询任务" if mode == "query" else "配置任务")
            recommendation = follow_up_action
        elif not root_cause or not impact_scope or not recommendation:
            return None

        confidence = None
        try:
            raw_confidence = plan.get("confidence")
            if raw_confidence is not None:
                confidence = max(0.0, min(1.0, float(raw_confidence)))
        except Exception:
            confidence = None

        evidence_refs_raw = plan.get("evidence_refs", [])
        evidence_refs: list[dict] = []
        if isinstance(evidence_refs_raw, list):
            for item in evidence_refs_raw:
                if isinstance(item, dict):
                    evidence_refs.append(item)
                elif isinstance(item, str):
                    evidence_refs.append({"quote": item})

        return IncidentSummary(
            session_id=session_id,
            mode=mode,
            root_cause=root_cause,
            impact_scope=impact_scope,
            recommendation=recommendation,
            query_result=query_result or None,
            follow_up_action=follow_up_action or None,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )

    def _append_user_problem_to_ai_context(
        self,
        session_id: str,
        vendor: str,
        protocol: str,
        user_content: str,
        operation_mode: str,
    ) -> None:
        sanitized_content = self._sanitize_for_llm(user_content)
        header = (
            f"会话ID: {session_id}\n"
            f"当前设备厂商: {vendor}\n"
            f"接入协议: {protocol}\n"
            f"会话模式: {operation_mode}\n"
            "请只基于当前会话上下文继续诊断，禁止引用或臆测其他会话。\n"
            f"用户问题: {sanitized_content}"
        )
        self.store.append_ai_context(session_id, "user", header)

    def _append_command_result_to_ai_context(self, session_id: str, command: CommandExecution) -> None:
        text = (
            f"执行结果\n"
            f"- step: {command.step_no}\n"
            f"- title: {command.title}\n"
            f"- command: {command.command}\n"
            f"- status: {command.status.value}\n"
        )
        if command.error:
            text += f"- error: {self._sanitize_for_llm(command.error)[:1200]}\n"
        if command.output:
            text += f"- output:\n{self._sanitize_for_llm(command.output)[:2500]}"
        self.store.append_ai_context(session_id, "user", text)

    def _sanitize_for_llm(self, text: str) -> str:
        if not text:
            return text

        sanitized = text
        patterns = [
            (
                re.compile(
                    r"(?i)\b(username|user|account|login|password|passwd|pwd|token|api[_-]?key|secret)\b\s*[:=：]?\s*([^\s,;，。]+)"
                ),
                r"\1 [REDACTED]",
            ),
            (
                re.compile(r"(账号|用户名|密码|口令|令牌|密钥)\s*[:：]?\s*([^\s,;，。]+)", re.IGNORECASE),
                r"\1 [REDACTED]",
            ),
        ]
        for pattern, replacement in patterns:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    async def confirm_command(self, session_id: str, command_id: str, request: ConfirmCommandRequest) -> ConfirmCommandResponse:
        session = self.store.get_session(session_id)
        command = self.store.get_command(session_id, command_id)

        if command.status != CommandStatus.pending_confirm:
            return ConfirmCommandResponse(
                command_id=command.id,
                status=command.status,
                message="Command is not waiting for confirmation.",
            )

        if not request.approved:
            command.status = CommandStatus.rejected
            command.error = "Rejected by operator"
            self.store.update_command(command)
            if command.batch_id:
                for queued in self.store.list_commands(session_id):
                    if (
                        queued.id != command.id
                        and queued.batch_id == command.batch_id
                        and queued.status == CommandStatus.pending_confirm
                    ):
                        queued.status = CommandStatus.rejected
                        queued.error = "Rejected by operator (batch)"
                        queued.completed_at = now_utc()
                        queued.duration_ms = 0
                        self.store.update_command(queued)
            return ConfirmCommandResponse(
                command_id=command.id,
                status=command.status,
                message="High-risk command was rejected.",
            )

        pending_targets = [command]
        if command.batch_id:
            pending_targets = sorted(
                [
                    item
                    for item in self.store.list_commands(session_id)
                    if item.batch_id == command.batch_id and item.status == CommandStatus.pending_confirm
                ],
                key=lambda item: (item.batch_index or item.step_no, item.step_no),
            )

        if len(pending_targets) > 1:
            await self._execute_confirmed_batch(session_id, session, pending_targets)
        else:
            await self._execute_confirmed_command(session_id, session, pending_targets[0])

        failed = next((item for item in pending_targets if item.status == CommandStatus.failed), None)
        if failed:
            self.store.set_summary(self._build_execution_failure_summary(session_id, failed.error or "设备执行失败"))
        else:
            preferred_mode = self._preferred_mode_from_session(session.operation_mode)
            user_problem = self._latest_user_problem(session_id)
            async for _ in self._run_autonomous_loop(
                session_id,
                user_problem,
                preferred_mode,
            ):
                pass

            if not self._has_pending_confirmation(session_id):
                summary = self.store.get_summary(session_id)
                if summary is None:
                    summary = await self._request_final_from_context(session_id, preferred_mode=preferred_mode)
                self.store.set_summary(summary or self._build_no_conclusion_summary(session_id))

        message = "Command approved and executed."
        if command.batch_id and len(pending_targets) > 1:
            message = f"Batch approved. Executed {len(pending_targets)} commands."
        if failed:
            message = "Command execution failed."

        return ConfirmCommandResponse(
            command_id=command.id,
            status=command.status,
            message=message,
        )

    async def _execute_and_record(self, adapter, command: CommandExecution) -> None:
        command.status = CommandStatus.running
        command.started_at = now_utc()
        command.completed_at = None
        command.duration_ms = None
        self.store.update_command(command)

        try:
            output = await adapter.run_command(command.command)
            category, parsed_data, conclusion = parse_command_output(command.command, output)
            parsed_device_name = str(parsed_data.get("device_name") or "").strip() if isinstance(parsed_data, dict) else ""
            if parsed_device_name:
                self.store.update_session_device_name(command.session_id, parsed_device_name)
            command.output = output
            # Keep transport/execution semantics simple: command is successful if SSH/API execution returned.
            # Whether CLI syntax is semantically correct is left for the LLM to judge from raw output.
            command.status = CommandStatus.succeeded
            command.completed_at = now_utc()
            command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
            self.store.update_command(command)

            evidence = Evidence(
                session_id=command.session_id,
                command_id=command.id,
                category=category,
                raw_output=output,
                parsed_data=parsed_data,
                conclusion=conclusion,
            )
            self.store.add_evidence(evidence)
        except Exception as exc:  # pragma: no cover
            command.status = CommandStatus.failed
            command.error = str(exc)
            command.completed_at = now_utc()
            command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
            self.store.update_command(command)

    async def _get_session_adapter(self, session):
        adapter = self._session_adapters.get(session.id)
        if adapter is None:
            adapter = build_adapter(session, allow_simulation=self.allow_simulation)
            self._session_adapters[session.id] = adapter
        await adapter.connect()
        return adapter

    async def _drop_session_adapter(self, session_id: str) -> None:
        adapter = self._session_adapters.pop(session_id, None)
        if adapter is None:
            return
        try:
            await adapter.close()
        except Exception:
            pass

    async def _request_final_from_context(
        self,
        session_id: str,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> IncidentSummary | None:
        session = self.store.get_session(session_id)
        ai_context = list(self.store.list_ai_context(session_id))
        if not ai_context:
            return None

        mode_hint = ""
        if preferred_mode == "query":
            mode_hint = "当前用户意图偏向信息查询，请优先输出mode=query。"
        elif preferred_mode == "diagnosis":
            mode_hint = "当前用户意图偏向故障诊断，请优先输出mode=diagnosis。"
        elif preferred_mode == "config":
            mode_hint = "当前用户意图偏向配置任务，请优先输出mode=config。"

        final_prompt = (
            "请基于当前会话已有证据直接给出最终结论。"
            "只允许输出decision=final，不要再输出run_command。"
            "如果用户是信息查询任务，请返回mode=query和query_result；"
            "如果用户是配置任务，请返回mode=config和query_result；"
            "如果是故障诊断任务，请返回mode=diagnosis和根因/影响/建议。"
            f"{mode_hint}"
        )
        ai_context.append({"role": "user", "content": final_prompt})
        plan = await self.deepseek_diagnoser.propose_next_step(
            session=session,
            user_problem="",
            commands=self.store.list_commands(session_id),
            evidences=self.store.list_evidence(session_id),
            iteration=self.max_autonomous_steps + 1,
            max_iterations=self.max_autonomous_steps,
            conversation_history=ai_context,
        )
        if not plan:
            return None
        if str(plan.get("decision", "")).strip().lower() != "final":
            return None
        return self._summary_from_plan(session_id, plan, preferred_mode=preferred_mode)

    def _build_llm_unavailable_summary(self, session_id: str) -> IncidentSummary:
        return IncidentSummary(
            session_id=session_id,
            mode="unavailable",
            root_cause="LLM 服务不可用，无法生成有效根因诊断。",
            impact_scope="无法评估。",
            recommendation="请检查模型 API 可用性与密钥配置后重试。",
        )

    def _build_execution_failure_summary(self, session_id: str, error: str) -> IncidentSummary:
        reason = (error or "设备连接或命令执行失败").strip()
        compact = reason.replace("\n", " ")
        return IncidentSummary(
            session_id=session_id,
            mode="error",
            root_cause=f"设备连接或执行失败：{compact[:240]}",
            impact_scope="无法获取有效设备证据，诊断未完成。",
            recommendation="请检查 SSH 可达性、账号权限、设备会话模式与命令回显后重试。",
        )

    def _build_no_conclusion_summary(self, session_id: str) -> IncidentSummary:
        return IncidentSummary(
            session_id=session_id,
            mode="error",
            root_cause="模型未返回可用最终结论。",
            impact_scope="无法形成完整诊断闭环。",
            recommendation="请重试一次，或补充更具体的问题描述后继续同会话诊断。",
        )

    def _render_final_message(self, summary: IncidentSummary, interrupted: bool = False) -> str:
        if summary.mode == "query":
            result = summary.query_result or summary.root_cause
            follow = summary.follow_up_action or summary.recommendation
            return f"查询完成。结果: {result}。后续: {follow}"
        if summary.mode == "config":
            result = summary.query_result or summary.root_cause
            follow = summary.follow_up_action or summary.recommendation
            return f"配置完成。结果: {result}。后续: {follow}"
        if interrupted:
            return f"诊断中断。根因判断: {summary.root_cause}。影响范围: {summary.impact_scope}。建议: {summary.recommendation}"
        return f"诊断完成。根因判断: {summary.root_cause}。影响范围: {summary.impact_scope}。建议: {summary.recommendation}"

    def _preferred_mode_from_session(self, operation_mode) -> Literal["query", "diagnosis", "config"]:
        value = str(getattr(operation_mode, "value", operation_mode or "")).strip().lower()
        if value == "query":
            return "query"
        if value == "config":
            return "config"
        return "diagnosis"

    def _has_pending_confirmation(self, session_id: str) -> bool:
        return any(cmd.status == CommandStatus.pending_confirm for cmd in self.store.list_commands(session_id))

    def _latest_user_problem(self, session_id: str) -> str:
        messages = self.store.list_messages(session_id)
        for message in reversed(messages):
            if message.role == "user" and message.content.strip():
                return message.content.strip()
        return ""

    def _sse(self, event: str, payload: dict) -> str:
        envelope = EventEnvelope(event=event, payload=payload)
        return f"event: {envelope.event}\ndata: {json.dumps(envelope.payload, ensure_ascii=False)}\n\n"

    def _mark_pending_confirmation(self, command: CommandExecution, reason: str) -> str:
        command.status = CommandStatus.pending_confirm
        command.requires_confirmation = True
        command.completed_at = now_utc()
        command.duration_ms = 0
        self.store.add_command(command)
        return self._sse(
            "command_pending_confirmation",
            {
                "command": command.model_dump(mode="json"),
                "reason": reason,
            },
        )

    async def _execute_confirmed_batch(self, session_id: str, session, commands: list[CommandExecution]) -> None:
        confirm_trace = None
        try:
            adapter = await self._get_session_adapter(session)
            confirm_trace = self._trace_start(
                session_id=session_id,
                step_type="command_confirm_execution",
                title=f"执行确认批次命令 ({len(commands)} 条)",
                detail=" ; ".join(command.command for command in commands)[:260],
            )
            await self._execute_batch_and_record(adapter, commands)
            first_error = next((command.error for command in commands if command.status == CommandStatus.failed), "")
            if first_error:
                self._trace_finish(confirm_trace, status="failed", detail=first_error[:300])
            else:
                first_output = next((command.output for command in commands if command.output), "")
                self._trace_finish(confirm_trace, status="succeeded", detail=(first_output or "")[:300])
            for command in commands:
                self._append_command_result_to_ai_context(session_id, command)
        except Exception as exc:
            failed_at = now_utc()
            for command in commands:
                command.status = CommandStatus.failed
                command.error = str(exc)
                command.completed_at = failed_at
                if command.started_at:
                    command.duration_ms = max(
                        0,
                        int((command.completed_at - command.started_at).total_seconds() * 1000),
                    )
                self.store.update_command(command)
            if confirm_trace is None:
                confirm_trace = self._trace_start(
                    session_id=session_id,
                    step_type="command_confirm_execution",
                    title=f"执行确认批次命令 ({len(commands)} 条)",
                )
            self._trace_finish(confirm_trace, status="failed", detail=str(exc)[:300])
            await self._drop_session_adapter(session.id)

    async def _execute_confirmed_command(self, session_id: str, session, command: CommandExecution) -> None:
        confirm_trace = None
        try:
            adapter = await self._get_session_adapter(session)
            confirm_trace = self._trace_start(
                session_id=session_id,
                step_type="command_confirm_execution",
                title=f"执行确认命令 #{command.step_no}: {command.title}",
                command_id=command.id,
                detail=command.command[:260],
            )
            await self._execute_and_record(adapter, command)
            self._trace_finish(
                confirm_trace,
                status=command.status.value,
                detail=(command.error or command.output or "")[:300],
            )
            self._append_command_result_to_ai_context(session_id, command)
        except Exception as exc:
            command.status = CommandStatus.failed
            command.error = str(exc)
            command.completed_at = now_utc()
            if command.started_at:
                command.duration_ms = max(
                    0,
                    int((command.completed_at - command.started_at).total_seconds() * 1000),
                )
            self.store.update_command(command)
            if confirm_trace is None:
                confirm_trace = self._trace_start(
                    session_id=session_id,
                    step_type="command_confirm_execution",
                    title=f"执行确认命令 #{command.step_no}: {command.title}",
                    command_id=command.id,
                )
            self._trace_finish(confirm_trace, status="failed", detail=str(exc)[:300])
            await self._drop_session_adapter(session.id)

    def _trace_start(
        self,
        *,
        session_id: str,
        step_type: str,
        title: str,
        command_id: str | None = None,
        detail: str | None = None,
    ) -> ServiceTraceStep:
        step = ServiceTraceStep(
            session_id=session_id,
            seq_no=self.store.next_trace_seq(session_id),
            step_type=step_type,
            title=title,
            status="running",
            command_id=command_id,
            detail=detail,
        )
        self.store.add_trace_step(step)
        self._trace_perf_started[step.id] = time.perf_counter()
        return step

    def _trace_finish(self, step: ServiceTraceStep, *, status: str, detail: str | None = None) -> None:
        started = self._trace_perf_started.pop(step.id, None)
        step.status = status
        step.completed_at = now_utc()
        if started is not None:
            step.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        elif step.started_at:
            step.duration_ms = max(0, int((step.completed_at - step.started_at).total_seconds() * 1000))
        if detail:
            step.detail = detail
        self.store.update_trace_step(step)
