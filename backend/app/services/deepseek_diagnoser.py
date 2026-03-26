from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from app.models.schemas import CommandExecution, Evidence, IncidentSummary, Session

OUTPUT_COMPACTION_RULES = (
    "为减少命令回显长度并提升执行效率，生成命令时必须优先使用“最小化输出”策略。"
    "优先使用设备支持的过滤语法，例如 include/exclude/begin/section/count/match/grep/regex。"
    "若不确定过滤语法是否支持，先用简短探测命令确认，再选择等效过滤写法。"
    "先执行摘要型命令，再按命中结果执行细节命令，避免直接抓取全量输出。"
    "每轮命令应控制在2-5条，且每条命令都要有明确目标，禁止无目标全量采集。"
    "commands数组建议使用对象结构{title, command}，title需简述该命令要验证的信号。"
    "当decision=final时，仅引用关键证据行，禁止粘贴大段原始回显。"
)

PERMISSION_PRECHECK_RULES = (
    "执行查询前必须先评估当前会话是否具备所需执行权限。"
    "在基线识别命令（如show version/display version）完成后，下一步优先检查当前权限级别与会话模式。"
    "若第一次发现权限不足，应优先返回最小必要提权命令并立即复核权限，不要继续无关查询。"
    "对可能受权限限制的命令，先返回权限探测命令（如角色/级别/模式检查），再决定是否继续执行目标命令。"
    "若探测结果显示权限不足，不要继续盲目下发后续命令，应先输出明确的提权或放权需求。"
    "若最近证据已显示在特权/配置模式，禁止重复输出enable或system-view这类提权命令。"
    "当用户可确认放行时，应将待执行命令组一次性给出，避免逐条失败后再补权限。"
)

ACTION_MARKER_RULES = (
    "当decision=final时，必须在行动建议文本中显式给出状态词。"
    "若仍需执行动作（如继续配置/修复），follow_up_action或recommendation必须包含以下词之一：建议执行、修复、打开、变更。"
    "若任务已闭环且无需继续动作，follow_up_action或recommendation必须包含以下词之一：已完成、无需。"
)

MINIMAL_CHANGE_RULES = (
    "变更执行遵循通用“最小必要变更”原则："
    "优先闭环当前会话目标与已确认根因，不做机会性扩展修复。"
    "若发现其他潜在问题，可写入follow_up_action/recommendation，但不要在同一轮直接下发无关变更命令。"
    "配置命令必须具备证据锚点：命令中的目标对象需在用户目标或当前会话证据中出现。"
    "若已定位到单一对象（如某接口/邻居/下一跳），后续变更仅允许作用于该对象，禁止扩展到未验证对象。"
    "若要引入新对象或新子域，先返回只读验证命令收集证据，待证据成立后再变更。"
)

BASELINE_CONTEXT_RULES = (
    "系统已在每轮开始前自动执行基线采集：版本识别、设备时钟、会话权限。"
    "你应优先利用这些基线证据做判断，减少重复探测命令。"
    "仅在证据不足或状态可能变化时，再追加必要复核命令。"
)

VENDOR_COMMAND_FAMILY_RULES = (
    "当会话中已识别厂商/平台/版本（如厂商不为unknown，或存在版本指纹）时，必须遵循命令家族一致性。"
    "Huawei/华为设备优先使用display家族命令；Arista/Cisco-like设备优先使用show家族命令。"
    "除非为会话控制命令（如enable/terminal length/screen-length）或已明确说明兼容探测原因，禁止跨家族盲试。"
    "若出现一次“命令不识别/参数错误”且可判断为家族不匹配，下一轮必须切换到同厂商等效命令，不要重复原家族。"
    "禁止在相邻两轮中反复 show/display 来回切换；仅在证据表明当前家族不可用时才切换，并在reason说明原因。"
)

HISTORY_FORENSICS_RULES = (
    "当用户问题包含“上次/历史/曾经/闪断/flap/间歇”这类历史性故障诉求时："
    "下一轮命令必须优先包含历史取证命令（设备日志、告警、协议邻接变化记录），不能只看当前瞬时状态。"
    "若协议相关（如OSPF/BGP），应优先给出协议事件日志与邻接变化证据采集命令。"
    "若日志命令不可用或无记录，必须在reason中明确说明“日志证据不足/不可得”，再给出替代取证路径。"
)

