"""Unit tests for the framework-agnostic SIP core (no Home Assistant needed).

Run with either:
    py tests/test_pure.py
    py -m pytest tests/test_pure.py
"""
import os
import struct
import sys
import tempfile

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
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402
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


# ------------------------------------------------------- realtime pacing
class _FakeClock:
    """Stand-in for the event loop clock so pacing is deterministic."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


def _pacer(sample_rate=8000):
    async def build():
        return audio._RealtimePacer(sample_rate)

    p = asyncio.run(build())
    clock = _FakeClock()
    p._loop = clock
    p._start = 0.0
    return p, clock


def test_pacer_throttles_only_past_the_prebuffer_window():
    """Queueing less than the prebuffer lead must not stall the source."""
    p, clock = _pacer()
    slept = []

    async def fake_sleep(delay=0, result=None):
        slept.append(delay)
        return result

    # 0.4 s of 8 kHz PCM queued, no time elapsed -> inside the 0.5 s window.
    p.account(8000 * 2 * 4 // 10)
    with patch.object(audio.asyncio, "sleep", fake_sleep):
        asyncio.run(p.wait())
    assert slept == [0]

    # Push past the window: it must now throttle by exactly the excess.
    p.account(8000 * 2 * 4 // 10)  # total 0.8 s queued
    with patch.object(audio.asyncio, "sleep", fake_sleep):
        asyncio.run(p.wait())
    assert abs(slept[-1] - (0.8 - audio._PCM_PREBUFFER_SEC)) < 1e-6


def test_pacer_catches_up_after_an_event_loop_stall():
    """A stalled loop must not permanently cost audio (issue #45).

    The old fixed per-frame sleep accumulated every delay, so the source fell
    behind real time and RtpSession transmitted comfort silence in the gap.
    """
    p, clock = _pacer()
    slept = []

    async def fake_sleep(delay=0, result=None):
        slept.append(delay)
        return result

    p.account(8000 * 2)          # 1.0 s of audio queued
    clock.now = 3.0              # ...but 3 s of wall time went by: badly behind
    with patch.object(audio.asyncio, "sleep", fake_sleep):
        asyncio.run(p.wait())
    # Must yield without stalling so the loop can catch back up.
    assert slept == [0]


def test_pacer_tracks_rate_for_wideband_codecs():
    """G.722 runs at 16 kHz, so the same byte count is half the duration."""
    p, clock = _pacer(sample_rate=16000)
    slept = []

    async def fake_sleep(delay=0, result=None):
        slept.append(delay)
        return result

    p.account(16000 * 2)  # 1.0 s at 16 kHz
    with patch.object(audio.asyncio, "sleep", fake_sleep):
        asyncio.run(p.wait())
    assert abs(slept[-1] - (1.0 - audio._PCM_PREBUFFER_SEC)) < 1e-6


def test_ffmpeg_source_reassembles_short_reads_into_whole_frames():
    """``StreamReader.read(n)`` may return fewer bytes; frames must stay aligned."""
    # Deliberately not a frame multiple: 20 full 320 B frames + a 50 B tail,
    # so the trailing partial-frame push is exercised too. Under the 0.5 s
    # prebuffer window, so the test does no real waiting.
    total = 6450
    script = (
        "#!" + sys.executable + "\n"
        "import sys, time\n"
        "out = sys.stdout.buffer\n"
        "written = 0\n"
        "while written < %d:\n"
        "    n = min(100, %d - written)\n"  # deliberately not a frame multiple
        "    out.write(b'\\x11\\x22' * (n // 2)); out.flush()\n"
        "    written += (n // 2) * 2\n"
        # Pause so the reader drains each burst: read() then returns a short
        # chunk, which is exactly the case the frame reassembly must handle.
        "    time.sleep(0.001)\n"
    ) % (total, total)

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(script)
        path = fh.name
    os.chmod(path, 0o755)

    try:
        chunks = []

        async def main():
            source = audio.FfmpegAudioSource(ffmpeg_bin=path, url="ignored")
            source.configure(8000, 320)
            await source.run(chunks.append, lambda: True)

        asyncio.run(main())
    finally:
        os.unlink(path)

    assert sum(len(c) for c in chunks) == total
    # Every chunk but a possible remainder is exactly one 20 ms frame.
    assert all(len(c) == 320 for c in chunks[:-1])
    assert len(chunks[-1]) == total % 320  # the tail is emitted, not dropped


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


def test_parse_combines_repeated_routing_headers_in_wire_order():
    msg = sm.parse_sip_message(
        "BYE sip:client@example SIP/2.0\r\n"
        "Via: SIP/2.0/UDP first.example;branch=1\r\n"
        "Via: SIP/2.0/UDP second.example;branch=2\r\n"
        "Record-Route: <sip:first.example;lr>\r\n"
        "Record-Route: <sip:second.example;lr>\r\n\r\n"
    )
    assert msg.header("Via") == (
        "SIP/2.0/UDP first.example;branch=1, "
        "SIP/2.0/UDP second.example;branch=2"
    )
    assert msg.header("Record-Route") == (
        "<sip:first.example;lr>, <sip:second.example;lr>"
    )


def test_split_header_values_ignores_nested_commas():
    assert sm.split_header_values(
        '"Proxy, One" <sip:first.example;lr>, '
        '<sip:second.example?Subject=hello,world;lr>'
    ) == [
        '"Proxy, One" <sip:first.example;lr>',
        '<sip:second.example?Subject=hello,world;lr>',
    ]


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


def test_parse_sdp_hold_attributes():
    sendonly = sm.parse_sdp(
        "c=IN IP4 192.168.0.5\r\nm=audio 4002 RTP/AVP 8\r\na=sendonly\r\n"
    )
    assert sendonly.direction == "sendonly"
    assert sendonly.is_hold
    inactive = sm.parse_sdp(
        "c=IN IP4 192.168.0.5\r\nm=audio 4002 RTP/AVP 8\r\na=inactive\r\n"
    )
    assert inactive.is_hold
    rfc2543 = sm.parse_sdp("c=IN IP4 0.0.0.0\r\nm=audio 4002 RTP/AVP 8\r\n")
    assert rfc2543.connection_ip == "0.0.0.0"
    assert rfc2543.is_hold
    resume = sm.parse_sdp(
        "c=IN IP4 192.168.0.5\r\nm=audio 4002 RTP/AVP 8\r\na=sendrecv\r\n"
    )
    assert resume.direction == "sendrecv"
    assert not resume.is_hold


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


def test_codecs_keep_or_choose_prefers_current_if_still_offered():
    current = codecs.G722
    # Refresh still lists G.722: keep it rather than rebuilding encoder state.
    kept = codecs.keep_or_choose(current, _sdp({9, 0, 8}, pcmu=0, pcma=8, g722=9))
    assert kept is current
    # Offerless / no audio payloads: keep current.
    assert codecs.keep_or_choose(current, sm.SdpInfo()) is current
    # Remote dropped G.722: fall back to the remaining offer.
    switched = codecs.keep_or_choose(current, _sdp({8}, pcma=8))
    assert switched.name == "PCMA"
    assert switched.payload_type == 8


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


def _info_dtmf_request(digit="1"):
    body = f"Signal={digit}\r\nDuration=160\r\n"
    return sm.parse_sip_message(
        "INFO sip:alice@example SIP/2.0\r\n"
        "Via: SIP/2.0/UDP pbx.example;branch=z9hG4bKinfo\r\n"
        "From: <sip:bob@example>;tag=remote\r\n"
        "To: <sip:alice@example>;tag=local\r\n"
        "Call-ID: inbound@example\r\n"
        "CSeq: 2 INFO\r\n"
        "Content-Type: application/dtmf-relay\r\n"
        f"Content-Length: {len(body)}\r\n\r\n" + body
    )


def _handle_info_dtmf(state):
    """Feed one DTMF INFO to a client in `state`; return (digits, responses)."""
    async def run():
        got = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example"),
            sip_client.SipCallbacks(on_dtmf=got.append),
        )
        client.state = state
        with patch.object(client, "_send_raw") as send:
            client._handle_request(_info_dtmf_request())
        return got, [c.args[0] for c in send.call_args_list]

    return asyncio.run(run())


def test_info_dtmf_fires_during_call():
    if sip_client is None:
        return
    # ANSWERING too: the INFO can arrive before the ACK that ends it.
    for state in (sip_client.SipState.IN_CALL, sip_client.SipState.ANSWERING):
        digits, sent = _handle_info_dtmf(state)
        assert digits == ["1"]
        assert sent and sent[0].startswith("SIP/2.0 200 OK")


def test_info_dtmf_outside_call_is_answered_but_not_delivered():
    if sip_client is None:
        return
    # An INFO out of any call still gets its 200 OK, but must not inject a
    # keypress into IVR menus / automations.
    digits, sent = _handle_info_dtmf(sip_client.SipState.REGISTERED)
    assert digits == []
    assert sent and sent[0].startswith("SIP/2.0 200 OK")


def test_response_copies_complete_via_chain():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        request = sm.parse_sip_message(
            "BYE sip:alice@example SIP/2.0\r\n"
            "Via: SIP/2.0/UDP first.example;branch=1\r\n"
            "Via: SIP/2.0/UDP second.example;branch=2\r\n"
            "From: <sip:bob@example>;tag=remote\r\n"
            "To: <sip:alice@example>;tag=local\r\n"
            "Call-ID: call@example\r\n"
            "CSeq: 1 BYE\r\n\r\n"
        )
        return client._build_response(request, 200, "OK", False)

    response = asyncio.run(run())
    assert (
        "Via: SIP/2.0/UDP first.example;branch=1, "
        "SIP/2.0/UDP second.example;branch=2\r\n"
    ) in response


def test_successful_invite_ack_and_bye_use_reversed_record_route():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._local_ip = "192.0.2.10"
        client._local_port = 5060
        client._outbound = True
        client._d_call_id = "call@example"
        client._d_local = "<sip:alice@example>;tag=local"
        client._d_remote = "<sip:bob@example>"
        client._d_remote_target = "sip:bob@example"
        client._d_cseq = 2
        client._invite_cseq = 2
        client.registered = True
        client.state = sip_client.SipState.RINGING_OUT
        response = sm.parse_sip_message(
            "SIP/2.0 200 OK\r\n"
            "Record-Route: <sip:first.example;lr>,<sip:middle.example;lr>\r\n"
            "Record-Route: <sip:last.example;lr>\r\n"
            "To: <sip:bob@example>;tag=remote\r\n"
            "Contact: <sip:bob@target.example>\r\n"
            "Call-ID: call@example\r\n"
            "CSeq: 2 INVITE\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client, "_apply_remote_sdp"),
            patch.object(client, "_start_media", AsyncMock()) as start_media,
        ):
            client._handle_invite_response(response)
            first_ack = send.call_args_list[0].args[0]
            client._handle_invite_response(response)
            repeated_ack = send.call_args_list[1].args[0]
            client.hangup()
            local_bye = send.call_args_list[2].args[0]
            client._handle_invite_response(response)
            delayed_ack = send.call_args_list[3].args[0]
            await asyncio.sleep(0)
        return (
            first_ack,
            repeated_ack,
            delayed_ack,
            local_bye,
            start_media.await_count,
            client.state,
            client._d_cseq,
            client._invite_cseq,
        )

    (
        first_ack,
        repeated_ack,
        delayed_ack,
        bye,
        media_starts,
        state,
        dialog_cseq,
        invite_cseq,
    ) = asyncio.run(run())
    expected = (
        "Route: <sip:last.example;lr>, <sip:middle.example;lr>, "
        "<sip:first.example;lr>\r\n"
    )
    assert expected in first_ack
    assert expected in repeated_ack
    assert expected in delayed_ack
    assert expected in bye
    assert first_ack.startswith("ACK sip:bob@target.example SIP/2.0\r\n")
    assert bye.startswith("BYE sip:bob@target.example SIP/2.0\r\n")
    assert media_starts == 1
    assert state == sip_client.SipState.REGISTERED
    assert dialog_cseq == 3
    assert invite_cseq == 2


def test_cancel_race_acks_and_ends_late_2xx_with_original_invite_cseq():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._local_ip = "192.0.2.10"
        client._local_port = 5060
        client._outbound = True
        client._d_call_id = "call@example"
        client._d_local = "<sip:alice@example>;tag=local"
        client._d_remote = "<sip:bob@example>"
        client._d_remote_target = "sip:bob@example"
        client._d_branch = "z9hG4bKinvite"
        client._d_cseq = 2
        client._invite_cseq = 2
        client.registered = True
        client.state = sip_client.SipState.RINGING_OUT
        response = sm.parse_sip_message(
            "SIP/2.0 200 OK\r\n"
            "Record-Route: <sip:first.example;lr>,<sip:last.example;lr>\r\n"
            "To: <sip:bob@example>;tag=late\r\n"
            "Contact: <sip:bob@late.example>\r\n"
            "Call-ID: call@example\r\n"
            "CSeq: 2 INVITE\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client, "_start_media", AsyncMock()) as start_media,
        ):
            client.hangup()
            client._handle_invite_response(response)
            await asyncio.sleep(0)
        return client, [call.args[0] for call in send.call_args_list], start_media

    client, sent, start_media = asyncio.run(run())
    assert len(sent) == 3
    assert sent[0].startswith("CANCEL sip:bob@example SIP/2.0\r\n")
    assert "branch=z9hG4bKinvite;rport" in sent[0]
    assert "CSeq: 2 CANCEL\r\n" in sent[0]
    assert sent[1].startswith("ACK sip:bob@late.example SIP/2.0\r\n")
    assert sent[2].startswith("BYE sip:bob@late.example SIP/2.0\r\n")
    assert client._d_cseq == 2
    assert client._invite_cseq == 2
    assert client.state == sip_client.SipState.REGISTERED
    start_media.assert_not_awaited()


def test_invite_response_from_previous_call_id_is_ignored():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._outbound = True
        client._d_call_id = "current@example"
        client._d_cseq = 1
        client._invite_cseq = 1
        client.state = sip_client.SipState.RINGING_OUT
        response = sm.parse_sip_message(
            "SIP/2.0 200 OK\r\n"
            "To: <sip:bob@example>;tag=old\r\n"
            "Contact: <sip:bob@old.example>\r\n"
            "Call-ID: previous@example\r\n"
            "CSeq: 1 INVITE\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client, "_start_media", AsyncMock()) as start_media,
        ):
            client._handle_invite_response(response)
        return client, send, start_media

    client, send, start_media = asyncio.run(run())
    send.assert_not_called()
    start_media.assert_not_awaited()
    assert client.state == sip_client.SipState.RINGING_OUT


def test_forked_invite_2xx_is_acknowledged_and_ended_without_replacing_dialog():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client._local_ip = "192.0.2.10"
        client._local_port = 5060
        client._outbound = True
        client._d_call_id = "call@example"
        client._d_local = "<sip:alice@example>;tag=local"
        client._d_remote = "<sip:bob@example>;tag=accepted"
        client._d_remote_target = "sip:bob@accepted.example"
        client._d_cseq = 1
        client._invite_cseq = 1
        client._dialog_routes = ["<sip:accepted-proxy.example;lr>"]
        client._accepted_dialog_to = client._d_remote
        client.state = sip_client.SipState.IN_CALL
        response = sm.parse_sip_message(
            "SIP/2.0 200 OK\r\n"
            "Record-Route: <sip:first-fork.example;lr>,<sip:last-fork.example;lr>\r\n"
            "To: <sip:bob@example>;tag=forked\r\n"
            "Contact: <sip:bob@forked.example>\r\n"
            "Call-ID: call@example\r\n"
            "CSeq: 1 INVITE\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        with patch.object(client, "_send_raw") as send:
            client._handle_invite_response(response)
        return client, [call.args[0] for call in send.call_args_list]

    client, sent = asyncio.run(run())
    assert len(sent) == 2
    assert sent[0].startswith("ACK sip:bob@forked.example SIP/2.0\r\n")
    assert sent[1].startswith("BYE sip:bob@forked.example SIP/2.0\r\n")
    expected = "Route: <sip:last-fork.example;lr>, <sip:first-fork.example;lr>\r\n"
    assert expected in sent[0]
    assert expected in sent[1]
    assert client._d_remote == "<sip:bob@example>;tag=accepted"
    assert client._d_remote_target == "sip:bob@accepted.example"
    assert client._dialog_routes == ["<sip:accepted-proxy.example;lr>"]


def test_new_outbound_call_clears_previous_dialog_routes():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.state = sip_client.SipState.REGISTERED
        client._dialog_routes = ["<sip:stale.example;lr>"]
        with (
            patch.object(client, "_send_raw"),
            patch.object(client, "_start_invite_retx"),
        ):
            client.call("1234")
        return client._dialog_routes, client._d_cseq, client._invite_cseq

    assert asyncio.run(run()) == ([], 1, 1)


def test_authenticated_invite_updates_retained_invite_cseq():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(
            sip_client.SipConfig(
                server="pbx.example",
                username="alice",
                password="secret",
                domain="example",
            )
        )
        client._local_ip = "192.0.2.10"
        client._local_port = 5060
        client._outbound = True
        client._d_call_id = "call@example"
        client._d_local = "<sip:alice@example>;tag=local"
        client._d_remote = "<sip:bob@example>"
        client._d_remote_target = "sip:bob@example"
        client._d_branch = "z9hG4bKinitial"
        client._d_cseq = 1
        client._invite_cseq = 1
        client.state = sip_client.SipState.INVITING
        response = sm.parse_sip_message(
            "SIP/2.0 407 Proxy Authentication Required\r\n"
            "To: <sip:bob@example>;tag=proxy\r\n"
            "Call-ID: call@example\r\n"
            "CSeq: 1 INVITE\r\n"
            'Proxy-Authenticate: Digest realm="example", nonce="abc123"\r\n'
            "Content-Length: 0\r\n\r\n"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client, "_start_invite_retx"),
        ):
            client._handle_invite_response(response)
        return client, [call.args[0] for call in send.call_args_list]

    client, sent = asyncio.run(run())
    assert client._d_cseq == 2
    assert client._invite_cseq == 2
    assert "CSeq: 1 ACK\r\n" in sent[0]
    assert "CSeq: 2 INVITE\r\n" in sent[1]


def test_inbound_dialog_keeps_record_route_wire_order():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.state = sip_client.SipState.REGISTERED
        invite = sm.parse_sip_message(
            "INVITE sip:alice@example SIP/2.0\r\n"
            "Record-Route: <sip:first.example;lr>,<sip:second.example;lr>\r\n"
            "From: <sip:bob@example>;tag=remote\r\n"
            "To: <sip:alice@example>\r\n"
            "Contact: <sip:bob@target.example>\r\n"
            "Call-ID: inbound@example\r\n"
            "CSeq: 1 INVITE\r\n"
            "Content-Type: application/sdp\r\n\r\n"
            "v=0\r\nc=IN IP4 198.51.100.10\r\n"
            "m=audio 4000 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\n"
        )
        with patch.object(client, "_send_raw") as send:
            client._handle_request(invite)
            response = client._build_response(invite, 200, "OK", True)
        return client._build_in_dialog("BYE"), response, send

    bye, response, _send = asyncio.run(run())
    assert bye.startswith("BYE sip:bob@target.example SIP/2.0\r\n")
    assert "Route: <sip:first.example;lr>, <sip:second.example;lr>\r\n" in bye
    assert "Record-Route: <sip:first.example;lr>,<sip:second.example;lr>\r\n" in response


def _inbound_client_with_routes(record_route):
    """Drive an incoming INVITE carrying `record_route` and return the client."""
    client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
    client.state = sip_client.SipState.REGISTERED
    invite = sm.parse_sip_message(
        "INVITE sip:14@192.168.1.237:59436 SIP/2.0\r\n"
        f"Record-Route: {record_route}\r\n"
        "From: <sip:12@example>;tag=remote\r\n"
        "To: <sip:14@example>\r\n"
        "Contact: <sip:12@127.0.0.1:5060>\r\n"
        "Call-ID: inbound@example\r\n"
        "CSeq: 1 INVITE\r\n"
        "Content-Type: application/sdp\r\n\r\n"
        "v=0\r\nc=IN IP4 198.51.100.10\r\n"
        "m=audio 4000 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\n"
    )
    with patch.object(client, "_send_raw"):
        client._handle_request(invite)
    return client, invite


def test_strict_route_moves_first_hop_into_request_uri():
    """A route set without ";lr" is a strict router (RFC 3261 §12.2.1.1).

    The 3CX SBC in issue #39 record-routes without ";lr", so sending the BYE
    to the peer's Contact would address 127.0.0.1 and never reach the SBC.
    """
    if sip_client is None:
        return

    async def run():
        client, _ = _inbound_client_with_routes(
            "<sip:3CXSBC@192.168.1.194:5060;user=proxy;tnlid=sbc.c8d9>"
        )
        return client._build_in_dialog("BYE")

    bye = asyncio.run(run())
    assert bye.startswith(
        "BYE sip:3CXSBC@192.168.1.194:5060;user=proxy;tnlid=sbc.c8d9 SIP/2.0\r\n"
    )
    # The unreachable Contact is preserved as the final route, not dropped.
    assert "Route: <sip:12@127.0.0.1:5060>\r\n" in bye


def test_strict_route_keeps_remaining_hops_from_combined_header():
    """Comma-combined and repeated Record-Route rows must behave identically."""
    if sip_client is None:
        return

    async def run():
        client, _ = _inbound_client_with_routes(
            "<sip:sbc.example>,<sip:middle.example>"
        )
        return client._build_in_dialog("BYE")

    bye = asyncio.run(run())
    assert bye.startswith("BYE sip:sbc.example SIP/2.0\r\n")
    assert "Route: <sip:middle.example>, <sip:12@127.0.0.1:5060>\r\n" in bye


def test_lr_lookalike_param_is_not_treated_as_loose():
    if sip_client is None:
        return

    async def run():
        client, _ = _inbound_client_with_routes("<sip:sbc.example;lrx=1>")
        return client._build_in_dialog("BYE")

    bye = asyncio.run(run())
    assert bye.startswith("BYE sip:sbc.example;lrx=1 SIP/2.0\r\n")


def test_ringing_response_establishes_early_dialog():
    """18x opens an early dialog, so it needs Record-Route and Contact too."""
    if sip_client is None:
        return

    async def run():
        client, invite = _inbound_client_with_routes("<sip:sbc.example;lr>")
        return (
            client._build_response(invite, 180, "Ringing", False),
            client._build_response(invite, 100, "Trying", False),
        )

    ringing, trying = asyncio.run(run())
    assert "Record-Route: <sip:sbc.example;lr>\r\n" in ringing
    assert "Contact: <sip:" in ringing
    # 100 Trying is not dialog-establishing and must stay bare.
    assert "Record-Route:" not in trying
    assert "Contact:" not in trying


# ------------------------------------------------------- in-dialog re-INVITE
def _sdp_body(ip="198.51.100.10", port=4000, extra="", pts="8", rtpmap="a=rtpmap:8 PCMA/8000\r\n"):
    return (
        "v=0\r\n"
        f"c=IN IP4 {ip}\r\n"
        f"m=audio {port} RTP/AVP {pts}\r\n"
        f"{rtpmap}"
        f"{extra}"
    )


def _invite_request(
    *,
    call_id="dlg@example",
    cseq=1,
    branch="z9hG4bKorig",
    frm="<sip:bob@example>;tag=remote",
    to="<sip:alice@example>",
    contact="<sip:bob@target.example>",
    sdp_ip="198.51.100.10",
    sdp_port=4000,
    extra_sdp="",
    body=None,
    pts="8",
    rtpmap="a=rtpmap:8 PCMA/8000\r\n",
):
    if body is None:
        body = _sdp_body(sdp_ip, sdp_port, extra_sdp, pts=pts, rtpmap=rtpmap)
    ctype = "Content-Type: application/sdp\r\n" if body else ""
    return sm.parse_sip_message(
        "INVITE sip:alice@example SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP pbx.example;branch={branch}\r\n"
        f"From: {frm}\r\n"
        f"To: {to}\r\n"
        f"Contact: {contact}\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: {cseq} INVITE\r\n"
        f"{ctype}"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    )


def _inbound_in_call():
    """Drive an inbound INVITE through to IN_CALL and return (client, orig)."""
    incoming = []
    client = sip_client.SipClient(
        sip_client.SipConfig(server="pbx.example"),
        sip_client.SipCallbacks(on_incoming_call=incoming.append),
    )
    client.state = sip_client.SipState.REGISTERED
    client._local_ip = "192.0.2.1"
    client._local_port = 5060
    orig = _invite_request()
    with patch.object(client, "_send_raw"):
        client._handle_request(orig)
    client.state = sip_client.SipState.IN_CALL
    client._media_active = True
    return client, orig, incoming


def _outbound_in_call():
    incoming = []
    client = sip_client.SipClient(
        sip_client.SipConfig(server="pbx.example"),
        sip_client.SipCallbacks(on_incoming_call=incoming.append),
    )
    client._local_ip = "192.0.2.1"
    client._local_port = 5060
    client._outbound = True
    client._d_call_id = "out@example"
    client._d_local_tag = "local"
    client._d_local = "<sip:alice@example>;tag=local"
    client._d_remote = "<sip:bob@example>;tag=remote"
    client._d_remote_target = "sip:bob@target.example"
    client._remote_rtp_ip = "198.51.100.10"
    client._remote_rtp_port = 4000
    client.last_caller = "keep-me"
    client.registered = True
    client.state = sip_client.SipState.IN_CALL
    client._media_active = True
    return client, incoming


def test_inbound_reinvite_answers_with_new_transaction():
    """re-INVITE 200 must echo the re-INVITE Via branch and CSeq, not the original."""
    if sip_client is None:
        return

    async def run():
        client, orig, incoming = _inbound_in_call()
        last_caller = client.last_caller
        reinvite = _invite_request(
            cseq=2,
            branch="z9hG4bKreinv",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            contact="<sip:bob@new.example>",
            sdp_ip="203.0.113.50",
            sdp_port=5000,
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client.rtp, "set_remote") as set_remote,
            patch.object(client.rtp, "stop", new_callable=AsyncMock) as rtp_stop,
        ):
            client._handle_request(reinvite)
        return client, orig, incoming, last_caller, send, set_remote, rtp_stop

    client, orig, incoming, last_caller, send, set_remote, rtp_stop = asyncio.run(run())
    sent = send.call_args_list[0].args[0]
    assert sent.startswith("SIP/2.0 200 OK")
    assert "CSeq: 2 INVITE\r\n" in sent
    assert "branch=z9hG4bKreinv" in sent
    assert "CSeq: 1 INVITE\r\n" not in sent
    assert "branch=z9hG4bKorig" not in sent
    assert "Content-Type: application/sdp" in sent
    assert "m=audio" in sent
    assert client.last_caller == last_caller
    assert client.state == sip_client.SipState.IN_CALL
    assert client._d_remote_target == "sip:bob@new.example"
    assert incoming == ["bob"]  # original only; re-INVITE must not re-emit
    set_remote.assert_called_once_with("203.0.113.50", 5000)
    rtp_stop.assert_not_called()


def test_outbound_reinvite_is_not_busy_here():
    if sip_client is None:
        return

    async def run():
        client, incoming = _outbound_in_call()
        reinvite = _invite_request(
            call_id="out@example",
            cseq=1,
            branch="z9hG4bKout-reinv",
            to="<sip:alice@example>;tag=local",
            sdp_ip="203.0.113.8",
            sdp_port=6000,
        )
        with patch.object(client, "_send_raw") as send:
            client._handle_request(reinvite)
        return client, incoming, [c.args[0] for c in send.call_args_list]

    client, incoming, sent = asyncio.run(run())
    assert sent and sent[0].startswith("SIP/2.0 200 OK")
    assert "486 Busy Here" not in sent[0]
    assert "Content-Type: application/sdp" in sent[0]
    assert "CSeq: 1 INVITE\r\n" in sent[0]
    assert "branch=z9hG4bKout-reinv" in sent[0]
    assert client.last_caller == "keep-me"
    assert client.state == sip_client.SipState.IN_CALL
    assert incoming == []


def test_invite_retransmission_replays_prior_response():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.state = sip_client.SipState.REGISTERED
        client._local_ip = "192.0.2.1"
        orig = _invite_request()
        with patch.object(client, "_send_raw") as send:
            client._handle_request(orig)
            ringing = [c.args[0] for c in send.call_args_list]
            send.reset_mock()
            client._handle_request(orig)
            replay = [c.args[0] for c in send.call_args_list]
            client.state = sip_client.SipState.IN_CALL
            send.reset_mock()
            client._handle_request(orig)
            in_call_replay = [c.args[0] for c in send.call_args_list]
        return ringing, replay, in_call_replay

    ringing, replay, in_call_replay = asyncio.run(run())
    assert any(s.startswith("SIP/2.0 180 Ringing") for s in ringing)
    assert replay and replay[0].startswith("SIP/2.0 180 Ringing")
    assert "CSeq: 1 INVITE\r\n" in replay[0]
    assert "branch=z9hG4bKorig" in replay[0]
    assert in_call_replay and in_call_replay[0].startswith("SIP/2.0 200 OK")
    assert "CSeq: 1 INVITE\r\n" in in_call_replay[0]
    assert "branch=z9hG4bKorig" in in_call_replay[0]


def test_reinvite_hold_and_resume_toggles_tx():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        hold = _invite_request(
            cseq=2,
            branch="z9hG4bKhold",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            extra_sdp="a=sendonly\r\n",
        )
        resume = _invite_request(
            cseq=3,
            branch="z9hG4bKresume",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            extra_sdp="a=sendrecv\r\n",
        )
        with patch.object(client, "_send_raw") as send:
            client._handle_request(hold)
            held = client.rtp.send_silence, client._on_hold, send.call_args.args[0]
            send.reset_mock()
            client._handle_request(resume)
            resumed = client.rtp.send_silence, client._on_hold, send.call_args.args[0]
        return held, resumed, client.state

    held, resumed, state = asyncio.run(run())
    assert held[0] is False
    assert held[1] is True
    assert held[2].startswith("SIP/2.0 200 OK")
    assert "a=recvonly" in held[2]
    assert resumed[0] is True
    assert resumed[1] is False
    assert "a=sendrecv" in resumed[2]
    assert state == sip_client.SipState.IN_CALL


def test_reinvite_inactive_answers_inactive():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        hold = _invite_request(
            cseq=2,
            branch="z9hG4bKinact",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            extra_sdp="a=inactive\r\n",
        )
        with patch.object(client, "_send_raw") as send:
            client._handle_request(hold)
        return send.call_args.args[0], client.rtp.tx_enabled, client._local_direction

    sent, tx_enabled, direction = asyncio.run(run())
    assert sent.startswith("SIP/2.0 200 OK")
    assert "a=inactive" in sent
    assert "a=recvonly" not in sent
    assert tx_enabled is False
    assert direction == "inactive"


def test_hold_drops_queued_tx_audio():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        client.rtp._transport = object()
        client.rtp.tx_enabled = True
        hold = _invite_request(
            cseq=2,
            branch="z9hG4bKholdtx",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            extra_sdp="a=sendonly\r\n",
        )
        with patch.object(client, "_send_raw"):
            client._handle_request(hold)
        client.rtp.push_tx_audio(b"\x00" * 320)
        return client.rtp.tx_enabled, bytes(client.rtp._tx_buffer)

    tx_enabled, buffered = asyncio.run(run())
    assert tx_enabled is False
    assert buffered == b""


def test_answering_reinvite_applies_new_sdp():
    """A lost ACK followed by CSeq+1 re-INVITE must not replay the original 200."""
    if sip_client is None:
        return

    async def run():
        incoming = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example"),
            sip_client.SipCallbacks(on_incoming_call=incoming.append),
        )
        client.state = sip_client.SipState.REGISTERED
        client._local_ip = "192.0.2.1"
        orig = _invite_request()
        with patch.object(client, "_send_raw"):
            client._handle_request(orig)
        client.state = sip_client.SipState.ANSWERING
        client._media_active = True
        reinvite = _invite_request(
            cseq=2,
            branch="z9hG4bKans-reinv",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            sdp_ip="203.0.113.70",
            sdp_port=5100,
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client.rtp, "set_remote") as set_remote,
        ):
            client._handle_request(reinvite)
        return send.call_args.args[0], set_remote, client.state, incoming

    sent, set_remote, state, incoming = asyncio.run(run())
    assert sent.startswith("SIP/2.0 200 OK")
    assert "CSeq: 2 INVITE\r\n" in sent
    assert "branch=z9hG4bKans-reinv" in sent
    set_remote.assert_called_once_with("203.0.113.70", 5100)
    assert state == sip_client.SipState.ANSWERING
    assert incoming == ["bob"]


def test_offerless_reinvite_applies_ack_sdp():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        reinvite = _invite_request(
            cseq=2,
            branch="z9hG4bKofferless",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            body="",
        )
        ack_body = _sdp_body("203.0.113.80", 5200)
        ack = sm.parse_sip_message(
            "ACK sip:alice@example SIP/2.0\r\n"
            "Via: SIP/2.0/UDP pbx.example;branch=z9hG4bKack\r\n"
            "From: <sip:bob@example>;tag=remote\r\n"
            f"To: <sip:alice@example>;tag={client._d_local_tag}\r\n"
            "Call-ID: dlg@example\r\n"
            "CSeq: 2 ACK\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(ack_body)}\r\n\r\n{ack_body}"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client.rtp, "set_remote") as set_remote,
        ):
            client._handle_request(reinvite)
            offer = send.call_args.args[0]
            client._handle_request(ack)
        return offer, set_remote, client._remote_rtp_ip, client._remote_rtp_port

    offer, set_remote, ip, port = asyncio.run(run())
    assert offer.startswith("SIP/2.0 200 OK")
    assert "Content-Type: application/sdp" in offer
    set_remote.assert_called_once_with("203.0.113.80", 5200)
    assert ip == "203.0.113.80"
    assert port == 5200


def test_reinvite_keeps_codec_when_still_offered():
    if sip_client is None:
        return

    async def run():
        incoming = []
        changed = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example"),
            sip_client.SipCallbacks(
                on_incoming_call=incoming.append,
                on_codec_change=changed.append,
            ),
        )
        client.state = sip_client.SipState.REGISTERED
        client._local_ip = "192.0.2.1"
        g722_rtpmap = (
            "a=rtpmap:9 G722/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
        )
        orig = _invite_request(pts="9 8", rtpmap=g722_rtpmap)
        with patch.object(client, "_send_raw"):
            client._handle_request(orig)
        client.state = sip_client.SipState.IN_CALL
        client._media_active = True
        with patch.object(client.rtp, "set_codec") as set_codec:
            refresh = _invite_request(
                cseq=2,
                branch="z9hG4bKkeep",
                to=f"<sip:alice@example>;tag={client._d_local_tag}",
                pts="9 8",
                rtpmap=g722_rtpmap,
            )
            client._handle_request(refresh)
        return client.codec.name, set_codec.call_count, [c.name for c in changed]

    name, set_codec_calls, changed_names = asyncio.run(run())
    assert name == "G722"
    assert set_codec_calls == 0
    assert changed_names == ["G722"]  # initial negotiation only


def test_reinvite_notifies_codec_change():
    if sip_client is None:
        return

    async def run():
        changed = []
        client = sip_client.SipClient(
            sip_client.SipConfig(server="pbx.example"),
            sip_client.SipCallbacks(on_codec_change=changed.append),
        )
        client.state = sip_client.SipState.REGISTERED
        client._local_ip = "192.0.2.1"
        orig = _invite_request(
            pts="9 8",
            rtpmap="a=rtpmap:9 G722/8000\r\na=rtpmap:8 PCMA/8000\r\n",
        )
        with patch.object(client, "_send_raw"):
            client._handle_request(orig)
        client.state = sip_client.SipState.IN_CALL
        client._media_active = True
        with patch.object(client.rtp, "set_codec") as set_codec:
            drop_g722 = _invite_request(
                cseq=2,
                branch="z9hG4bKpcma",
                to=f"<sip:alice@example>;tag={client._d_local_tag}",
            )
            client._handle_request(drop_g722)
        return client.codec.name, set_codec.call_count, [c.name for c in changed]

    name, set_codec_calls, changed_names = asyncio.run(run())
    assert name == "PCMA"
    assert set_codec_calls == 1
    assert changed_names == ["G722", "PCMA"]


def test_update_wrong_call_id_is_481():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        body = _sdp_body("203.0.113.9", 7000)
        update = sm.parse_sip_message(
            "UPDATE sip:alice@example SIP/2.0\r\n"
            "Via: SIP/2.0/UDP pbx.example;branch=z9hG4bKupd\r\n"
            "From: <sip:bob@example>;tag=remote\r\n"
            "To: <sip:alice@example>;tag=local\r\n"
            "Call-ID: other-dialog@example\r\n"
            "CSeq: 4 UPDATE\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(body)}\r\n\r\n{body}"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client.rtp, "set_remote") as set_remote,
        ):
            client._handle_request(update)
        return send.call_args.args[0], set_remote, client._remote_rtp_ip

    sent, set_remote, ip = asyncio.run(run())
    assert sent.startswith("SIP/2.0 481")
    set_remote.assert_not_called()
    assert ip == "198.51.100.10"


def test_reinvite_starts_media_if_never_started():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        client._media_active = False
        client._remote_rtp_ip = ""
        client._remote_rtp_port = 0
        reinvite = _invite_request(
            cseq=2,
            branch="z9hG4bKlate-media",
            to=f"<sip:alice@example>;tag={client._d_local_tag}",
            sdp_ip="203.0.113.90",
            sdp_port=5300,
        )
        with (
            patch.object(client, "_send_raw"),
            patch.object(client, "_start_media", new_callable=AsyncMock) as start_media,
        ):
            client._handle_request(reinvite)
            await asyncio.sleep(0)
        return start_media.await_count, client._remote_rtp_ip, client._remote_rtp_port

    starts, ip, port = asyncio.run(run())
    assert starts == 1
    assert ip == "203.0.113.90"
    assert port == 5300


def test_update_with_sdp_returns_answer():
    if sip_client is None:
        return

    async def run():
        client, _, _ = _inbound_in_call()
        body = _sdp_body("203.0.113.9", 7000)
        update = sm.parse_sip_message(
            "UPDATE sip:alice@example SIP/2.0\r\n"
            "Via: SIP/2.0/UDP pbx.example;branch=z9hG4bKupd\r\n"
            "From: <sip:bob@example>;tag=remote\r\n"
            f"To: <sip:alice@example>;tag={client._d_local_tag}\r\n"
            "Call-ID: dlg@example\r\n"
            "CSeq: 4 UPDATE\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(body)}\r\n\r\n{body}"
        )
        with (
            patch.object(client, "_send_raw") as send,
            patch.object(client.rtp, "set_remote") as set_remote,
            patch.object(client.rtp, "stop", new_callable=AsyncMock) as rtp_stop,
        ):
            client._handle_request(update)
        return send.call_args.args[0], set_remote, rtp_stop, client.state

    sent, set_remote, rtp_stop, state = asyncio.run(run())
    assert sent.startswith("SIP/2.0 200 OK")
    assert "CSeq: 4 UPDATE\r\n" in sent
    assert "Content-Type: application/sdp" in sent
    set_remote.assert_called_once_with("203.0.113.9", 7000)
    rtp_stop.assert_not_called()
    assert state == sip_client.SipState.IN_CALL


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


def test_rfc2833_rx_ignores_non_digit_events():
    # Event 16 is hook flash, not a keypress: it must not reach on_dtmf.
    assert _collect_dtmf([_te_packet(101, True, 1000, 16)]) == []


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
        INTENT = "intent"
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


def _initial_prompt_doubles(PET, PE, *events):
    """Return reusable doubles for a text-input Assist pipeline run."""
    from contextlib import nullcontext

    pipeline_runs = []
    pipeline_inputs = []

    class FakePipelineRun:
        def __init__(self, hass, **kwargs):
            self.hass = hass
            self.kwargs = kwargs
            pipeline_runs.append(self)

    class FakePipelineInput:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            pipeline_inputs.append(self)

        async def validate(self):
            return None

        async def execute(self):
            callback = self.kwargs["run"].kwargs["event_callback"]
            callback(
                PE(
                    PET.RUN_START,
                    {"conversation_id": self.kwargs["session"].conversation_id},
                )
            )
            for event_type, data in events:
                callback(PE(event_type, data))

    fake_chat_session = MagicMock()
    fake_chat_session.async_get_chat_session.side_effect = (
        lambda hass, conversation_id: nullcontext(
            types.SimpleNamespace(
                conversation_id=conversation_id or "opening-conversation"
            )
        )
    )
    return (
        pipeline_runs,
        pipeline_inputs,
        FakePipelineRun,
        FakePipelineInput,
        fake_chat_session,
    )


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


def test_assist_hangup_on_end_shrinks_silent_turn_budget():
    """hangup_on_end ends after 1 silent turn instead of the configured 2.

    Regression for #41: without a smaller budget, hangup_on_end callers wait
    through the full silent-turn count before the call is torn down.
    """
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
        hangup_on_end=True,
    )
    _run_bridge_session(bridge)
    assert turn_count == 1
    assert len(done_calls) == 1


def test_assist_hangup_on_end_does_not_widen_a_stricter_budget():
    """hangup_on_end only ever shrinks the budget, never grows it.

    An explicit max_silent_turns=0 already ends after the first silent turn;
    hangup_on_end clamping it up to 1 would wait through an extra turn.
    """
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    turn_count = 0

    async def mock_pipeline(hass, **kwargs):
        nonlocal turn_count
        turn_count += 1
        kwargs["event_callback"](PE(PET.ERROR, {"code": "stt-no-text-recognized"}))

    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        max_silent_turns=0,
        hangup_on_end=True,
    )
    _run_bridge_session(bridge)
    assert turn_count == 1


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


def test_assist_initial_prompt_error_still_starts_audio_with_context():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    audio_calls = []
    (
        pipeline_runs,
        pipeline_inputs,
        fake_run,
        fake_input,
        fake_chat_session,
    ) = _initial_prompt_doubles(
        PET, PE, (PET.ERROR, {"code": "intent-failed"})
    )

    async def mock_audio_pipeline(hass, **kwargs):
        audio_calls.append(kwargs)

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline

    with (
        patch.object(assist_mod, "PipelineRun", fake_run),
        patch.object(assist_mod, "PipelineInput", fake_input),
        patch.object(assist_mod, "chat_session", fake_chat_session),
    ):
        bridge = assist_mod.AssistBridge(
            MagicMock(),
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            initial_prompt="Greet the caller",
            system_prompt="Keep answers concise",
            max_turns=1,
        )
        _run_bridge_session(bridge)

    assert len(pipeline_inputs) == 1
    assert pipeline_inputs[0].kwargs["intent_input"] == "Greet the caller"
    assert (
        pipeline_inputs[0].kwargs["conversation_extra_system_prompt"]
        == "Keep answers concise"
    )
    assert pipeline_runs[0].kwargs["start_stage"] == "intent"
    assert pipeline_runs[0].kwargs["end_stage"] == "tts"
    assert len(audio_calls) == 1
    assert audio_calls[0]["conversation_id"] == "opening-conversation"
    assert (
        audio_calls[0]["conversation_extra_system_prompt"]
        == "Keep answers concise"
    )


def test_assist_system_prompt_does_not_create_opening_turn():
    assist_mod, mock_ap, _, _ = _assist_ctx()
    audio_calls = []

    async def mock_audio_pipeline(hass, **kwargs):
        audio_calls.append(kwargs)

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        system_prompt="Keep answers concise",
        max_turns=2,
    )
    _run_bridge_session(bridge)

    assert len(audio_calls) == 2
    assert all(
        call["conversation_extra_system_prompt"] == "Keep answers concise"
        for call in audio_calls
    )


def test_assist_reuses_supplied_conversation_id_for_audio_turns():
    assist_mod, mock_ap, _, _ = _assist_ctx()
    conversation_ids = []

    async def mock_audio_pipeline(hass, **kwargs):
        conversation_ids.append(kwargs["conversation_id"])

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=MagicMock(),
        on_done_fn=MagicMock(),
        conversation_id="existing-conversation",
        max_turns=2,
    )
    _run_bridge_session(bridge)

    assert conversation_ids == ["existing-conversation", "existing-conversation"]


def test_assist_initial_prompt_reuses_supplied_conversation_id():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    audio_calls = []
    _, _, fake_run, fake_input, fake_chat_session = _initial_prompt_doubles(PET, PE)

    async def mock_audio_pipeline(hass, **kwargs):
        audio_calls.append(kwargs)

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline
    hass = MagicMock()

    with (
        patch.object(assist_mod, "PipelineRun", fake_run),
        patch.object(assist_mod, "PipelineInput", fake_input),
        patch.object(assist_mod, "chat_session", fake_chat_session),
    ):
        bridge = assist_mod.AssistBridge(
            hass,
            play_source_fn=MagicMock(),
            on_done_fn=MagicMock(),
            conversation_id="existing-conversation",
            initial_prompt="Continue our conversation",
            max_turns=1,
        )
        _run_bridge_session(bridge)

    fake_chat_session.async_get_chat_session.assert_called_once_with(
        hass, "existing-conversation"
    )
    assert audio_calls[0]["conversation_id"] == "existing-conversation"


def test_assist_initial_prompt_waits_for_tts_before_listening():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value
    audio_calls = []
    play_source = MagicMock()

    _, _, fake_run, fake_input, fake_chat_session = _initial_prompt_doubles(
        PET, PE, (PET.TTS_END, {"tts_output": {"token": "opening-tts"}})
    )

    async def stream_result():
        yield b"RIFF...."

    stream = MagicMock()
    stream.async_stream_result = stream_result
    mock_tts.async_get_stream.return_value = stream

    async def mock_audio_pipeline(hass, **kwargs):
        audio_calls.append(kwargs)

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline

    async def run():
        with (
            patch.object(assist_mod, "PipelineRun", fake_run),
            patch.object(assist_mod, "PipelineInput", fake_input),
            patch.object(assist_mod, "chat_session", fake_chat_session),
        ):
            bridge = assist_mod.AssistBridge(
                MagicMock(),
                play_source_fn=play_source,
                on_done_fn=MagicMock(),
                initial_prompt="Greet the caller",
                max_turns=1,
            )
            bridge.start()
            for _ in range(50):
                await asyncio.sleep(0.01)
                if play_source.called:
                    break
            assert play_source.called
            assert audio_calls == []
            assert bridge.session_task is not None
            assert not bridge.session_task.done()
            bridge.on_playback_done()
            await bridge.session_task

    try:
        asyncio.run(run())
    finally:
        mock_tts.async_get_stream.return_value = original_get_stream

    assert len(audio_calls) == 1


def test_assist_initial_prompt_barge_in_becomes_first_turn_preroll():
    assist_mod, mock_ap, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value
    original_min = assist_mod._VAD_MIN_SPEECH_FRAMES
    original_micro_vad = assist_mod.MicroVad
    preroll_queue_sizes = []
    play_source = MagicMock()
    stop_audio = MagicMock()

    _, _, fake_run, fake_input, fake_chat_session = _initial_prompt_doubles(
        PET, PE, (PET.TTS_END, {"tts_output": {"token": "opening-tts"}})
    )

    class StubVad:
        def Process10ms(self, frame: bytes) -> float:
            return 0.9

    async def stream_result():
        yield b"RIFF...."

    stream = MagicMock()
    stream.async_stream_result = stream_result
    mock_tts.async_get_stream.return_value = stream

    async def mock_audio_pipeline(hass, **kwargs):
        preroll_queue_sizes.append(kwargs["stt_stream"].queue.qsize())

    mock_ap.async_pipeline_from_audio_stream.reset_mock()
    mock_ap.async_pipeline_from_audio_stream.side_effect = mock_audio_pipeline
    assist_mod.MicroVad = lambda: StubVad()
    assist_mod._VAD_MIN_SPEECH_FRAMES = 2

    async def run():
        with (
            patch.object(assist_mod, "PipelineRun", fake_run),
            patch.object(assist_mod, "PipelineInput", fake_input),
            patch.object(assist_mod, "chat_session", fake_chat_session),
        ):
            bridge = assist_mod.AssistBridge(
                MagicMock(),
                play_source_fn=play_source,
                on_done_fn=MagicMock(),
                initial_prompt="Greet the caller",
                barge_in=True,
                stop_audio_fn=stop_audio,
                max_turns=1,
            )
            bridge.start()
            for _ in range(50):
                await asyncio.sleep(0.01)
                if play_source.called:
                    break
            assert play_source.called
            frame = b"\x00\x01" * (assist_mod._VAD_FRAME_BYTES // 2)
            bridge.write(frame)
            assert bridge.session_task is not None
            await bridge.session_task

    try:
        asyncio.run(run())
    finally:
        mock_tts.async_get_stream.return_value = original_get_stream
        assist_mod._VAD_MIN_SPEECH_FRAMES = original_min
        assist_mod.MicroVad = original_micro_vad

    stop_audio.assert_called_once_with(flush=True)
    assert preroll_queue_sizes[0] > 0


def test_assist_ignores_prior_playback_done_while_tts_is_pending():
    assist_mod, _, PET, PE = _assist_ctx()
    mock_tts = sys.modules["homeassistant.components.tts"]
    original_get_stream = mock_tts.async_get_stream.return_value
    media_playing = {"value": True}
    play_source = MagicMock()

    async def stream_result():
        yield b"RIFF...."

    stream = MagicMock()
    stream.async_stream_result = stream_result
    mock_tts.async_get_stream.return_value = stream

    bridge = assist_mod.AssistBridge(
        MagicMock(),
        play_source_fn=play_source,
        on_done_fn=MagicMock(),
        media_playing_fn=lambda: media_playing["value"],
    )

    async def run():
        bridge._on_pipeline_event(
            PE(PET.TTS_END, {"tts_output": {"token": "opening-tts"}})
        )
        await asyncio.sleep(0.03)
        assert bridge._background_tasks
        bridge.on_playback_done()
        assert not bridge._tx_done.is_set()
        media_playing["value"] = False
        for _ in range(50):
            await asyncio.sleep(0.01)
            if play_source.called:
                break
        assert play_source.called
        assert bridge._tx_wait == "tts"
        bridge.on_playback_done()
        assert bridge._tx_done.is_set()
        bridge.close()
        await asyncio.sleep(0)

    try:
        asyncio.run(run())
    finally:
        mock_tts.async_get_stream.return_value = original_get_stream


def test_start_assist_service_accepts_and_forwards_prompts():
    from unittest.mock import AsyncMock

    _assist_ctx()
    helpers = sys.modules["homeassistant.helpers"]
    original_cv_attr = helpers.config_validation
    original_cv_module = sys.modules["homeassistant.helpers.config_validation"]
    original_service_module = sys.modules.get("homeassistant.helpers.service")
    cv_stub = types.SimpleNamespace(
        boolean=bool,
        match_all=lambda value: value,
        positive_int=int,
        string=str,
        make_entity_service_schema=lambda schema: vol.Schema(schema),
    )
    helpers.config_validation = cv_stub
    sys.modules["homeassistant.helpers.config_validation"] = cv_stub
    service_stub = types.ModuleType("homeassistant.helpers.service")
    service_stub.async_extract_config_entry_ids = AsyncMock()
    sys.modules["homeassistant.helpers.service"] = service_stub
    try:
        module_name = f"{_CC_PKG}._integration_init_test"
        spec = importlib.util.spec_from_file_location(
            module_name, os.path.join(os.path.abspath(_COMPONENT), "__init__.py")
        )
        integration = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = integration
        spec.loader.exec_module(integration)
    finally:
        helpers.config_validation = original_cv_attr
        sys.modules["homeassistant.helpers.config_validation"] = original_cv_module
        if original_service_module is None:
            sys.modules.pop("homeassistant.helpers.service", None)
        else:
            sys.modules["homeassistant.helpers.service"] = original_service_module

    service_data = integration.SERVICE_ASSIST_SCHEMA(
        {
            "conversation_id": "existing-conversation",
            "initial_prompt": "Greet the caller",
            "system_prompt": "Keep answers concise",
        }
    )
    assert service_data["conversation_id"] == "existing-conversation"
    assert service_data["initial_prompt"] == "Greet the caller"
    assert service_data["system_prompt"] == "Keep answers concise"

    trigger_assist = AsyncMock()
    entry = MagicMock()
    entry.domain = "sip"
    entry.state.value = "loaded"
    entry.runtime_data = {"trigger_assist_fn": trigger_assist}

    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.config_entries.async_entries.return_value = [entry]
    hass.config_entries.async_get_entry.return_value = entry
    integration.async_extract_config_entry_ids = AsyncMock(return_value={"entry-1"})

    async def run_service():
        await integration.async_register_services(hass)
        registration = next(
            call
            for call in hass.services.async_register.call_args_list
            if call.args[:2] == ("sip", "start_assist")
        )
        handler = registration.args[2]
        await handler(types.SimpleNamespace(data=service_data))

    asyncio.run(run_service())
    trigger_assist.assert_awaited_once_with(
        conversation_id="existing-conversation",
        initial_prompt="Greet the caller",
        system_prompt="Keep answers concise",
    )


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


def test_sip_device_id_lookup():
    """_sip_device_id uses async_get_device_by_identifier."""
    import types
    from unittest.mock import MagicMock, patch

    if "homeassistant.helpers.service" not in sys.modules:
        service_stub = types.ModuleType("homeassistant.helpers.service")
        service_stub.async_extract_config_entry_ids = MagicMock()
        sys.modules["homeassistant.helpers.service"] = service_stub

    init_mod = _load_component_module("__init__")

    hass = MagicMock()
    mock_dr = MagicMock()
    mock_device = MagicMock(id="dev_12345")
    mock_dr.async_get_device_by_identifier.return_value = mock_device

    with patch.object(init_mod.dr, "async_get", return_value=mock_dr):
        dev_id = init_mod._sip_device_id(hass, "entry_abc")
        assert dev_id == "dev_12345"
        mock_dr.async_get_device_by_identifier.assert_called_once_with(
            ("sip", "entry_abc"), config_entry_id="entry_abc"
        )

        mock_dr.async_get_device_by_identifier.return_value = None
        assert init_mod._sip_device_id(hass, "entry_abc") is None


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
