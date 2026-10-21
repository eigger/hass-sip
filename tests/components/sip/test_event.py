"""Telephony events emitted by the loaded SIP event platform."""

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, phone_line, wait_for


async def test_incoming_and_ended_event_payloads(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """Real SIP packets publish caller data and the subsequent hangup reason."""
    entry = init_integration
    client = entry.runtime_data["client"]
    entity_id = entity_id_for(hass, entry, "event", "telephony_events")

    mock_pbx.send_invite("2002")
    await wait_for(lambda: client.state == SipState.INCOMING)
    await hass.async_block_till_done()
    incoming = hass.states.get(entity_id)
    assert incoming.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert incoming.attributes["event_type"] == "incoming"
    assert incoming.attributes["caller"] == "2002"

    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()
    ended = hass.states.get(entity_id)
    assert ended.attributes["event_type"] == "ended"
    assert ended.attributes["reason"] == "local"
    assert "caller" not in ended.attributes


async def test_connected_event_and_unrecognized_dispatcher_event(
    hass: HomeAssistant, init_integration
) -> None:
    """An answered call emits connected; unsupported events preserve that state."""
    entry = init_integration
    entity_id = entity_id_for(hass, entry, "event", "telephony_events")
    await hass.services.async_call(
        "sip", "dial", {"number": "2002", **phone_line(hass, entry)}, blocking=True
    )
    await wait_for(lambda: entry.runtime_data["client"].state == SipState.IN_CALL)
    await hass.async_block_till_done()
    connected = hass.states.get(entity_id)
    assert connected.attributes["event_type"] == "connected"
    assert "connected" in connected.attributes["event_types"]

    async_dispatcher_send(
        hass, f"sip_event_{entry.entry_id}", "unsupported_event", {"caller": "other"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) == connected

    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
