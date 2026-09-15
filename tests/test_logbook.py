"""Logbook descriptions for SIP bus events."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

from test_pure import _load_component_module


def _load_logbook():
    logbook_stub = types.ModuleType("homeassistant.components.logbook")
    logbook_stub.LOGBOOK_ENTRY_MESSAGE = "message"
    logbook_stub.LOGBOOK_ENTRY_NAME = "name"
    saved = sys.modules.get("homeassistant.components.logbook")
    sys.modules["homeassistant.components.logbook"] = logbook_stub
    try:
        return _load_component_module("logbook")
    finally:
        if saved is None:
            sys.modules.pop("homeassistant.components.logbook", None)
        else:
            sys.modules["homeassistant.components.logbook"] = saved


def _describers():
    logbook = _load_logbook()
    registered: dict[str, object] = {}

    def register(_domain, event_type, describe):
        registered[event_type] = describe

    logbook.async_describe_events(MagicMock(), register)
    return logbook, registered


def _event(data):
    return types.SimpleNamespace(data=data)


def test_playback_done_describes_success_and_failure():
    logbook, describers = _describers()
    describe = describers[logbook.EVENT_SIP_PLAYBACK_DONE]

    ok = describe(_event({"sip_account": "100"}))
    assert ok == {"name": "SIP (100)", "message": "playback finished"}

    failed = describe(_event({"sip_account": "100", "error": "ffmpeg exited with status 1"}))
    assert failed["message"] == "playback failed: ffmpeg exited with status 1"
