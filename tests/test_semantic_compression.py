from app.ai.semantic_compression import build_semantic_package, normalize_strategy


def test_normalize_strategy_legacy_bool_mapping():
    assert normalize_strategy(True) == "group_repeats"
    assert normalize_strategy("1") == "group_repeats"
    assert normalize_strategy("factor_time") == "factor_time"
    assert normalize_strategy("template_vars") == "template_vars"
    assert normalize_strategy("repeat_timeline") == "template_vars"
    assert normalize_strategy("bad") == "off"


def test_repeated_huawei_log_builds_group_repeat_view():
    text = "\n".join(
        [
            "Feb  3 2026 23:09:25+08:00 R1 %%01IFNET/2/linkDown_active(l):The interface status changes. (ifName=25GE1/0/1)",
            "Feb  3 2026 23:10:25+08:00 R1 %%01IFNET/2/linkDown_active(l):The interface status changes. (ifName=25GE1/0/1)",
            "Feb  3 2026 23:11:25+08:00 R1 %%01IFNET/2/linkDown_active(l):The interface status changes. (ifName=25GE1/0/1)",
            "Feb  3 2026 23:12:25+08:00 R1 %%01IFNET/2/linkDown_active(l):The interface status changes. (ifName=25GE1/0/1)",
            "Feb  3 2026 23:13:25+08:00 R1 %%01IFNET/2/linkDown_active(l):The interface status changes. (ifName=25GE1/0/1)",
        ]
        + [
            f"Feb  3 2026 23:{14 + i:02d}:25+08:00 R1 info filler line {i}"
            for i in range(130)
        ]
    )
    package = build_semantic_package(
        text,
        source_name="filtered.log",
        device_id="dev-1",
        default_year=2026,
        strategy="group_repeats",
    )
    assert package["used"] is True
    groups = package["index"]["groups"]
    repeated = next(g for g in groups if g["event_code"] == "%%01IFNET/2/linkDown_active")
    assert repeated["count"] == 5
    assert len(repeated["timeline"]) == 5
    assert "Conservative Semantic Compression View" in package["markdown"]


def test_small_non_repeated_log_skips_group_repeat_compression():
    text = "\n".join(
        [
            "2026 Feb 18 14:01:27.855 leaf-01 netstack: Routing adjacency over vPC detected without required configuration.",
            "2026 Feb 18 14:05:31.863 leaf-01 netstack: another event body",
            "2026 Feb 18 14:07:36.870 leaf-01 netstack: third different event body",
        ]
    )
    package = build_semantic_package(
        text,
        source_name="filtered.log",
        device_id="dev-2",
        default_year=2026,
        strategy="group_repeats",
    )
    assert package["used"] is False
    assert package["markdown"] == ""


