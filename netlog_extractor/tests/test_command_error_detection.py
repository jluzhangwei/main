from app.services.device_service import _looks_like_cli_error


def test_detect_invalid_command_marker():
    out = "show log\n                                     ^\n% Invalid command at '^' marker.\n"
    assert _looks_like_cli_error(out) is True


def test_no_false_positive_on_log_content_error_word():
    out = (
        "2026 Feb 19 18:30:07.935 HOST %DAEMON-3-SYSTEM_MSG: error: Unable to open btmp\n"
        "2026 Feb 19 18:30:08.941 HOST %AUTHPRIV-3-SYSTEM_MSG: pam_aaa:Authentication failed\n"
    )
    assert _looks_like_cli_error(out) is False
