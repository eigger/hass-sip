"""Fixtures for the SIP Client integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import socket
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.sip.const import (
    CONF_DOMAIN,
    CONF_LOCAL_RTP_PORT,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_REGISTER_EXPIRATION,
    CONF_SERVER,
    CONF_USERNAME,
    DOMAIN,
)

from .common import reset_assist_ctx, wait_for
from .mock_pbx import MockPbx


# <!-- hass-standards:domain-fixtures:start -->
# Use pytest_homeassistant_custom_component.common.MockConfigEntry for
# mock_config_entry when the integration uses config entries.
# Define mock_client only when an external communication client is present.
# Mock external communication boundaries, never Home Assistant core modules.
# Keep integration-specific fixtures below this managed block.
# <!-- hass-standards:domain-fixtures:end -->


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Enable SIP loading after recorder setup; keep protocol tests loop-free.

    Override the shared fixture within this integration because logbook needs
    recorder_mock before hass starts, including config-flow tests.
    """
    if "hass" not in request.fixturenames:
        return
    request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _reset_assist_doubles() -> Generator[None]:
    """Stop the per-test Assist pipeline doubles from common._assist_ctx()."""
    yield
    reset_assist_ctx()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Fail on any timer left behind (the plugin only warns for custom domains)."""
    return False


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def mock_pbx(socket_enabled: None) -> AsyncGenerator[MockPbx]:
    """A UDP SIP registrar on 127.0.0.1 standing in for the user's PBX.

    This is the only external boundary the integration tests replace: the
    integration's own SipClient talks real SIP over a real UDP socket to it.
    """
    pbx = MockPbx()
    await pbx.start()
    yield pbx
    await pbx.stop()


@pytest.fixture
def entry_data(mock_pbx: MockPbx) -> dict[str, Any]:
    """Config entry data pointing at the mock PBX."""
    return {
        CONF_SERVER: "127.0.0.1",
        CONF_PORT: mock_pbx.port,
        CONF_USERNAME: mock_pbx.username,
        CONF_PASSWORD: mock_pbx.password,
        CONF_DOMAIN: mock_pbx.domain,
        CONF_REGISTER_EXPIRATION: 300,
        CONF_LOCAL_RTP_PORT: _free_udp_port(),
    }


@pytest.fixture
def mock_config_entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """A SIP account config entry (not yet added to hass)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"SIP: {entry_data[CONF_USERNAME]}",
        unique_id=f"{entry_data[CONF_USERNAME]}@{entry_data[CONF_SERVER]}",
        data=entry_data,
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration through the config entry manager."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: mock_config_entry.runtime_data["registered"])
    yield mock_config_entry
    if (
        hass.config_entries.async_get_entry(mock_config_entry.entry_id)
        and mock_config_entry.state is ConfigEntryState.LOADED
    ):
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
