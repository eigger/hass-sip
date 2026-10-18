"""sip.* actions on a real Home Assistant: targets, permissions and the Assist gate.

The account is a real config entry registered against the mock PBX. Only the
Assist pipeline bridge (STT/LLM/TTS engines) is replaced, so the tests observe
whether the integration would start Assist and with which options.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockUser,
    async_capture_events,
)
import voluptuous as vol

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import entity_registry as er

from custom_components.sip.assist_gate import (
    REASON_NOT_ALLOWED,
    REASON_PIN_MISMATCH,
)
from custom_components.sip.const import (
    DOMAIN,
    EVENT_SIP_ASSIST_REJECTED,
    EVENT_SIP_DTMF_DIGIT,
)
from custom_components.sip.ivr import IvrSession

from .common import phone_line, sip_device, wait_for


@pytest.fixture
def assist_bridge() -> Generator[MagicMock]:
    """Replace the Assist pipeline bridge (the STT/LLM/TTS boundary)."""
    with patch("custom_components.sip.AssistBridge") as bridge_cls:
        yield bridge_cls


def _ring(entry, caller: str) -> None:
    """Deliver an incoming call through the SIP client's callback."""
    entry.runtime_data["client"].cb.on_incoming_call(caller)


async def _start_assist(
    hass: HomeAssistant,
    data: dict,
    *,
    user_id: str | None = None,
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "start_assist",
        data,
        blocking=True,
        context=Context(user_id=user_id),
    )


