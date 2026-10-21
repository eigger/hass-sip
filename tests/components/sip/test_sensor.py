"""Sensor states and call metadata from the real integration lifecycle."""

from datetime import datetime

from homeassistant.core import HomeAssistant

from custom_components.sip.sip_client.sip_client import SipState

from .common import entity_id_for, phone_line, sm, wait_for


async def test_registration_sensor_exposes_account_metadata(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """Successful digest registration is visible with account diagnostics."""
    entity_id = entity_id_for(hass, init_integration, "sensor", "registration_status")
    state = hass.states.get(entity_id)
    assert state.state == "registered"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["server"] == "127.0.0.1"
    assert state.attributes["port"] == mock_pbx.port
    assert state.attributes["username"] == mock_pbx.username
    assert state.attributes["domain"] == mock_pbx.domain


async def test_call_sensors_preserve_completed_incoming_call(
    hass: HomeAssistant, init_integration, mock_pbx
) -> None:
    """Answering negotiates a codec and hanging up retains call history/reason."""
    entry = init_integration
    client = entry.runtime_data["client"]
    last_call = entity_id_for(hass, entry, "sensor", "last_call")
    codec = entity_id_for(hass, entry, "sensor", "negotiated_codec")
    audio = entity_id_for(hass, entry, "sensor", "call_audio")
    reason = entity_id_for(hass, entry, "sensor", "last_call_reason")
    assert hass.states.get(last_call).state == "none"
    assert hass.states.get(audio).state == "none"

    mock_pbx.send_invite("2002")
    await wait_for(lambda: client.state == SipState.INCOMING)
    await hass.services.async_call(
        "sip", "answer", phone_line(hass, entry), blocking=True
    )
    # The minimal PBX records responses; act as its caller and acknowledge
    # the 200 response so the real SIP dialog becomes established.
    await wait_for(
        lambda: any(
            not msg.is_request
            and msg.status_code == 200
            and "INVITE" in msg.header("CSeq")
            for msg in mock_pbx.received
        )
    )
    response = next(
        msg
        for msg in mock_pbx.received
        if not msg.is_request
        and msg.status_code == 200
        and "INVITE" in msg.header("CSeq")
    )
    mock_pbx.send_raw(
        f"ACK {mock_pbx.client_contact} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {mock_pbx.host}:{mock_pbx.port};branch={sm.gen_branch()}\r\n"
        f"From: {response.header('From')}\r\n"
        f"To: {response.header('To')}\r\n"
        f"Call-ID: {response.header('Call-ID')}\r\n"
        "CSeq: 1 ACK\r\nContent-Length: 0\r\n\r\n"
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.async_block_till_done()
    assert hass.states.get(last_call).state == "incoming"
    assert datetime.fromisoformat(hass.states.get(last_call).attributes["timestamp"])
    assert hass.states.get(codec).state == "PCMU"
    assert hass.states.get(codec).attributes["payload_type"] == 0
    assert hass.states.get(codec).attributes["sample_rate"] == 8000

    await hass.services.async_call(
        "sip", "hangup", phone_line(hass, entry), blocking=True
    )
    await wait_for(lambda: client.state == SipState.REGISTERED)
    await hass.async_block_till_done()
    completed = hass.states.get(last_call)
    assert completed.state == "incoming"
    assert completed.attributes["last_caller"] == "2002"
    history = completed.attributes["call_history"]
    assert len(history) == 1
    assert history[0]["number"] == "2002"
    assert history[0]["direction"] == "incoming"
    assert history[0]["status"] == "answered"
    assert history[0]["reason"] == "local"
    assert history[0]["duration"] >= 0
    assert hass.states.get(reason).state == "local"
    assert hass.states.get(audio).attributes["last_end_reason"] == "local"
    assert hass.states.get(codec).state == "PCMU"
