from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

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
from app.services.adapters import build_adapter
from app.services.command_policy import CommandPolicyEngine
from app.services.command_group_runtime import execute_command_group
from app.services.command_runtime import (
    apply_adapter_command_meta,
    apply_device_profile_to_session_store,
    parse_command_runtime,
)
from app.services.compound_command_runtime import run_compound_command_batch
from app.services.adapter_runtime import close_connected_adapter, ensure_connected_adapter
from app.services.deepseek_diagnoser import DeepSeekDiagnoser
from app.services.llm_planner_bridge import LLMPlannerBridge
from app.services.risk_engine import RiskEngine
from app.services.single_command_runtime import execute_single_command
from app.services.sop_archive import SOPArchive
from app.services.store import InMemoryStore


class ConversationOrchestrator:
    def __init__(self, store: InMemoryStore, *, allow_simulation: bool = True):
        self.store = store
        self.risk_engine = RiskEngine()
        self.command_policy_engine = CommandPolicyEngine()
        self.deepseek_diagnoser = DeepSeekDiagnoser()
        self.llm_planner_bridge = LLMPlannerBridge()
        self.sop_archive = SOPArchive()
        self.allow_simulation = allow_simulation
        self.max_autonomous_steps = int(os.getenv("AUTONOMOUS_MAX_STEPS", "8"))
        self._session_adapters: dict[str, object] = {}
        self._trace_perf_started: dict[str, float] = {}
        self._stop_requested_sessions: set[str] = set()
        self._running_sessions: set[str] = set()

    def prompt_runtime_policy(self) -> dict[str, str]:
        return {
            "runtime_session_header_template": (
                "会话ID: {session_id}\\n"
                "当前设备厂商: {vendor}\\n"
                "接入协议: {protocol}\\n"
                "会话模式: {operation_mode}\\n"
                "会话目标: {session_goal}\\n"
                "本轮请求: {user_content}\\n"
                "请只基于当前会话上下文继续诊断，禁止引用或臆测其他会话。\\n"
                "优先围绕会话目标完成闭环，避免无证据的机会性扩展变更。"
            ),
            "runtime_command_result_template": (
                "执行结果\\n"
                "- step: {step_no}\\n"
                "- title: {title}\\n"
                "- original_command: {original_command}\\n"
                "- effective_command: {effective_command}\\n"
                "- command: {command}\\n"
                "- status: {status}\\n"
                "- capability_state: {capability_state(optional)}\\n"
                "- capability_reason: {capability_reason(optional)}\\n"
                "- permission_state: {permission_state(optional)}\\n"
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
            "runtime_permission_precheck_policy": (
                "运行期权限预检策略："
                "基线识别命令（show/display version）后优先探测当前权限/模式；"
                "执行可能受限命令前先探测当前会话权限/模式；"
                "若首次探测权限不足，优先最小提权并立即复核；"
                "若已在特权/配置模式，不重复提权命令。"
            ),
            "runtime_baseline_collection_policy": (
                "运行期基线采集策略："
                "会话首轮先执行版本探针（多厂商show/display version），再按识别厂商补采设备时钟与会话权限；"
                "将用户问题、控制器当前时间、设备基线画像汇总后再进入LLM规划；"
                "避免在证据未消费前重复同类基线探测。"
            ),
            "runtime_vendor_command_family_policy": (
                "运行期厂商命令家族策略："
                "当厂商/版本已识别后，Huawei优先display家族，Arista/Cisco-like优先show家族；"
                "禁止跨家族盲试与来回切换；如需切换必须在reason中说明兼容性依据。"
            ),
            "runtime_history_forensics_policy": (
                "运行期历史取证策略："
                "当用户请求“上次/历史/闪断/flap/间歇”类问题时，应优先采集日志/告警/协议邻接变化记录；"
                "不得仅依据当前瞬时状态直接下结论；"
                "若日志不可得，需显式返回“日志证据不足”并给出替代取证命令。"
            ),
            "runtime_mode_scope_policy": (
                "运行期模式范围策略："
                "query/diagnosis 模式仅允许采集与查询命令；"
                "若命中配置变更命令（如configure terminal/system-view/interface/shutdown/no shutdown/save/write memory），"
                "系统会在执行前硬拦截并回传 out-of-scope 原因；"
                "config 模式允许变更命令继续进入风险与策略判定。"
            ),
            "runtime_capability_precheck_policy": (
                "运行期命令能力预检策略："
                "系统会在执行前进行本地命令能力命中（rewrite/block）；"
                "命中rewrite时会直接改写执行并回传改写关系；"
                "命中block时会直接拦截并回传结构化原因；"
                "执行后会基于语法错误与成功候选进行自动学习更新。"
            ),
            "runtime_final_action_marker_policy": (
                "运行期结论动作词策略："
                "若仍需继续执行，结论中应包含“建议执行/修复/打开/变更”；"
                "若任务已闭环，结论中应包含“已完成/无需”。"
            ),
            "runtime_sop_archive_policy": self.sop_archive.prompt_policy(),
        }

    async def _propose_next_step_with_debug(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        conversation_history=None,
        planner_context: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        return await self.llm_planner_bridge.propose_next_step_with_debug(
            self.deepseek_diagnoser,
            session=session,
            user_problem=user_problem,
            commands=commands,
            evidences=evidences,
            iteration=iteration,
            max_iterations=max_iterations,
            conversation_history=conversation_history,
            planner_context=planner_context,
        )

    async def stop_session(self, session_id: str) -> dict[str, object]:
        self._stop_requested_sessions.add(session_id)
        running = session_id in self._running_sessions
        adapter_closed = session_id in self._session_adapters
        await self._drop_session_adapter(session_id)

        trace = self._trace_start(
            session_id=session_id,
            step_type="session_control",
            title="用户停止当前会话",
        )
        self._trace_finish(
            trace,
            status="stopped",
            detail="operator requested stop; adapter closed and autonomous loop cancellation requested",
        )

        latest_messages = self.store.list_messages(session_id)
        latest_text = latest_messages[-1].content.strip() if latest_messages else ""
        if "会话已手动停止" not in latest_text:
            self.store.add_message(
                Message(
                    session_id=session_id,
                    role="system",
                    content="会话已手动停止。可在历史会话点击“恢复”继续与 AI 对话。",
                )
            )

        return {
            "session_id": session_id,
            "stop_requested": True,
            "adapter_closed": adapter_closed,
            "running": running,
            "message": "Stop requested. Current session execution is being halted.",
        }

    async def stream_message(self, session_id: str, user_content: str) -> AsyncIterator[str]:
        self._stop_requested_sessions.discard(session_id)
        self._running_sessions.add(session_id)
        try:
            session = self.store.get_session(session_id)
            user_trace = self._trace_start(
                session_id=session_id,
                step_type="user_input",
                title="接收用户请求",
                detail=user_content[:220],
                detail_payload={
                    "user_input": self._clip_trace_text(self._sanitize_for_llm(user_content), 12000),
                    "session_mode": session.operation_mode.value,
                    "automation_level": session.automation_level.value,
                    "device": {
                        "host": session.device.host,
                        "name": session.device.name or "",
                        "protocol": session.device.protocol.value,
                    },
                },
            )
            preferred_mode = self._preferred_mode_from_session(session.operation_mode)
            self.store.clear_summary(session_id)
            if not self.store.list_commands(session_id):
                self.store.reset_ai_context(session_id)

            user_message = Message(session_id=session_id, role="user", content=user_content)
            self.store.add_message(user_message)
            self._trace_finish(user_trace, status="succeeded")
            yield self._sse("message_ack", {"message": user_message.model_dump(mode="json")})

            if self._is_stop_requested(session_id):
                yield self._sse("session_stopped", {"session_id": session_id, "reason": "operator_stop"})
                return

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

            if self._is_stop_requested(session_id):
                yield self._sse("session_stopped", {"session_id": session_id, "reason": "operator_stop"})
                return

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
                summary, final_debug = await self._request_final_from_context_with_debug(
                    session_id,
                    preferred_mode=preferred_mode,
                )
                final_llm_request = self._trace_start(
                    session_id=session_id,
                    step_type="llm_request",
                    title="提交给 AI（最终总结）",
                )
                self._trace_finish(
                    final_llm_request,
                    status="succeeded",
                    detail="request_submitted",
                    detail_payload=self._build_llm_request_payload(final_debug),
                )
                final_llm_response = self._trace_start(
                    session_id=session_id,
                    step_type="llm_response",
                    title="AI 原始回复（最终总结）",
                )
                if summary is None:
                    summary = self._build_no_conclusion_summary(session_id)
                    self._trace_finish(
                        final_llm_response,
                        status="failed",
                        detail="LLM未返回final",
                        detail_payload=self._build_llm_response_payload(final_debug.get("llm"), None),
                    )
                    self._trace_finish(
                        final_trace,
                        status="failed",
                        detail="LLM未返回final",
                        detail_payload=self._compact_trace_payload(final_debug),
                    )
                else:
                    self._trace_finish(
                        final_llm_response,
                        status="succeeded",
                        detail="decision=final",
                        detail_payload=self._build_llm_response_payload(final_debug.get("llm"), final_debug.get("plan")),
                    )
                    self._trace_finish(
                        final_trace,
                        status="succeeded",
                        detail_payload=self._compact_trace_payload(
                            {
                                **final_debug,
                                "final_summary": summary.model_dump(mode="json"),
                            }
                        ),
                    )
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
        finally:
            self._running_sessions.discard(session_id)
            self._stop_requested_sessions.discard(session_id)

    async def _run_autonomous_loop(
        self,
        session_id: str,
        user_content: str,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> AsyncIterator[str]:
        if self._is_stop_requested(session_id):
            return
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
            if self._is_stop_requested(session_id):
                return
            baseline_version_commands: list[CommandExecution] = []
            baseline_version_plan = self._baseline_version_probe_plan(session.device.vendor)
            if baseline_version_plan:
                for title, command_text in baseline_version_plan:
                    step_no += 1
                    risk_level = self.risk_engine.decide(command_text, session.automation_level).risk_level
                    command = CommandExecution(
                        session_id=session_id,
                        step_no=step_no,
                        title=title,
                        command=command_text,
                        adapter_type=session.device.protocol,
                        risk_level=risk_level,
                        requires_confirmation=False,
                        status=CommandStatus.queued,
                    )
                    baseline_version_commands.append(command)
                    async for event in self._execute_with_policy(session, command):
                        yield event
                    self._append_command_result_to_ai_context(session_id, command)

                if self._is_stop_requested(session_id):
                    return
                if self._has_pending_confirmation(session_id):
                    return
                if not any(command.status == CommandStatus.succeeded for command in baseline_version_commands):
                    error_text = (
                        next(
                            (item.error for item in baseline_version_commands if (item.error or "").strip()),
                            "",
                        )
                        or "基础信息采集失败（版本探针全部失败）"
                    )
                    self.store.set_summary(self._build_execution_failure_summary(session_id, error_text))
                    return

            # Refresh session profile after version probes (vendor/platform/version may have changed).
            session = self.store.get_session(session_id)
            baseline_profile_plan = self._baseline_profile_plan(session.device.vendor)
            baseline_profile_commands: list[CommandExecution] = []
            if baseline_profile_plan:
                for title, command_text in baseline_profile_plan:
                    step_no += 1
                    risk_level = self.risk_engine.decide(command_text, session.automation_level).risk_level
                    command = CommandExecution(
                        session_id=session_id,
                        step_no=step_no,
                        title=title,
                        command=command_text,
                        adapter_type=session.device.protocol,
                        risk_level=risk_level,
                        requires_confirmation=False,
                        status=CommandStatus.queued,
                    )
                    baseline_profile_commands.append(command)
                    async for event in self._execute_with_policy(session, command):
                        yield event
                    self._append_command_result_to_ai_context(session_id, command)

                if self._is_stop_requested(session_id):
                    return
                if self._has_pending_confirmation(session_id):
                    return

            self._append_baseline_snapshot_to_ai_context(
                session_id=session_id,
                user_content=user_content,
                version_commands=baseline_version_commands,
                profile_commands=baseline_profile_commands,
            )
            self._append_sop_context_to_ai_context(
                session_id=session_id,
                problem=user_content,
                vendor=session.device.vendor,
            )

        for iteration in range(1, self.max_autonomous_steps + 1):
            if self._is_stop_requested(session_id):
                break
            commands = self.store.list_commands(session_id)
            evidences = self.store.list_evidence(session_id)
            ai_context = self.store.list_ai_context(session_id)
            self._trace_decision(
                session_id=session_id,
                step_type="context_snapshot",
                title=f"会话上下文快照（第 {iteration} 轮）",
                detail=(
                    f"iteration={iteration}; "
                    f"commands={len(commands)}; "
                    f"evidences={len(evidences)}; "
                    f"ai_context={len(ai_context)}"
                ),
                detail_payload=self._compact_trace_payload(
                    {
                        "iteration": iteration,
                        "counts": {
                            "commands": len(commands),
                            "evidences": len(evidences),
                            "ai_context": len(ai_context),
                        },
                        "latest_command": self._command_trace_record(commands[-1], include_output=True) if commands else None,
                        "latest_evidence": evidences[-1].model_dump(mode="json") if evidences else None,
                    }
                ),
            )
            llm_trace = self._trace_start(
                session_id=session_id,
                step_type="llm_plan",
                title=f"LLM 规划第 {iteration} 轮",
                detail_payload={
                    "iteration": iteration,
                    "max_iterations": self.max_autonomous_steps,
                    "status": "requesting",
                },
            )
            plan, plan_debug = await self._propose_next_step_with_debug(
                session=session,
                user_problem=user_content,
                commands=commands,
                evidences=evidences,
                iteration=iteration,
                max_iterations=self.max_autonomous_steps,
                conversation_history=ai_context,
            )
            llm_request_trace = self._trace_start(
                session_id=session_id,
                step_type="llm_request",
                title=f"提交给 AI（第 {iteration} 轮）",
            )
            self._trace_finish(
                llm_request_trace,
                status="succeeded",
                detail="request_submitted",
                detail_payload=self._build_llm_request_payload(plan_debug),
            )
            llm_response_trace = self._trace_start(
                session_id=session_id,
                step_type="llm_response",
                title=f"AI 原始回复（第 {iteration} 轮）",
            )

            if not plan:
                self._trace_finish(
                    llm_response_trace,
                    status="failed",
                    detail="LLM未返回可解析计划",
                    detail_payload=self._build_llm_response_payload(plan_debug, None),
                )
                self._trace_finish(
                    llm_trace,
                    status="failed",
                    detail="LLM未返回可解析计划",
                    detail_payload=self._build_llm_trace_payload(
                        session=session,
                        user_problem=user_content,
                        plan_debug=plan_debug,
                        parsed_plan=None,
                    ),
                )
                break
            self._trace_finish(
                llm_response_trace,
                status="succeeded",
                detail=f"decision={str(plan.get('decision', ''))}",
                detail_payload=self._build_llm_response_payload(plan_debug, plan),
            )
            referenced_sops = self._referenced_sops_from_plan(plan)
            self._trace_finish(
                llm_trace,
                status="succeeded",
                detail=f"decision={str(plan.get('decision', ''))}",
                detail_payload=self._build_llm_trace_payload(
                    session=session,
                    user_problem=user_content,
                    plan_debug=plan_debug,
                    parsed_plan=plan,
                    referenced_sops=referenced_sops,
                ),
            )
            self._trace_sop_reference(
                session_id=session_id,
                iteration=iteration,
                referenced_sops=referenced_sops,
                plan=plan,
            )
            plan_text = json.dumps(plan, ensure_ascii=False)
            self.store.append_ai_context(session_id, "assistant", plan_text)
            self._trace_ai_context_submission(
                session_id=session_id,
                title=f"系统提交上下文（AI计划第 {iteration} 轮）",
                role="assistant",
                source="llm_plan",
                content=plan_text,
                extra_payload={
                    "plan": plan,
                    "iteration": iteration,
                },
            )

            if self._is_stop_requested(session_id):
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="operator_stop",
                    status="stopped",
                )
                break

            if plan.get("decision") == "final":
                summary = self._summary_from_plan(session_id, plan, preferred_mode=preferred_mode)
                if summary:
                    self.store.set_summary(summary)
                    self._trace_decision(
                        session_id=session_id,
                        step_type="llm_final",
                        title="LLM 输出最终总结",
                        detail=f"mode={summary.mode}; confidence={summary.confidence}",
                        status="succeeded",
                    )
                else:
                    self._trace_decision(
                        session_id=session_id,
                        step_type="llm_final",
                        title="LLM 输出最终总结",
                        detail="LLM返回decision=final，但内容不完整，未生成有效总结。",
                        status="failed",
                    )
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="decision=final",
                )
                break

            plan_commands = self._extract_plan_commands(plan, next_step_no=step_no + 1)
            if not plan_commands:
                self._trace_decision(
                    session_id=session_id,
                    step_type="plan_decision",
                    title="计划命令解析",
                    detail="LLM plan未提取到可执行命令，结束本轮。",
                    status="failed",
                )
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="no_executable_commands",
                    status="failed",
                )
                break
            plan_message = Message(
                session_id=session_id,
                role="assistant",
                content=self._render_plan_message(iteration=iteration, plan=plan, plan_commands=plan_commands),
            )
            self.store.add_message(plan_message)
            yield self._sse("message_ack", {"message": plan_message.model_dump(mode="json")})
            self._trace_decision(
                session_id=session_id,
                step_type="plan_parse",
                title="计划命令解析结果",
                detail=f"commands={len(plan_commands)}",
                detail_payload=self._compact_trace_payload(
                    {
                        "iteration": iteration,
                        "commands": [{"title": title, "command": cmd} for title, cmd in plan_commands],
                    }
                ),
            )

            batch_group_enabled = self._batch_execution_enabled() and len(plan_commands) > 1
            self._trace_decision(
                session_id=session_id,
                step_type="plan_decision",
                title="批量分组判定",
                detail=(
                    f"commands={len(plan_commands)}; "
                    f"batch_execution_enabled={self._batch_execution_enabled()}; "
                    f"batch_group_enabled={batch_group_enabled}"
                ),
            )

            if batch_group_enabled:
                plan_batch_id = make_id()
                plan_batch_total = len(plan_commands)
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
                            batch_index=index,
                            batch_total=plan_batch_total,
                        )
                    )

                blocked_hit = False
                pending_hit = False
                async for event in self._execute_batch_with_policy(session, batch_commands):
                    if "command_blocked" in event:
                        blocked_hit = True
                    if "command_pending_confirmation" in event:
                        pending_hit = True
                    yield event

                if self._is_stop_requested(session_id):
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="operator_stop",
                        status="stopped",
                    )
                    break

                for command in batch_commands:
                    self._append_command_result_to_ai_context(session_id, command)

                if pending_hit:
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="pending_confirmation",
                        status="pending_confirm",
                    )
                    break
                if blocked_hit:
                    # Let AI continue in next round with structured block reasons.
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="blocked_continue_next_round",
                    )
                    continue
                if any(command.status == CommandStatus.failed for command in batch_commands):
                    # Keep autonomous loop alive: feed execution failure back to AI so it can self-correct.
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="command_failed_continue_next_round",
                        status="failed",
                    )
                    continue
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="batch_succeeded_continue_next_round",
                )
                continue

            executed_commands: list[CommandExecution] = []
            blocked_hit = False
            pending_hit = False
            for title, next_command in plan_commands:
                step_no += 1
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
                async for event in self._execute_with_policy(session, command):
                    if "command_blocked" in event:
                        blocked_hit = True
                    if "command_pending_confirmation" in event:
                        pending_hit = True
                    yield event
                executed_commands.append(command)
                self._append_command_result_to_ai_context(session_id, command)

                if self._is_stop_requested(session_id):
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="operator_stop",
                        status="stopped",
                    )
                    break
                if pending_hit:
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="pending_confirmation",
                        status="pending_confirm",
                    )
                    break
                if blocked_hit:
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail="blocked_continue_next_round",
                    )
                    break
                if command.status == CommandStatus.failed:
                    # Let AI self-correct from the failure result before continuing.
                    self._trace_decision(
                        session_id=session_id,
                        step_type="loop_control",
                        title="循环控制判定",
                        detail=f"command_failed_at_step={command.step_no}",
                        status="failed",
                    )
                    break

            if self._is_stop_requested(session_id):
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="operator_stop",
                    status="stopped",
                )
                break
            if pending_hit:
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="pending_confirmation",
                    status="pending_confirm",
                )
                break
            if blocked_hit:
                # Let AI self-correct with blocked reason in next round.
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="blocked_continue_next_round",
                )
                continue
            if any(command.status == CommandStatus.failed for command in executed_commands):
                self._trace_decision(
                    session_id=session_id,
                    step_type="loop_control",
                    title="循环控制判定",
                    detail="command_failed_continue_next_round",
                    status="failed",
                )
                continue

    async def _execute_with_policy(
        self,
        session,
        command: CommandExecution,
    ) -> AsyncIterator[str]:
        if self._is_stop_requested(command.session_id):
            command.status = CommandStatus.rejected
            command.error = "Stopped by operator"
            command.completed_at = now_utc()
            command.duration_ms = 0
            self.store.add_command(command)
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：执行前终止",
                status="stopped",
                detail=f"operator_stop_before_execution; command={command.command[:140]}",
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "stopped_before_execution",
                        "reason": "Stopped by operator",
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
            return

        capability = self._apply_capability_precheck(session, command)
        if capability["action"] == "rewrite":
            command.constraint_source = "capability_rewrite"
            command.constraint_reason = str(capability["reason"])
        if capability["action"] == "block":
            command.constraint_source = "capability_block"
            command.constraint_reason = str(capability["reason"])
            command.status = CommandStatus.blocked
            command.error = str(capability["reason"])
            command.completed_at = now_utc()
            command.duration_ms = 0
            self.store.add_command(command)
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：命令被能力规则阻断",
                status="blocked",
                detail=str(capability["reason"])[:280],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "blocked_before_execution",
                        "reason": str(capability["reason"]),
                        "constraint_source": "capability_block",
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            yield self._sse(
                "command_blocked",
                {
                    "command": command.model_dump(mode="json"),
                    "reason": capability["reason"],
                },
            )
            return

        scope = self._apply_operation_scope_precheck(session, command.command, session_id=command.session_id)
        if scope["action"] == "block":
            command.constraint_source = "mode_scope_block"
            command.constraint_reason = str(scope["reason"])
            command.status = CommandStatus.blocked
            command.error = str(scope["reason"])
            command.completed_at = now_utc()
            command.duration_ms = 0
            self.store.add_command(command)
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：命令超出模式范围",
                status="blocked",
                detail=str(scope["reason"])[:280],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "blocked_before_execution",
                        "reason": str(scope["reason"]),
                        "constraint_source": "mode_scope_block",
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            yield self._sse(
                "command_blocked",
                {
                    "command": command.model_dump(mode="json"),
                    "reason": scope["reason"],
                },
            )
            return

        policy = self.store.get_command_policy()
        decision = self._decide_execution_action(session, command.command, policy, session_id=command.session_id)
        command.constraint_source = str(decision.get("reason_source", "") or "") or None
        command.constraint_reason = str(decision.get("reason", "") or "") or None
        command.risk_level = decision["risk_level"]
        command.requires_confirmation = decision["action"] == "confirm"

        if decision["action"] == "blocked":
            command.status = CommandStatus.blocked
            command.error = decision["reason"]
            command.completed_at = now_utc()
            command.duration_ms = 0
            self.store.add_command(command)
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：命令被策略阻断",
                status="blocked",
                detail=decision["reason"][:280],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "blocked_before_execution",
                        "reason": decision["reason"],
                        "constraint_source": str(decision.get("reason_source", "") or ""),
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            yield self._sse(
                "command_blocked",
                {
                    "command": command.model_dump(mode="json"),
                    "reason": decision["reason"],
                },
            )
            return

        if decision["action"] == "confirm":
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：命令等待确认",
                status="pending_confirm",
                detail=f"command={command.command[:140]}; reason={decision['reason'][:180]}",
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "pending_confirm",
                        "reason": decision["reason"],
                        "constraint_source": str(decision.get("reason_source", "") or ""),
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            yield self._mark_pending_confirmation(command, decision["reason"])
            return

        self.store.add_command(command)
        try:
            adapter = await self._get_session_adapter(session)
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
            self._trace_decision(
                session_id=command.session_id,
                step_type="session_control",
                title="会话控制：设备连接失败",
                status="failed",
                detail=str(exc)[:280],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "adapter_connect_failed",
                        "error": str(exc),
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            await self._drop_session_adapter(session.id)
            yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
            return

        execution_trace = self._trace_start(
            session_id=command.session_id,
            step_type="command_execution",
            title=f"设备执行命令 #{command.step_no}: {command.title}",
            command_id=command.id,
            detail=command.command[:260],
            detail_payload=self._compact_trace_payload(
                {
                    "phase": "start",
                    "command": self._command_trace_record(command, include_output=False),
                }
            ),
        )
        try:
            await self._execute_and_record(adapter, command)
            self._trace_finish(
                execution_trace,
                status=command.status.value,
                detail=(command.error or command.output or "")[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "completed",
                        "command": self._command_trace_record(command, include_output=True),
                    }
                ),
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
            self._trace_finish(
                execution_trace,
                status="failed",
                detail=str(exc)[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "failed",
                        "error": str(exc),
                        "command": self._command_trace_record(command, include_output=True),
                    }
                ),
            )
            await self._drop_session_adapter(session.id)
        yield self._sse("command_completed", {"command": command.model_dump(mode="json")})

    async def _execute_batch_with_policy(
        self,
        session,
        commands: list[CommandExecution],
    ) -> AsyncIterator[str]:
        if not commands:
            return

        if self._is_stop_requested(commands[0].session_id):
            stopped_at = now_utc()
            for command in commands:
                command.status = CommandStatus.rejected
                command.error = "Stopped by operator"
                command.completed_at = stopped_at
                command.duration_ms = 0
                self.store.add_command(command)
                yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
            self._trace_decision(
                session_id=commands[0].session_id,
                step_type="session_control",
                title="会话控制：批量执行前终止",
                status="stopped",
                detail="operator_stop_before_batch_execution",
            )
            return

        policy = self.store.get_command_policy()
        blocked_reasons: dict[str, str] = {}
        blocked_sources: dict[str, str] = {}
        decisions: list[dict[str, object]] = []
        first_confirm_idx: int | None = None
        confirm_reason = "批量命令包含待确认项，请确认后继续执行后续命令。"

        for idx, command in enumerate(commands):
            capability = self._apply_capability_precheck(session, command)
            if capability["action"] == "rewrite":
                command.constraint_source = "capability_rewrite"
                command.constraint_reason = str(capability["reason"])
            if capability["action"] == "block":
                blocked_reasons[command.id] = str(capability["reason"])
                blocked_sources[command.id] = "capability_block"
                continue
            scope = self._apply_operation_scope_precheck(session, command.command, session_id=command.session_id)
            if scope["action"] == "block":
                blocked_reasons[command.id] = str(scope["reason"])
                blocked_sources[command.id] = "mode_scope_block"
                continue
            decision = self._decide_execution_action(session, command.command, policy, session_id=command.session_id)
            decisions.append(decision)
            command.constraint_source = str(decision.get("reason_source", "") or "") or None
            command.constraint_reason = str(decision.get("reason", "") or "") or None
            command.risk_level = decision["risk_level"]
            command.requires_confirmation = decision["action"] == "confirm"

            if decision["action"] == "blocked":
                blocked_reasons[command.id] = decision["reason"]
                blocked_sources[command.id] = str(decision.get("reason_source", "") or "policy_block")
                continue
            if decision["action"] == "confirm":
                if first_confirm_idx is None:
                    first_confirm_idx = idx
                    confirm_reason = str(decision["reason"] or confirm_reason)

        blocked_commands = [command for command in commands if command.id in blocked_reasons]
        executable_commands = [command for command in commands if command.id not in blocked_reasons]

        if blocked_reasons:
            for command in blocked_commands:
                command.constraint_source = blocked_sources.get(command.id) or command.constraint_source
                command.constraint_reason = blocked_reasons.get(command.id) or command.constraint_reason
                command.status = CommandStatus.blocked
                command.error = blocked_reasons.get(command.id, "Command blocked by policy.")
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
            if not executable_commands:
                self._trace_decision(
                    session_id=commands[0].session_id,
                    step_type="session_control",
                    title="会话控制：命令组全部被拦截",
                    status="failed",
                    detail="All commands in batch were blocked.",
                )
                return

        if first_confirm_idx is not None:
            precheck_commands = [
                command
                for idx, command in enumerate(commands)
                if idx < first_confirm_idx and command.id not in blocked_reasons
            ]
            pending_commands = [
                command
                for idx, command in enumerate(commands)
                if idx >= first_confirm_idx and command.id not in blocked_reasons
            ]
            adapter = None
            precheck_trace = None

            if precheck_commands:
                for command in precheck_commands:
                    self.store.add_command(command)

                try:
                    adapter = await self._get_session_adapter(session)
                    precheck_trace = self._trace_start(
                        session_id=commands[0].session_id,
                        step_type="command_execution",
                        title=f"设备执行命令组预检查 ({len(precheck_commands)} 条)",
                        detail=" ; ".join(command.command for command in precheck_commands)[:260],
                        detail_payload=self._compact_trace_payload(
                            {
                                "phase": "start_precheck",
                                "batch_size": len(precheck_commands),
                                "commands": self._command_trace_records(precheck_commands, include_output=False),
                            }
                        ),
                    )
                    precheck_events: list[str] = []

                    async def _execute_precheck(command: CommandExecution, _idx: int):
                        await self._execute_and_record(adapter, command)
                        precheck_events.append(self._sse("command_completed", {"command": command.model_dump(mode="json")}))
                        return command

                    async def _mark_stopped_remaining(remaining: list[CommandExecution], _idx: int):
                        stopped_at = now_utc()
                        for queued in remaining:
                            if queued.status in {CommandStatus.succeeded, CommandStatus.failed, CommandStatus.rejected}:
                                continue
                            queued.status = CommandStatus.rejected
                            queued.error = "Stopped by operator"
                            queued.completed_at = stopped_at
                            queued.duration_ms = 0
                            self.store.update_command(queued)
                            precheck_events.append(self._sse("command_completed", {"command": queued.model_dump(mode="json")}))

                    async def _mark_failed_remaining(remaining: list[CommandExecution], failed_command: CommandExecution, _idx: int):
                        skipped_at = now_utc()
                        base_error = (failed_command.error or "Command execution failed").strip()
                        for queued in remaining:
                            if queued.status in {CommandStatus.succeeded, CommandStatus.failed, CommandStatus.rejected}:
                                continue
                            queued.status = CommandStatus.failed
                            queued.error = f"{base_error} (skipped subsequent command in same batch)"
                            queued.completed_at = skipped_at
                            queued.duration_ms = 0
                            self.store.update_command(queued)
                            precheck_events.append(self._sse("command_completed", {"command": queued.model_dump(mode="json")}))

                    group_result = await execute_command_group(
                        precheck_commands,
                        execute_item=_execute_precheck,
                        is_failure=lambda result: result.status == CommandStatus.failed,
                        should_stop=lambda: self._is_stop_requested(commands[0].session_id),
                        on_stopped_remaining=_mark_stopped_remaining,
                        on_failed_remaining=_mark_failed_remaining,
                    )
                    for event in precheck_events:
                        yield event
                    if group_result.stopped or any(command.status == CommandStatus.rejected for command in precheck_commands):
                        if precheck_trace is not None:
                            self._trace_finish(
                                precheck_trace,
                                status="stopped",
                                detail="Stopped by operator",
                                detail_payload=self._compact_trace_payload(
                                    {
                                        "phase": "stopped",
                                        "batch_size": len(precheck_commands),
                                        "commands": self._command_trace_records(precheck_commands, include_output=True),
                                    }
                                ),
                            )
                        return
                    first_failed_command = next((item for item in precheck_commands if item.status == CommandStatus.failed), None)
                    if first_failed_command is not None:
                        if precheck_trace is not None:
                            self._trace_finish(
                                precheck_trace,
                                status="failed",
                                detail=(first_failed_command.error or "")[:300],
                                detail_payload=self._compact_trace_payload(
                                    {
                                        "phase": "failed_precheck",
                                        "error": first_failed_command.error or "",
                                        "commands": self._command_trace_records(precheck_commands, include_output=True),
                                    }
                                ),
                            )
                        return
                    if precheck_trace is not None:
                        self._trace_finish(
                            precheck_trace,
                            status="succeeded",
                            detail="precheck commands completed",
                            detail_payload=self._compact_trace_payload(
                                {
                                    "phase": "succeeded_precheck",
                                    "commands": self._command_trace_records(precheck_commands, include_output=True),
                                }
                            ),
                        )
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
                    if precheck_trace is not None:
                        self._trace_finish(
                            precheck_trace,
                            status="failed",
                            detail=str(exc)[:300],
                            detail_payload=self._compact_trace_payload(
                                {
                                    "phase": "failed_precheck",
                                    "error": str(exc),
                                    "commands": self._command_trace_records(precheck_commands, include_output=True),
                                }
                            ),
                        )
                    self._trace_decision(
                        session_id=commands[0].session_id,
                        step_type="session_control",
                        title="会话控制：命令组预检查失败",
                        status="failed",
                        detail=str(exc)[:280],
                    )
                    await self._drop_session_adapter(session.id)
                    return

            reason = confirm_reason or "批量命令包含未命中可执行规则的命令，请确认后整批执行。"
            self._trace_decision(
                session_id=commands[0].session_id,
                step_type="session_control",
                title="会话控制：命令组等待确认",
                status="pending_confirm",
                detail=reason[:280],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "pending_confirm_batch",
                        "reason": reason,
                        "precheck_commands": self._command_trace_records(precheck_commands, include_output=True),
                        "pending_commands": self._command_trace_records(pending_commands, include_output=False),
                    }
                ),
            )
            for idx, command in enumerate(pending_commands):
                pending_reason = reason if idx == 0 else "批量命令等待统一确认"
                yield self._mark_pending_confirmation(command, pending_reason)
            return

        for command in executable_commands:
            self.store.add_command(command)

        try:
            adapter = await self._get_session_adapter(session)
        except Exception as exc:
            failed_at = now_utc()
            for command in executable_commands:
                command.status = CommandStatus.failed
                command.error = str(exc)
                command.completed_at = failed_at
                if command.started_at:
                    command.duration_ms = max(0, int((failed_at - command.started_at).total_seconds() * 1000))
                self.store.update_command(command)
                yield self._sse("command_completed", {"command": command.model_dump(mode="json")})
            self._trace_decision(
                session_id=commands[0].session_id,
                step_type="session_control",
                title="会话控制：命令组设备连接失败",
                status="failed",
                detail=str(exc)[:280],
            )
            await self._drop_session_adapter(session.id)
            return

        execution_trace = self._trace_start(
            session_id=commands[0].session_id,
            step_type="command_execution",
            title=f"设备执行命令组 ({len(executable_commands)} 条)",
            detail=" ; ".join(command.command for command in executable_commands)[:260],
            detail_payload=self._compact_trace_payload(
                {
                    "phase": "start_batch",
                    "batch_size": len(executable_commands),
                    "commands": self._command_trace_records(executable_commands, include_output=False),
                }
            ),
        )

        try:
            await self._execute_batch_and_record(adapter, executable_commands)
        except Exception as exc:
            failed_at = now_utc()
            for command in executable_commands:
                command.status = CommandStatus.failed
                command.error = str(exc)
                command.completed_at = failed_at
                if command.started_at:
                    command.duration_ms = max(0, int((failed_at - command.started_at).total_seconds() * 1000))
                self.store.update_command(command)
            self._trace_finish(
                execution_trace,
                status="failed",
                detail=str(exc)[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "failed_batch",
                        "error": str(exc),
                        "commands": self._command_trace_records(executable_commands, include_output=True),
                    }
                ),
            )
            await self._drop_session_adapter(session.id)
        else:
            first_error = next((command.error for command in executable_commands if command.status == CommandStatus.failed), "")
            if first_error:
                self._trace_finish(
                    execution_trace,
                    status="failed",
                    detail=first_error[:300],
                    detail_payload=self._compact_trace_payload(
                        {
                            "phase": "failed_batch",
                            "error": first_error,
                            "commands": self._command_trace_records(executable_commands, include_output=True),
                        }
                    ),
                )
            else:
                output_brief = next((command.output for command in executable_commands if command.output), "")
                if blocked_commands:
                    self._trace_finish(
                        execution_trace,
                        status="succeeded",
                        detail=(
                            f"partial execution: blocked={len(blocked_commands)}, "
                            f"executed={len(executable_commands)}; {(output_brief or '')[:200]}"
                        ),
                        detail_payload=self._compact_trace_payload(
                            {
                                "phase": "succeeded_partial",
                                "blocked_count": len(blocked_commands),
                                "executed_count": len(executable_commands),
                                "commands": self._command_trace_records(executable_commands, include_output=True),
                            }
                        ),
                    )
                else:
                    self._trace_finish(
                        execution_trace,
                        status="succeeded",
                        detail=(output_brief or "")[:300],
                        detail_payload=self._compact_trace_payload(
                            {
                                "phase": "succeeded_batch",
                                "commands": self._command_trace_records(executable_commands, include_output=True),
                            }
                        ),
                    )

        for command in executable_commands:
            yield self._sse("command_completed", {"command": command.model_dump(mode="json")})

    def _decide_execution_action(
        self,
        session,
        command_text: str,
        policy: CommandPolicy,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._sync_risk_policy()
        policy_decision = self.command_policy_engine.evaluate(command_text, policy)
        risk_decision = self.risk_engine.decide(command_text, session.automation_level)
        risk_level = risk_decision.risk_level
        action: str
        reason: str
        reason_source: str

        # 1) Explicit block-rules always win.
        if policy_decision.result == "blocked":
            action = "blocked"
            reason = policy_decision.reason
            reason_source = "policy_block"
        # 2) Mode/risk baseline gate (includes hard-block and read-only baseline).
        elif not risk_decision.allowed:
            action = "blocked"
            reason = risk_decision.reason
            reason_source = "risk_baseline_block"
        # 3) Full-auto executes all non-hard-block commands.
        elif session.automation_level == AutomationLevel.full_auto:
            action = "allow"
            reason = "Full-auto executes non-hard-block commands."
            reason_source = "full_auto_allow"
        # 4) Explicit executable rules can bypass assisted-mode high-risk confirmation.
        elif policy_decision.result == "allowed":
            action = "allow"
            reason = policy_decision.reason
            reason_source = "policy_allow"
        # 5) In assisted mode, non-whitelisted high-risk commands need confirmation.
        elif session.automation_level == AutomationLevel.assisted and risk_level == RiskLevel.high:
            action = "confirm"
            reason = "中风险可执行模式下高风险命令需要人工确认。"
            reason_source = "risk_confirm"
        # 6) Any unmatched command in non-full-auto mode requires operator confirmation.
        elif policy_decision.result == "needs_confirmation":
            action = "confirm"
            reason = policy_decision.reason
            reason_source = "policy_confirm"
        else:
            action = "allow"
            reason = "Command allowed."
            reason_source = "default_allow"

        if session_id:
            self._trace_decision(
                session_id=session_id,
                step_type="policy_decision",
                title="执行前策略判定",
                detail=(
                    f"command={command_text[:140]}; "
                    f"policy_result={policy_decision.result}; "
                    f"risk={risk_level.value}; "
                    f"automation={session.automation_level.value}; "
                    f"action={action}; "
                    f"source={reason_source}; "
                    f"reason={reason[:180]}"
                ),
                status="succeeded" if action in {"allow", "confirm"} else "failed",
            )

        return {"action": action, "reason": reason, "risk_level": risk_level, "reason_source": reason_source}

    def _sync_risk_policy(self) -> None:
        policy = self.store.get_risk_policy()
        self.risk_engine.update_policy(
            high_risk_patterns=policy.high_risk_patterns,
            medium_risk_patterns=policy.medium_risk_patterns,
        )

    def _batch_execution_enabled(self) -> bool:
        return bool(getattr(self.deepseek_diagnoser, "batch_execution_enabled", True))

    def _apply_capability_precheck(self, session, command: CommandExecution) -> dict[str, str]:
        original_command = (command.command or "").strip()
        command.original_command = command.original_command or original_command
        command.effective_command = original_command
        if not original_command:
            command.capability_state = "invalid"
            command.capability_reason = "empty command"
            return {"action": "block", "reason": "Command is empty."}

        matched = self.store.resolve_command_capability(
            host=session.device.host,
            protocol=session.device.protocol,
            device_type=session.device.device_type,
            vendor=session.device.vendor,
            version_signature=session.device.version_signature,
            command_text=original_command,
        )
        if not matched:
            return {"action": "none", "reason": "no capability match"}

        rule = matched.rule
        if rule.action == "rewrite" and rule.rewrite_to:
            rewritten = rule.rewrite_to.strip()
            if rewritten:
                self.store.register_command_capability_hit(rule.id)
                command.command = rewritten
                command.effective_command = rewritten
                command.capability_state = "rewrite_hit"
                command.capability_rule_id = rule.id
                command.capability_reason = rule.reason_text or f"rewrite by capability rule {rule.id}"
                self._trace_decision(
                    session_id=command.session_id,
                    step_type="capability_decision",
                    title="执行前命令能力判定（改写）",
                    detail=(
                        f"original={original_command[:120]}; "
                        f"rewrite_to={rewritten[:120]}; "
                        f"rule_id={rule.id}"
                    ),
                )
                return {"action": "rewrite", "reason": command.capability_reason or "rewrite hit"}

        if rule.action == "block":
            if str(getattr(rule, "source", "")).strip().lower() == "learned" and self._is_permission_or_mode_error(
                str(rule.reason_text or "")
            ):
                self.store.set_command_capability_rule_enabled(rule.id, False)
                command.capability_state = "block_skip_permission"
                command.capability_rule_id = rule.id
                command.capability_reason = "permission/mode-sensitive learned block ignored and auto-disabled"
                self._trace_decision(
                    session_id=command.session_id,
                    step_type="capability_decision",
                    title="执行前命令能力判定（跳过权限阻断）",
                    detail=(
                        f"command={original_command[:120]}; "
                        f"rule_id={rule.id}; "
                        f"reason={str(rule.reason_text or '')[:120]}"
                    ),
                    status="skipped",
                )
                return {"action": "none", "reason": "permission/mode-sensitive learned block ignored"}
            if str(getattr(rule, "source", "")).strip().lower() == "learned" and self._is_mode_sensitive_command(
                original_command
            ):
                self.store.set_command_capability_rule_enabled(rule.id, False)
                command.capability_state = "block_skip_mode_sensitive"
                command.capability_rule_id = rule.id
                command.capability_reason = "context/mode-sensitive learned block ignored and auto-disabled"
                self._trace_decision(
                    session_id=command.session_id,
                    step_type="capability_decision",
                    title="执行前命令能力判定（跳过上下文阻断）",
                    detail=(
                        f"command={original_command[:120]}; "
                        f"rule_id={rule.id}; "
                        f"reason={str(rule.reason_text or '')[:120]}"
                    ),
                    status="skipped",
                )
                return {"action": "none", "reason": "context/mode-sensitive learned block ignored"}

            self.store.register_command_capability_hit(rule.id)
            command.capability_state = "block_hit"
            command.capability_rule_id = rule.id
            command.capability_reason = rule.reason_text or f"blocked by capability rule {rule.id}"
            self._trace_decision(
                session_id=command.session_id,
                step_type="capability_decision",
                title="执行前命令能力判定（阻断）",
                detail=(
                    f"command={original_command[:120]}; "
                    f"rule_id={rule.id}; "
                    f"reason={str(command.capability_reason)[:120]}"
                ),
                status="failed",
            )
            return {"action": "block", "reason": command.capability_reason or "blocked by capability"}

        return {"action": "none", "reason": "capability rule ignored"}

    def _apply_operation_scope_precheck(
        self,
        session,
        command_text: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, str]:
        mode = self._preferred_mode_from_session(session.operation_mode)
        normalized = " ".join(str(command_text or "").strip().lower().split())
        if mode == "config" or not normalized:
            return {"action": "none", "reason": "mode scope pass"}

        if self._is_mutating_command(normalized):
            reason = (
                f"Out-of-scope for mode={mode}: blocked mutating command `{normalized}`. "
                "请切换到配置模式后再执行变更。"
            )
            if session_id:
                self._trace_decision(
                    session_id=session_id,
                    step_type="scope_decision",
                    title="执行前会话范围判定",
                    detail=f"mode={mode}; command={normalized[:140]}; action=blocked; reason={reason[:180]}",
                    status="failed",
                )
            return {"action": "block", "reason": reason}

        if session_id:
            self._trace_decision(
                session_id=session_id,
                step_type="scope_decision",
                title="执行前会话范围判定",
                detail=f"mode={mode}; command={normalized[:140]}; action=allow",
            )
        return {"action": "none", "reason": "mode scope pass"}

    def _is_mutating_command(self, normalized_command: str) -> bool:
        normalized = " ".join(str(normalized_command or "").strip().lower().split())
        if not normalized:
            return False
        if self._is_read_only_or_session_probe_command(normalized):
            return False
        return any(
            normalized.startswith(prefix)
            for prefix in (
                "configure terminal",
                "conf t",
                "system-view",
                "interface ",
                "subinterface ",
                "router ",
                "vlan ",
                "line ",
                "ip address ",
                "ip route ",
                "ipv6 route ",
                "shutdown",
                "no shutdown",
                "undo ",
                "ospf ",
                "bgp ",
                "isis ",
                "rip ",
                "mpls ",
                "port ",
                "switchport ",
                "description ",
                "duplex ",
                "speed ",
                "mtu ",
                "stp ",
                "spanning-tree ",
                "commit",
                "save",
                "write memory",
                "copy running-config startup-config",
                "copy run start",
                "erase ",
                "delete ",
                "clear ",
                "reload",
                "reboot",
                "reset ",
            )
        )

    def _is_read_only_or_session_probe_command(self, normalized_command: str) -> bool:
        normalized = " ".join(str(normalized_command or "").strip().lower().split())
        if not normalized:
            return False
        if normalized in {"?", "??"}:
            return True
        if normalized in {"show", "display", "ping", "traceroute", "tracert"}:
            return True
        if normalized.startswith(("show ", "display ", "ping ", "traceroute ", "tracert ")):
            return True
        return normalized in {
            "enable",
            "disable",
            "super",
            "exit",
            "quit",
            "return",
            "terminal length 0",
            "terminal length 512",
            "screen-length 0 temporary",
            "screen-length disable",
        } or normalized.startswith(("terminal length ", "screen-length "))

    async def _execute_batch_and_record(self, adapter, commands: list[CommandExecution]) -> None:
        if len(commands) > 1:
            await self._execute_batch_with_single_call(adapter, commands)
            return

        for idx, command in enumerate(commands):
            if self._is_stop_requested(command.session_id):
                stopped_at = now_utc()
                for queued in commands[idx:]:
                    queued.status = CommandStatus.rejected
                    queued.error = "Stopped by operator"
                    queued.completed_at = stopped_at
                    queued.duration_ms = 0
                    self.store.update_command(queued)
                break
            await self._execute_and_record(adapter, command)
            if command.status == CommandStatus.failed:
                skipped_at = now_utc()
                base_error = (command.error or "Command execution failed").strip()
                for queued in commands[idx + 1:]:
                    queued.status = CommandStatus.failed
                    queued.error = f"{base_error} (skipped subsequent command in same batch)"
                    queued.completed_at = skipped_at
                    queued.duration_ms = 0
                    self.store.update_command(queued)
                break

    async def _execute_batch_with_single_call(self, adapter, commands: list[CommandExecution]) -> None:
        if not commands:
            return

        async def _mark_running(command: CommandExecution, started_at):
            command.status = CommandStatus.running
            command.started_at = started_at
            command.completed_at = None
            command.duration_ms = None
            self.store.update_command(command)

        async def _mark_failed(command: CommandExecution, error: str, failed_at):
            command.status = CommandStatus.failed
            command.error = error
            command.completed_at = failed_at
            if command.started_at:
                command.duration_ms = max(0, int((failed_at - command.started_at).total_seconds() * 1000))
            self.store.update_command(command)

        async def _record_output(command: CommandExecution, output: str):
            await self._record_command_output(adapter, command, output)

        await run_compound_command_batch(
            adapter,
            commands,
            get_command_text=lambda item: item.command,
            mark_running=_mark_running,
            mark_failed=_mark_failed,
            record_output=_record_output,
            now_factory=now_utc,
        )

    async def _record_command_output(self, adapter, command: CommandExecution, output: str) -> None:
        apply_adapter_command_meta(command, adapter)

        if self._is_stop_requested(command.session_id):
            command.status = CommandStatus.rejected
            command.error = "Stopped by operator"
            command.completed_at = now_utc()
            if command.started_at:
                command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
            self.store.update_command(command)
            return

        parsed = parse_command_runtime(command.effective_command or command.command, output)
        self._trace_decision(
            session_id=command.session_id,
            step_type="evidence_parse",
            title="证据解析",
            detail=(
                f"command={command.command[:120]}; "
                f"category={parsed.category}; "
                f"conclusion={str(parsed.conclusion)[:140]}"
            ),
            detail_payload=self._compact_trace_payload(
                {
                    "command": self._command_trace_record(command, include_output=False),
                    "parser_result": {
                        "category": parsed.category,
                        "conclusion": parsed.conclusion,
                        "parsed_data": parsed.parsed_data,
                    },
                }
            ),
        )
        apply_device_profile_to_session_store(self.store, command.session_id, parsed.device_profile)

        command.output = output
        command.status = CommandStatus.succeeded
        command.completed_at = now_utc()
        if command.started_at:
            command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
        self._learn_command_capability_from_result(
            session_id=command.session_id,
            command=command,
            category=parsed.category,
            parsed_data=parsed.parsed_data,
            adapter=adapter,
        )
        self.store.update_command(command)

        evidence = Evidence(
            session_id=command.session_id,
            command_id=command.id,
            category=parsed.category,
            raw_output=output,
            parsed_data=parsed.parsed_data,
            conclusion=parsed.conclusion,
        )
        self.store.add_evidence(evidence)

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
        session_goal = self._derive_session_goal(session_id, user_content)
        sanitized_content = self._sanitize_for_llm(user_content)
        sanitized_goal = self._sanitize_for_llm(session_goal)
        controller_now_local = datetime.now().astimezone().isoformat()
        controller_now_utc = now_utc().isoformat()
        header = (
            f"会话ID: {session_id}\n"
            f"控制器当前时间(local): {controller_now_local}\n"
            f"控制器当前时间(utc): {controller_now_utc}\n"
            f"当前设备厂商: {vendor}\n"
            f"接入协议: {protocol}\n"
            f"会话模式: {operation_mode}\n"
            f"会话目标: {sanitized_goal}\n"
            f"本轮请求: {sanitized_content}\n"
            f"命令家族约束: {self._vendor_command_family_hint(vendor)}\n"
            "请只基于当前会话上下文继续诊断，禁止引用或臆测其他会话。\n"
            "优先围绕会话目标完成闭环，避免无证据的机会性扩展变更。"
        )
        self.store.append_ai_context(session_id, "user", header)
        self._trace_ai_context_submission(
            session_id=session_id,
            title="系统提交上下文（会话头）",
            role="user",
            source="session_header",
            content=header,
            extra_payload={
                "session_goal": sanitized_goal,
                "request": sanitized_content,
                "vendor": vendor,
                "protocol": protocol,
                "operation_mode": operation_mode,
            },
        )

    def _baseline_version_probe_plan(self, vendor: str) -> list[tuple[str, str]]:
        normalized_vendor = (vendor or "").strip().lower()
        if "huawei" in normalized_vendor:
            return [("基线探针/版本识别", "display version")]
        if "arista" in normalized_vendor or "cisco" in normalized_vendor:
            return [("基线探针/版本识别", "show version")]
        return [
            ("基线探针/版本识别(show)", "show version"),
            ("基线探针/版本识别(display)", "display version"),
        ]

    def _baseline_profile_plan(self, vendor: str) -> list[tuple[str, str]]:
        normalized_vendor = (vendor or "").strip().lower()
        if "huawei" in normalized_vendor:
            return [
                ("基线画像/设备时钟", "display clock"),
                ("基线画像/会话权限", "display users"),
            ]
        if "arista" in normalized_vendor or "cisco" in normalized_vendor:
            return [
                ("基线画像/设备时钟", "show clock"),
                ("基线画像/会话权限", "show privilege"),
            ]
        return [
            ("基线画像/设备时钟(show)", "show clock"),
            ("基线画像/会话权限(show)", "show privilege"),
            ("基线画像/设备时钟(display)", "display clock"),
            ("基线画像/会话权限(display)", "display users"),
        ]

    def _append_baseline_snapshot_to_ai_context(
        self,
        *,
        session_id: str,
        user_content: str,
        version_commands: list[CommandExecution],
        profile_commands: list[CommandExecution],
    ) -> None:
        session = self.store.get_session(session_id)
        local_now = datetime.now().astimezone().isoformat()
        utc_now = datetime.now(timezone.utc).isoformat()
        version_success = sum(1 for item in version_commands if item.status == CommandStatus.succeeded)
        profile_success = sum(1 for item in profile_commands if item.status == CommandStatus.succeeded)
        permission_entry = next((item for item in profile_commands if "会话权限" in (item.title or "")), None)
        permission_state, permission_hint = self._derive_permission_signal(
            permission_entry.command if permission_entry else "",
            permission_entry.output if permission_entry else "",
            permission_entry.error if permission_entry else "",
        )
        summary_lines: list[str] = []
        for command in profile_commands:
            raw = (command.output or command.error or "").strip().replace("\n", " ")
            compact = self._sanitize_for_llm(raw)[:220] if raw else "-"
            summary_lines.append(
                f"  - step={command.step_no}, title={command.title}, command={command.command}, "
                f"status={command.status.value}, sample={compact}"
            )
        baseline_text = (
            "基线上下文汇总\n"
            f"- 控制器当前时间(local): {local_now}\n"
            f"- 控制器当前时间(utc): {utc_now}\n"
            f"- 用户问题: {self._sanitize_for_llm(user_content)}\n"
            f"- 设备地址: {session.device.host}\n"
            f"- 设备名称: {session.device.name or '-'}\n"
            f"- 厂商: {session.device.vendor or '-'}\n"
            f"- 平台: {session.device.platform or '-'}\n"
            f"- 软件版本: {session.device.software_version or '-'}\n"
            f"- 版本指纹: {session.device.version_signature or '-'}\n"
            f"- 命令家族建议: {self._vendor_command_family_hint(session.device.vendor)}\n"
            f"- 版本探针成功数: {version_success}/{len(version_commands)}\n"
            f"- 权限探测状态: {permission_state or '-'}\n"
            f"- 权限动作建议: {permission_hint or '-'}\n"
            f"- 画像命令成功数: {profile_success}/{len(profile_commands)}\n"
            "- 画像结果摘要:\n"
            f"{chr(10).join(summary_lines) if summary_lines else '  - (无)'}\n"
            "请基于以上基线信息与会话证据规划下一步，不要重复无效探测。"
            "当厂商与版本已识别时，必须遵循命令家族建议，不要跨厂商盲试。"
        )
        self.store.append_ai_context(session_id, "user", baseline_text)
        self._trace_ai_context_submission(
            session_id=session_id,
            title="系统提交上下文（基线汇总）",
            role="user",
            source="baseline_snapshot",
            content=baseline_text,
            extra_payload={
                "baseline_version_commands": self._command_trace_records(version_commands, include_output=True),
                "baseline_profile_commands": self._command_trace_records(profile_commands, include_output=True),
            },
        )

    def _vendor_command_family_hint(self, vendor: str) -> str:
        normalized = (vendor or "").strip().lower()
        if "huawei" in normalized:
            return "Huawei 已识别：优先 display 家族命令；show 仅用于兼容探测且需说明原因。"
        if "arista" in normalized or "cisco" in normalized:
            return "Arista/Cisco-like 已识别：优先 show 家族命令；display 仅用于兼容探测且需说明原因。"
        return "厂商未识别：可先做最小兼容探测（show/display），识别后固定到同厂商命令家族。"

    def _derive_session_goal(self, session_id: str, fallback: str) -> str:
        messages = self.store.list_messages(session_id)
        # Keep a stable primary goal anchor across "继续执行" style turns.
        for message in messages:
            if message.role != "user":
                continue
            content = (message.content or "").strip()
            if not content:
                continue
            if self._is_continue_like_request(content):
                continue
            return content
        return (fallback or "").strip()

    def _is_continue_like_request(self, text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        patterns = [
            "继续执行",
            "继续下一步",
            "不要结束",
            "继续",
            "go on",
            "continue",
            "next step",
            "keep going",
        ]
        return any(token in lowered for token in patterns)

    def _append_command_result_to_ai_context(self, session_id: str, command: CommandExecution) -> None:
        text = (
            f"执行结果\n"
            f"- step: {command.step_no}\n"
            f"- title: {command.title}\n"
            f"- original_command: {command.original_command or command.command}\n"
            f"- effective_command: {command.effective_command or command.command}\n"
            f"- command: {command.command}\n"
            f"- status: {command.status.value}\n"
        )
        if command.capability_state:
            text += f"- capability_state: {command.capability_state}\n"
        if command.capability_reason:
            text += f"- capability_reason: {self._sanitize_for_llm(command.capability_reason)[:500]}\n"
        if command.constraint_source:
            text += f"- constraint_source: {command.constraint_source}\n"
        if command.constraint_reason:
            text += f"- constraint_reason: {self._sanitize_for_llm(command.constraint_reason)[:500]}\n"
        permission_state, permission_hint = self._derive_permission_signal(
            command.command,
            command.output or "",
            command.error or "",
        )
        if permission_state:
            text += f"- permission_state: {permission_state}\n"
        if permission_hint:
            text += f"- permission_action_hint: {permission_hint}\n"
        if command.error:
            text += f"- error: {self._sanitize_for_llm(command.error)[:1200]}\n"
        if command.output:
            text += f"- output:\n{self._sanitize_for_llm(command.output)[:2500]}"
        self.store.append_ai_context(session_id, "user", text)
        self._trace_ai_context_submission(
            session_id=session_id,
            title=f"系统提交上下文（命令结果 #{command.step_no}）",
            role="user",
            source="command_result",
            content=text,
            command_id=command.id,
            extra_payload={
                "command_full": self._command_trace_record(command, include_output=True),
            },
        )

    def _append_sop_context_to_ai_context(self, *, session_id: str, problem: str, vendor: str | None) -> None:
        sop_text = self.sop_archive.prompt_context(problem, vendor=vendor)
        if not sop_text:
            return
        self.store.append_ai_context(session_id, "user", sop_text)
        self._trace_ai_context_submission(
            session_id=session_id,
            title="系统提交上下文（SOP档案候选）",
            role="user",
            source="sop_archive",
            content=sop_text,
            extra_payload={
                "problem": self._sanitize_for_llm(problem)[:1200],
                "vendor": str(vendor or "").strip(),
            },
        )

    def _trace_ai_context_submission(
        self,
        *,
        session_id: str,
        title: str,
        role: str,
        source: str,
        content: str,
        command_id: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        trace = self._trace_start(
            session_id=session_id,
            step_type="ai_context_submit",
            title=title,
            command_id=command_id,
            detail=f"source={source}; role={role}",
        )
        self._trace_finish(
            trace,
            status="succeeded",
            detail=f"source={source}; role={role}",
            detail_payload=self._compact_trace_payload(
                {
                    "source": source,
                    "role": role,
                    "to_ai_context": content,
                    "command_id": command_id,
                    "extra": extra_payload or {},
                }
            ),
        )

    def _derive_permission_signal(self, command: str, output: str, error: str) -> tuple[str, str]:
        merged = f"{output}\n{error}".strip()
        lowered = merged.lower()
        normalized_command = command.strip().lower()

        level_match = re.search(r"(?i)current\s+privilege\s+level\s+is\s+(\d+)", merged)
        if level_match:
            level = int(level_match.group(1))
            if level >= 15:
                return ("privileged(level=15)", "已具备高权限，禁止重复enable，继续目标命令。")
            return (f"insufficient(level={level})", "权限不足，下一步应先最小提权（如enable）并立即复核权限。")

        if any(
            token in lowered
            for token in (
                "permission denied",
                "insufficient privileges",
                "not authorized",
                "authorization failed",
                "privilege level insufficient",
                "not enough privileges",
                "权限不足",
                "无权限",
            )
        ):
            return ("insufficient", "权限不足，下一步应先最小提权并立即复核权限。")

        if normalized_command == "enable":
            if any(token in lowered for token in ("already in privileged mode", "privilege level is 15")):
                return ("privileged(level=15)", "已经在特权模式，禁止重复enable。")
            if "#" in merged:
                return ("privileged", "已进入特权模式，可继续执行目标命令。")

        return ("", "")

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

    def _clip_trace_text(self, value: str, limit: int = 200000) -> str:
        text = str(value or "")
        if limit <= 0 or len(text) <= limit:
            return text
        return f"{text[:limit]}...(truncated,{len(text)} chars)"

    def _compact_trace_payload(
        self,
        payload: Any,
        *,
        depth: int = 0,
        max_depth: int = 10,
        max_items: int = 200,
        text_limit: int = 200000,
    ) -> Any:
        if depth >= max_depth:
            return "<max-depth>"
        if isinstance(payload, str):
            return self._clip_trace_text(self._sanitize_for_llm(payload), text_limit)
        if isinstance(payload, (int, float, bool)) or payload is None:
            return payload
        if isinstance(payload, dict):
            out: dict[str, Any] = {}
            for idx, (key, value) in enumerate(payload.items()):
                if idx >= max_items:
                    out["__truncated_items__"] = len(payload) - max_items
                    break
                out[str(key)] = self._compact_trace_payload(
                    value,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    text_limit=text_limit,
                )
            return out
        if isinstance(payload, list):
            out_list = []
            for idx, item in enumerate(payload):
                if idx >= max_items:
                    out_list.append({"__truncated_items__": len(payload) - max_items})
                    break
                out_list.append(
                    self._compact_trace_payload(
                        item,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_items=max_items,
                        text_limit=text_limit,
                    )
                )
            return out_list
        return self._clip_trace_text(self._sanitize_for_llm(str(payload)), text_limit)

    def _command_trace_record(self, command: CommandExecution, *, include_output: bool) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": command.id,
            "step_no": command.step_no,
            "title": command.title,
            "command": command.command,
            "original_command": command.original_command or command.command,
            "effective_command": command.effective_command or command.command,
            "risk_level": command.risk_level.value,
            "status": command.status.value,
            "requires_confirmation": command.requires_confirmation,
            "constraint_source": command.constraint_source or "",
            "constraint_reason": command.constraint_reason or "",
            "capability_state": command.capability_state or "",
            "capability_reason": command.capability_reason or "",
            "started_at": command.started_at.isoformat() if command.started_at else None,
            "completed_at": command.completed_at.isoformat() if command.completed_at else None,
            "duration_ms": command.duration_ms,
            "error": command.error or "",
        }
        if include_output:
            record["output"] = command.output or ""
        return record

    def _command_trace_records(self, commands: list[CommandExecution], *, include_output: bool) -> list[dict[str, Any]]:
        return [self._command_trace_record(item, include_output=include_output) for item in commands]

    def _build_llm_trace_payload(
        self,
        *,
        session,
        user_problem: str,
        plan_debug: dict[str, Any] | None,
        parsed_plan: dict[str, Any] | None,
        referenced_sops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._compact_trace_payload(
            {
                "device": {
                    "host": session.device.host,
                    "name": session.device.name or "",
                    "vendor": session.device.vendor,
                    "protocol": session.device.protocol.value,
                    "version_signature": session.device.version_signature or "",
                },
                "session_mode": session.operation_mode.value,
                "automation_level": session.automation_level.value,
                "user_problem": user_problem,
                "to_ai": (plan_debug or {}),
                "ai_response_parsed": parsed_plan,
                "referenced_sops": referenced_sops or [],
            }
        )

    def _referenced_sops_from_plan(self, plan: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(plan, dict):
            return []
        reference_text = " ".join(
            part.strip()
            for part in (
                str(plan.get("title", "") or ""),
                str(plan.get("reason", "") or ""),
            )
            if part and str(part).strip()
        )
        matches = self.sop_archive.referenced_entries(reference_text)
        return [item.model_dump(mode="json") for item in matches]

    def _trace_sop_reference(
        self,
        *,
        session_id: str,
        iteration: int,
        referenced_sops: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> None:
        if not referenced_sops:
            return
        names = [str(item.get("name") or item.get("id") or "").strip() for item in referenced_sops]
        names = [item for item in names if item]
        self._trace_decision(
            session_id=session_id,
            step_type="scope_decision",
            title=f"AI 引用 SOP 档案（第 {iteration} 轮）",
            detail=" ; ".join(names)[:280] or "referenced_sop_archive",
            detail_payload=self._compact_trace_payload(
                {
                    "iteration": iteration,
                    "referenced_sops": referenced_sops,
                    "plan_title": str(plan.get("title", "") or ""),
                    "plan_reason": str(plan.get("reason", "") or ""),
                }
            ),
        )

    def _build_llm_request_payload(self, plan_debug: Any) -> dict[str, Any]:
        source = plan_debug if isinstance(plan_debug, dict) else {}
        nested = source.get("llm") if isinstance(source.get("llm"), dict) else {}
        nested = nested if isinstance(nested, dict) else {}

        def pick(key: str) -> Any:
            if key in source:
                return source.get(key)
            if key in nested:
                return nested.get(key)
            return None

        payload: dict[str, Any] = {}
        for key in ("iteration", "max_iterations", "with_history", "system_prompt", "request_messages", "request_payload"):
            value = pick(key)
            if value is not None:
                payload[key] = value
        if source.get("final_prompt") is not None:
            payload["final_prompt"] = source.get("final_prompt")
        if source.get("context_count") is not None:
            payload["context_count"] = source.get("context_count")
        return self._compact_trace_payload(payload, max_depth=10, max_items=500, text_limit=200000)

    def _build_llm_response_payload(self, plan_debug: Any, parsed_plan: Any) -> dict[str, Any]:
        source = plan_debug if isinstance(plan_debug, dict) else {}
        llm_payload: dict[str, Any] = {}
        for key in ("raw_response", "parsed_response", "error"):
            if key in source:
                llm_payload[key] = source.get(key)
        if parsed_plan is not None:
            llm_payload["parsed_response"] = parsed_plan
        return self._compact_trace_payload(
            {
                "llm": llm_payload,
                "ai_response_parsed": parsed_plan,
            },
            max_depth=10,
            max_items=500,
            text_limit=200000,
        )

    async def confirm_command(self, session_id: str, command_id: str, request: ConfirmCommandRequest) -> ConfirmCommandResponse:
        session = self.store.get_session(session_id)
        command = self.store.get_command(session_id, command_id)

        if self._is_stop_requested(session_id):
            return ConfirmCommandResponse(
                command_id=command.id,
                status=command.status,
                message="Session is stopping; confirmation is temporarily blocked.",
            )

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
        async def _mark_rejected(_message: str) -> None:
            command.status = CommandStatus.rejected
            command.error = "Stopped by operator"
            command.completed_at = now_utc()
            command.duration_ms = 0
            self.store.update_command(command)

        async def _mark_running() -> None:
            command.status = CommandStatus.running
            command.started_at = now_utc()
            command.completed_at = None
            command.duration_ms = None
            self.store.update_command(command)

        async def _handle_success(output: str) -> None:
            await self._record_command_output(adapter, command, output)

        async def _handle_failure(message: str) -> None:
            command.status = CommandStatus.failed
            command.error = message
            command.completed_at = now_utc()
            if command.started_at:
                command.duration_ms = max(0, int((command.completed_at - command.started_at).total_seconds() * 1000))
            self.store.update_command(command)

        await execute_single_command(
            adapter,
            command.command,
            should_stop=lambda: self._is_stop_requested(command.session_id),
            on_rejected=_mark_rejected,
            on_running=_mark_running,
            on_success=_handle_success,
            on_failure=_handle_failure,
        )

    def _learn_command_capability_from_result(
        self,
        *,
        session_id: str,
        command: CommandExecution,
        category: str,
        parsed_data: dict,
        adapter,
    ) -> None:
        if self._is_baseline_collection_command(command):
            return
        meta = getattr(adapter, "last_command_meta", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        if bool(meta.get("simulated")):
            return

        retry_used = bool(meta.get("retry_used"))
        retry_to = str(meta.get("retry_to") or "").strip()
        retry_from = str(meta.get("retry_from") or "").strip()
        original_command = str(command.original_command or retry_from or command.command or "").strip()

        if retry_used and retry_to:
            if original_command and original_command.lower() != retry_to.lower():
                learned = self.store.learn_command_rewrite(
                    session_id=session_id,
                    failed_command=original_command,
                    rewrite_to=retry_to,
                    reason_text=str(meta.get("retry_error") or "learned from successful retry"),
                )
                if not learned:
                    session = self.store.get_session(session_id)
                    if not (session.device.version_signature or "").strip():
                        command.capability_state = "learn_skipped"
                        command.capability_reason = "version_signature missing, skipped learned rewrite"
                        self._trace_decision(
                            session_id=session_id,
                            step_type="capability_decision",
                            title="命令能力学习（跳过）",
                            detail=(
                                f"learn_skipped; from={original_command[:120]}; "
                                f"to={retry_to[:120]}; reason=missing version signature"
                            ),
                            status="skipped",
                        )
                    return
                command.capability_state = "learned_update"
                command.capability_rule_id = learned.id
                command.capability_reason = learned.reason_text or "rewrite learned from retry success"
                command.original_command = original_command
                command.effective_command = retry_to
                self._trace_decision(
                    session_id=session_id,
                    step_type="capability_decision",
                    title="命令能力学习（rewrite）",
                    detail=(
                        f"learned_update; from={original_command[:120]}; "
                        f"to={retry_to[:120]}; rule_id={learned.id}"
                    ),
                )
            return

        if category != "command_error":
            return

        reason = ""
        if isinstance(parsed_data, dict):
            reason = str(parsed_data.get("reason") or "").strip()
        if not reason:
            reason = "CLI syntax/parameter error"

        if self._is_mode_sensitive_command(original_command):
            command.capability_state = "learn_skipped"
            command.capability_reason = "context/mode-sensitive command, skipped learned block"
            command.original_command = original_command
            command.effective_command = command.command
            self._trace_decision(
                session_id=session_id,
                step_type="capability_decision",
                title="命令能力学习（跳过）",
                detail=(
                    f"learn_skipped; command={original_command[:120]}; "
                    f"reason=context/mode-sensitive command"
                ),
                status="skipped",
            )
            return

        if self._is_permission_or_mode_error(reason):
            command.capability_state = "learn_skipped"
            command.capability_reason = "permission/mode-sensitive error, skipped learned block"
            command.original_command = original_command
            command.effective_command = command.command
            self._trace_decision(
                session_id=session_id,
                step_type="capability_decision",
                title="命令能力学习（跳过）",
                detail=(
                    f"learn_skipped; command={original_command[:120]}; "
                    f"reason={reason[:120]}"
                ),
                status="skipped",
            )
            return

        if original_command:
            learned = self.store.learn_command_block(
                session_id=session_id,
                failed_command=original_command,
                reason_text=reason,
            )
            if not learned:
                session = self.store.get_session(session_id)
                if not (session.device.version_signature or "").strip():
                    command.capability_state = "learn_skipped"
                    command.capability_reason = "version_signature missing, skipped learned block"
                    command.original_command = original_command
                    command.effective_command = command.command
                    self._trace_decision(
                        session_id=session_id,
                        step_type="capability_decision",
                        title="命令能力学习（跳过）",
                        detail=(
                            f"learn_skipped; command={original_command[:120]}; "
                            "reason=missing version signature"
                        ),
                        status="skipped",
                    )
                return
            command.capability_state = "learned_update"
            command.capability_rule_id = learned.id
            command.capability_reason = learned.reason_text or reason
            command.original_command = original_command
            command.effective_command = command.command
            self._trace_decision(
                session_id=session_id,
                step_type="capability_decision",
                title="命令能力学习（block）",
                detail=(
                    f"learned_update; command={original_command[:120]}; "
                    f"reason={reason[:120]}; rule_id={learned.id}"
                ),
                status="failed",
            )

    def _is_baseline_collection_command(self, command: CommandExecution) -> bool:
        title = str(getattr(command, "title", "") or "").strip()
        if not title:
            return False
        return title.startswith("基线探针/") or title.startswith("基线画像/")

    def _is_permission_or_mode_error(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        return any(
            token in lowered
            for token in (
                "privileged mode required",
                "permission denied",
                "insufficient privilege",
                "insufficient privileges",
                "not authorized",
                "authorization failed",
                "privilege level insufficient",
                "not enough privileges",
                "requires enable",
                "enable mode required",
                "requires privileged",
                "权限不足",
                "无权限",
                "权限不够",
            )
        )

    def _is_mode_sensitive_command(self, command: str) -> bool:
        normalized = " ".join(str(command or "").strip().lower().split())
        if not normalized:
            return False
        if normalized in {"?", "??"}:
            return True
        if normalized.startswith(("show ", "display ", "ping ", "traceroute ", "tracert ")):
            # Read-only probes are generally safe to learn, except commands that
            # are explicitly tied to a config/view context.
            return normalized.startswith("display this")
        if "|" in normalized:
            # Avoid treating generic filtered probes as mode-sensitive.
            return False
        return any(
            normalized.startswith(prefix)
            for prefix in (
                "configure terminal",
                "system-view",
                "interface ",
                "subinterface ",
                "undo ",
                "shutdown",
                "ip address ",
                "ospf ",
                "bgp ",
                "router ",
                "vlan ",
                "port ",
                "return",
                "end",
                "exit",
                "commit",
                "save",
                "write memory",
                "execute ",
            )
        )

    async def _get_session_adapter(self, session):
        adapter = self._session_adapters.get(session.id)
        try:
            adapter, _ = await ensure_connected_adapter(
                adapter,
                session,
                allow_simulation=self.allow_simulation,
                build_factory=build_adapter,
                on_create=lambda _adapter: self._trace_decision(
                    session_id=session.id,
                    step_type="session_adapter",
                    title="会话控制：设备连接会话创建",
                    detail=f"adapter_created; host={session.device.host}; protocol={session.device.protocol.value}",
                    detail_payload=self._compact_trace_payload(
                        {
                            "host": session.device.host,
                            "protocol": session.device.protocol.value,
                            "vendor": session.device.vendor,
                            "action": "created",
                        }
                    ),
                ),
            )
        except Exception:
            self._session_adapters.pop(session.id, None)
            raise
        self._session_adapters[session.id] = adapter
        return adapter

    async def _drop_session_adapter(self, session_id: str) -> None:
        adapter = self._session_adapters.pop(session_id, None)
        if adapter is None:
            return
        try:
            await close_connected_adapter(
                adapter,
                on_close=lambda: self._trace_decision(
                    session_id=session_id,
                    step_type="session_adapter",
                    title="会话控制：设备连接会话关闭",
                    detail="adapter_closed",
                ),
            )
        except Exception:
            pass

    async def _request_final_from_context(
        self,
        session_id: str,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> IncidentSummary | None:
        summary, _ = await self._request_final_from_context_with_debug(
            session_id=session_id,
            preferred_mode=preferred_mode,
        )
        return summary

    async def _request_final_from_context_with_debug(
        self,
        session_id: str,
        preferred_mode: Literal["query", "diagnosis", "config"] | None = None,
    ) -> tuple[IncidentSummary | None, dict[str, Any]]:
        session = self.store.get_session(session_id)
        ai_context = list(self.store.list_ai_context(session_id))
        debug: dict[str, Any] = {
            "session_id": session_id,
            "preferred_mode": preferred_mode or "",
            "context_count": len(ai_context),
        }
        if not ai_context:
            debug["error"] = "empty_context"
            return None, debug

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
        debug["final_prompt"] = self._clip_trace_text(final_prompt, 12000)
        ai_context.append({"role": "user", "content": final_prompt})
        plan, plan_debug = await self._propose_next_step_with_debug(
            session=session,
            user_problem="",
            commands=self.store.list_commands(session_id),
            evidences=self.store.list_evidence(session_id),
            iteration=self.max_autonomous_steps + 1,
            max_iterations=self.max_autonomous_steps,
            conversation_history=ai_context,
        )
        debug["llm"] = plan_debug
        if not plan:
            return None, debug
        if str(plan.get("decision", "")).strip().lower() != "final":
            debug["error"] = "decision_not_final"
            debug["plan"] = plan
            return None, debug
        summary = self._summary_from_plan(session_id, plan, preferred_mode=preferred_mode)
        debug["plan"] = plan
        if summary is None:
            debug["error"] = "invalid_final_payload"
            return None, debug
        return summary, debug

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

    def _render_plan_message(
        self,
        *,
        iteration: int,
        plan: dict[str, Any],
        plan_commands: list[tuple[str, str]],
    ) -> str:
        title = str(plan.get("title", "")).strip() or f"第 {iteration} 轮执行计划"
        reason = str(plan.get("reason", "")).strip() or "基于当前证据继续取证。"
        command_lines = [f"{idx}. {cmd}" for idx, (_cmd_title, cmd) in enumerate(plan_commands, start=1)]
        commands_text = "\n".join(command_lines)
        return (
            f"AI 规划（第 {iteration} 轮）\n"
            f"目标: {title}\n"
            f"判断: {reason}\n"
            f"下一步命令({len(plan_commands)}条):\n"
            f"{commands_text}"
        )

    def _preferred_mode_from_session(self, operation_mode) -> Literal["query", "diagnosis", "config"]:
        value = str(getattr(operation_mode, "value", operation_mode or "")).strip().lower()
        if value == "query":
            return "query"
        if value == "config":
            return "config"
        return "diagnosis"

    def _is_stop_requested(self, session_id: str) -> bool:
        return session_id in self._stop_requested_sessions

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
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "confirm_batch_start",
                        "commands": self._command_trace_records(commands, include_output=False),
                    }
                ),
            )
            await self._execute_batch_and_record(adapter, commands)
            first_error = next((command.error for command in commands if command.status == CommandStatus.failed), "")
            if first_error:
                self._trace_finish(
                    confirm_trace,
                    status="failed",
                    detail=first_error[:300],
                    detail_payload=self._compact_trace_payload(
                        {
                            "phase": "confirm_batch_failed",
                            "error": first_error,
                            "commands": self._command_trace_records(commands, include_output=True),
                        }
                    ),
                )
            else:
                first_output = next((command.output for command in commands if command.output), "")
                self._trace_finish(
                    confirm_trace,
                    status="succeeded",
                    detail=(first_output or "")[:300],
                    detail_payload=self._compact_trace_payload(
                        {
                            "phase": "confirm_batch_succeeded",
                            "commands": self._command_trace_records(commands, include_output=True),
                        }
                    ),
                )
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
            self._trace_finish(
                confirm_trace,
                status="failed",
                detail=str(exc)[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "confirm_batch_failed",
                        "error": str(exc),
                        "commands": self._command_trace_records(commands, include_output=True),
                    }
                ),
            )
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
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "confirm_single_start",
                        "command": self._command_trace_record(command, include_output=False),
                    }
                ),
            )
            await self._execute_and_record(adapter, command)
            self._trace_finish(
                confirm_trace,
                status=command.status.value,
                detail=(command.error or command.output or "")[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "confirm_single_completed",
                        "command": self._command_trace_record(command, include_output=True),
                    }
                ),
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
            self._trace_finish(
                confirm_trace,
                status="failed",
                detail=str(exc)[:300],
                detail_payload=self._compact_trace_payload(
                    {
                        "phase": "confirm_single_failed",
                        "error": str(exc),
                        "command": self._command_trace_record(command, include_output=True),
                    }
                ),
            )
            await self._drop_session_adapter(session.id)

    def _trace_start(
        self,
        *,
        session_id: str,
        step_type: str,
        title: str,
        command_id: str | None = None,
        detail: str | None = None,
        detail_payload: dict[str, Any] | None = None,
    ) -> ServiceTraceStep:
        step = ServiceTraceStep(
            session_id=session_id,
            seq_no=self.store.next_trace_seq(session_id),
            step_type=step_type,
            title=title,
            status="running",
            command_id=command_id,
            detail=detail,
            detail_payload=detail_payload,
        )
        self.store.add_trace_step(step)
        self._trace_perf_started[step.id] = time.perf_counter()
        return step

    def _trace_finish(
        self,
        step: ServiceTraceStep,
        *,
        status: str,
        detail: str | None = None,
        detail_payload: dict[str, Any] | None = None,
    ) -> None:
        started = self._trace_perf_started.pop(step.id, None)
        step.status = status
        step.completed_at = now_utc()
        if started is not None:
            step.duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        elif step.started_at:
            step.duration_ms = max(0, int((step.completed_at - step.started_at).total_seconds() * 1000))
        if detail:
            step.detail = detail
        if detail_payload is not None:
            step.detail_payload = detail_payload
        self.store.update_trace_step(step)

    def _trace_decision(
        self,
        *,
        session_id: str,
        step_type: str,
        title: str,
        detail: str,
        status: str = "succeeded",
        detail_payload: dict[str, Any] | None = None,
    ) -> None:
        step = self._trace_start(
            session_id=session_id,
            step_type=step_type,
            title=title,
            detail=detail[:280],
            detail_payload=detail_payload,
        )
        self._trace_finish(step, status=status, detail=detail[:280], detail_payload=detail_payload)
