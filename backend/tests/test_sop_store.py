from pathlib import Path

from app.services.sop_store import SOPSeed, SOPStore


def test_sop_store_matches_semantic_aliases_for_interface_shutdown(tmp_path: Path):
    store = SOPStore(
        snapshot_path=tmp_path / "sop_snapshot.json",
        wal_path=tmp_path / "sop.wal",
        seeds=(
            SOPSeed(
                id="interface_shutdown",
                name="接口管理性关闭排查",
                summary="从历史会话提炼的接口 shutdown 排查方法。",
                usage_hint="仅在接口被管理性关闭或疑似 shutdown 时参考。",
                trigger_keywords=("接口", "shutdown", "disable"),
                vendor_tags=("arista",),
                version_signatures=("veos",),
                preconditions=("接口",),
                anti_conditions=("物理断链",),
            ),
        ),
    )
    matched = store.matched_entries(
        problem="帮我查询一下设备端口是否admin shutdown的情况",
        vendor="arista",
        version_signature="arista|veos-lab|4.32.4.1m",
        run_key="run_s:test",
    )
    assert len(matched) == 1
    assert matched[0].id == "interface_shutdown"


def test_sop_store_runtime_dedupe_keeps_latest_equivalent_entry(tmp_path: Path):
    store = SOPStore(snapshot_path=tmp_path / "sop_snapshot.json", wal_path=tmp_path / "sop.wal")
    seed = SOPSeed(
        id="interface_shutdown_a",
        name="接口管理性关闭排查",
        summary="从历史会话提炼的接口 shutdown 排查方法。",
        usage_hint="hint",
        trigger_keywords=("接口", "shutdown"),
        vendor_tags=("arista",),
        version_signatures=("veos",),
        preconditions=("接口",),
    )
    record_a = seed.to_record()
    store._records[record_a.id] = record_a
    record_b = seed.to_record().model_copy(update={"id": "interface_shutdown_b"})
    store._records[record_b.id] = record_b
    entries = [record_a.to_archive_response(), record_b.to_archive_response()]
    deduped = store.dedupe_entries_for_runtime(entries)
    assert len(deduped) == 1
    assert deduped[0].id in {"interface_shutdown_a", "interface_shutdown_b"}
