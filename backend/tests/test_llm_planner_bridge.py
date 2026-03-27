from __future__ import annotations

import pytest

from app.services.llm_planner_bridge import LLMPlannerBridge


class LegacyDiagnoser:
    async def propose_next_step(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
    ):
        return {
            "decision": "run_command",
            "title": "legacy-plan",
            "command": "show version",
            "reason": user_problem,
        }


class ModernDiagnoser:
    async def propose_next_step_with_debug(
        self,
        *,
        session,
        user_problem: str,
        commands,
        evidences,
        iteration: int,
        max_iterations: int,
        planner_context: str | None = None,
    ):
        plan = {
            "decision": "run_command",
            "title": "modern-plan",
            "command": "display version",
            "reason": user_problem,
        }
        debug = {
            "iteration": iteration,
            "max_iterations": max_iterations,
            "planner_context": planner_context,
            "parsed_response": plan,
        }
        return plan, debug


@pytest.mark.asyncio
async def test_llm_planner_bridge_supports_legacy_diagnoser_without_debug_method():
    bridge = LLMPlannerBridge()
    plan, debug = await bridge.propose_next_step_with_debug(
        LegacyDiagnoser(),
        session=object(),
        user_problem="检查版本",
        commands=[],
        evidences=[],
        iteration=1,
        max_iterations=3,
        planner_context="sop-context",
    )
    assert plan is not None
    assert plan["command"] == "show version"
    assert debug["compat_mode"] == "legacy_propose_next_step"
    assert debug["planner_context"] == "sop-context"


@pytest.mark.asyncio
async def test_llm_planner_bridge_prefers_native_debug_method_when_available():
    bridge = LLMPlannerBridge()
    plan, debug = await bridge.propose_next_step_with_debug(
        ModernDiagnoser(),
        session=object(),
        user_problem="检查版本",
        commands=[],
        evidences=[],
        iteration=2,
        max_iterations=4,
        planner_context="sop-context",
    )
    assert plan is not None
    assert plan["command"] == "display version"
    assert debug["planner_context"] == "sop-context"
    assert debug["parsed_response"]["title"] == "modern-plan"
