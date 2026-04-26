from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import PtyProxyRunner, RunnerConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssh-proxy",
        description="Run a custom SSH/login command under a transparent PTY policy and audit proxy.",
    )
    parser.add_argument("--audit-dir", type=Path, default=None, help="Directory for session audit files.")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Login command to run. Use '--' before the command, for example: ssh-proxy -- smc pam nd ssh 10.1.1.1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing login command; usage: ssh-proxy -- <login command...>")

    runner = PtyProxyRunner(RunnerConfig(command=command, audit_dir=args.audit_dir))
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
