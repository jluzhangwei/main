from __future__ import annotations

import re

# Keep these patterns aligned with healthcheck/app/healthcheck.py SMC flow.
PROMPT_PATTERN = re.compile(r"(?m)^([A-Za-z0-9_.-]+(?:\([^)]+\))?[>#]|<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
JUMP_PROMPT_PATTERN = re.compile(r"(?m)^.*[@].*[$#]\s*$")
YES_PATTERN = re.compile(r"\(yes/no(?:/\[fingerprint\])?\)\??", re.IGNORECASE)
TOKEN_RETRY_PATTERN = re.compile(
    r"(try\s+login\s+with\s+the\s+old\s+token.*?\(y/n\).*?(?:default\s*:\s*n|\[default:n\]))",
    re.IGNORECASE | re.DOTALL,
)
PASSWORD_PATTERN = re.compile(r"(enter\s+password|password)\s*:\s*$", re.IGNORECASE | re.MULTILINE)
FAIL_PATTERN = re.compile(
    r"(permission denied|authentication failed|received disconnect|connection is closed by ssh server|"
    r"connection timed out|could not resolve|connection refused|no route to host|closed by remote host)",
    re.IGNORECASE,
)
ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
