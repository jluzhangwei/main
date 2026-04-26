from app.parsers.time_parser import extract_device_profile


def test_detect_cisco_iosxr():
    text = "Cisco IOS XR Software, Version 7.9.2"
    vendor, os_family, _, version = extract_device_profile(text)
    assert vendor == "cisco"
    assert os_family == "cisco_iosxr"
    assert version == "7.9.2"


def test_detect_cisco_nxos():
    text = "Cisco Nexus Operating System (NX-OS) Software, Version 10.2(5)"
    vendor, os_family, _, version = extract_device_profile(text)
    assert vendor == "cisco"
    assert os_family == "cisco_nxos"
    assert version == "10.2(5)"


def test_detect_arista_eos():
    text = "Arista DCS-7050TX3-48C8-F\nSoftware image version: 4.31.1F\nArista EOS"
    vendor, os_family, _, _ = extract_device_profile(text)
    assert vendor == "arista"
    assert os_family == "arista_eos"


def test_detect_huawei_vrp():
    text = "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.220"
    vendor, os_family, _, version = extract_device_profile(text)
    assert vendor == "huawei"
    assert os_family == "huawei_vrp"
    assert version == "8.220"
