from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.routers import netdiag as netdiag_router


class _FakeZabbixClient:
    def __init__(self, _cfg) -> None:
        pass

    def host_get(self, *, keyword: str = "", limit: int = 20, include_disabled: bool = True):
        _ = (keyword, limit, include_disabled)
        return [{"hostid": "10001", "host": "core-sw", "name": "core-sw", "status": "0"}]

    def item_get(
        self,
        *,
        hostids,
        key_filter: str = "",
        name_filter: str = "",
        value_type=None,
        limit: int = 20,
    ):
        _ = (hostids, key_filter, name_filter, value_type, limit)
        return [{"itemid": "30001", "hostid": "10001", "name": "CPU Util", "key_": "system.cpu.util"}]

    def history_get(self, *, itemids, time_from: int, time_till: int, value_type: int = 0, limit: int = 1000):
        _ = (itemids, time_from, time_till, value_type, limit)
        return [
            {"itemid": "30001", "clock": str(time_from + 60), "value": "32"},
            {"itemid": "30001", "clock": str(time_from + 120), "value": "91"},
        ]

    def trend_get(self, *, itemids, time_from: int, time_till: int, limit: int = 1000):
        _ = (itemids, time_from, time_till, limit)
        return []


def test_zabbix_history_endpoint_returns_signals(monkeypatch):
    monkeypatch.setattr(netdiag_router, "ZabbixClient", _FakeZabbixClient)
    c = TestClient(app)
    c.post(
        "/api/netdiag/zabbix/config",
        json={
            "base_url": "https://zbx.local",
            "api_token": "abc",
            "verify_ssl": False,
            "request_timeout_sec": 10,
        },
    ).raise_for_status()

    now = datetime.now()
    start = (now - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    end = now.replace(microsecond=0).isoformat()
    r = c.post(
        "/api/netdiag/zabbix/history",
        json={
            "host": "core-sw",
            "item_key": "system.cpu.util",
            "start_at": start,
            "end_at": end,
            "timezone": "Asia/Singapore",
            "limit": 100,
        },
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    assert payload["points_count"] == 2
    assert isinstance(payload.get("signals"), list)
    assert any(str(x.get("signal") or "") == "zabbix_high_resource_util" for x in payload.get("signals", []))
