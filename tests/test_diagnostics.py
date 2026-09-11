"""Config-entry diagnostics: redaction and registration / codec / one-way audio (P1-2)."""
from __future__ import annotations

import asyncio
import struct
import sys
import types
from unittest.mock import MagicMock

from test_pure import _load_component_module, rtp_session, sip_client, sm


def _redact(data, to_redact):
    if isinstance(data, dict):
        return {
            key: "**REDACTED**" if key in to_redact else _redact(value, to_redact)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_redact(item, to_redact) for item in data]
    return data


_diag_mod = types.ModuleType("homeassistant.components.diagnostics")
_diag_mod.async_redact_data = _redact
sys.modules["homeassistant.components.diagnostics"] = _diag_mod
mock_components = sys.modules.get("homeassistant.components")
if isinstance(mock_components, MagicMock):
    mock_components.diagnostics = _diag_mod

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
    redacted = _redact(payload, diagnostics.TO_REDACT)
    assert redacted["config"]["password"] == "**REDACTED**"
    assert redacted["config"]["authentication_username"] == "**REDACTED**"
    assert redacted["config"]["server"] == "pbx.example"
    assert redacted["config"]["username"] == "100"
    assert payload["call_history"][0]["number"] == "*********78"
    assert payload["call_history"][0]["name"] == "Kitchen"
    assert payload["current_call"]["number"] == "*********78"


def test_diagnostics_snapshot_registration_failure():
    if sip_client is None:
        return

    async def run():
        failed = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example", password="secret"),
            sip_client.SipCallbacks(on_register_failed=failed.append),
        )
        client._reg_cseq = 1
        client._handle_register_response(
            sm.parse_sip_message(
                "SIP/2.0 403 Forbidden\r\nCSeq: 1 REGISTER\r\n\r\n"
            )
        )
        return client.diagnostics_snapshot(), failed

    snap, failed = asyncio.run(run())
    assert failed == ["403 Forbidden"]
    assert snap["registration"]["registered"] is False
    assert snap["registration"]["last_failure"] == "403 Forbidden"
    assert "secret" not in str(snap)


def test_diagnostics_snapshot_codec_mismatch():
    if sip_client is None:
        return

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
    if sip_client is None:
        return

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
    if sip_client is None:
        return

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
    if sip_client is None:
        return

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
        client._end_call("remote_reject")
        after_b = client.diagnostics_snapshot()
        return after_a, ringing, after_b

    after_a, ringing, after_b = asyncio.run(run())
    assert after_a["rtp"]["audio_path"] == "bidirectional"
    assert after_a["call"]["last_end_reason"] == "local"
    assert ringing["rtp"]["audio_path"] == "none"
    assert ringing["rtp"]["bytes_received"] == 0
    assert ringing["rtp"]["bytes_sent"] == 0
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
        te = bytes([0x80, 101]) + struct.pack("!HII", 2, 100, 0x1234) + bytes(
            [1, 0x0A, 0x00, 0xA0]
        )
        session.dtmf_pt = 101
        session._receive_impl(te, ("203.0.113.8", 20000))
        return session.bytes_sent, session.bytes_received

    sent, received = asyncio.run(run())
    assert sent == 320
    assert received == 320  # decoded PCMU frame


def test_async_get_config_entry_diagnostics_redacts():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example", password="secret")
        )
        client.last_caller = "01099998888"
        entry = MagicMock()
        entry.data = {
            "server": "pbx.example",
            "username": "100",
            "password": "secret",
            "authentication_username": "auth100",
        }
        entry.runtime_data = {
            "client": client,
            "call_history": [{"number": "01099998888", "reason": "local"}],
        }
        return await diagnostics.async_get_config_entry_diagnostics(MagicMock(), entry)

    payload = asyncio.run(run())
    assert payload["config"]["password"] == "**REDACTED**"
    assert payload["config"]["authentication_username"] == "**REDACTED**"
    assert "secret" not in str(payload)
    assert payload["call_history"][0]["number"] == "*********88"
    assert payload["sip"]["call"]["last_caller"] == "*********88"