MODE_SCOPE_RULES = (
    "你必须严格遵守会话模式边界（字段“会话模式”）："
    "当会话模式为query或diagnosis时，只能输出采集/查询类命令，禁止输出配置变更类命令。"
    "配置变更类命令包括但不限于configure terminal/system-view/interface/shutdown/no shutdown/undo/save/write memory/commit。"
    "当会话模式为config时，允许输出配置变更命令，但必须先给出必要的只读验证，并在变更后给出复核命令。"
    "若当前模式无法完成目标，请在reason中明确说明“需要切换到配置模式”，不要直接越界下发命令。"
)

NEXT_STEP_SYSTEM_PROMPT_WITH_HISTORY = (
    "你是网络故障诊断代理。"
    "你正在同一会话内连续对话，必须结合已有上下文。"
    "你无法访问其他会话，禁止引用其他会话的信息。"
    "你的任务是决定下一步动作。"
    "只输出JSON对象。"
    "字段: decision, title, command, commands, reason, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "decision只能是run_command或final。"
    "run_command时优先使用commands（数组，最多5条）；仅在确实只有单条且无需分组时才使用command。"
    "commands每项可为字符串，或对象{title, command}。"
    "final时如果是查询任务，mode=query且必须给出query_result，可选follow_up_action；"
    "final时如果是配置任务，mode=config且必须给出query_result，可选follow_up_action；"
    "final时如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
    "confidence是0到1。evidence_refs是数组，quote应来自会话中的证据输出。"
    "若要执行配置命令，必须先有只读取证证明目标对象存在且状态明确。"
    "当用户未明确提供对象标识（如具体接口名）时，禁止直接输出配置命令，必须先输出只读发现命令。"
    "禁止凭空假设接口名（如Ethernet1/Gi1/0/1）并直接下发配置。"
    f"{OUTPUT_COMPACTION_RULES}"
    f"{PERMISSION_PRECHECK_RULES}"
    f"{MODE_SCOPE_RULES}"
    f"{ACTION_MARKER_RULES}"
    f"{MINIMAL_CHANGE_RULES}"
    f"{BASELINE_CONTEXT_RULES}"
    f"{VENDOR_COMMAND_FAMILY_RULES}"
    f"{HISTORY_FORENSICS_RULES}"
)

NEXT_STEP_SYSTEM_PROMPT = (
    "你是网络故障诊断代理。"
    "任务是基于用户问题和已有证据，决定下一步动作。"
    "你可以自由决定诊断路径，不使用固定剧本。"
    "只输出JSON对象。"
    "字段: decision, title, command, commands, reason, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "decision只能是run_command或final。"
    "当decision为run_command时，优先使用commands（数组，最多5条）；仅在确实只有单条且无需分组时才使用command。优先只读排查命令。"
    "commands每项可为字符串，或对象{title, command}。"
    "当decision为final时，如果是查询任务，mode=query且必须给出query_result；"
    "当decision为final时，如果是配置任务，mode=config且必须给出query_result；"
    "当decision为final时，如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
    "confidence是0到1。evidence_refs是数组，且quote必须来自已有证据原文。"
    "若要执行配置命令，必须先有只读取证证明目标对象存在且状态明确。"
    "当用户未明确提供对象标识（如具体接口名）时，禁止直接输出配置命令，必须先输出只读发现命令。"
    "禁止凭空假设接口名（如Ethernet1/Gi1/0/1）并直接下发配置。"
    f"{OUTPUT_COMPACTION_RULES}"
    f"{PERMISSION_PRECHECK_RULES}"
    f"{MODE_SCOPE_RULES}"
    f"{ACTION_MARKER_RULES}"
    f"{MINIMAL_CHANGE_RULES}"
    f"{BASELINE_CONTEXT_RULES}"
    f"{VENDOR_COMMAND_FAMILY_RULES}"
    f"{HISTORY_FORENSICS_RULES}"
)

PRIMARY_SUMMARY_SYSTEM_PROMPT = (
    "你是网络故障诊断引擎。"
    "严格依据输入证据判断，不得猜测。"
    "若证据不足以确认根因，必须明确说明不确定。"
    "只输出JSON对象。"
    "字段必须是: root_cause, impact_scope, recommendation, confidence, evidence_refs。"
    "confidence是0到1的小数。"
    "evidence_refs是数组，每项包含 command_step, quote, why。quote必须是输入证据中的原文片段。"
)

