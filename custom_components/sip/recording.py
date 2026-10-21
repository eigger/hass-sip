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

    The ``local`` media dir (HA's default is ``/media`` when running in a
    container, i.e. HA OS, Supervised and the Docker image, and
    ``<config>/media`` for a Core/venv install)
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
    database, and opening a recording truncates the target immediately. Even
    when an allowlisted dir covers it, :func:`is_protected_config_path` keeps
    recordings out of it.
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
    is already a WAV (a previous recording) or empty: the recorder creates its
    file before the first frame arrives, so a recording that never got audio
    (or crashed before the header was written) leaves a 0-byte file that a
    new ``start_recording`` must be able to reuse. Does blocking I/O.
    """
    if not path.lower().endswith(RECORDING_SUFFIX):
        return f"recording file must end in {RECORDING_SUFFIX}"
    if os.path.lexists(path):
        if not os.path.isfile(path):
            return "recording path exists and is not a regular file"
        try:
            empty = os.path.getsize(path) == 0
        except OSError:
            empty = False
        if not empty and not is_wav_file(path):
            return "refusing to overwrite an existing file that is not a WAV"
    return None


def is_protected_config_path(path: str, config_dir: str, media_dirs: list[str]) -> bool:
    """True if ``path`` is inside the HA config dir but not inside a media dir.

    The config dir holds ``configuration.yaml``, ``secrets.yaml``,
    ``.storage``, the recorder database and the unauthenticated ``www``
    folder. It can still end up in ``allowlist_external_dirs`` (``<config>/www``
    is there by default, some installs allowlist the whole config dir), so
    recordings are kept out of it explicitly; only a media dir located under
    the config dir (``<config>/media``) is allowed. A media dir that *is* the
    config dir does not count. Paths are resolved with ``realpath``.
    """
    try:
        real = os.path.realpath(path)
        config_real = os.path.realpath(config_dir)
    except OSError:
        return True
    if not is_allowed_recording_path(real, [config_real]):
        return False
    for media_dir in media_dirs:
        if not media_dir:
            continue
        try:
            media_real = os.path.realpath(media_dir)
        except OSError:
            continue
        if media_real == config_real:
            continue
        if is_allowed_recording_path(real, [media_real]):
            return False
    return True


class RecordingPathError(ValueError):
    """A recording target was rejected; ``str(err)`` is the log message."""


def validate_recording_target(
    raw: str,
    base_dir: str,
    config_dir: str,
    extra_roots: list[str],
    media_dirs: list[str],
) -> str:
    """Resolve and vet a recording path; return the absolute path to record to.

    Raises :class:`RecordingPathError` when the target must not be used. Does
    blocking I/O (``realpath``, ``isdir``, ``stat``, ``open``): run it in the
    executor, never on the event loop.
    """
    try:
        path = resolve_recording_path(raw, base_dir)
    except ValueError as err:
        raise RecordingPathError("Recording path is empty") from err
    if not is_allowed_recording_path(path, recording_allow_roots(extra_roots)):
        raise RecordingPathError(
            f"Recording path {path} is outside allowed directories "
            "(allowlist_external_dirs, media_dirs, /media, /share)"
        )
    if is_protected_config_path(path, config_dir, media_dirs):
        raise RecordingPathError(
            f"Recording path {path} is inside the configuration directory; "
            "use a media directory instead"
        )
    # The recorder truncates its target as soon as it starts, call or no call,
    # so refuse anything that is not (already) a WAV file.
    if reason := recording_target_error(path):
        raise RecordingPathError(f"Recording path {path} rejected: {reason}")
    return path


def close_recorder_slot(data: dict[str, Any]) -> Any:
    """Pop and close ``data['recorder']``. Returns the sink, or None."""
    recorder = data.pop("recorder", None)
    if recorder is not None:
        recorder.close()
    return recorder
