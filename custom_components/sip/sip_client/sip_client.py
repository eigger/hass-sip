"""Asyncio SIP user-agent (registrar client) — port of sip_client.cpp.

Framework-agnostic: it talks UDP and exposes call-control methods plus a set of
callback hooks. The Home Assistant layer wires those hooks to entities, events
and services. No knowledge of Home Assistant lives here.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Callable

from . import codecs
from . import sip_message as sm
from . import trace
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
    auth_username: str = ""
    domain: str = ""
    caller_id: str = ""
    register_expiration: int = 300
    local_rtp_port: int = 7078
    outbound_proxy: str = ""
    media_timeout: int = 30
    max_call_duration: int = 3600


@dataclass
class SipCallbacks:
    on_state_change: Callable[[SipState], None] | None = None
    on_registered: Callable[[], None] | None = None
    on_register_failed: Callable[[str], None] | None = None
    on_incoming_call: Callable[[str], None] | None = None
    on_call_connected: Callable[[], None] | None = None
    on_call_ended: Callable[[str], None] | None = None
    on_dtmf: Callable[[str], None] | None = None
    on_playback_done: Callable[[], None] | None = None
    on_codec_change: Callable[[codecs.Codec], None] | None = None


_HOLD_IPS = frozenset({"0.0.0.0", "0:0:0:0:0:0:0:0", "::"})
_INFO_DTMF_TYPES = ("application/dtmf-relay", "application/dtmf", "audio/telephone-event")


def _dtmf_from_token(token: str) -> str | None:
    """Map a DTMF signal token (``1``, ``10``, ``*``, ``#``, ``A``…) to a character."""
    token = token.strip()
    if not token:
        return None
    if len(token) == 1 and (token.isdigit() or token in "*#ABCD"):
        return token.upper()
    # Numeric event codes (RFC 4733): 10 -> '*', 11 -> '#', 12..15 -> A..D
    if token.isdigit():
        event = int(token)
        if event <= 9:
            return str(event)
        if event == 10:
            return "*"
        if event == 11:
            return "#"
        if event <= 15:
            return chr(ord("A") + (event - 12))
    return None


def _parse_info_dtmf(content_type: str, body: str) -> str | None:
    """Extract a DTMF digit from a SIP INFO body.

    Handles the ``Signal=1`` / ``d=1`` key-value form used by most ATAs and
    gateways, as well as a body that is just the bare digit.
    """
    if content_type and not any(t in content_type.lower() for t in _INFO_DTMF_TYPES):
        return None
    if not body:
        return None

    for line in body.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.strip().lower() in ("signal", "d", "dtmf"):
            return _dtmf_from_token(value)

    # No key/value pair: some devices send just the digit as the whole body.
    return _dtmf_from_token(body)


def _angle_uri(value: str) -> str:
    lt = value.find("<")
    gt = value.find(">")
    if lt != -1 and gt != -1 and gt > lt:
        return value[lt + 1:gt]
    # No angle brackets: treat the whole value as a bare URI (RFC 3261 §20.10)
    stripped = value.strip()
    return stripped if stripped.startswith("sip") else ""


# A route is loose only when its URI carries an ";lr" parameter (RFC 3261
# §19.1.1). Match it as a whole parameter so ";lrx" or a userinfo "lr" is not
# mistaken for one.
_LOOSE_ROUTE_PARAM = re.compile(r";lr(?=[;=?]|$)", re.IGNORECASE)


def _is_loose_route(route: str) -> bool:
    """Whether a Record-Route/Route field-value points at a loose router."""
    return bool(_LOOSE_ROUTE_PARAM.search(_angle_uri(route) or route.strip()))


def _cseq_number(header: str) -> int:
    try:
        return int(header.split()[0])
    except (ValueError, IndexError):
        return 0


def _via_branch(via: str) -> str:
    """Branch of the top Via (the transaction this request belongs to)."""
    first = via.split(",", 1)[0]
    match = re.search(r";branch=([^;,\s]+)", first, re.IGNORECASE)
    return match.group(1) if match else ""


def _answer_direction(sdp: sm.SdpInfo) -> str:
    """RFC 3264 answer direction for a remote offer, including RFC 2543 hold."""
    if sdp.direction == "inactive":
        return "inactive"
    if sdp.direction == "sendonly" or sdp.connection_ip in _HOLD_IPS:
        return "recvonly"
    if sdp.direction == "recvonly":
        return "sendonly"
    return "sendrecv"


def _offered_codec_names(sdp: sm.SdpInfo) -> list[str]:
    named = {"G722": sdp.g722_pt, "PCMU": sdp.pcmu_pt, "PCMA": sdp.pcma_pt}
    names: list[str] = []
    for codec in codecs.SUPPORTED:
        if named[codec.name] >= 0 or codec.payload_type in sdp.offered_pts:
            names.append(codec.name)
    return names


def _codec_mismatch(
    remote_names: list[str], remote_pts: list[int], dtmf_pt: int
) -> bool:
    """True when the remote offer has audio we cannot negotiate."""
    supported = {c.name for c in codecs.SUPPORTED}
    if remote_names:
        return supported.isdisjoint(remote_names)
    audio_pts = {pt for pt in remote_pts if pt != dtmf_pt and pt != 13}
    if not audio_pts:
        return False
    known = {c.payload_type for c in codecs.SUPPORTED}
    return audio_pts.isdisjoint(known)


def _audio_path(rx: int, tx: int) -> str:
    if rx > 0 and tx > 0:
        return "bidirectional"
    if tx > 0 and rx == 0:
        return "no_rx"
    if rx > 0 and tx == 0:
        return "no_tx"
    return "none"


def _fmt_diag_endpoint(addr: tuple[str, int] | None) -> str | None:
    if not addr:
        return None
    return f"{addr[0]}:{addr[1]}"


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
        self.last_registered_at: float | None = None
        self.last_register_failed: str | None = None
        self.last_call_reason: str | None = None
        self.last_call_bytes_rx = 0
        self.last_call_bytes_tx = 0
        self._remote_offered_pts: list[int] = []
        self._remote_offered_names: list[str] = []
        self._register_handle: asyncio.TimerHandle | None = None
        self._reg_attempts = 0

        # registration transaction
        self._reg_call_id = ""
        self._reg_tag = ""
        self._reg_branch = ""
        self._reg_cseq = 0
        self._register_auth_tried = False
        self._service_routes: str | None = None

        # current dialog
        self._d_call_id = ""
        self._d_local = ""
        self._d_remote = ""
        self._d_remote_target = ""
        self._d_local_tag = ""
        self._d_branch = ""
        self._d_cseq = 0
        self._invite_cseq = 0
        self._outbound = False
        self._invite_auth_tried = False
        self._incoming_invite: sm.SipMessage | None = None
        self._dialog_routes: list[str] = []
        self._accepted_dialog_to = ""
        # Last INVITE we received in this dialog (initial or re-INVITE). Used
        # to tell a retransmission (same CSeq + branch) from a re-INVITE.
        self._remote_invite_cseq = 0
        self._remote_invite_branch = ""
        self._on_hold = False
        self._local_direction = "sendrecv"

        # negotiated media
        self._remote_rtp_ip = ""
        self._remote_rtp_port = 0
        self._codec: codecs.Codec = codecs.DEFAULT
        self._chosen_pt = self._codec.payload_type
        self._remote_dtmf_pt = -1
        self._sdp_negotiated = False
        self._media_active = False

        self.rtp = RtpSession()
        self.rtp.media_timeout = float(self.config.media_timeout)
        self.rtp.on_media_timeout = self._on_media_timeout
        self.sink: AudioSink = NullSink()
        self._tx_source_task: asyncio.Task | None = None
        self._pending_source: AudioSource | None = None
        self._ring_timeout_handle: asyncio.TimerHandle | None = None
        self._max_duration_handle: asyncio.TimerHandle | None = None
        # INVITE retransmission (RFC 3261 over unreliable UDP)
        self._invite_msg: str | None = None
        self._invite_retx_handle: asyncio.TimerHandle | None = None
        self._invite_retx_count = 0
        self.dnd = False
        self.auto_answer_checker: Callable[[str], bool] | None = None

    # ------------------------------------------------------------------
    @property
    def codec(self) -> codecs.Codec:
        """Negotiated audio codec for the current / last dialog."""
        return self._codec

    @property
    def in_call(self) -> bool:
        return self.state == SipState.IN_CALL

    @property
    def media_playing(self) -> bool:
        return self._tx_source_task is not None and not self._tx_source_task.done()

    def set_sink(self, sink: AudioSink) -> None:
        self.sink = sink

    def diagnostics_snapshot(self) -> dict:
        """Runtime SIP/RTP state for the HA diagnostics download (no secrets)."""
        if self.rtp.running:
            rx, tx = self.rtp.bytes_received, self.rtp.bytes_sent
        else:
            rx, tx = self.last_call_bytes_rx, self.last_call_bytes_tx
        sdp = self.rtp.sdp_remote
        if sdp is None and self._remote_rtp_ip:
            sdp = (self._remote_rtp_ip, self._remote_rtp_port)
        return {
            "state": str(self.state),
            "registration": {
                "registered": self.registered,
                "last_registered_at": self.last_registered_at,
                "last_failure": self.last_register_failed,
            },
            "codec": {
                "negotiated": self._codec.name,
                "payload_type": self._codec.payload_type,
                "sample_rate": self._codec.sample_rate,
                "telephone_event_pt": self._remote_dtmf_pt,
                "local_supported": [c.name for c in codecs.SUPPORTED],
                "remote_offered": list(self._remote_offered_names),
                "remote_offered_pts": list(self._remote_offered_pts),
                "mismatch": _codec_mismatch(
                    self._remote_offered_names,
                    self._remote_offered_pts,
                    self._remote_dtmf_pt,
                ),
            },
            "rtp": {
                "sdp_remote": _fmt_diag_endpoint(sdp),
                "latched_remote": _fmt_diag_endpoint(self.rtp.latched_remote),
                "bytes_received": rx,
                "bytes_sent": tx,
                "audio_path": _audio_path(rx, tx),
                "expect_rx": self.rtp.expect_rx,
                "tx_enabled": self.rtp.tx_enabled,
                "running": self.rtp.running,
            },
            "call": {
                "in_call": self.in_call,
                "outbound": self._outbound,
                "local_direction": self._local_direction,
                "on_hold": self._on_hold,
                "last_end_reason": self.last_call_reason,
                "last_caller": self.last_caller,
            },
        }

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        self._closing = False
        if await self._open_socket():
            self._do_register()
        else:
            self._register_failed("Connection failed")
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
        target_server = self.config.outbound_proxy if self.config.outbound_proxy else self.config.server
        # Resolve via the loop so a slow DNS lookup never blocks the event loop.
        try:
            infos = await self._loop.getaddrinfo(
                target_server,
                self.config.port,
                family=socket.AF_INET,
                type=socket.SOCK_DGRAM,
            )
        except OSError as err:
            _LOGGER.warning("Cannot resolve SIP target '%s': %s", target_server, err)
            return False
        if not infos:
            _LOGGER.warning("No address found for SIP target '%s'", target_server)
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
        trace.log_sip("TX", msg)
        self._transport.sendto(msg.encode("utf-8"))


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
            if state == SipState.IN_CALL:
                self._arm_max_duration()

    # -- registration ---------------------------------------------------
    def _contact_uri(self) -> str:
        return f"<sip:{self.config.username}@{self._local_ip}:{self._local_port}>"

    @staticmethod
    def _parse_min_expires(m: sm.SipMessage) -> int | None:
        """Return Min-Expires from a 423 response, or None if missing/invalid."""
        raw = m.header("Min-Expires")
        if not raw:
            return None
        try:
            value = int(raw.strip().split(";")[0].strip())
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

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
        if m.status_code == 423:
            # RFC 3261 §10.2.8 / §21.4.17: retry with Expires >= Min-Expires.
            min_expires = self._parse_min_expires(m)
            if min_expires is not None and min_expires > self.config.register_expiration:
                _LOGGER.info(
                    "REGISTER 423 Interval Too Brief; raising Expires from %s to %s",
                    self.config.register_expiration,
                    min_expires,
                )
                self.config.register_expiration = min_expires
                self._do_register()
                return
            _LOGGER.warning(
                "REGISTER failed: 423 Interval Too Brief (Min-Expires=%s, current=%s)",
                min_expires,
                self.config.register_expiration,
            )
            self.registered = False
            self._reg_attempts = 0
            self._register_failed(
                f"423 Interval Too Brief (Min-Expires={min_expires})"
            )
            self._schedule_register(10)
            return
        if 200 <= m.status_code < 300:
            was = self.registered
            self.registered = True
            self.last_register_failed = None
            self.last_registered_at = time.time()
            self._reg_attempts = 0
            self._set_state(SipState.REGISTERED)
            self._schedule_register(max(self.config.register_expiration // 2, 30))
            if sr := m.header("Service-Route"):
                self._service_routes = sr
            if not was:
                _LOGGER.info("Registered with %s", self.config.server)
                self._emit("on_registered")
            return
        _LOGGER.warning("REGISTER failed: %s %s", m.status_code, m.reason)
        self.registered = False
        # The server responded, so the socket is alive: gentle retry, no reconnect.
        self._reg_attempts = 0
        self._register_failed(f"{m.status_code} {m.reason}")
        self._schedule_register(10)

    def _register_failed(self, reason: str) -> None:
        self.last_register_failed = reason
        self._emit("on_register_failed", reason)

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
        auth_user = self.config.auth_username or self.config.username
        resp = digest_response(
            auth_user, self.config.password, realm, "REGISTER", uri,
            nonce, "auth" if qop else "", nc, cnonce,
        )
        self._reg_cseq += 1
        self._reg_branch = sm.gen_branch()
        msg = self._build_register()
        auth_user = self.config.auth_username or self.config.username
        auth = self._digest_auth_line(proxy, auth_user, realm, nonce, uri, resp, qop, nc, cnonce, opaque)
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
        self._dialog_routes = []
        self._accepted_dialog_to = ""
        self._remote_invite_cseq = 0
        self._remote_invite_branch = ""
        self._begin_dialog_media()
        self._d_call_id = sm.gen_call_id(self._local_ip)
        self._d_local_tag = sm.gen_tag()
        self._d_branch = sm.gen_branch()
        self._d_cseq = 1
        self._invite_cseq = self._d_cseq
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
                self.hangup(reason="ring_timeout")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Ring timeout handler error")

    def _local_sdp(self, only: codecs.Codec | None = None) -> str:
        """Build a local SDP body.

        With ``only=None`` this is a full offer (all supported codecs). Pass the
        negotiated codec for an answer so we do not re-advertise codecs the
        remote never offered (RFC 3264).
        """
        sid = str(int(time.time()))
        # RFC 3264: answer sendonly with recvonly, inactive with inactive.
        direction = self._local_direction
        return (
            "v=0\r\n"
            f"o=- {sid} {sid} IN IP4 {self._local_ip}\r\n"
            "s=homeassistant\r\n"
            f"c=IN IP4 {self._local_ip}\r\n"
            "t=0 0\r\n"
            f"{codecs.sdp_media_line(self.config.local_rtp_port, only=only)}"
            f"{codecs.sdp_rtpmaps(only=only)}"
            "a=fmtp:101 0-15\r\n"
            "a=ptime:20\r\n"
            f"a={direction}\r\n"
        )

    def _build_invite(self) -> str:
        sdp = self._local_sdp()
        msg = (
            f"INVITE {self._d_remote_target} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={self._d_branch};rport\r\n"
            "Max-Forwards: 70\r\n"
        )
        if self._service_routes:
            msg += f"Route: {self._service_routes}\r\n"
        
        msg += (
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
        return msg

    def _build_ack(
        self, resp: sm.SipMessage, routes: list[str] | None = None
    ) -> str:
        to = resp.header("To")
        target = _angle_uri(resp.header("Contact")) or self._d_remote_target
        try:
            cseq = resp.header("CSeq").split()[0]
        except (ValueError, IndexError):
            cseq = str(self._invite_cseq)
            
        if 300 <= resp.status_code < 700:
            # ACK to a non-2xx response MUST use the exact same branch as the original request
            branch = self._d_branch
        else:
            # ACK to a 2xx response is a new transaction, needs a new branch
            branch = sm.gen_branch()
            
        via = f"SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={branch};rport"

        if self._service_routes and 300 <= resp.status_code < 700:
            route_block = f"Route: {self._service_routes}\r\n"
        elif 200 <= resp.status_code < 300:
            target, route_block = self._route_request(target, routes)
        else:
            route_block = ""

        msg = (
            f"ACK {target} SIP/2.0\r\n"
            f"Via: {via}\r\n"
            "Max-Forwards: 70\r\n"
            f"{route_block}"
            f"From: {self._d_local}\r\n"
            f"To: {to or self._d_remote}\r\n"
            f"Call-ID: {self._d_call_id}\r\n"
            f"CSeq: {cseq} ACK\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        return msg

    def _handle_invite_response(self, m: sm.SipMessage) -> None:
        if not self._outbound:
            return

        cseq_parts = m.header("CSeq").split()
        try:
            cseq_num = int(cseq_parts[0])
        except (ValueError, IndexError):
            cseq_num = 0

        if (
            len(cseq_parts) < 2
            or cseq_parts[1].upper() != "INVITE"
            or m.header("Call-ID") != self._d_call_id
        ):
            return

        if cseq_num != self._invite_cseq:
            # Ignore responses for old transactions, but re-ACK final failures (>=300)
            # to stop server retransmissions.
            if 300 <= m.status_code < 700:
                self._send_raw(self._build_ack(m))
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
            auth_user = self.config.auth_username or self.config.username
            resp = digest_response(
                auth_user, self.config.password, realm, "INVITE", uri,
                nonce, "auth" if qop else "", nc, cnonce,
            )
            self._d_cseq += 1
            self._invite_cseq = self._d_cseq
            self._d_branch = sm.gen_branch()
            msg = self._build_invite()
            auth = self._digest_auth_line(
                proxy, auth_user, realm, nonce, uri, resp, qop, nc, cnonce, opaque
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
            response_routes = list(
                reversed(sm.split_header_values(m.header("Record-Route")))
            )
            response_to = m.header("To") or self._d_remote

            # Every 2xx requires an ACK. A retransmission of the accepted
            # dialog must not repeat media setup, even if the call has ended.
            if self._accepted_dialog_to:
                if response_to == self._accepted_dialog_to:
                    self._send_raw(self._build_ack(m, response_routes))
                else:
                    self._end_forked_dialog(m, response_routes, response_to)
                return

            # A 2xx can race with local cancellation. Acknowledge and close
            # the unwanted dialog without reviving the call.
            if self.state not in (SipState.INVITING, SipState.RINGING_OUT):
                self._end_forked_dialog(m, response_routes, response_to)
                return
            if self._ring_timeout_handle is not None:
                self._ring_timeout_handle.cancel()
                self._ring_timeout_handle = None
            self._accepted_dialog_to = response_to
            self._d_remote = response_to
            contact_uri = _angle_uri(m.header("Contact"))
            if contact_uri:
                self._d_remote_target = contact_uri
            self._dialog_routes = response_routes
            self._apply_remote_sdp(sm.parse_sdp(m.body))
            self._send_raw(self._build_ack(m))

            async def _start_and_play():
                await self._start_media()
                if self._pending_source is not None:
                    self.play_source(self._pending_source)
                    self._pending_source = None

            self._loop.create_task(_start_and_play())
            self._set_state(SipState.IN_CALL)
            _LOGGER.info("Call connected")
            self._emit("on_call_connected")
            return

        # >= 300 final failure
        self._send_raw(self._build_ack(m))
        _LOGGER.warning("Call failed: %s %s", m.status_code, m.reason)
        self._end_call("remote_reject")

    def _digest_auth_line(self, proxy, auth_user, realm, nonce, uri, resp, qop, nc, cnonce, opaque) -> str:
        head = "Proxy-Authorization: " if proxy else "Authorization: "
        auth = (
            f'{head}Digest username="{auth_user}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{resp}", algorithm=MD5'
        )
        if qop:
            auth += f", qop=auth, nc={nc}, cnonce=\"{cnonce}\""
        if opaque:
            auth += f', opaque="{opaque}"'
        return auth + "\r\n"

    def _apply_remote_sdp(self, sdp: sm.SdpInfo) -> None:
        old_ip, old_port = self._remote_rtp_ip, self._remote_rtp_port
        # RFC 2543 hold uses c=0.0.0.0; keep the last real destination.
        if sdp.connection_ip and sdp.connection_ip not in _HOLD_IPS:
            self._remote_rtp_ip = sdp.connection_ip
        if sdp.audio_port:
            self._remote_rtp_port = sdp.audio_port

        old_codec = self._codec
        in_dialog = self._sdp_negotiated
        # First SDP of a dialog picks the preferred codec. Later re-INVITE /
        # UPDATE keep the current one when it is still offered.
        if in_dialog:
            new_codec = codecs.keep_or_choose(self._codec, sdp)
        else:
            new_codec = codecs.choose(sdp)
        codec_changed = (
            new_codec.name != old_codec.name
            or new_codec.payload_type != old_codec.payload_type
        )
        self._codec = new_codec
        self._chosen_pt = self._codec.payload_type
        self._remote_offered_pts = sorted(sdp.offered_pts)
        self._remote_offered_names = _offered_codec_names(sdp)
        if sdp.valid:
            self._sdp_negotiated = True
            # Including -1: a new offer without telephone-event must not
            # inherit the previous call's DTMF payload type.
            self._remote_dtmf_pt = sdp.telephone_event_pt
            self.rtp.dtmf_pt = self._remote_dtmf_pt
        if codec_changed:
            self.rtp.set_codec(self._codec)
            if old_codec.sample_rate != new_codec.sample_rate:
                self.rtp.flush_tx_buffer()
                if self._tx_source_task is not None:
                    self._cancel_source()
                    # Unblock Assist/IVR waiters; CancelledError skips the
                    # normal on_playback_done at the end of _run_source.
                    self._emit("on_playback_done")
            if in_dialog:
                _LOGGER.info(
                    "Negotiated codec %s (pt=%s, %s Hz)",
                    self._codec.name, self._codec.payload_type, self._codec.sample_rate,
                )
                self._emit("on_codec_change", self._codec)

        self._local_direction = _answer_direction(sdp)
        self._set_hold(sdp.is_hold)
        self.rtp.set_expect_rx(self._local_direction != "sendonly")
        self._sync_media_endpoint(old_ip, old_port)

    def _begin_dialog_media(self) -> None:
        """Clear per-call media state so the previous dialog cannot leak."""
        self._codec = codecs.DEFAULT
        self._chosen_pt = self._codec.payload_type
        self._remote_dtmf_pt = -1
        self._remote_offered_pts = []
        self._remote_offered_names = []
        self.rtp.dtmf_pt = -1
        self._sdp_negotiated = False
        self._remote_rtp_ip = ""
        self._remote_rtp_port = 0
        self._on_hold = False
        self._local_direction = "sendrecv"
        self.rtp.send_silence = True
        self.rtp.clear_tx_pause()
        self.rtp.set_expect_rx(True)

    def _sync_media_endpoint(self, old_ip: str, old_port: int) -> None:
        """Retarget a live RTP session, or start one once a real address arrives."""
        if not self._remote_rtp_ip or not self._remote_rtp_port:
            return
        if self._media_active:
            if (self._remote_rtp_ip, self._remote_rtp_port) != (old_ip, old_port):
                self.rtp.set_remote(self._remote_rtp_ip, self._remote_rtp_port)
            return
        # Initial INVITE was offerless or c=0.0.0.0 so _start_media never
        # bound a socket. A later re-INVITE / ACK / UPDATE can supply the
        # real endpoint — start then, without tearing anything down.
        if self.state in (SipState.IN_CALL, SipState.ANSWERING):
            self._loop.create_task(self._start_media())

    def _set_hold(self, held: bool) -> None:
        if held == self._on_hold:
            return
        self._on_hold = held
        self.rtp.send_silence = not held
        self.rtp.set_tx_enabled(not held)
        _LOGGER.info("Remote %s the call", "held" if held else "resumed")

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
        # Answers carry only the negotiated codec (RFC 3264); offers use the full set.
        sdp = self._local_sdp(only=self._codec) if with_sdp else ""
        msg = (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {req.header('Via')}\r\n"
            f"From: {req.header('From')}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {req.header('Call-ID')}\r\n"
            f"CSeq: {req.header('CSeq')}\r\n"
        )
        # RFC 3261 §12.1.1: every dialog-establishing response — the early
        # dialog of a 18x included, not just the 2xx — must echo the request's
        # Record-Route and carry a Contact the peer can route in-dialog
        # requests to. Dropping either strands a proxy/SBC outside the dialog.
        if req.method in ("INVITE", "UPDATE") and 101 <= code < 300:
            if req.method == "INVITE":
                if record_route := req.header("Record-Route"):
                    msg += f"Record-Route: {record_route}\r\n"
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

    def _handle_dialog_invite(self, m: sm.SipMessage) -> bool:
        """Handle INVITE that belongs to the current dialog.

        Returns True when the request was consumed (retransmission or re-INVITE).
        """
        if not self._d_call_id or m.header("Call-ID") != self._d_call_id:
            return False
        if self.state not in (SipState.INCOMING, SipState.ANSWERING, SipState.IN_CALL):
            return False

        cseq = _cseq_number(m.header("CSeq"))
        branch = _via_branch(m.header("Via"))
        # RFC 3261: re-INVITE raises CSeq. Accept higher-CSeq while ANSWERING
        # too: a lost ACK is often followed by a media re-INVITE before the
        # original transaction completes. Same CSeq + different branch is a
        # non-standard re-INVITE some PBXes send; treat it as renegotiation
        # only once the call is established.
        established = self.state in (SipState.IN_CALL, SipState.ANSWERING)
        is_reinvite = established and (
            cseq > self._remote_invite_cseq
            or (
                self.state == SipState.IN_CALL
                and cseq == self._remote_invite_cseq
                and branch
                and branch != self._remote_invite_branch
            )
        )
        if is_reinvite:
            self._handle_reinvite(m)
            return True
        if self.state == SipState.INCOMING:
            self._send_raw(self._build_response(m, 180, "Ringing", False))
        else:
            # Replay 200 using this request's Via/CSeq so a lost 200 for the
            # original INVITE or a re-INVITE still matches the transaction.
            self._send_raw(self._build_response(m, 200, "OK", True))
        return True

    def _handle_reinvite(self, m: sm.SipMessage) -> None:
        """Answer an in-dialog re-INVITE without restarting the media session."""
        self._remote_invite_cseq = _cseq_number(m.header("CSeq"))
        self._remote_invite_branch = _via_branch(m.header("Via"))
        contact = _angle_uri(m.header("Contact"))
        if contact:
            self._d_remote_target = contact
        if m.body.strip():
            self._apply_remote_sdp(sm.parse_sdp(m.body))
        _LOGGER.info("Accepted re-INVITE (cseq=%s)", self._remote_invite_cseq)
        self._send_raw(self._build_response(m, 200, "OK", True))

    def _handle_update(self, m: sm.SipMessage) -> None:
        """Answer UPDATE; include an SDP answer when the request offered one."""
        if not self._d_call_id or m.header("Call-ID") != self._d_call_id:
            self._send_raw(
                self._build_response(m, 481, "Call/Transaction Does Not Exist", False)
            )
            return
        if self.state not in (SipState.IN_CALL, SipState.ANSWERING):
            self._send_raw(
                self._build_response(m, 481, "Call/Transaction Does Not Exist", False)
            )
            return
        if m.body.strip():
            self._apply_remote_sdp(sm.parse_sdp(m.body))
            contact = _angle_uri(m.header("Contact"))
            if contact:
                self._d_remote_target = contact
            self._send_raw(self._build_response(m, 200, "OK", True))
            return
        self._send_raw(self._build_response(m, 200, "OK", False))

    def _handle_request(self, m: sm.SipMessage) -> None:
        method = m.method
        if method == "INVITE":
            if self._handle_dialog_invite(m):
                return
            caller = self._extract_caller(m)
            self.last_caller = caller
            if self.state != SipState.REGISTERED or self.dnd:
                if self.dnd:
                    _LOGGER.info("Call rejected due to DND: Busy Here")
                    self._emit("on_incoming_call", caller)
                    self._emit("on_call_ended", "local")
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
            self._dialog_routes = sm.split_header_values(m.header("Record-Route"))
            try:
                self._d_cseq = int(m.header("CSeq").split()[0])
            except (ValueError, IndexError):
                self._d_cseq = 1
            self._remote_invite_cseq = self._d_cseq
            self._remote_invite_branch = _via_branch(m.header("Via"))
            self._begin_dialog_media()
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
            if self._d_call_id and m.header("Call-ID") != self._d_call_id:
                return
            # Offerless INVITE/re-INVITE: our 200 was the offer; the answer
            # arrives in the ACK body (RFC 3261 §13.2.1 / §14.1).
            if m.body.strip():
                self._apply_remote_sdp(sm.parse_sdp(m.body))
            if self.state == SipState.ANSWERING:
                self._set_state(SipState.IN_CALL)
                _LOGGER.info("Call connected (inbound)")
                self._emit("on_call_connected")
            return

        if method == "BYE":
            self._send_raw(self._build_response(m, 200, "OK", False))
            _LOGGER.info("Remote hung up")
            self._end_call("remote_bye")
            return

        if method == "CANCEL":
            self._send_raw(self._build_response(m, 200, "OK", False))
            if self.state == SipState.INCOMING and self._incoming_invite is not None:
                self._send_raw(
                    self._build_response(self._incoming_invite, 487, "Request Terminated", False)
                )
                self._end_call("remote_cancel")
            return

        if method == "INFO":
            # Many ATAs / gateways signal DTMF out-of-band via SIP INFO instead
            # of RFC 2833 telephone-event packets.
            self._send_raw(self._build_response(m, 200, "OK", False))
            digit = _parse_info_dtmf(m.header("Content-Type"), m.body)
            if digit is None:
                return
            # A keypress only means something inside a call. ANSWERING counts:
            # the INFO can arrive before the ACK that moves us to IN_CALL.
            if self.state not in (SipState.IN_CALL, SipState.ANSWERING):
                _LOGGER.debug("DTMF '%s' via SIP INFO ignored in state %s", digit, self.state)
                return
            _LOGGER.debug("DTMF '%s' received via SIP INFO", digit)
            self._on_rx_dtmf(digit)
            return

        if method == "UPDATE":
            self._handle_update(m)
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

    def hangup(self, sip_code: int | None = None, *, reason: str = "local") -> None:
        if self.state in (SipState.IN_CALL, SipState.ANSWERING):
            self._d_cseq += 1
            self._send_raw(self._build_in_dialog("BYE"))
            self._end_call(reason)
        elif self.state in (SipState.INVITING, SipState.RINGING_OUT):
            msg = (
                f"CANCEL {self._d_remote_target} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={self._d_branch};rport\r\n"
                "Max-Forwards: 70\r\n"
                f"From: {self._d_local}\r\n"
                f"To: {self._d_remote}\r\n"
                f"Call-ID: {self._d_call_id}\r\n"
                f"CSeq: {self._invite_cseq} CANCEL\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            self._send_raw(msg)
            self._end_call(reason)
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
            phrase = reasons.get(code, "Decline")
            self._send_raw(self._build_response(self._incoming_invite, code, phrase, False))
            self._end_call(reason)

    def _build_in_dialog(
        self,
        method: str,
        *,
        target: str | None = None,
        remote: str | None = None,
        routes: list[str] | None = None,
        cseq: int | None = None,
    ) -> str:
        request_uri, route_block = self._route_request(
            target or self._d_remote_target, routes
        )
        return (
            f"{method} {request_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self._local_ip}:{self._local_port};branch={sm.gen_branch()};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"{route_block}"
            f"From: {self._d_local}\r\n"
            f"To: {remote or self._d_remote}\r\n"
            f"Call-ID: {self._d_call_id}\r\n"
            f"CSeq: {cseq if cseq is not None else self._d_cseq} {method}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            "Content-Length: 0\r\n\r\n"
        )

    def _route_request(
        self, target: str, routes: list[str] | None = None
    ) -> tuple[str, str]:
        """Resolve the Request-URI and Route block for an in-dialog request.

        RFC 3261 §12.2.1.1: when the first hop is a loose router the remote
        target stays in the Request-URI and the whole route set travels as
        Route headers. A strict router instead takes the Request-URI, and the
        remote target moves to the tail of the route set so it is not lost.
        """
        active = [r for r in (self._dialog_routes if routes is None else routes) if r.strip()]
        if not active:
            return target, ""
        if _is_loose_route(active[0]):
            return target, f"Route: {', '.join(active)}\r\n"
        remaining = active[1:]
        if target:
            remaining = [*remaining, f"<{target}>"]
        route_block = f"Route: {', '.join(remaining)}\r\n" if remaining else ""
        return _angle_uri(active[0]) or active[0], route_block

    def _end_forked_dialog(
        self, response: sm.SipMessage, routes: list[str], remote: str
    ) -> None:
        """Acknowledge and close an unwanted 2xx dialog without changing state."""
        target = _angle_uri(response.header("Contact")) or self._d_remote_target
        self._send_raw(self._build_ack(response, routes))
        self._send_raw(
            self._build_in_dialog(
                "BYE",
                target=target,
                remote=remote,
                routes=routes,
                cseq=self._d_cseq + 1,
            )
        )

    def send_dtmf(self, digits: str) -> None:
        if self.state != SipState.IN_CALL:
            _LOGGER.warning("DTMF ignored: not in call")
            return
        self.rtp.queue_dtmf(digits)

    def _end_call(self, reason: str = "local") -> None:
        self._cancel_invite_retx()
        if self._ring_timeout_handle is not None:
            self._ring_timeout_handle.cancel()
            self._ring_timeout_handle = None
        self._cancel_max_duration()
        self.rtp._cancel_media_timeout()
        self._on_hold = False
        self._local_direction = "sendrecv"
        self.rtp.send_silence = True
        self.rtp.clear_tx_pause()
        self.last_call_reason = reason
        self.last_call_bytes_rx = self.rtp.bytes_received
        self.last_call_bytes_tx = self.rtp.bytes_sent
        self._loop.create_task(self._stop_media())
        self._set_state(SipState.REGISTERED if self.registered else SipState.IDLE)
        self._emit("on_call_ended", reason)

    def _arm_max_duration(self) -> None:
        self._cancel_max_duration()
        seconds = self.config.max_call_duration
        if seconds <= 0:
            return
        self._max_duration_handle = self._loop.call_later(
            seconds, self._on_max_duration
        )

    def _cancel_max_duration(self) -> None:
        if self._max_duration_handle is not None:
            self._max_duration_handle.cancel()
            self._max_duration_handle = None

    def _on_max_duration(self) -> None:
        self._max_duration_handle = None
        if self.state not in (SipState.IN_CALL, SipState.ANSWERING):
            return
        _LOGGER.warning("Max call duration reached (%ss)", self.config.max_call_duration)
        self.hangup(reason="max_duration")

    def _on_media_timeout(self) -> None:
        if self.state not in (SipState.IN_CALL, SipState.ANSWERING):
            return
        _LOGGER.warning("RTP media timeout")
        self.hangup(reason="media_timeout")

    # -- media ----------------------------------------------------------
    async def _start_media(self) -> None:
        if self._media_active:
            return
        if not self._remote_rtp_ip or not self._remote_rtp_port:
            _LOGGER.warning("No remote RTP endpoint; media not started")
            return
        self.rtp.set_codec(self._codec)
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

    def stop_audio(self, *, flush: bool = False) -> None:
        """Stop the current TX audio source; optionally discard queued RTP PCM."""
        self._cancel_source()
        if flush:
            self.rtp.flush_tx_buffer()

    def _cancel_source(self) -> None:
        if self._tx_source_task is not None:
            self._tx_source_task.cancel()
            self._tx_source_task = None

    async def _run_source(self, source: AudioSource) -> None:
        try:
            configure = getattr(source, "configure", None)
            if callable(configure):
                configure(self._codec.sample_rate, self._codec.pcm_frame_bytes)
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
            trace.log_sip("RX", raw)

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
