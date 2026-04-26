from __future__ import annotations

from ssh_proxy.policy import CommandPolicy


def test_allows_read_only_network_commands() -> None:
    policy = CommandPolicy()

    assert policy.decide("show version").allowed
    assert policy.decide("display clock").allowed
    assert policy.decide("dis interface brief").allowed
    assert policy.decide("ping 10.0.0.1").allowed
    assert policy.decide("traceroute 10.0.0.1").allowed


def test_blocks_dangerous_commands() -> None:
    policy = CommandPolicy()

    for command in (
        "reload",
        "reboot",
        "delete flash:/config",
        "shutdown",
        "system-view",
        "configure terminal",
        "commit",
        "write memory",
        "rm -rf /tmp/x",
    ):
        decision = policy.decide(command)
        assert not decision.allowed, command


def test_blocks_compound_shell_like_commands() -> None:
    policy = CommandPolicy()

    for command in (
        "show version; reload",
        "display clock && reboot",
        "show users || delete flash:/x",
        "show version | sh",
        "show version `whoami`",
        "show version $(whoami)",
    ):
        decision = policy.decide(command)
        assert not decision.allowed, command


def test_paste_blocks_if_any_line_is_dangerous() -> None:
    policy = CommandPolicy()

    decision = policy.decide("show version\nreload\nshow clock")

    assert not decision.allowed
    assert decision.rule == "reload"


def test_config_mode_only_allows_exit_commands() -> None:
    policy = CommandPolicy()
    policy.observe_output("router(config)# ")

    assert not policy.decide("show version").allowed
    assert policy.decide("exit").allowed
    assert policy.decide("show version").allowed


def test_unknown_commands_default_deny() -> None:
    policy = CommandPolicy()

    decision = policy.decide("ssh user@host")

    assert not decision.allowed
    assert decision.rule == "default-deny"
