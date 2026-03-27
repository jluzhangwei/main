from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.parsers import parse_command_output


@dataclass(frozen=True)
class ParsedDeviceProfile:
    device_name: str = ""
    vendor: str = ""
    platform: str = ""
    software_version: str = ""
    version_signature: str = ""

    def has_value(self) -> bool:
        return any(
            [
                self.device_name,
                self.vendor,
                self.platform,
                self.software_version,
                self.version_signature,
            ]
        )


@dataclass(frozen=True)
class ParsedCommandRuntime:
    category: str
    parsed_data: dict[str, Any]
    conclusion: str
    device_profile: ParsedDeviceProfile


def apply_adapter_command_meta(command: Any, adapter: Any) -> None:
    meta = getattr(adapter, "last_command_meta", {}) or {}
    if not isinstance(meta, dict):
        return
    effective = str(meta.get("effective_command") or "").strip()
    if effective:
        command.effective_command = effective
    if not getattr(command, "original_command", None):
        original = str(meta.get("original_command") or "").strip()
        command.original_command = original or str(getattr(command, "command", "") or "").strip()


def parse_command_runtime(command_text: str, output: str) -> ParsedCommandRuntime:
    category, parsed_data, conclusion = parse_command_output(command_text, output)
    if not isinstance(parsed_data, dict):
        parsed_data = {}
    return ParsedCommandRuntime(
        category=category,
        parsed_data=parsed_data,
        conclusion=conclusion,
        device_profile=extract_device_profile(parsed_data),
    )


def extract_device_profile(parsed_data: dict[str, Any] | Any) -> ParsedDeviceProfile:
    if not isinstance(parsed_data, dict):
        return ParsedDeviceProfile()
    return ParsedDeviceProfile(
        device_name=str(parsed_data.get("device_name") or "").strip(),
        vendor=str(parsed_data.get("vendor") or "").strip(),
        platform=str(parsed_data.get("platform") or "").strip(),
        software_version=str(parsed_data.get("software_version") or "").strip(),
        version_signature=str(parsed_data.get("version_signature") or "").strip(),
    )


def apply_device_profile_to_session_store(store: Any, session_id: str, profile: ParsedDeviceProfile) -> None:
    if not profile.has_value():
        return
    store.update_session_device_profile(
        session_id,
        vendor=profile.vendor.lower() or None,
        platform=profile.platform or None,
        software_version=profile.software_version or None,
        version_signature=profile.version_signature.lower() or None,
    )
    if profile.device_name:
        store.update_session_device_name(session_id, profile.device_name)


def apply_device_profile_to_job_device(device: Any, profile: ParsedDeviceProfile) -> None:
    if not profile.has_value():
        return
    if profile.device_name:
        device.name = profile.device_name
    if profile.vendor:
        device.vendor = profile.vendor
    if profile.platform:
        device.platform = profile.platform
    if profile.software_version:
        device.software_version = profile.software_version
    if profile.version_signature:
        device.version_signature = profile.version_signature.lower()
