# Diagnosis Scaffold

This package is the NetDiag orchestration scaffold for command-driven diagnosis.

## Files
- `models.py`: session/round/command data contracts.
- `policy.py`: read-only command safety checks.
- `session_manager.py`: in-memory session manager (phase-1 scaffold).
- `state_store.py`: device state history + baseline compare.
- `config_store.py`: config snapshot history + diff.
- `case_store.py`: reusable fault case library for planning/analyze priors.
- `duel_store.py`: lab duel sessions (simulated fault plan + judge scoring + case promotion).

## Next Phase
- Add persistent storage.
- Bind round execution with device connectors.
- Add AI planner/judge integration with approval gating.
- Keep netlog-compatible logs for regression testing.
