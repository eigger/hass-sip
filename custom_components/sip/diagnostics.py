"""Config-entry diagnostics for the SIP Client integration.

Download from Settings → Devices & Services → SIP Client → ⋮ → Download
diagnostics. The payload is meant to distinguish registration failure, codec
mismatch, and one-way audio without exposing passwords.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_AUTH_USERNAME, CONF_PASSWORD

TO_REDACT = {CONF_PASSWORD, "password", CONF_AUTH_USERNAME}

_KEEP_DIGITS = 2


def mask_number(number: str | None) -> str | None:
    """Mask a phone number / SIP user, keeping the last two digits."""
    if not number:
        return number
    digits = sum(1 for c in number if c.isdigit())
    if digits == 0:
        return "*" * len(number)
    seen = 0
    out: list[str] = []
    for char in number:
        if char.isdigit():
            seen += 1
            out.append(char if seen > digits - _KEEP_DIGITS else "*")
        else:
            out.append(char)
    return "".join(out)


def _mask_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    masked = dict(entry)
    if "number" in masked:
        masked["number"] = mask_number(masked.get("number"))
    return masked


def collect_diagnostics(
    config: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Build the diagnostics payload (before HA credential redaction)."""
    client = runtime.get("client")
    sip = client.diagnostics_snapshot() if client is not None else {}
    call = sip.get("call")
    if isinstance(call, dict) and call.get("last_caller"):
        sip = {
            **sip,
            "call": {**call, "last_caller": mask_number(call["last_caller"])},
        }
    history = [_mask_history_entry(item) for item in runtime.get("call_history", [])]
    start = runtime.get("call_start_time")
    connect = runtime.get("call_connect_time")
    current = {
        "direction": runtime.get("call_direction"),
        "status": runtime.get("call_status"),
        "start_time": start,
        "connect_time": connect,
        "number": mask_number(runtime.get("call_number") or None),
    }
    return {
        "config": dict(config),
        "sip": sip,
        "current_call": current,
        "call_history": history,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    del hass  # required by the HA diagnostics platform signature
    runtime = entry.runtime_data if isinstance(entry.runtime_data, dict) else {}
    payload = collect_diagnostics(dict(entry.data), runtime)
    return async_redact_data(payload, TO_REDACT)
