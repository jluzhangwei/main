import json

from app.ai.analysis_manager import AIAnalysisManager


def test_filter_log_text_by_time_range_keeps_only_selected_window():
    mgr = AIAnalysisManager(output_root="/tmp/netlog-test-unused")
    text = "\n".join(
        [
            "2026-04-17 18:10:00 [sql] host event before",
            "2026-04-17 18:20:00 [sql] host event in",
            "continuation line",
            "2026-04-17 18:30:00 [sql] host event after",
        ]
    )
    filtered = mgr._filter_log_text_by_time_range(
        text,
        "2026-04-17 18:15:00",
        "2026-04-17 18:25:00",
    )
    assert "event before" not in filtered
    assert "event in" in filtered
    assert "continuation line" in filtered
    assert "event after" not in filtered


def test_build_device_report_text_respects_analysis_time_window(tmp_path):
    task_id = "task-1"
    device_id = "dev-1"
    dev_dir = tmp_path / task_id / device_id
    dev_dir.mkdir(parents=True)
    (dev_dir / "meta.json").write_text(
        json.dumps({"device_name": "R1", "device_ip": "10.0.0.1", "vendor": "huawei", "os_family": "huawei_sql"}),
        encoding="utf-8",
    )
    (dev_dir / "filtered.log").write_text(
        "\n".join(
            [
                "2026-04-17 18:10:00 [sql] host before event",
                "2026-04-17 18:20:00 [sql] host target event",
                "2026-04-17 18:30:00 [sql] host after event",
            ]
        ),
        encoding="utf-8",
    )
    mgr = AIAnalysisManager(output_root=str(tmp_path))
    text = mgr._build_device_report_text(
        task_id,
        device_id,
        compression_strategy="off",
        analysis_time_start="2026-04-17 18:15:00",
        analysis_time_end="2026-04-17 18:25:00",
        persist_artifacts=False,
    )
    assert "target event" in text
    assert "before event" not in text
    assert "after event" not in text
    assert '"analysis_time_window"' in text
