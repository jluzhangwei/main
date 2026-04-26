from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audit_store import audit_base, list_sessions, read_commands, read_events, read_replay, resolve_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssh-proxy-audit", description="Inspect ssh-proxy audit sessions.")
    parser.add_argument("--audit-dir", type=Path, default=None, help="Audit base directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List recent sessions.")
    p_list.add_argument("-n", "--limit", type=int, default=20)

    p_show = sub.add_parser("show", help="Show one session summary.")
    p_show.add_argument("session_id", nargs="?", help="Session id or prefix. Defaults to latest.")

    p_cmd = sub.add_parser("commands", help="Show command decisions.")
    p_cmd.add_argument("session_id", nargs="?", help="Session id or prefix. Defaults to latest.")

    p_blocked = sub.add_parser("blocked", help="Show blocked commands.")
    p_blocked.add_argument("session_id", nargs="?", help="Session id or prefix. Defaults to latest.")

    p_replay = sub.add_parser("replay", help="Print terminal replay text.")
    p_replay.add_argument("session_id", nargs="?", help="Session id or prefix. Defaults to latest.")
    p_replay.add_argument("--json", action="store_true", help="Print raw JSONL replay rows.")

    p_raw = sub.add_parser("raw", help="Print raw events JSONL.")
    p_raw.add_argument("session_id", nargs="?", help="Session id or prefix. Defaults to latest.")
    p_raw.add_argument("--event", help="Filter by event type.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base = audit_base(args.audit_dir)

    if args.command == "list":
        _print_sessions(list_sessions(base, args.limit))
        return 0

    session = resolve_session(base, getattr(args, "session_id", None))
    if args.command == "show":
        _print_summary(session.metadata)
        print(f"session_dir: {session.session_dir}")
        return 0
    if args.command == "commands":
        _print_commands(read_commands(session), only_blocked=False)
        return 0
    if args.command == "blocked":
        _print_commands(read_commands(session), only_blocked=True)
        return 0
    if args.command == "replay":
        rows = read_replay(session)
        if args.json:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        else:
            for row in rows:
                data = str(row.get("data") or "")
                if data:
                    print(data, end="" if data.endswith("\n") else "\n")
        return 0
    if args.command == "raw":
        for row in read_events(session, args.event):
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _print_sessions(sessions: list[Any]) -> None:
    print(_fmt_row(["SESSION", "STARTED", "EXIT", "TARGET", "COMMAND"]))
    for session in sessions:
        meta = session.metadata
        print(
            _fmt_row(
                [
                    session.session_id[:12],
                    str(meta.get("started_at") or "-"),
                    str(meta.get("exit_code")),
                    str(meta.get("target_hint") or "-"),
                    " ".join(str(x) for x in meta.get("login_command") or []),
                ]
            )
        )


def _print_summary(meta: dict[str, Any]) -> None:
    print(f"session_id: {meta.get('session_id')}")
    print(f"user: {meta.get('user')}")
    print(f"hostname: {meta.get('hostname')}")
    print(f"target: {meta.get('target_hint')}")
    print(f"started_at: {meta.get('started_at')}")
    print(f"ended_at: {meta.get('ended_at')}")
    print(f"exit_code: {meta.get('exit_code')}")
    print(f"login_command: {' '.join(str(x) for x in meta.get('login_command') or [])}")


def _print_commands(commands: list[dict[str, Any]], only_blocked: bool) -> None:
    print(_fmt_row(["TIME", "DECISION", "RULE", "COMMAND", "REASON"]))
    for row in commands:
        if only_blocked and row["allowed"]:
            continue
        decision = "ALLOW" if row["allowed"] else "BLOCK"
        print(_fmt_row([str(row["ts"] or "-"), decision, str(row["rule"]), str(row["command"]), str(row["reason"])]))


def _fmt_row(values: list[str]) -> str:
    widths = [24, 24, 10, 28]
    cells: list[str] = []
    for idx, value in enumerate(values):
        text = value.replace("\n", "\\n")
        if idx < len(widths):
            cells.append(text[: widths[idx]].ljust(widths[idx]))
        else:
            cells.append(text)
    return "  ".join(cells)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
