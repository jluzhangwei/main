from datetime import datetime

from app.parsers.log_filter import filter_log_with_context


def test_single_hit():
    raw = "\n".join(
        [
            "Feb 19 10:00:00 line0",
            "Feb 19 10:00:01 line1",
            "Feb 19 10:00:02 line2",
            "Feb 19 10:00:03 hit",
            "Feb 19 10:00:04 line4",
        ]
    )
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 10, 0, 3),
        datetime(2026, 2, 19, 10, 0, 3),
        context_lines=1,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 1
    assert res.blocks_count == 1
    assert "line2" in res.text and "line4" in res.text


def test_overlap_merge():
    raw = "\n".join([f"Feb 19 10:00:{i:02d} line{i}" for i in range(10)])
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 10, 0, 3),
        datetime(2026, 2, 19, 10, 0, 4),
        context_lines=2,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 2
    assert res.blocks_count == 1


def test_adjacent_merge():
    raw = "\n".join([f"Feb 19 10:00:{i:02d} line{i}" for i in range(10)])
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 10, 0, 2),
        datetime(2026, 2, 19, 10, 0, 5),
        context_lines=1,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 4
    assert res.blocks_count == 1


def test_no_hit():
    raw = "\n".join([f"Feb 19 10:00:{i:02d} line{i}" for i in range(5)])
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 11, 0, 0),
        datetime(2026, 2, 19, 11, 1, 0),
        context_lines=3,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 0
    assert res.blocks_count == 0
    assert res.text == ""


def test_huawei_month_day_year_with_tz_hit():
    raw = "\n".join(
        [
            "Feb  3 2026 23:09:24+08:00 dev %% msg0",
            "Feb  3 2026 23:09:25+08:00 dev %% target",
            "Feb  3 2026 23:09:26+08:00 dev %% msg2",
        ]
    )
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 3, 23, 9, 25),
        datetime(2026, 2, 3, 23, 9, 25),
        context_lines=1,
        vendor="huawei",
        reference_year=2026,
    )
    assert res.hits_count == 1
    assert res.blocks_count == 1
    assert "target" in res.text


def test_vendor_specific_parser_isolation():
    # Cisco-style timestamp should not be treated as Huawei when vendor is fixed to huawei.
    raw = "\n".join(
        [
            "Feb 19 10:00:00 cisco-like",
            "Feb 19 10:00:01 cisco-like-hit",
            "Feb 19 10:00:02 cisco-like",
        ]
    )
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 10, 0, 1),
        datetime(2026, 2, 19, 10, 0, 1),
        context_lines=1,
        vendor="huawei",
        reference_year=2026,
    )
    assert res.hits_count == 0
    assert res.blocks_count == 0


def test_cisco_year_prefix_with_milliseconds_hit():
    raw = "\n".join(
        [
            "2026 Feb  7 09:05:27.741 host netstack: msg1",
            "2026 Feb  7 09:06:30.748 host netstack: msg2",
            "2026 Feb  7 09:07:32.753 host netstack: target",
            "2026 Feb  7 09:08:35.752 host netstack: msg4",
        ]
    )
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 7, 9, 7, 32),
        datetime(2026, 2, 7, 9, 7, 32),
        context_lines=1,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 1
    assert res.blocks_count == 1
    assert "target" in res.text


def test_cisco_asr_prefix_with_slot_hit():
    raw = "\n".join(
        [
            "RP/0/RSP0/CPU0:Feb 19 00:10:47.775 SGT: bgp[1092]: msg1",
            "RP/0/RSP1/CPU0:Feb 19 00:12:29.297 SGT: bgp[1092]: target",
            "RP/0/RSP0/CPU0:Feb 19 00:13:39.591 SGT: tcp[295]: msg3",
        ]
    )
    res = filter_log_with_context(
        raw,
        datetime(2026, 2, 19, 0, 12, 29),
        datetime(2026, 2, 19, 0, 12, 29),
        context_lines=1,
        vendor="cisco",
        reference_year=2026,
    )
    assert res.hits_count == 1
    assert res.blocks_count == 1
    assert "target" in res.text
