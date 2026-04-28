from __future__ import annotations

import argparse
import curses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .audit_store import audit_base, list_sessions, read_commands, read_events, read_replay, resolve_session


def build_parser(prog: str = "ssh-proxy-audit") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect ssh-proxy audit sessions.",
        epilog=(
            "Run without a subcommand to open the interactive audit viewer. "
            "Use Up/Down to select a session, Enter to show commands, Esc to return, q to quit."
        ),
    )
    parser.add_argument("--audit-dir", type=Path, default=None, help="Audit base directory.")
    sub = parser.add_subparsers(dest="command")

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


def main(argv: list[str] | None = None, prog: str = "ssh-proxy-audit") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    base = audit_base(args.audit_dir)

    if args.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _run_tui(base)
        _print_sessions(list_sessions(base))
        return 0

    if args.command == "list":
        _print_sessions(list_sessions(base, args.limit))
        return 0

    session = resolve_session(base, getattr(args, "session_id", None))
    if args.command == "show":
        _print_summary(session.metadata, session.session_dir, read_commands(session))
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


def _run_tui(base: Path) -> int:
    try:
        curses.wrapper(_AuditTui(base).run)
    except KeyboardInterrupt:
        return 130
    return 0


class _AuditTui:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.sessions: list[Any] = []
        self.selected = 0
        self.list_offset = 0
        self.detail_offset = 0
        self.mode = "list"
        self._counts_cache: dict[str, dict[str, int]] = {}
        self._commands_cache: dict[str, list[dict[str, Any]]] = {}
        self._command_rows_cache: dict[str, list[list[str]]] = {}

    def run(self, stdscr: Any) -> None:
        self.stdscr = stdscr
        curses.curs_set(0)
        curses.use_default_colors()
        curses.set_escdelay(25)
        stdscr.keypad(True)
        self._load_sessions()

        while True:
            if self.mode == "list":
                self._draw_list()
                key = stdscr.getch()
                if self._handle_list_key(key):
                    return
            else:
                self._draw_detail()
                key = stdscr.getch()
                if self._handle_detail_key(key):
                    return

    def _load_sessions(self) -> None:
        self.sessions = list_sessions(self.base, 200)
        self._counts_cache.clear()
        self._commands_cache.clear()
        self._command_rows_cache.clear()
        if self.selected >= len(self.sessions):
            self.selected = max(0, len(self.sessions) - 1)
        self.list_offset = min(self.list_offset, self.selected)

    def _handle_list_key(self, key: int) -> bool:
        if key in (ord("q"), ord("Q")):
            return True
        if key in (ord("r"), ord("R")):
            self._load_sessions()
            return False
        if key in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
            return False
        if key in (curses.KEY_DOWN, ord("j")):
            self.selected = min(max(0, len(self.sessions) - 1), self.selected + 1)
            return False
        if key == curses.KEY_PPAGE:
            self.selected = max(0, self.selected - self._page_size())
            return False
        if key == curses.KEY_NPAGE:
            self.selected = min(max(0, len(self.sessions) - 1), self.selected + self._page_size())
            return False
        if key == curses.KEY_HOME:
            self.selected = 0
            return False
        if key == curses.KEY_END:
            self.selected = max(0, len(self.sessions) - 1)
            return False
        if key in (10, 13, curses.KEY_ENTER) and self.sessions:
            self.mode = "detail"
            self.detail_offset = 0
            return False
        return False

    def _handle_detail_key(self, key: int) -> bool:
        if key in (ord("q"), ord("Q")):
            return True
        if key == 27:
            self.mode = "list"
            return False
        if key in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
            self.detail_offset = 0
            return False
        if key in (curses.KEY_DOWN, ord("j")):
            self.selected = min(max(0, len(self.sessions) - 1), self.selected + 1)
            self.detail_offset = 0
            return False
        if key == curses.KEY_PPAGE:
            self.detail_offset = max(0, self.detail_offset - self._page_size())
            return False
        if key == curses.KEY_NPAGE:
            self.detail_offset += self._page_size()
            return False
        if key == curses.KEY_HOME:
            self.detail_offset = 0
            return False
        return False

    def _draw_list(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        self._add(0, 0, "ssh-proxy audit", curses.A_BOLD)
        self._add(0, 17, "Enter: commands  Esc: back  r: refresh  q: quit")

        if not self.sessions:
            self._add(2, 0, f"No audit sessions found under {self.base}")
            self.stdscr.refresh()
            return

        headers = ["SESSION", "STARTED", "DURATION", "STATUS", "CMDS", "BLOCK", "TARGET"]
        widths = self._list_widths(width)
        self._add(2, 0, _format_table_row(headers, widths), curses.A_BOLD)
        self._add(3, 0, _format_table_row(["-" * item for item in widths], widths))

        visible = max(1, height - 5)
        self._adjust_list_offset(visible)
        for screen_row, session in enumerate(self.sessions[self.list_offset : self.list_offset + visible], start=4):
            idx = self.list_offset + screen_row - 4
            text = _format_table_row(self._session_row(session), widths)
            attr = curses.A_REVERSE if idx == self.selected else curses.A_NORMAL
            self._add(screen_row, 0, text, attr)

        self._draw_footer(height, width, f"{self.selected + 1}/{len(self.sessions)}  audit_dir={self.base}")
        self.stdscr.refresh()

    def _draw_detail(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if not self.sessions:
            self.mode = "list"
            return
        session = self.sessions[self.selected]
        meta = session.metadata

        title = f"session {session.session_id[:12]}  target={_value(meta.get('target_hint'))}"
        self._add(0, 0, title, curses.A_BOLD)
        self._add(0, max(0, width - 58), "Esc: list  Up/Down: session  PgUp/PgDn: scroll  q: quit")
        self._add(1, 0, f"started={_fmt_time(meta.get('started_at'))}  duration={_fmt_duration(meta)}  status={_fmt_status(meta)}")
        self._add(2, 0, f"login={_join_command(meta.get('login_command'))}")

        rows = self._command_rows(session)
        if not rows:
            self._add(4, 0, "No command decisions recorded.")
            self._draw_footer(height, width, str(session.session_dir))
            self.stdscr.refresh()
            return

        widths = self._command_widths(width)
        self._add(4, 0, _format_table_row(["TIME", "STATE", "RULE", "COMMAND"], widths), curses.A_BOLD)
        self._add(5, 0, _format_table_row(["-" * item for item in widths], widths))
        visible = max(1, height - 7)
        max_offset = max(0, len(rows) - visible)
        self.detail_offset = min(self.detail_offset, max_offset)
        for screen_row, row in enumerate(rows[self.detail_offset : self.detail_offset + visible], start=6):
            attr = curses.A_BOLD if row[1] == "BLOCK" else curses.A_NORMAL
            self._add(screen_row, 0, _format_table_row(row, widths), attr)

        self._draw_footer(height, width, f"{self.detail_offset + 1}-{min(len(rows), self.detail_offset + visible)}/{len(rows)}  {session.session_dir}")
        self.stdscr.refresh()

    def _session_row(self, session: Any) -> list[str]:
        meta = session.metadata
        counts = self._command_counts(session)
        return [
            session.session_id[:12],
            _fmt_time(meta.get("started_at")),
            _fmt_duration(meta),
            _fmt_status(meta),
            str(counts["total"]),
            str(counts["blocked"]),
            _value(meta.get("target_hint")),
        ]

    def _command_counts(self, session: Any) -> dict[str, int]:
        cached = self._counts_cache.get(session.session_id)
        if cached is not None:
            return cached
        commands = self._commands(session)
        blocked = sum(1 for row in commands if not row["allowed"])
        counts = {"total": len(commands), "blocked": blocked}
        self._counts_cache[session.session_id] = counts
        return counts

    def _commands(self, session: Any) -> list[dict[str, Any]]:
        cached = self._commands_cache.get(session.session_id)
        if cached is not None:
            return cached
        commands = read_commands(session)
        self._commands_cache[session.session_id] = commands
        return commands

    def _command_rows(self, session: Any) -> list[list[str]]:
        cached = self._command_rows_cache.get(session.session_id)
        if cached is not None:
            return cached
        commands = self._commands(session)
        rows = []
        for row in commands:
            decision = "ALLOW" if row["allowed"] else "BLOCK"
            rows.append([_fmt_time(row["ts"]), decision, str(row["rule"]), str(row["command"])])
        self._command_rows_cache[session.session_id] = rows
        return rows

    def _adjust_list_offset(self, visible: int) -> None:
        if self.selected < self.list_offset:
            self.list_offset = self.selected
        if self.selected >= self.list_offset + visible:
            self.list_offset = self.selected - visible + 1

    def _page_size(self) -> int:
        height, _ = self.stdscr.getmaxyx()
        return max(1, height - 6)

    def _list_widths(self, width: int) -> list[int]:
        return _fit_widths(width, [12, 19, 8, 8, 4, 5, 20])

    def _command_widths(self, width: int) -> list[int]:
        return _fit_widths(width, [19, 5, 18, 56])

    def _draw_footer(self, height: int, width: int, text: str) -> None:
        if height <= 1:
            return
        self._add(height - 1, 0, _clip(text, width), curses.A_DIM)

    def _add(self, y: int, x: int, text: str, attr: int = curses.A_NORMAL) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        self.stdscr.addnstr(y, x, text, max(0, width - x - 1), attr)


def _print_sessions(sessions: list[Any]) -> None:
    if not sessions:
        print("No audit sessions found.")
        return

    rows: list[list[str]] = []
    for session in sessions:
        meta = session.metadata
        counts = _command_counts(session)
        rows.append(
            [
                session.session_id[:12],
                _fmt_time(meta.get("started_at")),
                _fmt_duration(meta),
                _fmt_status(meta),
                str(counts["total"]),
                str(counts["blocked"]),
                _value(meta.get("target_hint")),
            ]
        )
    _print_table(
        ["SESSION", "STARTED", "DURATION", "STATUS", "CMDS", "BLOCK", "TARGET"],
        rows,
        min_widths=[12, 19, 8, 8, 4, 5, 20],
    )


def _print_summary(meta: dict[str, Any], session_dir: Path | None = None, commands: list[dict[str, Any]] | None = None) -> None:
    commands = commands or []
    allowed = sum(1 for row in commands if row["allowed"])
    blocked = len(commands) - allowed
    print("Session")
    print(f"  id:          {_value(meta.get('session_id'))}")
    print(f"  status:      {_fmt_status(meta)}")
    print(f"  target:      {_value(meta.get('target_hint'))}")
    print(f"  user:        {_value(meta.get('user'))}@{_value(meta.get('hostname'))}")
    print(f"  started:     {_fmt_time(meta.get('started_at'))}")
    print(f"  ended:       {_fmt_time(meta.get('ended_at'))}")
    print(f"  duration:    {_fmt_duration(meta)}")
    print(f"  exit_code:   {_value(meta.get('exit_code'))}")
    print(f"  login_cmd:   {_join_command(meta.get('login_command'))}")
    print("")
    print("Audit")
    print(f"  commands:    total={len(commands)} allowed={allowed} blocked={blocked}")
    if session_dir is not None:
        print(f"  directory:   {session_dir}")
        print(f"  events:      {session_dir / 'events.jsonl'}")
        print(f"  replay:      {session_dir / 'replay.jsonl'}")


def _print_commands(commands: list[dict[str, Any]], only_blocked: bool) -> None:
    rows: list[list[str]] = []
    for row in commands:
        if only_blocked and row["allowed"]:
            continue
        decision = "ALLOW" if row["allowed"] else "BLOCK"
        rows.append([_fmt_time(row["ts"]), decision, str(row["rule"]), str(row["command"])])

    if not rows:
        if only_blocked:
            print("No blocked commands recorded.")
        else:
            print("No command decisions recorded.")
        return

    _print_table(
        ["TIME", "STATE", "RULE", "COMMAND"],
        rows,
        min_widths=[19, 5, 18, 56],
    )


def _command_counts(session: Any) -> dict[str, int]:
    try:
        commands = read_commands(session)
    except Exception:
        return {"total": 0, "blocked": 0}
    blocked = sum(1 for row in commands if not row["allowed"])
    return {"total": len(commands), "blocked": blocked}


def _print_table(headers: list[str], rows: list[list[str]], min_widths: list[int]) -> None:
    width = max(80, shutil.get_terminal_size((120, 20)).columns)
    widths = _fit_widths(width, min_widths)

    print(_format_table_row(headers, widths))
    print(_format_table_row(["-" * width for width in widths], widths))
    for row in rows:
        print(_format_table_row(row, widths))


def _fit_widths(total_width: int, min_widths: list[int]) -> list[int]:
    gap = 2
    fixed = sum(min_widths[:-1]) + gap * (len(min_widths) - 1)
    last_width = max(min_widths[-1], total_width - fixed)
    widths = [*min_widths[:-1], last_width]
    if sum(widths) + gap * (len(widths) - 1) <= total_width:
        return widths

    # Narrow terminals: shrink from the right while keeping key columns usable.
    over = sum(widths) + gap * (len(widths) - 1) - total_width
    minimums = [8 if idx == 0 else 4 for idx in range(len(widths))]
    for idx in range(len(widths) - 1, -1, -1):
        reducible = max(0, widths[idx] - minimums[idx])
        take = min(reducible, over)
        widths[idx] -= take
        over -= take
        if over <= 0:
            break
    return widths


def _format_table_row(values: list[str], widths: list[int]) -> str:
    cells = []
    for value, width in zip(values, widths):
        cells.append(_clip(str(value).replace("\n", "\\n"), width).ljust(width))
    return "  ".join(cells).rstrip()


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _join_command(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _fmt_time(value: Any) -> str:
    if not value:
        return "-"
    text = str(value)
    if "T" in text:
        text = text.replace("T", " ")
    if len(text) >= 19:
        return text[:19]
    return text


def _fmt_status(meta: dict[str, Any]) -> str:
    exit_code = meta.get("exit_code")
    ended_at = meta.get("ended_at")
    if exit_code is None and not ended_at:
        return "RUNNING"
    if exit_code == 0:
        return "OK"
    if exit_code is None:
        return "ENDED"
    return f"FAIL({exit_code})"


def _fmt_duration(meta: dict[str, Any]) -> str:
    start = meta.get("started_at_epoch")
    end = meta.get("ended_at_epoch")
    if not isinstance(start, (int, float)):
        return "-"
    if not isinstance(end, (int, float)):
        return "running"
    seconds = max(0, int(end - start))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
