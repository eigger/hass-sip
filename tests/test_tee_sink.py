"""TeeSink fan-out so Assist and recording can share RTP RX (P2-3)."""
from __future__ import annotations

import asyncio

from test_pure import audio, sip_client


class _ListSink(audio.AudioSink):
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, pcm_le: bytes) -> None:
        self.chunks.append(pcm_le)


class _BoomSink(audio.AudioSink):
    def write(self, pcm_le: bytes) -> None:
        raise RuntimeError("sink exploded")


def test_tee_sink_fans_out_to_every_listener():
    a = _ListSink()
    b = _ListSink()
    tee = audio.TeeSink(a, b)
    tee.write(b"\x01\x02")
    assert a.chunks == [b"\x01\x02"]
    assert b.chunks == [b"\x01\x02"]


def test_tee_sink_isolates_listener_errors():
    boom = _BoomSink()
    ok = _ListSink()
    tee = audio.TeeSink(boom, ok)
    tee.write(b"\x03")
    assert ok.chunks == [b"\x03"]


def test_tee_sink_add_remove_does_not_duplicate():
    a = _ListSink()
    tee = audio.TeeSink()
    tee.add(a)
    tee.add(a)
    tee.write(b"x")
    assert a.chunks == [b"x"]
    tee.remove(a)
    tee.write(b"y")
    assert a.chunks == [b"x"]
    tee.remove(a)  # missing is a no-op


def test_start_recording_during_assist_keeps_both_sinks():
    """Acceptance: Assist stays subscribed when a recorder is added."""
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        assist = _ListSink()
        recorder = _ListSink()
        client.add_sink(assist)
        client._on_rx_audio(b"pre")
        client.add_sink(recorder)
        client._on_rx_audio(b"both")
        client.remove_sink(assist)
        client._on_rx_audio(b"post")
        return assist.chunks, recorder.chunks

    assist_chunks, recorder_chunks = asyncio.run(run())
    assert assist_chunks == [b"pre", b"both"]
    assert recorder_chunks == [b"both", b"post"]


def test_stop_recording_does_not_detach_assist():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        assist = _ListSink()
        recorder = _ListSink()
        client.add_sink(assist)
        client.add_sink(recorder)
        client.remove_sink(recorder)
        client._on_rx_audio(b"keep")
        return assist.chunks, recorder.chunks

    assist_chunks, recorder_chunks = asyncio.run(run())
    assert assist_chunks == [b"keep"]
    assert recorder_chunks == []


def test_clear_sinks_drops_every_listener():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        a = _ListSink()
        b = _ListSink()
        client.add_sink(a)
        client.add_sink(b)
        client.clear_sinks()
        client._on_rx_audio(b"gone")
        return a.chunks, b.chunks

    a_chunks, b_chunks = asyncio.run(run())
    assert a_chunks == []
    assert b_chunks == []
