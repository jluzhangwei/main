from __future__ import annotations

import argparse
import json
import sys

from .policy import CommandPolicy, POLICY_DESCRIPTIONS


def build_parser(prog: str = "ssh-proxy-policy") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Inspect and test ssh-proxy command policy.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Show allow and block rules.")
    p_list.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    p_check = sub.add_parser("check", help="Check whether one command would be allowed.")
    p_check.add_argument("cli_command", help="Command string to check.")
    p_check.add_argument("--config-mode", action="store_true", help="Check as if already in config mode.")
    p_check.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    return parser


def main(argv: list[str] | None = None, prog: str = "ssh-proxy-policy") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)

    if args.command == "list":
        if args.json:
            print(json.dumps(POLICY_DESCRIPTIONS, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_policy()
        return 0

    if args.command == "check":
        policy = CommandPolicy()
        if args.config_mode:
            policy.in_config_mode = True
        decision = policy.decide(args.cli_command)
        if args.json:
            print(json.dumps(decision.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            status = "ALLOW" if decision.allowed else "BLOCK"
            print(f"{status}: {args.cli_command}")
            print(f"rule: {decision.rule}")
            print(f"reason: {decision.reason}")
        return 0 if decision.allowed else 1

    parser.error(f"unknown command: {args.command}")
    return 2


def _print_policy() -> None:
    print("ALLOW prefixes:")
    for item in POLICY_DESCRIPTIONS["allow_prefixes"]:
        print(f"  - {item}")

    print("\nBLOCK dangerous patterns:")
    for item in POLICY_DESCRIPTIONS["dangerous_patterns"]:
        print(f"  - {item['rule']}: {item['pattern']}")

    print("\nALLOW session display settings:")
    for item in POLICY_DESCRIPTIONS["session_setting_patterns"]:
        print(f"  - {item['rule']}: {item['pattern']}")

    print("\nBLOCK compound/shell patterns:")
    for item in POLICY_DESCRIPTIONS["compound_patterns"]:
        print(f"  - {item['rule']}: {item['pattern']}")

    print("\nCONFIG mode allowed exits:")
    for item in POLICY_DESCRIPTIONS["config_exit_commands"]:
        print(f"  - {item}")

    print("\nDefault:")
    print("  - Commands not matching the allow list are blocked by default-deny.")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
