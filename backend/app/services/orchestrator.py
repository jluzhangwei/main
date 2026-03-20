from __future__ import annotations

import json
import os
import re
from typing import AsyncIterator, Literal

from app.models.schemas import (
    CommandExecution,
    CommandStatus,
    ConfirmCommandRequest,
    ConfirmCommandResponse,
    Evidence,
    EventEnvelope,
    IncidentSummary,
    Message,
)
from app.services.adapters import build_adapter
from app.services.deepseek_diagnoser import DeepSeekDiagnoser
from app.services.parsers import parse_command_output
from app.services.risk_engine import RiskEngine
from app.services.store import InMemoryStore


class ConversationOrchestrator:
    def __init__(self, store: InMemoryStore, *, allow_simulation: bool = True):
        self.store = store
        self.risk_engine = RiskEngine()
        self.deepseek_diagnoser = DeepSeekDiagnoser()
        self.allow_simulation = allow_simulation
        self.max_autonomous_steps = int(os.getenv("AUTONOMOUS_MAX_STEPS", "8"))

    async def stream_message(self, session_id: str, user_content: str) -> AsyncIterator[str]:
        try:
            session = self.store.get_session(session_id)
            preferred_mode = self._preferred_mode_from_session(session.operation_mode)
            self.store.clear_summary(session_id)
            if not self.store.list_commands(session_id):
                self.store.reset_ai_context(session_id)

            user_message = Message(session_id=session_id, role="user", content=user_content)
            self.store.add_message(user_message)
            yield self._sse("message_ack", {"message": user_message.model_dump(mode="json")})

            if not self.deepseek_diagnoser.enabled:
                summary = self._build_llm_unavailable_summary(session_id)
                self.store.set_summary(summary)
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

            summary = self.store.get_summary(session_id)
            if summary is None:
                summary = await self._request_final_from_context(session_id, preferred_mode=preferred_mode)
                if summary is None:
                    summary = self._build_no_conclusion_summary(session_id)
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
            summary = self._build_execution_failure_summary(session_id, str(exc))
            self.store.set_summary(summary)
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
                break
            self.store.append_ai_context(session_id, "assistant", json.dumps(plan, ensure_ascii=False))

            if plan.get("decision") == "final":
                summary = self._summary_from_plan(session_id, plan, preferred_mode=preferred_mode)
                if summary:
                    self.store.set_summary(summary)
                break

            next_command = str(plan.get("command", "")).strip()
            if not next_command:
                break

            if self._looks_duplicate_command(next_command, commands):
                continue

            step_no += 1
            title = str(plan.get("title", "")).strip() or f"AI诊断步骤{step_no}"
            decision = self.risk_engine.decide(next_command, session.automation_level)
            command = CommandExecution(
                session_id=session_id,
                step_no=step_no,
                title=title,
                command=next_command,
                adapter_type=session.device.protocol,
                risk_level=decision.risk_level,
                requires_confirmation=decision.requires_confirmation,
                status=CommandStatus.queued,
            )
            blocked_or_pending = False
            async for event in self._execute_with_policy(session, command):
                if "command_blocked" in event or "command_pending_confirmation" in event:
                    blocked_or_pending = True
                yield event
            self._append_command_result_to_ai_context(session_id, command)
            if command.status == CommandStatus.failed:
                self.store.set_summary(self._build_execution_failure_summary(session_id, command.error or "设备执行失败"))
                break
            if blocked_or_pending:
                break

    async def _execute_with_policy(self, session, command: CommandExecution) -> AsyncIterator[str]:
        decision = self.risk_engine.decide(command.command, session.automation_level)
        command.risk_level = decision.risk_level
        command.requires_confirmation = decision.requires_confirmation

        if not decision.allowed:
            command.status = CommandStatus.blocked
            command.error = decision.reason
            self.store.add_command(command)
            yield self._sse("command_blocked", {"command": command.model_dump(mode="json")})
            return

        if decision.requires_confirmation:
            command.status = CommandStatus.pending_confirm
            self.store.add_command(command)
            yield self._sse(
                "command_pending_confirmation",
                {
                    "command": command.model_dump(mode="json"),
                    "reason": decision.reason,
                },
            )
            return

        self.store.add_command(command)
        adapter = build_adapter(session, allow_simulation=self.allow_simulation)
        try:
            await adapter.connect()
            await self._execute_and_record(adapter, command)
        except Exception as exc:
            command.status = CommandStatus.failed
            command.error = str(exc)
            self.store.update_command(command)
        finally:
            try:
                await adapter.close()
            except Exception:
                pass
        yield self._sse("command_completed", {"command": command.model_dump(mode="json")})

    def _bootstrap_command_for_vendor(self, vendor: str) -> str:
        normalized = (vendor or "").strip().lower()
        if "huawei" in normalized:
            return "display version"
        return "show version"

    def _looks_duplicate_command(self, command: str, existing: list[CommandExecution]) -> bool:
        normalized = command.strip().lower()
        for item in existing[-3:]:
            if item.command.strip().lower() == normalized:
                return True
        return False

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
            return ConfirmCommandResponse(
                command_id=command.id,
                status=command.status,
                message="High-risk command was rejected.",
            )

        adapter = build_adapter(session, allow_simulation=self.allow_simulation)
        try:
            await adapter.connect()
            await self._execute_and_record(adapter, command)
        except Exception as exc:
            command.status = CommandStatus.failed
            command.error = str(exc)
            self.store.update_command(command)
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

        if command.status == CommandStatus.failed:
            self.store.set_summary(self._build_execution_failure_summary(session_id, command.error or "设备执行失败"))
        else:
            preferred_mode = self._preferred_mode_from_session(session.operation_mode)
            summary = await self._request_final_from_context(session_id, preferred_mode=preferred_mode)
            self.store.set_summary(summary or self._build_no_conclusion_summary(session_id))

        return ConfirmCommandResponse(
            command_id=command.id,
            status=command.status,
            message="Command approved and executed." if command.status == CommandStatus.succeeded else "Command execution failed.",
        )

    async def _execute_and_record(self, adapter, command: CommandExecution) -> None:
        command.status = CommandStatus.running
        self.store.update_command(command)

        try:
            output = await adapter.run_command(command.command)
            category, parsed_data, conclusion = parse_command_output(command.command, output)
            command.output = output
            # Keep transport/execution semantics simple: command is successful if SSH/API execution returned.
            # Whether CLI syntax is semantically correct is left for the LLM to judge from raw output.
            command.status = CommandStatus.succeeded
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
            self.store.update_command(command)

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

    def _sse(self, event: str, payload: dict) -> str:
        envelope = EventEnvelope(event=event, payload=payload)
        return f"event: {envelope.event}\ndata: {json.dumps(envelope.payload, ensure_ascii=False)}\n\n"
