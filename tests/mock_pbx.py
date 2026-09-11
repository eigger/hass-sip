"""Asyncio UDP mock SIP registrar / minimal UAS.

No Home Assistant. Used by registration-recovery tests and reusable for
in-dialog INVITE / re-INVITE flows (P0-1) over the real socket path.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys

_SIP = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "sip", "sip_client"
)
sys.path.insert(0, os.path.abspath(_SIP))

import sip_auth  # noqa: E402
import sip_message as sm  # noqa: E402

_DEFAULT_SDP = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=mock-pbx\r\n"
    "c=IN IP4 127.0.0.1\r\n"
    "t=0 0\r\n"
    "m=audio 40000 RTP/AVP 0 8 101\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:8 PCMA/8000\r\n"
    "a=rtpmap:101 telephone-event/8000\r\n"
    "a=sendrecv\r\n"
)


def _angle_uri(value: str) -> str:
    lt = value.find("<")
    gt = value.find(">")
    if lt != -1 and gt > lt:
        return value[lt + 1 : gt]
    return value.strip()


def _expires(msg: sm.SipMessage) -> int:
    raw = msg.header("Expires")
    if not raw:
        contact = msg.header("Contact")
        if "expires=" not in contact.lower():
            return 0
        idx = contact.lower().find("expires=")
        raw = contact[idx + 8 :].split(";")[0].split(">")[0]
    try:
        return int(str(raw).strip().split(";")[0].strip())
    except (TypeError, ValueError):
        return 0


class _PbxProtocol(asyncio.DatagramProtocol):
    def __init__(self, pbx: MockPbx) -> None:
        self._pbx = pbx

    def datagram_received(self, data: bytes, addr) -> None:
        self._pbx._on_datagram(data, addr)


class MockPbx:
    """UDP registrar that can 401/407-challenge, 423, drop, or force a status."""

    def __init__(
        self,
        username: str = "1001",
        password: str = "secret",
        realm: str = "asterisk",
        domain: str = "pbx.example",
    ) -> None:
        self.username = username
        self.password = password
        self.realm = realm
        self.domain = domain
        self.require_auth = True
        self.challenge = "www"  # "www" → 401, "proxy" → 407
        self.min_expires = 0
        self.drop = False
        self.force_code: int | None = None
        self.auto_answer_invite = True
        self.nonce = "n" + os.urandom(8).hex()
        self.to_tag = sm.gen_tag()
        self.received: list[sm.SipMessage] = []
        self.peer_addr: tuple[str, int] | None = None
        self.client_contact = ""
        self.host = "127.0.0.1"
        self.port = 0
        self._transport: asyncio.DatagramTransport | None = None
        self._sock: socket.socket | None = None

    @property
    def registers(self) -> list[sm.SipMessage]:
        return [m for m in self.received if m.method == "REGISTER"]

    @property
    def invites(self) -> list[sm.SipMessage]:
        return [m for m in self.received if m.method == "INVITE"]

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        self.host, self.port = sock.getsockname()
        self._sock = sock
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _PbxProtocol(self),
            sock=sock,
        )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._sock = None

    async def restart(self) -> None:
        """Close and rebind the same port (PBX reboot)."""
        port = self.port
        host = self.host
        await self.stop()
        await self.start(host, port)

    def send_raw(self, msg: str, addr: tuple[str, int] | None = None) -> None:
        dest = addr or self.peer_addr
        if self._transport is None or dest is None:
            return
        self._transport.sendto(msg.encode("utf-8"), dest)

    def send_reinvite(
        self,
        *,
        call_id: str,
        frm: str,
        to: str,
        cseq: int = 2,
        sdp: str | None = None,
    ) -> None:
        """Send an in-dialog re-INVITE toward the last client socket."""
        if not self.client_contact:
            return
        body = sdp or _DEFAULT_SDP.replace("40000", "40002")
        target = self.client_contact
        msg = (
            f"INVITE {target} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.host}:{self.port};branch={sm.gen_branch()}\r\n"
            "Max-Forwards: 70\r\n"
            f"From: {frm}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq} INVITE\r\n"
            f"Contact: <sip:mock@{self.host}:{self.port}>\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(body)}\r\n\r\n{body}"
        )
        self.send_raw(msg)

    def _on_datagram(self, data: bytes, addr) -> None:
        self.peer_addr = addr
        raw = data.decode("utf-8", errors="replace")
        msg = sm.parse_sip_message(raw)
        self.received.append(msg)
        if not msg.is_request:
            return
        if msg.method == "REGISTER":
            self._handle_register(msg, addr)
        elif msg.method == "INVITE":
            self._handle_invite(msg, addr)
        elif msg.method == "ACK":
            return
        elif msg.method == "BYE":
            self.send_raw(self._response(msg, 200, "OK"), addr)

    def _handle_register(self, msg: sm.SipMessage, addr) -> None:
        contact = _angle_uri(msg.header("Contact"))
        if contact:
            self.client_contact = contact
        if self.drop:
            return
        if self.force_code is not None:
            extra = ""
            if self.force_code == 423:
                extra = f"Min-Expires: {self.min_expires or 600}\r\n"
            self.send_raw(
                self._response(msg, self.force_code, self._reason(self.force_code), extra),
                addr,
            )
            return
        expires = _expires(msg)
        if self.min_expires and expires < self.min_expires:
            extra = f"Min-Expires: {self.min_expires}\r\n"
            self.send_raw(self._response(msg, 423, "Interval Too Brief", extra), addr)
            return
        if self.require_auth and not self._valid_digest(msg, "REGISTER"):
            self.send_raw(self._challenge(msg), addr)
            return
        extra = f"Contact: {msg.header('Contact')}\r\nExpires: {expires or 300}\r\n"
        self.send_raw(self._response(msg, 200, "OK", extra), addr)

    def _handle_invite(self, msg: sm.SipMessage, addr) -> None:
        if not self.auto_answer_invite:
            return
        extra = (
            f"Contact: <sip:mock@{self.host}:{self.port}>\r\n"
            "Content-Type: application/sdp\r\n"
        )
        self.send_raw(self._response(msg, 200, "OK", extra, _DEFAULT_SDP), addr)

    def _challenge(self, msg: sm.SipMessage) -> str:
        if self.challenge == "proxy":
            code, reason, hdr = 407, "Proxy Authentication Required", "Proxy-Authenticate"
        else:
            code, reason, hdr = 401, "Unauthorized", "WWW-Authenticate"
        extra = (
            f'{hdr}: Digest realm="{self.realm}", nonce="{self.nonce}", '
            'algorithm=MD5, qop="auth"\r\n'
        )
        return self._response(msg, code, reason, extra)

    def _valid_digest(self, msg: sm.SipMessage, method: str) -> bool:
        hdr = msg.header("Authorization") or msg.header("Proxy-Authorization")
        if not hdr:
            return False
        username = sm.auth_param(hdr, "username")
        nonce = sm.auth_param(hdr, "nonce")
        uri = sm.auth_param(hdr, "uri")
        response = sm.auth_param(hdr, "response")
        qop = sm.auth_param(hdr, "qop")
        nc = sm.auth_param(hdr, "nc")
        cnonce = sm.auth_param(hdr, "cnonce")
        if username != self.username or nonce != self.nonce or not uri or not response:
            return False
        expected = sip_auth.digest_response(
            username,
            self.password,
            self.realm,
            method,
            uri,
            nonce,
            "auth" if qop else "",
            nc,
            cnonce,
        )
        return response == expected

    def _response(
        self,
        req: sm.SipMessage,
        code: int,
        reason: str,
        extra: str = "",
        body: str = "",
    ) -> str:
        to = req.header("To")
        if "tag=" not in to:
            to += f";tag={self.to_tag}"
        return (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {req.header('Via')}\r\n"
            f"From: {req.header('From')}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {req.header('Call-ID')}\r\n"
            f"CSeq: {req.header('CSeq')}\r\n"
            f"{extra}"
            f"Content-Length: {len(body)}\r\n\r\n{body}"
        )

    @staticmethod
    def _reason(code: int) -> str:
        return {
            403: "Forbidden",
            404: "Not Found",
            423: "Interval Too Brief",
            480: "Temporarily Unavailable",
            486: "Busy Here",
        }.get(code, "Error")
