"""Config-entry diagnostics: redaction and registration / codec / one-way audio (P1-2)."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .common import (
    _load_component_module,
    phone_line,
    rtp_session,
    sip_client,
    sm,
    wait_for,
)


diagnostics = _load_component_module("diagnostics")


def _pcmu(seq=1, ssrc=0x1234):
    return bytes([0x80, 0]) + struct.pack("!HII", seq, 100, ssrc) + b"\xff" * 160


def test_mask_number_keeps_last_two_digits():
    assert diagnostics.mask_number("01012345678") == "*********78"
    assert diagnostics.mask_number("sip:1001@pbx.example") == "sip:**01@pbx.example"
    assert diagnostics.mask_number("12") == "12"
    assert diagnostics.mask_number("") == ""
    assert diagnostics.mask_number(None) is None


def test_collect_diagnostics_redacts_password_and_masks_history():
    payload = diagnostics.collect_diagnostics(
        {
            "server": "pbx.example",
            "username": "100",
            "password": "super-secret",
            "authentication_username": "auth100",
            "caller_id": "Kim 01011112222",
            "outbound_proxy": "proxy.carrier.example:5060",
            "local_rtp_port": 7078,
        },
        {
            "call_history": [
                {
                    "number": "01012345678",
                    "name": "Kitchen",
                    "direction": "incoming",
                    "reason": "remote_bye",
                }
            ],
            "call_direction": "incoming",
            "call_number": "01012345678",
            "call_status": "answered",
        },
    )
    redacted = async_redact_data(payload, diagnostics.TO_REDACT)
    assert redacted["config"]["password"] == "**REDACTED**"
    assert redacted["config"]["authentication_username"] == "**REDACTED**"
    assert redacted["config"]["caller_id"] == "**REDACTED**"
    assert redacted["config"]["outbound_proxy"] == "**REDACTED**"
    # The extension username is kept on purpose: it identifies which account
    # the download belongs to and is not a secret on its own.
    assert redacted["config"]["server"] == "pbx.example"
    assert redacted["config"]["username"] == "100"
    assert payload["call_history"][0]["number"] == "*********78"
    assert payload["call_history"][0]["name"] == "Kitchen"
    assert payload["current_call"]["number"] == "*********78"


def test_diagnostics_snapshot_registration_failure():

    async def run():
        failed = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example", password="secret"),
            sip_client.SipCallbacks(on_register_failed=failed.append),
        )
        client._reg_cseq = 1
        client._handle_register_response(
            sm.parse_sip_message("SIP/2.0 403 Forbidden\r\nCSeq: 1 REGISTER\r\n\r\n")
        )
        return client.diagnostics_snapshot(), failed

    snap, failed = asyncio.run(run())
    assert failed == ["403 Forbidden"]
    assert snap["registration"]["registered"] is False
    assert snap["registration"]["last_failure"] == "403 Forbidden"
    assert "secret" not in str(snap)


def test_diagnostics_snapshot_codec_mismatch():

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        sdp = sm.parse_sdp(
            "v=0\r\nc=IN IP4 203.0.113.8\r\n"
            "m=audio 5004 RTP/AVP 96 101\r\n"
            "a=rtpmap:96 opus/48000/2\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
        )
        client._apply_remote_sdp(sdp)
        return client.diagnostics_snapshot()

    snap = asyncio.run(run())
    assert snap["codec"]["mismatch"] is True
    assert snap["codec"]["remote_offered"] == []
    assert 96 in snap["codec"]["remote_offered_pts"]
    assert snap["codec"]["negotiated"] == "PCMU"


def test_diagnostics_snapshot_matching_codec_is_not_mismatch():

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        sdp = sm.parse_sdp(
            "v=0\r\nc=IN IP4 203.0.113.8\r\n"
            "m=audio 5004 RTP/AVP 9 0 101\r\n"
            "a=rtpmap:9 G722/8000\r\n"
        )
        client._apply_remote_sdp(sdp)
        return client.diagnostics_snapshot()

    snap = asyncio.run(run())
    assert snap["codec"]["mismatch"] is False
    assert snap["codec"]["negotiated"] == "G722"
    assert "G722" in snap["codec"]["remote_offered"]


def test_diagnostics_snapshot_one_way_audio():

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.rtp.bytes_sent = 32000
        client.rtp.bytes_received = 0
        client.last_call_bytes_tx = 32000
        client.last_call_bytes_rx = 0
        client.last_call_reason = "media_timeout"
        no_rx = client.diagnostics_snapshot()
        client.rtp.bytes_received = 16000
        client.last_call_bytes_rx = 16000
        both = client.diagnostics_snapshot()
        return no_rx, both

    no_rx, both = asyncio.run(run())
    assert no_rx["rtp"]["audio_path"] == "no_rx"
    assert no_rx["call"]["last_end_reason"] == "media_timeout"
    assert both["rtp"]["audio_path"] == "bidirectional"


def test_diagnostics_no_media_call_does_not_inherit_previous_rtp_bytes():
    """486 / CANCEL / ring-timeout must not copy the previous call's PCM totals."""

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.registered = True
        client.rtp.bytes_sent = 32000
        client.rtp.bytes_received = 16000
        client._end_call("local")
        after_a = client.diagnostics_snapshot()
        client._begin_dialog_media()
        client.state = sip_client.SipState.RINGING_OUT
        ringing = client.diagnostics_snapshot()
        client.state = sip_client.SipState.IN_CALL
        in_call_no_rtp = client.diagnostics_snapshot()
        client._end_call("remote_reject")
        after_b = client.diagnostics_snapshot()
        return after_a, ringing, in_call_no_rtp, after_b

    after_a, ringing, in_call_no_rtp, after_b = asyncio.run(run())
    assert after_a["rtp"]["audio_path"] == "bidirectional"
    assert after_a["call"]["last_end_reason"] == "local"
    assert ringing["rtp"]["audio_path"] == "none"
    assert ringing["rtp"]["bytes_received"] == 0
    assert ringing["rtp"]["bytes_sent"] == 0
    assert in_call_no_rtp["call"]["in_call"] is True
    assert in_call_no_rtp["rtp"]["running"] is False
    assert in_call_no_rtp["rtp"]["audio_path"] == "none"
    assert after_b["call"]["last_end_reason"] == "remote_reject"
    assert after_b["rtp"]["audio_path"] == "none"
    assert after_b["rtp"]["bytes_received"] == 0
    assert after_b["rtp"]["bytes_sent"] == 0


