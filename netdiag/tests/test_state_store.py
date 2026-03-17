from app.diagnosis.state_store import NetdiagStateStore


def _store(tmp_path):
    return NetdiagStateStore((tmp_path / "state" / "device_state.json").as_posix(), max_points=5000)


def test_append_and_query_points(tmp_path):
    store = _store(tmp_path)
    r = store.append_points(
        [
            {"ts": 100, "device_id": "dev-1", "domain": "link", "key": "domain_delta", "value": 0.2},
            {"ts": 101, "device_id": "dev-1", "domain": "link", "key": "domain_delta", "value": 0.4},
            {"ts": 102, "device_id": "dev-2", "domain": "resource", "key": "domain_delta", "value": -0.3},
        ]
    )
    assert r["added"] == 3
    rows = store.query_points(device_id="dev-1", domain="link", key="domain_delta", limit=10)
    assert len(rows) == 2
    assert rows[0]["value"] == 0.2


def test_baseline_compare_has_significance(tmp_path):
    store = _store(tmp_path)
    store.append_points(
        [{"ts": 100 + i, "device_id": "dev-1", "domain": "link", "key": "domain_delta", "value": 0.1} for i in range(20)]
    )
    cmp = store.baseline_compare(device_id="dev-1", domain="link", key="domain_delta", current_value=0.9, history_limit=50)
    assert cmp["history_count"] >= 1
    assert float(cmp["delta_vs_median"]) > 0.5
    assert bool(cmp["is_significant"]) is True
