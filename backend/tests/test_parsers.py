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


def test_parse_command_error_output_for_invalid_input():
    output = "          ^\n% Invalid input detected at '^' marker."
    category, parsed, conclusion = parse_command_output("interface Ethernet2", output)

    assert category == "command_error"
    assert parsed["cli_error"] is True
    assert "invalid input" in parsed["reason"].lower()
    assert "failed" in conclusion.lower()


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
