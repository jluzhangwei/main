# NetDiag 10h Real-Device LongRun (192.168.0.88)

## Goal
Run continuous regression for the real alarm scenario and require end-to-end diagnosis convergence:

- Device: `192.168.0.88` (direct SSH)
- Fault window: after `2026-03-10 20:30` (`Asia/Singapore`)
- Target alarm: `Ethernet1/0/6`
- Expected root cause: interface was administratively shutdown (`shutdown` + `commit`)

## Acceptance Criteria
Each iteration is marked `passed=true` only when all checks are true:

1. `round.evidence_signals` contains shutdown-related signal:
   - `huawei_interface_shutdown_event` and/or `huawei_interface_admin_down`
2. `round.analysis_result` contains shutdown/admin-down evidence text.
3. `round.stop_decision.recommend_conclude == true`.
4. Analysis output does not drift to lab noise (`[LAB]`, `BGP neighbor down injection`).

## Workflow Per Iteration
One iteration strictly executes:

1. `POST /api/netdiag/sessions`
2. `POST /api/netdiag/sessions/{sid}/baseline_collect`
3. `POST /api/netdiag/sessions/{sid}/rounds/plan`
4. `POST /api/netdiag/sessions/{sid}/rounds/{round_no}/approve`
5. `POST /api/netdiag/sessions/{sid}/rounds/{round_no}/execute`
6. `POST /api/netdiag/sessions/{sid}/rounds/{round_no}/analyze`
7. If recommended: `POST /api/netdiag/sessions/{sid}/conclude`

All commands stay read-only (`show/display/dis`) through existing policy checks.

## Run Command (10h)
```bash
cd /Users/zhangwei/python/netdiag
./.venv/bin/python scripts/netdiag_longrun_test.py \
  --hours 10 \
  --interval-sec 120 \
  --device-ip 192.168.0.88 \
  --username zhangwei \
  --password '***' \
  --fault-start 2026-03-10T20:30:00 \
  --fault-end 2026-03-10T23:59:59 \
  --timezone Asia/Singapore \
  --output-dir /Users/zhangwei/python/netdiag/output/longrun/longrun_192168088_20260310
```

## Artifacts
Under the chosen output directory:

- `runtime.log`: rolling runtime status
- `iterations.jsonl`: one JSON record per iteration
- `summary.json`: counters/pass-rate/recent failures snapshot

## Notes on Improvements Applied
During this validation cycle, the following improvements were implemented and verified:

1. Direct SSH connector switched to persistent interactive shell (PTY), fixing Huawei direct-login command channel drops.
2. Evidence parser enhanced with **fact-layer** signals (vendor parsers feed neutral facts; AI does final root-cause judgement):
   - `interface_admin_shutdown_event` (generic)
   - `interface_admin_down_present` (generic)
   - plus vendor detail signals for explainability
3. Analyzer no longer hard-forces convergence by vendor-specific shortcut rules; final convergence is decided by AI + SOP score/gap.
4. Case-library priors were down-weighted to prevent overriding direct command evidence.
5. Baseline summary now includes calibrated `device_window` for stable time-window reasoning.
6. Time reasoning prompt now enforces: if key events are in `device_window`, treat window as covered.

Historical compatibility signals retained:
   - `huawei_interface_shutdown_event`
   - `huawei_shutdown_recovery_flap_pattern`
   - `huawei_interface_admin_down`
