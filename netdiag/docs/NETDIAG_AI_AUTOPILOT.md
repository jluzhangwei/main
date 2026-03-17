# NetDiag AI Autopilot (Auto Profile -> Auto Command)

## Goal
Do not rely on user-side vendor selection.  
AI should diagnose by:
1. discovering device profile,
2. selecting diagnostic intents,
3. mapping intents to executable read-only commands,
4. executing and iterating by evidence.

## Runtime Flow
1. Baseline collection identifies `vendor/os_family/model/version` and time offset.
2. System seeds SOP hypotheses from question/focus goals and known-issue hits.
3. Planner LLM receives device profiles, intent catalog, SOP hypotheses, and known-issue hints.
4. LLM outputs `steps` (intent list) instead of free-form command text.
5. Server maps intent -> command by profile, preferring command library rules.
6. `Focus Lock` goals are injected into planner/analyzer prompts every round.
7. Command policy validates read-only and removes placeholder commands.
8. Analyzer scores hypotheses by evidence and computes `stop_decision` + `retrospective`.
9. If LLM output times out, server retries with reduced context payload.
10. If LLM output is invalid/timeout after retries, server applies deterministic SOP/profile fallback plan.

## Why this works
- Keeps AI adaptive, but execution deterministic.
- Prevents placeholder/hallucinated commands from reaching devices.
- Preserves multi-vendor correctness (Cisco/Huawei/Arista/Palo Alto).
- Preserves time-window diagnosis with calibrated offsets.
- Uses field-proven troubleshooting style: hypothesis tree + evidence scoring + convergence rules.
- Allows known production issues to be reused as machine-readable diagnosis priors.

## Current supported profiles
- `cisco_ios`, `cisco_iosxe`, `cisco_iosxr`, `cisco_nxos`
- `huawei_vrp`
- `arista_eos`
- `paloalto_panos`
- `unknown` (safe fallback)

## Notes
- `vendor_hint` remains optional seed; baseline detection is authoritative.
- All execution stays read-only (`show/display/dis`).
- `Focus Lock` goals persist across rounds and each round records coverage review (`covered/uncovered/ratio`).
- Each round now stores `hypotheses`, `known_issue_hits`, `stop_decision`, and `retrospective`.
