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


g722 = _load_pkg_module("g722")
codecs = _load_pkg_module("codecs")
rtp_session = _load_pkg_module("rtp_session")
audio = _load_pkg_module("audio")
try:
    sip_client = _load_pkg_module("sip_client")
except AttributeError:  # enum.StrEnum needs Python 3.11+ (CI runs 3.12+)
    sip_client = None

# config_flow.py only needs voluptuous plus a couple of HA symbols it never
# calls (FlowResult, cv.*). Stub those directly with setdefault so this works
# standalone too — not just under pytest, where conftest.py already mocks
# `homeassistant`/`homeassistant.core`/`homeassistant.config_entries`.
from unittest.mock import MagicMock, patch  # noqa: E402
import voluptuous as vol  # noqa: E402

for _mod_name in (
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
):
    sys.modules.setdefault(_mod_name, MagicMock())
sys.modules.setdefault("homeassistant.data_entry_flow", MagicMock(FlowResult=dict))
sys.modules.setdefault("homeassistant.helpers.config_validation", MagicMock())

_COMPONENT = os.path.join(os.path.dirname(__file__), "..", "custom_components", "sip")
_CC_PKG = "custom_components.sip"
sys.modules.setdefault(
    "custom_components", types.ModuleType("custom_components")
)
_cc_sip_pkg = types.ModuleType(_CC_PKG)
_cc_sip_pkg.__path__ = [os.path.abspath(_COMPONENT)]
sys.modules[_CC_PKG] = _cc_sip_pkg


