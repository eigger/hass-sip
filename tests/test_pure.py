"""Unit tests for the framework-agnostic SIP core (no Home Assistant needed).

Run with either:
    py tests/test_pure.py
    py -m pytest tests/test_pure.py
"""
import os
import struct
import sys

# Load the standalone modules directly (they have no HA / relative-package deps).
_SIP = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "sip", "sip_client"
)
sys.path.insert(0, os.path.abspath(_SIP))

import g711  # noqa: E402
import sip_auth  # noqa: E402
import sip_message as sm  # noqa: E402

# rtp_session / sip_client use relative imports, so expose the directory as a
# throwaway package to load them without Home Assistant.
import asyncio  # noqa: E402
import importlib.util  # noqa: E402
import types  # noqa: E402

_PKG = "_sipcore"
_pkg_mod = types.ModuleType(_PKG)
_pkg_mod.__path__ = [os.path.abspath(_SIP)]
sys.modules[_PKG] = _pkg_mod


def _load_pkg_module(name):
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.{name}", os.path.join(os.path.abspath(_SIP), f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


rtp_session = _load_pkg_module("rtp_session")
try:
    sip_client = _load_pkg_module("sip_client")
except AttributeError:  # enum.StrEnum needs Python 3.11+ (CI runs 3.12+)
    sip_client = None


# ---------------------------------------------------------------- g711
def test_g711_silence_constants():
    assert g711.ulaw_to_linear(g711.linear_to_ulaw(0)) == 0
    assert abs(g711.alaw_to_linear(g711.linear_to_alaw(0))) <= 8


def test_g711_roundtrip_within_quant_error():
    for s in (-32000, -8000, -512, -1, 0, 1, 512, 8000, 32000):
        u = g711.ulaw_to_linear(g711.linear_to_ulaw(s))
        a = g711.alaw_to_linear(g711.linear_to_alaw(s))
        tol = abs(s) // 8 + 256  # companding quantisation error grows with magnitude
        assert abs(u - s) <= tol, (s, u)
        assert abs(a - s) <= tol, (s, a)
        # sign must be preserved for non-trivial samples
        if abs(s) > 512:
            assert (u < 0) == (s < 0)
            assert (a < 0) == (s < 0)


def test_g711_encode_decode_frame_length():
    pcm = struct.pack("<160h", *([1000] * 160))
    enc = g711.encode(pcm, 0)
    assert len(enc) == 160
    dec = g711.decode(enc, 0)
    assert len(dec) == 320  # back to s16le


