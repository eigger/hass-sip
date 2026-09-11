"""Call recording close, non-blocking WAV I/O, and path policy (P2-2)."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import wave
from unittest.mock import MagicMock, patch

from test_pure import _load_component_module, audio

recording = _load_component_module("recording")


def test_resolve_recording_path_joins_relative_to_config_dir():
    with tempfile.TemporaryDirectory() as config_dir:
        resolved = recording.resolve_recording_path("www/rec.wav", config_dir)
        assert resolved == os.path.realpath(os.path.join(config_dir, "www/rec.wav"))


def test_resolve_recording_path_keeps_absolute():
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "out.wav")
        assert recording.resolve_recording_path(raw, "/unused") == os.path.realpath(raw)


def test_resolve_recording_path_rejects_empty():
    try:
        recording.resolve_recording_path("  ", "/config")
    except ValueError:
        return
    raise AssertionError("empty path must raise")


def test_recording_path_rejects_escape_from_config_dir():
    with tempfile.TemporaryDirectory() as config_dir:
        escaped = recording.resolve_recording_path("../secret.wav", config_dir)
        assert recording.is_allowed_recording_path(escaped, [config_dir]) is False
        inside = recording.resolve_recording_path("ok.wav", config_dir)
        assert recording.is_allowed_recording_path(inside, [config_dir]) is True


def test_close_recorder_slot_closes_and_clears():
    sink = MagicMock()
    data = {"recorder": sink, "other": 1}
    assert recording.close_recorder_slot(data) is sink
    sink.close.assert_called_once()
    assert "recorder" not in data
    assert recording.close_recorder_slot(data) is None


def test_wav_recorder_write_does_not_block_on_open():
    """wave.open lives on the worker thread; write() must not wait for it."""
    if audio is None:
        return
    opened = threading.Event()
    proceed = threading.Event()
    real_open = wave.open

    def slow_open(*args, **kwargs):
        opened.set()
        proceed.wait(timeout=2)
        return real_open(*args, **kwargs)

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.wav")
            with patch.object(wave, "open", side_effect=slow_open):
                t0 = time.monotonic()
                sink = audio.WavRecorderSink(path)
                assert time.monotonic() - t0 < 0.2
                assert opened.wait(timeout=1)
                t1 = time.monotonic()
                sink.write(b"\x00" * 320)
                assert time.monotonic() - t1 < 0.05
                proceed.set()
                sink.close()
                await sink.wait_closed()

    asyncio.run(run())


def test_wav_recorder_close_finalizes_playable_wav():
    if audio is None:
        return

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.wav")
            sink = audio.WavRecorderSink(path, sample_rate=8000)
            pcm = b"\x00\x01" * 160
            sink.write(pcm)
            sink.close()
            await sink.wait_closed()
            with wave.open(path, "rb") as wav:
                assert wav.getnchannels() == 1
                assert wav.getsampwidth() == 2
                assert wav.getframerate() == 8000
                assert wav.readframes(wav.getnframes()) == pcm

    asyncio.run(run())


def test_wav_recorder_consecutive_calls_do_not_append():
    """Hangup must close the WAV so the next call cannot extend the same file."""
    if audio is None:
        return

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path_a = os.path.join(tmp, "a.wav")
            path_b = os.path.join(tmp, "b.wav")
            same = os.path.join(tmp, "same.wav")
            first = b"\x11" * 320
            second = b"\x22" * 640

            sink = audio.WavRecorderSink(path_a, sample_rate=8000)
            sink.write(first)
            sink.close()
            await sink.wait_closed()

            sink = audio.WavRecorderSink(path_b, sample_rate=8000)
            sink.write(second)
            sink.close()
            await sink.wait_closed()

            with wave.open(path_a, "rb") as wav:
                assert wav.readframes(wav.getnframes()) == first
            with wave.open(path_b, "rb") as wav:
                assert wav.readframes(wav.getnframes()) == second

            sink = audio.WavRecorderSink(same, sample_rate=8000)
            sink.write(first)
            sink.close()
            await sink.wait_closed()
            sink = audio.WavRecorderSink(same, sample_rate=8000)
            sink.write(second)
            sink.close()
            await sink.wait_closed()
            with wave.open(same, "rb") as wav:
                data = wav.readframes(wav.getnframes())
            assert data == second
            assert len(data) != len(first) + len(second)

    asyncio.run(run())


def test_wav_recorder_creates_parent_directory():
    if audio is None:
        return

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "rec.wav")
            sink = audio.WavRecorderSink(path, sample_rate=8000)
            sink.write(b"\x00" * 320)
            sink.close()
            await sink.wait_closed()
            assert os.path.isfile(path)

    asyncio.run(run())
