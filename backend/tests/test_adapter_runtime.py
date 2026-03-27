from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import adapter_runtime


class _FakeAdapter:
    def __init__(self, *, fail_connect: bool = False):
        self.fail_connect = fail_connect
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self):
        self.connect_calls += 1
        if self.fail_connect:
            raise RuntimeError("connect failed")

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_ensure_connected_adapter_create_then_reuse(monkeypatch):
    created: list[_FakeAdapter] = []
    events: list[tuple[str, str]] = []

    def _build_adapter(_session, *, allow_simulation=True):
        adapter = _FakeAdapter()
        created.append(adapter)
        return adapter

    monkeypatch.setattr(adapter_runtime, "build_adapter", _build_adapter)
    session = SimpleNamespace(device=SimpleNamespace(host="192.0.2.10"))

    adapter, mode = await adapter_runtime.ensure_connected_adapter(
        None,
        session,
        on_create=lambda _adapter: events.append(("create", "create")),
        on_connect_success=lambda connect_mode, _adapter: events.append(("success", connect_mode)),
    )
    reused, reuse_mode = await adapter_runtime.ensure_connected_adapter(
        adapter,
        session,
        on_connect_success=lambda connect_mode, _adapter: events.append(("success", connect_mode)),
    )

    assert mode == "create"
    assert reuse_mode == "reuse"
    assert reused is adapter
    assert len(created) == 1
    assert adapter.connect_calls == 2
    assert events == [("create", "create"), ("success", "create"), ("success", "reuse")]


@pytest.mark.asyncio
async def test_ensure_connected_adapter_reports_failure(monkeypatch):
    failing = _FakeAdapter(fail_connect=True)
    errors: list[tuple[str, str]] = []

    def _build_adapter(_session, *, allow_simulation=True):
        return failing

    monkeypatch.setattr(adapter_runtime, "build_adapter", _build_adapter)
    session = SimpleNamespace(device=SimpleNamespace(host="192.0.2.20"))

    with pytest.raises(RuntimeError, match="connect failed"):
        await adapter_runtime.ensure_connected_adapter(
            None,
            session,
            on_connect_failure=lambda connect_mode, exc, _adapter: errors.append((connect_mode, str(exc))),
        )

    assert errors == [("create", "connect failed")]


@pytest.mark.asyncio
async def test_close_connected_adapter_calls_close_and_callback():
    adapter = _FakeAdapter()
    closed: list[str] = []

    await adapter_runtime.close_connected_adapter(
        adapter,
        on_close=lambda: closed.append("closed"),
    )

    assert adapter.close_calls == 1
    assert closed == ["closed"]
