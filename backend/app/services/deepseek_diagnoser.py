from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

from app.models.schemas import CommandExecution, Evidence, IncidentSummary, Session


class DeepSeekDiagnoser:
    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

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
        if not self.enabled:
            return None

        if conversation_history:
            content = await self._chat_completion_messages(
                system_prompt=(
                    "你是网络故障诊断代理。"
                    "你正在同一会话内连续对话，必须结合已有上下文。"
                    "你无法访问其他会话，禁止引用其他会话的信息。"
                    "你的任务是决定下一步动作。"
                    "只输出JSON对象。"
                    "字段: decision, title, command, reason, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
                    "decision只能是run_command或final。"
                    "run_command时必须给出command。"
                    "final时如果是查询任务，mode=query且必须给出query_result，可选follow_up_action；"
                    "final时如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
                    "confidence是0到1。evidence_refs是数组，quote应来自会话中的证据输出。"
                ),
                messages=conversation_history,
            )
            if not content:
                return None
            parsed = self._parse_json_object(content)
            if not parsed:
                return None
            decision = str(parsed.get("decision", "")).strip().lower()
            if decision not in {"run_command", "final"}:
                return None
            parsed["decision"] = decision
            return parsed

        payload = {
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

        content = await self._chat_completion(
            system_prompt=(
                "你是网络故障诊断代理。"
                "任务是基于用户问题和已有证据，决定下一步动作。"
                "你可以自由决定诊断路径，不使用固定剧本。"
                "只输出JSON对象。"
                "字段: decision, title, command, reason, mode, query_result, follow_up_action, root_cause, impact_scope, recommendation, confidence, evidence_refs。"
                "decision只能是run_command或final。"
                "当decision为run_command时，必须给出command；优先只读排查命令。"
                "当decision为final时，如果是查询任务，mode=query且必须给出query_result；"
                "当decision为final时，如果是诊断任务，mode=diagnosis且必须给出root_cause, impact_scope, recommendation。"
                "confidence是0到1。evidence_refs是数组，且quote必须来自已有证据原文。"
            ),
            user_payload=payload,
        )
        if not content:
            return None
        parsed = self._parse_json_object(content)
        if not parsed:
            return None

        decision = str(parsed.get("decision", "")).strip().lower()
        if decision not in {"run_command", "final"}:
            return None
        parsed["decision"] = decision

        return parsed

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

    async def _run_primary(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        content = await self._chat_completion(
            system_prompt=(
                "你是网络故障诊断引擎。"
                "严格依据输入证据判断，不得猜测。"
                "若证据不足以确认根因，必须明确说明不确定。"
                "只输出JSON对象。"
                "字段必须是: root_cause, impact_scope, recommendation, confidence, evidence_refs。"
                "confidence是0到1的小数。"
                "evidence_refs是数组，每项包含 command_step, quote, why。quote必须是输入证据中的原文片段。"
            ),
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
            system_prompt=(
                "你是网络诊断结果审稿器。"
                "只能依据证据审查，不得引入新事实。"
                "如果candidate缺证据支撑，给出fail并可附corrected_summary。"
                "只输出JSON对象。"
            ),
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
            system_prompt=(
                "你是网络诊断改写器。"
                "只依据证据，修正不被支持的结论。"
                "只输出JSON对象。"
            ),
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
        history = []
        for item in messages:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            history.append({"role": role, "content": content})

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

    async def _post_json(self, request_body: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            return None
        return None

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