def _load_component_module(name):
    spec = importlib.util.spec_from_file_location(
        f"{_CC_PKG}.{name}", os.path.join(os.path.abspath(_COMPONENT), f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_CC_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_load_component_module("const")
config_flow = _load_component_module("config_flow")


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


# ------------------------------------------------------- ToneAudioSource
def test_tone_audio_source_byte_count_and_fade():
    async def instant_sleep(_delay=0, result=None):
        return result

    async def collect(rate, frame_bytes):
        chunks = []
        source = audio.ToneAudioSource()
        source.configure(rate, frame_bytes)
        await source.run(chunks.append, lambda: True)
        return b"".join(chunks)

    async def main():
        with patch.object(audio.asyncio, "sleep", instant_sleep):
            return await collect(8000, 320), await collect(16000, 640)

    pcm8, pcm16 = asyncio.run(main())
    assert len(pcm8) == 8000 * 120 // 1000 * 2
    assert len(pcm16) == 16000 * 120 // 1000 * 2
    samples8 = struct.unpack(f"<{len(pcm8) // 2}h", pcm8)
    samples16 = struct.unpack(f"<{len(pcm16) // 2}h", pcm16)
    assert abs(samples8[0]) <= 1
    assert abs(samples8[-1]) <= 1
    assert abs(samples16[0]) <= 1
    assert abs(samples16[-1]) <= 1
    assert max(abs(s) for s in samples8) > 1000
    assert max(abs(s) for s in samples16) > 1000


def test_tone_pcm_cache_reuses_rendered_bytes():
    audio._TONE_PCM_CACHE.clear()
    kwargs = {
        "sample_rate": 8000,
        "freq_hz": 880,
        "duration_ms": 120,
        "amplitude": 0.25,
        "fade_ms": 10,
    }
    first = audio._render_tone_pcm(**kwargs)
    second = audio._render_tone_pcm(**kwargs)
    assert first is second
    audio._TONE_PCM_CACHE.clear()


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
    assert sdp.offered_pts == {0, 8, 101}


def test_parse_sdp_offered_pts_from_rtpmap_only():
    # Dynamic PT advertised only via rtpmap still lands in offered_pts.
    body = (
        "m=audio 4002 RTP/AVP 96 97\r\n"
        "a=rtpmap:96 PCMU/8000\r\n"
        "a=rtpmap:97 telephone-event/8000\r\n"
    )
    sdp = sm.parse_sdp(body)
    assert sdp.offered_pts == {96, 97}
    assert sdp.pcmu_pt == 96
    assert sdp.telephone_event_pt == 97


# -------------------------------------------------------------- codecs
def _sdp(pts, *, pcmu=-1, pcma=-1, g722=-1):
    info = sm.SdpInfo(offered_pts=set(pts), pcmu_pt=pcmu, pcma_pt=pcma, g722_pt=g722)
    return info


def test_codecs_choose_preference():
    assert codecs.choose(_sdp({9, 0, 8}, pcmu=0, pcma=8, g722=9)).payload_type == 9
    assert codecs.choose(_sdp({0, 8}, pcmu=0, pcma=8)).payload_type == 0
    assert codecs.choose(_sdp({8}, pcma=8)).payload_type == 8
    assert codecs.choose(_sdp({101})).payload_type == 0  # default PCMU
    assert codecs.choose(_sdp(set())).payload_type == 0


def test_codecs_choose_dynamic_pt_by_name():
    # a=rtpmap:96 PCMU/8000 — static PT 0 absent; must bind PCMU to 96.
    chosen = codecs.choose(_sdp({96, 97}, pcmu=96))
    assert chosen.name == "PCMU"
    assert chosen.payload_type == 96
    chosen = codecs.choose(_sdp({98}, pcma=98))
    assert chosen.name == "PCMA" and chosen.payload_type == 98
    chosen = codecs.choose(_sdp({110}, g722=110))
    assert chosen.name == "G722" and chosen.payload_type == 110


def test_codecs_sdp_offer_includes_g722():
    assert codecs.sdp_media_line(7078) == "m=audio 7078 RTP/AVP 9 0 8 101\r\n"
    assert codecs.sdp_rtpmaps() == (
        "a=rtpmap:9 G722/8000\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
    )
    # RFC 3551: G.722 rtpmap clock is 8000, not 16000.
    assert codecs.G722.clock_rate == 8000
    assert codecs.G722.sample_rate == 16000
    assert codecs.G722.pcm_frame_bytes == 640
    assert codecs.G722.ts_increment == 160


def test_codecs_sdp_answer_only_negotiated():
    only = codecs.PCMU
    assert codecs.sdp_media_line(7078, only=only) == "m=audio 7078 RTP/AVP 0 101\r\n"
    assert codecs.sdp_rtpmaps(only=only) == (
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
    )
    dyn = codecs.PCMU.with_payload_type(96)
    assert "96" in codecs.sdp_media_line(7078, only=dyn)
    assert "PCMU/8000" in codecs.sdp_rtpmaps(only=dyn)


def test_local_sdp_offer_and_answer():
    if sip_client is None:
        return

    async def run():
        cfg = sip_client.SipConfig(server="pbx.example", local_rtp_port=7078)
        client = sip_client.SipClient(cfg)
        client._local_ip = "192.0.2.1"
        offer = client._local_sdp()
        client._codec = codecs.PCMU
        answer = client._local_sdp(only=client.codec)
        return offer, answer

    offer, answer = asyncio.run(run())
    assert "RTP/AVP 9 0 8 101" in offer
    assert "a=rtpmap:9 G722/8000" in offer
    assert "RTP/AVP 0 101" in answer
    assert "G722" not in answer
    assert "PCMA" not in answer


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


# ---------------------------------------------- rate-aware RTP (Stage 2)
class _FakeTransport:
    def __init__(self):
        self.packets = []

    def sendto(self, packet, addr):
        self.packets.append(packet)


def test_rtp_pcmu_frame_size_and_timestamp():
    async def run():
        session = rtp_session.RtpSession()
        session.set_codec(codecs.PCMU)
        session._transport = _FakeTransport()
        session.set_remote("127.0.0.1", 4000)
        session._timestamp = 1000
        session._seq = 1
        session._first_packet = False
        assert session._pcm_frame_bytes == 320
        assert session._ts_increment == 160
        assert len(session._silence_frame()) == 320

        frame = struct.pack("<160h", *([0] * 160))
        session._send_audio_packet(frame)
        session._send_audio_packet(frame)
        return session._transport.packets, session._timestamp

    packets, ts = asyncio.run(run())
    assert len(packets) == 2
    # 12-byte RTP header + 160-byte G.711 payload
    assert all(len(p) == 172 for p in packets)
    assert packets[0][1] & 0x7F == 0  # PCMU
    ts0 = int.from_bytes(packets[0][4:8], "big")
    ts1 = int.from_bytes(packets[1][4:8], "big")
    assert ts1 - ts0 == 160
    assert ts == 1000 + 320


def test_rtp_set_codec_resets_encoder():
    async def run():
        session = rtp_session.RtpSession()
        session.set_codec(codecs.PCMA)
        assert session.payload_type == 8
        assert session._pcm_frame_bytes == 320
        assert session._ts_increment == 160
        # Encode path uses the session encoder (PCMA).
        session._transport = _FakeTransport()
        session.set_remote("127.0.0.1", 4000)
        session._timestamp = 0
        session._first_packet = False
        session._send_audio_packet(b"\x00" * 320)
        return session._transport.packets[0]

    pkt = asyncio.run(run())
    assert pkt[1] & 0x7F == 8


def test_rtp_decoder_cache_reuses_stateful_decoder():
    async def run():
        session = rtp_session.RtpSession()
        session.set_codec(codecs.G722)
        # Off-PT G.711 packet still gets a cached decoder (not recreated).
        d1 = session._decoder_for(0)
        d2 = session._decoder_for(0)
        assert d1 is d2
        assert session._decoder_for(9) is session._decode
        return True

    assert asyncio.run(run())


def test_rtp_flush_tx_buffer():
    async def run():
        session = rtp_session.RtpSession()
        session.set_codec(codecs.PCMU)
        session._transport = MagicMock()
        session.push_tx_audio(b"\x00" * 640)
        assert session._tx_buffer
        session.flush_tx_buffer()
        assert not session._tx_buffer

    asyncio.run(run())


def test_rtp_g722_frame_size_and_timestamp():
    async def run():
        session = rtp_session.RtpSession()
        session.set_codec(codecs.G722)
        session._transport = _FakeTransport()
        session.set_remote("127.0.0.1", 4000)
        session._timestamp = 5000
        session._first_packet = False
        assert session._pcm_frame_bytes == 640
        assert session._ts_increment == 160
        assert len(session._silence_frame()) == 640

        frame = b"\x00" * 640
        session._send_audio_packet(frame)
        session._send_audio_packet(frame)
        return session._transport.packets

    packets = asyncio.run(run())
    assert all(len(p) == 172 for p in packets)  # 12 + 160 payload
    assert packets[0][1] & 0x7F == 9
    ts0 = int.from_bytes(packets[0][4:8], "big")
    ts1 = int.from_bytes(packets[1][4:8], "big")
    assert ts1 - ts0 == 160  # RFC 3551 clock quirk


# --------------------------------------------------------------- g722
def test_g722_frame_lengths():
    pcm = struct.pack("<320h", *([1000] * 320))
    enc = g722.G722Encoder()
    dec = g722.G722Decoder()
    payload = enc.encode(pcm)
    assert len(payload) == 160
    out = dec.decode(payload)
    assert len(out) == 640


def test_g722_bitexact_reference_vectors():
    """Pin encode output against sippy/libg722 (ITU-verified) reference frames.

    Fixtures were cross-checked with the PyPI ``G722`` C extension on the same
    PCM inputs (silence → 440 Hz tone → full-scale square), stateful across
    frames. A table typo will fail this test even if correlation still looks ok.
    """
    import math

    sr = 16000
    n = 320
    silence = struct.pack("<320h", *([0] * n))
    tone = struct.pack(
        "<320h",
        *[int(10000 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(n)],
    )
    square = struct.pack(
        "<320h",
        *[32000 if (i // 40) % 2 == 0 else -32000 for i in range(n)],
    )
    # Full 160-byte reference payloads (hex), stateful encode of silence→tone→square.
    ref = [
        bytes.fromhex(
            "fafafafafafafafafafafafafafafafafafafafafafafafafafafafafafafafa"
            "fafafafafafafafafafafafaf7f7f7f7f7f7f7f8f7faf7f8f8fafaf7f7f8f7fa"
            "f8f7faf8f7faf7f8f8fadbf0f8f8f8f8fadbf1f8f8fafaf8def3faf8f8fafa"
            "def0fafaf8f8fadbf1fafaf8f8fadcf2faf8f8fafadef0fafaf8f8fad8f3fa"
            "f8f8fadef3faf8f8fadef3faf8f8fadef2faf8f8fadef2faf8f8fadef2faf8"
            "f8fade"
        ),
        bytes.fromhex(
            "f2992688228ca0a060a8d8d6d99454dbb17ed59d5effbdecea7af9f9dfdbdeb8"
            "5bd75a95559bf5f5b6f3f674f5b576fffed7d9d3d9d6d9ddde77bcf0aff9eeb6"
            "77fedddfd7985298d5d8debb78f3b46ef4f0f2dff7d7fe53d895d49addfdfab6"
            "73f4edb4f2fbdff9d75bd696d79adc9a75b6f8eef5f0f27bfcbbd7dd5493fed1"
            "5bbadef6b6f2f46fb9f4f65abdd85496d9d6dbfddff5b7f772b2f4f3dc7fdfdc"
        ),
        bytes.fromhex(
            "923f89248420a0049f2ab7fd76777ef973fff6f73db43094228404a0268bd339"
            "df5edcdd5dd8da7cdd1b98308520a004caab6d745ef75c5ef2fbfa7b7db83191"
            "228404e0e5538d5976597dfbd8dbff5c5b3898328520a04449f268ed587dfcfb"
            "fb7a74f87b9e3094228404e0e6548c567bd87a7adadc577adb3a96308720a0c5"
            "4ef96c6c78f8fedbf475777d78b83494228404e1a95f50515f5efbdf7cddd77f"
        ),
    ]
    enc = g722.G722Encoder()
    for pcm, expected in zip((silence, tone, square), ref):
        assert enc.encode(pcm) == expected

    # Decode path: same payloads through a fresh decoder must match reference PCM
    # prefixes (first 16 samples of each frame) from the C implementation.
    dec_ref_prefix = [
        bytes.fromhex("0000ffffffff00000000ffff00000000ffffffff000001000200020001000100"),
        bytes.fromhex("0100010001000000ffffffff000002000100fdfffeff0300fdfff2fffbff1600"),
        bytes.fromhex("dc24932628278926c324eb21101e3d19da130b0e4d07baff05fa42f5d5ecdee3"),
    ]
    dec = g722.G722Decoder()
    for payload, prefix in zip(ref, dec_ref_prefix):
        out = dec.decode(payload)
        assert out[:32] == prefix


def test_g722_roundtrip_correlates():
    import math

    sr = 16000
    n = 320 * 20  # 400 ms
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(n)]
    pcm = struct.pack("<%dh" % n, *samples)
    enc = g722.G722Encoder()
    dec = g722.G722Decoder()
    # Encode/decode in 20 ms frames to exercise state continuity.
    out = bytearray()
    for off in range(0, len(pcm), 640):
        out.extend(dec.decode(enc.encode(pcm[off : off + 640])))
    out_s = struct.unpack("<%dh" % (len(out) // 2), out)
    # The transmit+receive QMF pair delays by ~22 samples; align before correlating.
    qmf_delay = 22
    a = samples[640 : 640 + 4000]
    b = out_s[640 + qmf_delay : 640 + qmf_delay + 4000]
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    corr = dot / (na * nb)
    assert corr > 0.95, corr


def test_g722_perf_under_budget():
    import math
    import time

    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(320)]
    pcm = struct.pack("<320h", *samples)
    enc = g722.G722Encoder()
    dec = g722.G722Decoder()
    for _ in range(5):
        dec.decode(enc.encode(pcm))
    t0 = time.perf_counter()
    n = 50
    for _ in range(n):
        dec.decode(enc.encode(pcm))
    us = (time.perf_counter() - t0) / n * 1e6
    # Soft budget: must stay well inside the 20 ms frame (headroom for Pi).
    assert us < 15000, f"encode+decode took {us:.0f} µs/frame"


# ------------------------------------------------------- assist bridge
def _setup_assist_deps():
    """Install minimal HA mocks so assist.py can be loaded."""
    from unittest.mock import AsyncMock

    class _PipelineEventType:
        RUN_START = "run-start"
        STT_END = "stt-end"
        INTENT_END = "intent-end"
        TTS_END = "tts-end"
        ERROR = "error"

    class _PipelineStage:
        STT = "stt"
        TTS = "tts"

    class _PipelineEvent:
        def __init__(self, event_type, data=None):
            self.type = event_type
            self.data = data if data is not None else {}

    class _AudioSettings:
        """Records the kwargs assist.py builds, so tests can assert on them."""

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    mock_ap = MagicMock()
    mock_ap.AudioSettings = _AudioSettings
    mock_ap.PipelineEventType = _PipelineEventType
    mock_ap.PipelineStage = _PipelineStage
    mock_ap.PipelineEvent = _PipelineEvent

    pipeline_obj = MagicMock()
    pipeline_obj.id = "default"

    def _get_pipeline(hass, pipeline_id):
        return pipeline_obj

    mock_ap.async_get_pipeline = _get_pipeline
    mock_ap.async_pipeline_from_audio_stream = AsyncMock()

    mock_components = sys.modules.get("homeassistant.components")
    if not isinstance(mock_components, MagicMock):
        mock_components = MagicMock()
        sys.modules["homeassistant.components"] = mock_components
    mock_components.assist_pipeline = mock_ap
    sys.modules["homeassistant.components.assist_pipeline"] = mock_ap

    mock_stt = MagicMock()

    class _SpeechMetadata:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    mock_stt.SpeechMetadata = _SpeechMetadata
    mock_stt.AudioFormats = MagicMock(WAV="wav")
    mock_stt.AudioCodecs = MagicMock(PCM="pcm")
    mock_stt.AudioBitRates = MagicMock(BITRATE_16=16)
    mock_stt.AudioSampleRates = MagicMock(SAMPLERATE_16000=16000)
    mock_stt.AudioChannels = MagicMock(CHANNEL_MONO=1)
    mock_components.stt = mock_stt
    sys.modules["homeassistant.components.stt"] = mock_stt

    mock_tts = MagicMock()
    mock_tts.async_get_stream = MagicMock(return_value=None)
    mock_components.tts = mock_tts
    sys.modules["homeassistant.components.tts"] = mock_tts

    mock_ffmpeg = MagicMock()
    mock_ffmpeg.get_ffmpeg_manager = MagicMock(
        return_value=MagicMock(binary="/usr/bin/ffmpeg")
    )
    mock_components.ffmpeg = mock_ffmpeg
    sys.modules["homeassistant.components.ffmpeg"] = mock_ffmpeg

    _sip_client_pkg = f"{_CC_PKG}.sip_client"
    if _sip_client_pkg not in sys.modules:
        sc_pkg = types.ModuleType(_sip_client_pkg)
        sc_pkg.__path__ = [os.path.join(os.path.abspath(_COMPONENT), "sip_client")]
        sys.modules[_sip_client_pkg] = sc_pkg

    _load_component_module("helpers")
    assist = _load_component_module("assist")
    return assist, mock_ap, _PipelineEventType, _PipelineEvent


_ASSIST_CTX = None


def _assist_ctx():
    global _ASSIST_CTX
    if _ASSIST_CTX is None:
        _ASSIST_CTX = _setup_assist_deps()
    return _ASSIST_CTX


def _run_bridge_session(bridge):
    async def _wait():
        bridge.start()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(_wait())


def test_assist_listening_gate():
    assist_mod, _, _, _ = _assist_ctx()
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
    )
    bridge._listening = False
    bridge.audio_stream = assist_mod.AssistAudioStream()
    bridge.write(b"\x00\x00")
    assert bridge.audio_stream.queue.empty()

    bridge._listening = True
    bridge.write(b"\x00\x00")
    assert not bridge.audio_stream.queue.empty()


def test_assist_queue_isolation_per_turn():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    streams = []

    async def mock_pipeline(hass, **kwargs):
        streams.append(kwargs["stt_stream"])
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=2,
    )
    _run_bridge_session(bridge)
    assert len(streams) == 2
    assert streams[0] is not streams[1]


def test_assist_silent_turns_end_session():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turn_count = 0
    done_calls = []

    async def mock_pipeline(hass, **kwargs):
        nonlocal turn_count
        turn_count += 1
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=lambda: done_calls.append(1),
        max_silent_turns=2,
    )
    _run_bridge_session(bridge)
    assert turn_count == 2
    assert len(done_calls) == 1


def test_assist_conversation_id_carried_across_turns():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    conv_ids = []

    async def mock_pipeline(hass, **kwargs):
        conv_ids.append(kwargs.get("conversation_id"))
        cb = kwargs["event_callback"]
        if len(conv_ids) == 1:
            cb(PE(PET.RUN_START, {"conversation_id": "conv-abc"}))
        cb(PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=2,
    )
    _run_bridge_session(bridge)
    assert conv_ids[1] == "conv-abc"


def test_assist_playback_done_unblocks_next_turn():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turn_count = 0

    async def mock_pipeline(hass, **kwargs):
        nonlocal turn_count
        turn_count += 1
        cb = kwargs["event_callback"]
        if turn_count == 1:
            cb(PE(PET.RUN_START, {"conversation_id": "c1"}))
        else:
            cb(PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=2,
    )

    async def run():
        bridge.start()
        await asyncio.sleep(0.05)
        bridge._speaking = True
        bridge._tx_wait = "tts"
        bridge._tx_done.clear()
        bridge.on_playback_done()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(run())
    assert turn_count >= 2


def test_assist_playback_timeout_continues():
    assist_mod, _, _, _ = _assist_ctx()

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            stop_audio_fn=MagicMock(),
        )
        bridge._speaking = True
        bridge._tts_epoch = 4
        stale_task = asyncio.create_task(asyncio.sleep(60))
        bridge._background_tasks.add(stale_task)
        real_timeout = asyncio.timeout

        def short_timeout(delay):
            return real_timeout(0.05)

        import unittest.mock as um

        with um.patch("asyncio.timeout", short_timeout):
            await bridge._wait_playback_done()
        assert bridge._speaking is False
        assert bridge._tts_epoch == 5
        assert stale_task.cancelled()

    asyncio.run(run())


def test_assist_playback_timeout_stale_tts_does_not_play():
    assist_mod, _, _, _ = _assist_ctx()
    play_calls = []
    release = asyncio.Event()

    async def slow_stream():
        await release.wait()
        yield b"RIFF...."

    stream = MagicMock()
    stream.async_stream_result = slow_stream

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=lambda src: play_calls.append(type(src).__name__),
            on_done_fn=MagicMock(),
            stop_audio_fn=MagicMock(),
        )
        bridge._speaking = True
        task = asyncio.create_task(bridge._play_tts_stream(stream, epoch=1))
        bridge._background_tasks.add(task)
        await asyncio.sleep(0.02)
        real_timeout = asyncio.timeout

        def short_timeout(delay):
            return real_timeout(0.05)

        with patch("asyncio.timeout", short_timeout):
            await bridge._wait_playback_done()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert not play_calls


