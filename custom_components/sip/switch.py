"""Switch platform for SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .helpers import build_device_info
from .sip_client.sip_client import SipClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SIP Client switch from a config entry."""
    entry_data = entry.runtime_data
    async_add_entities([SipDndSwitch(entry, entry_data)])


class SipDndSwitch(SwitchEntity):
    """Representation of a DND switch for the SIP client."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the DND switch."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_dnd"
        self._attr_translation_key = "dnd"
        self._attr_device_info = build_device_info(entry, self._config)

    @property
    def icon(self) -> str:
        """DND on -> phone-off (calls blocked); DND off -> phone (normal)."""
        return "mdi:phone-off" if self._client.dnd else "mdi:phone"

    @property
    def is_on(self) -> bool:
        """Return true if DND is on."""
        return self._client.dnd

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn DND on."""
        self._client.dnd = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn DND off."""
        self._client.dnd = False
        self.async_write_ha_state()
