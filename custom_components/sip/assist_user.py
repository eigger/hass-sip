"""Home Assistant user that owns Assist intent execution for a SIP account."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ASSIST_USER, CONF_USERNAME

_USERS_GROUP = "system-users"


async def ensure_assist_user(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return a user id for Assist contexts, creating a system user if needed.

    Follows core ``voip``: a Users-group system user so intent service calls
    have a logbook subject and are not executed as an empty Context.
    """
    try:
        from homeassistant.auth.const import GROUP_ID_USER
    except ImportError:
        GROUP_ID_USER = _USERS_GROUP

    existing_id = entry.data.get(CONF_ASSIST_USER)
    if existing_id:
        user = await hass.auth.async_get_user(existing_id)
        if user is not None:
            return existing_id

    username = entry.data.get(CONF_USERNAME) or entry.entry_id
    created = await hass.auth.async_create_system_user(
        f"SIP Assist ({username})",
        group_ids=[GROUP_ID_USER],
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_ASSIST_USER: created.id}
    )
    return created.id


async def remove_assist_user(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the system user created for this entry, if it still exists."""
    user_id = entry.data.get(CONF_ASSIST_USER)
    if not user_id:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is not None:
        await hass.auth.async_remove_user(user)