REVIEW_SYSTEM_PROMPT = (
    "你是网络诊断结果审稿器。"
    "只能依据证据审查，不得引入新事实。"
    "如果candidate缺证据支撑，给出fail并可附corrected_summary。"
    "只输出JSON对象。"
)

REWRITE_SYSTEM_PROMPT = (
    "你是网络诊断改写器。"
    "只依据证据，修正不被支持的结论。"
    "只输出JSON对象。"
)


class DeepSeekDiagnoser:
    def __init__(self) -> None:
        self.default_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        self.default_nvidia_base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
        self.default_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.nvidia_api_key = os.getenv("NVIDIA_API_KEY", "").strip()
        self.base_url = self.default_base_url
        self.nvidia_base_url = self.default_nvidia_base_url
        self.model = self.default_model
        self.failover_enabled = os.getenv("NETOPS_MODEL_FAILOVER_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
        self.batch_execution_enabled = (
            os.getenv("NETOPS_BATCH_EXECUTION_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
        )
        self.model_candidates = self._normalize_model_candidates(
            (os.getenv("DEEPSEEK_MODEL_CANDIDATES", "deepseek-chat,deepseek-reasoner")).split(",")
        )
        if self.model:
            self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model: Optional[str] = self.model
        self.last_error: Optional[str] = None
        self.last_error_code: Optional[str] = None
        self.last_failover_at: Optional[datetime] = None
        self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))
        env_config_path = (os.getenv("NETOPS_LLM_CONFIG_PATH") or "").strip()
        self.config_path = Path(env_config_path).expanduser() if env_config_path else self._default_config_path()
        self.legacy_config_path = Path(tempfile.gettempdir()) / "netops_ai_v1_llm_config.json"
        self._load_saved_config()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.nvidia_api_key)

    def configure(
        self,
        *,
        api_key: Optional[str] = None,
        nvidia_api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        nvidia_base_url: Optional[str] = None,
        model: Optional[str] = None,
        failover_enabled: Optional[bool] = None,
        model_candidates: Optional[list[str]] = None,
        batch_execution_enabled: Optional[bool] = None,
    ) -> None:
        if api_key is not None:
            self.api_key = api_key.strip()
        if nvidia_api_key is not None:
            self.nvidia_api_key = nvidia_api_key.strip()
        if base_url:
            self.base_url = base_url.strip().rstrip("/")
        if nvidia_base_url:
            self.nvidia_base_url = nvidia_base_url.strip().rstrip("/")
        if model:
            self.model = model.strip()
        if failover_enabled is not None:
            self.failover_enabled = bool(failover_enabled)
        if batch_execution_enabled is not None:
            self.batch_execution_enabled = bool(batch_execution_enabled)
        if model_candidates is not None:
            self.model_candidates = self._normalize_model_candidates(model_candidates)
        self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model = self.model
        self.last_error = None
        self.last_error_code = None
        self._save_config()

    def delete_saved_config(self) -> None:
        self.api_key = ""
        self.nvidia_api_key = ""
        self.base_url = self.default_base_url
        self.nvidia_base_url = self.default_nvidia_base_url
        self.model = self.default_model
        self.active_model = self.default_model
        self.last_error = None
        self.last_error_code = None
        self.last_failover_at = None
        try:
            if self.config_path.exists():
                self.config_path.unlink()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "active_model": self.active_model or self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
            "nvidia_enabled": bool(self.nvidia_api_key),
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "unavailable_reason": self._unavailable_reason(),
            "last_failover_at": self.last_failover_at,
        }

    def prompt_strategy(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "active_model": self.active_model or self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
            "nvidia_enabled": bool(self.nvidia_api_key),
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "prompts": {
                "next_step_history": self._next_step_prompt(with_history=True),
                "next_step_default": self._next_step_prompt(with_history=False),
                "summary_primary": PRIMARY_SUMMARY_SYSTEM_PROMPT,
                "summary_review": REVIEW_SYSTEM_PROMPT,
                "summary_rewrite": REWRITE_SYSTEM_PROMPT,
            },
        }

    def _load_saved_config(self) -> None:
        data = self._read_config(self.config_path)
        loaded_from = self.config_path
        if data is None and self.legacy_config_path != self.config_path:
            data = self._read_config(self.legacy_config_path)
            loaded_from = self.legacy_config_path
        if data is None:
            return
        self._apply_loaded_config(data)

        # Migrate legacy temp-file config to persistent default location.
        if loaded_from == self.legacy_config_path and self.config_path != self.legacy_config_path:
            self._save_config()
            try:
                self.legacy_config_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _save_config(self) -> None:
        payload = {
            "api_key": self.api_key,
            "nvidia_api_key": self.nvidia_api_key,
            "base_url": self.base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "model": self.model,
            "failover_enabled": self.failover_enabled,
            "batch_execution_enabled": self.batch_execution_enabled,
            "model_candidates": list(self.model_candidates),
        }
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(self.config_path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def _default_config_path(self) -> Path:
        home = Path.home()
        if str(home).strip() and str(home) != "/":
            return home / ".netops-ai-v1" / "llm_config.json"
        return Path(tempfile.gettempdir()) / "netops_ai_v1_llm_config.json"

    def _read_config(self, path: Path) -> Optional[dict[str, Any]]:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _apply_loaded_config(self, data: dict[str, Any]) -> None:
        api_key = str(data.get("api_key", "")).strip()
        nvidia_api_key = str(data.get("nvidia_api_key", "")).strip()
        base_url = str(data.get("base_url", "")).strip().rstrip("/")
        nvidia_base_url = str(data.get("nvidia_base_url", "")).strip().rstrip("/")
        model = str(data.get("model", "")).strip()
        failover_enabled = data.get("failover_enabled")
        batch_execution_enabled = data.get("batch_execution_enabled")
        candidates = data.get("model_candidates")
        if api_key:
            self.api_key = api_key
        if nvidia_api_key:
            self.nvidia_api_key = nvidia_api_key
        if base_url:
            self.base_url = base_url
        if nvidia_base_url:
            self.nvidia_base_url = nvidia_base_url
        if model:
            self.model = model
        if isinstance(failover_enabled, bool):
            self.failover_enabled = failover_enabled
        if isinstance(batch_execution_enabled, bool):
            self.batch_execution_enabled = batch_execution_enabled
        if isinstance(candidates, list):
            self.model_candidates = self._normalize_model_candidates([str(item) for item in candidates])
        self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
        self.active_model = self.model

    async def diagnose(
        self,
        session: Session,
        commands: list[CommandExecution],
        evidences: list[Evidence],
    ) -> Optional[IncidentSummary]:
        if not self.enabled:
            return None

        payload = self._build_payload(session, commands, evidences)

        primary = await self._run_primary(payload)
        if primary is None:
            return None

        review = await self._run_review(payload, primary)
        if review is None:
            return self._to_incident_summary(session.id, primary)

        if review.get("verdict") == "pass":
            return self._to_incident_summary(session.id, primary)

        corrected_summary = review.get("corrected_summary")
        candidate = corrected_summary if isinstance(corrected_summary, dict) else None

        if candidate is None:
            candidate = await self._run_rewrite(payload, primary, review.get("issues", []))
            if candidate is None:
                return self._to_incident_summary(session.id, primary)

        second_review = await self._run_review(payload, candidate)
        if second_review is None:
            return self._to_incident_summary(session.id, candidate)
        if second_review.get("verdict") != "pass":
            return self._to_incident_summary(session.id, candidate)

        return self._to_incident_summary(session.id, candidate)

    async def propose_next_step(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> Optional[dict[str, Any]]:
        plan, _ = await self.propose_next_step_with_debug(
            session=session,
            user_problem=user_problem,
            commands=commands,
            evidences=evidences,
            iteration=iteration,
            max_iterations=max_iterations,
            conversation_history=conversation_history,
        )
        return plan

    async def propose_next_step_with_debug(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
        debug: dict[str, Any] = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "with_history": bool(conversation_history),
        }
        if not self.enabled:
            debug["error"] = "llm_disabled"
            return None, debug

        if conversation_history:
            system_prompt = self._next_step_prompt(with_history=True)
            history = self._normalize_history_messages(conversation_history)
            debug["system_prompt"] = self._clip_trace_text(system_prompt, 200000)
            debug["request_messages"] = [
                {
                    "role": item.get("role", ""),
                    "content": self._clip_trace_text(item.get("content", ""), 200000),
                }
                for item in [{"role": "system", "content": system_prompt}, *history]
            ]
            content = await self._chat_completion_messages(
                system_prompt=system_prompt,
                messages=history,
            )
            debug["raw_response"] = self._clip_trace_text(content, 200000)
            if not content:
                debug["error"] = "empty_response"
                return None, debug
            parsed = self._parse_json_object(content)
            if not parsed:
                debug["error"] = "unparseable_json"
                return None, debug
            decision = str(parsed.get("decision", "")).strip().lower()
            if decision not in {"run_command", "final"}:
                debug["error"] = f"invalid_decision:{decision}"
                debug["parsed_response"] = parsed
                return None, debug
            parsed["decision"] = decision
            debug["parsed_response"] = parsed
            return parsed, debug

        payload = self._build_next_step_payload(
            session=session,
            user_problem=user_problem,
            commands=commands,
            evidences=evidences,
            iteration=iteration,
            max_iterations=max_iterations,
        )
        system_prompt = self._next_step_prompt(with_history=False)
        debug["system_prompt"] = self._clip_trace_text(system_prompt, 200000)
        debug["request_payload"] = payload

        content = await self._chat_completion(
            system_prompt=system_prompt,
            user_payload=payload,
        )
        debug["raw_response"] = self._clip_trace_text(content, 200000)
        if not content:
            debug["error"] = "empty_response"
            return None, debug
        parsed = self._parse_json_object(content)
        if not parsed:
            debug["error"] = "unparseable_json"
            return None, debug

        decision = str(parsed.get("decision", "")).strip().lower()
        if decision not in {"run_command", "final"}:
            debug["error"] = f"invalid_decision:{decision}"
            debug["parsed_response"] = parsed
            return None, debug
        parsed["decision"] = decision
        debug["parsed_response"] = parsed
        return parsed, debug

    def _build_payload(
        self,
        session: Session,
        commands: list[CommandExecution],
        evidences: list[Evidence],
    ) -> dict[str, Any]:
        return {
            "session": {
                "id": session.id,
                "vendor": session.device.vendor,
                "protocol": session.device.protocol.value,
                "issue_scope": session.issue_scope,
            },
            "commands": [
                {
                    "step_no": cmd.step_no,
                    "title": cmd.title,
                    "command": cmd.command,
                    "status": cmd.status.value,
                    "risk_level": cmd.risk_level.value,
                    "output": (cmd.output or "")[:2500],
                    "error": cmd.error,
                }
                for cmd in commands
            ],
            "evidences": [
                {
                    "category": evidence.category,
                    "conclusion": evidence.conclusion,
                    "raw_output": evidence.raw_output[:2500],
                    "parsed_data": evidence.parsed_data,
                }
                for evidence in evidences
            ],
            "task": (
                "请给出根因、影响范围、建议。"
                "只能依据证据，不得增加未出现的假设。"
            ),
        }

    def _build_next_step_payload(
        self,
        *,
        session: Session,
        user_problem: str,
        commands: list[CommandExecution],
        evidences: list[Evidence],
        iteration: int,
        max_iterations: int,
    ) -> dict[str, Any]:
        return {
            "session": {
                "id": session.id,
                "vendor": session.device.vendor,
                "protocol": session.device.protocol.value,
            },
            "user_problem": user_problem,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "commands": [
                {
                    "step_no": cmd.step_no,
                    "title": cmd.title,
                    "command": cmd.command,
                    "status": cmd.status.value,
                    "output": (cmd.output or "")[:2500],
                    "error": cmd.error,
                }
                for cmd in commands
            ],
            "evidences": [
                {
                    "category": evidence.category,
                    "conclusion": evidence.conclusion,
                    "raw_output": evidence.raw_output[:2500],
                    "parsed_data": evidence.parsed_data,
                }
                for evidence in evidences
            ],
        }

    async def _run_primary(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        content = await self._chat_completion(
            system_prompt=PRIMARY_SUMMARY_SYSTEM_PROMPT,
            user_payload=payload,
        )
        if not content:
            return None
        parsed = self._parse_json_object(content)
        if not parsed:
            return None
        return parsed

    async def _run_review(self, payload: dict[str, Any], candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
        review_payload = {
            "evidence_package": payload,
            "candidate_summary": candidate,
            "review_task": (
                "审查candidate_summary是否完全由证据支持。"
                "如果evidence_refs中的quote在证据里找不到，必须判定fail。"
                "仅输出JSON对象，字段: verdict, issues, corrected_summary。"
                "verdict只能是pass或fail。"
                "issues是字符串数组。"
                "corrected_summary要么是null，要么是与candidate同结构的JSON。"
            ),
        }
        content = await self._chat_completion(
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_payload=review_payload,
        )
        if not content:
            return None
        parsed = self._parse_json_object(content)
        if not parsed:
            return None
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in {"pass", "fail"}:
            return None
        parsed["verdict"] = verdict
        if "issues" not in parsed or not isinstance(parsed["issues"], list):
            parsed["issues"] = []
        return parsed

    async def _run_rewrite(
        self,
        payload: dict[str, Any],
        previous_summary: dict[str, Any],
        issues: list[Any],
    ) -> Optional[dict[str, Any]]:
        rewrite_payload = {
            "evidence_package": payload,
            "previous_summary": previous_summary,
            "issues": issues,
            "task": (
                "根据issues重写诊断结果。"
                "只输出JSON对象。"
                "字段必须是: root_cause, impact_scope, recommendation, confidence, evidence_refs。"
            ),
        }
        content = await self._chat_completion(
            system_prompt=REWRITE_SYSTEM_PROMPT,
            user_payload=rewrite_payload,
        )
        if not content:
            return None
        return self._parse_json_object(content)

    async def _chat_completion(self, *, system_prompt: str, user_payload: dict[str, Any]) -> str:
        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        data = await self._post_json(request_body)
        if data:
            return self._extract_content(data)

        # Compatibility retry for providers that do not support response_format.
        request_body.pop("response_format", None)
        data = await self._post_json(request_body)
        if not data:
            return ""
        return self._extract_content(data)

    async def _chat_completion_messages(self, *, system_prompt: str, messages: list[dict[str, str]]) -> str:
        history = self._normalize_history_messages(messages)

        request_body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt}, *history],
        }
        data = await self._post_json(request_body)
        if data:
            return self._extract_content(data)

        request_body.pop("response_format", None)
        data = await self._post_json(request_body)
        if not data:
            return ""
        return self._extract_content(data)

    def _normalize_history_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        history = []
        for item in messages:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            history.append({"role": role, "content": content})
        return history

    def _clip_trace_text(self, value: Any, limit: int) -> str:
        text = str(value or "")
        if limit <= 0:
            return text
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...(truncated,{len(text)} chars)"

    async def _post_json(self, request_body: dict[str, Any]) -> Optional[dict[str, Any]]:
        requested = str(request_body.get("model") or self.model).strip() or self.default_model
        models = self._candidate_model_order(requested)
        if not self.failover_enabled and models:
            models = [models[0]]

        first_model = models[0] if models else requested
        first_error: Optional[str] = None
        first_error_code: Optional[str] = None
        for idx, model in enumerate(models):
            body = dict(request_body)
            body["model"] = model
            data, error, error_code = await self._post_json_once(body)
            if data is not None:
                self.active_model = model
                if idx > 0:
                    self.last_failover_at = datetime.now(timezone.utc)
                    self.last_error = (
                        f"Model failover: {first_model} -> {model}. "
                        f"Root error: {(first_error or error or 'unknown')[:260]}"
                    )
                    self.last_error_code = first_error_code or error_code or "model_failover"
                    # Keep system stable after successful failover.
                    self.model = model
                    self.model_candidates = self._normalize_model_candidates([self.model, *self.model_candidates])
                    self._save_config()
                else:
                    self.last_error = None
                    self.last_error_code = None
                return data
            if idx == 0:
                first_error = error
                first_error_code = error_code

        self.active_model = first_model
        self.last_error = (first_error or "LLM request failed")[:300]
        self.last_error_code = first_error_code or "llm_request_failed"
        return None

    async def _post_json_once(
        self, request_body: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
        model = str(request_body.get("model") or "").strip()
        request_base_url = self._resolve_request_base_url(model=model)
        request_api_key = self._resolve_request_api_key(model=model, base_url=request_base_url)
        if not request_api_key:
            return None, f"[{model}] missing api key", "api_key_missing"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{request_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {request_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                if resp.status_code >= 400:
                    snippet = (resp.text or "").strip().replace("\n", " ")
                    code = "provider_http_error"
                    if resp.status_code in {401, 403}:
                        code = "auth_error"
                    elif resp.status_code == 429:
                        code = "rate_limit"
                    elif resp.status_code >= 500:
                        code = "provider_unavailable"
                    return None, f"[{model}] HTTP {resp.status_code}: {snippet[:220]}", code
                data = resp.json()
                if isinstance(data, dict):
                    return data, None, None
                return None, f"[{model}] invalid response payload", "invalid_payload"
        except Exception as exc:
            return None, f"[{model}] {str(exc)[:220]}", "connectivity_error"

    def _normalize_model_candidates(self, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(text)
        return out

    def _candidate_model_order(self, preferred: str) -> list[str]:
        fallback_defaults = ["deepseek-chat", "deepseek-reasoner"]
        return self._normalize_model_candidates([preferred, *self.model_candidates, *fallback_defaults])

    def _resolve_request_base_url(self, *, model: str) -> str:
        model_text = str(model or "").strip().lower()
        configured = str(self.base_url or "").strip().rstrip("/")
        configured_nvidia = str(self.nvidia_base_url or "").strip().rstrip("/")

        # NVIDIA catalog models are usually provider-prefixed, e.g. deepseek-ai/deepseek-r1.
        if self._is_nvidia_model(model_text):
            return configured_nvidia or self.default_nvidia_base_url

        # DeepSeek models should always hit DeepSeek-compatible endpoint.
        if model_text.startswith("deepseek"):
            if configured and "nvidia.com" not in configured.lower():
                return configured
            return self.default_base_url

        # Keep user-configured endpoint for unknown model families.
        if configured:
            return configured
        return self.default_base_url

    def _resolve_request_api_key(self, *, model: str, base_url: str) -> str:
        model_text = str(model or "").strip().lower()
        current_base_url = str(base_url or "").strip().lower()
        if "nvidia.com" in current_base_url:
            return self.nvidia_api_key
        if model_text.startswith("deepseek") and self.api_key:
            return self.api_key
        if self.api_key:
            return self.api_key
        if self.nvidia_api_key:
            return self.nvidia_api_key
        return ""

    def _is_nvidia_model(self, model_text: str) -> bool:
        if not model_text:
            return False
        if "/" in model_text:
            return True
        return model_text.startswith(
            (
                "meta/",
                "nvidia/",
                "mistralai/",
                "qwen/",
                "microsoft/",
                "google/",
            )
        )

    def _unavailable_reason(self) -> Optional[str]:
        if not self.enabled:
            return "api_key_missing"
        if self.last_error_code:
            return self.last_error_code
        return None

    def _next_step_prompt(self, *, with_history: bool) -> str:
        base = NEXT_STEP_SYSTEM_PROMPT_WITH_HISTORY if with_history else NEXT_STEP_SYSTEM_PROMPT
        if not self.batch_execution_enabled:
            return base
        return (
            f"{base}"
            "优先使用批量命令计划。"
            "若任务涉及多步命令（尤其配置任务），请优先返回commands数组，一次给出完整命令组，而不是多轮单条命令。"
            "若会话模式是config且需要变更配置，请优先返回commands数组。"
            "若包含配置变更，请先用只读命令采集当前状态，再给出配置命令组。"
            "不要在同一个commands数组里混合“状态采集命令”和“配置变更命令”。"
            "若用户未明确对象标识（如接口名），先返回发现对象的只读命令，不要猜测对象名。"
            "同一轮变更应聚焦一个已证实目标；其他潜在问题写入follow_up_action，不要并行下发。"
        )

    def _extract_content(self, data: dict[str, Any]) -> str:
        try:
            return str(data["choices"][0]["message"]["content"])
        except Exception:
            return ""

    def _parse_json_object(self, text: str) -> Optional[dict[str, Any]]:
        text = text.strip()
        if not text:
            return None

        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None

        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except Exception:
            return None

        return None

    def _to_incident_summary(self, session_id: str, result: dict[str, Any]) -> Optional[IncidentSummary]:
        root_cause = str(result.get("root_cause", "")).strip()
        impact_scope = str(result.get("impact_scope", "")).strip()
        recommendation = str(result.get("recommendation", "")).strip()

        if not root_cause or not impact_scope or not recommendation:
            return None

        confidence_raw = result.get("confidence")
        confidence: Optional[float]
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else None
        except Exception:
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        evidence_refs_raw = result.get("evidence_refs", [])
        evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []

        return IncidentSummary(
            session_id=session_id,
            root_cause=root_cause,
            impact_scope=impact_scope,
            recommendation=recommendation,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )
