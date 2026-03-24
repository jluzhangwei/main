from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Optional

from app.models.schemas import DeviceProtocol, Session

try:
    from netmiko import ConnectHandler
except Exception:  # pragma: no cover - optional dependency during local dry run
    ConnectHandler = None

try:
    import paramiko
except Exception:  # pragma: no cover - optional dependency during local dry run
    paramiko = None


class DeviceAdapter(ABC):
    def __init__(self, session: Session, *, allow_simulation: bool = True):
        self.session = session
        self.allow_simulation = allow_simulation
        self.last_command_meta: dict[str, object] = {}

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
        self._paging_configured = False
        self._use_expect_fallback = False
        self._expect_fallback_enabled = (
            os.getenv("NETOPS_ENABLE_EXPECT_FALLBACK", "0").strip().lower() in {"1", "true", "yes"}
        )
        self._jump_client = None

    async def connect(self) -> None:
        simulation_only = (
            os.getenv("NETOPS_SIMULATION_ONLY", "0").strip().lower() in {"1", "true", "yes"}
        )
        if self.allow_simulation and simulation_only:
            self.connection_error = RuntimeError("simulation-only mode enabled")
            return

        if self._use_expect_fallback:
            return

        if self.conn:
            try:
                alive = await asyncio.to_thread(self.conn.is_alive)
                if isinstance(alive, dict):
                    if bool(alive.get("is_alive")):
                        return
                elif bool(alive):
                    return
            except Exception:
                self.conn = None

        if ConnectHandler and self.session.device.username and self.session.device.password:
            candidates = self._candidate_device_types()
            banner_reset_seen = False
            jump_setup_failed = False
            for candidate in candidates:
                for _ in range(2):
                    jump_sock = None
                    try:
                        jump_sock = await self._open_jump_sock_if_needed()
                        connect_kwargs = dict(
                            device_type=candidate,
                            host=self.session.device.host,
                            username=self.session.device.username,
                            password=self.session.device.password,
                            port=self.session.device.port,
                            timeout=10,
                            conn_timeout=15,
                            auth_timeout=15,
                            banner_timeout=45,
                            keepalive=30,
                        )
                        if jump_sock is not None:
                            connect_kwargs["sock"] = jump_sock
                        self.conn = await asyncio.to_thread(
                            ConnectHandler,
                            **connect_kwargs,
                        )
                        jump_sock = None
                        if not await self._probe_device_identity(candidate):
                            try:
                                await asyncio.to_thread(self.conn.disconnect)
                            except Exception:
                                pass
                            self.conn = None
                            self.connection_error = RuntimeError(f"device type probe mismatch: {candidate}")
                            continue
                        self.connection_error = None
                        self.session.device.device_type = candidate
                        self._refresh_vendor_hint_from_device_type(candidate)
                        self._paging_configured = False
                        await self._ensure_terminal_paging_off()
                        await self._refresh_device_name_from_prompt()
                        return
                    except Exception as exc:
                        if jump_sock is not None:
                            try:
                                await asyncio.to_thread(jump_sock.close)
                            except Exception:
                                pass
                        self.conn = None
                        self.connection_error = exc
                        if self._is_jump_setup_error(exc):
                            jump_setup_failed = True
                            break
                        if self._is_auth_error(exc):
                            break
                        if self._is_banner_reset_error(exc):
                            banner_reset_seen = True
                            # Remote side reset before SSH banner; avoid rapid hammering.
                            await asyncio.sleep(1.2)
                            break
                        await asyncio.sleep(0.35)

                if banner_reset_seen:
                    # Banner reset is transport-side and independent from device_type probing.
                    # Stop rotating candidates to reduce repeated resets.
                    break
                if jump_setup_failed:
                    break

            if not self.allow_simulation:
                if self._expect_fallback_enabled and self._can_use_expect_fallback():
                    ok, error_message = await asyncio.to_thread(self._probe_expect_login)
                    if ok:
                        self._use_expect_fallback = True
                        self.connection_error = None
                        self._paging_configured = False
                        return
                    if error_message:
                        self.connection_error = RuntimeError(error_message)
                raise self.connection_error
        elif not self.allow_simulation:
            if self._expect_fallback_enabled and self._can_use_expect_fallback():
                ok, error_message = await asyncio.to_thread(self._probe_expect_login)
                if ok:
                    self._use_expect_fallback = True
                    self.connection_error = None
                    self._paging_configured = False
                    return
                if error_message:
                    raise RuntimeError(error_message)
            raise RuntimeError("SSH connection cannot be established without netmiko and credentials")

    async def run_command(self, command: str) -> str:
        translated = self._translate_command(command)
        translated = self._normalize_interface_tokens(translated)
        normalized_translated = translated.strip().lower()
        command_parts = self._split_commands(translated)
        workflow_commands = self._extract_config_workflow(translated)
        self.last_command_meta = {
            "original_command": command,
            "translated_command": translated,
            "effective_command": translated,
            "retry_used": False,
            "retry_from": None,
            "retry_to": None,
            "simulated": False,
        }
        if not self.conn:
            try:
                await self.connect()
            except Exception:
                pass

        if self._use_expect_fallback:
            try:
                prepared_commands = self._prepare_expect_commands(command_parts or [translated])
                output = await asyncio.to_thread(
                    self._run_expect_commands,
                    prepared_commands,
                    True,
                )
                self.last_command_meta.update({"effective_command": translated, "retry_used": False})
                self._refresh_vendor_hint_from_output(output)
                self._refresh_device_name_from_output(output)
                return output
            except Exception as exc:
                if self.allow_simulation:
                    self.last_command_meta.update(
                        {
                            "effective_command": command,
                            "simulated": True,
                            "execution_error": str(exc),
                        }
                    )
                    return _simulate_cli_output(command)
                raise

        for attempt in range(2):
            if not self.conn:
                break
            try:
                return await self._run_command_on_active_connection(
                    original_command=command,
                    translated=translated,
                    normalized_translated=normalized_translated,
                    command_parts=command_parts,
                    workflow_commands=workflow_commands,
                )
            except Exception as exc:
                if attempt == 0 and self._is_retryable_execution_error(exc):
                    await self._reset_connection()
                    try:
                        await self.connect()
                        continue
                    except Exception:
                        pass
                if self.allow_simulation:
                    self.last_command_meta.update(
                        {
                            "effective_command": command,
                            "simulated": True,
                            "execution_error": str(exc),
                        }
                    )
                    return _simulate_cli_output(command)
                raise

        if not self.allow_simulation:
            reason = self.connection_error or RuntimeError("SSH connection not established")
            raise RuntimeError(f"SSH connection not established: {reason}")

        await asyncio.sleep(0.2)
        self.last_command_meta.update(
            {
                "effective_command": command,
                "simulated": True,
                "execution_error": str(self.connection_error) if self.connection_error else None,
            }
        )
        return _simulate_cli_output(command)

    async def _run_command_on_active_connection(
        self,
        *,
        original_command: str,
        translated: str,
        normalized_translated: str,
        command_parts: list[str],
        workflow_commands: list[str],
    ) -> str:
        if not self.conn:
            raise RuntimeError("SSH session is not connected")

        if normalized_translated == "enable":
            if hasattr(self.conn, "check_enable_mode"):
                try:
                    already_enabled = await asyncio.to_thread(self.conn.check_enable_mode)
                    if bool(already_enabled):
                        return "Already in privileged mode."
                except Exception:
                    pass
            try:
                output = await asyncio.to_thread(self.conn.enable)
                await self._ensure_terminal_paging_off(force=True)
                self.last_command_meta.update({"effective_command": translated, "retry_used": False})
                if isinstance(output, str) and output.strip():
                    return output
                return "Entered privileged mode."
            except Exception as exc:
                if self._is_prompt_detection_error(exc):
                    output = await asyncio.to_thread(self.conn.send_command_timing, "enable", read_timeout=30)
                    await self._ensure_terminal_paging_off(force=True)
                    self.last_command_meta.update({"effective_command": translated, "retry_used": False})
                    if isinstance(output, str) and output.strip():
                        return output
                    return "Entered privileged mode."
                raise

        if workflow_commands:
            if self._is_pure_config_workflow(command_parts) and not self._should_force_compound_timing(command_parts):
                try:
                    self.last_command_meta.update({"effective_command": translated, "retry_used": False})
                    return await asyncio.to_thread(
                        self.conn.send_config_set,
                        workflow_commands,
                        cmd_verify=False,
                        read_timeout=45,
                    )
                except Exception as exc:
                    if self._is_prompt_detection_error(exc):
                        self.last_command_meta.update({"effective_command": translated, "retry_used": False})
                        return await self._run_compound_timing(command_parts)
                    raise
            self.last_command_meta.update({"effective_command": translated, "retry_used": False})
            return await self._run_compound_timing(command_parts)

        if len(command_parts) > 1:
            self.last_command_meta.update({"effective_command": translated, "retry_used": False})
            return await self._run_compound_timing(command_parts)

        if len(command_parts) == 1 and self._looks_like_config_command(command_parts[0]):
            self.last_command_meta.update({"effective_command": translated, "retry_used": False})
            return await asyncio.to_thread(self.conn.send_command_timing, translated, read_timeout=30)

        try:
            output = await asyncio.to_thread(self.conn.send_command, translated)
            retried, effective_command, retry_used, retry_error = await self._retry_on_cli_error(
                original_command,
                translated,
                output,
            )
            self.last_command_meta.update(
                {
                    "effective_command": effective_command,
                    "retry_used": retry_used,
                    "retry_from": translated if retry_used else None,
                    "retry_to": effective_command if retry_used else None,
                    "retry_error": retry_error if retry_used else None,
                }
            )
            self._refresh_vendor_hint_from_output(retried)
            self._refresh_device_name_from_output(retried)
            return retried
        except Exception as exc:
            # Huawei ping often has delayed prompt return; timing mode is safer as a fallback.
            if self._is_prompt_detection_error(exc):
                output = await asyncio.to_thread(self.conn.send_command_timing, translated, read_timeout=30)
                retried, effective_command, retry_used, retry_error = await self._retry_on_cli_error(
                    original_command,
                    translated,
                    output,
                )
                self.last_command_meta.update(
                    {
                        "effective_command": effective_command,
                        "retry_used": retry_used,
                        "retry_from": translated if retry_used else None,
                        "retry_to": effective_command if retry_used else None,
                        "retry_error": retry_error if retry_used else None,
                    }
                )
                self._refresh_vendor_hint_from_output(retried)
                self._refresh_device_name_from_output(retried)
                return retried
            raise

    async def close(self) -> None:
        if self.conn:
            try:
                await asyncio.to_thread(self.conn.disconnect)
            except Exception:
                pass
            finally:
                self.conn = None
                self._paging_configured = False
        await asyncio.to_thread(self._close_jump_client)
        self._use_expect_fallback = False

    def _can_use_expect_fallback(self) -> bool:
        return bool(
            self.session.device.username
            and self.session.device.password
            and shutil.which("ssh")
            and shutil.which("expect")
        )

    def _jump_enabled(self) -> bool:
        return bool(str(self.session.device.jump_host or "").strip())

    def _resolved_jump_host(self) -> str:
        return str(self.session.device.jump_host or "").strip()

    def _resolved_jump_port(self) -> int:
        try:
            port = int(self.session.device.jump_port or 22)
        except Exception:
            port = 22
        return port if port > 0 else 22

    def _resolved_jump_username(self) -> str:
        return str(self.session.device.jump_username or self.session.device.username or "").strip()

    def _resolved_jump_password(self) -> str:
        raw = self.session.device.jump_password
        if raw is None or not str(raw).strip():
            raw = self.session.device.password
        return str(raw or "")

    async def _open_jump_sock_if_needed(self):
        if not self._jump_enabled():
            return None
        return await asyncio.to_thread(self._open_jump_channel)

    def _open_jump_channel(self):
        self._ensure_jump_client()
        if not self._jump_client:
            return None
        transport = self._jump_client.get_transport()
        if transport is None or not transport.is_active():
            self._close_jump_client()
            raise RuntimeError("Jump host transport is not active")
        try:
            return transport.open_channel(
                "direct-tcpip",
                (str(self.session.device.host), int(self.session.device.port or 22)),
                ("127.0.0.1", 0),
            )
        except Exception as exc:
            raise RuntimeError(f"Jump host tunnel open failed: {exc}") from exc

    def _ensure_jump_client(self) -> None:
        if not self._jump_enabled():
            self._close_jump_client()
            return
        if paramiko is None:
            raise RuntimeError("Jump host requires paramiko to be installed")

        host = self._resolved_jump_host()
        user = self._resolved_jump_username()
        password = self._resolved_jump_password()
        port = self._resolved_jump_port()
        if not host:
            raise RuntimeError("Jump host address is empty")
        if not user:
            raise RuntimeError("Jump host username is required")

        if self._jump_client:
            transport = self._jump_client.get_transport()
            if transport is not None and transport.is_active():
                return
            self._close_jump_client()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        use_password = bool(password.strip())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password if use_password else None,
                timeout=12,
                banner_timeout=45,
                auth_timeout=20,
                look_for_keys=not use_password,
                allow_agent=not use_password,
            )
        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            raise RuntimeError(f"Jump host SSH login failed: {exc}") from exc
        self._jump_client = client

    def _close_jump_client(self) -> None:
        if self._jump_client is None:
            return
        try:
            self._jump_client.close()
        except Exception:
            pass
        self._jump_client = None

    def _probe_expect_login(self) -> tuple[bool, str | None]:
        try:
            self._run_expect_commands([], configure_paging=False)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _run_expect_commands(self, commands: list[str], configure_paging: bool = True) -> str:
        host = (self.session.device.host or "").strip()
        user = (self.session.device.username or "").strip()
        password = self.session.device.password or ""
        port = int(self.session.device.port or 22)
        jump_host = self._resolved_jump_host()
        jump_port = self._resolved_jump_port()
        jump_user = self._resolved_jump_username()
        jump_password = self._resolved_jump_password()
        if not host or not user:
            raise RuntimeError("SSH expect fallback requires host and username")
        if jump_host and not jump_user:
            raise RuntimeError("SSH expect fallback requires jump host username")

        final_commands = [cmd.strip() for cmd in commands if cmd and cmd.strip()]
        if configure_paging and not self._paging_configured:
            paging_candidates = self._paging_disable_candidates()
            if paging_candidates:
                final_commands.insert(0, paging_candidates[0])
            self._paging_configured = True

        script = r'''
set timeout 45
match_max 262144
log_user 0
set host $env(NETOPS_EXPECT_HOST)
set port $env(NETOPS_EXPECT_PORT)
set user $env(NETOPS_EXPECT_USER)
set pass $env(NETOPS_EXPECT_PASSWORD)
set jump_host $env(NETOPS_EXPECT_JUMP_HOST)
set jump_port $env(NETOPS_EXPECT_JUMP_PORT)
set jump_user $env(NETOPS_EXPECT_JUMP_USER)
set jump_pass $env(NETOPS_EXPECT_JUMP_PASSWORD)
set cmds [split $env(NETOPS_EXPECT_COMMANDS) "\n"]
set confirm_default [string toupper $env(NETOPS_EXPECT_CONFIRM_DEFAULT)]
if {$confirm_default ne "Y" && $confirm_default ne "N"} {
  set confirm_default "Y"
}
set prompt_re {(<[^>\r\n]+>|\[[^\]\r\n]+\]|[A-Za-z0-9._/-]{1,128}(\([^)]+\))?[>#])\s*$}
set yn_prompt_re {(?i)(\[(yes/no|y/n|y\(yes\)/n\(no\)(/c\(cancel\))?)\]|(yes/no|y/n|y\(yes\)/n\(no\))|are you sure|continue\?|confirm\?)\s*:?\s*$}
set more_prompt_re {(?i)(----\s*more\s*----|--more--|<---\s*more\s*--->|press any key to continue|press .* key to continue)}
if {[string trim $jump_host] ne ""} {
  if {[string trim $jump_port] eq ""} {
    set jump_port "22"
  }
  set jump_target "${jump_user}@${jump_host}:${jump_port}"
  spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o ConnectTimeout=10 -J $jump_target -p $port $user@$host
} else {
  spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o ConnectTimeout=10 -p $port $user@$host
}
set jump_pass_sent 0
expect {
  -re "(?i)password:" {
    if {[string trim $jump_host] ne "" && $jump_pass_sent == 0 && [string trim $jump_pass] ne ""} {
      send -- "$jump_pass\r"
      set jump_pass_sent 1
    } else {
      send -- "$pass\r"
    }
    exp_continue
  }
  -re "(?i)permission denied" { puts "__AUTH_FAILED__"; exit 13 }
  -re "(?i)connection closed by remote host" { puts "__SSH_CLOSED__"; exit 14 }
  -re "(?i)connection reset" { puts "__SSH_RESET__"; exit 15 }
  -re "(?i)connection timed out|operation timed out|no route to host|host unreachable" { puts "__SSH_TIMEOUT__"; exit 16 }
  -re $more_prompt_re { send -- " "; exp_continue }
  -re $yn_prompt_re { send -- "$confirm_default\r"; exp_continue }
  -re $prompt_re {}
  timeout { puts "__SSH_TIMEOUT__"; exit 16 }
  eof { puts "__SSH_EOF__"; exit 17 }
}
set all ""
foreach cmd $cmds {
  if {[string trim $cmd] eq ""} {
    continue
  }
  send -- "$cmd\r"
  expect {
    -re $more_prompt_re {
      send -- " "
      exp_continue
    }
    -re $yn_prompt_re {
      send -- "$confirm_default\r"
      exp_continue
    }
    -re $prompt_re {
      append all "\n# $cmd\n$expect_out(buffer)\n"
    }
    timeout {
      append all "\n# $cmd\n__CMD_TIMEOUT__\n"
      break
    }
    eof {
      append all "\n# $cmd\n__SSH_EOF__\n"
      break
    }
  }
}
send -- "quit\r"
expect eof
puts $all
'''
        env = os.environ.copy()
        env["NETOPS_EXPECT_HOST"] = host
        env["NETOPS_EXPECT_PORT"] = str(port)
        env["NETOPS_EXPECT_USER"] = user
        env["NETOPS_EXPECT_PASSWORD"] = password
        env["NETOPS_EXPECT_JUMP_HOST"] = jump_host
        env["NETOPS_EXPECT_JUMP_PORT"] = str(jump_port)
        env["NETOPS_EXPECT_JUMP_USER"] = jump_user
        env["NETOPS_EXPECT_JUMP_PASSWORD"] = jump_password
        env["NETOPS_EXPECT_COMMANDS"] = "\n".join(final_commands)
        env["NETOPS_EXPECT_CONFIRM_DEFAULT"] = "Y"
        result = subprocess.run(
            [
                "expect",
                "-c",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=70,
            env=env,
        )
        out = self._sanitize_expect_output((result.stdout or "").strip())
        err = self._sanitize_expect_output((result.stderr or "").strip())
        if result.returncode == 0:
            return out
        message = out or err or f"expect exited with code {result.returncode}"
        lowered = message.lower()
        if "__AUTH_FAILED__" in message or "permission denied" in lowered or "cannot log on" in lowered:
            raise RuntimeError("SSH authentication failed: username/password rejected by device")
        if "__SSH_TIMEOUT__" in message:
            raise RuntimeError("SSH connection timeout in expect fallback")
        if "__SSH_CLOSED__" in message or "__SSH_RESET__" in message:
            raise RuntimeError("SSH connection closed/reset by remote host")
        raise RuntimeError(message)

    def _sanitize_expect_output(self, text: str) -> str:
        if not text:
            return text
        lines: list[str] = []
        for raw in text.splitlines():
            line = raw.rstrip()
            lowered = line.lower()
            if lowered.startswith("spawn ssh "):
                continue
            if "warning: permanently added" in lowered:
                continue
            if "password:" in lowered:
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _prepare_expect_commands(self, commands: list[str]) -> list[str]:
        parts = [str(cmd or "").strip() for cmd in commands if str(cmd or "").strip()]
        if not parts:
            return []

        prepared: list[str] = []
        in_config = False
        for command in parts:
            normalized = command.lower()
            if normalized in {"configure terminal", "system-view"}:
                prepared.append(command)
                in_config = True
                continue
            if normalized in {"end", "return"}:
                prepared.append(command)
                in_config = False
                continue
            if normalized == "exit":
                prepared.append(command)
                in_config = False
                continue

            if self._looks_like_config_command(command):
                if not in_config:
                    prepared.append(self._config_enter_command())
                    in_config = True
                prepared.append(command)
                continue

            if in_config and (self._is_exec_mode_only_command(command) or self._looks_like_readonly_command(command)):
                prepared.append(self._config_exit_command())
                in_config = False
            prepared.append(command)

        if in_config:
            prepared.append(self._config_exit_command())
        return prepared

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

    def _candidate_device_types(self) -> list[str]:
        if self.session.device.protocol == DeviceProtocol.telnet:
            return [self._resolve_device_type()]

        vendor = (self.session.device.vendor or "").strip().lower()
        current = (self.session.device.device_type or "").strip().lower()
        candidates: list[str] = []

        if current and current not in {"autodetect", "auto", "unknown"}:
            candidates.append(current)
        if "huawei" in vendor:
            candidates.append("huawei")
        if "arista" in vendor:
            candidates.append("arista_eos")
        if "cisco" in vendor:
            candidates.extend(["cisco_ios", "cisco_xe"])

        candidates.extend(["cisco_ios", "arista_eos", "cisco_xe", "huawei"])

        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = item.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _refresh_vendor_hint_from_device_type(self, device_type: str) -> None:
        normalized = (device_type or "").strip().lower()
        if normalized == "huawei":
            self.session.device.vendor = "huawei"
        elif normalized == "arista_eos":
            self.session.device.vendor = "arista"
        elif normalized in {"cisco_ios", "cisco_xe"}:
            self.session.device.vendor = "cisco_like"

    def _is_auth_error(self, exc: Exception) -> bool:
        lowered = str(exc).lower()
        return any(
            token in lowered
            for token in [
                "authentication failed",
                "permission denied",
                "auth failed",
                "login invalid",
            ]
        )

    def _is_jump_setup_error(self, exc: Exception) -> bool:
        lowered = str(exc).lower()
        return "jump host" in lowered or "jump tunnel" in lowered

    def _is_banner_reset_error(self, exc: Exception) -> bool:
        lowered = str(exc).lower()
        return (
            "error reading ssh protocol banner" in lowered
            or "connection reset by peer" in lowered
        )

    async def _probe_device_identity(self, device_type: str) -> bool:
        if not self.conn:
            return False

        probe = "display version" if device_type == "huawei" else "show version"
        try:
            output = await asyncio.to_thread(self.conn.send_command, probe)
        except Exception as exc:
            if self._is_prompt_detection_error(exc):
                try:
                    output = await asyncio.to_thread(self.conn.send_command_timing, probe, read_timeout=30)
                except Exception:
                    return False
            else:
                return not self._is_prompt_detection_error(exc)

        if self._looks_like_cli_error(output):
            return False

        return self._probe_output_matches_device_type(output, device_type)

    def _probe_output_matches_device_type(self, output: str, device_type: str) -> bool:
        lowered = output.lower()
        if device_type == "huawei":
            return any(token in lowered for token in ("huawei", "versatile routing platform", "vrp"))
        if device_type == "arista_eos":
            return "arista" in lowered or "eos" in lowered
        if device_type in {"cisco_ios", "cisco_xe"}:
            return "cisco" in lowered
        return True

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
        if len(parts) <= 1:
            return []

        if not any(self._looks_like_config_command(part) for part in parts):
            return []

        workflow = [part for part in parts if not self._is_mode_wrapper(part)]
        return workflow

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
        return normalized in {"configure terminal", "system-view", "return", "exit", "quit", "end"}

    def _should_force_compound_timing(self, parts: list[str]) -> bool:
        if not parts:
            return False
        vendor = (self.session.device.vendor or "").strip().lower()
        device_type = (self.session.device.device_type or "").strip().lower()
        if "huawei" in vendor or device_type == "huawei":
            return True
        has_config = any(self._looks_like_config_command(part) for part in parts)
        has_readonly = any(self._looks_like_readonly_command(part) for part in parts)
        has_exec_only = any(self._is_exec_mode_only_command(part) for part in parts)
        return has_config and (has_readonly or has_exec_only)

    def _is_exec_mode_only_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        return normalized.startswith(
            (
                "write memory",
                "wr mem",
                "copy running-config startup-config",
                "save",
            )
        )

    def _looks_like_readonly_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        return normalized.startswith(("show ", "display ", "ping ", "traceroute ", "tracert "))

    def _is_pure_config_workflow(self, parts: list[str]) -> bool:
        if not parts:
            return False
        has_config = any(self._looks_like_config_command(part) for part in parts)
        if not has_config:
            return False
        for part in parts:
            if self._is_mode_wrapper(part):
                continue
            if self._looks_like_config_command(part):
                continue
            return False
        return True

    def _config_enter_command(self) -> str:
        vendor = (self.session.device.vendor or "").strip().lower()
        return "system-view" if "huawei" in vendor else "configure terminal"

    def _config_exit_command(self) -> str:
        vendor = (self.session.device.vendor or "").strip().lower()
        return "return" if "huawei" in vendor else "end"

    async def _run_compound_timing(self, parts: list[str]) -> str:
        if not self.conn:
            return ""
        outputs: list[str] = []
        in_config = False
        for raw in parts:
            command = raw.strip()
            if not command:
                continue
            normalized = command.lower()

            if normalized in {"configure terminal", "system-view"}:
                out = await asyncio.to_thread(self.conn.send_command_timing, command, read_timeout=45)
                outputs.append(self._wrap_compound_output(command, out))
                in_config = True
                continue

            if normalized in {"end", "return"}:
                out = await asyncio.to_thread(self.conn.send_command_timing, command, read_timeout=45)
                outputs.append(self._wrap_compound_output(command, out))
                in_config = False
                continue

            if normalized == "exit":
                out = await asyncio.to_thread(self.conn.send_command_timing, command, read_timeout=45)
                outputs.append(self._wrap_compound_output(command, out))
                in_config = False
                continue

            if self._looks_like_config_command(command):
                if not in_config:
                    enter = self._config_enter_command()
                    outputs.append(
                        self._wrap_compound_output(
                            enter,
                            await asyncio.to_thread(self.conn.send_command_timing, enter, read_timeout=45),
                        )
                    )
                    in_config = True
                outputs.append(
                    self._wrap_compound_output(
                        command,
                        await asyncio.to_thread(self.conn.send_command_timing, command, read_timeout=45),
                    )
                )
                continue

            if in_config and (self._is_exec_mode_only_command(command) or self._looks_like_readonly_command(command)):
                leave = self._config_exit_command()
                outputs.append(
                    self._wrap_compound_output(
                        leave,
                        await asyncio.to_thread(self.conn.send_command_timing, leave, read_timeout=45),
                    )
                )
                in_config = False

            outputs.append(
                self._wrap_compound_output(
                    command,
                    await asyncio.to_thread(self.conn.send_command_timing, command, read_timeout=45),
                )
            )

        if in_config:
            leave = self._config_exit_command()
            outputs.append(
                self._wrap_compound_output(
                    leave,
                    await asyncio.to_thread(self.conn.send_command_timing, leave, read_timeout=45),
                )
            )

        return "\n".join(part for part in outputs if isinstance(part, str) and part.strip())

    def _wrap_compound_output(self, command: str, output: str) -> str:
        body = str(output or "").strip()
        if body:
            return f"# {command}\n{body}"
        return f"# {command}\n(无回显)"

    def _is_prompt_detection_error(self, exc: Exception) -> bool:
        return "Pattern not detected" in str(exc)

    def _is_retryable_execution_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return self._is_prompt_detection_error(exc) or any(
            token in text
            for token in (
                "session not alive",
                "socket is closed",
                "channel closed",
                "eof",
                "timed out",
                "read timeout",
            )
        )

    async def _reset_connection(self) -> None:
        if not self.conn:
            return
        try:
            await asyncio.to_thread(self.conn.disconnect)
        except Exception:
            pass
        finally:
            self.conn = None
            self._paging_configured = False

    async def _ensure_terminal_paging_off(self, *, force: bool = False) -> None:
        if not self.conn:
            return
        if self._paging_configured and not force:
            return
        sender = getattr(self.conn, "send_command_timing", None)
        if not callable(sender):
            self._paging_configured = True
            return

        commands = self._paging_disable_candidates()
        for cmd in commands:
            try:
                output = await asyncio.to_thread(sender, cmd, read_timeout=20)
            except TypeError:
                try:
                    output = await asyncio.to_thread(sender, cmd)
                except Exception:
                    continue
            except Exception:
                continue

            if isinstance(output, str) and self._looks_like_cli_error(output):
                continue
            self._paging_configured = True
            return

        self._paging_configured = True

    def _paging_disable_candidates(self) -> list[str]:
        vendor = (self.session.device.vendor or "").strip().lower()
        device_type = (self.session.device.device_type or "").strip().lower()

        is_huawei = "huawei" in vendor or device_type == "huawei"
        is_arista = "arista" in vendor or "arista" in device_type
        is_cisco = "cisco" in vendor or device_type in {"cisco_ios", "cisco_xe"}

        if is_huawei:
            return ["screen-length 0 temporary", "screen-length 0"]
        if is_arista or is_cisco:
            return ["terminal length 0"]
        return ["terminal length 0", "screen-length 0 temporary", "screen-length 0"]

    def _normalize_interface_tokens(self, command: str) -> str:
        # Normalize forms like "GigabitEthernet 1/0/6" -> "GigabitEthernet1/0/6"
        return re.sub(
            r"\b(GigabitEthernet|XGigabitEthernet|Ethernet|GE)\s+(\d+(?:/\d+)+(?:\.\d+)?)\b",
            r"\1\2",
            command,
            flags=re.IGNORECASE,
        ).strip()

    async def _retry_on_cli_error(self, original: str, translated: str, output: str) -> tuple[str, str, bool, str | None]:
        if not self.conn:
            return output, translated, False, None
        if not self._looks_like_cli_error(output):
            return output, translated, False, None

        candidates = self._retry_candidates(original, translated)
        original_error = self._extract_cli_error_line(output)
        for candidate in candidates:
            if candidate.strip().lower() == translated.strip().lower():
                continue
            try:
                retried = await asyncio.to_thread(self.conn.send_command, candidate)
            except Exception:
                continue
            if not self._looks_like_cli_error(retried):
                return retried, candidate, True, original_error
        return output, translated, False, original_error

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
                "% invalid input",
                "invalid input",
            ]
        )

    def _extract_cli_error_line(self, output: str) -> str:
        for line in output.splitlines():
            if "error:" in line.lower() or "invalid input" in line.lower() or "unknown command" in line.lower():
                return line.strip()
        return "cli_error"

    def _refresh_vendor_hint_from_output(self, output: str) -> None:
        lowered = output.lower()
        if "huawei versatile routing platform" in lowered or " vrp " in lowered or "huawei ne" in lowered:
            self.session.device.vendor = "huawei"
            if self.session.device.protocol == DeviceProtocol.ssh:
                self.session.device.device_type = "huawei"

    async def _refresh_device_name_from_prompt(self) -> None:
        if not self.conn:
            return
        finder = getattr(self.conn, "find_prompt", None)
        if not callable(finder):
            return
        try:
            prompt = await asyncio.to_thread(finder)
        except Exception:
            return
        device_name = self._extract_device_name_from_prompt(prompt)
        if device_name:
            self.session.device.name = device_name

    def _refresh_device_name_from_output(self, output: str) -> None:
        if not output:
            return
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Prompt-like lines, e.g. "Device-102#" or "Device-102(config)#show version"
            prompt_match = re.match(
                r"^([A-Za-z0-9._-]{1,64})(?:\([^)]+\))?[>#]\s*(?:.*)?$",
                line,
            )
            if prompt_match:
                self.session.device.name = prompt_match.group(1)
                return
            # Explicit name fields occasionally present in platform outputs.
            explicit_match = re.search(
                r"(?i)\b(hostname|sysname|system\s+name)\b\s*[:=]\s*([A-Za-z0-9._-]{1,64})\b",
                line,
            )
            if explicit_match:
                self.session.device.name = explicit_match.group(2)
                return

    def _extract_device_name_from_prompt(self, prompt: str) -> str | None:
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", (prompt or "")).strip()
        if not text:
            return None

        line = text.splitlines()[-1].strip()
        if not line:
            return None

        bracket_match = re.match(r"^[<\[]\s*([A-Za-z0-9._-]{1,64})\s*[>\]]$", line)
        if bracket_match:
            return bracket_match.group(1)

        prompt_match = re.match(r"^([A-Za-z0-9._-]{1,64})(?:\([^)]+\))?[>#]\s*$", line)
        if prompt_match:
            return prompt_match.group(1)

        return None


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
