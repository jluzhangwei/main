from app.integrations.zabbix_client import ZabbixClient, ZabbixConfig


class _StubClient(ZabbixClient):
    def __init__(self, config: ZabbixConfig) -> None:
        super().__init__(config)
        self.calls: list[tuple[dict, dict]] = []

    def _post_json(self, body, headers=None):  # type: ignore[override]
        h = dict(headers or {})
        self.calls.append((dict(body), h))
        method = str(body.get("method") or "")
        if method == "apiinfo.version":
            return {"jsonrpc": "2.0", "result": "7.0.0", "id": body.get("id")}
        if method == "user.login":
            return {"jsonrpc": "2.0", "result": "auth-token", "id": body.get("id")}
        if method == "host.get":
            return {"jsonrpc": "2.0", "result": [{"hostid": "10001", "host": "core-sw"}], "id": body.get("id")}
        return {"jsonrpc": "2.0", "result": [], "id": body.get("id")}


def test_api_url_normalization():
    cfg = ZabbixConfig(base_url="https://zbx.local/zabbix")
    assert cfg.api_url() == "https://zbx.local/zabbix/api_jsonrpc.php"
    cfg2 = ZabbixConfig(base_url="https://zbx.local/api_jsonrpc.php")
    assert cfg2.api_url() == "https://zbx.local/api_jsonrpc.php"


def test_ping_uses_bearer_when_api_token():
    cli = _StubClient(
        ZabbixConfig(
            base_url="https://zbx.local",
            api_token="token-1",
            verify_ssl=False,
        )
    )
    out = cli.ping()
    assert out["version"] == "7.0.0"
    methods = [c[0].get("method") for c in cli.calls]
    assert "user.login" not in methods
    host_call = next(c for c in cli.calls if c[0].get("method") == "host.get")
    assert str(host_call[1].get("Authorization") or "").startswith("Bearer ")


def test_ping_uses_user_login_when_no_api_token():
    cli = _StubClient(
        ZabbixConfig(
            base_url="https://zbx.local",
            username="api-user",
            password="secret",
            verify_ssl=False,
        )
    )
    out = cli.ping()
    assert out["sample_hosts"] == 1
    methods = [c[0].get("method") for c in cli.calls]
    assert "user.login" in methods
    host_call = next(c for c in cli.calls if c[0].get("method") == "host.get")
    assert str(host_call[0].get("auth") or "") == "auth-token"
