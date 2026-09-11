"""Opt-in SIP/RTP protocol trace with credential masking.

Enable from Home Assistant ``configuration.yaml``:

    logger:
      logs:
        custom_components.sip.sip_client.trace: debug

Off by default. Callers must go through :func:`log_sip` / :func:`log_rtp`
(or check :func:`enabled`) before building log strings so masking and
format work are skipped when the logger is not at DEBUG.
"""
from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger(__name__)

REDACTED = "****"

# Digest fields that are the response hash or the challenge material used
# to compute it. Applied only on Authorization / Proxy-Authorization lines.
_SECRET_PARAM = re.compile(
    r"\b(response|nonce|cnonce|password)\s*=\s*"
    r'(?:"[^"]*"|\'[^\']*\'|[^\s,]+)',
    re.IGNORECASE,
)

_BASIC_AUTH = re.compile(
    r"^((?:Proxy-)?Authorization:\s*Basic)\s+\S+",
    re.IGNORECASE,
)


def enabled() -> bool:
    """True when the trace logger will emit DEBUG records."""
    return _LOGGER.isEnabledFor(logging.DEBUG)


def _redact_param(match: re.Match[str]) -> str:
    return f'{match.group(1)}="{REDACTED}"'


def mask_sip(msg: str) -> str:
    """Return a SIP message with digest secrets replaced by ``****``."""
    parts: list[str] = []
    for line in msg.splitlines(keepends=True):
        if line.endswith("\r\n"):
            body, ending = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, ending = line[:-1], "\n"
        elif line.endswith("\r"):
            body, ending = line[:-1], "\r"
        else:
            body, ending = line, ""
        stripped = body.lstrip(" \t")
        lead = body[: len(body) - len(stripped)]
        lower = stripped.lower()
        if lower.startswith("authorization:") or lower.startswith(
            "proxy-authorization:"
        ):
            basic = _BASIC_AUTH.sub(rf"\1 {REDACTED}", stripped, count=1)
            if basic != stripped:
                stripped = basic
            else:
                stripped = _SECRET_PARAM.sub(_redact_param, stripped)
        parts.append(lead + stripped + ending)
    return "".join(parts)


def log_sip(direction: str, msg: str) -> None:
    """Log a SIP datagram at DEBUG after masking, or no-op if disabled."""
    if not enabled():
        return
    _LOGGER.debug("%s\n%s", direction, mask_sip(msg))


def log_rtp(fmt: str, *args) -> None:
    """Log an RTP summary line at DEBUG, or no-op if disabled."""
    if not enabled():
        return
    _LOGGER.debug(fmt, *args)
