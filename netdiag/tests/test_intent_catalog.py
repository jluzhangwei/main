from app.diagnosis.intent_catalog import command_for_intent, default_plan_for_profile, normalize_profile


def test_normalize_profile_paloalto():
    assert normalize_profile("paloalto", "") == "paloalto_panos"
    assert normalize_profile("unknown", "paloalto_panos") == "paloalto_panos"


def test_command_for_intent_uses_vendor_profile():
    assert command_for_intent("clock_check", "huawei_vrp") == "display clock"
    assert command_for_intent("version_check", "paloalto_panos") == "show system info"
    assert command_for_intent("system_log_recent", "cisco_nxos") == "show logging logfile"
    assert command_for_intent("interface_errors", "cisco_nxos") == "show interface counters errors"


def test_default_plan_profile_is_non_empty():
    plan = default_plan_for_profile("arista_eos", max_commands=4)
    assert len(plan) == 4
    assert all(str(x.get("command", "")).startswith(("show ", "display ", "dis ")) for x in plan)
