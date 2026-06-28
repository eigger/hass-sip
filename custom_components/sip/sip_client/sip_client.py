"""Asyncio SIP user-agent (registrar client) — port of sip_client.cpp.

Framework-agnostic: it talks UDP and exposes call-control methods plus a set of
callback hooks. The Home Assistant layer wires those hooks to entities, events
and services. No knowledge of Home Assistant lives here.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Callable

from . import sip_message as sm
from .audio import AudioSink, AudioSource, NullSink
from .rtp_session import RtpSession
from .sip_auth import digest_response

_LOGGER = logging.getLogger(__name__)
USER_AGENT = "HomeAssistant-sip_client"


class SipState(enum.StrEnum):
    IDLE = "idle"
    REGISTERING = "registering"
    REGISTERED = "registered"
    INVITING = "inviting"
    RINGING_OUT = "ringing_out"
    INCOMING = "incoming"
    ANSWERING = "answering"
    IN_CALL = "in_call"


@dataclass
class SipConfig:
    server: str
    port: int = 5060
    username: str = ""
    password: str = ""
    domain: str = ""
    caller_id: str = ""
    register_expiration: int = 300
    local_rtp_port: int = 7078


@dataclass
class SipCallbacks:
    on_state_change: Callable[[SipState], None] | None = None
    on_registered: Callable[[], None] | None = None
    on_register_failed: Callable[[str], None] | None = None
    on_incoming_call: Callable[[str], None] | None = None
    on_call_connected: Callable[[], None] | None = None
    on_call_ended: Callable[[], None] | None = None
    on_dtmf: Callable[[str], None] | None = None
    on_playback_done: Callable[[], None] | None = None


def _choose_payload(sdp: sm.SdpInfo) -> int:
    if sdp.pcmu_pt >= 0:
        return sdp.pcmu_pt
    if sdp.pcma_pt >= 0:
        return sdp.pcma_pt
    return 0


def _angle_uri(value: str) -> str:
    lt = value.find("<")
    gt = value.find(">")
    if lt != -1 and gt != -1 and gt > lt:
        return value[lt + 1:gt]
    # No angle brackets: treat the whole value as a bare URI (RFC 3261 §20.10)
    stripped = value.strip()
    return stripped if stripped.startswith("sip") else ""


class _SipProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_packet(data)

    def error_received(self, exc) -> None:
        _LOGGER.debug("SIP socket error: %s", exc)


class SipClient:
    def __init__(self, config: SipConfig, callbacks: SipCallbacks | None = None) -> None:
        self.config = config
        if not self.config.domain:
            self.config.domain = config.server
        self.cb = callbacks or SipCallbacks()

        self._loop = asyncio.get_running_loop()
        self._transport: asyncio.DatagramTransport | None = None
        self._local_ip = ""
        self._local_port = 0
        self._closing = False

        self.state = SipState.IDLE
        self.registered = False
        self.last_caller = ""
        self._register_handle: asyncio.TimerHandle | None = None
        self._reg_attempts = 0

        # registration transaction
        self._reg_call_id = ""
        self._reg_tag = ""
        self._reg_branch = ""
        self._reg_cseq = 0
        self._register_auth_tried = False

        # current dialog
        self._d_call_id = ""
        self._d_local = ""
        self._d_remote = ""
        self._d_remote_target = ""
        self._d_local_tag = ""
        self._d_branch = ""
        self._d_cseq = 0
        self._outbound = False
        self._invite_auth_tried = False
        self._incoming_invite: sm.SipMessage | None = None

        # negotiated media
        self._remote_rtp_ip = ""
        self._remote_rtp_port = 0
        self._chosen_pt = 0
        self._remote_dtmf_pt = -1
        self._media_active = False

        self.rtp = RtpSession()
        self.sink: AudioSink = NullSink()
        self._tx_source_task: asyncio.Task | None = None
        self._pending_source: AudioSource | None = None
        self._ring_timeout_handle: asyncio.TimerHandle | None = None
        # INVITE retransmission (RFC 3261 over unreliable UDP)
        self._invite_msg: str | None = None
        self._invite_retx_handle: asyncio.TimerHandle | None = None
        self._invite_retx_count = 0
        self.dnd = False
        self.auto_answer_checker: Callable[[str], bool] | None = None

    # ------------------------------------------------------------------
    @property
    def in_call(self) -> bool:
        return self.state == SipState.IN_CALL

    @property
    def media_playing(self) -> bool:
        return self._tx_source_task is not None and not self._tx_source_task.done()

    def set_sink(self, sink: AudioSink) -> None:
        self.sink = sink

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        self._closing = False
        if await self._open_socket():
            self._do_register()
        else:
            self._emit("on_register_failed", "Connection failed")
            self._schedule_register(10)  # keep retrying; _register_timer recovers

    async def stop(self) -> None:
        self._closing = True
        if self._register_handle is not None:
            self._register_handle.cancel()
            self._register_handle = None
        self._cancel_invite_retx()
        if self._ring_timeout_handle is not None:
            self._ring_timeout_handle.cancel()
            self._ring_timeout_handle = None
        await self._stop_media()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._set_state(SipState.IDLE)

    async def _reconnect(self) -> None:
        """Rebuild the SIP socket and re-register (recovers from network loss)."""
        if self._closing:
            return
        try:
            _LOGGER.info("Reconnecting SIP transport to %s", self.config.server)
            if self._register_handle is not None:
                self._register_handle.cancel()
                self._register_handle = None
            self.registered = False
            if self._transport is not None:
                self._transport.close()
                self._transport = None
            self._set_state(SipState.IDLE)
            if await self._open_socket():
                self._reg_attempts = 0
                self._do_register()
            else:
                self._schedule_register(10)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Reconnect failed; will retry")
            self._schedule_register(10)

    async def _open_socket(self) -> bool:
        # Resolve via the loop so a slow DNS lookup never blocks the event loop.
        try:
            infos = await self._loop.getaddrinfo(
                self.config.server,
                self.config.port,
                family=socket.AF_INET,
                type=socket.SOCK_DGRAM,
            )
        except OSError as err:
            _LOGGER.warning("Cannot resolve SIP server '%s': %s", self.config.server, err)
            return False
        if not infos:
            _LOGGER.warning("No address found for SIP server '%s'", self.config.server)
            return False
        server_addr = infos[0][4]

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(server_addr)
            self._local_ip = probe.getsockname()[0]
        except OSError:
            self._local_ip = "0.0.0.0"
        finally:
            probe.close()

        try:
            transport, _ = await self._loop.create_datagram_endpoint(
                lambda: _SipProtocol(self._on_packet),
                remote_addr=server_addr,
            )
        except OSError as err:
            _LOGGER.warning("SIP connect failed: %s", err)
            return False
        self._transport = transport
        self._local_port = transport.get_extra_info("sockname")[1]
        _LOGGER.info("SIP socket bound, local %s:%s", self._local_ip, self._local_port)
        return True

    def _send_raw(self, msg: str) -> None:
        if self._transport is None:
            return
        self._transport.sendto(msg.encode("utf-8"))
        _LOGGER.log(5, "TX >>>\n%s", msg)

    def _emit(self, name: str, *args) -> None:
        """Invoke a user callback, never letting its failure break SIP logic."""
        cb = getattr(self.cb, name, None)
        if cb is None:
            return
        try:
            cb(*args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("SIP callback %s raised", name)

    def _set_state(self, state: SipState) -> None:
        if self.state != state:
            _LOGGER.debug("state %s -> %s", self.state, state)
            self.state = state
            self._emit("on_state_change", state)

    # -- registration ---------------------------------------------------
    def _contact_uri(self) -> str:
        return f"<sip:{self.config.username}@{self._local_ip}:{self._local_port}>"

    def _build_register(self) -> str:
        cfg = self.config
        aor = f"sip:{cfg.username}@{cfg.domain}"
        reg_uri = f"sip:{cfg.domain}"
        disp = cfg.caller_id or cfg.username
        return (
            f"REGISTER {reg_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={self._reg_branch};rport\r\n"
            "Max-Forwards: 70\r\n"
            f'From: "{disp}" <{aor}>;tag={self._reg_tag}\r\n'
            f"To: <{aor}>\r\n"
            f"Call-ID: {self._reg_call_id}\r\n"
            f"CSeq: {self._reg_cseq} REGISTER\r\n"
            f"Contact: {self._contact_uri()}\r\n"
            f"Expires: {cfg.register_expiration}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            "Content-Length: 0\r\n\r\n"
        )

    def _do_register(self) -> None:
        self._reg_call_id = sm.gen_call_id(self._local_ip)
        self._reg_tag = sm.gen_tag()
        self._reg_branch = sm.gen_branch()
        self._reg_cseq += 1
        self._register_auth_tried = False
        self._send_raw(self._build_register())
        self._set_state(SipState.REGISTERING)
        self._schedule_register(5)  # retry window if no response

    def _schedule_register(self, seconds: float) -> None:
        if self._register_handle is not None:
            self._register_handle.cancel()
        self._register_handle = self._loop.call_later(seconds, self._register_timer)

    def _register_timer(self) -> None:
        self._register_handle = None
        if self._closing:
            return
        # Guarantee the registration loop keeps ticking even if a tick errors,
        # so the component can never get permanently stuck unregistered.
        try:
            self._register_tick()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Registration tick failed; rescheduling")
            self._schedule_register(10)

    def _register_tick(self) -> None:
        # Don't disturb an active call; defer the refresh.
        if self.state in (
            SipState.IN_CALL,
            SipState.ANSWERING,
            SipState.INCOMING,
            SipState.INVITING,
            SipState.RINGING_OUT,
        ):
            self._schedule_register(30)
            return
        if self.state == SipState.REGISTERED:
            self._do_register()  # periodic refresh
        elif self.state == SipState.REGISTERING:
            # No response in the window. Resend a few times, then rebuild the
            # socket to recover from a dead transport or a changed local IP.
            self._reg_attempts += 1
            if self._reg_attempts >= 3:
                _LOGGER.warning("REGISTER unanswered; reconnecting socket")
                self._reg_attempts = 0
                self._loop.create_task(self._reconnect())
            else:
                self._do_register()
        else:  # IDLE: the socket is likely gone, rebuild it
            self._loop.create_task(self._reconnect())

    def _handle_register_response(self, m: sm.SipMessage) -> None:
        try:
            cseq_num = int(m.header("CSeq").split()[0])
        except (ValueError, IndexError):
            cseq_num = 0

        if cseq_num != self._reg_cseq:
            _LOGGER.debug("Ignoring REGISTER response for old CSeq %s", cseq_num)
            return

        if m.status_code in (401, 407) and not self._register_auth_tried:
            self._register_auth_tried = True
            self._send_raw(self._authorized_register(m))
            return
        if 200 <= m.status_code < 300:
            was = self.registered
            self.registered = True
            self._reg_attempts = 0
            self._set_state(SipState.REGISTERED)
            self._schedule_register(max(self.config.register_expiration // 2, 30))
            if not was:
                _LOGGER.info("Registered with %s", self.config.server)
                self._emit("on_registered")
            return
        _LOGGER.warning("REGISTER failed: %s %s", m.status_code, m.reason)
        self.registered = False
        # The server responded, so the socket is alive: gentle retry, no reconnect.
        self._reg_attempts = 0
        self._emit("on_register_failed", f"{m.status_code} {m.reason}")
        self._schedule_register(10)

    def _authorized_register(self, m: sm.SipMessage) -> str:
        proxy = m.status_code == 407
        ch = m.header("Proxy-Authenticate" if proxy else "WWW-Authenticate")
        realm = sm.auth_param(ch, "realm")
        nonce = sm.auth_param(ch, "nonce")
        qop = sm.auth_param(ch, "qop")
        opaque = sm.auth_param(ch, "opaque")
        uri = f"sip:{self.config.domain}"
        nc = "00000001"
        cnonce = sm.gen_random_hex(8)
        resp = digest_response(
            self.config.username, self.config.password, realm, "REGISTER", uri,
            nonce, "auth" if qop else "", nc, cnonce,
        )
        self._reg_cseq += 1
        self._reg_branch = sm.gen_branch()
        msg = self._build_register()
        auth = self._digest_auth_line(proxy, realm, nonce, uri, resp, qop, nc, cnonce, opaque)
        return msg.replace("Content-Length:", auth + "Content-Length:", 1)

    # -- outbound call --------------------------------------------------
    def call(
        self,
        number: str,
        on_connect_source: AudioSource | None = None,
        ring_timeout: int | None = None,
    ) -> None:
        if self.state != SipState.REGISTERED:
            _LOGGER.warning("Cannot call in state %s", self.state)
            return
        self._pending_source = on_connect_source
        self._outbound = True
        self._invite_auth_tried = False
        self._d_call_id = sm.gen_call_id(self._local_ip)
        self._d_local_tag = sm.gen_tag()
        self._d_branch = sm.gen_branch()
        self._d_cseq = 1
        self._invite_number = number
        disp = self.config.caller_id or self.config.username
        self._d_local = (
            f'"{disp}" <sip:{self.config.username}@{self.config.domain}>;tag={self._d_local_tag}'
        )
        self._d_remote = f"<sip:{number}@{self.config.domain}>"
        self._d_remote_target = f"sip:{number}@{self.config.domain}"
        self._invite_msg = self._build_invite()
        self._send_raw(self._invite_msg)
        self._set_state(SipState.INVITING)
        _LOGGER.info("Calling %s", number)
        self._start_invite_retx()
        if ring_timeout:
            self._ring_timeout_handle = self._loop.call_later(
                ring_timeout, self._handle_ring_timeout
            )

    # -- INVITE retransmission (UDP reliability) ------------------------
    def _start_invite_retx(self) -> None:
        self._invite_retx_count = 0
        self._schedule_invite_retx(0.5)

    def _schedule_invite_retx(self, seconds: float) -> None:
        self._cancel_invite_retx()
        self._invite_retx_handle = self._loop.call_later(seconds, self._invite_retx_timer)

    def _cancel_invite_retx(self) -> None:
        if self._invite_retx_handle is not None:
            self._invite_retx_handle.cancel()
            self._invite_retx_handle = None

    def _invite_retx_timer(self) -> None:
        self._invite_retx_handle = None
        try:
            if self.state != SipState.INVITING or self._invite_msg is None:
                return  # got a response or moved on
            if self._invite_retx_count >= 6:
                return  # give up; ring_timeout / failure handling takes over
            self._invite_retx_count += 1
            self._send_raw(self._invite_msg)
            # RFC 3261 T1 exponential backoff, capped at 4 s.
            self._schedule_invite_retx(min(0.5 * (2 ** self._invite_retx_count), 4.0))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("INVITE retransmit error")

    def _handle_ring_timeout(self) -> None:
        self._ring_timeout_handle = None
        try:
            if self.state in (SipState.INVITING, SipState.RINGING_OUT):
                _LOGGER.info("Ring timeout reached; canceling call")
                self.hangup()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Ring timeout handler error")

    def _local_sdp(self) -> str:
        sid = str(int(time.time()))
        return (
            "v=0\r\n"
            f"o=- {sid} {sid} IN IP4 {self._local_ip}\r\n"
            "s=homeassistant\r\n"
            f"c=IN IP4 {self._local_ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.config.local_rtp_port} RTP/AVP 0 8 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=fmtp:101 0-15\r\n"
            "a=ptime:20\r\n"
            "a=sendrecv\r\n"
        )

    def _build_invite(self) -> str:
        sdp = self._local_sdp()
        return (
            f"INVITE {self._d_remote_target} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={self._d_branch};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"From: {self._d_local}\r\n"
            f"To: {self._d_remote}\r\n"
            f"Call-ID: {self._d_call_id}\r\n"
            f"CSeq: {self._d_cseq} INVITE\r\n"
            f"Contact: {self._contact_uri()}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n\r\n"
            f"{sdp}"
        )

    def _build_ack(self, resp: sm.SipMessage) -> str:
        to = resp.header("To")
        target = _angle_uri(resp.header("Contact")) or self._d_remote_target
        try:
            cseq = resp.header("CSeq").split()[0]
        except (ValueError, IndexError):
            cseq = str(self._d_cseq)
        return (
            f"ACK {target} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={sm.gen_branch()};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"From: {self._d_local}\r\n"
            f"To: {to or self._d_remote}\r\n"
            f"Call-ID: {self._d_call_id}\r\n"
            f"CSeq: {cseq} ACK\r\n"
            "Content-Length: 0\r\n\r\n"
        )

    def _handle_invite_response(self, m: sm.SipMessage) -> None:
        if not self._outbound:
            return

        # Any response means the INVITE was received: stop retransmitting it.
        self._cancel_invite_retx()

        if m.status_code in (401, 407) and not self._invite_auth_tried:
            self._send_raw(self._build_ack(m))
            self._invite_auth_tried = True
            proxy = m.status_code == 407
            ch = m.header("Proxy-Authenticate" if proxy else "WWW-Authenticate")
            realm = sm.auth_param(ch, "realm")
            nonce = sm.auth_param(ch, "nonce")
            qop = sm.auth_param(ch, "qop")
            opaque = sm.auth_param(ch, "opaque")
            uri = self._d_remote_target
            nc = "00000001"
            cnonce = sm.gen_random_hex(8)
            resp = digest_response(
                self.config.username, self.config.password, realm, "INVITE", uri,
                nonce, "auth" if qop else "", nc, cnonce,
            )
            self._d_cseq += 1
            self._d_branch = sm.gen_branch()
            msg = self._build_invite()
            auth = self._digest_auth_line(
                proxy, realm, nonce, uri, resp, qop, nc, cnonce, opaque
            )
            self._invite_msg = msg.replace("Content-Type:", auth + "Content-Type:", 1)
            self._send_raw(self._invite_msg)
            self._start_invite_retx()  # new INVITE transaction, retransmit if lost
            return

        if 100 <= m.status_code < 200:
            if m.status_code in (180, 183):
                self._set_state(SipState.RINGING_OUT)
            return

        if 200 <= m.status_code < 300:
            # Retransmitted 2xx (our ACK was lost): re-ACK only, no duplicate setup.
            if self.state == SipState.IN_CALL:
                self._send_raw(self._build_ack(m))
                return
            if self._ring_timeout_handle is not None:
                self._ring_timeout_handle.cancel()
                self._ring_timeout_handle = None
            to = m.header("To")
            if to:
                self._d_remote = to
            contact_uri = _angle_uri(m.header("Contact"))
            if contact_uri:
                self._d_remote_target = contact_uri
            self._apply_remote_sdp(sm.parse_sdp(m.body))
            self._send_raw(self._build_ack(m))
            self._loop.create_task(self._start_media())
            self._set_state(SipState.IN_CALL)
            _LOGGER.info("Call connected")
            self._emit("on_call_connected")
            if self._pending_source is not None:
                self.play_source(self._pending_source)
                self._pending_source = None
            return

        # >= 300 final failure
        self._send_raw(self._build_ack(m))
        _LOGGER.warning("Call failed: %s %s", m.status_code, m.reason)
        self._end_call()

    def _digest_auth_line(self, proxy, realm, nonce, uri, resp, qop, nc, cnonce, opaque) -> str:
        head = "Proxy-Authorization: " if proxy else "Authorization: "
        auth = (
            f'{head}Digest username="{self.config.username}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{resp}", algorithm=MD5'
        )
        if qop:
            auth += f", qop=auth, nc={nc}, cnonce=\"{cnonce}\""
        if opaque:
            auth += f', opaque="{opaque}"'
        return auth + "\r\n"

    def _apply_remote_sdp(self, sdp: sm.SdpInfo) -> None:
        if sdp.connection_ip:
            self._remote_rtp_ip = sdp.connection_ip
        self._remote_rtp_port = sdp.audio_port
        self._chosen_pt = _choose_payload(sdp)
        self._remote_dtmf_pt = sdp.telephone_event_pt

    # -- inbound requests ----------------------------------------------
    @staticmethod
    def _extract_caller(m: sm.SipMessage) -> str:
        frm = m.header("From")
        lt = frm.find("sip:")
        if lt == -1:
            return frm
        at = frm.find("@", lt)
        gt = -1
        for ch in (">", ";"):
            idx = frm.find(ch, lt)
            if idx != -1 and (gt == -1 or idx < gt):
                gt = idx
        end = at if (at != -1 and (gt == -1 or at < gt)) else gt
        if end == -1:
            end = len(frm)
        return frm[lt + 4:end]

    def _build_response(self, req: sm.SipMessage, code: int, reason: str, with_sdp: bool) -> str:
        to = req.header("To")
        if "tag=" not in to:
            to += f";tag={self._d_local_tag}"
        sdp = self._local_sdp() if with_sdp else ""
        msg = (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {req.header('Via')}\r\n"
            f"From: {req.header('From')}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {req.header('Call-ID')}\r\n"
            f"CSeq: {req.header('CSeq')}\r\n"
        )
        if 200 <= code < 300 and req.method == "INVITE":
            msg += f"Contact: {self._contact_uri()}\r\n"
        msg += f"User-Agent: {USER_AGENT}\r\n"
        if with_sdp:
            msg += (
                "Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n\r\n{sdp}"
            )
        else:
            msg += "Content-Length: 0\r\n\r\n"
        return msg

    def _handle_request(self, m: sm.SipMessage) -> None:
        method = m.method
        if method == "INVITE":
            # Retransmitted INVITE for the dialog we're already handling (our
            # provisional / 200 was lost): replay the appropriate response.
            if (
                not self._outbound
                and self._incoming_invite is not None
                and m.header("Call-ID") == self._d_call_id
                and self.state in (SipState.INCOMING, SipState.ANSWERING, SipState.IN_CALL)
            ):
                if self.state == SipState.INCOMING:
                    self._send_raw(self._build_response(m, 180, "Ringing", False))
                else:
                    self._send_raw(self._build_response(self._incoming_invite, 200, "OK", True))
                return
            caller = self._extract_caller(m)
            self.last_caller = caller
            if self.state != SipState.REGISTERED or self.dnd:
                if self.dnd:
                    _LOGGER.info("Call rejected due to DND: Busy Here")
                    self._emit("on_incoming_call", caller)
                    self._emit("on_call_ended")
                self._send_raw(self._build_response(m, 486, "Busy Here", False))
                return
            self._outbound = False
            self._incoming_invite = m
            self._d_call_id = m.header("Call-ID")
            self._d_local_tag = sm.gen_tag()
            self._d_local = m.header("To")
            if "tag=" not in self._d_local:
                self._d_local += f";tag={self._d_local_tag}"
            self._d_remote = m.header("From")
            self._d_remote_target = _angle_uri(m.header("Contact"))
            try:
                self._d_cseq = int(m.header("CSeq").split()[0])
            except (ValueError, IndexError):
                self._d_cseq = 1
            self._apply_remote_sdp(sm.parse_sdp(m.body))

            # Check for standard Intercom/Doorbell auto-answer headers
            call_info = m.header("Call-Info")
            alert_info = m.header("Alert-Info")
            auto_answer = False
            if call_info and "answer-after=" in call_info:
                try:
                    idx = call_info.find("answer-after=")
                    val = call_info[idx + len("answer-after="):].split(";")[0].split()[0]
                    if int(val) == 0:
                        auto_answer = True
                except Exception:
                    pass
            if alert_info and any(x in alert_info.lower() for x in ("answer", "auto")):
                auto_answer = True

            # Also check contact-based auto-answer rule
            if not auto_answer and self.auto_answer_checker is not None:
                try:
                    auto_answer = self.auto_answer_checker(caller)
                except Exception:
                    pass

            if auto_answer:
                _LOGGER.info("Auto-answering incoming call from %s (intercom)", caller)
                self._send_raw(self._build_response(m, 100, "Trying", False))
                self._set_state(SipState.ANSWERING)
                self._send_raw(self._build_response(m, 200, "OK", True))
                self._loop.create_task(self._start_media())
                self._emit("on_incoming_call", caller)
                self._set_state(SipState.IN_CALL)
                _LOGGER.info("Call auto-answered and connected")
                self._emit("on_call_connected")
                return

            self._send_raw(self._build_response(m, 100, "Trying", False))
            self._send_raw(self._build_response(m, 180, "Ringing", False))
            self._set_state(SipState.INCOMING)
            _LOGGER.info("Incoming call from %s", caller)
            self._emit("on_incoming_call", caller)
            return

        if method == "ACK":
            if self.state == SipState.ANSWERING:
                self._set_state(SipState.IN_CALL)
                _LOGGER.info("Call connected (inbound)")
                self._emit("on_call_connected")
            return

        if method == "BYE":
            self._send_raw(self._build_response(m, 200, "OK", False))
            _LOGGER.info("Remote hung up")
            self._end_call()
            return

        if method == "CANCEL":
            self._send_raw(self._build_response(m, 200, "OK", False))
            if self.state == SipState.INCOMING and self._incoming_invite is not None:
                self._send_raw(
                    self._build_response(self._incoming_invite, 487, "Request Terminated", False)
                )
                self._end_call()
            return

        # OPTIONS / unknown in-dialog request: acknowledge.
        self._send_raw(self._build_response(m, 200, "OK", False))

    # -- call control ---------------------------------------------------
    def answer(self) -> None:
        if self.state != SipState.INCOMING or self._incoming_invite is None:
            _LOGGER.warning("answer() ignored in state %s", self.state)
            return
        self._loop.create_task(self._start_media())
        self._send_raw(self._build_response(self._incoming_invite, 200, "OK", True))
        self._set_state(SipState.ANSWERING)
        _LOGGER.info("Answered")

    def hangup(self, sip_code: int | None = None) -> None:
        if self.state in (SipState.IN_CALL, SipState.ANSWERING):
            self._d_cseq += 1
            self._send_raw(self._build_in_dialog("BYE"))
            self._end_call()
        elif self.state in (SipState.INVITING, SipState.RINGING_OUT):
            self._d_cseq += 1
            msg = (
                f"CANCEL {self._d_remote_target} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={self._d_branch};rport\r\n"
                "Max-Forwards: 70\r\n"
                f"From: {self._d_local}\r\n"
                f"To: {self._d_remote}\r\n"
                f"Call-ID: {self._d_call_id}\r\n"
                f"CSeq: {self._d_cseq} CANCEL\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            self._send_raw(msg)
            self._end_call()
        elif self.state == SipState.INCOMING and self._incoming_invite is not None:
            code = sip_code or 603
            reasons = {
                400: "Bad Request",
                403: "Forbidden",
                404: "Not Found",
                480: "Temporarily Unavailable",
                486: "Busy Here",
                603: "Decline",
            }
            reason = reasons.get(code, "Decline")
            self._send_raw(self._build_response(self._incoming_invite, code, reason, False))
            self._end_call()

    def _build_in_dialog(self, method: str) -> str:
        return (
            f"{method} {self._d_remote_target} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={sm.gen_branch()};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"From: {self._d_local}\r\n"
            f"To: {self._d_remote}\r\n"
            f"Call-ID: {self._d_call_id}\r\n"
            f"CSeq: {self._d_cseq} {method}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            "Content-Length: 0\r\n\r\n"
        )

    def send_dtmf(self, digits: str) -> None:
        if self.state != SipState.IN_CALL:
            _LOGGER.warning("DTMF ignored: not in call")
            return
        self.rtp.queue_dtmf(digits)

    def _end_call(self) -> None:
        self._cancel_invite_retx()
        if self._ring_timeout_handle is not None:
            self._ring_timeout_handle.cancel()
            self._ring_timeout_handle = None
        self._loop.create_task(self._stop_media())
        self._set_state(SipState.REGISTERED if self.registered else SipState.IDLE)
        self._emit("on_call_ended")

    # -- media ----------------------------------------------------------
    async def _start_media(self) -> None:
        if self._media_active:
            return
        if not self._remote_rtp_ip or not self._remote_rtp_port:
            _LOGGER.warning("No remote RTP endpoint; media not started")
            return
        self.rtp.payload_type = self._chosen_pt
        self.rtp.dtmf_pt = self._remote_dtmf_pt
        self.rtp.set_remote(self._remote_rtp_ip, self._remote_rtp_port)
        self.rtp.on_audio = self._on_rx_audio
        self.rtp.on_dtmf = self._on_rx_dtmf
        if not await self.rtp.start(self.config.local_rtp_port):
            return
        self._media_active = True
        _LOGGER.info(
            "Media started: remote %s:%s pt=%s dtmf_pt=%s",
            self._remote_rtp_ip, self._remote_rtp_port, self._chosen_pt, self._remote_dtmf_pt,
        )

    async def _stop_media(self) -> None:
        self._cancel_source()
        if not self._media_active:
            await self.rtp.stop()
            return
        await self.rtp.stop()
        self._media_active = False

    def _on_rx_audio(self, pcm_le: bytes) -> None:
        try:
            self.sink.write(pcm_le)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Audio sink error")

    def _on_rx_dtmf(self, c: str) -> None:
        self._emit("on_dtmf", c)

    # -- TX audio source (play file / TTS to the far end) ---------------
    def play_source(self, source: AudioSource) -> None:
        """Stream an :class:`AudioSource` into the call's TX path."""
        if not self.in_call:
            _LOGGER.warning("play_source ignored: not in call")
            return
        self._cancel_source()
        self._tx_source_task = self._loop.create_task(self._run_source(source))

    def stop_audio(self) -> None:
        self._cancel_source()

    def _cancel_source(self) -> None:
        if self._tx_source_task is not None:
            self._tx_source_task.cancel()
            self._tx_source_task = None

    async def _run_source(self, source: AudioSource) -> None:
        try:
            await source.run(self.rtp.push_tx_audio, lambda: self.in_call)
            # Wait for queued audio to actually leave the RTP buffer before
            # signalling completion, so a caller that hangs up on playback-done
            # doesn't truncate the tail of the message.
            for _ in range(500):  # safety cap (~10 s)
                if not self.in_call or self.rtp.tx_idle():
                    break
                await asyncio.sleep(0.02)
            self._emit("on_playback_done")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Audio source error")

    # -- packet dispatch ------------------------------------------------
    def _on_packet(self, data: bytes) -> None:
        # A single malformed packet or a downstream handler error must never
        # take down the UDP listener or the integration.
        try:
            raw = data.decode("utf-8", errors="replace")
            _LOGGER.log(5, "RX <<<\n%s", raw)
            m = sm.parse_sip_message(raw)
            if m.is_request:
                self._handle_request(m)
                return
            cseq = m.header("CSeq")
            parts = cseq.split()
            method = parts[1] if len(parts) >= 2 else ""
            if method == "REGISTER":
                self._handle_register_response(m)
            elif method == "INVITE":
                self._handle_invite_response(m)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error handling SIP packet (ignored)")
