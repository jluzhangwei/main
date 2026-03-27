from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from app.services.adapters import build_adapter


async def ensure_connected_adapter(
    existing_adapter: Any,
    session: Any,
    *,
    allow_simulation: bool = True,
    build_factory: Callable[..., Any] | None = None,
    on_create: Callable[[Any], Any] | None = None,
    on_connect_success: Callable[[str, Any], Any] | None = None,
    on_connect_failure: Callable[[str, Exception, Any], Any] | None = None,
) -> tuple[Any, str]:
    mode = "reuse" if existing_adapter is not None else "create"
    factory = build_factory or build_adapter
    adapter = existing_adapter if existing_adapter is not None else factory(session, allow_simulation=allow_simulation)

    if existing_adapter is None and on_create is not None:
        await _maybe_await(on_create(adapter))

    try:
        await adapter.connect()
    except Exception as exc:
        if on_connect_failure is not None:
            await _maybe_await(on_connect_failure(mode, exc, adapter))
        raise

    if on_connect_success is not None:
        await _maybe_await(on_connect_success(mode, adapter))

    return adapter, mode


async def close_connected_adapter(
    adapter: Any,
    *,
    on_close: Callable[[], Any] | None = None,
) -> None:
    await adapter.close()
    if on_close is not None:
        await _maybe_await(on_close())


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result
