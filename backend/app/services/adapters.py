from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Optional

from app.models.schemas import DeviceProtocol, Session

try:
    from netmiko import ConnectHandler
except Exception:  # pragma: no cover - optional dependency during local dry run
    ConnectHandler = None


class DeviceAdapter(ABC):
    def __init__(self, session: Session, *, allow_simulation: bool = True):
        self.session = session
        self.allow_simulation = allow_simulation

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run_command(self, command: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class SSHAdapter(DeviceAdapter):
    def __init__(self, session: Session, *, allow_simulation: bool = True):
        super().__init__(session, allow_simulation=allow_simulation)
        self.conn = None
        self.connection_error: Optional[Exception] = None

    async def connect(self) -> None:
        if ConnectHandler and self.session.device.username and self.session.device.password:
            for _ in range(2):
                try:
                    self.conn = await asyncio.to_thread(
                        ConnectHandler,
                        device_type=self._resolve_device_type(),
                        host=self.session.device.host,
                        username=self.session.device.username,
                        password=self.session.device.password,
                        port=self.session.device.port,
                        timeout=10,
                        conn_timeout=15,
                        auth_timeout=15,
                        banner_timeout=20,
                    )
                    self.connection_error = None
                    return
                except Exception as exc:
                    self.conn = None
                    self.connection_error = exc
                    await asyncio.sleep(0.5)

            if not self.allow_simulation:
                raise self.connection_error
        elif not self.allow_simulation:
            raise RuntimeError("SSH connection cannot be established without netmiko and credentials")

    async def run_command(self, command: str) -> str:
        translated = self._translate_command(command)
        translated = self._normalize_interface_tokens(translated)
        workflow_commands = self._extract_config_workflow(translated)

        if self.conn and workflow_commands:
            try:
                return await asyncio.to_thread(self.conn.send_config_set, workflow_commands)
            except Exception as exc:
                if self.allow_simulation:
                    return _simulate_cli_output(command)
                raise

        if self.conn:
            try:
                output = await asyncio.to_thread(self.conn.send_command, translated)
                retried = await self._retry_on_cli_error(command, translated, output)
                self._refresh_vendor_hint_from_output(retried)
                return retried
            except Exception as exc:
                # Huawei ping often has delayed prompt return; timing mode is safer as a fallback.
                if self._is_prompt_detection_error(exc):
                    output = await asyncio.to_thread(self.conn.send_command_timing, translated, read_timeout=30)
                    retried = await self._retry_on_cli_error(command, translated, output)
                    self._refresh_vendor_hint_from_output(retried)
                    return retried
                if self.allow_simulation:
                    return _simulate_cli_output(command)
                raise

        if not self.allow_simulation:
            reason = self.connection_error or RuntimeError("SSH connection not established")
            raise RuntimeError(f"SSH connection not established: {reason}")

        await asyncio.sleep(0.2)
        return _simulate_cli_output(command)

    async def close(self) -> None:
        if self.conn:
            try:
                await asyncio.to_thread(self.conn.disconnect)
            except Exception:
                pass

    def _resolve_device_type(self) -> str:
        if self.session.device.protocol != DeviceProtocol.telnet:
            return self.session.device.device_type

        base = self.session.device.device_type
        if base.endswith("_telnet"):
            return base

        telnet_map = {
            "huawei": "huawei_telnet",
            "cisco_ios": "cisco_ios_telnet",
            "cisco_xe": "cisco_xe_telnet",
            "arista_eos": "arista_eos_telnet",
        }
        return telnet_map.get(base, f"{base}_telnet")

    def _translate_command(self, command: str) -> str:
        vendor = self.session.device.vendor.strip().lower()
        if ";" in command:
            parts = [self._translate_single_command(part) for part in self._split_commands(command)]
            return " ; ".join(parts)
        return self._translate_single_command(command)

    def _translate_single_command(self, command: str) -> str:
        vendor = self.session.device.vendor.strip().lower()
        normalized = command.strip().lower()

        if "huawei" not in vendor:
            return command.strip()

        mapping = {
            "show arp": "display arp",
            "show ip interface brief": "display ip interface brief",
            "show interface status": "display interface brief",
            "show ip route": "display ip routing-table",
            "show ip route 0.0.0.0": "display ip routing-table 0.0.0.0",
            "configure terminal": "system-view",
            "no shutdown": "undo shutdown",
        }
        if normalized.startswith("show running-config interface "):
            interface_name = command.strip().split()[-1]
            return f"display current-configuration interface {interface_name}"
        if normalized.startswith("show "):
            return f"display {command.strip()[5:]}"
        return mapping.get(normalized, command.strip())

    def _split_commands(self, command: str) -> list[str]:
        return [part.strip() for part in command.split(";") if part.strip()]

    def _extract_config_workflow(self, command: str) -> list[str]:
        parts = self._split_commands(command)
        if not parts:
            return []

        if not any(self._looks_like_config_command(part) for part in parts):
            return []

        workflow = [part for part in parts if not self._is_mode_wrapper(part)]
        return workflow or parts

    def _looks_like_config_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        return any(
            normalized.startswith(prefix)
            for prefix in (
                "configure terminal",
                "system-view",
                "interface ",
                "no shutdown",
                "undo shutdown",
                "shutdown",
                "commit",
            )
        )

    def _is_mode_wrapper(self, command: str) -> bool:
        normalized = command.strip().lower()
        return normalized in {"configure terminal", "system-view", "return", "exit"}

    def _is_prompt_detection_error(self, exc: Exception) -> bool:
        return "Pattern not detected" in str(exc)

    def _normalize_interface_tokens(self, command: str) -> str:
        # Normalize forms like "GigabitEthernet 1/0/6" -> "GigabitEthernet1/0/6"
        return re.sub(
            r"\b(GigabitEthernet|XGigabitEthernet|Ethernet|GE)\s+(\d+(?:/\d+)+(?:\.\d+)?)\b",
            r"\1\2",
            command,
            flags=re.IGNORECASE,
        ).strip()

    async def _retry_on_cli_error(self, original: str, translated: str, output: str) -> str:
        if not self.conn:
            return output
        if not self._looks_like_cli_error(output):
            return output

        candidates = self._retry_candidates(original, translated)
        for candidate in candidates:
            if candidate.strip().lower() == translated.strip().lower():
                continue
            try:
                retried = await asyncio.to_thread(self.conn.send_command, candidate)
            except Exception:
                continue
            if not self._looks_like_cli_error(retried):
                return retried
        return output

    def _retry_candidates(self, original: str, translated: str) -> list[str]:
        candidates: list[str] = []

        normalized_original = original.strip().lower()
        normalized_translated = translated.strip().lower()

        if normalized_original.startswith("show "):
            candidates.append(self._normalize_interface_tokens(f"display {original.strip()[5:]}"))
        if normalized_translated.startswith("show "):
            candidates.append(self._normalize_interface_tokens(f"display {translated.strip()[5:]}"))
        if normalized_translated.startswith("display "):
            candidates.append(self._normalize_interface_tokens(f"show {translated.strip()[8:]}"))
        if "gigabitethernet" in normalized_translated:
            candidates.append(self._normalize_interface_tokens(re.sub(r"(?i)gigabitethernet", "Ethernet", translated)))
            candidates.append(self._normalize_interface_tokens(re.sub(r"(?i)gigabitethernet", "GE", translated)))

        candidates.append(self._normalize_interface_tokens(translated))
        return list(dict.fromkeys([item for item in candidates if item.strip()]))

    def _looks_like_cli_error(self, output: str) -> bool:
        lowered = output.lower()
        return any(
            token in lowered
            for token in [
                "unrecognized command",
                "wrong parameter",
                "incomplete command",
                "error:",
                "unknown command",
            ]
        )

    def _refresh_vendor_hint_from_output(self, output: str) -> None:
        lowered = output.lower()
        if "huawei versatile routing platform" in lowered or " vrp " in lowered or "huawei ne" in lowered:
            self.session.device.vendor = "huawei"
            if self.session.device.protocol == DeviceProtocol.ssh:
                self.session.device.device_type = "huawei"


class APIAdapter(DeviceAdapter):
    async def connect(self) -> None:
        await asyncio.sleep(0.05)

    async def run_command(self, command: str) -> str:
        await asyncio.sleep(0.15)
        return _simulate_api_output(command)

    async def close(self) -> None:
        await asyncio.sleep(0.01)


def build_adapter(session: Session, *, allow_simulation: bool = True) -> DeviceAdapter:
    if session.device.protocol == DeviceProtocol.api:
        return APIAdapter(session, allow_simulation=allow_simulation)
    return SSHAdapter(session, allow_simulation=allow_simulation)


def _simulate_cli_output(command: str) -> str:
    cmd = command.lower()
    if cmd.startswith("ping"):
        return "Success rate is 0 percent (0/5), 100% packet loss"
    if "show ip interface brief" in cmd or "display ip interface brief" in cmd:
        return """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0/0    10.0.0.1        YES manual up                    up
GigabitEthernet0/0/1    10.0.1.1        YES manual administratively down down
"""
    if "show interface status" in cmd or "display interface brief" in cmd:
        return "Gi0/0/1 is down, line protocol is down"
    if "show ip route 0.0.0.0" in cmd or "display ip routing-table 0.0.0.0" in cmd:
        return "% Network not in table"
    if "show ip route" in cmd or "display ip routing-table" in cmd:
        return "Codes: C - connected, S - static\nC 10.0.0.0/24 is directly connected, Gi0/0/0"
    if "show running-config interface" in cmd or "display current-configuration interface" in cmd:
        return """#
interface Ethernet1/0/6
 shutdown
#
return
"""
    if "show arp" in cmd or "display arp" in cmd:
        return "Protocol Address Age (min) Hardware Addr Type Interface"
    if "configure terminal" in cmd or "system-view" in cmd:
        return "Enter configuration commands, one per line. End with CNTL/Z."
    if "undo shutdown" in cmd or "no shutdown" in cmd:
        return "Interface state changed."

    return "Command completed."


def _simulate_api_output(command: str) -> str:
    cmd = command.lower()
    if "interface" in cmd:
        return '{"interfaces": [{"name": "GE0/0/1", "status": "down"}]}'
    if "route" in cmd:
        return '{"routes": [], "default_route": null}'
    if "ping" in cmd:
        return '{"success": false, "loss": 100}'
    return '{"result": "ok"}'
