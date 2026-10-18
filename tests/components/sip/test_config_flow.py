"""Config, reconfigure and options flows through HA's flow manager.

Registration is validated for real against the mock PBX (UDP on 127.0.0.1).
The user/reconfigure tests patch ``async_setup_entry`` so they only cover the
flow itself; test_init.py and the options test run the real setup.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.sip.const import (
    CONF_ASSIST_USER,
    CONF_CODEC_PREFERENCE,
    CONF_LOCAL_RTP_PORT,
    CONF_MAX_CALL_DURATION,
    CONF_MEDIA_TIMEOUT,
    CONF_PASSWORD,
    CONF_SERVER,
    CONF_USE_OBSERVED_ADDRESS,
    CONF_USERNAME,
    DEFAULT_LOCAL_RTP_PORT,
    DEFAULT_MAX_CALL_DURATION,
    DEFAULT_MEDIA_TIMEOUT,
    DEFAULT_USE_OBSERVED_ADDRESS,
    DOMAIN,
)

from .common import wait_for
from .mock_pbx import MockPbx


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep flow-only tests from setting the entry up (or tearing it down)."""
    with (
        patch(
            "custom_components.sip.async_setup_entry", return_value=True
        ) as setup_entry,
        patch("custom_components.sip.async_unload_entry", return_value=True),
    ):
        yield setup_entry


def _schema_default(result, key: str) -> Any:
    for marker in result["data_schema"].schema:
        if str(marker) == key:
            return marker.default()
    raise KeyError(key)


async def test_user_flow_creates_entry(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], entry_data
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SIP: 1001"
    assert result["data"] == {
        **entry_data,
        CONF_MEDIA_TIMEOUT: DEFAULT_MEDIA_TIMEOUT,
        CONF_MAX_CALL_DURATION: DEFAULT_MAX_CALL_DURATION,
        CONF_USE_OBSERVED_ADDRESS: DEFAULT_USE_OBSERVED_ADDRESS,
    }
    assert result["result"].unique_id == "1001@127.0.0.1"
    # The flow really registered (401 challenge, then authenticated REGISTER).
    assert any(m.header("Authorization") for m in mock_pbx.registers)
    await hass.async_block_till_done()
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("pbx_setup", "error"),
    [
        ({"password": "other-secret"}, "invalid_auth"),
        ({"force_code": 403}, "invalid_auth"),
        ({"force_code": 503}, "cannot_connect"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
    pbx_setup: dict[str, Any],
    error: str,
) -> None:
    good = {k: getattr(mock_pbx, k) for k in pbx_setup}
    for key, value in pbx_setup.items():
        setattr(mock_pbx, key, value)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], entry_data
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # The same flow can be retried once the PBX/credentials are fixed.
    for key, value in good.items():
        setattr(mock_pbx, key, value)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], entry_data
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_silent_server_times_out_as_cannot_connect(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
) -> None:
    """A PBX that never answers ends in cannot_connect, not a hung flow."""
    mock_pbx.drop = True
    real_wait_for = asyncio.wait_for

    async def short_wait_for(coro, timeout):
        return await real_wait_for(coro, min(timeout, 0.2))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch("custom_components.sip.config_flow.asyncio.wait_for", short_wait_for):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], entry_data
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_pbx.registers  # it did try
    mock_setup_entry.assert_not_called()


async def test_user_flow_aborts_on_duplicate(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], entry_data
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Duplicates are rejected before any REGISTER is sent.
    assert mock_pbx.registers == []
    mock_setup_entry.assert_not_called()


async def test_user_flow_suggests_free_rtp_port(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="1000@pbx",
        data={CONF_LOCAL_RTP_PORT: DEFAULT_LOCAL_RTP_PORT},
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert _schema_default(result, CONF_LOCAL_RTP_PORT) == DEFAULT_LOCAL_RTP_PORT + 2


async def test_reconfigure_keeps_password_and_assist_user(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1001@127.0.0.1",
        data={**entry_data, CONF_ASSIST_USER: "keep-me"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert _schema_default(result, CONF_SERVER) == "127.0.0.1"

    form = {k: v for k, v in entry_data.items() if k != CONF_PASSWORD}
    form[CONF_LOCAL_RTP_PORT] = entry_data[CONF_LOCAL_RTP_PORT] + 2
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == entry_data[CONF_PASSWORD]
    assert entry.data[CONF_ASSIST_USER] == "keep-me"
    assert entry.data[CONF_LOCAL_RTP_PORT] == entry_data[CONF_LOCAL_RTP_PORT] + 2


async def test_reconfigure_rejects_other_account(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1001@127.0.0.1", data=entry_data)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**entry_data, CONF_USERNAME: "1002"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == entry_data
    assert mock_pbx.registers == []


async def test_reconfigure_invalid_auth_shows_error(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    entry_data: dict[str, Any],
    mock_setup_entry: AsyncMock,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="1001@127.0.0.1", data=entry_data)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**entry_data, CONF_PASSWORD: "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data == entry_data


async def test_options_flow_saves_codec_preference_and_reloads(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    entry = init_integration
    old_client = entry.runtime_data["client"]
    assert old_client.config.codec_preference == "auto"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _schema_default(result, CONF_CODEC_PREFERENCE) == "auto"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CODEC_PREFERENCE: "g711_only_pcma_first"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CODEC_PREFERENCE: "g711_only_pcma_first"}
    await hass.async_block_till_done()

    # OptionsFlowWithReload: a fresh client runs with the new preference.
    assert entry.state is ConfigEntryState.LOADED
    new_client = entry.runtime_data["client"]
    assert new_client is not old_client
    assert new_client.config.codec_preference == "g711_only_pcma_first"
    await wait_for(lambda: entry.runtime_data["registered"])


async def test_options_flow_rejects_unknown_codec_preference(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_CODEC_PREFERENCE: "g722_only"}
        )
    assert init_integration.options == {}
