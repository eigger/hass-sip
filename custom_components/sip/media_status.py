"""Call-media view shared by sensors, binary_sensor, and media_player.

No Home Assistant imports. Tests load this module standalone.
"""
from __future__ import annotations

from typing import Any

# ~100 ms of 8 kHz s16le (or ~50 ms of 16 kHz G.722). Filters a stray packet.
AUDIO_CONFIRM_BYTES = 1600

AUDIO_PATH_OPTIONS = ("none", "no_rx", "no_tx", "bidirectional")
CODEC_OPTIONS = ("G722", "PCMU", "PCMA")


def audio_confirmed(rx: int, tx: int, threshold: int = AUDIO_CONFIRM_BYTES) -> bool:
    """True when both directions carried more than a blip of PCM."""
    return rx >= threshold and tx >= threshold


def call_duration_seconds(runtime: dict[str, Any], now: float) -> int | None:
    """Live duration while connected, else the last history entry."""
    connect = runtime.get("call_connect_time")
    if connect:
        return max(0, int(now - connect))
    history = runtime.get("call_history") or []
    if not history:
        return None
    duration = history[0].get("duration")
    if duration is None:
        return None
    return int(duration)


def media_view(
    client: Any | None, runtime: dict[str, Any], now: float
) -> dict[str, Any]:
    """Flatten SIP diagnostics into entity-friendly fields."""
    snap = client.diagnostics_snapshot() if client is not None else {}
    rtp = snap.get("rtp") or {}
    codec = snap.get("codec") or {}
    call = snap.get("call") or {}
    rx = int(rtp.get("bytes_received") or 0)
    tx = int(rtp.get("bytes_sent") or 0)
    path = rtp.get("audio_path") or "none"
    return {
        "codec": codec.get("negotiated"),
        "payload_type": codec.get("payload_type"),
        "sample_rate": codec.get("sample_rate"),
        "telephone_event_pt": codec.get("telephone_event_pt"),
        "bytes_received": rx,
        "bytes_sent": tx,
        "audio_path": path,
        "audio_confirmed": audio_confirmed(rx, tx),
        "last_end_reason": call.get("last_end_reason"),
        "call_duration": call_duration_seconds(runtime, now),
        "in_call": bool(call.get("in_call")),
    }
