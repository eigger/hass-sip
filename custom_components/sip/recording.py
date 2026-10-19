"""Call recording path policy and recorder-slot helpers.

Kept free of Home Assistant imports so the SIP core tests can load it.
"""

from __future__ import annotations

import os
from typing import Any

# Home Assistant OS mounts that are not always in allowlist_external_dirs.
HAOS_RECORDING_ROOTS = ("/media", "/share")

RECORDING_SUFFIX = ".wav"


def resolve_recording_path(raw: str, base_dir: str) -> str:
    """Return an absolute recording path; relative names go under ``base_dir``."""
    path = os.path.expanduser(str(raw).strip())
    if not path:
        raise ValueError("recording path is empty")
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    return os.path.realpath(path)


def default_recording_dir(media_dirs: dict[str, str], fallback: str) -> str:
    """Directory relative recording paths resolve against.

    The ``local`` media dir (``/media`` on HA OS, ``<config>/media`` otherwise)
    so recordings show up in the media browser; ``fallback`` only when no media
    dir is configured at all.
    """
    if media_dirs.get("local"):
        return media_dirs["local"]
    for path in media_dirs.values():
        if path:
            return path
    return fallback


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


def recording_allow_roots(extra: list[str] | None = None) -> list[str]:
    """Caller extras plus HA OS ``/media`` / ``/share`` when present.

    The config directory is deliberately not a root: it holds
    ``configuration.yaml``, ``secrets.yaml``, ``.storage`` and the recorder
    database, and opening a recording truncates the target immediately.
    """
    roots = list(extra or [])
    for well_known in HAOS_RECORDING_ROOTS:
        if os.path.isdir(well_known):
            roots.append(well_known)
    return roots


def is_wav_file(path: str) -> bool:
    """True if ``path`` starts with a RIFF/WAVE header."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
    except OSError:
        return False
    return len(header) == 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"


def recording_target_error(path: str) -> str | None:
    """Why ``path`` must not be opened for recording, or None if it is safe.

    Requires a ``.wav`` suffix and never replaces an existing file unless it
    is already a WAV (a previous recording). Does blocking I/O.
    """
    if not path.lower().endswith(RECORDING_SUFFIX):
        return f"recording file must end in {RECORDING_SUFFIX}"
    if os.path.lexists(path):
        if not os.path.isfile(path):
            return "recording path exists and is not a regular file"
        if not is_wav_file(path):
            return "refusing to overwrite an existing file that is not a WAV"
    return None


def close_recorder_slot(data: dict[str, Any]) -> Any:
    """Pop and close ``data['recorder']``. Returns the sink, or None."""
    recorder = data.pop("recorder", None)
    if recorder is not None:
        recorder.close()
    return recorder
