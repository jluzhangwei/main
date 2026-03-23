from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import CommandCapabilityRule, CommandCapabilityUpsertRequest, DeviceProtocol
from app.services.command_capability_store import CommandCapabilityStore


def _build_store(tmp_path: Path, *, threshold: int = 200, interval: int = 60) -> CommandCapabilityStore:
    return CommandCapabilityStore(
        snapshot_path=tmp_path / "snapshot.json",
        wal_path=tmp_path / "capability.wal",
        compact_event_threshold=threshold,
        compact_interval_seconds=interval,
    )


def test_snapshot_only_recovery(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|ne40e|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="show version",
            action="rewrite",
            rewrite_to="display version",
            source="learned",
        )
    )
    store.flush_snapshot()

    reloaded = _build_store(tmp_path, threshold=100000, interval=3600)
    rules = reloaded.list_rules()
    assert len(rules) == 1
    assert rules[0].action == "rewrite"
    assert rules[0].rewrite_to == "display version"


def test_wal_only_recovery(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|ne40e|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="show inventory",
            action="block",
            reason_text="CLI not supported",
            source="learned",
        )
    )

    assert not (tmp_path / "snapshot.json").exists()

    reloaded = _build_store(tmp_path, threshold=100000, interval=3600)
    matched = reloaded.resolve_match(
        host="192.168.0.102",
        protocol=DeviceProtocol.ssh,
        device_type=None,
        vendor=None,
        version_signature="huawei|ne40e|8.180",
        command_text="show inventory",
    )
    assert matched is not None
    assert matched.rule.action == "block"


def test_snapshot_and_wal_mixed_recovery(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|ne40e|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="show version",
            action="rewrite",
            rewrite_to="display version",
            source="learned",
        )
    )
    store.flush_snapshot()

    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|ne40e|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="display interface brief",
            action="block",
            reason_text="unsupported on this image",
            source="learned",
        )
    )

    reloaded = _build_store(tmp_path, threshold=100000, interval=3600)
    commands = {item.command_key: item for item in reloaded.list_rules(version_signature="huawei|ne40e|8.180")}
    assert "show version" in commands
    assert "display interface brief" in commands


def test_corrupted_snapshot_and_malformed_wal_tail_is_tolerated(tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    wal_path = tmp_path / "capability.wal"
    snapshot_path.write_text("{broken-json", encoding="utf-8")

    rule = CommandCapabilityRule(
        scope_type="version",
        scope_key=CommandCapabilityStore.build_scope_key(
            scope_type="version",
            host=None,
            protocol=DeviceProtocol.ssh,
            version_signature="huawei|ne40e|8.180",
        ),
        version_signature="huawei|ne40e|8.180",
        protocol=DeviceProtocol.ssh,
        command_key="show interfaces status",
        action="rewrite",
        rewrite_to="display interface brief",
        source="learned",
    )
    event = {"op": "upsert", "rule": rule.model_dump(mode="json")}
    wal_path.write_text(
        "\n".join([json.dumps(event, ensure_ascii=False), "{bad-line"]),
        encoding="utf-8",
    )

    store = CommandCapabilityStore(
        snapshot_path=snapshot_path,
        wal_path=wal_path,
        compact_event_threshold=100000,
        compact_interval_seconds=3600,
    )
    rules = store.list_rules(version_signature="huawei|ne40e|8.180")
    assert len(rules) == 1
    assert rules[0].command_key == "show interfaces status"


def test_hit_count_persists_across_reload(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    learned = store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|ne40e|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="show version",
            action="rewrite",
            rewrite_to="display version",
            source="learned",
        )
    )
    updated = store.register_hit(learned.id)
    assert updated is not None
    assert updated.hit_count == 1
    assert updated.last_hit_at is not None

    reloaded = _build_store(tmp_path, threshold=100000, interval=3600)
    rule = reloaded.get_rule(learned.id)
    assert rule is not None
    assert rule.hit_count == 1
    assert rule.last_hit_at is not None


def test_partial_version_signature_single_token_matches(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="ne40e",
            protocol=DeviceProtocol.ssh,
            command_key="show version",
            action="rewrite",
            rewrite_to="display version",
            source="manual",
        )
    )

    matched = store.resolve_match(
        host="192.168.0.88",
        protocol=DeviceProtocol.ssh,
        device_type=None,
        vendor=None,
        version_signature="huawei|ne40e|8.180",
        command_text="show version",
    )
    assert matched is not None
    assert matched.rule.rewrite_to == "display version"


def test_partial_version_signature_two_tokens_preferred_over_single(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei",
            protocol=DeviceProtocol.ssh,
            command_key="show interface brief",
            action="rewrite",
            rewrite_to="display interface brief",
            source="manual",
        )
    )
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="huawei|8.180",
            protocol=DeviceProtocol.ssh,
            command_key="show interface brief",
            action="rewrite",
            rewrite_to="display interface brief | include up",
            source="manual",
        )
    )

    matched = store.resolve_match(
        host="192.168.0.88",
        protocol=DeviceProtocol.ssh,
        device_type=None,
        vendor=None,
        version_signature="huawei|ne40e|8.180",
        command_text="show interface brief",
    )
    assert matched is not None
    assert matched.rule.version_signature == "huawei|8.180"
    assert matched.rule.rewrite_to == "display interface brief | include up"


def test_partial_version_signature_non_match_returns_none(tmp_path):
    store = _build_store(tmp_path, threshold=100000, interval=3600)
    store.upsert_rule(
        CommandCapabilityUpsertRequest(
            scope_type="version",
            version_signature="arista",
            protocol=DeviceProtocol.ssh,
            command_key="show version",
            action="rewrite",
            rewrite_to="show version",
            source="manual",
        )
    )

    matched = store.resolve_match(
        host="192.168.0.88",
        protocol=DeviceProtocol.ssh,
        device_type=None,
        vendor=None,
        version_signature="huawei|ne40e|8.180",
        command_text="show version",
    )
    assert matched is None
