"""ITU-T G.722 wideband ADPCM (64 kbit/s) — pure-Python port.

PCM is signed 16-bit little-endian, 16 kHz, mono. Encoded frames are 4:1
(320 samples / 20 ms -> 160 bytes).

Ported from sippy/libg722 (``g722_encode.c`` / ``g722_decode.c``), which Steve
Underwood placed in the public domain. Based on the Carnegie Mellon ADPCM
program (Copyright (c) CMU 1993, Chengxiang Lu and Alex Hauptmann); use for any
research or commercial purpose is unrestricted — acknowledgement of origin
appreciated.

Only the 64 kbit/s mode used in SIP practice is implemented. Encoder and
decoder are stateful across frames; create one instance per media stream.
"""
from __future__ import annotations

import array
import struct


def _saturate(amp: int) -> int:
    if amp > 32767:
        return 32767
    if amp < -32768:
        return -32768
    return amp


class _Band:
    __slots__ = (
        "s", "sp", "sz", "r", "a", "ap", "p", "d", "b", "bp", "sg", "nb", "det",
    )

    def __init__(self, det: int) -> None:
        self.s = 0
        self.sp = 0
        self.sz = 0
        self.r = [0, 0, 0]
        self.a = [0, 0, 0]
        self.ap = [0, 0, 0]
        self.p = [0, 0, 0]
        self.d = [0, 0, 0, 0, 0, 0, 0]
        self.b = [0, 0, 0, 0, 0, 0, 0]
        self.bp = [0, 0, 0, 0, 0, 0, 0]
        self.sg = [0, 0, 0, 0, 0, 0, 0]
        self.nb = 0
        self.det = det


def _block4(band: _Band, d: int) -> None:
    r = band.r
    a = band.a
    ap = band.ap
    p = band.p
    dd = band.d
    b = band.b
    bp = band.bp
    sg = band.sg

    # Block 4, RECONS
    dd[0] = d
    r[0] = _saturate(band.s + d)

    # Block 4, PARREC
    p[0] = _saturate(band.sz + d)

    # Block 4, UPPOL2
    sg[0] = p[0] >> 15
    sg[1] = p[1] >> 15
    sg[2] = p[2] >> 15
    wd1 = _saturate(a[1] << 2)

    wd2 = -wd1 if sg[0] == sg[1] else wd1
    if wd2 > 32767:
        wd2 = 32767
    wd3 = (wd2 >> 7) + (128 if sg[0] == sg[2] else -128)
    wd3 += (a[2] * 32512) >> 15
    if wd3 > 12288:
        wd3 = 12288
    elif wd3 < -12288:
        wd3 = -12288
    ap[2] = wd3

    # Block 4, UPPOL1
    sg[0] = p[0] >> 15
    sg[1] = p[1] >> 15
    wd1 = 192 if sg[0] == sg[1] else -192
    wd2 = (a[1] * 32640) >> 15

    ap[1] = _saturate(wd1 + wd2)
    wd3 = _saturate(15360 - ap[2])
    if ap[1] > wd3:
        ap[1] = wd3
    elif ap[1] < -wd3:
        ap[1] = -wd3

    # Block 4, UPZERO
    wd1 = 0 if d == 0 else 128
    sg[0] = d >> 15
    for i in range(1, 7):
        sg[i] = dd[i] >> 15
        wd2 = wd1 if sg[i] == sg[0] else -wd1
        bp[i] = _saturate(wd2 + ((b[i] * 32640) >> 15))

    # Block 4, DELAYA
    dd[6] = dd[5]
    dd[5] = dd[4]
    dd[4] = dd[3]
    dd[3] = dd[2]
    dd[2] = dd[1]
    dd[1] = dd[0]
    b[6] = bp[6]
    b[5] = bp[5]
    b[4] = bp[4]
    b[3] = bp[3]
    b[2] = bp[2]
    b[1] = bp[1]

    r[2] = r[1]
    r[1] = r[0]
    p[2] = p[1]
    p[1] = p[0]
    a[2] = ap[2]
    a[1] = ap[1]

    # Block 4, FILTEP
    wd1 = _saturate(r[1] + r[1])
    wd1 = (a[1] * wd1) >> 15
    wd2 = _saturate(r[2] + r[2])
    wd2 = (a[2] * wd2) >> 15
    band.sp = _saturate(wd1 + wd2)

    # Block 4, FILTEZ
    sz = 0
    for i in range(6, 0, -1):
        sz += (b[i] * _saturate(dd[i] + dd[i])) >> 15
    band.sz = _saturate(sz)

    # Block 4, PREDIC
    band.s = _saturate(band.sp + band.sz)


_QMF = (3, -11, 12, 32, -210, 951, 3876, -805, 362, -156, 53, -11)

