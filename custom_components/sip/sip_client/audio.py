"""Pluggable audio sources / sinks for the RTP stream.

This is the extension seam. The SIP/RTP core only knows about two PCM ports:

* :class:`AudioSource` feeds TX audio into ``RtpSession.push_tx_audio``.
* :class:`AudioSink` receives RX audio from ``RtpSession.on_audio``.

Today's implementations cover "play a file / TTS to the far end" (TX) and
"discard / record" (RX). A microphone source or a media_player sink can be added
later by implementing the same tiny interfaces, without touching the SIP core.
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct
import wave
from abc import ABC, abstractmethod
from typing import Callable

_LOGGER = logging.getLogger(__name__)

PushFn = Callable[[bytes], None]
ActiveFn = Callable[[], bool]

# How far ahead of real time a source may run. RtpSession's TX buffer holds
# one second and drops from the *front* when it overflows, so staying well
# under that turns event-loop jitter into slack instead of dropped audio.
_PCM_PREBUFFER_SEC = 0.5


def default_pcm_frame_bytes(sample_rate: int) -> int:
    """Return 20 ms of s16le mono PCM at ``sample_rate``."""
    return sample_rate // 50 * 2


class _RealtimePacer:
    """Pace PCM against an absolute deadline rather than a fixed per-frame sleep.

    Sleeping a fixed ~frame duration each iteration accumulates every
    scheduling delay: on a busy event loop the source slips behind real time,
    RtpSession finds nothing queued and transmits comfort silence instead,
    which is heard as choppy audio. Tracking the deadline in absolute terms
    lets the loop catch up after a stall, and the small prebuffer absorbs
    ordinary jitter outright.
    """

    def __init__(self, sample_rate: int) -> None:
        self._bytes_per_sec = sample_rate * 2
        self._loop = asyncio.get_running_loop()
        self._start = self._loop.time()
        self._queued_sec = 0.0

    def account(self, pcm_bytes: int) -> None:
        """Record PCM handed to the RTP TX path."""
        self._queued_sec += pcm_bytes / self._bytes_per_sec

    async def wait(self) -> None:
        ahead = self._queued_sec - (self._loop.time() - self._start)
        if ahead > _PCM_PREBUFFER_SEC:
            await asyncio.sleep(ahead - _PCM_PREBUFFER_SEC)
        else:
            # Inside the prebuffer window (or behind it): yield without
            # stalling so a delayed loop can catch back up to real time.
            await asyncio.sleep(0)


class AudioSource(ABC):
    """Produces s16le / mono PCM at the negotiated sample rate for RTP TX."""

    @abstractmethod
    async def run(self, push: PushFn, is_active: ActiveFn) -> None:
        """Stream until exhausted or ``is_active()`` returns False."""


class AudioSink(ABC):
    """Consumes s16le / mono PCM coming off the RTP RX path."""

    @abstractmethod
    def write(self, pcm_le: bytes) -> None:
        ...

    def close(self) -> None:  # optional
        ...


class NullSink(AudioSink):
    """Default sink: keeps the stream alive but discards audio.

    Tracks bytes received so the bidirectional stream can be verified.
    """

    def __init__(self) -> None:
        self.bytes_received = 0

    def write(self, pcm_le: bytes) -> None:
        self.bytes_received += len(pcm_le)


class WavRecorderSink(AudioSink):
    """Records received audio to a WAV file (handy for verifying the RX path)."""

    def __init__(self, path: str, sample_rate: int = 8000) -> None:
        self._wav = wave.open(path, "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)

    def write(self, pcm_le: bytes) -> None:
        self._wav.writeframes(pcm_le)

    def close(self) -> None:
        try:
            self._wav.close()
        except Exception:  # noqa: BLE001
            pass


class _ConfiguredPcmSource(AudioSource):
    """Shared negotiated-rate / paced-frame helpers for in-process PCM sources."""

    def __init__(
        self,
        *,
        sample_rate: int = 8000,
        pcm_frame_bytes: int | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._pcm_frame_bytes = (
            pcm_frame_bytes
            if pcm_frame_bytes is not None
            else default_pcm_frame_bytes(sample_rate)
        )

    def configure(self, sample_rate: int, pcm_frame_bytes: int) -> None:
        """Update output rate to match the negotiated codec."""
        self._sample_rate = sample_rate
        self._pcm_frame_bytes = pcm_frame_bytes

    async def _push_paced_pcm(
        self, push: PushFn, is_active: ActiveFn, pcm: bytes
    ) -> None:
        offset = 0
        frame = self._pcm_frame_bytes
        pacer = _RealtimePacer(self._sample_rate)
        while is_active() and offset < len(pcm):
            chunk = pcm[offset : offset + frame]
            push(chunk)
            offset += frame
            pacer.account(len(chunk))
            await pacer.wait()


class FfmpegAudioSource(_ConfiguredPcmSource):
    """Decode any media (file path, URL, or raw bytes) to mono PCM via ffmpeg.

    ffmpeg transparently handles WAV/MP3/etc. and produces the exact format the
    active codec encoder expects, so this single source covers audio files, HTTP
    URLs and TTS output. It paces itself at ~real time so the RTP TX buffer stays
    small (no dropped audio).

    ``sample_rate`` / ``pcm_frame_bytes`` default to G.711 (8 kHz / 320 B). Call
    :meth:`configure` after negotiation when a different rate is active.
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        *,
        url: str | None = None,
        data: bytes | None = None,
        sample_rate: int = 8000,
        pcm_frame_bytes: int | None = None,
    ) -> None:
        super().__init__(sample_rate=sample_rate, pcm_frame_bytes=pcm_frame_bytes)
        if (url is None) == (data is None):
            raise ValueError("Provide exactly one of url/data")
        self._bin = ffmpeg_bin
        self._url = url
        self._data = data

    async def run(self, push: PushFn, is_active: ActiveFn) -> None:
        src = self._url if self._url is not None else "pipe:0"
        proc = await asyncio.create_subprocess_exec(
            self._bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            src,
            "-ac",
            "1",
            "-ar",
            str(self._sample_rate),
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE if self._data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        try:
            if self._data is not None and proc.stdin is not None:
                proc.stdin.write(self._data)
                proc.stdin.write_eof()

            # Read ~20 ms at a time and pace to real time so the RTP buffer
            # never overflows and drops audio.
            frame = self._pcm_frame_bytes
            pacer = _RealtimePacer(self._sample_rate)
            buf = bytearray()
            while is_active():
                chunk = await proc.stdout.read(frame)
                if not chunk:
                    break
                # ``read`` returns *up to* ``frame`` bytes. Emit whole frames
                # only, so a short read does not cost a full frame of pacing.
                buf.extend(chunk)
                while len(buf) >= frame:
                    push(bytes(buf[:frame]))
                    del buf[:frame]
                    pacer.account(frame)
                    await pacer.wait()
            if buf and is_active():
                push(bytes(buf))
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()


_TONE_PCM_CACHE: dict[tuple[int, int, int, float, int], bytes] = {}


def _render_tone_pcm(
    *,
    sample_rate: int,
    freq_hz: int,
    duration_ms: int,
    amplitude: float,
    fade_ms: int,
) -> bytes:
    key = (sample_rate, freq_hz, duration_ms, amplitude, fade_ms)
    cached = _TONE_PCM_CACHE.get(key)
    if cached is not None:
        return cached

    n_samples = int(sample_rate * duration_ms / 1000)
    if n_samples <= 0:
        return b""

    fade_samples = min(int(sample_rate * fade_ms / 1000), n_samples // 2)
    peak = int(amplitude * 32767)
    omega = 2.0 * math.pi * freq_hz / sample_rate
    buf = bytearray(n_samples * 2)
    last = n_samples - 1
    for i in range(n_samples):
        env = 1.0
        if fade_samples > 0:
            if i < fade_samples:
                env = i / fade_samples
            elif i > last - fade_samples:
                env = (last - i) / fade_samples
        struct.pack_into("<h", buf, i * 2, int(peak * env * math.sin(omega * i)))

    pcm = bytes(buf)
    _TONE_PCM_CACHE[key] = pcm
    return pcm


class ToneAudioSource(_ConfiguredPcmSource):
    """Generate a short sine burst for RTP TX without ffmpeg.

    A fade in/out is applied so the burst does not click on codecs that
    dislike an abrupt sine cutoff. Frames are paced like
    :class:`FfmpegAudioSource` (~18 ms sleep per 20 ms frame).
    """

    def __init__(
        self,
        freq_hz: int = 880,
        duration_ms: int = 120,
        amplitude: float = 0.25,
        fade_ms: int = 10,
        sample_rate: int = 8000,
        pcm_frame_bytes: int | None = None,
    ) -> None:
        super().__init__(sample_rate=sample_rate, pcm_frame_bytes=pcm_frame_bytes)
        self._freq_hz = freq_hz
        self._duration_ms = duration_ms
        self._amplitude = amplitude
        self._fade_ms = fade_ms

    def _render_pcm(self) -> bytes:
        return _render_tone_pcm(
            sample_rate=self._sample_rate,
            freq_hz=self._freq_hz,
            duration_ms=self._duration_ms,
            amplitude=self._amplitude,
            fade_ms=self._fade_ms,
        )

    async def run(self, push: PushFn, is_active: ActiveFn) -> None:
        await self._push_paced_pcm(push, is_active, self._render_pcm())
