"""HA repairs helpers for SIP registration auth failures.

Kept free of Home Assistant imports so SIP core tests can load it.
The integration layer calls ``issue_registry`` with these ids.
"""
from __future__ import annotations

ISSUE_REGISTER_AUTH = "register_auth_failed"
AUTH_FAIL_ISSUE_AFTER = 3


def register_auth_issue_id(entry_id: str) -> str:
    return f"{ISSUE_REGISTER_AUTH}_{entry_id}"


def is_register_auth_failure(reason: str) -> bool:
    """True for 401/403/407 REGISTER rejections (wrong credentials / proxy auth)."""
    code = str(reason).split(" ", 1)[0]
    return code in {"401", "403", "407"}


def should_open_auth_repair(auth_failures: int) -> bool:
    return auth_failures >= AUTH_FAIL_ISSUE_AFTER
