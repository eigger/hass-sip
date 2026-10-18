"""Logbook descriptions for SIP bus events."""

from __future__ import annotations

from homeassistant.components.logbook import DOMAIN as LOGBOOK_DOMAIN
from homeassistant.core import Event, HomeAssistant

from custom_components.sip.const import DOMAIN, EVENT_SIP_PLAYBACK_DONE


async def test_playback_done_describes_success_and_failure(
    hass: HomeAssistant, init_integration
) -> None:
    """The sip logbook platform is registered by HA's real logbook component."""
    domain, describe = hass.data[LOGBOOK_DOMAIN].external_events[
        EVENT_SIP_PLAYBACK_DONE
    ]
    assert domain == DOMAIN

    ok = describe(Event(EVENT_SIP_PLAYBACK_DONE, {"sip_account": "100"}))
    assert ok == {"name": "SIP (100)", "message": "playback finished"}

    failed = describe(
        Event(
            EVENT_SIP_PLAYBACK_DONE,
            {"sip_account": "100", "error": "ffmpeg exited with status 1"},
        )
    )
    assert failed["message"] == "playback failed: ffmpeg exited with status 1"
