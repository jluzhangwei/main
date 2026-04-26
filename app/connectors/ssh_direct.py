from __future__ import annotations

import asyncio
from typing import Callable

import asyncssh


class DirectSSHClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = 30,
        debug: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._conn: asyncssh.SSHClientConnection | None = None
        self._debug = debug

    def _dbg(self, message: str) -> None:
        if self._debug:
            self._debug(message)

    async def connect(self) -> None:
        self._dbg(f"[LOGIN] connect {self.username}@{self.host}:{self.port}")
        self._conn = await asyncio.wait_for(
            asyncssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                known_hosts=None,
            ),
            timeout=self.timeout,
        )
        self._dbg("[LOGIN] connected")

    async def exec(self, command: str, timeout: int = 30) -> str:
        if not self._conn:
            raise RuntimeError("SSH connection is not established")
        self._dbg(f"[CMD] {command}")
        result = await asyncio.wait_for(self._conn.run(command, check=False), timeout=timeout)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = (stdout + ("\n" + stderr if stderr else "")).strip()
        if output:
            self._dbg(f"[OUT]\n{output}\n")
        else:
            self._dbg("[OUT] <empty>")
        return output

    async def try_disable_paging(self, vendor: str) -> None:
        cmds = ["terminal length 0"]
        if vendor == "huawei":
            cmds = ["screen-length 0 temporary"]
        for cmd in cmds:
            try:
                await self.exec(cmd, timeout=10)
            except Exception:
                continue

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None
            self._dbg("[LOGIN] connection closed")
