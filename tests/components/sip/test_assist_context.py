"""Assist pipeline Context subject (P4-2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import HomeAssistant

from custom_components.sip.const import CONF_ASSIST_USER, CONF_USERNAME, DOMAIN

from .common import (
    _assist_ctx,
    _initial_prompt_doubles,
    _load_component_module,
    _run_bridge_session,
)

assist_user = _load_component_module("assist_user")


async def test_ensure_assist_user_reuses_existing(hass: HomeAssistant) -> None:
    user = await hass.auth.async_create_system_user("existing")
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_USERNAME: "100", CONF_ASSIST_USER: user.id}
    )
    entry.add_to_hass(hass)
    users_before = len(await hass.auth.async_get_users())

    assert await assist_user.ensure_assist_user(hass, entry) == user.id
    assert len(await hass.auth.async_get_users()) == users_before
    assert entry.data[CONF_ASSIST_USER] == user.id


async def test_ensure_assist_user_creates_when_missing(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERNAME: "100"})
    entry.add_to_hass(hass)

    uid = await assist_user.ensure_assist_user(hass, entry)

    user = await hass.auth.async_get_user(uid)
    assert user is not None
    assert user.name == "SIP Assist (100)"
    assert user.system_generated
    assert [group.id for group in user.groups] == [GROUP_ID_USER]
    assert entry.data[CONF_ASSIST_USER] == uid


async def test_ensure_assist_user_recreates_deleted_user(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_USERNAME: "100", CONF_ASSIST_USER: "gone"}
    )
    entry.add_to_hass(hass)

    uid = await assist_user.ensure_assist_user(hass, entry)

    assert uid != "gone"
    assert await hass.auth.async_get_user(uid) is not None
    assert entry.data[CONF_ASSIST_USER] == uid


async def test_remove_assist_user_deletes_existing(hass: HomeAssistant) -> None:
    user = await hass.auth.async_create_system_user("SIP Assist (100)")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ASSIST_USER: user.id})

    await assist_user.remove_assist_user(hass, entry)

    assert await hass.auth.async_get_user(user.id) is None


async def test_remove_assist_user_skips_when_absent(hass: HomeAssistant) -> None:
    users_before = len(await hass.auth.async_get_users())
    await assist_user.remove_assist_user(hass, MockConfigEntry(domain=DOMAIN, data={}))
    await assist_user.remove_assist_user(
        hass, MockConfigEntry(domain=DOMAIN, data={CONF_ASSIST_USER: "unknown"})
    )
    assert len(await hass.auth.async_get_users()) == users_before


def test_assist_reuses_session_context_and_forwards_user():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    seen = []

    async def mock_pipeline(hass, **kwargs):
        seen.append(kwargs)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        user_id="user-sip",
        device_id="dev-1",
        max_silent_turns=2,
    )
    _run_bridge_session(bridge)

    assert len(seen) == 2
    assert seen[0]["context"] is seen[1]["context"]
    assert seen[0]["context"].user_id == "user-sip"
    assert seen[0]["device_id"] == "dev-1"
    assert seen[1]["device_id"] == "dev-1"


def test_assist_initial_prompt_uses_same_context():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    audio_calls = []
    (
        pipeline_runs,
        pipeline_inputs,
        fake_run,
        fake_input,
        fake_chat_session,
    ) = _initial_prompt_doubles(PET, PE, (PET.ERROR, {"code": "intent-failed"}))

    async def mock_audio_pipeline(hass, **kwargs):
        audio_calls.append(kwargs)

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline

    with (
        patch.object(assist_mod, "PipelineRun", fake_run),
        patch.object(assist_mod, "PipelineInput", fake_input),
        patch.object(assist_mod, "chat_session", fake_chat_session),
    ):
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            initial_prompt="Greet the caller",
            user_id="user-sip",
            device_id="dev-9",
            max_turns=1,
        )
        _run_bridge_session(bridge)

    assert pipeline_runs[0].kwargs["context"].user_id == "user-sip"
    assert pipeline_inputs[0].kwargs["device_id"] == "dev-9"
    assert audio_calls[0]["context"] is pipeline_runs[0].kwargs["context"]
    assert audio_calls[0]["device_id"] == "dev-9"
