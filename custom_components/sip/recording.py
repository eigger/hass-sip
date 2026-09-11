"""Call recording path policy and recorder-slot helpers.

Kept free of Home Assistant imports so the SIP core tests can load it.
"""
from __future__ import annotations

import os
from typing import Any

# Home Assistant OS mounts that are not always in allowlist_external_dirs.
HAOS_RECORDING_ROOTS = ("/media", "/share")


def resolve_recording_path(raw: str, config_dir: str) -> str:
    """Return an absolute recording path; relative names go under ``config_dir``."""
    path = os.path.expanduser(str(raw).strip())
    if not path:
        raise ValueError("recording path is empty")
    if not os.path.isabs(path):
        path = os.path.join(config_dir, path)
    return os.path.realpath(path)


def is_allowed_recording_path(path: str, roots: list[str]) -> bool:
    """True if ``path`` resolves inside one of ``roots``."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    for root in roots:
        try:
            root_real = os.path.realpath(root)
        except OSError:
            continue
        try:
            if os.path.commonpath([root_real, real]) == root_real:
                return True
        except ValueError:
            continue
    return False


def recording_allow_roots(
    config_dir: str, extra: list[str] | None = None
) -> list[str]:
    """Config dir, caller extras, and HA OS ``/media`` / ``/share`` when present."""
    roots = [config_dir]
    if extra:
        roots.extend(extra)
    for well_known in HAOS_RECORDING_ROOTS:
        if os.path.isdir(well_known):
            roots.append(well_known)
    return roots


def close_recorder_slot(data: dict[str, Any]) -> Any:
    """Pop and close ``data['recorder']``. Returns the sink, or None."""
    recorder = data.pop("recorder", None)
    if recorder is not None:
        recorder.close()
    return recorder
