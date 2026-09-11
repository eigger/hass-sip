"""Opt-in SIP/RTP trace: credential masking and no-cost-when-disabled (P1-1)."""
from __future__ import annotations

import asyncio
import logging
import struct
from unittest.mock import AsyncMock, MagicMock, patch

from test_pure import rtp_session, sip_client, trace

_DIGEST = (
    "REGISTER sip:pbx.example SIP/2.0\r\n"
    "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK1\r\n"
    "From: <sip:100@pbx.example>;tag=local\r\n"
    "To: <sip:100@pbx.example>\r\n"
    "Call-ID: reg@host\r\n"
    "CSeq: 2 REGISTER\r\n"
    'Authorization: Digest username="100", realm="asterisk", '
    'nonce="abc123nonce", uri="sip:pbx.example", '
    'response="6629fae49393a05397450978507c4ef1", algorithm=MD5, '
    'qop=auth, nc=00000001, cnonce="0a4f113b"\r\n'
    "Content-Length: 0\r\n"
    "\r\n"
)

_INVITE = (
    "INVITE sip:200@pbx.example SIP/2.0\r\n"
    "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKinv\r\n"
    "From: <sip:100@pbx.example>;tag=out\r\n"
    "To: <sip:200@pbx.example>\r\n"
    "Call-ID: call@host\r\n"
    "CSeq: 1 INVITE\r\n"
    "Content-Type: application/sdp\r\n"
    "Content-Length: 0\r\n"
    "\r\n"
)

_OK_REGISTER = (
    "SIP/2.0 200 OK\r\n"
    "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK1\r\n"
    "From: <sip:100@pbx.example>;tag=local\r\n"
    "To: <sip:100@pbx.example>;tag=pbx\r\n"
    "Call-ID: reg@host\r\n"
    "CSeq: 1 REGISTER\r\n"
    "Content-Length: 0\r\n"
    "\r\n"
)

_OK_INVITE = (
    "SIP/2.0 200 OK\r\n"
    "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKinv\r\n"
    "From: <sip:100@pbx.example>;tag=out\r\n"
    "To: <sip:200@pbx.example>;tag=pbx\r\n"
    "Call-ID: call@host\r\n"
    "CSeq: 1 INVITE\r\n"
    "Content-Length: 0\r\n"
    "\r\n"
)


def test_trace_disabled_by_default():
    assert not trace.enabled()
    assert not trace._LOGGER.isEnabledFor(logging.DEBUG)


def test_mask_sip_authorization_response():
    secret = "6629fae49393a05397450978507c4ef1"
    out = trace.mask_sip(_DIGEST)
    assert secret not in out
    assert 'response="****"' in out
    assert "username=\"100\"" in out
    assert "REGISTER sip:pbx.example" in out


def test_mask_sip_nonce_and_cnonce():
    out = trace.mask_sip(_DIGEST)
    assert "abc123nonce" not in out
    assert "0a4f113b" not in out
    assert 'nonce="****"' in out
    assert 'cnonce="****"' in out


def test_mask_sip_proxy_authorization():
    secret = "aabbccddeeff00112233445566778899"
    msg = (
        "INVITE sip:200@pbx SIP/2.0\r\n"
        f'Proxy-Authorization: Digest username="100", nonce="n1", '
        f'response="{secret}", cnonce="cn1"\r\n'
        "\r\n"
    )
    out = trace.mask_sip(msg)
    assert secret not in out
    assert "n1" not in out
    assert "cn1" not in out
    assert 'response="****"' in out
    assert "Proxy-Authorization:" in out


def test_mask_sip_unquoted_digest_params():
    msg = (
        "Authorization: Digest username=100, nonce=plainnonce, "
        "response=deadbeefcafebabe, cnonce=plaincnonce\r\n"
    )
    out = trace.mask_sip(msg)
    assert "deadbeefcafebabe" not in out
    assert "plainnonce" not in out
    assert "plaincnonce" not in out
    assert 'response="****"' in out


def test_mask_sip_leaves_www_authenticate():
    nonce = "server-challenge-nonce"
    msg = (
        "SIP/2.0 401 Unauthorized\r\n"
        f'WWW-Authenticate: Digest realm="asterisk", nonce="{nonce}"\r\n'
        "\r\n"
    )
    out = trace.mask_sip(msg)
    assert nonce in out
    assert "WWW-Authenticate:" in out


def test_mask_sip_basic_authorization():
    msg = "Authorization: Basic dXNlcjpwYXNzd29yZA==\r\n"
    out = trace.mask_sip(msg)
    assert "dXNlcjpwYXNzd29yZA==" not in out
    assert "Authorization: Basic ****" in out


def test_mask_sip_password_param():
    msg = 'Authorization: Digest username="u", password="s3cret", response="ab"\r\n'
    out = trace.mask_sip(msg)
    assert "s3cret" not in out
    assert 'password="****"' in out


