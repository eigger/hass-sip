"""DND switch restores on/off across Home Assistant restarts (P2-5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.sip.const import DOMAIN
from custom_components.sip.sip_client.sip_client import SipState

from .common import _load_component_module, wait_for
from .mock_pbx import MockPbx

dnd = _load_component_module("dnd")


def test_restored_dnd_enabled_mapping():
    assert dnd.restored_dnd_enabled("on") is True
    assert dnd.restored_dnd_enabled("off") is False
    assert dnd.restored_dnd_enabled(None) is None
    assert dnd.restored_dnd_enabled("unknown") is None
    assert dnd.restored_dnd_enabled("unavailable") is None


def test_dnd_switch_restores_on_from_last_state():
    client = MagicMock()
    client.dnd = False
    dnd.apply_restored_dnd(client, "on")
    assert client.dnd is True


def test_dnd_switch_restores_off_over_live_default():
    client = MagicMock()
    client.dnd = True
    dnd.apply_restored_dnd(client, "off")
    assert client.dnd is False


def test_dnd_switch_keeps_default_when_no_last_state():
    client = MagicMock()
    client.dnd = False
    dnd.apply_restored_dnd(client, None)
    assert client.dnd is False
    dnd.apply_restored_dnd(client, "unknown")
    assert client.dnd is False


# ------------------------------------------------- real Home Assistant restart
@pytest.mark.parametrize(("restored", "expected"), [("on", True), ("off", False)])
async def test_dnd_restored_after_restart_through_setup(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    restored: str,
    expected: bool,
) -> None:
    """The DnD switch's last state is restored on a real config entry setup."""
    mock_config_entry.add_to_hass(hass)
    # The entity existed before the restart, so HA restores it by entity_id.
    entity_id = entity_registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{mock_config_entry.entry_id}_dnd",
        suggested_object_id="sip_1001_dnd",
        config_entry=mock_config_entry,
    ).entity_id
    mock_restore_cache(hass, [State(entity_id, restored)])

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: mock_config_entry.runtime_data["registered"])
    client = mock_config_entry.runtime_data["client"]

    assert client.dnd is expected
    assert hass.states.get(entity_id).state == (STATE_ON if expected else STATE_OFF)

    if expected:
        # A restored DnD really rejects the next inbound call with 486.
        mock_pbx.send_invite("01055556666")
        await wait_for(
            lambda: any(
                not m.is_request and m.status_code == 486 for m in mock_pbx.received
            )
        )
        await hass.async_block_till_done()
        assert client.state == SipState.REGISTERED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
