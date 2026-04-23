# NetLog Extractor Refactor and Optimization Plan

## Goal
Refactor the web/UI and AI analysis code to improve maintainability, workflow clarity, and UI quality without reducing collection or analysis accuracy, and without introducing measurable performance regressions.

## Guardrails
- Preserve device collection and AI analysis behavior unless explicitly improved.
- Keep EN/ZH behavior stable and deterministic.
- Preserve current URLs and API contracts where possible.
- Every phase must pass compile/app boot checks before moving on.
- Do not mix environment fixes with structural refactors in the same step.

## Phases

### Phase 1: Low-Risk Frontend Asset Extraction
Goal: reduce template size and inline script/style coupling.
- Extract shared `base.html` CSS/JS into `/static/css/base.css` and `/static/js/base.js`.
- Extract `index.html` CSS/JS into `/static/css/index.css` and `/static/js/index.js`.
- Keep server-rendered markup unchanged.
- Add a safe page config bootstrap pattern for template-driven JS.

Validation:
- `python3 -m compileall app`
- `create_app()` boot check
- template load smoke checks

### Phase 2: AI Analysis Workspace Extraction
Goal: make the AI analysis page maintainable without changing behavior.
- Move `ai_settings.html` CSS/JS into dedicated static files.
- Keep prompt maps/device JSON embedded as data blocks only.
- Centralize page config in one object.
- Verify: prompt review modal, provider switching, preview, precheck, start/stop, history restore, EN/ZH.

Validation:
- compile/app boot
- rendered template smoke check
- targeted JS syntax sanity by loading the page in browser-compatible structure

### Phase 3: Task Detail / Debug Console Extraction
Goal: isolate the most complex interactive page.
- Move `task_detail.html` CSS/JS into dedicated static files.
- Preserve debug console behavior: device list, keyword search, time filter, category navigation, fullscreen, downloads.
- Keep page-local JSON bootstrap only.

Validation:
- compile/app boot
- targeted template render check

### Phase 4: Backend AI Structure Cleanup
Goal: reduce analysis orchestration complexity without altering analysis correctness.
- First extraction boundary: prompt/language assembly from `analysis_manager.py` into a helper module.
- Keep call graph and persisted output unchanged.
- Do not refactor execution flow and concurrency in the same pass.

Validation:
- compile/app boot
- smoke test `_build_llm_input()` and preview generation

### Phase 5: Regression and UX Audit
Goal: confirm no regression in correctness or workflow efficiency.
- Re-run compile and boot checks.
- Run targeted smoke checks on preview/build paths.
- Summarize remaining backlog separately from completed work.

## Non-Goals For This Pass
- No framework migration.
- No API redesign.
- No database migration.
- No visual redesign that risks breaking current user workflows.
