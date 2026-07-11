"""Config flow for SIP Client integration."""
from __future__ import annotations

from typing import Any
import asyncio
import socket
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

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
    DEFAULT_PORT,
    DEFAULT_REGISTER_EXPIRATION,
    DEFAULT_LOCAL_RTP_PORT,
)

def _build_schema(rtp_port_default: int) -> vol.Schema:
    """Build the user form schema with a per-account default RTP port."""
    return vol.Schema(
        {
            vol.Required(CONF_SERVER): cv.string,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
            vol.Required(CONF_USERNAME): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
            vol.Optional(CONF_DOMAIN): cv.string,
            vol.Optional(CONF_CALLER_ID): cv.string,
            vol.Optional(
                CONF_REGISTER_EXPIRATION, default=DEFAULT_REGISTER_EXPIRATION
            ): cv.positive_int,
            vol.Optional(CONF_LOCAL_RTP_PORT, default=rtp_port_default): cv.port,
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
        domain=user_input.get(CONF_DOMAIN, ""),
        caller_id=user_input.get(CONF_CALLER_ID, ""),
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
