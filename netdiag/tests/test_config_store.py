from pathlib import Path

from app.diagnosis.config_store import NetdiagConfigStore


def _store(tmp_path):
    return NetdiagConfigStore((tmp_path / "state" / "config_history.json").as_posix())


def test_add_list_and_diff_snapshot(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    a = d / "a.txt"
    b = d / "b.txt"
    a.write_text("interface Eth1/1\n description old\n", encoding="utf-8")
    b.write_text("interface Eth1/1\n description new\n router bgp 65001\n", encoding="utf-8")

    sa = store.add_snapshot({"device_id": "dev-1", "session_id": "s1", "profile": "cisco_nxos", "command": "show running-config", "file_path": a.as_posix()})
    sb = store.add_snapshot({"device_id": "dev-1", "session_id": "s1", "profile": "cisco_nxos", "command": "show running-config", "file_path": b.as_posix()})
    rows = store.list_snapshots(device_id="dev-1", session_id="s1", limit=10)
    assert len(rows) == 2
    diff = store.diff_snapshots(sa["snapshot_id"], sb["snapshot_id"], context=2, max_lines=200)
    assert diff["changed_lines_add"] >= 1
    assert "router bgp" in diff["diff_text"].lower()
