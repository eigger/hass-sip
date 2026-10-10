"""Button platform for SIP Client integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .helpers import build_device_info
from .sip_client.sip_client import SipClient, SipState

_LOGGER = logging.getLogger(__name__)


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


class SipCallButton(ButtonEntity):
    """Base button that tracks call state so dashboards can hide it when idle."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the button."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_device_info = build_device_info(entry, self._config)

    async def async_added_to_hass(self) -> None:
        """Subscribe to state updates so ``can_press`` stays current."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_state_update_{self.entry.entry_id}",
                self._update_callback,
            )
        )

    @callback
    def _update_callback(self) -> None:
        self.async_write_ha_state()

    @property
    def _can_press(self) -> bool:
        """Whether pressing the button does something in the current state."""
        raise NotImplementedError

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose whether the action applies now for conditional cards/automations."""
        return {"can_press": self._can_press}


class SipAnswerButton(SipCallButton):
    """Button to answer an incoming SIP call."""

    _attr_icon = "mdi:phone"
    _attr_translation_key = "answer"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the answer button."""
        super().__init__(entry, entry_data)
        self._attr_unique_id = f"{entry.entry_id}_answer"

    @property
    def _can_press(self) -> bool:
        """Answering only applies while a call is ringing in."""
        return self.entry_data.get("state") == SipState.INCOMING

    async def async_press(self) -> None:
        """Press the button to answer."""
        self._client.answer()


class SipHangupButton(SipCallButton):
    """Button to hang up the current SIP call."""

    _attr_icon = "mdi:phone-hangup"
    _attr_translation_key = "hangup"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the hangup button."""
        super().__init__(entry, entry_data)
        self._attr_unique_id = f"{entry.entry_id}_hangup"

    @property
    def _can_press(self) -> bool:
        """Hanging up applies whenever any call leg is active."""
        return self.entry_data.get("state") in (
            SipState.INVITING,
            SipState.RINGING_OUT,
            SipState.INCOMING,
            SipState.ANSWERING,
            SipState.IN_CALL,
        )

    async def async_press(self) -> None:
        """Press the button to hang up."""
        state = self.entry_data.get("state")
        _LOGGER.debug(
            "Hang Up button press requested for entry %s (entity state=%s, client state=%s)",
            self.entry.entry_id,
            state,
            self._client.state,
        )
        if not self._can_press:
            _LOGGER.debug("Hang Up button press rejected: no active call")
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_active_call"
            )
        self._client.hangup()
        _LOGGER.debug(
            "Hang Up button press returned for entry %s (client state=%s)",
            self.entry.entry_id,
            self._client.state,
        )
