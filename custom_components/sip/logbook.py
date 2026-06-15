"""Logbook descriptions for SIP Client events.

Event entities only ever render a generic "detected an event" line in the
logbook, so we describe the integration's bus events here to produce readable
messages (and attach the device id so they show in the device's logbook tab).
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    DOMAIN,
    EVENT_SIP_CALL_CONNECTED,
    EVENT_SIP_CALL_ENDED,
    EVENT_SIP_DTMF_DIGIT,
    EVENT_SIP_INCOMING_CALL,
    EVENT_SIP_PLAYBACK_DONE,
    EVENT_SIP_RECORDING_STARTED,
    EVENT_SIP_RECORDING_STOPPED,
    EVENT_SIP_REGISTERED,
)


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe SIP bus events for the logbook."""

    def _name(data: dict) -> str:
        account = data.get("sip_account")
        return f"SIP ({account})" if account else "SIP"

    @callback
    def describe_incoming(event: Event) -> dict[str, str]:
        data = event.data
        who = data.get("caller_name") or data.get("caller") or "unknown"
        return {LOGBOOK_ENTRY_NAME: _name(data), LOGBOOK_ENTRY_MESSAGE: f"incoming call from {who}"}

    @callback
    def describe_connected(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: "call connected"}

    @callback
    def describe_ended(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: "call ended"}

    @callback
    def describe_dtmf(event: Event) -> dict[str, str]:
        digit = event.data.get("digit", "")
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: f"DTMF keypress {digit}".strip()}

    @callback
    def describe_playback_done(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: "playback finished"}

    @callback
    def describe_recording_started(event: Event) -> dict[str, str]:
        target = event.data.get("recording_file")
        message = f"recording started ({target})" if target else "recording started"
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: message}

    @callback
    def describe_recording_stopped(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: "recording stopped"}

    @callback
    def describe_registered(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: _name(event.data), LOGBOOK_ENTRY_MESSAGE: "registered"}

    async_describe_event(DOMAIN, EVENT_SIP_INCOMING_CALL, describe_incoming)
    async_describe_event(DOMAIN, EVENT_SIP_CALL_CONNECTED, describe_connected)
    async_describe_event(DOMAIN, EVENT_SIP_CALL_ENDED, describe_ended)
    async_describe_event(DOMAIN, EVENT_SIP_DTMF_DIGIT, describe_dtmf)
    async_describe_event(DOMAIN, EVENT_SIP_PLAYBACK_DONE, describe_playback_done)
    async_describe_event(DOMAIN, EVENT_SIP_RECORDING_STARTED, describe_recording_started)
    async_describe_event(DOMAIN, EVENT_SIP_RECORDING_STOPPED, describe_recording_stopped)
    async_describe_event(DOMAIN, EVENT_SIP_REGISTERED, describe_registered)
