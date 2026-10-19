"""What a non-admin caller can reach through sip.* service data.

Real Home Assistant, real config entry against the mock PBX, real service
calls made as a (non-admin) user. Covers the recording path policy as wired
in ``sip.start_recording``, the caller context carried into IVR menus, and
IVR ``audio_file`` resolution.
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

from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers.service import async_register_admin_service

from custom_components.sip.const import DOMAIN, EVENT_SIP_RECORDING_STARTED
from custom_components.sip.helpers import async_resolve_playback_url
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
