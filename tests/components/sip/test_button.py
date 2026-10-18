"""SIP call buttons only advertise actions that can affect a call."""

from __future__ import annotations

import pytest

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, phone_line, wait_for
from .mock_pbx import MockPbx


async def test_hangup_button_only_advertises_active_call_states(
    hass: HomeAssistant, init_integration, mock_pbx: MockPbx
) -> None:
    entry = init_integration
    client = entry.runtime_data["client"]
    hangup = entity_id_for(hass, entry, "button", "hangup")

    # Drive the integration through the same callback the SIP stack uses.
    for state in (
        SipState.IDLE,
        SipState.REGISTERING,
        SipState.REGISTERED,
    ):
        client.cb.on_state_change(state)
        await hass.async_block_till_done()
        assert hass.states.get(hangup).attributes["can_press"] is False

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: hangup}, blocking=True
        )
    assert exc_info.value.translation_key == "no_active_call"
    assert not [m for m in mock_pbx.received if m.method in ("BYE", "CANCEL")]

    for state in (
        SipState.INVITING,
        SipState.RINGING_OUT,
        SipState.INCOMING,
        SipState.ANSWERING,
        SipState.IN_CALL,
    ):
        client.cb.on_state_change(state)
        await hass.async_block_till_done()
        assert hass.states.get(hangup).attributes["can_press"] is True
    client.cb.on_state_change(client.state)

    # A real call: the mock PBX answers, pressing Hang Up sends BYE.
    await hass.services.async_call(
        "sip", "dial", {"number": "200", **phone_line(hass, entry)}, blocking=True
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.async_block_till_done()
    assert hass.states.get(hangup).attributes["can_press"] is True

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: hangup}, blocking=True
    )
    await wait_for(lambda: [m for m in mock_pbx.received if m.method == "BYE"])
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()
    assert hass.states.get(hangup).attributes["can_press"] is False
