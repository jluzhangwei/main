from app.diagnosis.policy import has_placeholder_token, is_read_only_command


def test_read_only_command_accepts_normal_show():
    assert is_read_only_command("show version") is True
    assert is_read_only_command("display clock") is True


def test_read_only_command_rejects_placeholder_text():
    assert has_placeholder_token("display interface (具体接口名)") is True
    assert is_read_only_command("display interface (具体接口名)") is False
    assert is_read_only_command("show logging | include (ERROR|WARN)") is False


def test_read_only_command_rejects_write_actions():
    assert is_read_only_command("show running-config | include reload") is False
    assert is_read_only_command("configure terminal") is False
