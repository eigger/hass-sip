"""Config flow for SIP Client integration."""
from __future__ import annotations

from typing import Any
from collections.abc import Mapping
import asyncio
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    CONF_SERVER,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_DOMAIN,
    CONF_CALLER_ID,
    CONF_REGISTER_EXPIRATION,
    CONF_LOCAL_RTP_PORT,
    CONF_OUTBOUND_PROXY,
    CONF_AUTH_USERNAME,
    DEFAULT_PORT,
    DEFAULT_REGISTER_EXPIRATION,
    DEFAULT_LOCAL_RTP_PORT,
    CONF_MEDIA_TIMEOUT,
    CONF_MAX_CALL_DURATION,
    DEFAULT_MEDIA_TIMEOUT,
    DEFAULT_MAX_CALL_DURATION,
)

def _password_selector() -> TextSelector:
    """Return a password text selector so the field is never shown in the clear."""
    return TextSelector(
        TextSelectorConfig(
            type=TextSelectorType.PASSWORD,
            autocomplete="current-password",
        )
    )


def _build_schema(
    rtp_port_default: int,
    defaults: dict[str, Any] | None = None,
    *,
    omit_password_default: bool = False,
) -> vol.Schema:
    """Build the account form schema.

    When `defaults` is given (editing an existing entry), every field is
    pre-filled with its current value instead of the fresh-entry defaults.
    The password is a masked selector. Reconfigure always passes
    ``omit_password_default=True`` so a failed retry can still submit with a
    blank password and keep the stored secret.
    """

    def _default(key: str, fallback: Any = vol.UNDEFINED) -> Any:
        return defaults.get(key, fallback) if defaults is not None else fallback

    if omit_password_default:
        password_key: Any = vol.Optional(CONF_PASSWORD)
    elif defaults is not None:
        password_key = vol.Required(CONF_PASSWORD, default=_default(CONF_PASSWORD))
    else:
        password_key = vol.Required(CONF_PASSWORD)

    return vol.Schema(
        {
            vol.Required(CONF_SERVER, default=_default(CONF_SERVER)): cv.string,
            vol.Optional(
                CONF_PORT, default=_default(CONF_PORT, DEFAULT_PORT)
            ): cv.port,
            vol.Required(CONF_USERNAME, default=_default(CONF_USERNAME)): cv.string,
            password_key: _password_selector(),
            vol.Optional(
                CONF_AUTH_USERNAME, default=_default(CONF_AUTH_USERNAME)
            ): cv.string,
            vol.Optional(CONF_DOMAIN, default=_default(CONF_DOMAIN)): cv.string,
            vol.Optional(
                CONF_CALLER_ID, default=_default(CONF_CALLER_ID)
            ): cv.string,
            vol.Optional(
                CONF_OUTBOUND_PROXY, default=_default(CONF_OUTBOUND_PROXY)
            ): cv.string,
            vol.Optional(
                CONF_REGISTER_EXPIRATION,
                default=_default(
                    CONF_REGISTER_EXPIRATION, DEFAULT_REGISTER_EXPIRATION
                ),
            ): cv.positive_int,
            vol.Optional(
                CONF_LOCAL_RTP_PORT,
                default=_default(CONF_LOCAL_RTP_PORT, rtp_port_default),
            ): cv.port,
            vol.Optional(
                CONF_MEDIA_TIMEOUT,
                default=_default(CONF_MEDIA_TIMEOUT, DEFAULT_MEDIA_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional(
                CONF_MAX_CALL_DURATION,
                default=_default(CONF_MAX_CALL_DURATION, DEFAULT_MAX_CALL_DURATION),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }
    )


def _suggested_rtp_port(hass: HomeAssistant) -> int:
    """Suggest a distinct RTP port so multiple accounts don't clash on bind."""
    used = {
        entry.data.get(CONF_LOCAL_RTP_PORT, DEFAULT_LOCAL_RTP_PORT)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    port = DEFAULT_LOCAL_RTP_PORT
    while port in used:
        port += 2  # RTP/RTCP pair convention
    return port


def merge_reconfigure_data(
    entry_data: Mapping[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Apply form fields onto existing entry data.

    Reconfigure must not drop keys the form does not collect (``assist_user``).
    An omitted or blank password keeps the stored secret.
    """
    merged = {**entry_data, **user_input}
    if not str(user_input.get(CONF_PASSWORD) or "").strip():
        if CONF_PASSWORD in entry_data:
            merged[CONF_PASSWORD] = entry_data[CONF_PASSWORD]
        else:
            merged.pop(CONF_PASSWORD, None)
    return merged


async def async_validate_sip_registration(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[bool, str]:
    """Test actual SIP registration with the server."""
    from .sip_client.sip_client import SipCallbacks, SipClient, SipConfig

    sip_config = SipConfig(
        server=user_input[CONF_SERVER],
        port=user_input.get(CONF_PORT, 5060),
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
        auth_username=user_input.get(CONF_AUTH_USERNAME, ""),
        domain=user_input.get(CONF_DOMAIN, ""),
        caller_id=user_input.get(CONF_CALLER_ID, ""),
        outbound_proxy=user_input.get(CONF_OUTBOUND_PROXY, ""),
        # Use the configured expiration — a hardcoded short value (e.g. 10s)
        # triggers SIP 423 Interval Too Brief on registrars with Min-Expires.
        register_expiration=user_input.get(
            CONF_REGISTER_EXPIRATION, DEFAULT_REGISTER_EXPIRATION
        ),
        local_rtp_port=user_input.get(CONF_LOCAL_RTP_PORT, 7078),
    )

    event = asyncio.Event()
    reg_success = False
    error_msg = ""

    def on_registered() -> None:
        nonlocal reg_success
        reg_success = True
        event.set()

    def on_register_failed(reason: str) -> None:
        nonlocal error_msg
        error_msg = reason
        event.set()

    callbacks = SipCallbacks(
        on_registered=on_registered,
        on_register_failed=on_register_failed,
    )

    client = SipClient(sip_config, callbacks)
    await client.start()

    try:
        # Wait up to 5 seconds for registration success/failure
        await asyncio.wait_for(event.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        error_msg = "timeout"
    finally:
        await client.stop()

    return reg_success, error_msg


class SipConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SIP Client."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Prevent configuring the same username/server combination
            unique_id = f"{user_input[CONF_USERNAME]}@{user_input[CONF_SERVER]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            # Validate connection and authentication via actual REGISTER sequence
            success, error_msg = await async_validate_sip_registration(
                self.hass, user_input
            )
            if not success:
                # Map specific SIP response/failure reasons to HA errors
                if any(x in error_msg for x in ("401", "403", "407")):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"SIP: {user_input[CONF_USERNAME]}",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(_suggested_rtp_port(self.hass)),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle editing an already configured SIP account."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            # Changing the account identity here would silently repurpose
            # this entry for a different SIP account; block that instead.
            await self.async_set_unique_id(
                f"{user_input[CONF_USERNAME]}@{user_input[CONF_SERVER]}"
            )
            self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")

            candidate = merge_reconfigure_data(reconfigure_entry.data, user_input)
            success, error_msg = await async_validate_sip_registration(
                self.hass, candidate
            )
            if not success:
                if any(x in error_msg for x in ("401", "403", "407")):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data=candidate
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_schema(
                reconfigure_entry.data.get(
                    CONF_LOCAL_RTP_PORT, DEFAULT_LOCAL_RTP_PORT
                ),
                defaults=user_input or reconfigure_entry.data,
                omit_password_default=True,
            ),
            errors=errors,
        )
