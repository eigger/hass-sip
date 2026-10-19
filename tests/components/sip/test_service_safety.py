"""What a non-admin caller can reach through sip.* service data.

Real Home Assistant, real config entry against the mock PBX, real service
calls made as a (non-admin) user. Covers the recording path policy as wired
in ``sip.start_recording``, the caller context carried into IVR menus, and
how caller-supplied media (IVR ``audio_file``, Assist tone media,
``media_player.play_media``) is resolved before it reaches ffmpeg.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockUser,
    async_capture_events,
)

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_register_admin_service

from custom_components.sip.assist import AssistBridge
from custom_components.sip.const import DOMAIN, EVENT_SIP_RECORDING_STARTED
from custom_components.sip.helpers import async_resolve_playback_url
from custom_components.sip.sip_client.audio import FfmpegAudioSource, ToneAudioSource
from custom_components.sip.sip_client.sip_client import SipState

from .common import phone_line, wait_for


def _line_user(hass: HomeAssistant, target: dict) -> MockUser:
    """A non-admin user who may control only the account's phone line."""
    user = MockUser().add_to_hass(hass)
    assert not user.is_admin
    user.mock_policy({"entities": {"entity_ids": {target["entity_id"]: True}}})
    return user


# ---------------------------------------------------------- start_recording
@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """A throwaway config dir holding real config files, with media under it.

    Mirrors a non-HA-OS install: ``<config>/media`` is the ``local`` media dir
    and the only allowlisted external dir.
    """
    config = tmp_path / "config"
    media = config / "media"
    (media / "sip").mkdir(parents=True)
    (config / "configuration.yaml").write_text("homeassistant:\n")
    (config / "secrets.yaml").write_text("pin: 1234\n")
    # A stray non-WAV file that happens to have a .wav name.
    (media / "sip" / "notes.wav").write_text("not audio")
    hass.config.config_dir = str(config)
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(media)
    hass.config.allowlist_external_dirs = {str(media)}
    return config


async def _start_recording(
    hass: HomeAssistant, target: dict, recording_file: str, user: MockUser
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "start_recording",
        {"recording_file": recording_file, **target},
        blocking=True,
        context=Context(user_id=user.id),
    )


