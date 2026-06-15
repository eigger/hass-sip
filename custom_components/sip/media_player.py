"""Media Player platform for SIP Client integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.STOP
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

        # If we are in call, check if an audio source is playing.
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

    # -- read-only call/playback info shown on the card -----------------
    @property
    def media_content_type(self) -> str | None:
        """Mark playback as music so the card renders the now-playing area."""
        if (
            self.entry_data.get("state") == SipState.IN_CALL
            and self._client.media_playing
        ):
            return MediaType.MUSIC
        return None

    @property
    def media_title(self) -> str | None:
        """Human-readable description of what the line is doing."""
        state = self.entry_data.get("state", SipState.IDLE)
        if state in (SipState.INVITING, SipState.RINGING_OUT):
            return "Dialing"
        if state == SipState.INCOMING:
            return "Incoming call"
        if state == SipState.ANSWERING:
            return "Connecting"
        if state == SipState.IN_CALL:
            return "Playing message" if self._client.media_playing else "In call"
        return None

    @property
    def media_artist(self) -> str | None:
        """The remote party (friendly name if known, otherwise the number)."""
        if self.entry_data.get("state", SipState.IDLE) == SipState.IDLE:
            return None
        party = self.entry_data.get("last_caller") or self.entry_data.get("call_number")
        return party or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose call details for dashboards/automations."""
        state = self.entry_data.get("state", SipState.IDLE)
        return {
            "sip_state": str(state),
            "remote_party": self.entry_data.get("call_number") or "",
            "remote_name": self.entry_data.get("last_caller") or "",
            "call_direction": self.entry_data.get("call_direction"),
        }

    # -- controls -------------------------------------------------------
    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse audio media (TTS engines, local media) to play into the call."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a piece of media (URL or TTS media source ID) to the call."""
        if not self._client.in_call:
            raise HomeAssistantError(
                "Cannot play media: the SIP line is not in an active call"
            )

        url = media_id
        # Resolve a media source ID if provided (e.g. TTS or the media browser).
        if media_source.is_media_source_id(media_id):
            try:
                media_item = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id
                )
                url = media_item.url
            except Exception as err:
                raise HomeAssistantError(
                    f"Failed to resolve media source '{media_id}': {err}"
                ) from err

        # Prepend the base URL for relative paths (local media / TTS proxy).
        if url.startswith("/"):
            try:
                url = get_url(self.hass) + url
            except Exception as err:
                raise HomeAssistantError(
                    f"Failed to resolve a base URL for '{url}'. Set an internal "
                    f"URL in Home Assistant network settings: {err}"
                ) from err

        LOGGER.info("Streaming media to SIP call: %s", url)
        source = FfmpegAudioSource(url=url, ffmpeg_bin=get_ffmpeg_bin(self.hass))
        self._client.play_source(source)
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop playing the current audio source (does not end the call)."""
        self._client.stop_audio()
        self.async_write_ha_state()