# ------------------------------------------------------------ sip_message
def test_parse_response():
    raw = (
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 1.2.3.4:5060\r\n"
        "CSeq: 2 REGISTER\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    m = sm.parse_sip_message(raw)
    assert not m.is_request
    assert m.status_code == 200
    assert m.reason == "OK"
    assert m.header("cseq") == "2 REGISTER"


def test_parse_request():
    raw = (
        "INVITE sip:100@pbx SIP/2.0\r\n"
        "From: <sip:200@pbx>;tag=abc\r\n"
        "Call-ID: xyz@host\r\n\r\n"
    )
    m = sm.parse_sip_message(raw)
    assert m.is_request
    assert m.method == "INVITE"
    assert m.request_uri == "sip:100@pbx"
    assert m.header("call-id") == "xyz@host"


def test_parse_sdp():
    body = (
        "v=0\r\n"
        "c=IN IP4 192.168.0.5\r\n"
        "m=audio 4002 RTP/AVP 0 8 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
    )
    sdp = sm.parse_sdp(body)
    assert sdp.valid
    assert sdp.connection_ip == "192.168.0.5"
    assert sdp.audio_port == 4002
    assert sdp.pcmu_pt == 0
    assert sdp.pcma_pt == 8
    assert sdp.telephone_event_pt == 101


def test_auth_param():
    h = 'Digest realm="asterisk", nonce="abc123", qop="auth", algorithm=MD5'
    assert sm.auth_param(h, "realm") == "asterisk"
    assert sm.auth_param(h, "nonce") == "abc123"
    assert sm.auth_param(h, "qop") == "auth"
    assert sm.auth_param(h, "algorithm") == "MD5"
    assert sm.auth_param(h, "missing") == ""


# -------------------------------------------------------------- sip_auth
def test_digest_response_rfc2617_vector():
    # Canonical RFC 2617 §3.5 example.
    resp = sip_auth.digest_response(
        "Mufasa",
        "Circle Of Life",
        "testrealm@host.com",
        "GET",
        "/dir/index.html",
        "dcd98b7102dd2f0e8b11d0f600bfb0c093",
        "auth",
        "00000001",
        "0a4f113b",
    )
    assert resp == "6629fae49393a05397450978507c4ef1"


def test_digest_response_legacy_no_qop():
    # HA1:nonce:HA2 form must still compute deterministically.
    r1 = sip_auth.digest_response("u", "p", "r", "REGISTER", "sip:x", "n", "", "", "")
    r2 = sip_auth.digest_response("u", "p", "r", "REGISTER", "sip:x", "n", "", "", "")
    assert r1 == r2 and len(r1) == 32


# ------------------------------------------------------- SIP INFO DTMF
def test_info_dtmf_signal_forms():
    if sip_client is None:
        return
    p = sip_client._parse_info_dtmf
    assert p("application/dtmf-relay", "Signal=1\r\nDuration=160") == "1"
    assert p("application/dtmf-relay", "signal=#") == "#"
    assert p("application/dtmf-relay", "d=7") == "7"
    assert p("application/dtmf", "5") == "5"


def test_info_dtmf_numeric_event_codes():
    if sip_client is None:
        return
    # RFC 4733 event numbers, as sent by some gateways.
    p = sip_client._parse_info_dtmf
    assert p("application/dtmf-relay", "Signal=10") == "*"
    assert p("application/dtmf-relay", "Signal=11") == "#"
    assert p("application/dtmf-relay", "Signal=12") == "A"


def test_info_dtmf_ignores_other_content():
    if sip_client is None:
        return
    p = sip_client._parse_info_dtmf
    assert p("application/sdp", "Signal=1") is None
    assert p("application/dtmf-relay", "") is None
    assert p("application/dtmf-relay", "Duration=160") is None


# ------------------------------------------------------- RFC 2833 RX
def _te_packet(pt, marker, timestamp, event, seq=1):
    """Build one telephone-event RTP packet."""
    b1 = (0x80 if marker else 0) | pt
    return bytes([0x80, b1]) + struct.pack("!HII", seq, timestamp, 0x1234) + bytes(
        [event, 0x0A, 0x00, 0xA0]
    )


def _collect_dtmf(packets, dtmf_pt=101):
    async def run():
        session = rtp_session.RtpSession()
        session.dtmf_pt = dtmf_pt
        got = []
        session.on_dtmf = got.append
        for pkt in packets:
            session._receive_impl(pkt)
        return got

    return asyncio.run(run())


def test_rfc2833_rx_without_marker_bit():
    # Some ATAs never set the marker bit; one keypress must still fire once.
    got = _collect_dtmf([_te_packet(101, False, 1000, 1) for _ in range(5)])
    assert got == ["1"]


def test_rfc2833_rx_deduplicates_by_timestamp():
    packets = [_te_packet(101, False, 1000, 1) for _ in range(3)]
    packets += [_te_packet(101, False, 2000, 2) for _ in range(3)]
    assert _collect_dtmf(packets) == ["1", "2"]


def test_rfc2833_rx_marker_forces_new_event():
    # Same digit twice in a row shares no timestamp gap; marker separates them.
    packets = [_te_packet(101, True, 700, 3), _te_packet(101, True, 700, 3)]
    assert _collect_dtmf(packets) == ["3", "3"]


def test_rfc2833_rx_marker_style_unchanged():
    # Zoiper-style: marker on the first packet, repeats after it.
    packets = [_te_packet(101, True, 700, 3)]
    packets += [_te_packet(101, False, 700, 3) for _ in range(4)]
    assert _collect_dtmf(packets) == ["3"]


def test_rfc2833_rx_unnegotiated_payload_type():
    # telephone-event absent from the remote SDP: a 4-byte dynamic-PT payload
    # is still accepted rather than dropped.
    packets = [_te_packet(96, False, 500, 9) for _ in range(3)]
    assert _collect_dtmf(packets, dtmf_pt=-1) == ["9"]


def test_rfc2833_rx_does_not_swallow_audio():
    async def run():
        session = rtp_session.RtpSession()
        session.dtmf_pt = 101
        audio = []
        session.on_audio = audio.append
        pcmu = bytes([0x80, 0]) + struct.pack("!HII", 1, 100, 0x1234) + b"\xff" * 160
        session._receive_impl(pcmu)
        return audio

    audio = asyncio.run(run())
    # 160 bytes of G.711 decode to 160 16-bit samples.
    assert len(audio) == 1 and len(audio[0]) == 320


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
