from app.integrations.zabbix_store import NetdiagZabbixStore


def test_zabbix_store_update_and_mask(tmp_path):
    store = NetdiagZabbixStore((tmp_path / "state" / "zabbix.json").as_posix())
    row = store.update(
        {
            "base_url": "https://zabbix.example.com",
            "username": "api-user",
            "password": "secret",
            "api_token": "token-x",
            "verify_ssl": False,
            "request_timeout_sec": 44,
        }
    )
    assert row["base_url"] == "https://zabbix.example.com"
    assert row["verify_ssl"] is False
    assert row["request_timeout_sec"] == 44
    assert row["password"] == "***"
    assert row["api_token"] == "***"
    assert row["has_password"] is True
    assert row["has_api_token"] is True

    plain = store.get(masked=False)
    assert plain["password"] == "secret"
    assert plain["api_token"] == "token-x"
