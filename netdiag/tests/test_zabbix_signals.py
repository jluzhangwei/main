from app.routers.netdiag import _build_zabbix_signals


def test_build_zabbix_signals_resource_high():
    signals, summary = _build_zabbix_signals(
        items=[{"itemid": "1001", "name": "CPU", "key_": "system.cpu.util"}],
        points=[
            {"itemid": "1001", "value": "61.2"},
            {"itemid": "1001", "value": "92.4"},
        ],
        use_trend=False,
    )
    assert any(str(s.get("signal") or "") == "zabbix_high_resource_util" for s in signals)
    assert any(str(s.get("domain") or "") == "resource" for s in signals)
    assert len(summary) >= 1


def test_build_zabbix_signals_link_loss():
    signals, _summary = _build_zabbix_signals(
        items=[{"itemid": "2001", "name": "Loss", "key_": "net.if.loss[Eth1/1]"}],
        points=[
            {"itemid": "2001", "value": "1.8"},
            {"itemid": "2001", "value": "2.2"},
        ],
        use_trend=False,
    )
    assert any(str(s.get("signal") or "") == "zabbix_packet_loss_indicator" for s in signals)