def test_assist_playback_timeout_does_not_stop_long_playback():
    assist_mod, _, _, _ = _assist_ctx()
    stop_calls = []

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            stop_audio_fn=lambda **kw: stop_calls.append(kw),
        )
        bridge._speaking = True
        real_timeout = asyncio.timeout

        def short_timeout(delay):
            return real_timeout(0.05)

        with patch("asyncio.timeout", short_timeout):
            await bridge._wait_playback_done()
        assert not stop_calls
        assert bridge._speaking is False

    asyncio.run(run())


def test_assist_tts_wait_timeout_is_generous():
    """Real calls play 39-52s responses; the safety-net timeout must clear that
    comfortably or every turn logs a spurious warning (seen at the old 30s)."""
    assist_mod, _, _, _ = _assist_ctx()
    assert assist_mod._TTS_WAIT_TIMEOUT_SECONDS >= 120
    assert assist_mod._TONE_WAIT_TIMEOUT_SECONDS == 3


def test_assist_wait_playback_done_waits_for_media_idle():
    assist_mod, _, _, _ = _assist_ctx()
    polls: list[int] = []
    media_state = {"playing": True}

    def media_fn() -> bool:
        polls.append(1)
        if len(polls) >= 3:
            media_state["playing"] = False
        return media_state["playing"]

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            media_playing_fn=media_fn,
        )
        bridge._speaking = True
        bridge._tx_wait = "tts"
        bridge._tx_done.set()
        await bridge._wait_playback_done()
        assert len(polls) >= 3
        assert bridge._speaking is False

    asyncio.run(run())


