from app.services.parsers import parse_command_output


def test_parse_interface_detects_down():
    output = """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0/1    10.0.1.1        YES manual administratively down down
"""
    category, parsed, conclusion = parse_command_output("show ip interface brief", output)

    assert category == "interface"
    assert parsed["down_interfaces"] == ["GigabitEthernet0/0/1"]
    assert parsed["admin_down_interfaces"] == ["GigabitEthernet0/0/1"]
    assert "down" in conclusion.lower()


def test_parse_routing_missing_default():
    output = "Codes: C - connected\nC 10.0.0.0/24 is directly connected, Gi0/0/0"
    category, parsed, _ = parse_command_output("show ip route", output)

    assert category == "routing"
    assert parsed["missing_default_route"] is True


def test_parse_routing_target_prefix_missing_does_not_become_default_route_issue():
    output = ""
    category, parsed, conclusion = parse_command_output("show ip route 2.0.0.0/24", output)

    assert category == "routing"
    assert parsed["missing_target_route"] is True
    assert parsed.get("missing_default_route") is None
    assert "target route not detected" in conclusion.lower()


def test_parse_routing_target_prefix_detected():
    output = "S        2.0.0.0/24 [1/0] via 10.0.0.1"
    category, parsed, conclusion = parse_command_output("show ip route | include 2.0.0.0", output)

    assert category == "routing"
    assert parsed["matched_targets"] == ["2.0.0.0"]
    assert parsed["missing_target_route"] is False
    assert "matching target" in conclusion.lower()


def test_parse_router_config_is_not_misclassified_as_route_lookup():
    output = "router ospf 1\n   network 1.1.1.1/32 area 0.0.0.0"
    category, parsed, conclusion = parse_command_output("show running-config | section router", output)

    assert category == "generic"
    assert "review raw output" in conclusion.lower()


def test_parse_huawei_interface_brief_marks_admin_down():
    output = """*down: administratively down
Interface                         IP Address/Mask      Physical   Protocol VPN
Ethernet1/0/6                     unassigned           *down      down     --
"""
    category, parsed, conclusion = parse_command_output("show ip interface brief", output)

    assert category == "interface"
    assert parsed["down_interfaces"] == ["Ethernet1/0/6"]
    assert parsed["admin_down_interfaces"] == ["Ethernet1/0/6"]
    assert "administratively down" in conclusion.lower()


def test_parse_interface_config_detects_shutdown_line():
    output = """#
interface Ethernet1/0/6
 shutdown
#
"""
    category, parsed, conclusion = parse_command_output(
        "show running-config interface Ethernet1/0/6",
        output,
    )

    assert category == "interface"
    assert parsed["admin_shutdown"] is True
    assert parsed["interface"] == "Ethernet1/0/6"
    assert "shutdown" in conclusion.lower()


def test_parse_command_error_output():
    output = "            ^\nError: Unrecognized command found at '^' position."
    category, parsed, conclusion = parse_command_output("show inventory", output)

    assert category == "command_error"
    assert parsed["cli_error"] is True
    assert "unrecognized command" in parsed["reason"].lower()
    assert "failed" in conclusion.lower()


def test_parse_command_error_output_for_invalid_input():
    output = "          ^\n% Invalid input detected at '^' marker."
    category, parsed, conclusion = parse_command_output("interface Ethernet2", output)

    assert category == "command_error"
    assert parsed["cli_error"] is True
    assert "invalid input" in parsed["reason"].lower()
    assert "failed" in conclusion.lower()


def test_parse_command_error_output_for_shell_command_not_found():
    output = "/bin/sh: line 1: count: command not found"
    category, parsed, conclusion = parse_command_output("show ip route ospf | count", output)

    assert category == "command_error"
    assert parsed["cli_error"] is True
    assert "command not found" in parsed["reason"].lower()
    assert "failed" in conclusion.lower()


def test_parse_ospf_neighbor_table_detects_full_neighbor():
    output = """Neighbor ID     Instance VRF      Pri State                  Dead Time   Address         Interface
12.1.1.2        1        default  1   FULL/DR                00:00:35    12.1.1.2        Ethernet2
"""
    category, parsed, conclusion = parse_command_output("show ip ospf neighbor", output)

    assert category == "protocol"
    assert parsed["neighbor_count"] == 1
    assert parsed["full_count"] == 1
    assert parsed["non_full_count"] == 0
    assert "full=1" in conclusion.lower()


