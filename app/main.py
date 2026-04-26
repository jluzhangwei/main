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
    local_shared_dir = base_dir / "shared"
    legacy_shared_dir = base_dir.parent / "service_hub" / "shared"
    shared_dir = local_shared_dir if local_shared_dir.is_dir() else legacy_shared_dir
    static_dir = base_dir / "static"
    db_path = base_dir / "tasks.db"

    app = FastAPI(title="NetLog Extractor", version="1.0.0")
    def static_asset_url(relative_path: str) -> str:
        rel = str(relative_path or "").lstrip("/")
        target = static_dir / rel
        version = int(target.stat().st_mtime) if target.exists() else 0
        return f"/static/{rel}?v={version}"

    static_version = int(max(
        (path.stat().st_mtime for path in static_dir.rglob('*') if path.is_file()),
        default=0,
    ))
    db = TaskDB(db_path.as_posix())
    manager = TaskManager(db=db, output_root=output_dir.as_posix())
    ai_manager = AIAnalysisManager(output_root=output_dir.as_posix())
    initialize_default_prompt_files()
    app.state.task_manager = manager
    app.state.ai_manager = ai_manager
    app.state.static_version = static_version
    app.state.static_asset_url = static_asset_url

    app.include_router(page_router)
    app.include_router(api_router)
    app.include_router(ai_router)
    if shared_dir.is_dir():
        app.mount("/shared", StaticFiles(directory=shared_dir.as_posix()), name="shared")
    app.mount("/static", StaticFiles(directory=static_dir.as_posix()), name="static")
    app.mount("/output", StaticFiles(directory=output_dir.as_posix()), name="output")
    return app


app = create_app()
