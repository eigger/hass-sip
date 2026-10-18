"""Phone-line media player: state and play_media URL handling."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_IDLE, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.sip.sip_client.sip_client import SipState

from .common import phone_line, wait_for


async def test_play_media_requires_active_call(
    hass: HomeAssistant, init_integration
) -> None:
    target = phone_line(hass, init_integration)
    with pytest.raises(HomeAssistantError, match="not in an active call") as err:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                **target,
                ATTR_MEDIA_CONTENT_TYPE: "music",
                ATTR_MEDIA_CONTENT_ID: "http://example.invalid/a.mp3",
            },
            blocking=True,
        )
    assert err.value.translation_domain == "sip"
    assert err.value.translation_key == "not_in_call"


async def test_media_source_url_is_prepared_and_log_omits_signature(
    hass: HomeAssistant,
    init_integration,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A media-source item is resolved and signed for ffmpeg; the log is not."""
    entry = init_integration
    client = entry.runtime_data["client"]
    target = phone_line(hass, entry)
    hass.config.internal_url = "http://hass.lan:8123"
    (tmp_path / "elevator.mp3").write_bytes(b"ID3")
    # media_source is a manifest dependency, so HA loaded it with the
    # integration; its local source reads this dict, so update it in place.
    assert "media_source" in hass.config.components
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(tmp_path)

    assert hass.states.get(target[ATTR_ENTITY_ID]).state == STATE_OFF
    await hass.services.async_call(
        "sip", "dial", {"number": "200", **target}, blocking=True
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.async_block_till_done()
    assert hass.states.get(target[ATTR_ENTITY_ID]).state == STATE_IDLE

    caplog.set_level(logging.INFO, logger="custom_components.sip")
    # play_source hands the source to the RTP sender (ffmpeg + UDP); capture it.
    with patch.object(client, "play_source") as play_source:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                **target,
                ATTR_MEDIA_CONTENT_TYPE: "audio/mpeg",
                ATTR_MEDIA_CONTENT_ID: "media-source://media_source/local/elevator.mp3",
            },
            blocking=True,
        )

    url = play_source.call_args.args[0]._url
    assert url.startswith("http://hass.lan:8123/media/local/elevator.mp3?authSig=")
    assert (
        "Streaming media to SIP call: http://hass.lan:8123/media/local/elevator.mp3"
        in (caplog.text)
    )
    assert "authSig" not in caplog.text

    await hass.services.async_call("sip", "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)
