"""SIP call buttons only advertise actions that can affect a call."""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from test_pure import _load_component_module


class _ButtonEntity:
    """Minimal Home Assistant button base for platform import."""


button_component = sys.modules["homeassistant.components.button"]
original_button_entity = getattr(button_component, "ButtonEntity", None)
original_helpers = sys.modules.get("custom_components.sip.helpers")
helpers = types.ModuleType("custom_components.sip.helpers")
helpers.build_device_info = MagicMock(return_value={})
button_component.ButtonEntity = _ButtonEntity
sys.modules["custom_components.sip.helpers"] = helpers
try:
    button = _load_component_module("button")
finally:
    button_component.ButtonEntity = original_button_entity
    if original_helpers is None:
        sys.modules.pop("custom_components.sip.helpers", None)
    else:
        sys.modules["custom_components.sip.helpers"] = original_helpers


def test_hangup_button_only_advertises_active_call_states():
    client = MagicMock()
    data = {
        "client": client,
        "config": MagicMock(),
        "state": button.SipState.REGISTERING,
    }
    entry = MagicMock(entry_id="account-1")
    entry.runtime_data = data
    hangup = button.SipHangupButton(entry, data)

    for state in (
        None,
        button.SipState.IDLE,
        button.SipState.REGISTERING,
        button.SipState.REGISTERED,
    ):
        data["state"] = state
        assert hangup.extra_state_attributes["can_press"] is False

    with pytest.raises(ServiceValidationError) as exc_info:
        asyncio.run(hangup.async_press())
    assert exc_info.value.translation_key == "no_active_call"
    client.hangup.assert_not_called()

    for state in (
        button.SipState.INVITING,
        button.SipState.RINGING_OUT,
        button.SipState.INCOMING,
        button.SipState.ANSWERING,
        button.SipState.IN_CALL,
    ):
        data["state"] = state
        assert hangup.extra_state_attributes["can_press"] is True

    asyncio.run(hangup.async_press())
    client.hangup.assert_called_once_with()
