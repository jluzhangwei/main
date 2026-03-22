from __future__ import annotations

import pytest

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


def test_config_wrapper_only_command_is_not_treated_as_config_set_workflow():
    session = Session(
        device=DeviceTarget(host="192.168.0.88", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    workflow = adapter._extract_config_workflow("configure terminal")

    assert workflow == []


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
    assert adapter._looks_like_cli_error("% Invalid input detected at '^' marker.")
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


class _FakeConfigConn:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.enable_calls = 0
        self.last_config_set: list[str] = []

    def check_enable_mode(self) -> bool:
        return self.enabled

    def enable(self) -> str:
        self.enable_calls += 1
        self.enabled = True
        return "enabled"

    def send_config_set(self, commands: list[str], **kwargs) -> str:
        self.last_config_set = list(commands)
        return "ok"

    def send_command_timing(self, command: str, read_timeout: int = 30) -> str:
        return f"timing:{command}"


@pytest.mark.asyncio
async def test_enable_command_uses_connection_enable_api():
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    fake = _FakeConfigConn(enabled=False)
    adapter.conn = fake

    output = await adapter.run_command("enable")

    assert output == "enabled"
    assert fake.enable_calls == 1


@pytest.mark.asyncio
async def test_enable_command_is_skipped_when_already_privileged():
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    fake = _FakeConfigConn(enabled=True)
    adapter.conn = fake

    output = await adapter.run_command("enable")

    assert output == "Already in privileged mode."
    assert fake.enable_calls == 0


@pytest.mark.asyncio
async def test_config_workflow_does_not_force_enable_implicitly():
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    fake = _FakeConfigConn(enabled=False)
    adapter.conn = fake

    output = await adapter.run_command("configure terminal ; interface GigabitEthernet1/0/6 ; no shutdown")

    assert output == "ok"
    assert fake.enable_calls == 0
    assert fake.last_config_set == ["interface GigabitEthernet1/0/6", "no shutdown"]


class _FakePromptFailConfigConn(_FakeConfigConn):
    def __init__(self):
        super().__init__(enabled=True)
        self.timing_history: list[str] = []

    def send_config_set(self, commands: list[str], **kwargs) -> str:
        raise RuntimeError("Pattern not detected: '[>#]' in output.")

    def send_command_timing(self, command: str, read_timeout: int = 30) -> str:
        self.timing_history.append(command)
        return f"timing:{command}"


@pytest.mark.asyncio
async def test_config_workflow_falls_back_to_compound_timing_on_prompt_detection_error():
    session = Session(
        device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh, vendor="arista", device_type="arista_eos"),
    )
    adapter = SSHAdapter(session, allow_simulation=False)
    fake = _FakePromptFailConfigConn()
    adapter.conn = fake

    output = await adapter.run_command("configure terminal ; interface Ethernet2 ; no shutdown")

    assert "timing:configure terminal" in output
    assert "timing:interface Ethernet2" in output
    assert "timing:no shutdown" in output
    assert "timing:end" in output
    assert fake.timing_history[-1].lower() == "end"


@pytest.mark.asyncio
async def test_mixed_compound_workflow_uses_timing_sequence_and_runs_save_after_exit():
    session = Session(
        device=DeviceTarget(host="192.168.0.102", protocol=DeviceProtocol.ssh, vendor="arista", device_type="arista_eos"),
    )
    adapter = SSHAdapter(session, allow_simulation=False)
    fake = _FakePromptFailConfigConn()
    adapter.conn = fake

    output = await adapter.run_command(
        "show interfaces status ; configure terminal ; interface Ethernet2 ; no shutdown ; write memory"
    )

    assert "timing:show interfaces status" in output
    assert "timing:configure terminal" in output
    assert "timing:interface Ethernet2" in output
    assert "timing:no shutdown" in output
    assert "timing:end" in output
    assert "timing:write memory" in output
    # Should exit config before save command.
    end_idx = fake.timing_history.index("end")
    save_idx = fake.timing_history.index("write memory")
    assert end_idx < save_idx


@pytest.mark.asyncio
async def test_single_config_command_uses_timing_not_send_config_set():
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    fake = _FakeConfigConn(enabled=True)
    adapter.conn = fake

    output = await adapter.run_command("interface GigabitEthernet1/0/6")

    assert output == "timing:interface GigabitEthernet1/0/6"
    assert fake.last_config_set == []


class _FakeEnablePromptConn(_FakeConfigConn):
    def __init__(self):
        super().__init__(enabled=False)
        self.timing_calls = 0
        self.timing_history: list[str] = []

    def enable(self) -> str:
        raise RuntimeError("Pattern not detected: 'Arista\\\\-EOS\\\\-1.*' in output.")

    def send_command_timing(self, command: str, read_timeout: int = 30) -> str:
        self.timing_calls += 1
        self.timing_history.append(command)
        if command.strip().lower().startswith("terminal length"):
            return "Arista-EOS-1#"
        if command.strip().lower().startswith("screen-length"):
            return "Arista-EOS-1#"
        self.enabled = True
        return "enable\nArista-EOS-1#"


@pytest.mark.asyncio
async def test_enable_falls_back_to_timing_mode_when_prompt_detection_fails():
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session, allow_simulation=False)
    fake = _FakeEnablePromptConn()
    adapter.conn = fake

    output = await adapter.run_command("enable")

    assert "Arista-EOS-1#" in output
    assert fake.timing_calls >= 1
    assert "enable" in [item.lower() for item in fake.timing_history]