def test_assist_turn_tone_defers_until_media_idle():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    play_calls: list[str] = []
    media_state = {"playing": True}

    async def mock_pipeline(hass, **kwargs):
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=lambda src: play_calls.append(type(src).__name__),
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        turn_tone=True,
        media_playing_fn=lambda: media_state["playing"],
    )

    async def run():
        bridge.start()
        await asyncio.sleep(0.05)
        assert not play_calls
        media_state["playing"] = False
        bridge.on_playback_done()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(run())
    assert play_calls == ["ToneAudioSource"]


def test_assist_wait_for_tx_idle_times_out():
    assist_mod, _, _, _ = _assist_ctx()

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            media_playing_fn=lambda: True,
        )
        t0 = asyncio.get_running_loop().time()
        with patch.object(assist_mod, "_TX_IDLE_TIMEOUT_SECONDS", 0.05):
            await bridge._wait_for_tx_idle()
        assert asyncio.get_running_loop().time() - t0 < 1.0

    asyncio.run(run())


def test_assist_close_during_session():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    done_calls = []

    async def mock_pipeline(hass, **kwargs):
        await asyncio.sleep(1)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=lambda: done_calls.append(1),
        max_silent_turns=99,
    )

    async def run():
        bridge.start()
        await asyncio.sleep(0.02)
        bridge.close()
        if bridge.session_task:
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await bridge.session_task

    asyncio.run(run())
    assert len(done_calls) == 1


