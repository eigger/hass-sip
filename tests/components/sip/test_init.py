"""Config entry lifecycle on a real Home Assistant.

The integration is loaded by HA's loader and config entry manager, its real
SipClient registers over UDP with the mock PBX, and the results are checked
through hass.states, the entity/device/issue registries and HA services.
"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
from typing import Any
from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.util import dt as dt_util

from custom_components.sip import async_unload_entry
from custom_components.sip.const import (
    CONF_ASSIST_USER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.sip.repairs import AUTH_FAIL_ISSUE_AFTER, register_auth_issue_id
from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, sip_device, wait_for
from .mock_pbx import MockPbx

SERVICES = {
    "dial",
    "hangup",
    "answer",
    "send_dtmf",
    "start_recording",
    "stop_recording",
    "start_assist",
}

# (platform, unique-id suffix) for every entity the account creates.
ENTITIES = [
    ("sensor", "registration_status"),
    ("sensor", "last_call"),
    ("sensor", "negotiated_codec"),
    ("sensor", "call_audio"),
    ("sensor", "last_call_reason"),
    ("binary_sensor", "call_active"),
    ("binary_sensor", "audio_bidirectional"),
    ("media_player", "media_player"),
    ("switch", "dnd"),
    ("event", "telephony_events"),
    ("button", "answer"),
    ("button", "hangup"),
]


def _fire_register_retry(client) -> None:
    """Run the client's pending REGISTER retry now instead of after backoff."""
    handle = client._register_handle
    assert handle is not None
    handle.cancel()
    client._register_handle = None
    client._register_timer()


def _client_closed(client) -> bool:
    return client._transport is None and client._register_handle is None


def _register_failure_records(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "custom_components.sip"
        and r.levelno == level
        and "SIP registration" in r.getMessage()
        and "fail" in r.getMessage()
    ]


async def test_setup_registers_and_loads_platforms(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_pbx: MockPbx,
    entity_registry: er.EntityRegistry,
) -> None:
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data["registered"] is True
    assert entry.runtime_data["client"].state == SipState.REGISTERED
    # 401 challenge answered with a digest REGISTER.
    assert any(m.header("Authorization") for m in mock_pbx.registers)

    device = sip_device(hass, entry)
    assert device.name == "SIP Extension 1001"
    assert device.serial_number == "1001"

    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    registered = {(e.domain, e.unique_id) for e in entities}
    for platform, key in ENTITIES:
        assert (platform, f"{entry.entry_id}_{key}") in registered
        assert all(e.device_id == device.id for e in entities)

    assert (
        hass.states.get(
            entity_id_for(hass, entry, "sensor", "registration_status")
        ).state
        == "registered"
    )
    assert (
        hass.states.get(
            entity_id_for(hass, entry, "binary_sensor", "call_active")
        ).state
        == STATE_OFF
    )
    assert (
        hass.states.get(entity_id_for(hass, entry, "switch", "dnd")).state == STATE_OFF
    )
    assert (
        hass.states.get(
            entity_id_for(hass, entry, "media_player", "media_player")
        ).state
        == STATE_OFF
    )
    # A DTMF digit from the SIP stack lands on the telephony event entity.
    event_entity = entity_id_for(hass, entry, "event", "telephony_events")
    assert hass.states.get(event_entity).state != STATE_UNAVAILABLE
    entry.runtime_data["client"].cb.on_dtmf("5")
    await hass.async_block_till_done()
    event_state = hass.states.get(event_entity)
    assert event_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
    assert event_state.attributes["event_type"] == "dtmf"
    assert event_state.attributes["digit"] == "5"

    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES
    # The Assist system user is created once and stored on the entry.
    user = await hass.auth.async_get_user(entry.data[CONF_ASSIST_USER])
    assert user is not None and user.system_generated


