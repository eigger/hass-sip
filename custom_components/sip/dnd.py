"""DND restore mapping. Kept free of Home Assistant imports."""
from __future__ import annotations


def restored_dnd_enabled(state: str | None) -> bool | None:
    """Map last HA switch state to DND. None means leave the client unchanged."""
    if state in (None, "unknown", "unavailable"):
        return None
    return state == "on"


def apply_restored_dnd(client, state: str | None) -> None:
    """Copy a restored switch state onto ``client.dnd`` when it is definitive."""
    enabled = restored_dnd_enabled(state)
    if enabled is not None:
        client.dnd = enabled
