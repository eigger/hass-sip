"""IVR lifecycle regression tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from test_pure import _load_component_module


ivr = _load_component_module("ivr")


def _session(*, trigger_assist=None):
    return ivr.IvrSession(
        MagicMock(),
        {"timeout": 3600},
        play_message_fn=AsyncMock(),
        play_audio_file_fn=AsyncMock(),
        hangup_fn=MagicMock(),
        fire_event_fn=MagicMock(),
        trigger_assist_fn=trigger_assist or AsyncMock(),
    )


def test_close_cancels_timeout_without_rearming_it():
    async def run():
        session = _session()
        session._start_dtmf_collection()
        original = session.timeout_task
        assert original is not None
        session.close()
        await asyncio.sleep(0)
        return session.timeout_task, original.cancelled()

    timeout_task, cancelled = asyncio.run(run())
    assert timeout_task is None
    assert cancelled


def test_assist_handoff_does_not_leave_an_ivr_timeout():
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        session._start_dtmf_collection()
        await session._execute_choice({"assist": True})
        await asyncio.sleep(0)
        return session.timeout_task, trigger

    timeout_task, trigger = asyncio.run(run())
    assert timeout_task is None
    trigger.assert_awaited_once()


def test_assist_mapping_forwards_start_assist_options():
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        await session._execute_choice(
            {
                "assist": {
                    "pipeline_id": "door-pipeline",
                    "system_prompt": "Be brief.",
                    "initial_prompt": "Greet the caller.",
                    "bogus": 1,
                    "pin": "1234",  # gate options are not menu options
                }
            }
        )
        return session, trigger

    session, trigger = asyncio.run(run())
    assert session.is_active is False
    trigger.assert_awaited_once_with(
        pipeline_id="door-pipeline",
        system_prompt="Be brief.",
        initial_prompt="Greet the caller.",
    )


def test_assist_mapping_on_menu_entry():
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        await session._enter_menu({"assist": {"hangup_on_end": True}})
        return trigger

    trigger = asyncio.run(run())
    trigger.assert_awaited_once_with(hangup_on_end=True)


def test_assist_false_does_not_hand_off():
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        await session._enter_menu({"assist": False, "timeout": 3600})
        active = session.is_active
        session.close()
        return trigger, active

    trigger, active = asyncio.run(run())
    trigger.assert_not_awaited()
    assert active is True


def test_assist_mapping_coerces_values_like_the_service_schema():
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        await session._enter_menu(
            {"assist": {"max_turns": "3", "barge_in": "false", "silence_seconds": 1}}
        )
        return trigger

    trigger = asyncio.run(run())
    trigger.assert_awaited_once_with(max_turns=3, barge_in=False, silence_seconds=1.0)


def test_assist_mapping_drops_only_the_invalid_key():
    """max_silent_turns=0 would end the session after the first turn; the
    service schema rejects it, so the menu must not smuggle it through —
    but the pipeline choice next to it survives."""
    async def run():
        trigger = AsyncMock()
        session = _session(trigger_assist=trigger)
        await session._enter_menu(
            {"assist": {"max_silent_turns": 0, "pipeline_id": "p"}}
        )
        return session, trigger

    session, trigger = asyncio.run(run())
    assert session.is_active is False
    trigger.assert_awaited_once_with(pipeline_id="p")


def test_suspend_holds_playback_done_until_resume():
    """An announcement finishing under a sip.start_assist PIN prompt must not
    hang up; a rejected PIN resumes the menu and its post_action runs then."""
    async def run():
        session = _session()
        session.hass.async_create_task = asyncio.ensure_future
        session.current_menu = {"message": "Enter your PIN", "post_action": "hangup"}
        session.suspend()
        session.on_playback_done()
        await asyncio.sleep(0)
        before = session.hangup.call_count
        session.resume()
        await asyncio.sleep(0)
        return before, session.hangup.call_count

    before, after = asyncio.run(run())
    assert before == 0
    assert after == 1


def test_suspend_pauses_input_timeout_and_resume_rearms_it():
    async def run():
        session = _session()
        session._start_dtmf_collection()
        session.suspend()
        await asyncio.sleep(0)
        paused = session.timeout_task
        await session.handle_dtmf("1")
        buffered = session.digit_buffer
        session.resume()
        rearmed = session.timeout_task
        session.close()
        return paused, buffered, rearmed

    paused, buffered, rearmed = asyncio.run(run())
    assert paused is None
    assert buffered == ""
    assert rearmed is not None


def test_resume_after_close_stays_closed():
    session = _session()
    session.suspend()
    session.close()
    session.resume()
    assert session.is_active is False
    assert session.timeout_task is None