def test_log_sip_skips_masking_when_disabled():
    with patch.object(trace, "mask_sip", wraps=trace.mask_sip) as mask:
        with patch.object(trace._LOGGER, "debug") as debug:
            trace.log_sip("TX", _DIGEST)
    assert mask.call_count == 0
    assert debug.call_count == 0


def test_log_sip_masks_before_debug():
    captured = []

    def fake_debug(fmt, *args):
        captured.append(fmt % args if args else fmt)

    with patch.object(trace._LOGGER, "isEnabledFor", return_value=True):
        with patch.object(trace._LOGGER, "debug", side_effect=fake_debug):
            trace.log_sip("TX", _DIGEST)
    text = "\n".join(captured)
    assert text.startswith("TX\n")
    assert "6629fae49393a05397450978507c4ef1" not in text
    assert "REGISTER" in text


def test_sip_send_and_recv_traced_when_enabled():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._transport = MagicMock()
        captured = []

        def fake_debug(fmt, *args):
            captured.append(fmt % args if args else fmt)

        with patch.object(trace._LOGGER, "isEnabledFor", return_value=True):
            with patch.object(trace._LOGGER, "debug", side_effect=fake_debug):
                client._send_raw(_DIGEST)
                client._send_raw(_INVITE)
                client._on_packet(_OK_REGISTER.encode("utf-8"))
                client._on_packet(_OK_INVITE.encode("utf-8"))
        return captured, client._transport.sendto.call_count

    logs, sent = asyncio.run(run())
    text = "\n".join(logs)
    assert sent == 2
    assert "TX\nREGISTER" in text
    assert "TX\nINVITE" in text
    assert "RX\nSIP/2.0 200 OK" in text
    assert "CSeq: 1 REGISTER" in text
    assert "CSeq: 1 INVITE" in text
    assert "6629fae49393a05397450978507c4ef1" not in text
    assert 'response="****"' in text


def test_sip_send_raw_does_not_format_when_disabled():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._transport = MagicMock()
        with patch.object(trace, "mask_sip", wraps=trace.mask_sip) as mask:
            with patch.object(trace._LOGGER, "debug") as debug:
                client._send_raw(_DIGEST)
                client._on_packet(_OK_REGISTER.encode("utf-8"))
        return mask.call_count, debug.call_count, client._transport.sendto.call_count

    masks, debugs, sent = asyncio.run(run())
    assert sent == 1
    assert masks == 0
    assert debugs == 0


def test_rtp_trace_summary_counts_loss_and_latch():
    async def run():
        session = rtp_session.RtpSession()
        session.set_remote("192.168.1.10", 10000)
        session._transport = MagicMock()
        pkt = bytes([0x80, 0]) + struct.pack("!HII", 1, 100, 0x1234) + b"\xff" * 160
        session._send(pkt)
        for seq in (1, 2, 4, 5):
            session._receive_impl(
                bytes([0x80, 0]) + struct.pack("!HII", seq, 100, 0x1234) + b"\xff" * 160,
                ("203.0.113.8", 20000),
            )
        captured = []

        def fake_debug(fmt, *args):
            captured.append(fmt % args if args else fmt)

        with patch.object(trace._LOGGER, "isEnabledFor", return_value=True):
            with patch.object(trace._LOGGER, "debug", side_effect=fake_debug):
                session._emit_rtp_trace()
        return captured, session._trace_rx, session.latched_remote

    logs, after_reset, latched = asyncio.run(run())
    assert len(logs) == 1
    line = logs[0]
    assert "rx=4" in line
    assert "tx=1" in line
    assert "lost~1" in line
    assert "latch=203.0.113.8:20000" in line
    assert "sdp=192.168.1.10:10000" in line
    assert after_reset == 0
    assert latched == ("203.0.113.8", 20000)


def test_rtp_trace_summary_silent_when_disabled():
    async def run():
        session = rtp_session.RtpSession()
        session._transport = MagicMock()
        session._remote = ("192.0.2.1", 5004)
        session._send(b"\x80\x00" + b"\x00" * 10)
        with patch.object(trace._LOGGER, "debug") as debug:
            session._emit_rtp_trace()
        return debug.call_count, session._trace_tx

    debugs, tx = asyncio.run(run())
    assert debugs == 0
    assert tx == 1


def test_rtp_trace_timer_cancelled_on_stop():
    async def run():
        session = rtp_session.RtpSession()
        transport = MagicMock()
        with patch.object(
            session._loop,
            "create_datagram_endpoint",
            new=AsyncMock(return_value=(transport, MagicMock())),
        ):
            assert await session.start(4000)
        armed = session._trace_handle is not None
        await session.stop()
        return armed, session._trace_handle

    armed, after = asyncio.run(run())
    assert armed is True
    assert after is None
