from app.diagnosis.known_issue_store import NetdiagKnownIssueStore


def _store(tmp_path):
    return NetdiagKnownIssueStore((tmp_path / "state" / "known_issues.json").as_posix())


def test_import_and_search_by_profile_query(tmp_path):
    store = _store(tmp_path)
    rows = [
        {
            "issue_id": "KI-100",
            "title": "BGP peer flap on IOSXE",
            "vendor": "cisco",
            "os_family": "cisco_iosxe",
            "symptoms": "bgp flap,packet loss",
            "evidence_patterns": "neighbor.*idle",
            "diag_intents": "bgp_summary,system_log_recent",
            "root_cause": "peer instability",
            "fix_actions": "check optical and peer side",
            "severity": "high",
            "domain": "routing",
            "priority": 180,
            "enabled": True,
            "source": "noc",
        }
    ]
    result = store.import_rows(rows, source="noc")
    assert result["imported"] == 1

    hits = store.search(
        profile="cisco_iosxe",
        version="17.9.3",
        query_text="bgp flap and packet loss",
        evidence_text="BGP neighbor entered IDLE state",
        limit=5,
    )
    assert len(hits) == 1
    assert hits[0]["issue_id"] == "KI-100"
    assert float(hits[0]["score"]) > 0.0
    assert isinstance(hits[0].get("match_reasons"), list)
    assert str(hits[0].get("explain") or "")
    assert float(hits[0].get("match_confidence") or 0.0) > 0.0


def test_disabled_issue_not_returned(tmp_path):
    store = _store(tmp_path)
    store.upsert_issue(
        {
            "issue_id": "KI-200",
            "title": "Interface CRC spike",
            "vendor": "huawei",
            "os_family": "huawei_vrp",
            "symptoms": ["crc", "drop"],
            "root_cause": "optic problem",
            "enabled": False,
        }
    )
    hits = store.search(
        profile="huawei_vrp",
        query_text="crc and drop",
        evidence_text="CRC error increased rapidly",
        limit=5,
    )
    assert hits == []


def test_parse_csv_and_enabled_flag(tmp_path):
    store = _store(tmp_path)
    csv_text = (
        "issue_id,title,vendor,os_family,symptoms,root_cause,enabled,priority\n"
        "KI-300,OSPF neighbor down,huawei,huawei_vrp,ospf down;neighbor reset,ospf instability,false,150\n"
    )
    rows = store.parse_csv_text(csv_text)
    assert len(rows) == 1
    result = store.import_rows(rows, source="noc")
    assert result["imported"] == 1
    items = store.list_issues(enabled_only=False)
    assert items[0]["enabled"] is False
