#!/usr/bin/env python3
"""Extract topology rows from LLDP CLI output.

Supported input examples:
- show lldp neighbour details / show lldp neighbor details
- display lldp neighbour / dis lldp neighbor

Output CSV structure (required):
递归次数,源设备名称,源设备端口,目的设备名称,目的设备端口
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

LOCAL_IF_RE = re.compile(
    r"^(?:local\s+intf|local\s+port|local\s+interface|local\s+port\s+id|本地(?:接口|端口))\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
REMOTE_NAME_RE = re.compile(
    r"^(?:system\s+name|sysname|neighbor\s+name|neighbour\s+name|邻居(?:系统)?名称)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
REMOTE_IF_RE = re.compile(
    r"^(?:port\s+description|port\s+id|neighbor\s+interface|neighbour\s+interface|neighbor\s+port|neighbour\s+port|邻居(?:接口|端口))\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
REMOTE_IP_LINE_RE = re.compile(
    r"(?:management\s+address(?:es)?|management\s+ip|management\s+address\s*\(ipv4\)|管理地址|邻居管理地址)",
    re.IGNORECASE,
)
BLOCK_START_RE = re.compile(
    r"(neighbor\s+index|lldp\s+neighbor-information|local\s+intf|本地(?:接口|端口))",
    re.IGNORECASE,
)
HOST_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


def is_valid_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def normalize_token(s: str) -> str:
    t = (s or "").strip()
    t = t.strip("[]<>(){}\"' ")
    return t


def parse_lldp_topology_rows(text: str, source_name: str, depth: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    cur = {"local_if": "", "remote_name": "", "remote_if": "", "remote_ip": ""}

    def flush() -> None:
        local_if = normalize_token(cur["local_if"])
        remote_name = normalize_token(cur["remote_name"])
        remote_if = normalize_token(cur["remote_if"])
        remote_ip = normalize_token(cur["remote_ip"])

        if not local_if:
            return

        # Topology requires destination device name; fallback to management IP when no name.
        dst = remote_name or remote_ip
        if not dst:
            return

        rows.append(
            {
                "递归次数": str(depth),
                "源设备名称": source_name,
                "源设备端口": local_if,
                "目的设备名称": dst,
                "目的设备端口": remote_if,
                "_目的设备地址": remote_ip,
            }
        )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if BLOCK_START_RE.search(line):
            flush()
            cur = {"local_if": "", "remote_name": "", "remote_if": "", "remote_ip": ""}

        m_local = LOCAL_IF_RE.search(line)
        if m_local:
            cur["local_if"] = m_local.group(1)
            continue
        m_local_huawei = re.search(r"lldp\\s+neighbor-information\\s+of\\s+port\\s+([^\\s]+)", line, re.IGNORECASE)
        if m_local_huawei and not cur["local_if"]:
            cur["local_if"] = m_local_huawei.group(1)
            continue

        m_name = REMOTE_NAME_RE.search(line)
        if m_name:
            cand = normalize_token(m_name.group(1))
            if cand and HOST_TOKEN_RE.match(cand):
                cur["remote_name"] = cand
            continue

        m_remote_if = REMOTE_IF_RE.search(line)
        if m_remote_if and not cur["remote_if"]:
            cur["remote_if"] = m_remote_if.group(1)
            continue

        if REMOTE_IP_LINE_RE.search(line):
            for ip in IPV4_PATTERN.findall(line):
                if is_valid_ipv4(ip):
                    cur["remote_ip"] = ip
                    break

    flush()

    # Deduplicate
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        key = "||".join(
            [
                r["递归次数"],
                r["源设备名称"].lower(),
                r["源设备端口"].lower(),
                r["目的设备名称"].lower(),
                r["目的设备端口"].lower(),
                (r.get("_目的设备地址", "") or "").lower(),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    return sys.stdin.read()


def write_output(rows: list[dict[str, str]], out_path: str | None, as_csv: bool) -> None:
    headers = ["递归次数", "源设备名称", "源设备端口", "目的设备名称", "目的设备端口"]

    if out_path:
        p = Path(out_path)
        if as_csv:
            with p.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        else:
            lines = ["\t".join(headers)]
            for r in rows:
                lines.append("\t".join(r.get(h, "") for h in headers))
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    if as_csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        print("\t".join(headers))
        for r in rows:
            print("\t".join(r.get(h, "") for h in headers))


def extract_next_query_addresses(rows: list[dict[str, str]]) -> list[str]:
    """Return unique destination addresses for recursive next-step querying."""
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        # Prefer parsed management IP; fallback to destination name if needed.
        addr = (r.get("_目的设备地址") or r.get("目的设备名称") or "").strip()
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(addr)
    return out


def write_next_query_addresses(addrs: list[str], path: str, as_csv: bool) -> None:
    p = Path(path)
    if as_csv:
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["query_address"])
            for a in addrs:
                w.writerow([a])
    else:
        p.write_text("\n".join(addrs) + ("\n" if addrs else ""), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract topology CSV rows from LLDP command output")
    p.add_argument("-i", "--input", default="", help="Input text file path; omit to read from stdin")
    p.add_argument("-o", "--output", default="", help="Output path; omit to print to stdout")
    p.add_argument("--csv", action="store_true", help="Output in CSV format")
    p.add_argument("--source-name", required=True, help="Source device name for output column '源设备名称'")
    p.add_argument("--depth", type=int, default=1, help="Recursion depth value for output column '递归次数' (default: 1)")
    p.add_argument(
        "--next-query-out",
        default="",
        help="Optional output file for next recursive query addresses (one per line by default)",
    )
    p.add_argument(
        "--next-query-csv",
        action="store_true",
        help="Write --next-query-out as CSV (column: query_address)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    text = read_input(args.input or None)
    rows = parse_lldp_topology_rows(text, source_name=args.source_name.strip(), depth=max(1, args.depth))
    write_output(rows, args.output or None, args.csv)
    next_addrs = extract_next_query_addresses(rows)
    if args.next_query_out:
        write_next_query_addresses(next_addrs, args.next_query_out, args.next_query_csv)
        print(f"[INFO] wrote {len(next_addrs)} next query address(es) -> {args.next_query_out}", file=sys.stderr)
    print(f"[INFO] extracted {len(rows)} topology row(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
