"""Media Player platform for SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url

from .const import DOMAIN, LOGGER
from .helpers import build_device_info, get_ffmpeg_bin
from .sip_client.audio import FfmpegAudioSource
from .sip_client.sip_client import SipClient, SipState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SIP Client media player from a config entry."""
    entry_data = entry.runtime_data
    async_add_entities([SipMediaPlayer(entry, entry_data)])


class SipMediaPlayer(MediaPlayerEntity):
    """Media Player representing the active SIP call stream."""

    _attr_icon = "mdi:phone-in-talk"
    _attr_has_entity_name = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        """Initialize the media player."""
        self.entry = entry
        self.entry_data = entry_data
        self._client: SipClient = entry_data["client"]
        self._config = entry_data["config"]
        self._attr_unique_id = f"{entry.entry_id}_media_player"
        self._attr_translation_key = "phone_line"
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
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        state = self.entry_data.get("state", SipState.IDLE)
        if state == SipState.IDLE:
            return MediaPlayerState.OFF

        # If we are in call, check if audio source is playing
        if state == SipState.IN_CALL:
            if self._client.media_playing:
                return MediaPlayerState.PLAYING
            return MediaPlayerState.IDLE

        if state in (
            SipState.INVITING,
            SipState.RINGING_OUT,
            SipState.INCOMING,
            SipState.ANSWERING,
        ):
            return MediaPlayerState.ON

        return MediaPlayerState.OFF

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a piece of media (URL or TTS media source ID) to the call."""
        if not self._client.in_call:
            LOGGER.warning("Cannot play media: SIP client is not in an active call")
            return

        url = media_id
        # Resolve media source ID if provided (e.g. for TTS or media browser)
        if media_source.is_media_source_id(media_id):
            try:
                media_item = await media_source.async_resolve_media(self.hass, media_id, None)
                url = media_item.url
            except Exception as err:
                LOGGER.error("Failed to resolve media source ID %s: %s", media_id, err)
                return

        # Prepend base URL for relative paths (e.g. local media or TTS proxy paths)
        if url.startswith("/"):
            try:
                base_url = get_url(self.hass)
                url = base_url + url
            except Exception as err:
                LOGGER.error("Failed to resolve base URL for relative media path: %s", err)
                return

        LOGGER.info("Streaming media to SIP call: %s", url)
        try:
            source = FfmpegAudioSource(url=url, ffmpeg_bin=get_ffmpeg_bin(self.hass))
            self._client.play_source(source)
            self.async_write_ha_state()
        except Exception as err:
            LOGGER.error("Failed to play media over SIP stream: %s", err)

    async def async_media_stop(self) -> None:
        """Stop playing the current audio source."""
        self._client.stop_audio()
        self.async_write_ha_state()
