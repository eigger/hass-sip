"""Asyncio RTP audio session for a single G.711 call.

Owns one UDP socket, paces transmission at 20 ms (160 samples @ 8 kHz), decodes
received audio and emits / receives RFC 2833 telephone-event (DTMF). All PCM
exchanged with callers is signed-16-bit-LE, 8 kHz, mono.

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

from . import g711

_LOGGER = logging.getLogger(__name__)

SAMPLES_PER_FRAME = 160  # 20 ms @ 8 kHz
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # s16le
FRAME_SEC = 0.02
_DTMF_TONE_SAMPLES = 8 * SAMPLES_PER_FRAME  # ~160 ms
_DTMF_END_PACKETS = 3
_TX_BUFFER_MAX = 8000 * 2  # ~1 s of audio (bytes), drop oldest beyond this


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
        self.payload_type = 0
        self.dtmf_pt = 101
        self.send_silence = True

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

    # -- configuration --------------------------------------------------
    def set_remote(self, ip: str, port: int) -> None:
        self._remote = (ip, port)

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
        """Queue captured PCM (s16le, 8 kHz, mono) for transmission."""
        if self._transport is None:
            return
        self._tx_buffer.extend(pcm_le)
        if len(self._tx_buffer) > _TX_BUFFER_MAX:
            overflow = len(self._tx_buffer) - _TX_BUFFER_MAX
            del self._tx_buffer[:overflow]

    def queue_dtmf(self, digits: str) -> None:
        if self.dtmf_pt < 0:
            _LOGGER.warning("Remote did not offer telephone-event; DTMF dropped")
            return
        self._dtmf_queue.extend(digits)

    def tx_idle(self) -> bool:
        return len(self._tx_buffer) < FRAME_BYTES and not self._dtmf_queue and not self._dtmf_active

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
        self._send(header + g711.encode(frame, self.payload_type))
        self._seq += 1
        self._timestamp += SAMPLES_PER_FRAME
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
                self._timestamp = self._dtmf_timestamp + self._dtmf_duration + SAMPLES_PER_FRAME
                self._first_packet = True  # re-mark audio after DTMF
        else:
            self._dtmf_duration += SAMPLES_PER_FRAME

    def _silence_frame(self) -> bytes:
        return b"\x00" * FRAME_BYTES

    # -- sender loop ----------------------------------------------------
    async def _sender(self) -> None:
        next_t = self._loop.time()
        while True:
            next_t += FRAME_SEC
            try:
                if self._remote is not None:
                    if self._dtmf_active or self._dtmf_queue:
                        self._send_dtmf_packet()
                    elif len(self._tx_buffer) >= FRAME_BYTES:
                        frame = bytes(self._tx_buffer[:FRAME_BYTES])
                        del self._tx_buffer[:FRAME_BYTES]
                        self._send_audio_packet(frame)
                    elif self.send_silence:
                        self._send_audio_packet(self._silence_frame())
            except Exception:  # noqa: BLE001 - never let pacing die mid-call
                _LOGGER.exception("RTP send error")
            delay = next_t - self._loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = self._loop.time()  # we fell behind; resync pacing

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
                if self.on_dtmf is not None:
                    event = data[header_len]
                    if event <= 9:
                        c = chr(ord("0") + event)
                    elif event == 10:
                        c = "*"
                    elif event == 11:
                        c = "#"
                    elif event <= 15:
                        c = chr(ord("A") + (event - 12))
                    else:
                        c = "?"
                    self.on_dtmf(c)
            return

        if pt not in (0, 8):
            return
        if self.on_audio is not None:
            self.on_audio(g711.decode(data[header_len:], pt))
