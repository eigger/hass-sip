"""Binary sensor states observed through real SIP calls and HA services."""

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, phone_line, wait_for


async def test_incoming_call_active_until_declined(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """An unanswered inbound call is active, and declining clears it."""
    entry = init_integration
    client = entry.runtime_data["client"]
    entity_id = entity_id_for(hass, entry, "binary_sensor", "call_active")
    assert hass.states.get(entity_id).state == STATE_OFF

    mock_pbx.send_invite("2002")
    await wait_for(lambda: client.state == SipState.INCOMING)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["in_call"] is False
    assert state.attributes["username"] == mock_pbx.username

    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_outbound_call_is_active_before_answer(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """Dialling counts as active even before the remote party answers."""
    entry = init_integration
    mock_pbx.auto_answer_invite = False
    entity_id = entity_id_for(hass, entry, "binary_sensor", "call_active")
    audio_id = entity_id_for(hass, entry, "binary_sensor", "audio_bidirectional")

    await hass.services.async_call(
        "sip", "dial", {"number": "2002", **phone_line(hass, entry)}, blocking=True
    )
    await wait_for(lambda: bool(mock_pbx.invites))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON
    assert hass.states.get(entity_id).attributes["in_call"] is False
    audio = hass.states.get(audio_id)
    assert audio.state == STATE_OFF
    assert audio.attributes["audio_path"] == "none"
    assert audio.attributes["bytes_received"] == 0
    assert audio.attributes["bytes_sent"] == 0

    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_OFF
