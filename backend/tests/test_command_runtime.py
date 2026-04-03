from __future__ import annotations

from app.models.schemas import JobCommandResult
from app.services import command_runtime


class _Command:
    def __init__(self):
        self.command = "show version"
        self.original_command = ""
        self.effective_command = ""


class _Store:
    def __init__(self):
        self.profile_updates = []
        self.name_updates = []

    def update_session_device_profile(self, session_id: str, **kwargs):
        self.profile_updates.append((session_id, kwargs))

    def update_session_device_name(self, session_id: str, device_name: str):
        self.name_updates.append((session_id, device_name))


class _Device:
    def __init__(self):
        self.name = ""
        self.vendor = ""
        self.platform = ""
        self.software_version = ""
        self.version_signature = ""


def test_apply_adapter_command_meta_reads_transport_metadata():
    command = _Command()
    adapter = type(
        "Adapter",
        (),
        {"last_command_meta": {"effective_command": "display version", "original_command": "show version"}},
    )()

    command_runtime.apply_adapter_command_meta(command, adapter)

    assert command.effective_command == "display version"
    assert command.original_command == "show version"


def test_apply_adapter_command_meta_supports_job_command_result():
    command = JobCommandResult(
        job_id="job-1",
        device_id="device-1",
        step_no=1,
        title="版本探测",
        command="show version",
    )
    adapter = type(
        "Adapter",
        (),
        {"last_command_meta": {"effective_command": "display version", "original_command": "show version"}},
    )()

    command_runtime.apply_adapter_command_meta(command, adapter)

    assert command.effective_command == "display version"
    assert command.original_command == "show version"


def test_parse_command_runtime_extracts_profile(monkeypatch):
    monkeypatch.setattr(
        command_runtime,
        "parse_command_output",
        lambda command_text, output: (
            "version",
            {
                "device_name": "Core-1",
                "vendor": "Huawei",
                "platform": "NE40E",
                "software_version": "8.180",
                "version_signature": "Huawei|NE40E|8.180",
            },
            "ok",
        ),
    )

    parsed = command_runtime.parse_command_runtime("display version", "output")

    assert parsed.category == "version"
    assert parsed.conclusion == "ok"
    assert parsed.device_profile.device_name == "Core-1"
    assert parsed.device_profile.vendor == "Huawei"
    assert parsed.device_profile.platform == "NE40E"
    assert parsed.device_profile.software_version == "8.180"
    assert parsed.device_profile.version_signature == "Huawei|NE40E|8.180"


def test_apply_device_profile_to_session_store_normalizes_vendor_and_signature():
    store = _Store()
    profile = command_runtime.ParsedDeviceProfile(
        device_name="Dist-1",
        vendor="Huawei",
        platform="CE12800",
        software_version="V200R001",
        version_signature="Huawei|CE12800|V200R001",
    )

    command_runtime.apply_device_profile_to_session_store(store, "session-1", profile)

    assert store.profile_updates == [
        (
            "session-1",
            {
                "vendor": "huawei",
                "platform": "CE12800",
                "software_version": "V200R001",
                "version_signature": "huawei|ce12800|v200r001",
            },
        )
    ]
    assert store.name_updates == [("session-1", "Dist-1")]


def test_apply_device_profile_to_job_device_preserves_vendor_case_and_normalizes_signature():
    device = _Device()
    profile = command_runtime.ParsedDeviceProfile(
        device_name="Leaf-1",
        vendor="Arista",
        platform="vEOS-lab",
        software_version="4.32.4M",
        version_signature="Arista|vEOS-lab|4.32.4M",
    )

    command_runtime.apply_device_profile_to_job_device(device, profile)

    assert device.name == "Leaf-1"
    assert device.vendor == "Arista"
    assert device.platform == "vEOS-lab"
    assert device.software_version == "4.32.4M"
    assert device.version_signature == "arista|veos-lab|4.32.4m"