_Q6 = (
    0, 35, 72, 110, 150, 190, 233, 276,
    323, 370, 422, 473, 530, 587, 650, 714,
    786, 858, 940, 1023, 1121, 1219, 1339, 1458,
    1612, 1765, 1980, 2195, 2557, 2919, 0, 0,
)
_ILN = (
    0, 63, 62, 31, 30, 29, 28, 27,
    26, 25, 24, 23, 22, 21, 20, 19,
    18, 17, 16, 15, 14, 13, 12, 11,
    10, 9, 8, 7, 6, 5, 4, 0,
)
_ILP = (
    0, 61, 60, 59, 58, 57, 56, 55,
    54, 53, 52, 51, 50, 49, 48, 47,
    46, 45, 44, 43, 42, 41, 40, 39,
    38, 37, 36, 35, 34, 33, 32, 0,
)
_WL = (-60, -30, 58, 172, 334, 538, 1198, 3042)
_RL42 = (0, 7, 6, 5, 4, 3, 2, 1, 7, 6, 5, 4, 3, 2, 1, 0)
_ILB = (
    2048, 2093, 2139, 2186, 2233, 2282, 2332,
    2383, 2435, 2489, 2543, 2599, 2656, 2714,
    2774, 2834, 2896, 2960, 3025, 3091, 3158,
    3228, 3298, 3371, 3444, 3520, 3597, 3676,
    3756, 3838, 3922, 4008,
)
_QM4 = (
    0, -20456, -12896, -8968,
    -6288, -4240, -2584, -1200,
    20456, 12896, 8968, 6288,
    4240, 2584, 1200, 0,
)
_QM2 = (-7408, -1616, 7408, 1616)
_IHN = (0, 1, 0)
_IHP = (0, 3, 2)
_WH = (0, -214, 798)
_RH2 = (2, 1, 2, 1)
_QM6 = (
    -136, -136, -136, -136,
    -24808, -21904, -19008, -16704,
    -14984, -13512, -12280, -11192,
    -10232, -9360, -8576, -7856,
    -7192, -6576, -6000, -5456,
    -4944, -4464, -4008, -3576,
    -3168, -2776, -2400, -2032,
    -1688, -1360, -1040, -728,
    24808, 21904, 19008, 16704,
    14984, 13512, 12280, 11192,
    10232, 9360, 8576, 7856,
    7192, 6576, 6000, 5456,
    4944, 4464, 4008, 3576,
    3168, 2776, 2400, 2032,
    1688, 1360, 1040, 728,
    432, 136, -432, -136,
)


class G722Encoder:
    """Stateful G.722 encoder (64 kbit/s, 16 kHz PCM in)."""

    __slots__ = ("_x", "_xi", "_band0", "_band1")

    def __init__(self) -> None:
        # 24-sample QMF history in a 48-slot buffer; _xi points at the next
        # write position (starts at 22 so the first pair lands at x[22]/x[23],
        # matching the C reference after its initial shuffle of zeros).
        self._x = [0] * 48
        self._xi = 22
        self._band0 = _Band(32)
        self._band1 = _Band(8)

    def encode(self, pcm_le: bytes) -> bytes:
        """Encode s16le PCM to G.722. Length must be a multiple of 4 bytes."""
        n = len(pcm_le) // 2
        if n & 1:
            raise ValueError("G.722 encode needs an even number of samples")
        amp = struct.unpack_from("<%dh" % n, pcm_le)
        out = bytearray(n >> 1)
        band0 = self._band0
        band1 = self._band1
        x = self._x
        xi = self._xi
        qmf = _QMF
        o = 0
        j = 0
        while j < n:
            # Place the new pair at the end of the 24-sample window.
            pos = xi
            x[pos] = amp[j]
            x[pos + 1] = amp[j + 1]
            j += 2

            # Window starts 22 samples before the new pair.
            base = pos - 22
            sumeven = 0
            sumodd = 0
            for i in range(12):
                sumodd += x[base + 2 * i] * qmf[i]
                sumeven += x[base + 2 * i + 1] * qmf[11 - i]
            xlow = (sumeven + sumodd) >> 14
            xhigh = (sumeven - sumodd) >> 14

            xi = pos + 2
            if xi >= 24:
                # Slide the live 24-sample window back to the start of the buffer.
                x[0:24] = x[xi - 24 : xi]
                xi = 24

            # Block 1L, SUBTRA / QUANTL
            el = _saturate(xlow - band0.s)
            wd = el if el >= 0 else -(el + 1)
            i = 1
            det0 = band0.det
            while i < 30:
                if wd < (_Q6[i] * det0) >> 12:
                    break
                i += 1
            ilow = _ILN[i] if el < 0 else _ILP[i]

            # Block 2L, INVQAL
            ril = ilow >> 2
            dlow = (det0 * _QM4[ril]) >> 15

            # Block 3L, LOGSCL / SCALEL
            nb = ((band0.nb * 127) >> 7) + _WL[_RL42[ril]]
            if nb < 0:
                nb = 0
            elif nb > 18432:
                nb = 18432
            band0.nb = nb
            wd1 = (nb >> 6) & 31
            wd2 = 8 - (nb >> 11)
            wd3 = (_ILB[wd1] << -wd2) if wd2 < 0 else (_ILB[wd1] >> wd2)
            band0.det = wd3 << 2

            _block4(band0, dlow)

            # Block 1H, SUBTRA / QUANTH
            eh = _saturate(xhigh - band1.s)
            wd = eh if eh >= 0 else -(eh + 1)
            det1 = band1.det
            mih = 2 if wd >= ((564 * det1) >> 12) else 1
            ihigh = _IHN[mih] if eh < 0 else _IHP[mih]

            # Block 2H, INVQAH
            dhigh = (det1 * _QM2[ihigh]) >> 15

            # Block 3H, LOGSCH / SCALEH
            nb = ((band1.nb * 127) >> 7) + _WH[_RH2[ihigh]]
            if nb < 0:
                nb = 0
            elif nb > 22528:
                nb = 22528
            band1.nb = nb
            wd1 = (nb >> 6) & 31
            wd2 = 10 - (nb >> 11)
            wd3 = (_ILB[wd1] << -wd2) if wd2 < 0 else (_ILB[wd1] >> wd2)
            band1.det = wd3 << 2

            _block4(band1, dhigh)
            out[o] = ((ihigh << 6) | ilow) & 0xFF
            o += 1

        self._xi = xi
        return bytes(out)


