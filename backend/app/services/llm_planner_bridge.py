from __future__ import annotations

import asyncio
import inspect
from typing import Any


class LLMPlannerBridge:
    def _supported_call_kwargs(self, func, **kwargs) -> dict[str, Any]:
        filtered = {key: value for key, value in kwargs.items() if value is not None}
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return filtered
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return filtered
        return {key: value for key, value in filtered.items() if key in signature.parameters}

    async def propose_next_step_with_debug(
        self,
        diagnoser,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        conversation_history=None,
        planner_context: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        base_debug: dict[str, Any] = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "with_history": bool(conversation_history),
        }

        async def _call() -> tuple[dict[str, Any] | None, dict[str, Any]]:
            if hasattr(diagnoser, "propose_next_step_with_debug"):
                kwargs = self._supported_call_kwargs(
                    diagnoser.propose_next_step_with_debug,
                    session=session,
                    user_problem=user_problem,
                    commands=commands,
                    evidences=evidences,
                    iteration=iteration,
                    max_iterations=max_iterations,
                    conversation_history=conversation_history,
                    planner_context=planner_context,
                )
                return await diagnoser.propose_next_step_with_debug(**kwargs)

            kwargs = self._supported_call_kwargs(
                diagnoser.propose_next_step,
                session=session,
                user_problem=user_problem,
                commands=commands,
                evidences=evidences,
                iteration=iteration,
                max_iterations=max_iterations,
                conversation_history=conversation_history,
                planner_context=planner_context,
            )
            plan = await diagnoser.propose_next_step(**kwargs)
            debug: dict[str, Any] = {
                "iteration": iteration,
                "max_iterations": max_iterations,
                "with_history": bool(conversation_history),
                "compat_mode": "legacy_propose_next_step",
                "parsed_response": plan,
            }
            if planner_context is not None:
                debug["planner_context"] = planner_context
            return plan, debug

        if timeout_seconds is not None and timeout_seconds > 0:
            try:
                return await asyncio.wait_for(_call(), timeout=float(timeout_seconds))
            except TimeoutError:
                debug = dict(base_debug)
                debug["error"] = "llm_timeout"
                debug["timeout_seconds"] = float(timeout_seconds)
                if planner_context is not None:
                    debug["planner_context"] = planner_context
                return None, debug

        return await _call()