def test_assist_consecutive_errors_end_session():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turn_count = 0

    async def mock_pipeline(hass, **kwargs):
        nonlocal turn_count
        turn_count += 1
        kwargs["event_callback"](PE(PET.ERROR, {"code": "cloud-auth-failed"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    original_backoff = assist_mod._ERROR_TURN_BACKOFF_SECONDS
    assist_mod._ERROR_TURN_BACKOFF_SECONDS = 0
    try:
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
        )
        _run_bridge_session(bridge)
        assert turn_count == assist_mod._MAX_CONSECUTIVE_ERRORS
    finally:
        assist_mod._ERROR_TURN_BACKOFF_SECONDS = original_backoff


def test_assist_continue_conversation_resets_silent_streak():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turn_count = 0

    async def mock_pipeline(hass, **kwargs):
        nonlocal turn_count
        turn_count += 1
        cb = kwargs["event_callback"]
        if turn_count == 1:
            cb(PE(PET.INTENT_END, {"intent_output": {"continue_conversation": True}}))
        cb(PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=2,
    )
    _run_bridge_session(bridge)
    assert turn_count == 3


def test_assist_max_turns_waits_for_final_playback():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value
    mock_tts.async_get_stream.return_value = MagicMock()
    try:
        async def mock_pipeline(hass, **kwargs):
            cb = kwargs["event_callback"]
            cb(PE(PET.RUN_START, {"conversation_id": "c1"}))
            cb(PE(PET.TTS_END, {"tts_output": {"token": "tok"}}))

        mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            max_turns=1,
        )

        async def run():
            bridge.start()
            for _ in range(50):
                await asyncio.sleep(0.01)
                if (
                    bridge._speaking
                    and bridge.session_task
                    and not bridge.session_task.done()
                ):
                    break
            assert bridge._speaking
            assert bridge.session_task is not None
            assert not bridge.session_task.done()
            bridge.on_playback_done()
            await bridge.session_task

        asyncio.run(run())
    finally:
        mock_tts.async_get_stream.return_value = original_get_stream


def test_assist_tts_failure_signals_playback_done():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value

    failing_stream = MagicMock()

    async def failing_result():
        raise RuntimeError("TTS fetch failed")
        yield b""  # pragma: no cover

    failing_stream.async_stream_result = failing_result
    mock_tts.async_get_stream.return_value = failing_stream

    async def mock_pipeline(hass, **kwargs):
        cb = kwargs["event_callback"]
        cb(PE(PET.TTS_END, {"tts_output": {"token": "tok"}}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_turns=1,
    )

    async def run():
        bridge.start()
        await asyncio.wait_for(bridge._tx_done.wait(), timeout=2)
        assert bridge._tx_done.is_set()
        if bridge.session_task:
            await bridge.session_task

    try:
        asyncio.run(run())
    finally:
        mock_tts.async_get_stream.return_value = original_get_stream


def test_assist_barge_in_triggers_stop_and_preroll():
    assist_mod, _, _, _ = _assist_ctx()
    original_min = assist_mod._VAD_MIN_SPEECH_FRAMES
    original_micro_vad = assist_mod.MicroVad
    assist_mod._VAD_MIN_SPEECH_FRAMES = 2

    class _StubVad:
        def Process10ms(self, frame: bytes) -> float:
            return 0.9

    assist_mod.MicroVad = lambda: _StubVad()
    try:
        stop_calls = []
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            barge_in=True,
            stop_audio_fn=lambda *, flush=False: stop_calls.append(flush),
        )
        bridge._speaking = True
        frame = b"\x00\x01" * (assist_mod._VAD_FRAME_BYTES // 2)
        for _ in range(5):
            bridge.write(frame)
        assert stop_calls == [True]
        assert bridge._barge_in_preroll
        assert bridge._tx_done.is_set()
        assert bridge._post_barge_in_capture
    finally:
        assist_mod._VAD_MIN_SPEECH_FRAMES = original_min
        assist_mod.MicroVad = original_micro_vad


def test_assist_barge_in_disabled_without_micro_vad():
    assist_mod, _, _, _ = _assist_ctx()
    original_micro_vad = assist_mod.MicroVad
    assist_mod.MicroVad = None
    try:
        stop_calls = []
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            barge_in=True,
            stop_audio_fn=lambda: stop_calls.append(1),
        )
        assert bridge.barge_in is False
        bridge._speaking = True
        loud = struct.pack("<160h", *([8000] * 160))
        for _ in range(5):
            bridge.write(loud)
        assert not stop_calls
    finally:
        assist_mod.MicroVad = original_micro_vad


def test_assist_barge_in_cancels_inflight_tts():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value
    original_min = assist_mod._VAD_MIN_SPEECH_FRAMES
    original_micro_vad = assist_mod.MicroVad
    play_calls = []

    class _StubVad:
        def Process10ms(self, frame: bytes) -> float:
            return 0.9

    assist_mod.MicroVad = lambda: _StubVad()
    assist_mod._VAD_MIN_SPEECH_FRAMES = 2

    async def slow_stream():
        await asyncio.sleep(0.15)
        yield b"RIFF...."

    stream = MagicMock()
    stream.async_stream_result = slow_stream
    mock_tts.async_get_stream.return_value = stream

    try:

        async def mock_pipeline(hass, **kwargs):
            cb = kwargs["event_callback"]
            cb(PE(PET.TTS_END, {"tts_output": {"token": "tok"}}))

        mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=lambda src: play_calls.append(1),
            on_done_fn=MagicMock(),
            barge_in=True,
            stop_audio_fn=MagicMock(),
            max_turns=1,
            max_silent_turns=99,
        )

        async def run():
            bridge.start()
            await asyncio.sleep(0.02)
            frame = b"\x00\x01" * (assist_mod._VAD_FRAME_BYTES // 2)
            for _ in range(5):
                bridge.write(frame)
            if bridge.session_task:
                await asyncio.wait_for(bridge.session_task, timeout=2)

        asyncio.run(run())
        assert not play_calls
    finally:
        assist_mod._VAD_MIN_SPEECH_FRAMES = original_min
        assist_mod.MicroVad = original_micro_vad
        mock_tts.async_get_stream.return_value = original_get_stream


def test_assist_post_barge_in_capture_extends_preroll():
    assist_mod, _, _, _ = _assist_ctx()
    original_min = assist_mod._VAD_MIN_SPEECH_FRAMES
    original_micro_vad = assist_mod.MicroVad

    class _StubVad:
        def Process10ms(self, frame: bytes) -> float:
            return 0.9

    assist_mod.MicroVad = lambda: _StubVad()
    assist_mod._VAD_MIN_SPEECH_FRAMES = 2
    try:
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            barge_in=True,
            stop_audio_fn=MagicMock(),
        )
        bridge._speaking = True
        frame = b"\x00\x01" * (assist_mod._VAD_FRAME_BYTES // 2)
        for _ in range(5):
            bridge.write(frame)
        assert bridge._post_barge_in_capture
        extra = b"\x02\x03" * 320
        bridge.write(extra)
        assert bridge._ring_buffer
    finally:
        assist_mod._VAD_MIN_SPEECH_FRAMES = original_min
        assist_mod.MicroVad = original_micro_vad


def test_assist_preroll_injected_into_stream():
    assist_mod, _, _, _ = _assist_ctx()
    stream = assist_mod.AssistAudioStream()
    stream.inject_preroll(b"\x00" * 640)
    assert stream.queue.qsize() == 1


def test_assist_done_guard_skips_superseded_bridge():
    """Regression: superseded bridge on_done must not clobber the active sink."""
    state = {"assist_bridge": None, "sink": None}

    def make_on_done(bridge):
        def on_assist_done() -> None:
            if state["assist_bridge"] is not bridge:
                return
            state["sink"] = "null"
            state["assist_bridge"] = None

        return on_assist_done

    bridge_a = object()
    bridge_b = object()
    state["assist_bridge"] = bridge_b
    state["sink"] = "bridge_b"
    make_on_done(bridge_a)()
    assert state["assist_bridge"] is bridge_b
    assert state["sink"] == "bridge_b"


def test_call_ended_restores_sink_when_assist_bridge_cleared():
    """Regression: call end must restore sink even if on_assist_done guard no-ops."""
    state = {"assist_bridge": object(), "sink": "bridge"}

    def on_call_ended() -> None:
        if state["assist_bridge"] is not None:
            state["assist_bridge"] = None
            state["sink"] = "null"

    on_call_ended()
    assert state["assist_bridge"] is None
    assert state["sink"] == "null"


def _run_one_turn(assist_mod, mock_ap, PET, PE, **bridge_kwargs):
    """Run a single pipeline turn and return the kwargs it was called with."""
    captured = {}

    async def mock_pipeline(hass, **kwargs):
        captured.update(kwargs)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        **bridge_kwargs,
    )
    _run_bridge_session(bridge)
    return captured


