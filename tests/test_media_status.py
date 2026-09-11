"""P1-3 media status view: one-way vs bidirectional from entity fields."""
from __future__ import annotations

import asyncio

from test_pure import _load_component_module, sip_client

media_status = _load_component_module("media_status")


class _FakeClient:
    def __init__(self, snap: dict) -> None:
        self._snap = snap

    def diagnostics_snapshot(self) -> dict:
        return self._snap


def test_audio_confirmed_requires_both_directions_above_threshold():
    thresh = media_status.AUDIO_CONFIRM_BYTES
    assert media_status.audio_confirmed(thresh, thresh) is True
    assert media_status.audio_confirmed(thresh - 1, thresh) is False
    assert media_status.audio_confirmed(thresh, thresh - 1) is False
    assert media_status.audio_confirmed(0, 32000) is False
    assert media_status.audio_confirmed(32000, 0) is False


def test_call_duration_live_then_history():
    now = 1_000.0
    live = media_status.call_duration_seconds(
        {"call_connect_time": now - 12.4}, now
    )
    assert live == 12
    ended = media_status.call_duration_seconds(
        {"call_history": [{"duration": 41}]}, now
    )
    assert ended == 41
    assert media_status.call_duration_seconds({}, now) is None


def test_media_view_one_way_is_not_confirmed():
    client = _FakeClient(
        {
            "codec": {"negotiated": "PCMU", "payload_type": 0, "sample_rate": 8000},
            "rtp": {
                "bytes_received": 0,
                "bytes_sent": 32000,
                "audio_path": "no_rx",
            },
            "call": {"last_end_reason": "media_timeout", "in_call": False},
        }
    )
    view = media_status.media_view(client, {}, 0.0)
    assert view["audio_path"] == "no_rx"
    assert view["audio_confirmed"] is False
    assert view["bytes_sent"] == 32000
    assert view["bytes_received"] == 0
    assert view["last_end_reason"] == "media_timeout"
    assert view["codec"] == "PCMU"


def test_media_view_bidirectional_is_confirmed():
    thresh = media_status.AUDIO_CONFIRM_BYTES
    client = _FakeClient(
        {
            "codec": {"negotiated": "G722", "payload_type": 9, "sample_rate": 16000},
            "rtp": {
                "bytes_received": thresh,
                "bytes_sent": thresh,
                "audio_path": "bidirectional",
            },
            "call": {"last_end_reason": "remote_bye", "in_call": False},
        }
    )
    view = media_status.media_view(
        client, {"call_history": [{"duration": 8}]}, 0.0
    )
    assert view["audio_path"] == "bidirectional"
    assert view["audio_confirmed"] is True
    assert view["call_duration"] == 8
    assert view["codec"] == "G722"


def test_media_view_from_real_sip_client_one_way():
    if sip_client is None:
        return

    async def run():
        client = sip_client.SipClient(sip_client.SipConfig(server="pbx.example"))
        client.last_call_bytes_tx = 32000
        client.last_call_bytes_rx = 0
        client.last_call_reason = "media_timeout"
        return media_status.media_view(client, {}, 0.0)

    view = asyncio.run(run())
    assert view["audio_path"] == "no_rx"
    assert view["audio_confirmed"] is False
    assert view["last_end_reason"] == "media_timeout"
