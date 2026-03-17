from app.diagnosis.evidence_parser import extract_round_evidence, format_evidence_brief


def test_extract_round_evidence_cisco_command_error_and_bundle_signal():
    out = extract_round_evidence(
        executions=[
            {
                "device_id": "dev-1",
                "command": "show interface brief",
                "status": "success",
                "error": "",
                "output_text": (
                    "Eth1/2 1000 eth access down XCVR not inserted auto(D)\n"
                    "Po102 1000 eth access down No operational members auto(D) lacp\n"
                ),
            },
            {
                "device_id": "dev-1",
                "command": "show interfaces counters errors",
                "status": "success",
                "error": "",
                "output_text": "^\n% Invalid command at '^' marker.\n",
            },
        ],
        profile_map={"dev-1": "cisco_nxos"},
    )
    assert out["command_health"]["total"] == 2
    assert out["command_health"]["error_output"] == 1
    sig_names = {str(x.get("signal") or "") for x in out.get("signals", [])}
    assert "bundle_no_operational_members" in sig_names
    assert "command_syntax_or_unsupported" in sig_names


def test_extract_round_evidence_huawei_error_counter_signal():
    out = extract_round_evidence(
        executions=[
            {
                "device_id": "dev-1",
                "command": "display interface brief",
                "status": "success",
                "error": "",
                "output_text": (
                    "Interface                   PHY   Protocol  InUti OutUti   inErrors  outErrors\n"
                    "GigabitEthernet0/0/25       up    up           0%     0%          1          0\n"
                    "GigabitEthernet0/0/33       up    up           0%     0%          2          0\n"
                ),
            }
        ],
        profile_map={"dev-1": "huawei_vrp"},
    )
    assert out["command_health"]["valid_output"] == 1
    sig_names = {str(x.get("signal") or "") for x in out.get("signals", [])}
    assert "huawei_interface_error_counter_nonzero" in sig_names
    assert float(out["domain_delta"].get("link", 0.0)) > 0.0


def test_format_evidence_brief_contains_health_and_signals():
    parsed = extract_round_evidence(
        executions=[
            {
                "device_id": "dev-1",
                "command": "display clock",
                "status": "success",
                "error": "",
                "output_text": "2026-03-08 16:41:19+08:00\nTime Zone(SG) : UTC+08:00\n",
            }
        ],
        profile_map={"dev-1": "huawei_vrp"},
    )
    brief = format_evidence_brief(parsed)
    assert "command_health" in brief
    assert "[clock]" in brief


def test_non_firewall_vendor_drop_keyword_not_misclassified_as_firewall():
    parsed = extract_round_evidence(
        executions=[
            {
                "device_id": "dev-1",
                "command": "display logbuffer",
                "status": "success",
                "error": "",
                "output_text": "2026-03-08 link packet drop observed on uplink\n",
            }
        ],
        profile_map={"dev-1": "huawei_vrp"},
    )
    fw_signals = [x for x in (parsed.get("signals") or []) if str(x.get("domain") or "") == "firewall"]
    assert fw_signals == []