def test_assist_audio_settings_omitted_by_default():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    captured = _run_one_turn(assist_mod, mock_ap, PET, PE)
    # None keeps Home Assistant's own AudioSettings() defaults.
    assert captured["audio_settings"] is None


def test_assist_audio_settings_passed_when_tuned():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    captured = _run_one_turn(
        assist_mod, mock_ap, PET, PE, silence_seconds=1.2, noise_suppression=3
    )
    settings = captured["audio_settings"]
    assert settings is not None
    assert settings.kwargs == {"silence_seconds": 1.2, "noise_suppression_level": 3}


def test_assist_audio_settings_partial_options():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    captured = _run_one_turn(assist_mod, mock_ap, PET, PE, silence_seconds=1.5)
    assert captured["audio_settings"].kwargs == {"silence_seconds": 1.5}

    captured = _run_one_turn(assist_mod, mock_ap, PET, PE, noise_suppression=2)
    assert captured["audio_settings"].kwargs == {"noise_suppression_level": 2}


def test_assist_stt_and_intent_text_extractors():
    assist_mod, _, _, _ = _assist_ctx()
    assert assist_mod._stt_text({"stt_output": {"text": "turn on the light"}}) == (
        "turn on the light"
    )
    assert assist_mod._stt_text(None) == ""
    assert assist_mod._stt_text({}) == ""
    assert assist_mod._stt_text({"stt_output": None}) == ""

    intent_output = {"response": {"speech": {"plain": {"speech": "Done."}}}}
    assert assist_mod._intent_speech(intent_output) == "Done."
    assert assist_mod._intent_speech({}) == ""
    assert assist_mod._intent_speech({"response": {"speech": None}}) == ""


