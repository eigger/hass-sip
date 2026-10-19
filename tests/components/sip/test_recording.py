"""Call recording close, non-blocking WAV I/O, and path policy (P2-2)."""

from __future__ import annotations

import asyncio
import os
import socket
import struct
import tempfile
import threading
import time
import wave
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import async_capture_events

from homeassistant.core import HomeAssistant

from custom_components.sip.const import (
    EVENT_SIP_RECORDING_STARTED,
    EVENT_SIP_RECORDING_STOPPED,
)
from custom_components.sip.sip_client.sip_client import SipState

from .common import _load_component_module, audio, phone_line, sip_device, wait_for

recording = _load_component_module("recording")


def test_resolve_recording_path_joins_relative_to_base_dir():
    with tempfile.TemporaryDirectory() as media_dir:
        resolved = recording.resolve_recording_path("sip/rec.wav", media_dir)
        assert resolved == os.path.realpath(os.path.join(media_dir, "sip/rec.wav"))


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


# ------------------------------------------------ sip.start/stop_recording flow
def _pcmu_packet(seq: int, ssrc: int = 0x5150) -> bytes:
    # 20 ms of PCMU silence (0xFF) with a real RTP header.
    return bytes([0x80, 0]) + struct.pack("!HII", seq, seq * 160, ssrc) + b"\xff" * 160


async def test_start_stop_recording_records_call_audio(
    hass: HomeAssistant, init_integration, tmp_path
) -> None:
    """Record a real call with the mock PBX into a WAV through HA services."""
    entry = init_integration
    client = entry.runtime_data["client"]
    target = phone_line(hass, entry)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    wav_path = tmp_path / "calls" / "call_{{ username }}.wav"
    started = async_capture_events(hass, EVENT_SIP_RECORDING_STARTED)
    stopped = async_capture_events(hass, EVENT_SIP_RECORDING_STOPPED)

    await hass.services.async_call(
        "sip", "dial", {"number": "200", **target}, blocking=True
    )
    await wait_for(lambda: client.state == SipState.IN_CALL)
    await hass.services.async_call(
        "sip",
        "start_recording",
        {"recording_file": str(wav_path), **target},
        blocking=True,
    )
    expected = tmp_path / "calls" / "call_1001.wav"
    assert [e.data["recording_file"] for e in started] == [str(expected)]
    assert started[0].data["device_id"] == sip_device(hass, entry).id
    assert entry.runtime_data["recorder"] is not None

    # The far end talks: RTP from the PBX side reaches the client's RTP port
    # and is written to the recording by the real RTP stack.
    # Media starts in a task after IN_CALL, so wait for the RTP socket and
    # keep talking until the client has decoded ten 20 ms frames.
    await wait_for(lambda: client.rtp.running)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as far_end:
        far_end.bind(("127.0.0.1", 0))
        port = entry.data["local_rtp_port"]
        seq = 0

        def talk() -> bool:
            nonlocal seq
            seq += 1
            far_end.sendto(_pcmu_packet(seq), ("127.0.0.1", port))
            return client.rtp.bytes_received >= 10 * 320

        await wait_for(talk)

    await hass.services.async_call("sip", "stop_recording", target, blocking=True)
    assert entry.runtime_data.get("recorder") is None
    assert len(stopped) == 1
    assert stopped[0].data["sip_account"] == "1001"

    with wave.open(str(expected), "rb") as wav:
        assert wav.getframerate() == 8000
        assert wav.getsampwidth() == 2
        assert wav.getnframes() >= 10 * 160

    await hass.services.async_call("sip", "hangup", target, blocking=True)
    await wait_for(lambda: client.state == SipState.REGISTERED)
    # Stopping explicitly does not fire a second "stopped" on hang-up.
    await hass.async_block_till_done()
    assert len(stopped) == 1


# ------------------------------------------------------------ path policy
def test_allow_roots_never_include_config_dir():
    with tempfile.TemporaryDirectory() as config_dir:
        media = os.path.join(config_dir, "media")
        roots = recording.recording_allow_roots([media])
        assert config_dir not in roots
        assert media in roots
        for name in ("configuration.yaml", "secrets.yaml", ".storage/auth"):
            assert not recording.is_allowed_recording_path(
                os.path.join(config_dir, name), roots
            )
        assert recording.is_allowed_recording_path(
            os.path.join(media, "sip", "rec.wav"), roots
        )
        assert recording.recording_allow_roots() == [
            root for root in recording.HAOS_RECORDING_ROOTS if os.path.isdir(root)
        ]


def test_default_recording_dir_prefers_local_media_dir():
    assert (
        recording.default_recording_dir(
            {"other": "/srv/other", "local": "/config/media"}, "/config"
        )
        == "/config/media"
    )
    assert recording.default_recording_dir({"x": "/srv/x"}, "/config") == "/srv/x"
    assert recording.default_recording_dir({}, "/config") == "/config"


def test_recording_target_requires_wav_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        assert recording.recording_target_error(os.path.join(tmp, "a.wav")) is None
        assert recording.recording_target_error(os.path.join(tmp, "A.WAV")) is None
        for name in (
            "configuration.yaml",
            "home-assistant_v2.db",
            "rec",
            "rec.wav.txt",
        ):
            assert recording.recording_target_error(os.path.join(tmp, name))


def test_recording_target_refuses_to_overwrite_non_wav_file():
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "notes.wav")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("homeassistant:\n  name: Home\n")
        empty = os.path.join(tmp, "empty.wav")
        open(empty, "wb").close()
        assert recording.recording_target_error(fake)
        assert recording.recording_target_error(empty)
        with open(fake, encoding="utf-8") as fh:
            assert fh.read().startswith("homeassistant:")

        folder = os.path.join(tmp, "dir.wav")
        os.mkdir(folder)
        assert recording.recording_target_error(folder)


def test_recording_target_allows_replacing_previous_recording():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "last.wav")
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00" * 320)
        assert recording.is_wav_file(path)
        assert recording.recording_target_error(path) is None
