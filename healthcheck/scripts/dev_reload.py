#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


WATCH_SUFFIXES = {".py", ".html", ".css", ".js", ".yaml", ".yml", ".md"}
IGNORE_DIRS = {
    ".git",
    ".venv",
    ".venv39",
    ".venv310",
    ".venv311",
    "__pycache__",
    "output",
    "runtime",
    "state",
}


def collect_snapshot(root: Path) -> Dict[str, float]:
    snapshot: Dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".pytest_cache")]
        for filename in filenames:
            p = Path(dirpath) / filename
            if p.suffix.lower() not in WATCH_SUFFIXES:
                continue
            try:
                snapshot[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    return snapshot


def start_process(cmd: List[str], cwd: Path) -> subprocess.Popen:
    print(f"[RELOAD] starting: {' '.join(cmd)}")
    if os.name == "nt":
        return subprocess.Popen(cmd, cwd=str(cwd))
    return subprocess.Popen(cmd, cwd=str(cwd), preexec_fn=os.setsid)


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple file-watcher with auto restart")
    parser.add_argument("--interval", type=float, default=1.0, help="poll interval seconds")
    parser.add_argument("--root", default=".", help="watch root")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command to run, prefix with --")
    args = parser.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[RELOAD] missing command, example: dev_reload.py -- python app/web_server.py")
        return 2

    root = Path(args.root).resolve()
    proc = start_process(cmd, root)
    snapshot = collect_snapshot(root)
    try:
        while True:
            time.sleep(max(0.2, args.interval))
            current = collect_snapshot(root)
            if current != snapshot:
                changed = sorted(set(current.keys()) ^ set(snapshot.keys()))
                if not changed:
                    for k, v in current.items():
                        if snapshot.get(k) != v:
                            changed.append(k)
                            if len(changed) >= 5:
                                break
                show = ", ".join(Path(x).name for x in changed[:5]) if changed else "unknown files"
                print(f"[RELOAD] change detected: {show}")
                stop_process(proc)
                proc = start_process(cmd, root)
                snapshot = current
            if proc.poll() is not None:
                print(f"[RELOAD] child exited with code {proc.returncode}, restarting...")
                proc = start_process(cmd, root)
                snapshot = collect_snapshot(root)
    except KeyboardInterrupt:
        print("\n[RELOAD] stopping ...")
        stop_process(proc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

