"""Sensor platforms for the SIP Client integration."""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Set up SIP Client sensors from a config entry."""
    entry_data = entry.runtime_data

    sensors = [
        SipRegistrationSensor(entry, entry_data),
        SipLastCallSensor(entry, entry_data),
    ]

    async_add_entities(sensors)


class SipRegistrationSensor(SensorEntity):
    """Monitors the registration status of the SIP client."""

    _attr_icon = "mdi:phone-check"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["registered", "registering", "unregistered"]

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the registration sensor."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_registration_status"
        self._attr_translation_key = "registration_status"
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
    def native_value(self) -> str:
        """Return the registration state."""
        if self.entry_data.get("registered", False):
            return "registered"
        state = self.entry_data.get("state", SipState.IDLE)
        if state == SipState.REGISTERING:
            return "registering"
        return "unregistered"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return config details as attributes."""
        return {
            "server": self._config.server,
            "username": self._config.username,
            "port": self._config.port,
            "domain": self._config.domain,
        }


class SipLastCallSensor(SensorEntity):
    """Monitors information about the most recent SIP call."""

    _attr_icon = "mdi:phone"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["none", "incoming", "outgoing"]

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the last call sensor."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_last_call"
        self._attr_translation_key = "last_call"
        self._attr_device_info = build_device_info(entry, self._config)
        self._last_state = "none"
        self._timestamp: str | None = None

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
        """Calculate state based on latest SipClient state."""
        state = self.entry_data.get("state", SipState.IDLE)
        # Transitioning to call state
        if state in (
            SipState.IN_CALL,
            SipState.ANSWERING,
            SipState.INVITING,
            SipState.RINGING_OUT,
        ):
            is_outbound = state in (SipState.INVITING, SipState.RINGING_OUT)
            self._last_state = "outgoing" if is_outbound else "incoming"
            self._timestamp = datetime.datetime.now().isoformat()
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return the call direction of the last call."""
        return self._last_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return last caller metadata."""
        return {
            "last_caller": self._client.last_caller,
            "timestamp": self._timestamp,
            "username": self._config.username,
            "call_history": self.entry_data.get("call_history", []),
        }
