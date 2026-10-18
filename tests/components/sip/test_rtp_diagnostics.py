"""Diagnostics for calls that end up with no audio (one-way / no RTP)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from .common import sip_client, sm

# These tests drive real UDP sockets on 127.0.0.1 (mock PBX / RTP peers).
pytestmark = pytest.mark.usefixtures("socket_enabled")


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
    assert sip_client._via_received("SIP/2.0/UDP a:1;rport=1;received=10.0.0.5") == (
        "10.0.0.5"
    )
    assert sip_client._via_received("SIP/2.0/UDP a:1;rport=1") == ""
    assert (
        sip_client._via_received(
            "SIP/2.0/UDP a:1;received=10.0.0.5, SIP/2.0/UDP b:1;received=10.0.0.9"
        )
        == "10.0.0.5"
    )


def test_nat_mismatch_warns_once(caplog):

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = lambda *_: None
        client._reg_cseq = 1
        client._local_ip = "192.168.1.10"
        with caplog.at_level(logging.INFO):
            client._handle_register_response(_reg_response("10.0.0.7"))
            client._handle_register_response(_reg_response("10.0.0.7"))
        assert len(_warnings(caplog, "sees this client as 10.0.0.7")) == 1
        assert client._observed_ip == "10.0.0.7"

    asyncio.run(run())


def test_public_received_address_is_only_info(caplog):

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = lambda *_: None
        client._reg_cseq = 1
        client._local_ip = "192.168.1.10"
        with caplog.at_level(logging.INFO):
            client._handle_register_response(_reg_response("8.8.8.8"))
        assert _warnings(caplog, "sees this client") == []
        assert any(
            r.levelno == logging.INFO
            and "sees this client as 8.8.8.8" in r.getMessage()
            for r in caplog.records
        )

    asyncio.run(run())


def test_observed_address_cleared_when_it_matches_again():

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = lambda *_: None
        client._reg_cseq = 1
        client._local_ip = "192.168.1.10"
        client._handle_register_response(_reg_response("10.0.0.7"))
        assert client._observed_ip == "10.0.0.7"
        client._handle_register_response(_reg_response("192.168.1.10"))
        assert client._observed_ip == ""

    asyncio.run(run())


def test_cgnat_and_vpn_range_counts_as_lan():
    assert sip_client._is_lan_address("100.64.1.2")
    assert sip_client._is_lan_address("192.168.1.2")
    assert not sip_client._is_lan_address("8.8.8.8")
    assert not sip_client._is_lan_address("[2001:db8::1]")


def test_matching_or_missing_received_does_not_warn(caplog):

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


def _in_call_client(
    rx: int, tx: int, state=None, on_hold=False, expect_rx=True, rx_packets=None
):
    client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
    client.state = state or sip_client.SipState.IN_CALL
    client._local_ip = "192.0.2.10"
    client._on_hold = on_hold
    client.rtp.bytes_received = rx
    client.rtp.rx_packets = rx_packets if rx_packets is not None else (1 if rx else 0)
    client.rtp.expect_rx = expect_rx
    client.rtp.bytes_sent = tx
    client.rtp._sdp_remote = ("192.0.2.1", 7084)
    return client


def _end(client):
    client._spawn = lambda coro: coro.close()
    client._end_call("local")


def test_call_without_any_rtp_warns(caplog):

    async def run():
        client = _in_call_client(rx=0, tx=80000)
        with caplog.at_level(logging.WARNING):
            _end(client)
        msgs = _warnings(caplog, "without receiving any RTP")
        assert len(msgs) == 1
        assert "192.0.2.1:7084" in msgs[0]
        assert "saw this client" not in msgs[0]

    asyncio.run(run())


def test_end_of_call_warning_includes_observed_address(caplog):

    async def run():
        client = _in_call_client(rx=0, tx=80000)
        client._observed_ip = "10.0.0.7"
        with caplog.at_level(logging.WARNING):
            _end(client)
        msgs = _warnings(caplog, "without receiving any RTP")
        assert "saw this client as 10.0.0.7" in msgs[0]

    asyncio.run(run())


def test_no_rtp_warning_when_audio_arrived_short_or_held(caplog):

    async def run():
        cases = [
            _in_call_client(rx=32000, tx=40000),  # audio came in
            _in_call_client(rx=0, tx=1000),  # too short to judge
            _in_call_client(rx=0, tx=47999),  # just under 3 s of G.711
            _in_call_client(rx=0, tx=80000, rx_packets=5),  # only CN/undecodable
            _in_call_client(rx=0, tx=80000, expect_rx=False),  # paging speaker
            _in_call_client(rx=0, tx=80000, on_hold=True),  # remote on hold
            _in_call_client(
                rx=0, tx=80000, state=sip_client.SipState.INVITING
            ),  # never connected
        ]
        with caplog.at_level(logging.WARNING):
            for client in cases:
                _end(client)
        assert _warnings(caplog, "without receiving any RTP") == []

    asyncio.run(run())


def test_first_rtp_packet_source_is_logged_once(caplog):

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        rtp = client.rtp
        rtp._sdp_remote = ("192.0.2.1", 7084)
        packet = bytes([0x80, 8]) + (1).to_bytes(2, "big") + bytes(8) + b"\x00" * 160
        with caplog.at_level(logging.INFO):
            rtp._receive(packet, ("192.0.2.1", 7084))
            rtp._receive(packet, ("192.0.2.1", 7084))
        first = [
            r.getMessage()
            for r in caplog.records
            if "First RTP packet" in r.getMessage()
        ]
        assert len(first) == 1 and "differs" not in first[0]
        assert rtp.rx_packets == 2

        caplog.clear()
        rtp.reset_byte_counters()
        with caplog.at_level(logging.INFO):
            rtp._receive(packet, ("203.0.113.9", 4000))
        first = [
            r.getMessage()
            for r in caplog.records
            if "First RTP packet" in r.getMessage()
        ]
        assert len(first) == 1 and "differs from SDP" in first[0]

    asyncio.run(run())


def test_via_rport_parsing():
    assert sip_client._via_rport("SIP/2.0/UDP a:1;rport=5062;received=10.0.0.5") == 5062
    assert sip_client._via_rport("SIP/2.0/UDP a:1;rport") == 0
    assert sip_client._via_rport("SIP/2.0/UDP a:1;rport=0") == 0
    assert sip_client._via_rport("SIP/2.0/UDP a:1;rport=70000") == 0
    assert (
        sip_client._via_rport("SIP/2.0/UDP a:1;rport=5062, SIP/2.0/UDP b:1;rport=9")
        == 5062
    )


def _nat_client(use_observed: bool, received: str | None, rport: int = 40000):
    client = sip_client.SipClient(
        sip_client.SipConfig(
            server="pbx.example", username="100", use_observed_address=use_observed
        )
    )
    client._schedule_register = lambda *_: None
    client._do_register = lambda: None
    client._reg_cseq = 1
    client._local_ip = "192.168.2.208"
    client._local_port = 5060
    if received:
        via = f"SIP/2.0/UDP 192.168.2.208:5060;rport={rport};received={received}"
        client._handle_register_response(
            sm.parse_sip_message(
                f"SIP/2.0 200 OK\r\nVia: {via}\r\nCSeq: 1 REGISTER\r\n\r\n"
            )
        )
    return client


def test_advertised_address_off_uses_local():

    async def run():
        client = _nat_client(False, "192.168.3.100")
        assert client._contact_uri() == "<sip:100@192.168.2.208:5060>"
        sdp = client._local_sdp()
        assert "o=- " in sdp and "IN IP4 192.168.2.208\r\n" in sdp
        assert "192.168.3.100" not in sdp

    asyncio.run(run())


def test_advertised_address_on_uses_observed():

    async def run():
        client = _nat_client(True, "192.168.3.100")
        assert client._contact_uri() == "<sip:100@192.168.3.100:40000>"
        sdp = client._local_sdp()
        assert "o=- " in sdp and "IN IP4 192.168.3.100\r\n" in sdp
        assert "c=IN IP4 192.168.3.100\r\n" in sdp
        assert "192.168.2.208" not in sdp
        assert f"m=audio {client.config.local_rtp_port} " in sdp
        assert "Contact: <sip:100@192.168.3.100:40000>" in client._build_register()

    asyncio.run(run())


def test_advertised_address_on_falls_back_to_local():

    async def run():
        client = _nat_client(True, None)
        assert client._contact_uri() == "<sip:100@192.168.2.208:5060>"
        assert "c=IN IP4 192.168.2.208\r\n" in client._local_sdp()

    asyncio.run(run())


def test_observed_address_triggers_one_reregister():

    async def run():
        client = _nat_client(True, None)
        calls = []

        def fake_register():
            calls.append(1)
            client._build_register()

        client._do_register = fake_register
        client.state = sip_client.SipState.REGISTERED
        client._reg_contact_sent = client._contact_uri()
        via = "SIP/2.0/UDP 192.168.2.208:5060;rport=40000;received=192.168.3.100"
        resp = sm.parse_sip_message(
            f"SIP/2.0 200 OK\r\nVia: {via}\r\nCSeq: 1 REGISTER\r\n\r\n"
        )
        client._handle_register_response(resp)
        client._handle_register_response(resp)
        assert len(calls) == 1

    asyncio.run(run())


def test_observed_reregister_deferred_during_call():

    async def run():
        client = _nat_client(True, None)
        calls = []
        client._do_register = lambda: calls.append(1)
        client._reg_contact_sent = client._contact_uri()
        client.state = sip_client.SipState.INCOMING
        via = "SIP/2.0/UDP 192.168.2.208:5060;rport=40000;received=192.168.3.100"
        client._handle_register_response(
            sm.parse_sip_message(
                f"SIP/2.0 200 OK\r\nVia: {via}\r\nCSeq: 1 REGISTER\r\n\r\n"
            )
        )
        assert calls == []
        assert client.state == sip_client.SipState.INCOMING
        # The next refresh builds its Contact from the updated address.
        assert "Contact: <sip:100@192.168.3.100:40000>" in client._build_register()

    asyncio.run(run())


def test_observed_address_cleared_when_not_reported_or_socket_reopened():

    async def run():
        client = _nat_client(True, "192.168.3.100")
        assert client._contact_uri() == "<sip:100@192.168.3.100:40000>"
        # Network change: the server now sees the sent-by address (no received).
        client._handle_register_response(
            sm.parse_sip_message(
                "SIP/2.0 200 OK\r\nVia: SIP/2.0/UDP 192.168.2.208:5060\r\n"
                "CSeq: 1 REGISTER\r\n\r\n"
            )
        )
        assert client._observed_ip == "" and client._observed_port == 0
        assert client._contact_uri() == "<sip:100@192.168.2.208:5060>"
        # A new socket forgets everything previously observed.
        client._observed_ip = "192.168.3.100"
        client._observed_port = 40000
        client._warned_observed_ip = "192.168.3.100"
        client.config.server = "invalid.invalid"
        # DNS is the network boundary: the lookup fails like NXDOMAIN would.
        with patch.object(
            client._loop, "getaddrinfo", AsyncMock(side_effect=OSError("NXDOMAIN"))
        ):
            assert await client._open_socket() is False
        assert client._observed_ip == "" and client._observed_port == 0
        assert client._warned_observed_ip == ""

    asyncio.run(run())
