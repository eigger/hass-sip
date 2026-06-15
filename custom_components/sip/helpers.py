"""Helper utilities for the SIP Client integration."""
from __future__ import annotations

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def get_ffmpeg_bin(hass: HomeAssistant) -> str:
    """Return the ffmpeg binary path configured by the HA ffmpeg integration.

    ``ffmpeg`` is a hard dependency of this integration (see manifest), so the
    manager is always available here.
    """
    return get_ffmpeg_manager(hass).binary


def build_device_info(entry: ConfigEntry, config) -> DeviceInfo:
    """Common device info for all entities of one SIP account.

    Surfaces the extension number and the SIP server so the device card is
    self-explanatory.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"SIP Extension {config.username}",
        manufacturer="Home Assistant",
        model=f"SIP Extension ({config.username} @ {config.server})",
        serial_number=config.username,
        configuration_url=f"http://{config.server}",
    )
