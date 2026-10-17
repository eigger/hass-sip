"""ITU-T G.711 mu-law / A-law companding (pure Python port of g711.h).

PCM is signed 16-bit linear, 8 kHz, mono. Each encoded sample is one byte.
Ported faithfully from the ESPHome ``sip_client`` component so both ends behave
identically.
"""

from __future__ import annotations

import struct

_BIAS = 0x84
_CLIP = 32635


def linear_to_ulaw(pcm: int) -> int:
    sign = (pcm >> 8) & 0x80
    if sign != 0:
        pcm = -pcm
    if pcm > _CLIP:
        pcm = _CLIP
    pcm = pcm + _BIAS
    exponent = 7
    mask = 0x4000
    while (pcm & mask) == 0 and exponent > 0:
        exponent -= 1
        mask >>= 1
    mantissa = (pcm >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def ulaw_to_linear(ulaw: int) -> int:
    ulaw = (~ulaw) & 0xFF
    sign = ulaw & 0x80
    exponent = (ulaw >> 4) & 0x07
    mantissa = ulaw & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


def linear_to_alaw(pcm: int) -> int:
    sign = ((~pcm) >> 8) & 0x80
    if sign == 0:
        pcm = -pcm
    if pcm > _CLIP:
        pcm = _CLIP
    if pcm >= 256:
        exponent = 7
        mask = 0x4000
        while (pcm & mask) == 0 and exponent > 0:
            exponent -= 1
            mask >>= 1
        mantissa = (pcm >> (exponent + 3)) & 0x0F
        alaw = (exponent << 4) | mantissa
    else:
        alaw = pcm >> 4
    return (alaw ^ sign ^ 0x55) & 0xFF


def alaw_to_linear(alaw: int) -> int:
    alaw ^= 0x55
    sign = alaw & 0x80
    exponent = (alaw >> 4) & 0x07
    mantissa = alaw & 0x0F
    sample = (mantissa << 4) + 8
    if exponent != 0:
        sample += 0x100
        sample <<= exponent - 1
    return sample if sign else -sample


# Precomputed lookup tables (encode is the hot path on the send side).
_ULAW_ENCODE = bytes(
    linear_to_ulaw(s if s < 32768 else s - 65536) for s in range(65536)
)
_ALAW_ENCODE = bytes(
    linear_to_alaw(s if s < 32768 else s - 65536) for s in range(65536)
)
_ULAW_DECODE = [ulaw_to_linear(b) for b in range(256)]
_ALAW_DECODE = [alaw_to_linear(b) for b in range(256)]

# Encoded value of a silence sample, used for comfort-noise frames.
ULAW_SILENCE = _ULAW_ENCODE[0]
ALAW_SILENCE = _ALAW_ENCODE[0]


def encode(pcm_le: bytes, payload_type: int) -> bytes:
    """Encode signed-16-bit-LE PCM to G.711 (pt 0 = PCMU, pt 8 = PCMA)."""
    table = _ALAW_ENCODE if payload_type == 8 else _ULAW_ENCODE
    samples = struct.unpack_from("<%dh" % (len(pcm_le) // 2), pcm_le)
    return bytes(table[s & 0xFFFF] for s in samples)


def decode(encoded: bytes, payload_type: int) -> bytes:
    """Decode G.711 to signed-16-bit-LE PCM."""
    table = _ALAW_DECODE if payload_type == 8 else _ULAW_DECODE
    return struct.pack("<%dh" % len(encoded), *(table[b] for b in encoded))