def test_assist_stt_end_event_is_logged_without_error():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turns = []

    async def mock_pipeline(hass, **kwargs):
        cb = kwargs["event_callback"]
        turns.append(1)
        cb(PE(PET.STT_END, {"stt_output": {"text": "hello"}}))
        cb(PE(PET.INTENT_END, {"intent_output": {"response": {}}}))
        cb(PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=1,
    )
    _run_bridge_session(bridge)
    assert turns == [1]
    assert bridge._turn_index == 1


def test_assist_turn_tone_playback_done_does_not_set_tts_event():
    """Trap 1: tone completion must not unblock TTS playback wait."""
    assist_mod, _, _, _ = _assist_ctx()
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        turn_tone=True,
    )
    bridge._tx_wait = "tone"
    bridge._tx_done.clear()
    bridge.on_playback_done()
    assert bridge._tx_done.is_set()

    bridge._tx_wait = "tts"
    bridge._tx_done.clear()
    bridge.on_playback_done()
    assert bridge._tx_done.is_set()

    bridge._tx_wait = None
    bridge._tx_done.clear()
    bridge.on_playback_done()
    assert not bridge._tx_done.is_set()


def test_assist_stale_tone_done_ignored_after_wait_cleared():
    assist_mod, _, _, _ = _assist_ctx()
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        turn_tone=True,
    )
    bridge._tx_wait = None
    bridge._tx_done.clear()
    bridge.on_playback_done()
    assert not bridge._tx_done.is_set()


