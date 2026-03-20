from __future__ import annotations

from app.models.schemas import DeviceProtocol, DeviceTarget, Session
from app.services.adapters import SSHAdapter
from app.services.playbook import build_playbook, extract_interface_target, infer_intents


def test_fix_requests_emit_default_vendor_repair_command():
    steps = build_playbook("请自动修复接口故障", vendor="cisco_like")

    repair_steps = [step for step in steps if step.title == "建议修复动作"]
    assert len(repair_steps) == 1
    assert repair_steps[0].command == "configure terminal ; interface GigabitEthernet0/0/1 ; no shutdown"


def test_fix_requests_emit_huawei_repair_command():
    steps = build_playbook("请自动修复接口故障", vendor="huawei")

    repair_steps = [step for step in steps if step.title == "建议修复动作"]
    assert len(repair_steps) == 1
    assert repair_steps[0].command == "system-view ; interface GigabitEthernet0/0/1 ; undo shutdown"


def test_vendor_repair_command_can_be_split_into_config_steps():
    steps = build_playbook("请自动修复接口故障", vendor="cisco_like")
    repair_command = next(step.command for step in steps if step.title == "建议修复动作")

    parts = split_config_command(repair_command)

    assert parts == [
        "configure terminal",
        "interface GigabitEthernet0/0/1",
        "no shutdown",
    ]


def test_huawei_show_commands_translate_to_display_forms():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="huawei", device_type="huawei"),
    )
    adapter = SSHAdapter(session)

    assert adapter._translate_command("show ip interface brief") == "display ip interface brief"
    assert adapter._translate_command("show arp") == "display arp"
    assert (
        adapter._translate_command("show running-config interface Ethernet1/0/6")
        == "display current-configuration interface Ethernet1/0/6"
    )
    assert adapter._translate_command("show inventory") == "display inventory"


def test_huawei_config_workflow_strips_mode_wrapper_before_send_config_set():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="huawei", device_type="huawei"),
    )
    adapter = SSHAdapter(session)
    workflow = adapter._extract_config_workflow("system-view ; interface GigabitEthernet0/0/1 ; undo shutdown")

    assert workflow == [
        "interface GigabitEthernet0/0/1",
        "undo shutdown",
    ]


def test_chinese_port_keyword_is_recognized_as_interface_intent():
    intents = infer_intents("请排查 Ethernet1/0/6 端口 down 的原因")
    assert intents == ["interface"]


def test_negated_fix_request_does_not_emit_repair_step():
    steps = build_playbook("请排查端口 down，先只诊断，不要执行修复", vendor="huawei")
    repair_steps = [step for step in steps if step.title == "建议修复动作"]
    assert repair_steps == []


def test_extract_interface_target_with_space_format():
    target = extract_interface_target("端口 Ethernet 1/0/6 down了")
    assert target == "Ethernet1/0/6"


def test_build_playbook_adds_target_interface_config_check():
    steps = build_playbook("请诊断 Ethernet 1/0/6 down", vendor="huawei")
    commands = [step.command for step in steps]
    assert "show running-config interface Ethernet1/0/6" in commands


def test_huawei_interface_name_spacing_is_normalized():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="huawei", device_type="huawei"),
    )
    adapter = SSHAdapter(session)
    normalized = adapter._normalize_interface_tokens("display interface GigabitEthernet 1/0/6")

    assert normalized == "display interface GigabitEthernet1/0/6"


def test_cli_error_detection_for_common_parser_errors():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="huawei", device_type="huawei"),
    )
    adapter = SSHAdapter(session)

    assert adapter._looks_like_cli_error("Error: Unrecognized command found at '^' position.")
    assert adapter._looks_like_cli_error("Error: Wrong parameter found at '^' position.")
    assert not adapter._looks_like_cli_error("Interface GigabitEthernet1/0/6 current state : UP")


def test_vendor_hint_can_be_refreshed_from_vrp_version_output():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)

    adapter._refresh_vendor_hint_from_output(
        "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.180"
    )

    assert adapter.session.device.vendor == "huawei"
    assert adapter.session.device.device_type == "huawei"


def test_retry_candidates_include_huawei_interface_family_variants():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="huawei", device_type="huawei"),
    )
    adapter = SSHAdapter(session)

    candidates = adapter._retry_candidates(
        "display interface GigabitEthernet 1/0/6",
        "display interface GigabitEthernet1/0/6",
    )

    lowered = [item.lower() for item in candidates]
    assert any("display interface ethernet1/0/6" in item for item in lowered)
    assert any("display interface ge1/0/6" in item for item in lowered)


def split_config_command(command: str) -> list[str]:
    return [part.strip() for part in command.split(";") if part.strip()]
