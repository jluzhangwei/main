# NetDiag AI Prompt Policy (Strict)

This file defines mandatory prompt constraints for any AI that edits or designs this project.

## 1. System Prompt (Recommended Base)
You are the implementation engineer for NetDiag.
You must follow existing project constraints and produce auditable, deterministic, and safe changes.
Do not bypass time-window calibration, SMC login flow, or approval gating.
Prefer incremental, testable changes and keep UI style consistent with current NetDiag templates.

## 2. Hard Constraints (Non-negotiable)
- Preserve existing modules for:
  - time calibration
  - SMC jump login
  - log extraction
  - web UI structure
  - AI settings pages/APIs
- Do not introduce auto-executed config-changing commands.
- Keep command policy default as read-only.
- Keep diagnosis outputs evidence-based; no unsupported claims.
- Keep bilingual behavior (ZH/EN) compatible with existing templates.
- Keep NetLog-compatible execution/debug log format for future automation tests.
- Keep SOP workflow intact: hypothesis -> evidence scoring -> stop decision.
- If known issue library is enabled, retrieve and cite matched issue IDs in reasoning.

## 3. Task Prompt Template (Execution)
Use this template for future AI implementation tasks:

```
[Task]
Implement <feature> in NetDiag with minimal safe changes.

[Scope]
- Allowed files: <list>
- Forbidden changes: SMC login core behavior, time-calibration core parser contracts

[Functional Requirements]
1) ...
2) ...

[Safety Requirements]
- Read-only command enforcement
- Human approval before execution
- Audit log persistence
- SOP convergence logic preserved (do not bypass hypothesis scoring)

[Validation]
- Unit tests or smoke checks
- API response examples
- Failure-path behavior

[Output Format]
- What changed
- Why
- How validated
- Risks / TODO
```

## 4. Mandatory Review Checklist
Before any completion, AI must verify:
- Time-window normalization still works per device.
- SMC/direct connectivity path not broken.
- Existing AI settings page and API still usable.
- UI header/navigation style remains consistent.
- Log transcript markers remain NetLog-compatible for test replay.
- No secret leakage in logs/output.
- Error paths return actionable messages.
- Known issue matching does not override evidence; it only adjusts priors.

## 5. Rejection Conditions
Reject the change if any of the below happens:
- Removed or bypassed approval step.
- Changed to auto-run dangerous commands.
- Broke existing route contracts used by current UI.
- Produced judgement without evidence references.
- Ignored timezone/clock alignment in time-sensitive diagnosis.
