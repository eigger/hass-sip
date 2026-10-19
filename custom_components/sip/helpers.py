"""Helper utilities for the SIP Client integration."""

from __future__ import annotations

import os

from homeassistant.components import media_source
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def get_ffmpeg_bin(hass: HomeAssistant) -> str:
    """Return the ffmpeg binary path configured by the HA ffmpeg integration.

    ``ffmpeg`` is a hard dependency of this integration (see manifest), so the
    manager is always available here.
    """
    return get_ffmpeg_manager(hass).binary


def build_device_info(entry: ConfigEntry, config) -> DeviceInfo:
    """Common device info for all entities of one SIP account.

    Surfaces the extension number and the SIP server so the device card is
    self-explanatory.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"SIP Extension {config.username}",
        manufacturer="Home Assistant",
        model=f"SIP Extension ({config.username} @ {config.server})",
        serial_number=config.username,
        configuration_url=f"http://{config.server}",
    )


# ffmpeg runs on the HA host. Only hand it network URLs, never a local path
# or one of its other protocols (file:, concat:, pipe:, ...).
_PLAYBACK_URL_SCHEMES = ("http", "https")


class MediaSourceResolveError(ValueError):
    """The media-source ID could not be resolved."""


async def async_resolve_playback_url(
    hass: HomeAssistant, media_id: str, *, target_media_player: str | None = None
) -> str:
    """Turn a media-source ID or URL into a URL ffmpeg may open.

    Same steps as the media_player entity's ``play_media``: resolve a
    media-source ID, then ``async_process_play_media_url`` makes HA paths
    absolute and signs protected ones. Anything that is still not an
    http(s) URL afterwards (a filesystem path, ``file:`` ...) is refused so a
    caller cannot make ffmpeg read arbitrary files on the host.

    Every place that hands caller-supplied media to ffmpeg (IVR
    ``audio_file``, Assist tone media, ``media_player.play_media``) goes
    through here.

    Raises ``ValueError`` when the media cannot be used
    (``MediaSourceResolveError`` when a media-source ID does not resolve).
    """
    url = str(media_id or "").strip()
    if not url:
        raise ValueError("no media given")
    if media_source.is_media_source_id(url):
        try:
            media_item = await media_source.async_resolve_media(
                hass, url, target_media_player
            )
        except Exception as err:  # noqa: BLE001
            raise MediaSourceResolveError(
                f"failed to resolve media source '{url}': {err}"
            ) from err
        url = media_item.url
    try:
        url = async_process_play_media_url(hass, url)
    except Exception as err:  # noqa: BLE001
        raise ValueError(f"failed to prepare media URL: {err}") from err
    scheme = url.partition(":")[0].lower() if ":" in url else ""
    if scheme not in _PLAYBACK_URL_SCHEMES:
        raise ValueError(
            "only media-source IDs and http(s) URLs can be played, "
            f"not '{url.partition('?')[0]}'"
        )
    return url


def media_dir_source_hint(media_dirs: dict[str, str], path: str) -> str | None:
    """Media-source ID for a host path inside a ``media_dirs`` entry, else None.

    Purely lexical (no filesystem access): only used to suggest the right ID
    in a log message when someone passes ``/media/foo.mp3`` style paths.
    """
    if not path.startswith("/"):
        return None
    target = os.path.normpath(path)
    for source_dir_id, root in media_dirs.items():
        if not root:
            continue
        root = os.path.normpath(root)
        try:
            if os.path.commonpath([root, target]) != root:
                continue
        except ValueError:
            continue
        rel = os.path.relpath(target, root).replace(os.sep, "/")
        if rel == ".":
            rel = ""
        return f"media-source://media_source/{source_dir_id}/{rel}"
    return None