def test_assist_turn_tone_listening_after_playback_done():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    listening_at_pipeline = []
    play_calls = []

    async def mock_pipeline(hass, **kwargs):
        listening_at_pipeline.append(bridge._listening)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    def play_source(src):
        play_calls.append(src)

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=play_source,
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        turn_tone=True,
    )

    async def run():
        bridge.start()
        await asyncio.sleep(0.05)
        assert bridge._listening is False
        assert bridge._tx_wait == "tone"
        assert play_calls
        assert type(play_calls[0]).__name__ == "ToneAudioSource"
        bridge.on_playback_done()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(run())
    assert listening_at_pipeline == [True]


def test_assist_turn_tone_timeout_preserves_captured_speech():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    captured_chunks = []

    async def mock_pipeline(hass, **kwargs):
        stream = kwargs["stt_stream"]
        captured_chunks.append(await asyncio.wait_for(stream.queue.get(), timeout=0.5))
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        turn_tone=True,
        stop_audio_fn=MagicMock(),
    )
    marker = b"\xab\xcd" * 80

    async def run():
        real_timeout = asyncio.timeout

        def short_tone_timeout(delay):
            if delay == 3:
                return real_timeout(0.05)
            return real_timeout(delay)

        with patch("asyncio.timeout", short_tone_timeout):
            bridge.start()
            await asyncio.sleep(0.02)
            bridge.write(marker)
            if bridge.session_task:
                await bridge.session_task

    asyncio.run(run())
    assert captured_chunks
    assert b"\xab\xcd" in captured_chunks[0]


def test_assist_turn_tone_success_does_not_inject_captured_speech():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    queue_sizes_at_pipeline = []

    async def mock_pipeline(hass, **kwargs):
        queue_sizes_at_pipeline.append(kwargs["stt_stream"].queue.qsize())
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        turn_tone=True,
    )
    marker = b"\xef\xbe" * 80

    async def run():
        bridge.start()
        await asyncio.sleep(0.02)
        bridge.write(marker)
        bridge.on_playback_done()
        if bridge.session_task:
            await bridge.session_task

    asyncio.run(run())
    assert queue_sizes_at_pipeline == [0]


def test_assist_turn_tone_timeout_continues_turn():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turns = []
    stop_calls = []

    async def mock_pipeline(hass, **kwargs):
        turns.append(1)
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    async def run():
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            max_silent_turns=1,
            turn_tone=True,
            stop_audio_fn=lambda **_kw: stop_calls.append(1),
        )
        real_timeout = asyncio.timeout

        def short_timeout(delay):
            return real_timeout(0.05)

        with patch("asyncio.timeout", short_timeout):
            bridge.start()
            if bridge.session_task:
                await bridge.session_task

    asyncio.run(run())
    assert turns == [1]
    assert stop_calls


def test_assist_turn_tone_skipped_when_preroll():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    play = MagicMock()

    async def mock_pipeline(hass, **kwargs):
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=play,
        on_done_fn=MagicMock(),
        max_silent_turns=1,
        turn_tone=True,
    )
    bridge._barge_in_preroll = b"\x00\x00" * 320
    _run_bridge_session(bridge)
    play.assert_not_called()


def test_assist_turn_tone_disabled_by_default():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    play = MagicMock()

    async def mock_pipeline(hass, **kwargs):
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=play,
        on_done_fn=MagicMock(),
        max_silent_turns=1,
    )
    _run_bridge_session(bridge)
    play.assert_not_called()
    assert bridge.turn_tone is False


def test_assist_close_unblocks_turn_tone_wait():
    assist_mod, mock_ap, PET, PE = _assist_ctx()

    async def mock_pipeline(hass, **kwargs):
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline
    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        turn_tone=True,
    )

    async def run():
        bridge.start()
        task = bridge.session_task
        await asyncio.sleep(0.05)
        assert bridge._tx_wait == "tone"
        t0 = asyncio.get_running_loop().time()
        bridge.close()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert asyncio.get_running_loop().time() - t0 < 1.0

    asyncio.run(run())


# ------------------------------------------------------- config_flow schema
def test_build_schema_new_entry_has_no_prefilled_values():
    # Matches the original (pre-reconfigure) schema exactly: required fields
    # start blank, only the fields that always had defaults keep them.
    schema = config_flow._build_schema(7078)
    markers = {str(k): k for k in schema.schema}
    assert markers["server"].default is vol.UNDEFINED
    assert markers["username"].default is vol.UNDEFINED
    assert markers["password"].default is vol.UNDEFINED
    assert markers["authentication_username"].default is vol.UNDEFINED
    assert markers["port"].default() == config_flow.DEFAULT_PORT
    assert markers["local_rtp_port"].default() == 7078
    assert (
        markers["register_expiration"].default()
        == config_flow.DEFAULT_REGISTER_EXPIRATION
    )


def test_build_schema_reconfigure_prefills_current_entry_values():
    current = {
        "server": "pbx.example.com",
        "port": 5061,
        "username": "1001",
        "password": "s3cret",
        "caller_id": "Front Desk",
        "register_expiration": 600,
        "local_rtp_port": 7080,
    }
    schema = config_flow._build_schema(7078, defaults=current)
    markers = {str(k): k for k in schema.schema}
    assert markers["server"].default() == "pbx.example.com"
    assert markers["username"].default() == "1001"
    assert markers["password"].default() == "s3cret"
    assert markers["port"].default() == 5061
    assert markers["caller_id"].default() == "Front Desk"
    assert markers["register_expiration"].default() == 600
    assert markers["local_rtp_port"].default() == 7080
    # Fields never set on the original entry stay untouched (no forced "").
    assert markers["domain"].default is vol.UNDEFINED
    assert markers["authentication_username"].default is vol.UNDEFINED
    assert markers["outbound_proxy"].default is vol.UNDEFINED


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