async def test_start_assist_forwards_prompts(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    _ring(entry, "100")
    await _start_assist(
        hass,
        {
            **phone_line(hass, entry),
            "conversation_id": "existing-conversation",
            "initial_prompt": "Greet the caller",
            "system_prompt": "Keep answers concise",
        },
    )

    assist_bridge.assert_called_once()
    kwargs = assist_bridge.call_args.kwargs
    assert kwargs["conversation_id"] == "existing-conversation"
    assert kwargs["initial_prompt"] == "Greet the caller"
    assert kwargs["system_prompt"] == "Keep answers concise"
    assert kwargs["allow_llm_hangup"] is False
    assert kwargs["interrupt_media"] is True
    # The bridge runs as the account's Assist system user on the SIP device.
    assert kwargs["user_id"] == entry.data["assist_user"]
    assert kwargs["device_id"] == sip_device(hass, entry).id
    assist_bridge.return_value.start.assert_called_once()


async def test_start_assist_rejects_unknown_caller(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    rejected = async_capture_events(hass, EVENT_SIP_ASSIST_REJECTED)
    _ring(entry, "sip:200@pbx")
    await _start_assist(hass, {**phone_line(hass, entry), "allowed_callers": ["100"]})
    await hass.async_block_till_done()

    assist_bridge.assert_not_called()
    assert len(rejected) == 1
    assert rejected[0].data["caller"] == "sip:200@pbx"
    assert rejected[0].data["reason"] == REASON_NOT_ALLOWED
    assert "pin" not in rejected[0].data


async def test_start_assist_allows_listed_caller(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    rejected = async_capture_events(hass, EVENT_SIP_ASSIST_REJECTED)
    _ring(entry, "sip:100@pbx")
    await _start_assist(hass, {**phone_line(hass, entry), "allowed_callers": ["100"]})
    await hass.async_block_till_done()

    assist_bridge.assert_called_once()
    assert rejected == []


# ---------------------------------------------- service target / permissions
async def test_explicit_unmatched_target_does_not_fall_back_to_first_account(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    _ring(init_integration, "100")
    with pytest.raises(ServiceValidationError, match="does not match") as err:
        await _start_assist(hass, {"entity_id": ["media_player.missing_phone_line"]})
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "target_not_loaded"
    assist_bridge.assert_not_called()


@pytest.mark.parametrize("user", [None, "read_only"])
async def test_untargeted_call_is_rejected_before_the_handler(
    hass: HomeAssistant,
    init_integration,
    assist_bridge: MagicMock,
    hass_read_only_user: MockUser,
    user: str | None,
) -> None:
    """HA's entity-service schema requires a target for every sip.* action.

    The integration's untargeted "first account" fallback (and its permission
    check) therefore cannot be reached through Home Assistant; an untargeted
    call never controls any account.
    """
    _ring(init_integration, "100")
    with pytest.raises(vol.Invalid):
        await _start_assist(hass, {}, user_id=hass_read_only_user.id if user else None)
    assist_bridge.assert_not_called()


async def test_service_user_must_control_target_phone_line(
    hass: HomeAssistant,
    init_integration,
    assist_bridge: MagicMock,
    hass_read_only_user: MockUser,
) -> None:
    _ring(init_integration, "100")
    with pytest.raises(Unauthorized) as err:
        await _start_assist(
            hass, phone_line(hass, init_integration), user_id=hass_read_only_user.id
        )
    assert err.value.permission == "control"
    assist_bridge.assert_not_called()


async def test_authorized_service_user_can_control_target_phone_line(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    target = phone_line(hass, init_integration)
    user = MockUser().add_to_hass(hass)
    user.mock_policy({"entities": {"entity_ids": {target["entity_id"]: True}}})
    _ring(init_integration, "100")

    await _start_assist(hass, target, user_id=user.id)

    assist_bridge.assert_called_once()


async def _remove_phone_line_entity(hass: HomeAssistant, entry) -> dict:
    """Drop the phone-line entity; target the account's device instead."""
    entity_id = phone_line(hass, entry)["entity_id"]
    er.async_get(hass).async_remove(entity_id)
    await hass.async_block_till_done()
    return {"device_id": sip_device(hass, entry).id}


# Removing the only media_player entity of a polling platform leaves HA's
# platform poll timer behind (EntityPlatform.async_reset returns early when the
# platform has no entities); the SIP entities keep HA's default should_poll.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_admin_is_not_locked_out_when_phone_line_entity_missing(
    hass: HomeAssistant,
    init_integration,
    assist_bridge: MagicMock,
    hass_admin_user: MockUser,
) -> None:
    target = await _remove_phone_line_entity(hass, init_integration)
    _ring(init_integration, "100")

    await _start_assist(hass, target, user_id=hass_admin_user.id)

    assist_bridge.assert_called_once()


# Removing the only media_player entity of a polling platform leaves HA's
# platform poll timer behind (EntityPlatform.async_reset returns early when the
# platform has no entities); the SIP entities keep HA's default should_poll.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_restricted_user_denied_when_phone_line_entity_missing(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    target = await _remove_phone_line_entity(hass, init_integration)
    user = MockUser().add_to_hass(hass)
    user.mock_policy({"entities": {"all": True}})  # may control any entity
    _ring(init_integration, "100")

    with pytest.raises(Unauthorized):
        await _start_assist(hass, target, user_id=user.id)
    assist_bridge.assert_not_called()


async def test_unknown_service_user_is_rejected(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    _ring(init_integration, "100")
    with pytest.raises(UnknownUser):
        await _start_assist(hass, phone_line(hass, init_integration), user_id="ghost")
    assist_bridge.assert_not_called()


# ------------------------------------------------------------- PIN and IVR
def _arm_ivr(entry) -> MagicMock:
    """Install an announcement IVR session as sip.dial/answer ``message`` does."""
    ivr = MagicMock(spec=IvrSession)
    entry.runtime_data["set_ivr"](ivr)
    return ivr


async def _run_pin_gate(hass: HomeAssistant, entry, digits: str):
    """Start Assist behind PIN 1234 and key ``digits`` in over SIP DTMF."""
    dtmf_events = async_capture_events(hass, EVENT_SIP_DTMF_DIGIT)
    task = hass.async_create_task(
        _start_assist(hass, {**phone_line(hass, entry), "pin": "1234"})
    )
    await wait_for(lambda: entry.runtime_data.get("pin_collector") is not None)
    for digit in digits:
        entry.runtime_data["client"].cb.on_dtmf(digit)
    await asyncio.wait_for(task, timeout=3)
    await hass.async_block_till_done()
    # PIN digits are consumed by the gate, never published as DTMF events.
    assert dtmf_events == []
    assert entry.runtime_data["pin_collector"] is None


async def test_start_assist_pin_mismatch_does_not_start(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    rejected = async_capture_events(hass, EVENT_SIP_ASSIST_REJECTED)
    _ring(init_integration, "100")

    await _run_pin_gate(hass, init_integration, "9999")

    assist_bridge.assert_not_called()
    assert len(rejected) == 1
    assert rejected[0].data["reason"] == REASON_PIN_MISMATCH
    assert "1234" not in str(rejected[0].data)
    assert "9999" not in str(rejected[0].data)


async def test_start_assist_pin_ok_starts_assist(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    rejected = async_capture_events(hass, EVENT_SIP_ASSIST_REJECTED)
    _ring(init_integration, "100")

    await _run_pin_gate(hass, init_integration, "1234")

    assist_bridge.assert_called_once()
    assert rejected == []


async def test_start_assist_closes_active_ivr_session(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    """sip.dial/answer ``message`` arms an announcement IVR (post_action:
    hangup); sip.start_assist must retire it so Assist's own playback_done
    does not hang up after the first reply (#92)."""
    entry = init_integration
    _ring(entry, "100")
    ivr = _arm_ivr(entry)

    await _start_assist(hass, phone_line(hass, entry))

    ivr.close.assert_called_once()
    assert entry.runtime_data["get_ivr"]() is None
    assist_bridge.assert_called_once()


async def test_start_assist_rejected_leaves_ivr_session_alone(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    _ring(entry, "200")
    ivr = _arm_ivr(entry)

    await _start_assist(hass, {**phone_line(hass, entry), "allowed_callers": ["100"]})

    ivr.close.assert_not_called()
    ivr.suspend.assert_not_called()
    assert entry.runtime_data["get_ivr"]() is ivr
    assist_bridge.assert_not_called()


async def test_start_assist_suspends_ivr_during_pin_and_retires_it_on_success(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    _ring(entry, "100")
    ivr = _arm_ivr(entry)
    suspended = []
    ivr.suspend.side_effect = lambda: suspended.append(
        entry.runtime_data.get("pin_collector") is not None
    )

    await _run_pin_gate(hass, entry, "1234")

    assert suspended == [True]  # held while the PIN was being collected
    ivr.resume.assert_not_called()
    ivr.close.assert_called_once()
    assert entry.runtime_data["get_ivr"]() is None
    assist_bridge.assert_called_once()


async def test_start_assist_resumes_ivr_when_pin_is_rejected(
    hass: HomeAssistant, init_integration, assist_bridge: MagicMock
) -> None:
    entry = init_integration
    _ring(entry, "100")
    ivr = _arm_ivr(entry)

    await _run_pin_gate(hass, entry, "9999")

    ivr.suspend.assert_called_once()
    ivr.resume.assert_called_once()
    ivr.close.assert_not_called()
    assert entry.runtime_data["get_ivr"]() is ivr
    assist_bridge.assert_not_called()
