from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from . import audit_cli, policy_cli
from .runner import PtyProxyRunner, RunnerConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssh-proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Transparent SSH/login proxy with command policy and audit replay.",
        epilog=textwrap.dedent(
            """\
            Commands:
              ssh-proxy -- <login command...>          run a login command through the proxy
              ssh-proxy audit <subcommand>             inspect sessions, commands, blocks and replay
              ssh-proxy policy <subcommand>            inspect or test command policy

            Audit subcommands:
              list                                     list recent sessions
              show [session_id]                        show session summary
              commands [session_id]                    show command decisions
              blocked [session_id]                     show blocked commands
              replay [session_id]                      print terminal replay text
              raw [session_id]                         print raw audit events

            Policy subcommands:
              list                                     show allow/block rules
              check <command>                          test whether a command is allowed

            Examples:
              ssh-proxy -- smc pam nd ssh 10.1.1.1
              ssh-proxy --audit-dir ~/ssh-proxy-audit -- smc server toc jump01
              ssh-proxy audit list
              ssh-proxy audit blocked
              ssh-proxy policy list
              ssh-proxy policy check "screen-length 0"
              ssh-proxy policy check "reload"

            More help:
              ssh-proxy audit --help
              ssh-proxy policy --help
              ssh-proxy audit replay --help
              ssh-proxy policy check --help
            """
        ),
    )
    parser.add_argument("--audit-dir", type=Path, default=None, help="Directory for session audit files.")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Login command to run. Use '--' before the command, for example: ssh-proxy -- smc pam nd ssh 10.1.1.1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "help":
        return _dispatch_help(argv[1:])

    dispatched = _dispatch_subcommand(argv)
    if dispatched is not None:
        return dispatched

    parser = build_parser()
    args = parser.parse_args(argv)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing login command; usage: ssh-proxy -- <login command...>")

    runner = PtyProxyRunner(RunnerConfig(command=command, audit_dir=args.audit_dir))
    return runner.run()


def _dispatch_help(argv: list[str]) -> int:
    if not argv:
        build_parser().print_help()
        return 0
    if argv[0] == "audit":
        return audit_cli.main([*argv[1:], "--help"] if len(argv) > 1 else ["--help"], prog="ssh-proxy audit")
    if argv[0] == "policy":
        return policy_cli.main([*argv[1:], "--help"] if len(argv) > 1 else ["--help"], prog="ssh-proxy policy")

    parser = build_parser()
    parser.error(f"unknown help topic: {argv[0]}")
    return 2


def _dispatch_subcommand(argv: list[str]) -> int | None:
    if not argv:
        return None

    first = argv[0]
    if first == "audit":
        return audit_cli.main(argv[1:], prog="ssh-proxy audit")
    if first == "policy":
        return policy_cli.main(argv[1:], prog="ssh-proxy policy")

    if first == "--audit-dir" and len(argv) >= 3 and argv[2] == "audit":
        return audit_cli.main(["--audit-dir", argv[1], *argv[3:]], prog="ssh-proxy audit")
    if first.startswith("--audit-dir=") and len(argv) >= 2 and argv[1] == "audit":
        audit_dir = first.split("=", 1)[1]
        return audit_cli.main(["--audit-dir", audit_dir, *argv[2:]], prog="ssh-proxy audit")

    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
