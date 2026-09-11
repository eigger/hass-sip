"""Assist pipeline Context subject (P4-2)."""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

from test_pure import _assist_ctx, _load_component_module, _run_bridge_session

assist_user = _load_component_module("assist_user")


class _Ctx:
    def __init__(self, user_id=None, parent_id=None, id=None):
        self.user_id = user_id
        self.parent_id = parent_id
        self.id = id or "generated"


def test_ensure_assist_user_reuses_existing():
    async def run():
        hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=MagicMock(id="keep-me")),
                async_create_system_user=AsyncMock(),
            ),
            config_entries=MagicMock(),
        )
        entry = MagicMock()
        entry.data = {"username": "100", "assist_user": "keep-me"}
        return await assist_user.ensure_assist_user(hass, entry)

    assert asyncio.run(run()) == "keep-me"


def test_ensure_assist_user_creates_when_missing():
    async def run():
        create = AsyncMock(return_value=MagicMock(id="new-user"))
        hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=None),
                async_create_system_user=create,
            ),
            config_entries=MagicMock(),
        )
        entry = MagicMock()
        entry.data = {"username": "100"}
        uid = await assist_user.ensure_assist_user(hass, entry)
        create.assert_awaited_once()
        assert create.call_args.args[0] == "SIP Assist (100)"
        hass.config_entries.async_update_entry.assert_called_once()
        updated = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert updated["assist_user"] == "new-user"
        return uid

    assert asyncio.run(run()) == "new-user"


def test_remove_assist_user_deletes_existing():
    async def run():
        user = MagicMock()
        remove = AsyncMock()
        hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=user),
                async_remove_user=remove,
            )
        )
        entry = MagicMock()
        entry.data = {"assist_user": "gone"}
        await assist_user.remove_assist_user(hass, entry)
        remove.assert_awaited_once_with(user)

    asyncio.run(run())


def test_remove_assist_user_skips_when_absent():
    async def run():
        remove = AsyncMock()
        hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(),
                async_remove_user=remove,
            )
        )
        entry = MagicMock()
        entry.data = {}
        await assist_user.remove_assist_user(hass, entry)
        remove.assert_not_called()

    asyncio.run(run())


def test_assist_reuses_session_context_and_forwards_user():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    seen = []

    async def mock_pipeline(hass, **kwargs):
        seen.append(kwargs)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    with patch.object(assist_mod, "Context", _Ctx):
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
    from test_pure import _initial_prompt_doubles

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
        patch.object(assist_mod, "Context", _Ctx),
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
