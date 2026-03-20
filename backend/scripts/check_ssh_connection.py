from __future__ import annotations

import argparse
import os
import sys
import telnetlib

try:
    from netmiko import ConnectHandler
except Exception as exc:  # pragma: no cover - dependency availability is environment specific
    print(f"netmiko is not available: {exc}", file=sys.stderr)
    raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check SSH/Telnet connectivity to a network device without changing config.")
    parser.add_argument("--host", default=os.getenv("DEVICE_HOST"), required=os.getenv("DEVICE_HOST") is None)
    parser.add_argument("--username", default=os.getenv("DEVICE_USERNAME"), required=os.getenv("DEVICE_USERNAME") is None)
    parser.add_argument("--password", default=os.getenv("DEVICE_PASSWORD"), required=os.getenv("DEVICE_PASSWORD") is None)
    parser.add_argument("--port", type=int, default=int(os.getenv("DEVICE_PORT", "22")))
    parser.add_argument("--device-type", default=os.getenv("DEVICE_TYPE", "cisco_ios"))
    parser.add_argument("--protocol", choices=["ssh", "telnet"], default=os.getenv("DEVICE_PROTOCOL", "ssh"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.protocol == "telnet":
        return check_telnet(args.host, args.username, args.password, args.port)
    return check_ssh(args.host, args.username, args.password, args.port, args.device_type)


def check_ssh(host: str, username: str, password: str, port: int, device_type: str) -> int:
    conn = None
    try:
        conn = ConnectHandler(
            device_type=device_type,
            host=host,
            username=username,
            password=password,
            port=port,
            timeout=8,
        )
        prompt = conn.find_prompt()
        print(f"SSH connection OK: host={host} username={username} prompt={prompt}")
        return 0
    except Exception as exc:
        print(f"SSH connection failed: host={host} username={username} error={exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def check_telnet(host: str, username: str, password: str, port: int) -> int:
    conn = None
    try:
        conn = telnetlib.Telnet(host, port, timeout=8)
        conn.read_until(b"Username", timeout=5)
        conn.write(username.encode() + b"\n")
        conn.read_until(b"Password", timeout=5)
        conn.write(password.encode() + b"\n")
        response = conn.read_until(b">", timeout=6).decode("utf-8", "ignore")
        if "Error: User was locked" in response:
            print(f"Telnet login failed: host={host} username={username} error=User was locked", file=sys.stderr)
            return 1
        print(f"Telnet connection OK: host={host} username={username}")
        return 0
    except Exception as exc:
        print(f"Telnet connection failed: host={host} username={username} error={exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None and hasattr(conn, "close"):
            try:
                conn.write(b"quit\n")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