class _FakeAliveConn:
    def is_alive(self):
        return {"is_alive": True}

    def disconnect(self):
        return None


@pytest.mark.asyncio
async def test_connect_reuses_existing_alive_connection(monkeypatch):
    session = Session(
        device=DeviceTarget(host="192.168.0.101", protocol=DeviceProtocol.ssh, vendor="cisco_like", device_type="cisco_ios"),
    )
    adapter = SSHAdapter(session)
    adapter.conn = _FakeAliveConn()

    monkeypatch.setattr("app.services.adapters.ConnectHandler", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("should not create")))

    await adapter.connect()

    assert adapter.conn is not None


class _FakeConnectedDevice:
    def __init__(self, device_type: str):
        self.device_type = device_type
        self.timing_history: list[str] = []
        self.prompt = "Device-102#"

    def is_alive(self):
        return {"is_alive": True}

    def send_command(self, command: str):
        if self.device_type == "huawei":
            return "Huawei Versatile Routing Platform Software\nVRP (R) software, Version 8.180"
        if self.device_type == "arista_eos":
            return "Arista vEOS-lab"
        return "Cisco IOS XE Software"

    def send_command_timing(self, command: str, read_timeout: int = 30):
        self.timing_history.append(command)
        lowered = command.strip().lower()
        if lowered.startswith("terminal length"):
            return "terminal length 0\n#"
        if lowered.startswith("screen-length"):
            return "screen-length 0 temporary\n>"
        return "#"

    def find_prompt(self):
        return self.prompt

    def disconnect(self):
        return None


@pytest.mark.asyncio
async def test_connect_can_fallback_to_huawei_when_initial_device_type_mismatches(monkeypatch):
    session = Session(
        device=DeviceTarget(
            host="192.168.0.88",
            protocol=DeviceProtocol.ssh,
            vendor="unknown",
            device_type="cisco_ios",
            username="tester",
            password="tester",
        ),
    )
    adapter = SSHAdapter(session, allow_simulation=False)
    attempts: list[str] = []

    def _fake_connect_handler(**kwargs):
        attempts.append(kwargs.get("device_type", ""))
        if kwargs.get("device_type") != "huawei":
            raise RuntimeError("Pattern not detected: '[>#]' in output.")
        return _FakeConnectedDevice(kwargs.get("device_type", ""))

    monkeypatch.setattr("app.services.adapters.ConnectHandler", _fake_connect_handler)

    await adapter.connect()

    assert adapter.conn is not None
    assert "cisco_ios" in attempts
    assert "huawei" in attempts
    assert adapter.session.device.device_type == "huawei"
    assert adapter.session.device.vendor == "huawei"


@pytest.mark.asyncio
async def test_connect_applies_terminal_paging_off_after_successful_probe(monkeypatch):
    session = Session(
        device=DeviceTarget(
            host="192.168.0.102",
            protocol=DeviceProtocol.ssh,
            vendor="arista",
            device_type="arista_eos",
            username="tester",
            password="tester",
        ),
    )
    adapter = SSHAdapter(session, allow_simulation=False)
    fake = _FakeConnectedDevice("arista_eos")

    monkeypatch.setattr("app.services.adapters.ConnectHandler", lambda **kwargs: fake)

    await adapter.connect()

    assert adapter.conn is fake
    assert any(cmd.strip().lower() == "terminal length 0" for cmd in fake.timing_history)
    assert adapter.session.device.name == "Device-102"


def split_config_command(command: str) -> list[str]:
    return [part.strip() for part in command.split(";") if part.strip()]
