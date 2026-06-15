"""Event platform for SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .helpers import build_device_info
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
    LOGGER,
)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the SIP Event entity."""
    entry_data = config_entry.runtime_data
    entity = SipTelephonyEventEntity(config_entry, entry_data)
    async_add_entities([entity])


class SipTelephonyEventEntity(EventEntity):
    """Event entity representing SIP telephony events."""

    _attr_has_entity_name = True
    _attr_translation_key = "telephony"
    _attr_event_types = [
        "incoming",
        "connected",
        "playback_done",
        "dtmf",
        "ended",
        "recording_started",
        "recording_stopped",
        "registered",
    ]

    def __init__(self, config_entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the event entity."""
        self._entry_data = entry_data
        self._entry_id = config_entry.entry_id
        self._attr_unique_id = f"{config_entry.entry_id}_telephony_events"

        # Group under the unified SIP device shared by all platform entities.
        self._attr_device_info = build_device_info(config_entry, entry_data["config"])

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to hass."""
        await super().async_added_to_hass()

        signal = f"{DOMAIN}_event_{self._entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._handle_sip_event)
        )

    @callback
    def _handle_sip_event(self, event_type: str, extra_data: dict[str, Any] | None) -> None:
        """Handle incoming SIP dispatcher events and update entity state."""
        LOGGER.debug("SipEventEntity received event %s: %s", event_type, extra_data)

        # Map raw event types to event_types supported by EventEntity
        mapped_type: str | None = None
        if event_type == EVENT_SIP_INCOMING_CALL:
            mapped_type = "incoming"
        elif event_type == EVENT_SIP_CALL_CONNECTED:
            mapped_type = "connected"
        elif event_type == EVENT_SIP_PLAYBACK_DONE:
            mapped_type = "playback_done"
        elif event_type == EVENT_SIP_DTMF_DIGIT:
            mapped_type = "dtmf"
        elif event_type == EVENT_SIP_CALL_ENDED:
            mapped_type = "ended"
        elif event_type == EVENT_SIP_RECORDING_STARTED:
            mapped_type = "recording_started"
        elif event_type == EVENT_SIP_RECORDING_STOPPED:
            mapped_type = "recording_stopped"
        elif event_type == EVENT_SIP_REGISTERED:
            mapped_type = "registered"

        if mapped_type in self._attr_event_types:
            self._trigger_event(mapped_type, extra_data or {})
            self.async_write_ha_state()