def test_parse_filtered_ospf_neighbor_no_match_is_not_missing_neighbor():
    output = ""
    category, parsed, conclusion = parse_command_output(
        "show ip ospf neighbor | include 192.168.0.102",
        output,
    )

    assert category == "protocol"
    assert parsed["filtered_lookup"] is True
    assert parsed["neighbor_count"] is None
    assert "不能仅据此判断邻居缺失" in conclusion


def test_parse_ospf_interface_brief_counts_neighbors():
    output = """Interface          Instance VRF        Area            IP Address         Cost  State      Nbrs
Et2                1        default    0.0.0.0         12.1.1.1/24        10    Backup DR  1
Lo0                1        default    0.0.0.0         1.1.1.1/32         10    DR         0
"""
    category, parsed, conclusion = parse_command_output("show ip ospf interface brief", output)

    assert category == "protocol"
    assert parsed["neighbor_count"] == 1
    assert parsed["full_count"] == 0
    assert parsed["non_full_count"] == 0
    assert "当前检测到 ospf 邻居/接口关联数量 1" in conclusion.lower()


def test_parse_display_ospf_interface_extracts_ospf_interfaces():
    output = """\n          OSPF Process 1 with Router ID 192.168.0.83\n                  Interfaces\n\n Area: 0.0.0.0\n Interface                    IP Address      Type         State    Cost    Pri\n Eth1/0/0                     0.0.0.0         Broadcast    Down     1       1\n Eth1/0/1                     192.168.0.83    Broadcast    DR       1       1\n"""
    category, parsed, conclusion = parse_command_output("display ospf interface", output)

    assert category == "protocol"
    assert parsed["ospf_interfaces"] == [
        {"interface": "Eth1/0/0", "ip_address": "0.0.0.0", "state": "Down"},
        {"interface": "Eth1/0/1", "ip_address": "192.168.0.83", "state": "DR"},
    ]
    assert parsed["active_ospf_interfaces"] == ["Eth1/0/1"]
    assert parsed["down_ospf_interfaces"] == ["Eth1/0/0"]
    assert "ospf" in conclusion.lower()


def test_parse_lldp_neighbor_brief_extracts_local_and_remote_interfaces():
    output = """Local Intf              Neighbor Dev         Neighbor Intf        Exptime (sec)\n-------------------------------------------------------------------------------\nEthernet1/0/0           12808-84             Ethernet1/0/0                  109\nEthernet1/0/2           12808-85             Ethernet1/0/0                  118\n"""
    category, parsed, conclusion = parse_command_output("display lldp neighbor brief", output)

    assert category == "protocol"
    assert parsed["neighbor_count"] == 2
    assert parsed["neighbors"] == [
        {"local_interface": "Ethernet1/0/0", "neighbor_device": "12808-84", "neighbor_interface": "Ethernet1/0/0"},
        {"local_interface": "Ethernet1/0/2", "neighbor_device": "12808-85", "neighbor_interface": "Ethernet1/0/0"},
    ]
    assert parsed["local_interfaces"] == ["Ethernet1/0/0", "Ethernet1/0/2"]
    assert "lldp" in conclusion.lower()


def test_parse_version_extracts_device_name_from_uptime_line():
    output = """Cisco IOS Software
R1 uptime is 3 weeks, 1 day
"""
    category, parsed, conclusion = parse_command_output("show version", output)

    assert category == "version"
    assert parsed["device_name"] == "R1"
    assert "detected device name" in conclusion.lower()


def test_parse_version_extracts_device_name_from_prompt_style_line():
    output = """<NE40E-CORE-01>
Huawei Versatile Routing Platform Software
"""
    category, parsed, _ = parse_command_output("display version", output)

    assert category == "version"
    assert parsed["device_name"] == "NE40E-CORE-01"


def test_parse_version_extracts_device_name_from_sysname_field():
    output = """System Name: ARISTA-LAB-02
Software image version: 4.28.4M
"""
    category, parsed, _ = parse_command_output("show version", output)

    assert category == "version"
    assert parsed["device_name"] == "ARISTA-LAB-02"


def test_parse_version_extracts_device_name_from_hash_prompt_line():
    output = """Device-102#show version
Arista vEOS-lab
Software image version: 4.29.2F
Device-102#
"""
    category, parsed, _ = parse_command_output("show version", output)

    assert category == "version"
    assert parsed["device_name"] == "Device-102"