async def test_unload_entry_cleans_up(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    entry = init_integration
    client = entry.runtime_data["client"]
    start_task = entry.runtime_data["start_task"]
    sensor = entity_id_for(hass, entry, "sensor", "registration_status")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _client_closed(client)
    assert client.state == SipState.IDLE
    assert start_task.done()
    assert hass.states.get(sensor).state == STATE_UNAVAILABLE
    assert hass.services.async_services_for_domain(DOMAIN) == {}
    # The contact refresh timer and the stop listener went with the entry;
    # verify_cleanup fails the test on any lingering timer, task or socket.


async def test_unload_before_registration_finishes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unloading right after setup must not leave the starting client running."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    client = mock_config_entry.runtime_data["client"]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert _client_closed(client)


async def test_platform_setup_failure_stops_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A failure after the client started must not leave it running (HA-05)."""
    mock_config_entry.add_to_hass(hass)

    async def fail_after_register(entry, platforms) -> None:
        # Fail only once the real client is registered with the mock PBX, so
        # there is a live UDP transport and a REGISTER refresh timer to leak.
        await wait_for(lambda: entry.runtime_data["registered"])
        raise RuntimeError("platform setup exploded")

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=fail_after_register,
    ):
        assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    client = mock_config_entry.runtime_data["client"]
    assert _client_closed(client)
    assert client.state == SipState.IDLE
    assert mock_config_entry.runtime_data["start_task"].done()
    # Nothing was registered for a dead entry.
    assert hass.services.async_services_for_domain(DOMAIN) == {}
    # verify_cleanup (expected_lingering_timers=False) fails the test if the
    # REGISTER timer, contact refresh timer or the socket outlived the entry.


async def test_unload_entry_without_runtime_data(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """async_unload_entry tolerates an entry that never got runtime_data."""
    mock_config_entry.add_to_hass(hass)
    assert not hasattr(mock_config_entry, "runtime_data")
    assert await async_unload_entry(hass, mock_config_entry)


async def test_info_logs_mask_phone_numbers(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_pbx: MockPbx,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """INFO logs keep only the last two digits of a caller / dialled number."""
    entry = init_integration
    client = entry.runtime_data["client"]
    target = {"entity_id": entity_id_for(hass, entry, "media_player", "media_player")}
    caplog.set_level(logging.INFO, logger="custom_components.sip")

    # Inbound: the PBX rings the account from a mobile number.
    mock_pbx.send_invite("01012345678")
    await wait_for(lambda: client.state == SipState.INCOMING)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)

    # Outbound: dialling a number.
    await hass.services.async_call(
        DOMAIN, "dial", {"number": "01099998888", **target}, blocking=True
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.services.async_call(DOMAIN, "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)

    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.sip") and r.levelno >= logging.INFO
    ]
    assert "[1001] Incoming call from *********78" in messages  # integration
    assert "Incoming call from *********78" in messages  # SIP stack
    assert "Calling *********88" in messages
    info = "\n".join(messages)
    assert "01012345678" not in info
    assert "01099998888" not in info


async def test_services_stay_until_last_entry_unloads(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entry_data: dict[str, Any],
) -> None:
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1002@127.0.0.1",
        data={
            **entry_data,
            CONF_USERNAME: "1002",
            "local_rtp_port": entry_data["local_rtp_port"] + 2,
        },
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert other.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES

    assert await hass.config_entries.async_unload(other.entry_id)
    await hass.async_block_till_done()
    assert hass.services.async_services_for_domain(DOMAIN) == {}


async def test_reload_replaces_client(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    entry = init_integration
    old_client = entry.runtime_data["client"]
    entities_before = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assist_user = entry.data[CONF_ASSIST_USER]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _client_closed(old_client)
    new_client = entry.runtime_data["client"]
    assert new_client is not old_client
    await wait_for(lambda: entry.runtime_data["registered"])
    assert {
        e.entity_id
        for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    } == {e.entity_id for e in entities_before}
    # Reload reuses the Assist user instead of creating another one.
    assert entry.data[CONF_ASSIST_USER] == assist_user


async def test_unreachable_pbx_keeps_entry_loaded_and_retrying(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SIP setup is push-based: a down PBX is reported, not a setup failure."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sip")
    mock_pbx.force_code = 503
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: mock_config_entry.runtime_data["last_register_failed"])

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data["registered"] is False
    sensor = entity_id_for(hass, mock_config_entry, "sensor", "registration_status")
    assert hass.states.get(sensor).state != "registered"
    client = mock_config_entry.runtime_data["client"]
    assert client._register_handle is not None  # retry scheduled

    # Every retry fails again, but the outage is warned about only once.
    for _ in range(2):
        mock_config_entry.runtime_data["last_register_failed"] = None
        _fire_register_retry(client)
        await wait_for(lambda: mock_config_entry.runtime_data["last_register_failed"])
    assert len(_register_failure_records(caplog, logging.WARNING)) == 1
    assert len(_register_failure_records(caplog, logging.DEBUG)) == 2

    # The PBX comes back: the retry registers and the sensor follows.
    mock_pbx.force_code = None
    _fire_register_retry(client)
    await wait_for(lambda: mock_config_entry.runtime_data["registered"])
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "registered"
    assert "SIP registration recovered" in caplog.text

    # A new outage after recovery is warned about again.
    mock_pbx.force_code = 503
    mock_config_entry.runtime_data["last_register_failed"] = None
    _fire_register_retry(client)
    await wait_for(lambda: mock_config_entry.runtime_data["last_register_failed"])
    assert len(_register_failure_records(caplog, logging.WARNING)) == 2

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _client_closed(client)


async def test_register_auth_failure_opens_and_clears_repair_issue(
    hass: HomeAssistant,
    mock_pbx: MockPbx,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_pbx.password = "rotated-on-the-pbx"
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    client = mock_config_entry.runtime_data["client"]
    issue_id = register_auth_issue_id(mock_config_entry.entry_id)

    for attempt in range(1, AUTH_FAIL_ISSUE_AFTER + 1):
        await wait_for(lambda: client.register_auth_failures >= attempt)
        if attempt < AUTH_FAIL_ISSUE_AFTER:
            # One bad password is not yet a repair issue.
            assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
            _fire_register_retry(client)
    await wait_for(lambda: issue_registry.async_get_issue(DOMAIN, issue_id))
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue.translation_key == "register_auth_failed"
    assert issue.translation_placeholders["username"] == "1001"
    assert "rotated" not in str(issue.translation_placeholders)

    # Fixing the password on the PBX side clears the issue on success.
    mock_pbx.password = mock_config_entry.data[CONF_PASSWORD]
    _fire_register_retry(client)
    await wait_for(lambda: mock_config_entry.runtime_data["registered"])
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_unload_clears_open_repair_issue(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    entry = init_integration
    issue_id = register_auth_issue_id(entry.entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="register_auth_failed",
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_remove_entry_deletes_assist_user(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    entry = init_integration
    user_id = entry.data[CONF_ASSIST_USER]
    client = entry.runtime_data["client"]

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.auth.async_get_user(user_id) is None
    assert _client_closed(client)


async def test_homeassistant_stop_stops_client(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    client = init_integration.runtime_data["client"]
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert _client_closed(client)


async def test_contacts_loaded_at_setup_and_refreshed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)
    contacts = tmp_path / "sip_contacts.json"
    contacts.write_text(
        json.dumps({"200": {"name": "Front door", "auto_answer": True}}),
        encoding="utf-8",
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    data = mock_config_entry.runtime_data
    client = data["client"]

    assert client.auto_answer_checker("200") is True
    assert client.auto_answer_checker("201") is False
    client.cb.on_incoming_call("200")
    await hass.async_block_till_done()
    assert data["last_caller"] == "Front door"
    assert data["call_status"] == "answered"  # auto-answer contact

    contacts.write_text(json.dumps({"201": "Gate"}), encoding="utf-8")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert data["contacts"] == {"201": "Gate"}
    assert client.auto_answer_checker("200") is False

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_device_shows_up_once_per_account(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert [d.identifiers for d in devices] == [{(DOMAIN, init_integration.entry_id)}]
