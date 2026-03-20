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
