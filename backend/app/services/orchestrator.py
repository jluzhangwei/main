from __future__ import annotations

import json
import os
from typing import AsyncIterator

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
        session = self.store.get_session(session_id)

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

        async for event in self._run_autonomous_loop(session_id, user_content):
            yield event

        summary = self.store.get_summary(session_id)
        if summary is None:
            summary = await self._build_summary(session_id)
            self.store.set_summary(summary)

        assistant_message = Message(
            session_id=session_id,
            role="assistant",
            content=(
                f"诊断完成。根因判断: {summary.root_cause}。"
                f"影响范围: {summary.impact_scope}。"
                f"建议: {summary.recommendation}"
            ),
        )
        self.store.add_message(assistant_message)

        yield self._sse("final_summary", {"message": assistant_message.model_dump(mode="json"), "summary": summary.model_dump(mode="json")})

    async def _run_autonomous_loop(self, session_id: str, user_content: str) -> AsyncIterator[str]:
        session = self.store.get_session(session_id)
        self._append_user_problem_to_ai_context(session_id, session.device.vendor, session.device.protocol.value, user_content)
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
                summary = self._summary_from_plan(session_id, plan)
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
        await adapter.connect()
        await self._execute_and_record(adapter, command)
        await adapter.close()
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

    def _summary_from_plan(self, session_id: str, plan: dict) -> IncidentSummary | None:
        root_cause = str(plan.get("root_cause", "")).strip()
        impact_scope = str(plan.get("impact_scope", "")).strip()
        recommendation = str(plan.get("recommendation", "")).strip()
        if not root_cause or not impact_scope or not recommendation:
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
            root_cause=root_cause,
            impact_scope=impact_scope,
            recommendation=recommendation,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )

    def _append_user_problem_to_ai_context(self, session_id: str, vendor: str, protocol: str, user_content: str) -> None:
        header = (
            f"当前设备厂商: {vendor}\n"
            f"接入协议: {protocol}\n"
            "请基于当前会话上下文继续诊断。\n"
            f"用户问题: {user_content}"
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
            text += f"- error: {command.error[:1200]}\n"
        if command.output:
            text += f"- output:\n{command.output[:2500]}"
        self.store.append_ai_context(session_id, "user", text)

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
        await adapter.connect()
        await self._execute_and_record(adapter, command)
        await adapter.close()
        self.store.set_summary(await self._build_summary(session_id))

        return ConfirmCommandResponse(
            command_id=command.id,
            status=command.status,
            message="Command approved and executed.",
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

    async def _build_summary(self, session_id: str) -> IncidentSummary:
        session = self.store.get_session(session_id)
        evidences = self.store.list_evidence(session_id)
        commands = self.store.list_commands(session_id)

        llm_summary = await self.deepseek_diagnoser.diagnose(
            session=session,
            commands=commands,
            evidences=evidences,
        )
        if llm_summary:
            return llm_summary

        return self._build_llm_unavailable_summary(session_id)

    def _build_llm_unavailable_summary(self, session_id: str) -> IncidentSummary:
        return IncidentSummary(
            session_id=session_id,
            root_cause="LLM 服务不可用，无法生成有效根因诊断。",
            impact_scope="无法评估。",
            recommendation="请检查模型 API 可用性与密钥配置后重试。",
        )

    def _sse(self, event: str, payload: dict) -> str:
        envelope = EventEnvelope(event=event, payload=payload)
        return f"event: {envelope.event}\ndata: {json.dumps(envelope.payload, ensure_ascii=False)}\n\n"
