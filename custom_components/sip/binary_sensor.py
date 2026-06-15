"""Binary sensor platforms for the SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .helpers import build_device_info
from .sip_client.sip_client import SipClient, SipState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SIP Client binary sensors from a config entry."""
    entry_data = entry.runtime_data

    binary_sensors = [
        SipCallActiveSensor(entry, entry_data),
    ]

    async_add_entities(binary_sensors)


class SipCallActiveSensor(BinarySensorEntity):
    """Monitors if a call is currently active on the SIP line."""

    _attr_icon = "mdi:phone-in-talk"
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the call active binary sensor."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_call_active"
        self._attr_translation_key = "call_active"
        self._attr_device_info = build_device_info(entry, self._config)

    async def async_added_to_hass(self) -> None:
        """Register dispatcher callback on mount."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_state_update_{self.entry.entry_id}",
                self._update_callback,
            )
        )

    @callback
    def _update_callback(self) -> None:
        """Update HA state."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return True if a call is active, ringing, or incoming."""
        state = self.entry_data.get("state", SipState.IDLE)
        return state in (
            SipState.INVITING,
            SipState.RINGING_OUT,
            SipState.INCOMING,
            SipState.ANSWERING,
            SipState.IN_CALL,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return call details as attributes when active."""
        state = self.entry_data.get("state", SipState.IDLE)
        return {
            "sip_state": str(state),
            "in_call": self._client.in_call,
            "username": self._config.username,
        }
