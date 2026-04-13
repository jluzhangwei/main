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


def test_sop_store_publish_archives_previous_published_in_same_topic(tmp_path: Path):
    store = SOPStore(snapshot_path=tmp_path / "sop_snapshot.json", wal_path=tmp_path / "sop.wal")
    base = SOPSeed(
        id="route_delivery_v1",
        name="目标前缀未传播排查",
        topic_key="route-delivery",
        topic_name="目标前缀未传播排查",
        summary="summary",
        usage_hint="hint",
        trigger_keywords=("路由", "前缀"),
    ).to_record()
    draft = base.model_copy(update={"id": "route_delivery_v2", "status": "draft", "version": 2, "is_current_published": False})
    store._records[base.id] = base
    store._records[draft.id] = draft

    payload = store.publish_record(draft.id)

    assert payload.item.id == draft.id
    assert store._records[draft.id].status == "published"
    assert store._records[draft.id].is_current_published is True
    assert store._records[base.id].status == "archived"
    assert store._records[base.id].is_current_published is False


def test_sop_store_next_version_for_topic_uses_topic_key(tmp_path: Path):
    store = SOPStore(snapshot_path=tmp_path / "sop_snapshot.json", wal_path=tmp_path / "sop.wal")
    base = SOPSeed(
        id="ospf_history_v1",
        name="OSPF 历史抖动取证",
        topic_key="ospf-history",
        topic_name="OSPF 历史抖动取证",
        summary="summary",
        usage_hint="hint",
        trigger_keywords=("ospf",),
    ).to_record()
    newer = base.model_copy(update={"id": "ospf_history_v2", "version": 2, "status": "draft", "is_current_published": False})
    store._records[base.id] = base
    store._records[newer.id] = newer

    assert store.next_version_for_topic("ospf-history") == 3


def test_sop_store_cleanup_historical_records_merges_duplicates_and_topic_suffixes(tmp_path: Path):
    store = SOPStore(snapshot_path=tmp_path / "sop_snapshot.json", wal_path=tmp_path / "sop.wal")
    base = SOPSeed(
        id="iface_shutdown_pub",
        name="接口管理性关闭排查",
        topic_key="接口管理性关闭排查",
        topic_name="接口管理性关闭排查",
        summary="summary",
        usage_hint="hint",
        trigger_keywords=("接口", "shutdown"),
    ).to_record()
    duplicate = base.model_copy(update={"id": "iface_shutdown_dup", "status": "draft", "is_current_published": False})
    suffixed = base.model_copy(update={"id": "iface_shutdown_v2", "topic_key": "接口管理性关闭排查-v2", "topic_name": "接口管理性关闭排查 v2", "status": "draft", "is_current_published": False})
    store._records[base.id] = base
    store._records[duplicate.id] = duplicate
    store._records[suffixed.id] = suffixed

    summary = store.cleanup_historical_records()

    assert summary["before"] == 3
    assert summary["after"] == 1
    remaining = list(store._records.values())
    assert len(remaining) == 1
    assert remaining[0].topic_key == "接口管理性关闭排查"
    assert remaining[0].topic_name == "接口管理性关闭排查"
