from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .ai.analysis_manager import AIAnalysisManager
from .ai.prompt_store import initialize_default_prompt_files
from .db import TaskDB
from .diagnosis import DiagnosisSessionManager
from .diagnosis.case_store import NetdiagCaseStore
from .diagnosis.config_store import NetdiagConfigStore
from .diagnosis.duel_store import NetdiagDuelStore
from .diagnosis.known_issue_store import NetdiagKnownIssueStore
from .diagnosis.learning_store import NetdiagLearningStore
from .diagnosis.state_store import NetdiagStateStore
from .integrations.connection_store import NetdiagConnectionStore
from .integrations.zabbix_store import NetdiagZabbixStore
from .routers.ai import router as ai_router
from .routers.api import router as api_router
from .routers.netdiag import router as netdiag_router
from .routers.pages import router as page_router
from .task_manager import TaskManager


def create_app() -> FastAPI:
    base_dir = Path(__file__).resolve().parent.parent
    output_dir = base_dir / "output"
    shared_dir = base_dir.parent / "service_hub" / "shared"
    static_dir = base_dir / "static"
    db_path = base_dir / "tasks.db"

    app = FastAPI(title="NetDiag", version="1.0.0")
    db = TaskDB(db_path.as_posix())
    manager = TaskManager(db=db, output_root=output_dir.as_posix())
    ai_manager = AIAnalysisManager(output_root=output_dir.as_posix())
    diag_session_manager = DiagnosisSessionManager(output_root=(output_dir / "netdiag_sessions").as_posix())
    learning_store = NetdiagLearningStore((base_dir / "state" / "netdiag_learning.json").as_posix())
    known_issue_store = NetdiagKnownIssueStore((base_dir / "state" / "netdiag_known_issues.json").as_posix())
    case_store = NetdiagCaseStore((base_dir / "state" / "netdiag_cases.json").as_posix())
    state_store = NetdiagStateStore((base_dir / "state" / "netdiag_device_state.json").as_posix())
    config_store = NetdiagConfigStore((base_dir / "state" / "netdiag_config_history.json").as_posix())
    duel_store = NetdiagDuelStore((base_dir / "state" / "netdiag_duels.json").as_posix())
    zabbix_store = NetdiagZabbixStore((base_dir / "state" / "netdiag_zabbix.json").as_posix())
    connection_store = NetdiagConnectionStore((base_dir / "state" / "netdiag_connection.json").as_posix())
    initialize_default_prompt_files()
    app.state.task_manager = manager
    app.state.ai_manager = ai_manager
    app.state.diag_session_manager = diag_session_manager
    app.state.learning_store = learning_store
    app.state.known_issue_store = known_issue_store
    app.state.case_store = case_store
    app.state.state_store = state_store
    app.state.config_store = config_store
    app.state.duel_store = duel_store
    app.state.zabbix_store = zabbix_store
    app.state.connection_store = connection_store

    app.include_router(page_router)
    app.include_router(api_router)
    app.include_router(ai_router)
    app.include_router(netdiag_router)
    if shared_dir.is_dir():
        app.mount("/shared", StaticFiles(directory=shared_dir.as_posix()), name="shared")
    app.mount("/static", StaticFiles(directory=static_dir.as_posix()), name="static")
    app.mount("/output", StaticFiles(directory=output_dir.as_posix()), name="output")
    return app


app = create_app()
