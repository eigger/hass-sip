"""DND switch restores on/off across Home Assistant restarts (P2-5)."""
from __future__ import annotations

from unittest.mock import MagicMock

from test_pure import _load_component_module

dnd = _load_component_module("dnd")


def test_restored_dnd_enabled_mapping():
    assert dnd.restored_dnd_enabled("on") is True
    assert dnd.restored_dnd_enabled("off") is False
    assert dnd.restored_dnd_enabled(None) is None
    assert dnd.restored_dnd_enabled("unknown") is None
    assert dnd.restored_dnd_enabled("unavailable") is None


def test_dnd_switch_restores_on_from_last_state():
    client = MagicMock()
    client.dnd = False
    dnd.apply_restored_dnd(client, "on")
    assert client.dnd is True


def test_dnd_switch_restores_off_over_live_default():
    client = MagicMock()
    client.dnd = True
    dnd.apply_restored_dnd(client, "off")
    assert client.dnd is False


def test_dnd_switch_keeps_default_when_no_last_state():
    client = MagicMock()
    client.dnd = False
    dnd.apply_restored_dnd(client, None)
    assert client.dnd is False
    dnd.apply_restored_dnd(client, "unknown")
    assert client.dnd is False
