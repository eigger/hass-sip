"""Asyncio RTP audio session for a single negotiated codec.

Owns one UDP socket, paces transmission at 20 ms, encodes/decodes via the
active :class:`codecs.Codec`, and emits / receives RFC 2833 telephone-event
(DTMF). All PCM exchanged with callers is signed-16-bit-LE mono at the codec's
sample rate.

Port of rtp_session.cpp. The audio I/O is fully decoupled:

* received audio -> ``on_audio`` callback (an :class:`AudioSink` plugs in here)
* transmitted audio <- :meth:`push_tx_audio` (an :class:`AudioSource` feeds here)

When no TX audio is queued during an active call, comfort-silence frames are
sent so the bidirectional stream / NAT mapping stays alive.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import Callable

from . import codecs
from .codecs import Codec

_LOGGER = logging.getLogger(__name__)

# G.711 defaults kept as module names for callers that still import them.
SAMPLES_PER_FRAME = 160  # 20 ms @ 8 kHz clock
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # s16le @ 8 kHz
FRAME_SEC = 0.02
_DTMF_TONE_SAMPLES = 8 * SAMPLES_PER_FRAME  # ~160 ms, in CLOCK ticks
_DTMF_END_PACKETS = 3


def _dtmf_event_to_char(event: int) -> str | None:
    """RFC 4733 event code -> DTMF character, or None for codes we do not expose."""
    if event <= 9:
        return chr(ord("0") + event)
    if event == 10:
        return "*"
    if event == 11:
        return "#"
    if event <= 15:
        return chr(ord("A") + (event - 12))
    return None


def _dtmf_char_to_event(c: str) -> int:
    if "0" <= c <= "9":
        return ord(c) - ord("0")
    if c == "*":
        return 10
    if c == "#":
        return 11
    c = c.upper()
    if "A" <= c <= "D":
        return 12 + (ord(c) - ord("A"))
    return -1


class _RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: D401
        self._on_packet(data)

    def error_received(self, exc) -> None:
        _LOGGER.debug("RTP socket error: %s", exc)


class RtpSession:
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._transport: asyncio.DatagramTransport | None = None
        self._sender_task: asyncio.Task | None = None

        self._remote: tuple[str, int] | None = None
        self.dtmf_pt = 101
        self.send_silence = True
        self.tx_enabled = True
        self._tx_paused_at: float | None = None

        self._seq = 0
        self._timestamp = 0
        self._ssrc = 0
        self._first_packet = True

        self._tx_buffer = bytearray()
        self._dtmf_queue: list[str] = []
        self._dtmf_active = False
        self._dtmf_event = -1
        self._dtmf_duration = 0
        self._dtmf_timestamp = 0
        self._dtmf_end_packets = 0

        # RX de-duplication: a telephone-event is repeated across many packets
        # that all share one RTP timestamp, so the timestamp identifies the
        # keypress. -1 means "no event seen yet".
        self._rx_dtmf_timestamp = -1
        self._rx_dtmf_pt_warned = False

        self.on_audio: Callable[[bytes], None] | None = None
        self.on_dtmf: Callable[[str], None] | None = None

        # Codec-derived pacing / encode state (defaults = G.711 PCMU).
        self._codec: Codec = codecs.DEFAULT
        self.payload_type = self._codec.payload_type
        self._pcm_frame_bytes = self._codec.pcm_frame_bytes
        self._ts_increment = self._codec.ts_increment
        self._tx_buffer_max = self._codec.sample_rate * 2
        self._encode = self._codec.new_encoder()
        self._decode = self._codec.new_decoder()
        # Per-PT decoder cache so off-PT (or late) packets keep ADPCM state.
        self._decoders: dict[int, Callable[[bytes], bytes]] = {
            self.payload_type: self._decode
        }

    # -- configuration --------------------------------------------------
    def set_remote(self, ip: str, port: int) -> None:
        self._remote = (ip, port)

    def set_tx_enabled(self, enabled: bool) -> None:
        """Gate RTP transmission; on resume, catch up the RTP timestamp.

        While TX is paused the sender loop does not advance ``_timestamp``.
        Jumping it by the elapsed 20 ms frames (and re-marking the next
        packet) keeps a long hold from looking like a burst of late packets.
        """
        if enabled == self.tx_enabled:
            return
        if enabled:
            if self._tx_paused_at is not None:
                elapsed = max(0.0, self._loop.time() - self._tx_paused_at)
                frames = int(elapsed / FRAME_SEC)
                if frames:
                    self._timestamp = (
                        self._timestamp + frames * self._ts_increment
                    ) & 0xFFFFFFFF
                self._first_packet = True
            self._tx_paused_at = None
            self.tx_enabled = True
            return
        self._tx_paused_at = self._loop.time()
        self.tx_enabled = False
        self.flush_tx_buffer()

    def clear_tx_pause(self) -> None:
        """Re-enable TX without catching up a hold gap (new/ended call)."""
        self.tx_enabled = True
        self._tx_paused_at = None

    def set_codec(self, codec: Codec) -> None:
        """Bind the negotiated codec and (re)create encoder/decoder state."""
        self._codec = codec
        self.payload_type = codec.payload_type
        self._pcm_frame_bytes = codec.pcm_frame_bytes
        self._ts_increment = codec.ts_increment
        self._tx_buffer_max = codec.sample_rate * 2
        self._encode = codec.new_encoder()
        self._decode = codec.new_decoder()
        self._decoders = {codec.payload_type: self._decode}

    def _decoder_for(self, pt: int) -> Callable[[bytes], bytes] | None:
        cached = self._decoders.get(pt)
        if cached is not None:
            return cached
        codec = codecs.BY_PT.get(pt)
        if codec is None:
            return None
        decode = codec.new_decoder()
        self._decoders[pt] = decode
        return decode

    @property
    def running(self) -> bool:
        return self._transport is not None

    # -- lifecycle ------------------------------------------------------
    async def start(self, local_port: int) -> bool:
        await self.stop()
        try:
            transport, _ = await self._loop.create_datagram_endpoint(
                lambda: _RtpProtocol(self._receive),
                local_addr=("0.0.0.0", local_port),
            )
        except OSError as err:
            _LOGGER.warning("RTP bind failed on port %s: %s", local_port, err)
            return False
        self._transport = transport

        self._seq = struct.unpack("<H", os.urandom(2))[0]
        self._timestamp = struct.unpack("<I", os.urandom(4))[0]
        self._ssrc = struct.unpack("<I", os.urandom(4))[0]
        self._first_packet = True
        self._tx_buffer.clear()
        self._dtmf_queue.clear()
        self._dtmf_active = False
        self._rx_dtmf_timestamp = -1
        # Fresh codec state for this call (important for stateful codecs).
        self.set_codec(self._codec)
        self._sender_task = self._loop.create_task(self._sender())
        _LOGGER.info(
            "RTP started on port %s (pt=%s, dtmf_pt=%s)",
            local_port,
            self.payload_type,
            self.dtmf_pt,
        )
        if self.dtmf_pt < 0:
            _LOGGER.warning(
                "Remote did not negotiate telephone-event (RFC 2833); inbound DTMF "
                "will only work if the device sends it via SIP INFO"
            )
        return True

    async def stop(self) -> None:
        if self._sender_task is not None:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
            self._sender_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._tx_buffer.clear()
        self._dtmf_queue.clear()
        self._dtmf_active = False
        self._rx_dtmf_timestamp = -1

    # -- TX -------------------------------------------------------------
    def push_tx_audio(self, pcm_le: bytes) -> None:
        """Queue captured PCM (s16le, mono, codec sample rate) for transmission."""
        if self._transport is None or not self.tx_enabled:
            return
        self._tx_buffer.extend(pcm_le)
        if len(self._tx_buffer) > self._tx_buffer_max:
            overflow = len(self._tx_buffer) - self._tx_buffer_max
            del self._tx_buffer[:overflow]

    def flush_tx_buffer(self) -> None:
        """Drop queued PCM not yet sent (e.g. after barge-in)."""
        self._tx_buffer.clear()

    def queue_dtmf(self, digits: str) -> None:
        if self.dtmf_pt < 0:
            _LOGGER.warning("Remote did not offer telephone-event; DTMF dropped")
            return
        self._dtmf_queue.extend(digits)

    def tx_idle(self) -> bool:
        return (
            len(self._tx_buffer) < self._pcm_frame_bytes
            and not self._dtmf_queue
            and not self._dtmf_active
        )

    # -- packet building ------------------------------------------------
    def _rtp_header(self, marker: bool, pt: int, timestamp: int) -> bytes:
        return struct.pack(
            ">BBHII",
            0x80,
            (0x80 if marker else 0x00) | (pt & 0x7F),
            self._seq & 0xFFFF,
            timestamp & 0xFFFFFFFF,
            self._ssrc & 0xFFFFFFFF,
        )

    def _send(self, packet: bytes) -> None:
        if self._transport is not None and self._remote is not None:
            self._transport.sendto(packet, self._remote)

    def _send_audio_packet(self, frame: bytes) -> None:
        header = self._rtp_header(self._first_packet, self.payload_type, self._timestamp)
        self._send(header + self._encode(frame))
        self._seq += 1
        self._timestamp += self._ts_increment
        self._first_packet = False

    def _send_dtmf_packet(self) -> None:
        if not self._dtmf_active:
            if not self._dtmf_queue:
                return
            event = _dtmf_char_to_event(self._dtmf_queue.pop(0))
            if event < 0:
                return
            self._dtmf_active = True
            self._dtmf_event = event
            self._dtmf_duration = 0
            self._dtmf_end_packets = 0
            self._dtmf_timestamp = self._timestamp

        end = self._dtmf_duration >= _DTMF_TONE_SAMPLES
        header = self._rtp_header(self._dtmf_duration == 0, self.dtmf_pt, self._dtmf_timestamp)
        payload = struct.pack(
            ">BBH",
            self._dtmf_event & 0xFF,
            (0x80 if end else 0x00) | 0x0A,  # E bit + volume 10
            self._dtmf_duration & 0xFFFF,
        )
        self._send(header + payload)
        self._seq += 1

        if end:
            self._dtmf_end_packets += 1
            if self._dtmf_end_packets >= _DTMF_END_PACKETS:
                self._dtmf_active = False
                # DTMF durations are in 8 kHz clock ticks for both codecs.
                self._timestamp = self._dtmf_timestamp + self._dtmf_duration + SAMPLES_PER_FRAME
                self._first_packet = True  # re-mark audio after DTMF
        else:
            self._dtmf_duration += SAMPLES_PER_FRAME

    def _silence_frame(self) -> bytes:
        return b"\x00" * self._pcm_frame_bytes

    # -- sender loop ----------------------------------------------------
    async def _sender(self) -> None:
        next_t = self._loop.time()
        while True:
            next_t += FRAME_SEC
            try:
                if self._remote is not None and self.tx_enabled:
                    if self._dtmf_active or self._dtmf_queue:
                        self._send_dtmf_packet()
                    elif len(self._tx_buffer) >= self._pcm_frame_bytes:
                        frame = bytes(self._tx_buffer[: self._pcm_frame_bytes])
                        del self._tx_buffer[: self._pcm_frame_bytes]
                        self._send_audio_packet(frame)
                    elif self.send_silence:
                        self._send_audio_packet(self._silence_frame())
            except Exception:  # noqa: BLE001 - never let pacing die mid-call
                _LOGGER.exception("RTP send error")
            delay = next_t - self._loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Running behind; resync so we don't spin.
                next_t = self._loop.time()

    # -- RX -------------------------------------------------------------
    def _receive(self, data: bytes) -> None:
        try:
            self._receive_impl(data)
        except Exception:  # noqa: BLE001 - a bad RTP packet must not kill the session
            _LOGGER.exception("Error handling RTP packet (ignored)")

    def _receive_impl(self, data: bytes) -> None:
        if len(data) < 12:
            return
        pt = data[1] & 0x7F
        marker = (data[1] & 0x80) != 0
        header_len = 12 + 4 * (data[0] & 0x0F)  # CSRC count
        if len(data) <= header_len:
            return

        payload_len = len(data) - header_len
        is_dtmf = pt == self.dtmf_pt if self.dtmf_pt >= 0 else False
        if not is_dtmf and 96 <= pt <= 127 and payload_len == 4:
            # Some ATAs send RFC 2833 without ever offering telephone-event in
            # their SDP (or on a different dynamic PT than negotiated). A 4-byte
            # payload on a dynamic PT is the telephone-event shape, so accept it
            # rather than dropping the keypress.
            if not self._rx_dtmf_pt_warned:
                self._rx_dtmf_pt_warned = True
                _LOGGER.info(
                    "Accepting inbound DTMF on unnegotiated payload type %s", pt
                )
            is_dtmf = True

        if is_dtmf:
            # Not every device sets the marker bit on the first packet of an
            # event, so key off the RTP timestamp instead: all packets of one
            # keypress repeat the same timestamp. The marker bit, when present,
            # still forces a new event (two identical digits back to back).
            timestamp = int.from_bytes(data[4:8], "big")
            if marker or timestamp != self._rx_dtmf_timestamp:
                self._rx_dtmf_timestamp = timestamp
                # Events above 15 (hook flash and up) are not DTMF digits;
                # passing them on would hand IVR menus a bogus keypress.
                c = _dtmf_event_to_char(data[header_len])
                if c is not None and self.on_dtmf is not None:
                    self.on_dtmf(c)
            return

        # DTMF (including the unnegotiated dynamic-PT fallback above) already
        # returned. Only decode payload types we know as audio codecs, and keep
        # a per-PT decoder so stateful codecs (G.722) are not reset every packet.
        decode = self._decoder_for(pt)
        if decode is None:
            return
        if self.on_audio is not None:
            self.on_audio(decode(data[header_len:]))
