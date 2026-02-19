from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .ai.analysis_manager import AIAnalysisManager
from .ai.prompt_store import initialize_default_prompt_files
from .db import TaskDB
from .routers.ai import router as ai_router
from .routers.api import router as api_router
from .routers.pages import router as page_router
from .task_manager import TaskManager


def create_app() -> FastAPI:
    base_dir = Path(__file__).resolve().parent.parent
    output_dir = base_dir / "output"
    db_path = base_dir / "tasks.db"

    app = FastAPI(title="NetLog Extractor", version="1.0.0")
    db = TaskDB(db_path.as_posix())
    manager = TaskManager(db=db, output_root=output_dir.as_posix())
    ai_manager = AIAnalysisManager(output_root=output_dir.as_posix())
    initialize_default_prompt_files()
    app.state.task_manager = manager
    app.state.ai_manager = ai_manager

    app.include_router(page_router)
    app.include_router(api_router)
    app.include_router(ai_router)
    app.mount("/output", StaticFiles(directory=output_dir.as_posix()), name="output")
    return app


app = create_app()
