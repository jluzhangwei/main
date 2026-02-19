#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Dict, Tuple

from app import llm_service
from app.state_store import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GPT_MODEL,
    DEFAULT_NVIDIA_MODEL,
)

DEFAULT_SYSTEM_PROMPT = "You are a senior network operations assistant. Be rigorous, evidence-based, and actionable."


class LLMAdapter:
    @staticmethod
    def model_used(llm: Dict[str, str]) -> str:
        provider = llm.get("provider", "chatgpt")
        if provider == "local":
            return llm.get("local_model", "")
        if provider == "deepseek":
            return llm.get("deepseek_model", "")
        if provider == "gemini":
            return llm.get("gemini_model", "")
        if provider == "nvidia":
            return llm.get("nvidia_model", "")
        return llm.get("chatgpt_model", "")

    @staticmethod
    def _normalize_usage(report_text: str, analysis_text: str, usage: Dict) -> Dict:
        raw = usage if isinstance(usage, dict) else {}
        try:
            p = int(raw.get("prompt_tokens", 0) or 0)
        except Exception:
            p = 0
        try:
            c = int(raw.get("completion_tokens", 0) or 0)
        except Exception:
            c = 0
        try:
            t = int(raw.get("total_tokens", 0) or 0)
        except Exception:
            t = 0
        if t <= 0:
            p = max(p, int(len(report_text or "") / 4))
            c = max(c, int(len(analysis_text or "") / 4))
            t = p + c
        return {
            "prompt_tokens": max(0, p),
            "completion_tokens": max(0, c),
            "total_tokens": max(0, t),
        }

    def run_analysis(self, llm: Dict[str, str], report_text: str) -> Tuple[str, Dict]:
        if llm["provider"] == "local":
            text, usage = llm_service.call_local_lmstudio_analysis(
                base_url=llm["local_base_url"],
                model=llm["local_model"],
                system_prompt=llm["system_prompt_text"],
                task_prompt=llm["task_prompt_text"],
                report_text=report_text,
            )
            return text, self._normalize_usage(report_text, text, usage)
        if llm["provider"] == "deepseek":
            if not llm["api_key"]:
                raise RuntimeError("DeepSeek API Key not set")
            text, usage = llm_service.call_deepseek_analysis(
                api_key=llm["api_key"],
                model=llm["deepseek_model"],
                system_prompt=llm["system_prompt_text"],
                task_prompt=llm["task_prompt_text"],
                report_text=report_text,
            )
            return text, self._normalize_usage(report_text, text, usage)
        if llm["provider"] == "gemini":
            if not llm["api_key"]:
                raise RuntimeError("Gemini API Key not set")
            text, usage = llm_service.call_gemini_analysis(
                api_key=llm["api_key"],
                model=llm["gemini_model"],
                system_prompt=llm["system_prompt_text"],
                task_prompt=llm["task_prompt_text"],
                report_text=report_text,
            )
            return text, self._normalize_usage(report_text, text, usage)
        if llm["provider"] == "nvidia":
            if not llm["api_key"]:
                raise RuntimeError("NVIDIA API Key not set")
            text, usage = llm_service.call_nvidia_analysis(
                api_key=llm["api_key"],
                model=llm["nvidia_model"],
                system_prompt=llm["system_prompt_text"],
                task_prompt=llm["task_prompt_text"],
                report_text=report_text,
            )
            return text, self._normalize_usage(report_text, text, usage)
        if not llm["api_key"]:
            raise RuntimeError("ChatGPT API Key not set")
        text, usage = llm_service.call_openai_analysis(
            api_key=llm["api_key"],
            system_prompt=llm["system_prompt_text"],
            task_prompt=llm["task_prompt_text"],
            report_text=report_text,
            model=llm["chatgpt_model"],
        )
        return text, self._normalize_usage(report_text, text, usage)

    def probe_cloud_token_balance(self, provider: str, api_key: str, model: str) -> Tuple[str, str]:
        p = (provider or "").strip().lower()
        probe_system = "You are a connectivity checker."
        probe_task = "Reply only with OK."
        probe_text = "ping"
        try:
            if p == "chatgpt":
                llm_service.call_openai_analysis(
                    api_key=api_key,
                    system_prompt=probe_system,
                    task_prompt=probe_task,
                    report_text=probe_text,
                    model=model or DEFAULT_GPT_MODEL,
                )
            elif p == "deepseek":
                llm_service.call_deepseek_analysis(
                    api_key=api_key,
                    system_prompt=probe_system,
                    task_prompt=probe_task,
                    report_text=probe_text,
                    model=model or DEFAULT_DEEPSEEK_MODEL,
                )
            elif p == "gemini":
                llm_service.call_gemini_analysis(
                    api_key=api_key,
                    system_prompt=probe_system,
                    task_prompt=probe_task,
                    report_text=probe_text,
                    model=model or DEFAULT_GEMINI_MODEL,
                )
            elif p == "nvidia":
                llm_service.call_nvidia_analysis(
                    api_key=api_key,
                    system_prompt=probe_system,
                    task_prompt=probe_task,
                    report_text=probe_text,
                    model=model or DEFAULT_NVIDIA_MODEL,
                )
            else:
                return "unknown", "未知（不支持的模型来源）"
            return "available", "可用"
        except Exception as exc:
            low = str(exc or "").lower()
            no_balance_hits = [
                "insufficient_quota",
                "billing_hard_limit_reached",
                "quota exceeded",
                "quota_exceeded",
                "credit balance is too low",
                "payment required",
                "余额不足",
                "欠费",
            ]
            if any(k in low for k in no_balance_hits):
                return "insufficient", "不足"
            return "unknown", f"未知（{str(exc)[:120]}）"
