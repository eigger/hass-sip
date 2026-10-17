"""Media-player URL handling tests."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

from test_pure import _load_component_module


class _MediaPlayerEntity:
    """Minimal entity base used while importing the platform."""


def _load_media_player_module():
    media_player = types.ModuleType("homeassistant.components.media_player")
    for name in (
        "BrowseMedia",
        "MediaPlayerEntityFeature",
        "MediaPlayerState",
        "MediaType",
    ):
        setattr(media_player, name, MagicMock())
    media_player.MediaPlayerEntity = _MediaPlayerEntity
    media_player.async_process_play_media_url = MagicMock()

    media_source = types.ModuleType("homeassistant.components.media_source")
    media_source.is_media_source_id = MagicMock()
    media_source.async_resolve_media = MagicMock()

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = RuntimeError

    helpers = types.ModuleType("custom_components.sip.helpers")
    helpers.build_device_info = MagicMock()
    helpers.get_ffmpeg_bin = MagicMock(return_value="ffmpeg")

    saved = {
        name: sys.modules.get(name)
        for name in (
            "homeassistant.components.media_player",
            "homeassistant.components.media_source",
            "homeassistant.exceptions",
            "custom_components.sip.helpers",
        )
    }
    sys.modules.update(
        {
            "homeassistant.components.media_player": media_player,
            "homeassistant.components.media_source": media_source,
            "homeassistant.exceptions": exceptions,
            "custom_components.sip.helpers": helpers,
        }
    )
    sys.modules["homeassistant.components"].media_source = media_source
    try:
        module = _load_component_module("media_player")
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module


media_player = _load_media_player_module()


def test_media_source_url_is_prepared_and_log_omits_signature():
    async def run():
        source_item = types.SimpleNamespace(url="/media/local/elevator.mp3")
        media_player.media_source.is_media_source_id.return_value = True

        async def resolve(*_args):
            return source_item

        media_player.media_source.async_resolve_media = resolve
        media_player.async_process_play_media_url = MagicMock(
            return_value=(
                "http://hass.lan:8123/media/local/elevator.mp3?authSig=secret"
            )
        )
        client = MagicMock(in_call=True)
        entity = media_player.SipMediaPlayer.__new__(media_player.SipMediaPlayer)
        entity.hass = MagicMock()
        entity.entity_id = "media_player.phone_line"
        entity._client = client
        entity.async_write_ha_state = MagicMock()

        original_info = media_player.LOGGER.info
        media_player.LOGGER.info = MagicMock()
        try:
            await entity.async_play_media("audio/mpeg", "media-source://local/test")
            logged = media_player.LOGGER.info.call_args.args[1]
        finally:
            media_player.LOGGER.info = original_info

        return client, logged

    client, logged = asyncio.run(run())
    media_player.async_process_play_media_url.assert_called_once()
    assert client.play_source.call_args.args[0]._url.endswith("authSig=secret")
    assert logged == "http://hass.lan:8123/media/local/elevator.mp3"
