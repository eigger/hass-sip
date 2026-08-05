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
import wave
from abc import ABC, abstractmethod
from typing import Callable

_LOGGER = logging.getLogger(__name__)

PushFn = Callable[[bytes], None]
ActiveFn = Callable[[], bool]


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


class FfmpegAudioSource(AudioSource):
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
        if (url is None) == (data is None):
            raise ValueError("Provide exactly one of url/data")
        self._bin = ffmpeg_bin
        self._url = url
        self._data = data
        self._sample_rate = sample_rate
        self._pcm_frame_bytes = (
            pcm_frame_bytes if pcm_frame_bytes is not None else sample_rate // 50 * 2
        )

    def configure(self, sample_rate: int, pcm_frame_bytes: int) -> None:
        """Update output rate to match the negotiated codec."""
        self._sample_rate = sample_rate
        self._pcm_frame_bytes = pcm_frame_bytes

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
            while is_active():
                chunk = await proc.stdout.read(self._pcm_frame_bytes)
                if not chunk:
                    break
                push(chunk)
                await asyncio.sleep(0.018)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
