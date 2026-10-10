"""Diagnostics for calls that end up with no audio (one-way / no RTP)."""
from __future__ import annotations

import asyncio
import logging

from test_pure import sip_client, sm

if sip_client is None:  # enum.StrEnum needs Python 3.11+
    def _skip():
        return True
else:
    def _skip():
        return False


def _reg_response(received: str | None, cseq: int = 1):
    via = "SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKx;rport=5060"
    if received:
        via += f";received={received}"
    return sm.parse_sip_message(
        f"SIP/2.0 200 OK\r\nVia: {via}\r\nCSeq: {cseq} REGISTER\r\n\r\n"
    )


def _warnings(caplog, needle: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and needle in r.getMessage()
    ]


def test_via_received_parsing():
    if _skip():
        return
    assert sip_client._via_received("SIP/2.0/UDP a:1;rport=1;received=10.0.0.5") == (
        "10.0.0.5"
    )
    assert sip_client._via_received("SIP/2.0/UDP a:1;rport=1") == ""
    assert sip_client._via_received(
        "SIP/2.0/UDP a:1;received=10.0.0.5, SIP/2.0/UDP b:1;received=10.0.0.9"
    ) == "10.0.0.5"


def test_nat_mismatch_warns_once(caplog):
    if _skip():
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = lambda *_: None
        client._reg_cseq = 1
        client._local_ip = "192.0.2.10"
        with caplog.at_level(logging.WARNING):
            client._handle_register_response(_reg_response("198.51.100.7"))
            client._handle_register_response(_reg_response("198.51.100.7"))
        assert len(_warnings(caplog, "sees this client as 198.51.100.7")) == 1

    asyncio.run(run())


def test_matching_or_missing_received_does_not_warn(caplog):
    if _skip():
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = lambda *_: None
        client._reg_cseq = 1
        client._local_ip = "192.0.2.10"
        with caplog.at_level(logging.WARNING):
            client._handle_register_response(_reg_response("192.0.2.10"))
            client._handle_register_response(_reg_response(None))
        assert _warnings(caplog, "sees this client") == []

    asyncio.run(run())


def _in_call_client(rx: int, tx: int, state=None, on_hold=False):
    client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
    client.state = state or sip_client.SipState.IN_CALL
    client._local_ip = "192.0.2.10"
    client._on_hold = on_hold
    client.rtp.bytes_received = rx
    client.rtp.bytes_sent = tx
    client.rtp._sdp_remote = ("192.0.2.1", 7084)
    return client


def _end(client):
    client._spawn = lambda coro: coro.close()
    client._end_call("local")


def test_call_without_any_rtp_warns(caplog):
    if _skip():
        return

    async def run():
        client = _in_call_client(rx=0, tx=40000)
        with caplog.at_level(logging.WARNING):
            _end(client)
        msgs = _warnings(caplog, "without receiving any RTP")
        assert len(msgs) == 1
        assert "192.0.2.1:7084" in msgs[0]

    asyncio.run(run())


def test_no_rtp_warning_when_audio_arrived_short_or_held(caplog):
    if _skip():
        return

    async def run():
        cases = [
            _in_call_client(rx=32000, tx=40000),  # audio came in
            _in_call_client(rx=0, tx=1000),  # too short to judge
            _in_call_client(rx=0, tx=40000, on_hold=True),  # remote on hold
            _in_call_client(
                rx=0, tx=40000, state=sip_client.SipState.INVITING
            ),  # never connected
        ]
        with caplog.at_level(logging.WARNING):
            for client in cases:
                _end(client)
        assert _warnings(caplog, "without receiving any RTP") == []

    asyncio.run(run())