def test_rtp_byte_counters_count_pcm_not_dtmf():
    async def run():
        session = rtp_session.RtpSession()
        session.set_remote("192.0.2.1", 5004)
        session._transport = MagicMock()
        session._send_audio_packet(b"\x00" * 320)
        session._receive_impl(_pcmu(), ("203.0.113.8", 20000))
        te = (
            bytes([0x80, 101])
            + struct.pack("!HII", 2, 100, 0x1234)
            + bytes([1, 0x0A, 0x00, 0xA0])
        )
        session.dtmf_pt = 101
        session._receive_impl(te, ("203.0.113.8", 20000))
        return session.bytes_sent, session.bytes_received

    sent, received = asyncio.run(run())
    assert sent == 320
    assert received == 320  # decoded PCMU frame


async def test_async_get_config_entry_diagnostics_redacts(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration,
) -> None:
    """Download diagnostics through HA's diagnostics platform after a real call."""
    entry = init_integration
    client = entry.runtime_data["client"]
    # A real outbound call to the mock PBX, then local hang-up, so the call
    # history entry comes from the integration's own callbacks.
    target = phone_line(hass, entry)
    await hass.services.async_call(
        "sip", "dial", {"number": "01099998888", **target}, blocking=True
    )
    await wait_for(lambda: client.state == sip_client.SipState.IN_CALL)
    await hass.services.async_call("sip", "hangup", target, blocking=True)
    await wait_for(lambda: entry.runtime_data["call_history"])
    client.last_caller = "01099998888"
    # Display name / carrier proxy are account details, not needed to debug.
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "caller_id": "Front desk 0215550000",
            "outbound_proxy": "proxy.carrier.example",
        },
    )

    payload = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert payload["config"]["password"] == "**REDACTED**"
    assert payload["config"]["username"] == entry.data["username"]
    assert entry.data["password"] not in str(payload)
    assert payload["config"]["caller_id"] == "**REDACTED**"
    assert payload["config"]["outbound_proxy"] == "**REDACTED**"
    assert "0215550000" not in str(payload)
    assert "proxy.carrier.example" not in str(payload)
    assert payload["call_history"][0]["number"] == "*********88"
    assert payload["call_history"][0]["direction"] == "outgoing"
    assert payload["sip"]["call"]["last_caller"] == "*********88"
    assert payload["sip"]["registration"]["registered"] is True
