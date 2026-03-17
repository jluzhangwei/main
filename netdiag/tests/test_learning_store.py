from app.diagnosis.learning_store import NetdiagLearningStore


def _store(tmp_path):
    return NetdiagLearningStore((tmp_path / "state" / "learning.json").as_posix(), max_events=200)


def test_import_and_resolve_command(tmp_path):
    store = _store(tmp_path)
    rows = [
        {
            "vendor": "huawei",
            "os_family": "huawei_vrp",
            "intent": "interface_summary",
            "command": "display interface brief",
            "priority": 200,
            "enabled": True,
            "source": "official",
        },
        {
            "vendor": "huawei",
            "os_family": "",
            "intent": "interface_summary",
            "command": "display interface",
            "priority": 80,
            "enabled": True,
            "source": "official",
        },
    ]
    result = store.import_rows(rows, source="official")
    assert result["imported"] == 2
    assert store.resolve_command("interface_summary", "huawei_vrp") == "display interface brief"


def test_record_event_updates_summary_and_rule_stats(tmp_path):
    store = _store(tmp_path)
    store.upsert_rule(
        {
            "vendor": "huawei",
            "os_family": "huawei_vrp",
            "intent": "cpu_health",
            "command": "display cpu-usage",
            "source": "official",
            "enabled": True,
        }
    )

    store.record_execution_event(
        session_id="s1",
        round_no=1,
        device_id="dev-1",
        profile="huawei_vrp",
        intent="cpu_health",
        command="display cpu-usage",
        status="success",
        output_text="CPU Usage Stat: 18%",
    )
    store.record_execution_event(
        session_id="s1",
        round_no=2,
        device_id="dev-1",
        profile="huawei_vrp",
        intent="cpu_health",
        command="display cpu-usage",
        status="success",
        output_text="% Unrecognized command found at '^' position.",
    )

    summary = store.summary()
    assert summary["events_total"] == 2
    assert summary["valid_outputs"] == 1
    assert len(summary["by_intent"]) >= 1
    assert summary["by_intent"][0]["key"] == "cpu_health"

    rows = store.list_library()
    assert len(rows) == 1
    assert rows[0]["success"] == 1
    assert rows[0]["fail"] == 1
    assert rows[0]["last_error_type"] == "syntax"


def test_parse_csv_text_then_import(tmp_path):
    store = _store(tmp_path)
    csv_text = (
        "vendor,os_family,intent,command,min_version,max_version,priority,enabled,source\n"
        "arista,arista_eos,system_log_recent,show logging,,,180,false,official\n"
    )
    rows = store.parse_csv_text(csv_text)
    assert len(rows) == 1
    result = store.import_rows(rows, source="official")
    assert result["imported"] == 1
    saved = store.list_library(enabled_only=False)[0]
    assert saved["enabled"] is False
    assert store.resolve_command("system_log_recent", "arista_eos") is None
