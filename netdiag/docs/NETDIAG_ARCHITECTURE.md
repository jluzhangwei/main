# NetDiag Overall Architecture

## 1. Product Positioning
NetDiag is a command-driven network diagnosis system with time-window validation.
It keeps the proven NetLog capabilities and adds AI-guided, human-approved multi-round diagnosis.

### Retained Baseline (must keep)
- Device clock calibration and time-window normalization.
- SMC jump-host login flow and direct SSH flow.
- Log extraction and timeline-based evidence filtering.
- Existing Web UI style and navigation structure.
- Existing AI settings and prompt/template management.
- Log/debug transcript format compatibility with NetLog for automated testing.

## 2. Core Workflow (Strict)
1. User submits diagnosis question + fault time window + target devices.
2. System calibrates device clocks and computes per-device window offsets.
3. System collects baseline evidence (logs + key read-only commands).
4. AI generates first-round diagnosis plan with commands and rationale.
5. User approves/rejects the plan.
6. If approved, system executes commands and parses facts.
7. AI updates judgement, confidence, and next actions.
8. SOP engine scores hypotheses and computes stop/continue decision.
9. If LLM fails/timeout, deterministic fallback analyzer must still output evidence-based judgement draft.
10. Repeat rounds until user ends session or judgement converges.

## 3. Time Rules
- Fault window must be stored as absolute time with timezone.
- Every evidence record must include:
  - raw_device_time
  - normalized_utc_time
  - session_timezone_time
  - in_fault_window (bool)
- If device/NTP time confidence is low, AI must explicitly lower confidence and request remediation.

## 4. Safety Rules
- Default command scope is read-only (`show`/`display`).
- Any non-read command must be blocked by policy unless explicitly enabled later.
- Every execution requires approval in current phase.
- Full audit chain is mandatory: question -> plan -> approval -> command -> output -> facts -> judgement.

## 5. Functional Modules
- `app/connectors`: direct SSH + SMC login and command execution.
- `app/services/device_service.py`: time calibration and log extraction.
- `app/ai`: provider settings, prompt management, connection tests, analysis persistence.
- `app/diagnosis`: session/round/plan policy orchestration.
- `app/diagnosis/sop_engine.py`: hypothesis seeding/scoring, SOP step generation, convergence decision.
- `app/diagnosis/evidence_parser.py`: vendor-aware structured evidence extraction and signal scoring input.
- `app/diagnosis/learning_store.py`: command library + command execution learning loop.
- `app/diagnosis/known_issue_store.py`: known issue knowledge base and retrieval.
- `app/integrations/zabbix_*`: Zabbix config store + JSON-RPC reader for historical metric review.
- `app/routers`: page/api/ai plus netdiag diagnosis APIs (new scaffold).

## 6. State Machine
- `draft`
- `calibrating_time`
- `planning`
- `waiting_approval`
- `executing`
- `analyzing`
- `need_next_round`
- `concluded`
- `failed`
- `aborted`

## 7. Deliverable Contract
For each round, output must contain:
- candidate_judgement
- confidence_score
- supporting_facts
- recommended_next_commands
- recommended_next_actions
- stop_or_continue_decision
- hypotheses (ranked)
- known_issue_hits (if any)
- known_issue_hit_explainability (`match_reasons/matched_terms/matched_patterns/explain`)
- evidence_overview (`domain_delta/command_health/summary_lines`)
- retrospective (command success + score delta)

## 8. Test Compatibility Rule
- Automated tests must validate against NetLog-compatible log patterns.
- Do not change existing `[LOGIN]/[CMD]/[OUT]/[SEND]/[RECV]/[FLOW]/[ERROR]` transcript semantics without explicit migration plan.
