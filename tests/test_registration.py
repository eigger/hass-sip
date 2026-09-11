"""Registration recovery tests against the UDP mock registrar (P0-4).

Timers are fired explicitly so the suite stays well under a second of
wall-clock wait. The harness also covers an in-dialog re-INVITE so P0-1
can reuse the real socket path.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from mock_pbx import MockPbx
from test_pure import sip_client

if sip_client is None:  # enum.StrEnum needs Python 3.11+
    def _skip():
        return True
else:
    def _skip():
        return False


def fire_register_timer(client) -> None:
    """Cancel the pending registration timer and run it now."""
    handle = client._register_handle
    if handle is not None:
        handle.cancel()
        client._register_handle = None
    client._register_timer()


async def _make_client(pbx, **cfg_extra):
    registered = []
    failed = []
    config = sip_client.SipConfig(
        server="127.0.0.1",
        port=pbx.port,
        username=pbx.username,
        password=pbx.password,
        domain=pbx.domain,
        register_expiration=cfg_extra.pop("register_expiration", 120),
        local_rtp_port=cfg_extra.pop("local_rtp_port", 17078),
        **cfg_extra,
    )
    client = sip_client.SipClient(
        config,
        sip_client.SipCallbacks(
            on_registered=lambda: registered.append(True),
            on_register_failed=failed.append,
        ),
    )
    return client, registered, failed


async def _stop(client, pbx) -> None:
    try:
        await client.stop()
    finally:
        await pbx.stop()


def test_register_401_then_success_emits_once():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        await pbx.start()
        client, registered, failed = await _make_client(pbx)
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            authed = [m for m in pbx.registers if m.header("Authorization")]
            return (
                list(registered),
                list(failed),
                client.registered,
                client.state,
                len(pbx.registers),
                len(authed),
            )
        finally:
            await _stop(client, pbx)

    registered, failed, ok, state, nreg, nauthed = asyncio.run(run())
    assert registered == [True]
    assert failed == []
    assert ok is True
    assert state == sip_client.SipState.REGISTERED
    assert nreg >= 2
    assert nauthed == 1


def test_register_407_proxy_auth():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        pbx.challenge = "proxy"
        await pbx.start()
        client, registered, failed = await _make_client(pbx)
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            authed = [m for m in pbx.registers if m.header("Proxy-Authorization")]
            www = [m for m in pbx.registers if m.header("Authorization")]
            return list(registered), list(failed), len(authed), len(www)
        finally:
            await _stop(client, pbx)

    registered, failed, nauthed, nwww = asyncio.run(run())
    assert registered == [True]
    assert failed == []
    assert nauthed == 1
    assert nwww == 0


def test_register_423_raises_expires():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        pbx.min_expires = 600
        await pbx.start()
        client, registered, failed = await _make_client(pbx, register_expiration=60)
        try:
            await client.start()
            for _ in range(30):
                if registered:
                    break
                await asyncio.sleep(0.02)
            expires = [m.header("Expires") for m in pbx.registers]
            return (
                list(registered),
                list(failed),
                client.config.register_expiration,
                expires,
            )
        finally:
            await _stop(client, pbx)

    registered, failed, exp, expires = asyncio.run(run())
    assert registered == [True]
    assert failed == []
    assert exp == 600
    assert "60" in expires
    assert "600" in expires


def test_register_unanswered_reconnects():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        pbx.drop = True
        await pbx.start()
        client, registered, failed = await _make_client(pbx)
        reconnects = []
        orig = client._reconnect

        async def wrapped():
            reconnects.append(True)
            pbx.drop = False
            await orig()

        client._reconnect = wrapped
        try:
            await client.start()
            await asyncio.sleep(0.05)
            fire_register_timer(client)  # attempt 1
            await asyncio.sleep(0.02)
            fire_register_timer(client)  # attempt 2
            await asyncio.sleep(0.02)
            fire_register_timer(client)  # attempt 3 → reconnect
            for _ in range(30):
                if registered:
                    break
                await asyncio.sleep(0.02)
            return list(reconnects), list(registered), client.registered
        finally:
            await _stop(client, pbx)

    reconnects, registered, ok = asyncio.run(run())
    assert reconnects == [True]
    assert registered == [True]
    assert ok is True


def test_register_refresh_at_half_expiration():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        await pbx.start()
        delays = []
        client, registered, _failed = await _make_client(pbx, register_expiration=120)
        real = client._schedule_register

        def spy(seconds):
            delays.append(seconds)
            real(seconds)

        client._schedule_register = spy
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            return list(registered), delays
        finally:
            await _stop(client, pbx)

    registered, delays = asyncio.run(run())
    assert registered == [True]
    # 200 OK schedules a refresh at expiration/2 (floored at 30s).
    assert delays[-1] == 60


def test_register_deferred_during_call():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        await pbx.start()
        client, registered, _failed = await _make_client(pbx)
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            before = len(pbx.registers)
            client.state = sip_client.SipState.IN_CALL
            delays = []
            real = client._schedule_register

            def spy(seconds):
                delays.append(seconds)
                real(3600)

            client._schedule_register = spy
            client._register_tick()
            during = len(pbx.registers)
            client.state = sip_client.SipState.REGISTERED
            client._register_tick()
            await asyncio.sleep(0.05)
            return before, during, len(pbx.registers), delays
        finally:
            await _stop(client, pbx)

    before, during, after, delays = asyncio.run(run())
    assert during == before
    assert delays[0] == 30
    assert after > before


def test_register_recovers_after_pbx_restart():
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        await pbx.start()
        client, registered, _failed = await _make_client(pbx)
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            first = list(registered)
            registered.clear()
            await pbx.stop()
            fire_register_timer(client)  # REGISTERED refresh → REGISTERING
            await asyncio.sleep(0.02)
            fire_register_timer(client)  # unanswered 1
            await asyncio.sleep(0.02)
            fire_register_timer(client)  # unanswered 2
            await asyncio.sleep(0.02)
            await pbx.start(pbx.host, pbx.port)
            fire_register_timer(client)  # unanswered 3 → reconnect
            for _ in range(40):
                if registered:
                    break
                await asyncio.sleep(0.02)
            return first, list(registered), client.registered
        finally:
            await _stop(client, pbx)

    first, recovered, ok = asyncio.run(run())
    assert first == [True]
    assert recovered == [True]
    assert ok is True


def test_register_403_retries_without_reconnect():
    """403 currently retries at 10s without reconnect; exponential backoff is P2-4."""
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        pbx.require_auth = False
        pbx.force_code = 403
        await pbx.start()
        delays = []
        reconnects = []
        client, registered, failed = await _make_client(pbx)
        orig_sched = client._schedule_register

        def spy(seconds):
            delays.append(seconds)
            orig_sched(3600)

        async def no_reconnect():
            reconnects.append(True)

        client._schedule_register = spy
        client._reconnect = no_reconnect
        try:
            await client.start()
            for _ in range(20):
                if failed or registered:
                    break
                await asyncio.sleep(0.02)
            return list(registered), list(failed), delays, reconnects
        finally:
            await _stop(client, pbx)

    registered, failed, delays, reconnects = asyncio.run(run())
    assert registered == []
    assert failed and failed[0].startswith("403")
    assert delays[-1] == 10
    assert reconnects == []


def test_harness_reinvite_answers_with_new_transaction():
    """Mock PBX UAS: outbound call then in-dialog re-INVITE (P0-1 reuse)."""
    if _skip():
        return

    async def run():
        pbx = MockPbx()
        pbx.require_auth = False
        await pbx.start()
        client, registered, _failed = await _make_client(pbx)
        try:
            await client.start()
            for _ in range(20):
                if registered:
                    break
                await asyncio.sleep(0.02)
            with patch.object(client, "_start_media", new_callable=AsyncMock):
                client.call("2000")
                for _ in range(30):
                    if client.state == sip_client.SipState.IN_CALL:
                        break
                    await asyncio.sleep(0.02)
                connected = client.state
                call_id = client._d_call_id
                local = client._d_local
                remote = client._d_remote
                sent = []
                orig_send = client._send_raw

                def capture(msg):
                    sent.append(msg)
                    orig_send(msg)

                client._send_raw = capture
                pbx.send_reinvite(
                    call_id=call_id,
                    frm=remote,
                    to=local,
                    cseq=2,
                )
                await asyncio.sleep(0.05)
            return connected, sent, client.state
        finally:
            await _stop(client, pbx)

    connected, sent, state = asyncio.run(run())
    assert connected == sip_client.SipState.IN_CALL
    assert state == sip_client.SipState.IN_CALL
    replies = [s for s in sent if s.startswith("SIP/2.0 200 OK")]
    assert replies
    assert "CSeq: 2 INVITE\r\n" in replies[-1]
    assert "Content-Type: application/sdp" in replies[-1]
