"""Registration recovery tests against the UDP mock registrar (P0-4).

Timers are fired explicitly so the suite stays well under a second of
wall-clock wait. The harness also covers an in-dialog re-INVITE so P0-1
can reuse the real socket path.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from mock_pbx import MockPbx
from test_pure import _load_component_module, sip_client, sm

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
    """403 retries with backoff and does not reconnect the socket."""
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
    assert delays[-1] == sip_client.REGISTER_RETRY_MIN
    assert reconnects == []


def _reg_response(code: int, reason: str, cseq: int = 1):
    return sm.parse_sip_message(
        f"SIP/2.0 {code} {reason}\r\nCSeq: {cseq} REGISTER\r\n\r\n"
    )


def test_register_403_exponential_backoff_caps_and_resets():
    """Consecutive 403s grow ×3 to 30 min; a 200 OK resets the interval."""
    if _skip():
        return

    async def run():
        delays: list[float] = []
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = delays.append
        client._reg_cseq = 1
        expected: list[float] = []
        delay = sip_client.REGISTER_RETRY_MIN
        for _ in range(8):
            expected.append(delay)
            client._handle_register_response(_reg_response(403, "Forbidden"))
            delay = min(delay * 3, sip_client.REGISTER_AUTH_RETRY_MAX)
        after_fail = (
            list(delays),
            client.register_auth_failures,
            client._register_backoff,
        )
        client._handle_register_response(_reg_response(200, "OK"))
        return after_fail + (
            expected,
            client._register_backoff,
            client.register_auth_failures,
            delays[-1],
        )

    (
        fail_delays,
        auth_fails,
        next_backoff,
        expected,
        reset_backoff,
        reset_fails,
        last_delay,
    ) = asyncio.run(run())
    assert fail_delays == expected
    assert fail_delays[0] == sip_client.REGISTER_RETRY_MIN
    assert fail_delays[-1] == sip_client.REGISTER_AUTH_RETRY_MAX
    assert auth_fails == 8
    assert next_backoff == sip_client.REGISTER_AUTH_RETRY_MAX
    assert reset_backoff == sip_client.REGISTER_RETRY_MIN
    assert reset_fails == 0
    assert last_delay == max(300 // 2, 30)


def test_register_500_backoff_is_slower_than_auth():
    if _skip():
        return

    async def run():
        delays: list[float] = []
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._schedule_register = delays.append
        client._reg_cseq = 1
        for _ in range(8):
            client._handle_register_response(_reg_response(500, "Server Error"))
        return delays, client.register_auth_failures, client._register_backoff

    delays, auth_fails, backoff = asyncio.run(run())
    assert delays[0] == sip_client.REGISTER_RETRY_MIN
    assert delays[1] == 20
    assert delays[-1] == sip_client.REGISTER_RETRY_MAX
    assert auth_fails == 0
    assert backoff == sip_client.REGISTER_RETRY_MAX


def test_register_401_challenge_does_not_count_as_failure():
    """First 401/407 is a digest challenge, not a failed register."""
    if _skip():
        return

    async def run():
        sent: list[str] = []
        delays: list[float] = []
        failed: list[str] = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example", password="secret"),
            sip_client.SipCallbacks(on_register_failed=failed.append),
        )
        client._send_raw = sent.append
        client._schedule_register = delays.append
        client._reg_cseq = 1
        client._handle_register_response(
            sm.parse_sip_message(
                "SIP/2.0 401 Unauthorized\r\n"
                "CSeq: 1 REGISTER\r\n"
                'WWW-Authenticate: Digest realm="ex", nonce="n", algorithm=MD5\r\n'
                "\r\n"
            )
        )
        after_challenge = (list(failed), list(delays), client.register_auth_failures)
        client._handle_register_response(
            _reg_response(401, "Unauthorized", client._reg_cseq)
        )
        return after_challenge + (list(failed), list(delays), client.register_auth_failures)

    (
        failed1,
        delays1,
        auth1,
        failed2,
        delays2,
        auth2,
    ) = asyncio.run(run())
    assert failed1 == []
    assert delays1 == []
    assert auth1 == 0
    assert failed2 == ["401 Unauthorized"]
    assert delays2 == [sip_client.REGISTER_RETRY_MIN]
    assert auth2 == 1


def test_auth_repair_opens_after_three_failures():
    repairs = _load_component_module("repairs")
    assert repairs.is_register_auth_failure("403 Forbidden") is True
    assert repairs.is_register_auth_failure("401 Unauthorized") is True
    assert repairs.is_register_auth_failure("407 Proxy Authentication Required") is True
    assert repairs.is_register_auth_failure("500 Server Error") is False
    assert repairs.is_register_auth_failure("Connection failed") is False
    assert repairs.should_open_auth_repair(2) is False
    assert repairs.should_open_auth_repair(3) is True
    assert repairs.register_auth_issue_id("abc") == "register_auth_failed_abc"


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