class G722Decoder:
    """Stateful G.722 decoder (64 kbit/s, 16 kHz PCM out)."""

    __slots__ = ("_x", "_xi", "_band0", "_band1")

    def __init__(self) -> None:
        self._x = [0] * 48
        self._xi = 22
        self._band0 = _Band(32)
        self._band1 = _Band(8)

    def decode(self, data: bytes) -> bytes:
        """Decode G.722 to s16le PCM (2 samples per encoded byte)."""
        out = array.array("h", [0]) * (len(data) << 1)
        band0 = self._band0
        band1 = self._band1
        x = self._x
        xi = self._xi
        qmf = _QMF
        o = 0
        for code in data:
            wd1 = code & 0x3F
            ihigh = (code >> 6) & 0x03
            wd2 = _QM6[wd1]
            wd1 >>= 2

            # Block 5L, LOW BAND INVQBL / RECONS / LIMIT
            det0 = band0.det
            wd2 = (det0 * wd2) >> 15
            rlow = band0.s + wd2
            if rlow > 16383:
                rlow = 16383
            elif rlow < -16384:
                rlow = -16384

            # Block 2L, INVQAL
            dlowt = (det0 * _QM4[wd1]) >> 15

            # Block 3L, LOGSCL / SCALEL
            nb = ((band0.nb * 127) >> 7) + _WL[_RL42[wd1]]
            if nb < 0:
                nb = 0
            elif nb > 18432:
                nb = 18432
            band0.nb = nb
            wd1s = (nb >> 6) & 31
            wd2s = 8 - (nb >> 11)
            wd3 = (_ILB[wd1s] << -wd2s) if wd2s < 0 else (_ILB[wd1s] >> wd2s)
            band0.det = wd3 << 2

            _block4(band0, dlowt)

            # High band
            det1 = band1.det
            dhigh = (det1 * _QM2[ihigh]) >> 15
            rhigh = dhigh + band1.s
            if rhigh > 16383:
                rhigh = 16383
            elif rhigh < -16384:
                rhigh = -16384

            nb = ((band1.nb * 127) >> 7) + _WH[_RH2[ihigh]]
            if nb < 0:
                nb = 0
            elif nb > 22528:
                nb = 22528
            band1.nb = nb
            wd1s = (nb >> 6) & 31
            wd2s = 10 - (nb >> 11)
            wd3 = (_ILB[wd1s] << -wd2s) if wd2s < 0 else (_ILB[wd1s] >> wd2s)
            band1.det = wd3 << 2

            _block4(band1, dhigh)

            # Receive QMF via ring buffer
            pos = xi
            x[pos] = rlow + rhigh
            x[pos + 1] = rlow - rhigh
            base = pos - 22
            xout1 = 0
            xout2 = 0
            for i in range(12):
                xout2 += x[base + 2 * i] * qmf[i]
                xout1 += x[base + 2 * i + 1] * qmf[11 - i]
            out[o] = _saturate(xout1 >> 11)
            out[o + 1] = _saturate(xout2 >> 11)
            o += 2

            xi = pos + 2
            if xi >= 24:
                x[0:24] = x[xi - 24 : xi]
                xi = 24

        self._xi = xi
        return out.tobytes()
