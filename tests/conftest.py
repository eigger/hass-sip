"""Shared pytest configuration.

Home Assistant itself is real here: pytest-homeassistant-custom-component
provides the ``hass`` fixture and friends. Only the network boundary (the
SIP registrar / PBX) is replaced, by tests/components/sip/mock_pbx.py.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Let the HA loader find custom_components/sip in every ``hass`` test.

    Pure protocol/codec tests do not request ``hass`` and stay loop-free.
    The manifest dependency ``logbook`` needs ``recorder``, which HA loads for
    config flows as well as for setup. ``recorder_mock`` (an in-memory
    database) must be prepared before ``hass`` starts, so request it first.
    """
    if "hass" not in request.fixturenames:
        return
    request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
