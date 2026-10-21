"""DND switch services affect actual inbound SIP call handling."""

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, phone_line, wait_for


async def test_dnd_services_reject_then_allow_incoming_calls(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """Changing DND through HA services updates both the switch and the PBX."""
    entry = init_integration
    client = entry.runtime_data["client"]
    entity_id = entity_id_for(hass, entry, "switch", "dnd")
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["icon"] == "mdi:phone-off"
    assert client.dnd is True

    mock_pbx.send_invite("2002")
    await wait_for(
        lambda: any(
            not msg.is_request and msg.status_code == 486 for msg in mock_pbx.received
        )
    )
    assert client.state == SipState.REGISTERED

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF
    assert state.attributes["icon"] == "mdi:phone"
    assert client.dnd is False

    mock_pbx.send_invite("2003")
    await wait_for(lambda: client.state == SipState.INCOMING)
    await hass.async_block_till_done()
    active = entity_id_for(hass, entry, "binary_sensor", "call_active")
    assert hass.states.get(active).state == STATE_ON
    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
