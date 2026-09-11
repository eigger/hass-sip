"""Assist caller allow-list and PIN gate. Kept free of Home Assistant imports."""
from __future__ import annotations

import asyncio

REASON_NOT_ALLOWED = "not_allowed"
REASON_PIN_MISMATCH = "pin_mismatch"
REASON_PIN_TIMEOUT = "pin_timeout"

_PIN_WAIT_SECONDS = 15.0


def normalize_caller(value: str) -> str:
    """Return a comparable caller identity (SIP URI user-part or extension).

    Strips SIP wrapping, spaces, and dashes, then lowercases the user-part.
    Phone numbers stay numbers (``100``, ``+821012345678``). Alphanumeric
    extensions (``doorbird1``, ``kitchen1``, ``cam2``) keep the full name so a
    shared trailing digit cannot collapse distinct identities.
    """
    raw = (value or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    lower = raw.lower()
    if lower.startswith("sip:"):
        raw = raw[4:]
    elif lower.startswith("sips:"):
        raw = raw[5:]
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return raw.replace(" ", "").replace("-", "").lower()


def caller_is_allowed(
    caller: str,
    *,
    allowed_callers: list[str] | None = None,
    contacts: dict | None = None,
    contacts_only: bool = False,
) -> bool:
    """True when Assist may start for ``caller``.

    No list and ``contacts_only`` false keeps the historical open behaviour.
    An explicit list and ``contacts_only`` both set must all match (intersection).
    """
    if allowed_callers is None and not contacts_only:
        return True
    ident = normalize_caller(caller)
    if not ident:
        return False
    if allowed_callers is not None:
        allowed = {normalize_caller(item) for item in allowed_callers if item}
        if ident not in allowed:
            return False
    if contacts_only:
        keys = {normalize_caller(str(key)) for key in (contacts or {})}
        if ident not in keys:
            return False
    return True


class PinCollector:
    """Collect DTMF until it matches ``expected`` or times out.

    The entered digits are never exposed on the result (ok / mismatch / timeout).
    """

    def __init__(self, expected: str) -> None:
        self._expected = expected
        self._buf = ""
        self._result: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def handle_digit(self, digit: str) -> None:
        if self._result.done() or not digit:
            return
        if digit == "#":
            self._finish(self._buf == self._expected)
            return
        self._buf += digit
        if self._expected and len(self._buf) >= len(self._expected):
            self._finish(self._buf == self._expected)

    def fail(self) -> None:
        """Treat hangup as a failed PIN attempt."""
        self._finish(False)

    def _finish(self, ok: bool) -> None:
        if not self._result.done():
            self._result.set_result(ok)

    async def wait(self, timeout: float | None = None) -> str:
        if timeout is None:
            timeout = _PIN_WAIT_SECONDS
        try:
            async with asyncio.timeout(timeout):
                ok = await self._result
            return "ok" if ok else REASON_PIN_MISMATCH
        except TimeoutError:
            self.fail()
            return REASON_PIN_TIMEOUT


def take_pin_digit(collector: PinCollector | None, digit: str) -> bool:
    """Consume ``digit`` for an active PIN wait.

    Returns True when the digit was handled privately. The caller must not
    log it or fire ``sip_dtmf_digit``.
    """
    if collector is None:
        return False
    collector.handle_digit(digit)
    return True