@pytest.mark.parametrize(
    "recording_file",
    [
        "{config}/configuration.yaml",
        "{config}/secrets.yaml",
        "../configuration.yaml",
        "../secrets.yaml",
        "sip/../../configuration.yaml",
        "{config}/evil.wav",
        "sip/notes.wav",
        "sip/rec.txt",
        "sip/rec",
    ],
)
async def test_start_recording_refuses_unsafe_targets(
    hass: HomeAssistant,
    init_integration,
    config_dir: Path,
    recording_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    started = async_capture_events(hass, EVENT_SIP_RECORDING_STARTED)
    before = {
        path: path.read_bytes() for path in config_dir.rglob("*") if path.is_file()
    }

    await _start_recording(hass, target, recording_file.format(config=config_dir), user)
    await hass.async_block_till_done()

    assert entry.runtime_data.get("recorder") is None
    assert started == []
    assert "Recording path" in caplog.text
    after = {
        path: path.read_bytes() for path in config_dir.rglob("*") if path.is_file()
    }
    assert after == before  # nothing created, truncated or overwritten


async def test_start_recording_relative_path_lands_in_media_dir(
    hass: HomeAssistant, init_integration, config_dir: Path
) -> None:
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    started = async_capture_events(hass, EVENT_SIP_RECORDING_STARTED)

    await _start_recording(hass, target, "sip/rec.wav", user)

    expected = config_dir / "media" / "sip" / "rec.wav"
    recorder = entry.runtime_data["recorder"]
    assert recorder is not None
    assert recorder.path == str(expected)
    assert [e.data["recording_file"] for e in started] == [str(expected)]

    await hass.services.async_call(
        DOMAIN,
        "stop_recording",
        target,
        blocking=True,
        context=Context(user_id=user.id),
    )
    assert entry.runtime_data.get("recorder") is None
    assert expected.is_file()
    assert (config_dir / "configuration.yaml").read_text() == "homeassistant:\n"


async def test_start_recording_can_be_reissued_to_the_same_path_before_audio(
    hass: HomeAssistant, init_integration, config_dir: Path
) -> None:
    """A 0-byte leftover (no audio yet, or a crash) must not block the path."""
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    started = async_capture_events(hass, EVENT_SIP_RECORDING_STARTED)
    expected = config_dir / "media" / "sip" / "again.wav"
    expected.write_bytes(b"")  # what a recorder that never got audio leaves

    await _start_recording(hass, target, "sip/again.wav", user)
    first = entry.runtime_data["recorder"]
    # No call, so no audio ever arrives: re-issue right away.
    await _start_recording(hass, target, "sip/again.wav", user)
    second = entry.runtime_data["recorder"]

    assert second is not first
    assert second.path == str(expected)
    assert [e.data["recording_file"] for e in started] == [str(expected)] * 2

    await hass.services.async_call(
        DOMAIN,
        "stop_recording",
        target,
        blocking=True,
        context=Context(user_id=user.id),
    )
    assert entry.runtime_data.get("recorder") is None
    assert expected.read_bytes()[:4] == b"RIFF"


@pytest.fixture
def config_dir_allowlisted(config_dir: Path, hass: HomeAssistant) -> Path:
    """``config_dir`` with the whole config dir allowlisted.

    Also lists ``<config>/www``, which HA allowlists by default.
    """
    (config_dir / "www").mkdir()
    hass.config.allowlist_external_dirs = {
        str(config_dir),
        str(config_dir / "www"),
        str(config_dir / "media"),
    }
    return config_dir


@pytest.mark.parametrize(
    "recording_file",
    [
        "{config}/evil.wav",
        "{config}/www/rec.wav",
        "{config}/.storage/rec.wav",
        "../evil.wav",
        "../www/rec.wav",
    ],
)
async def test_start_recording_refuses_config_dir_even_when_allowlisted(
    hass: HomeAssistant,
    init_integration,
    config_dir_allowlisted: Path,
    recording_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_dir = config_dir_allowlisted
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    started = async_capture_events(hass, EVENT_SIP_RECORDING_STARTED)
    before = set(config_dir.rglob("*"))

    await _start_recording(hass, target, recording_file.format(config=config_dir), user)
    await hass.async_block_till_done()

    assert entry.runtime_data.get("recorder") is None
    assert started == []
    assert "inside the configuration directory" in caplog.text
    assert set(config_dir.rglob("*")) == before


@pytest.mark.parametrize("recording_file", ["{config}/media/sip/ok.wav", "sip/ok.wav"])
async def test_start_recording_allows_media_dir_under_allowlisted_config(
    hass: HomeAssistant,
    init_integration,
    config_dir_allowlisted: Path,
    recording_file: str,
) -> None:
    config_dir = config_dir_allowlisted
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)

    await _start_recording(hass, target, recording_file.format(config=config_dir), user)

    recorder = entry.runtime_data["recorder"]
    assert recorder.path == str(config_dir / "media" / "sip" / "ok.wav")
    await hass.services.async_call(
        DOMAIN,
        "stop_recording",
        target,
        blocking=True,
        context=Context(user_id=user.id),
    )
    assert (config_dir / "media" / "sip" / "ok.wav").is_file()


# ------------------------------------------------------------- IVR context
async def test_dial_menu_gets_the_callers_context(
    hass: HomeAssistant, init_integration
) -> None:
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    context = Context(user_id=user.id)
    client = entry.runtime_data["client"]

    with (
        patch("custom_components.sip.IvrSession") as ivr_session,
        patch.object(client, "call"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "dial",
            {
                "number": "200",
                "menu": {"action": {"domain": "light", "service": "turn_on"}},
                **target,
            },
            blocking=True,
            context=context,
        )

    ivr_session.assert_called_once()
    assert ivr_session.call_args.kwargs["context"] is context


async def test_answer_menu_gets_the_callers_context(
    hass: HomeAssistant, init_integration
) -> None:
    entry = init_integration
    target = phone_line(hass, entry)
    user = _line_user(hass, target)
    context = Context(user_id=user.id)
    client = entry.runtime_data["client"]

    with (
        patch("custom_components.sip.IvrSession") as ivr_session,
        patch.object(client, "answer"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "answer",
            {"message": "Hello", **target},
            blocking=True,
            context=context,
        )

    ivr_session.assert_called_once()
    assert ivr_session.call_args.kwargs["context"] is context


@pytest.mark.parametrize(("admin", "allowed"), [(False, False), (True, True)])
async def test_dial_menu_action_runs_with_the_callers_permissions(
    hass: HomeAssistant,
    init_integration,
    hass_admin_user: MockUser,
    admin: bool,
    allowed: bool,
) -> None:
    """A real IVR menu action on a real call: admin-only stays admin-only."""
    entry = init_integration
    client = entry.runtime_data["client"]
    target = phone_line(hass, entry)
    user = hass_admin_user if admin else _line_user(hass, target)

    calls: list[ServiceCall] = []

    async def _record(call: ServiceCall) -> None:
        calls.append(call)

    async_register_admin_service(hass, "test", "admin_only", _record)

    await hass.services.async_call(
        DOMAIN,
        "dial",
        {
            "number": "200",
            "menu": {"action": {"domain": "test", "service": "admin_only"}},
            **target,
        },
        blocking=True,
        context=Context(user_id=user.id),
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.async_block_till_done()

    if allowed:
        assert len(calls) == 1
        assert calls[0].context.user_id == user.id
    else:
        assert calls == []

    await hass.services.async_call(DOMAIN, "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()


# ------------------------------------------------------- IVR audio_file URL
@pytest.mark.parametrize(
    "media_id",
    [
        "",
        "   ",
        "secrets.yaml",
        "config/secrets.yaml",
        "file:/config/secrets.yaml",
        "file:///config/.storage/auth",
        "concat:/config/a.wav|/config/secrets.yaml",
        "pipe:0",
        "C:\\config\\secrets.yaml",
    ],
)
async def test_resolve_playback_url_refuses_local_paths_and_protocols(
    hass: HomeAssistant, init_integration, media_id: str
) -> None:
    hass.config.internal_url = "http://hass.lan:8123"
    with pytest.raises(ValueError):
        await async_resolve_playback_url(hass, media_id)


async def test_resolve_playback_url_resolves_and_signs_media_source(
    hass: HomeAssistant, init_integration, tmp_path: Path
) -> None:
    hass.config.internal_url = "http://hass.lan:8123"
    (tmp_path / "hello.wav").write_bytes(b"RIFF")
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(tmp_path)

    url = await async_resolve_playback_url(
        hass, "media-source://media_source/local/hello.wav"
    )
    assert url.startswith("http://hass.lan:8123/media/local/hello.wav?authSig=")

    # A bare HA path becomes an HA URL, never a host filesystem path.
    assert (await async_resolve_playback_url(hass, "/local/a.mp3")).startswith(
        "http://hass.lan:8123/local/a.mp3"
    )
    assert (
        await async_resolve_playback_url(hass, "https://example.com/a.mp3")
        == "https://example.com/a.mp3"
    )
    with pytest.raises(ValueError, match="failed to resolve media source"):
        await async_resolve_playback_url(
            hass, "media-source://no_such_integration/hello.wav"
        )


@pytest.mark.parametrize(
    ("audio_file", "expected_prefix"),
    [
        (
            "media-source://media_source/local/hello.wav",
            "http://hass.lan:8123/media/local/hello.wav?authSig=",
        ),
        ("file:///config/secrets.yaml", None),
        ("secrets.yaml", None),
    ],
)
async def test_ivr_audio_file_is_resolved_before_ffmpeg(
    hass: HomeAssistant,
    init_integration,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    audio_file: str,
    expected_prefix: str | None,
) -> None:
    """A sip.dial menu ``audio_file`` reaches ffmpeg only as an http(s) URL."""
    entry = init_integration
    client = entry.runtime_data["client"]
    target = phone_line(hass, entry)
    hass.config.internal_url = "http://hass.lan:8123"
    (tmp_path / "hello.wav").write_bytes(b"RIFF")
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(tmp_path)
    caplog.set_level(logging.DEBUG, logger="custom_components.sip")

    with patch.object(client, "play_source") as play_source:
        await hass.services.async_call(
            DOMAIN,
            "dial",
            {"number": "200", "menu": {"audio_file": audio_file}, **target},
            blocking=True,
        )
        await wait_for(lambda: client.state == SipState.IN_CALL)
        await hass.async_block_till_done()

    if expected_prefix is None:
        play_source.assert_not_called()
        assert "IVR audio_file not played" in caplog.text
    else:
        play_source.assert_called_once()
        assert play_source.call_args.args[0]._url.startswith(expected_prefix)
        assert "IVR playing audio file" in caplog.text
    assert "authSig" not in caplog.text

    await hass.services.async_call(DOMAIN, "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()


async def test_ivr_audio_file_host_path_in_media_dir_logs_media_source_hint(
    hass: HomeAssistant,
    init_integration,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = init_integration
    hass.config.internal_url = "http://hass.lan:8123"
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(tmp_path)
    play_audio_file = entry.runtime_data["play_audio_file_fn"]

    with patch.object(entry.runtime_data["client"], "play_source"):
        await play_audio_file(str(tmp_path / "sounds" / "hello.mp3"))
    assert "media-source://media_source/local/sounds/hello.mp3" in caplog.text

    caplog.clear()
    with patch.object(entry.runtime_data["client"], "play_source"):
        await play_audio_file("media-source://media_source/local/hello.mp3")
    assert "looks like a host path" not in caplog.text


# --------------------------------------------------- Assist tone media URL
_REFUSED_MEDIA = ["file:///config/secrets.yaml", "secrets.yaml", "concat:a|b"]


def _tone_bridge(hass: HomeAssistant, played: list, **kwargs) -> AssistBridge:
    holder: dict = {}

    def play(source) -> None:
        played.append(source)
        holder["bridge"]._tx_done.set()

    bridge = AssistBridge(
        hass,
        play_source_fn=play,
        on_done_fn=lambda: None,
        interrupt_media=False,
        **kwargs,
    )
    holder["bridge"] = bridge
    bridge._running = True
    return bridge


@pytest.fixture
def media_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    hass.config.internal_url = "http://hass.lan:8123"
    (tmp_path / "ding.mp3").write_bytes(b"ID3")
    hass.config.media_dirs.clear()
    hass.config.media_dirs["local"] = str(tmp_path)
    return tmp_path


@pytest.mark.parametrize("media_id", _REFUSED_MEDIA)
async def test_assist_turn_tone_media_refuses_local_files(
    hass: HomeAssistant,
    init_integration,
    media_dir: Path,
    media_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    played: list = []
    bridge = _tone_bridge(hass, played, turn_tone=True, turn_tone_media=media_id)
    await bridge._play_turn_tone()
    assert [type(src) for src in played] == [ToneAudioSource]
    assert "tone media not used" in caplog.text


@pytest.mark.parametrize(
    ("media_id", "expected_prefix"),
    [
        (
            "media-source://media_source/local/ding.mp3",
            "http://hass.lan:8123/media/local/ding.mp3?authSig=",
        ),
        ("https://example.com/ding.mp3", "https://example.com/ding.mp3"),
    ],
)
async def test_assist_turn_tone_media_plays_media_source_and_http(
    hass: HomeAssistant,
    init_integration,
    media_dir: Path,
    media_id: str,
    expected_prefix: str,
) -> None:
    played: list = []
    bridge = _tone_bridge(hass, played, turn_tone=True, turn_tone_media=media_id)
    await bridge._play_turn_tone()
    assert len(played) == 1
    assert isinstance(played[0], FfmpegAudioSource)
    assert played[0]._url.startswith(expected_prefix)


@pytest.mark.parametrize(
    ("media_id", "expected_prefix"),
    [
        *((media_id, None) for media_id in _REFUSED_MEDIA),
        (
            "media-source://media_source/local/ding.mp3",
            "http://hass.lan:8123/media/local/ding.mp3?authSig=",
        ),
        ("http://example.com/wait.mp3", "http://example.com/wait.mp3"),
    ],
)
async def test_assist_processing_tone_media_is_resolved_before_ffmpeg(
    hass: HomeAssistant,
    init_integration,
    media_dir: Path,
    media_id: str,
    expected_prefix: str | None,
) -> None:
    played: list = []
    bridge = _tone_bridge(
        hass, played, processing_tone=True, processing_tone_media=media_id
    )
    bridge._processing_active = True
    await bridge._run_processing_cue()
    assert len(played) == 1
    if expected_prefix is None:
        assert isinstance(played[0], ToneAudioSource)
        assert bridge._processing_url is None
    else:
        assert isinstance(played[0], FfmpegAudioSource)
        assert played[0]._url.startswith(expected_prefix)
    bridge._processing_active = False


# ----------------------------------------------- media_player.play_media URL
async def test_play_media_refuses_local_files_and_plays_urls(
    hass: HomeAssistant, init_integration, media_dir: Path
) -> None:
    entry = init_integration
    client = entry.runtime_data["client"]
    target = phone_line(hass, entry)
    user = _line_user(hass, target)

    await hass.services.async_call(
        DOMAIN, "dial", {"number": "200", **target}, blocking=True
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.async_block_till_done()

    async def play(media_id: str) -> None:
        await hass.services.async_call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                **target,
                ATTR_MEDIA_CONTENT_TYPE: "music",
                ATTR_MEDIA_CONTENT_ID: media_id,
            },
            blocking=True,
            context=Context(user_id=user.id),
        )

    with patch.object(client, "play_source") as play_source:
        for media_id in (*_REFUSED_MEDIA, "file:/config/secrets.yaml", "pipe:0"):
            with pytest.raises(HomeAssistantError) as err:
                await play(media_id)
            assert err.value.translation_key == "media_url_prepare_failed"
        play_source.assert_not_called()

        await play("https://example.com/a.mp3")
        assert play_source.call_args.args[0]._url == "https://example.com/a.mp3"
        await play("media-source://media_source/local/ding.mp3")
        assert play_source.call_args.args[0]._url.startswith(
            "http://hass.lan:8123/media/local/ding.mp3?authSig="
        )
        with pytest.raises(HomeAssistantError) as err:
            await play("media-source://no_such_integration/x.mp3")
        assert err.value.translation_key == "media_source_resolve_failed"

    await hass.services.async_call(DOMAIN, "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()
