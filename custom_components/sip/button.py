"""Button platform for SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Set up SIP Client buttons from a config entry."""
    entry_data = entry.runtime_data
    async_add_entities([
        SipAnswerButton(entry, entry_data),
        SipHangupButton(entry, entry_data),
    ])


class SipAnswerButton(ButtonEntity):
    """Button to answer an incoming SIP call."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:phone"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the answer button."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_answer"
        self._attr_translation_key = "answer"
        self._attr_device_info = build_device_info(entry, self._config)

    async def async_press(self) -> None:
        """Press the button to answer."""
        self._client.answer()


class SipHangupButton(ButtonEntity):
    """Button to hang up the current SIP call."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-hangup"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the hangup button."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_hangup"
        self._attr_translation_key = "hangup"
        self._attr_device_info = build_device_info(entry, self._config)

    async def async_press(self) -> None:
        """Press the button to hang up."""
        self._client.hangup()