def test_factor_time_splits_collector_and_device_times_from_template():
    line1 = (
        "2026-04-17 18:16:57 [sql] TH-THSTT1-Garena-WAN-R030-Leaf-02 "
        "<188>Apr 17 2026 10:16:57 TH-THSTT1-Garena-WAN-R030-Leaf-02 "
        "%%01RM/4/hwIpv4PrefixThresholdExceed_active(l):CID=0x8071753d-alarmID=0x08390006;"
        "The number of IPv4 prefixes exceeded the threshold. "
        "(PrefixCount=686090, MaxValue=800000, ThresholdLowerLimit=560000, ThresholdUpperLimit=640000)"
    )
    line2 = (
        "2026-04-17 18:21:57 [sql] TH-THSTT1-Garena-WAN-R030-Leaf-02 "
        "<188>Apr 17 2026 10:21:57 TH-THSTT1-Garena-WAN-R030-Leaf-02 "
        "%%01RM/4/hwIpv4PrefixThresholdExceed_active(l):CID=0x8071753d-alarmID=0x08390006;"
        "The number of IPv4 prefixes exceeded the threshold. "
        "(PrefixCount=701234, MaxValue=800000, ThresholdLowerLimit=560000, ThresholdUpperLimit=640000)"
    )
    package = build_semantic_package(
        "\n".join([line1, line2]),
        source_name="filtered.log",
        device_id="dev-3",
        default_year=2026,
        strategy="factor_time",
    )
    assert package["used"] is True
    assert package["index"]["strategy"] == "factor_time"
    groups = package["index"]["groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["event_code"] == "%%01RM/4/hwIpv4PrefixThresholdExceed_active"
    assert "The number of IPv4 prefixes exceeded the threshold." in group["template_text"]
    assert group["static_fields"]["MaxValue"] == "800000"
    assert "PrefixCount" in group["varying_field_names"]
    assert group["occurrences"][0]["collector_time"] == "2026-04-17 18:16:57"
    assert group["occurrences"][0]["device_time"] == "2026-04-17 10:16:57"
    assert group["occurrences"][0]["varying_fields"]["PrefixCount"] == "686090"
    assert "Time-Factored Compression View" in package["markdown"]


def test_nxos_group_repeats_uses_facility_and_normalized_message():
    text = "\n".join(
        [
            "2026 Feb 18 14:01:27.855 leaf-01 netstack: Routing adjacency over vPC detected without required configuration. See https://cisco.com/go/l3pr for more information. (message repeated 2 times)",
            "2026 Feb 18 14:03:29.859 leaf-01 netstack: Routing adjacency over vPC detected without required configuration. See https://cisco.com/go/l3pr for more information. (message repeated 1 time)",
        ]
        + [f"2026 Feb 18 14:{10+i:02d}:00.000 leaf-01 filler: event {i}" for i in range(130)]
    )
    package = build_semantic_package(
        text,
        source_name="filtered.log",
        device_id="dev-4",
        default_year=2026,
        strategy="group_repeats",
        vendor="cisco",
        os_family="nxos",
    )
    groups = package["index"]["groups"]
    target = next(g for g in groups if g["event_code"] == "netstack")
    assert target["count"] == 2


def test_iosxr_factor_time_keeps_same_neighbor_in_one_template():
    text = "\n".join(
        [
            "RP/0/RSP0/CPU0:Feb 19 00:10:47.775 SGT: bgp[1092]: %ROUTING-BGP-5-MAXPFX : No. of IPv6 Unicast prefixes received from 2001:de8:4::13:6106:3 has reached 481, max 600",
            "RP/0/RSP0/CPU0:Feb 19 00:12:29.295 SGT: bgp[1092]: %ROUTING-BGP-5-MAXPFX : No. of IPv6 Unicast prefixes received from 2001:de8:4::13:6106:3 has reached 482, max 600",
        ]
    )
    package = build_semantic_package(
        text,
        source_name="filtered.log",
        device_id="dev-5",
        default_year=2026,
        strategy="factor_time",
        vendor="cisco",
        os_family="iosxr",
    )
    groups = package["index"]["groups"]
    assert len(groups) == 1
    assert groups[0]["event_code"] == "%ROUTING-BGP-5-MAXPFX"


def test_template_vars_merges_same_template_and_moves_changes_to_variables():
    text = "\n".join(
        [
            "2026-04-17 18:17:17 [sql] R1 <186>Apr 17 2026 10:17:17 R1 %%01BGP/2/bgpBackwardTransNotification(t):CID=0x80130440;The BGP FSM moves from a higher numbered state to a lower numbered state. (BgpPeerRemoteAddr=10.254.2.6, BgpPeerLastError=66, BgpPeerState=1,VpnInstance=_public_)",
            "2026-04-17 18:17:18 [sql] R1 <186>Apr 17 2026 10:17:18 R1 %%01BGP/2/bgpBackwardTransNotification(t):CID=0x80130440;The BGP FSM moves from a higher numbered state to a lower numbered state. (BgpPeerRemoteAddr=10.254.2.12, BgpPeerLastError=66, BgpPeerState=1,VpnInstance=_public_)",
        ]
    )
    package = build_semantic_package(
        text,
        source_name="filtered.log",
        device_id="dev-6",
        default_year=2026,
        strategy="template_vars",
        vendor="huawei",
        os_family="huawei_sql",
    )
    assert package["used"] is True
    assert package["index"]["strategy"] == "template_vars"
    groups = package["index"]["groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["event_code"] == "%%01BGP/2/bgpBackwardTransNotification"
    assert group["occurrence_count"] == 2
    assert "BgpPeerRemoteAddr" in group["varying_field_names"]
    assert group["static_fields"]["BgpPeerLastError"] == "66"
    assert group["occurrences"][0]["varying_fields"]["BgpPeerRemoteAddr"] == "10.254.2.6"
    assert group["occurrences"][1]["varying_fields"]["BgpPeerRemoteAddr"] == "10.254.2.12"
    assert "Template Variables View" in package["markdown"]
