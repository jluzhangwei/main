from __future__ import annotations

import pytest

from app.api import routes


@pytest.fixture(autouse=True)
def reset_global_store():
    routes.store.sessions.clear()
    routes.store.messages.clear()
    routes.store.commands.clear()
    routes.store.evidences.clear()
    routes.store.summary.clear()
    routes.store.ai_context.clear()
    yield
