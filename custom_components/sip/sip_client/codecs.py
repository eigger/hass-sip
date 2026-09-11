"""Audio codec registry.

One Codec entry per negotiable payload type. SDP generation, codec selection and
the RTP receive gate are all derived from SUPPORTED, so adding a codec is a
one-line change here.

Stateful codecs (G.722) need per-call encoder/decoder instances. Codec exposes
``new_encoder`` / ``new_decoder`` factories; G.711 returns stateless adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import g711
from . import g722

if TYPE_CHECKING:
    from .sip_message import SdpInfo

FRAME_SEC = 0.02

# PCM <-> payload transformers produced per call (or reused when stateless).
EncodeFn = Callable[[bytes], bytes]
DecodeFn = Callable[[bytes], bytes]


def _g711_encoder(payload_type: int) -> EncodeFn:
    def encode(pcm: bytes) -> bytes:
        return g711.encode(pcm, payload_type)

    return encode


def _g711_decoder(payload_type: int) -> DecodeFn:
    def decode(data: bytes) -> bytes:
        return g711.decode(data, payload_type)

    return decode


@dataclass(frozen=True)
class Codec:
    name: str  # SDP rtpmap name, e.g. "PCMU", "G722"
    payload_type: int  # static PT, or negotiated dynamic PT after choose()
    clock_rate: int  # RTP timestamp clock (G.722: 8000!)
    sample_rate: int  # real PCM rate (G.722: 16000)
    new_encoder: Callable[[], EncodeFn]
    new_decoder: Callable[[], DecodeFn]

    @property
    def pcm_frame_bytes(self) -> int:
        """Bytes of s16le PCM in one 20 ms frame."""
        return int(self.sample_rate * FRAME_SEC) * 2

    @property
    def ts_increment(self) -> int:
        """RTP timestamp ticks per 20 ms frame."""
        return int(self.clock_rate * FRAME_SEC)

    def with_payload_type(self, pt: int) -> Codec:
        """Return a copy bound to a (possibly dynamic) payload type."""
        if pt == self.payload_type:
            return self
        return Codec(
            self.name,
            pt,
            self.clock_rate,
            self.sample_rate,
            self.new_encoder,
            self.new_decoder,
        )


def _g722_encoder() -> EncodeFn:
    return g722.G722Encoder().encode


def _g722_decoder() -> DecodeFn:
    return g722.G722Decoder().decode


PCMU = Codec(
    "PCMU",
    0,
    8000,
    8000,
    lambda: _g711_encoder(0),
    lambda: _g711_decoder(0),
)
PCMA = Codec(
    "PCMA",
    8,
    8000,
    8000,
    lambda: _g711_encoder(8),
    lambda: _g711_decoder(8),
)
G722 = Codec(
    "G722",
    9,
    8000,  # RTP clock (RFC 3551); real PCM is 16 kHz
    16000,
    _g722_encoder,
    _g722_decoder,
)

# Order defines both the SDP offer order and the selection preference.
SUPPORTED: tuple[Codec, ...] = (G722, PCMU, PCMA)
BY_PT: dict[int, Codec] = {c.payload_type: c for c in SUPPORTED}
DEFAULT: Codec = PCMU

TELEPHONE_EVENT_PT = 101


def choose(sdp: SdpInfo) -> Codec:
    """Pick the most preferred codec the remote also offered.

    Prefers a static payload type present in ``offered_pts``. If the remote only
    advertised a codec under a dynamic PT (``a=rtpmap:96 PCMU/8000``), fall back
    to the name-based ``pcmu_pt`` / ``pcma_pt`` / ``g722_pt`` fields.
    """
    named = {
        "G722": sdp.g722_pt,
        "PCMU": sdp.pcmu_pt,
        "PCMA": sdp.pcma_pt,
    }
    for codec in SUPPORTED:
        if codec.payload_type in sdp.offered_pts:
            return codec
    for codec in SUPPORTED:
        npt = named.get(codec.name, -1)
        if npt >= 0:
            return codec.with_payload_type(npt)
    return DEFAULT


def keep_or_choose(current: Codec, sdp: SdpInfo) -> Codec:
    """Keep ``current`` when the offer still lists it; otherwise :func:`choose`.

    A mid-dialog refresh that still advertises the negotiated codec must not
    switch (or rebuild encoder state). An offer with no audio payloads — an
    offerless re-INVITE or a session-timer refresh — also leaves ``current``.
    """
    if current.payload_type in sdp.offered_pts:
        return current
    named = {
        "G722": sdp.g722_pt,
        "PCMU": sdp.pcmu_pt,
        "PCMA": sdp.pcma_pt,
    }
    npt = named.get(current.name, -1)
    if npt >= 0:
        return current.with_payload_type(npt)
    if not sdp.offered_pts and sdp.pcmu_pt < 0 and sdp.pcma_pt < 0 and sdp.g722_pt < 0:
        return current
    return choose(sdp)


def sdp_media_line(port: int, only: Codec | None = None) -> str:
    """Build the ``m=audio`` line. Pass ``only`` to answer with one codec (RFC 3264)."""
    if only is not None:
        pts = str(only.payload_type)
    else:
        pts = " ".join(str(c.payload_type) for c in SUPPORTED)
    return f"m=audio {port} RTP/AVP {pts} {TELEPHONE_EVENT_PT}\r\n"


def sdp_rtpmaps(only: Codec | None = None) -> str:
    """Build ``a=rtpmap`` lines. Pass ``only`` for a single-codec answer."""
    entries = (only,) if only is not None else SUPPORTED
    lines = "".join(
        f"a=rtpmap:{c.payload_type} {c.name}/{c.clock_rate}\r\n" for c in entries
    )
    return lines + f"a=rtpmap:{TELEPHONE_EVENT_PT} telephone-event/8000\r\n"
