from fastapi.testclient import TestClient

from app.main import app


def test_intent_parse_supports_english_month_time():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "172.20.192.179 故障时间 Mar 4 2026 10:07:54，端口震荡，重点检查: MSTP",
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    parsed = data.get("parsed", {})
    assert parsed.get("device_ip") == "172.20.192.179"
    assert str(parsed.get("fault_end") or "").startswith("2026-03-04T10:07:54")
    assert str(parsed.get("fault_start") or "").startswith("2026-03-04T09:37:54")
    assert "device_ip" not in (data.get("missing") or [])
    assert "fault_start" not in (data.get("missing") or [])
    assert "fault_end" not in (data.get("missing") or [])


def test_intent_parse_missing_fields_reported():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "故障时间 2026-03-04 10:07:54，端口抖动",
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    parsed = data.get("parsed", {})
    assert str(parsed.get("fault_start") or "").startswith("2026-03-04T09:37:54")
    assert str(parsed.get("fault_end") or "").startswith("2026-03-04T10:07:54")
    assert "device_ip" in (data.get("missing") or [])


def test_intent_parse_focus_goals_dedupe_repeated_long_phrase():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": (
                "问题：帮我诊断 192.168.0.88 设备重启问题。"
                "方向：确认设备是否发生重启 | 定位重启原因 | 检查重启对业务的影响 | "
                "确认设备是否发生重启定位重启原因检查重启对业务的影响"
            ),
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    goals = [str(x) for x in ((data.get("parsed") or {}).get("focus_goals") or [])]
    assert "确认设备是否发生重启" in goals
    assert "定位重启原因" in goals
    assert "检查重启对业务的影响" in goals
    assert all("确认设备是否发生重启定位重启原因" not in g for g in goals)


def test_intent_parse_question_not_polluted_by_direction_block():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": (
                "问题\n"
                "帮我诊断 192.168.0.88 设备重启问题\n"
                "方向\n"
                "确认设备是否发生重启 | 定位重启原因 | 检查重启对业务的影响"
            ),
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    question = str(((data.get("parsed") or {}).get("question") or "")).strip()
    assert question == "帮我诊断 192.168.0.88 设备重启问题"


def test_intent_parse_supports_yesterday_to_today_window():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "设备 192.168.0.88 端口 down 问题检测，发生时间是昨天到今天",
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    parsed = data.get("parsed", {})
    assert parsed.get("device_ip") == "192.168.0.88"
    assert str(parsed.get("fault_start") or "").strip()
    assert str(parsed.get("fault_end") or "").strip()
    assert "fault_start" not in (data.get("missing") or [])
    assert "fault_end" not in (data.get("missing") or [])


def test_intent_parse_supports_fullwidth_ip_punctuation():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "设备 192。168。0。88 端口 down，时间昨天到今天，帮我定位原因",
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    parsed = data.get("parsed", {})
    assert parsed.get("device_ip") == "192.168.0.88"
    assert str(parsed.get("fault_start") or "").strip()
    assert str(parsed.get("fault_end") or "").strip()


def test_intent_parse_supports_day_before_yesterday_window():
    c = TestClient(app)
    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "时间窗是 前天吧",
            "timezone": "Asia/Singapore",
            "use_llm": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    parsed = data.get("parsed", {})
    assert str(parsed.get("fault_start") or "").strip()
    assert str(parsed.get("fault_end") or "").strip()
    assert "fault_start" not in (data.get("missing") or [])
    assert "fault_end" not in (data.get("missing") or [])


def test_intent_parse_skips_llm_when_selected_provider_unavailable(monkeypatch):
    c = TestClient(app)
    called = {"llm": 0}

    async def _should_not_call(*_args, **_kwargs):
        called["llm"] += 1
        raise AssertionError("_llm_intent_parse should be skipped when provider is unavailable")

    monkeypatch.setattr("app.routers.netdiag._llm_intent_parse", _should_not_call)
    monkeypatch.setattr(
        "app.routers.netdiag.load_gpt_config",
        lambda: {
            "provider": "chatgpt",
            "chatgpt_api_key": "",
            "chatgpt_model": "gpt-4.1-mini",
            "selected_system_prompt": "sys",
            "selected_task_prompt": "task",
        },
    )
    monkeypatch.setattr("app.routers.netdiag.merged_system_prompt_catalog", lambda: {"sys": "SYS"})
    monkeypatch.setattr("app.routers.netdiag.merged_task_prompt_catalog", lambda: {"task": "TASK"})

    resp = c.post(
        "/api/netdiag/intent/parse",
        json={
            "text": "设备 192.168.0.88 端口 down 问题检测，发生时间是昨天到今天",
            "timezone": "Asia/Singapore",
            "use_llm": True,
            "llm_route": {"primary": {"provider": "chatgpt", "model": "gpt-4.1-mini"}},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert called["llm"] == 0
    assert str(((data.get("llm") or {}).get("error") or "")).find("API Key not set") >= 0
    assert str(data.get("source") or "").startswith("heuristic")
