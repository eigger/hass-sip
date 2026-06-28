"""SIP message / SDP parsing and small identifier helpers.

Pure-Python port of sip_message.cpp.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class SipMessage:
    is_request: bool = False
    method: str = ""
    request_uri: str = ""
    status_code: int = 0
    reason: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # lowercase name -> value
    body: str = ""

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    def has_header(self, name: str) -> bool:
        return name.lower() in self.headers


@dataclass
class SdpInfo:
    valid: bool = False
    connection_ip: str = ""
    audio_port: int = 0
    pcmu_pt: int = -1
    pcma_pt: int = -1
    telephone_event_pt: int = -1


# Compact header mapping (RFC 3261 Section 7.3.3)
_COMPACT_HEADERS = {
    "a": "accept-contact",
    "b": "referred-by",
    "c": "content-type",
    "e": "content-encoding",
    "f": "from",
    "i": "call-id",
    "k": "supported",
    "l": "content-length",
    "m": "contact",
    "o": "event",
    "r": "refer-to",
    "s": "subject",
    "t": "to",
    "u": "allow-events",
    "v": "via",
}


def parse_sip_message(raw: str) -> SipMessage:
    msg = SipMessage()
    header_end = raw.find("\r\n\r\n")
    if header_end == -1:
        head = raw
    else:
        head = raw[:header_end]
        msg.body = raw[header_end + 4:]

    first = True
    for line in head.split("\r\n"):
        if first:
            first = False
            if line.startswith("SIP/2.0 "):
                msg.is_request = False
                try:
                    msg.status_code = int(line[8:11])
                except ValueError:
                    msg.status_code = 0
                if len(line) > 12:
                    msg.reason = line[12:].strip()
            else:
                msg.is_request = True
                parts = line.split(" ")
                if parts:
                    msg.method = parts[0]
                    if len(parts) >= 2:
                        msg.request_uri = parts[1].strip()
            continue

        colon = line.find(":")
        if colon == -1:
            continue
        name = line[:colon].strip().lower()
        name = _COMPACT_HEADERS.get(name, name)
        value = line[colon + 1:].strip()
        # Keep the first occurrence (topmost Via, etc.).
        if name not in msg.headers:
            msg.headers[name] = value
    return msg


def parse_sdp(body: str) -> SdpInfo:
    info = SdpInfo()
    for raw_line in body.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("c=IN IP"):
            info.connection_ip = line.rsplit(" ", 1)[-1].strip()
        elif line.startswith("m=audio "):
            info.valid = True
            rest = line[8:]
            tokens = rest.split()
            if tokens:
                try:
                    info.audio_port = int(tokens[0])
                except ValueError:
                    info.audio_port = 0
            pts = tokens[2:] if len(tokens) > 2 else []
            if "0" in pts:
                info.pcmu_pt = 0
            if "8" in pts:
                info.pcma_pt = 8
        elif line.startswith("a=rtpmap:"):
            m = re.match(r"a=rtpmap:(\d+)", line)
            if not m:
                continue
            pt = int(m.group(1))
            lower = line.lower()
            if "telephone-event" in lower:
                info.telephone_event_pt = pt
            elif "pcmu" in lower:
                info.pcmu_pt = pt
            elif "pcma" in lower:
                info.pcma_pt = pt
    return info


def auth_param(header_value: str, key: str) -> str:
    """Extract a quoted-or-token parameter from an auth header value."""
    m = re.search(
        r"(?:^|[ ,\t])" + re.escape(key) + r"\s*=\s*(?:\"([^\"]*)\"|([^,\s]*))",
        header_value,
        re.IGNORECASE,
    )
    if not m:
        return ""
    return m.group(1) if m.group(1) is not None else (m.group(2) or "")


def gen_random_hex(num_bytes: int) -> str:
    return os.urandom(num_bytes).hex()


def gen_branch() -> str:
    return "z9hG4bK" + gen_random_hex(8)


def gen_tag() -> str:
    return gen_random_hex(6)


def gen_call_id(host: str) -> str:
    return gen_random_hex(12) + "@" + host
