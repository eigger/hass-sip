"""Shared helpers for the SIP Client tests.

Everything is imported from the real ``custom_components.sip`` package on top
of the real ``homeassistant`` installed by pytest-homeassistant-custom-component.
Nothing here registers fake ``homeassistant.*`` modules.
"""

from __future__ import annotations

import asyncio
import importlib
import types
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.sip.const import DOMAIN
from custom_components.sip.sip_client import (  # noqa: F401 - re-exported
    audio,
    codecs,
    g711,
    g722,
    rtp_session,
    sip_auth,
    sip_client,
    trace,
)
from custom_components.sip.sip_client import sip_message as sm  # noqa: F401


async def wait_for(predicate, timeout: float = 3.0) -> None:
    """Poll ``predicate`` while real SIP/RTP traffic is exchanged."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def entity_id_for(hass: HomeAssistant, entry, platform: str, key: str) -> str:
    """Entity id of ``<entry_id>_<key>`` from the real entity registry."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


def phone_line(hass: HomeAssistant, entry) -> dict[str, str]:
    """Service target for the account's phone-line media player."""
    return {"entity_id": entity_id_for(hass, entry, "media_player", "media_player")}


def sip_device(hass: HomeAssistant, entry) -> dr.DeviceEntry:
    """The account's device from the real device registry."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id
    )
    assert device is not None
    return device


def _load_component_module(name: str) -> types.ModuleType:
    """Return ``custom_components.sip.<name>`` (the package itself for __init__)."""
    if name == "__init__":
        return importlib.import_module("custom_components.sip")
    return importlib.import_module(f"custom_components.sip.{name}")


# ------------------------------------------------------------ assist bridge
#
# AssistBridge talks to HA's Assist pipeline, TTS and media-source. Those
# calls end in STT/LLM/TTS engines (external services), so bridge unit tests
# replace exactly those call sites on the assist module with doubles while the
# real HA types (PipelineEvent, PipelineEventType, SpeechMetadata,
# AudioSettings, Context) stay in use. The real config-entry path is covered
# by test_init.py. The patches live for one test only:
# tests/components/sip/conftest.py stops them after every test.

_ASSIST_CTX: tuple | None = None
_ASSIST_PATCHER = None
_HELPERS_PATCHER = None


def _setup_assist_deps():
    assist = _load_component_module("assist")

    mock_ap = MagicMock()
    pipeline_obj = MagicMock()
    pipeline_obj.id = "default"

    def _get_pipeline(hass, pipeline_id):
        return pipeline_obj

    mock_ap.async_get_pipeline = _get_pipeline
    mock_ap.async_pipeline_from_audio_stream = AsyncMock()

    mock_tts = MagicMock()
    mock_tts.async_get_stream = MagicMock(return_value=None)

    # Tone media resolution (turn_tone_media / processing_tone_media) goes
    # through helpers.async_resolve_playback_url: default to "not a
    # media-source ID" and an identity URL passthrough, so a plain http(s) URL
    # exercises the FfmpegAudioSource path without extra setup; tests can
    # reconfigure helpers.media_source / helpers.async_process_play_media_url.
    mock_media_source = MagicMock()
    mock_media_source.is_media_source_id = MagicMock(return_value=False)
    mock_media_source.async_resolve_media = AsyncMock()

    global _ASSIST_PATCHER
    _ASSIST_PATCHER = patch.multiple(
        assist,
        async_get_pipeline=mock_ap.async_get_pipeline,
        async_pipeline_from_audio_stream=mock_ap.async_pipeline_from_audio_stream,
        tts=mock_tts,
        get_ffmpeg_bin=MagicMock(return_value="/usr/bin/ffmpeg"),
        # Synthetic PCM is not speech to the real Silero/micro VAD; tests that
        # exercise VAD behaviour install their own deterministic stub.
        MicroVad=None,
    )
    _ASSIST_PATCHER.start()
    global _HELPERS_PATCHER
    _HELPERS_PATCHER = patch.multiple(
        _load_component_module("helpers"),
        media_source=mock_media_source,
        async_process_play_media_url=MagicMock(side_effect=lambda hass, url: url),
    )
    _HELPERS_PATCHER.start()
    return assist, mock_ap, PipelineEventType, PipelineEvent


def _assist_ctx():
    global _ASSIST_CTX
    if _ASSIST_CTX is None:
        _ASSIST_CTX = _setup_assist_deps()
    return _ASSIST_CTX


def reset_assist_ctx() -> None:
    """Undo the per-test Assist doubles (called from conftest)."""
    global _ASSIST_CTX, _ASSIST_PATCHER, _HELPERS_PATCHER
    if _ASSIST_PATCHER is not None:
        _ASSIST_PATCHER.stop()
    if _HELPERS_PATCHER is not None:
        _HELPERS_PATCHER.stop()
    _ASSIST_PATCHER = None
    _HELPERS_PATCHER = None
    _ASSIST_CTX = None


def _run_bridge_session(bridge):
    async def _wait():
        bridge.start()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(_wait())


def _initial_prompt_doubles(PET, PE, *events):
    """Return reusable doubles for a text-input Assist pipeline run."""
    from contextlib import nullcontext

    pipeline_runs = []
    pipeline_inputs = []

    class FakePipelineRun:
        def __init__(self, hass, **kwargs):
            self.hass = hass
            self.kwargs = kwargs
            pipeline_runs.append(self)

    class FakePipelineInput:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            pipeline_inputs.append(self)

        async def validate(self):
            return None

        async def execute(self):
            callback = self.kwargs["run"].kwargs["event_callback"]
            callback(
                PE(
                    PET.RUN_START,
                    {"conversation_id": self.kwargs["session"].conversation_id},
                )
            )
            for event_type, data in events:
                callback(PE(event_type, data))

    fake_chat_session = MagicMock()
    fake_chat_session.async_get_chat_session.side_effect = (
        lambda hass, conversation_id: nullcontext(
            types.SimpleNamespace(
                conversation_id=conversation_id or "opening-conversation"
            )
        )
    )
    return (
        pipeline_runs,
        pipeline_inputs,
        FakePipelineRun,
        FakePipelineInput,
        fake_chat_session,
    )
