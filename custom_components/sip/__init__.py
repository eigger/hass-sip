"""The SIP Client integration."""
from __future__ import annotations

import asyncio
import datetime
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.service import async_extract_config_entry_ids

from .assist import AssistBridge
from .const import (
    CONF_CALLER_ID,
    CONF_DOMAIN,
    CONF_LOCAL_RTP_PORT,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_REGISTER_EXPIRATION,
    CONF_SERVER,
    CONF_USERNAME,
    DOMAIN,
    EVENT_SIP_CALL_CONNECTED,
    EVENT_SIP_CALL_ENDED,
    EVENT_SIP_DTMF_DIGIT,
    EVENT_SIP_INCOMING_CALL,
    EVENT_SIP_PLAYBACK_DONE,
    EVENT_SIP_REGISTERED,
    EVENT_SIP_RECORDING_STARTED,
    EVENT_SIP_RECORDING_STOPPED,
    EVENT_SIP_STATE_CHANGED,
    LOGGER,
)
from .helpers import get_ffmpeg_bin
from .ivr import IvrSession
from .sip_client.audio import FfmpegAudioSource
from .sip_client.sip_client import SipCallbacks, SipClient, SipConfig, SipState

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.SWITCH,
    Platform.EVENT,
    Platform.BUTTON,
]


def load_contacts(hass: HomeAssistant) -> dict[str, Any]:
    """Load contacts from the JSON file."""
    import json
    import os

    config_dir = hass.config.path()
    contacts_file = os.path.join(config_dir, "sip_contacts.json")
    if os.path.exists(contacts_file):
        try:
            with open(contacts_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_contact_info_from_cache(contacts: dict[str, Any], number: str) -> tuple[str, bool]:
    """Look up friendly name and auto-answer settings from cached contacts."""
    info = contacts.get(str(number))
    if isinstance(info, dict):
        return info.get("name", number), info.get("auto_answer", False)
    elif isinstance(info, str):
        return info, False
    return number, False

# Service Schemas
SERVICE_DIAL_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("number"): cv.string,
        vol.Optional("menu"): cv.match_all,
        vol.Optional("ring_timeout"): cv.positive_int,
        vol.Optional("message"): cv.string,
        vol.Optional("tts_engine"): cv.string,
        vol.Optional("language"): cv.string,
        vol.Optional("tts_options"): cv.match_all,
    }
)

SERVICE_HANGUP_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("sip_code"): cv.positive_int,
    }
)

SERVICE_ANSWER_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("menu"): cv.match_all,
        vol.Optional("message"): cv.string,
        vol.Optional("tts_engine"): cv.string,
        vol.Optional("language"): cv.string,
        vol.Optional("tts_options"): cv.match_all,
    }
)

SERVICE_SEND_DTMF_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("digits"): cv.string,
    }
)

SERVICE_RECORDING_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("recording_file"): cv.string,
    }
)

SERVICE_GENERIC_SCHEMA = cv.make_entity_service_schema({})



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SIP Client from a config entry."""
    config = entry.data
    sip_config = SipConfig(
        server=config[CONF_SERVER],
        port=config.get(CONF_PORT, 5060),
        username=config[CONF_USERNAME],
        password=config[CONF_PASSWORD],
        domain=config.get(CONF_DOMAIN, ""),
        caller_id=config.get(CONF_CALLER_ID, ""),
        register_expiration=config.get(CONF_REGISTER_EXPIRATION, 300),
        local_rtp_port=config.get(CONF_LOCAL_RTP_PORT, 7078),
    )

    # Load contacts asynchronously from file to avoid blocking event loop on startup
    contacts = await hass.async_add_executor_job(load_contacts, hass)

    entry.runtime_data = {
        "client": None,
        "state": SipState.IDLE,
        "registered": False,
        "last_caller": "",
        "config": sip_config,
        "call_history": [],
        "call_start_time": None,
        "call_connect_time": None,
        "call_direction": None,
        "call_status": "missed",
        "contacts": contacts,
        "call_number": "",
    }

    # Active session state helpers
    ivr_session: IvrSession | None = None
    assist_bridge: AssistBridge | None = None

    def fire_sip_event(event_type: str, extra_data: dict[str, Any] | None = None) -> None:
        data = {
            "sip_account": sip_config.username,
            "server": sip_config.server,
        }
        if extra_data:
            data.update(extra_data)
        # event_type constants already carry the "sip_" prefix.
        hass.bus.async_fire(event_type, data)
        async_dispatcher_send(
            hass, f"{DOMAIN}_event_{entry.entry_id}", event_type, extra_data
        )

    # Callbacks implementation
    @callback
    def on_state_change(state: SipState) -> None:
        LOGGER.info("[%s] SIP Client state: %s", sip_config.username, state)
        entry.runtime_data["state"] = state
        async_dispatcher_send(hass, f"{DOMAIN}_state_update_{entry.entry_id}")
        fire_sip_event(EVENT_SIP_STATE_CHANGED, {"state": str(state)})

    @callback
    def on_registered() -> None:
        LOGGER.info("[%s] SIP Client successfully registered", sip_config.username)
        entry.runtime_data["registered"] = True
        async_dispatcher_send(hass, f"{DOMAIN}_state_update_{entry.entry_id}")
        fire_sip_event(EVENT_SIP_REGISTERED)

    @callback
    def on_incoming_call(caller: str) -> None:
        LOGGER.info("[%s] Incoming call from %s", sip_config.username, caller)
        
        # Reload contacts in background so any manual edits are picked up dynamically
        def reload_contacts_bg():
            contacts_data = load_contacts(hass)
            entry.runtime_data["contacts"] = contacts_data
        hass.async_add_executor_job(reload_contacts_bg)

        caller_name, contact_auto_answer = get_contact_info_from_cache(
            entry.runtime_data.get("contacts", {}), caller
        )
        auto_answer = contact_auto_answer or getattr(client, "auto_answer", False)
        entry.runtime_data["last_caller"] = caller_name
        entry.runtime_data["call_number"] = caller

        # Track call details
        entry.runtime_data["call_start_time"] = time.time()
        entry.runtime_data["call_connect_time"] = None
        entry.runtime_data["call_direction"] = "incoming"
        if client.dnd:
            entry.runtime_data["call_status"] = "rejected"
        elif auto_answer:
            entry.runtime_data["call_status"] = "answered"
            entry.runtime_data["call_connect_time"] = time.time()
        else:
            entry.runtime_data["call_status"] = "missed"

        async_dispatcher_send(hass, f"{DOMAIN}_state_update_{entry.entry_id}")
        fire_sip_event(EVENT_SIP_INCOMING_CALL, {"caller": caller, "caller_name": caller_name})

    @callback
    def on_call_connected() -> None:
        LOGGER.info("[%s] Call connected", sip_config.username)
        entry.runtime_data["call_connect_time"] = time.time()
        entry.runtime_data["call_status"] = "answered"
        fire_sip_event(EVENT_SIP_CALL_CONNECTED)
        nonlocal ivr_session
        if ivr_session is not None:
            # Trigger IVR menu execution
            hass.async_create_task(ivr_session.start())

    @callback
    def on_call_ended() -> None:
        LOGGER.info("[%s] Call ended", sip_config.username)

        # Save to call history log
        start_time = entry.runtime_data.get("call_start_time")
        connect_time = entry.runtime_data.get("call_connect_time")
        direction = entry.runtime_data.get("call_direction")
        status = entry.runtime_data.get("call_status", "missed")

        if start_time and direction:
            caller_num = entry.runtime_data.get("call_number", "Unknown")
            caller_name, _ = get_contact_info_from_cache(
                entry.runtime_data.get("contacts", {}), caller_num
            )

            duration = 0
            if connect_time:
                duration = int(time.time() - connect_time)
                status = "answered"

            history_entry = {
                "timestamp": datetime.datetime.fromtimestamp(start_time).isoformat(),
                "number": caller_num,
                "name": caller_name,
                "direction": direction,
                "duration": duration,
                "status": status,
            }

            history = entry.runtime_data.setdefault("call_history", [])
            history.insert(0, history_entry)
            if len(history) > 20:
                history.pop()

            # Clean up active tracking variables
            entry.runtime_data["call_start_time"] = None
            entry.runtime_data["call_connect_time"] = None
            entry.runtime_data["call_direction"] = None
            entry.runtime_data["call_status"] = "missed"
            entry.runtime_data["call_number"] = ""

        fire_sip_event(EVENT_SIP_CALL_ENDED)
        nonlocal ivr_session, assist_bridge
        if ivr_session is not None:
            ivr_session.close()
            ivr_session = None
        if assist_bridge is not None:
            assist_bridge.close()
            assist_bridge = None
        async_dispatcher_send(hass, f"{DOMAIN}_state_update_{entry.entry_id}")

    @callback
    def on_dtmf(digit: str) -> None:
        LOGGER.debug("[%s] DTMF digit received: %s", sip_config.username, digit)
        fire_sip_event(EVENT_SIP_DTMF_DIGIT, {"digit": digit})
        nonlocal ivr_session
        if ivr_session is not None:
            hass.async_create_task(ivr_session.handle_dtmf(digit))

    @callback
    def on_playback_done() -> None:
        LOGGER.debug("[%s] Audio playback done", sip_config.username)
        fire_sip_event(EVENT_SIP_PLAYBACK_DONE)
        nonlocal ivr_session
        if ivr_session is not None:
            ivr_session.on_playback_done()

    callbacks = SipCallbacks(
        on_state_change=on_state_change,
        on_registered=on_registered,
        on_incoming_call=on_incoming_call,
        on_call_connected=on_call_connected,
        on_call_ended=on_call_ended,
        on_dtmf=on_dtmf,
        on_playback_done=on_playback_done,
    )

    client = SipClient(sip_config, callbacks)
    entry.runtime_data["client"] = client
    client.auto_answer_checker = lambda num: get_contact_info_from_cache(
        entry.runtime_data.get("contacts", {}), num
    )[1]

    # Start the SIP client in a background task
    async def start_client() -> None:
        try:
            await client.start()
        except Exception:  # noqa: BLE001 - never let startup crash leave a dead task
            LOGGER.exception("SIP client startup failed; will keep retrying via timer")

    start_task = asyncio.create_task(start_client())
    entry.runtime_data["start_task"] = start_task

    # Setup platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Handle clean shutdown
    async def shutdown(event) -> None:
        await client.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    )

    # Helper function to play a message via TTS
    async def play_message_internal(
        message: str,
        language: str | None = None,
        engine: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        from homeassistant.components.tts import async_get_media_source_audio
        from homeassistant.components.tts.media_source import generate_media_source_id

        try:
            media_source_id = generate_media_source_id(
                hass, message, engine=engine, language=language, options=options
            )
            _, data = await async_get_media_source_audio(hass, media_source_id)
            source = FfmpegAudioSource(data=data, ffmpeg_bin=get_ffmpeg_bin(hass))
            client.play_source(source)
        except Exception as err:
            LOGGER.error("Failed to generate and play TTS: %s", err)

    # Helper function to play local/URL audio file
    async def play_audio_file_internal(audio_file: str) -> None:
        try:
            source = FfmpegAudioSource(url=audio_file, ffmpeg_bin=get_ffmpeg_bin(hass))
            client.play_source(source)
        except Exception as err:
            LOGGER.error("Failed to play audio file: %s", err)

    # Helper function to trigger Assist bridge
    async def trigger_assist_internal() -> None:
        nonlocal assist_bridge
        if assist_bridge is not None:
            assist_bridge.close()

        def on_assist_done():
            LOGGER.info("Assist pipeline bridge finished")

        assist_bridge = AssistBridge(
            hass,
            play_source_fn=client.play_source,
            on_done_fn=on_assist_done,
        )
        client.set_sink(assist_bridge)
        assist_bridge.start()

    # Keep a dict of active IVR/Assist objects we can update
    entry_data = entry.runtime_data

    def set_ivr(session: IvrSession | None) -> None:
        nonlocal ivr_session
        ivr_session = session

    entry_data["play_message_fn"] = play_message_internal
    entry_data["play_audio_file_fn"] = play_audio_file_internal
    entry_data["trigger_assist_fn"] = trigger_assist_internal
    entry_data["set_ivr"] = set_ivr
    entry_data["get_ivr"] = lambda: ivr_session
    entry_data["get_assist"] = lambda: assist_bridge

    def set_assist(bridge: AssistBridge | None) -> None:
        nonlocal assist_bridge
        assist_bridge = bridge

    entry_data["set_assist"] = set_assist

    # Register services
    await async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = entry.runtime_data
    if entry_data:
        client: SipClient = entry_data["client"]
        await client.stop()
        # Clean up tasks
        start_task: asyncio.Task = entry_data.get("start_task")
        if start_task:
            start_task.cancel()

        set_ivr = entry_data.get("set_ivr")
        if set_ivr:
            set_ivr(None)
        set_assist = entry_data.get("set_assist")
        if set_assist:
            set_assist(None)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up services if this was the last loaded entry
    if unload_ok:
        loaded_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state.value == "loaded"
        ]
        if not loaded_entries:
            for service in [
                "dial",
                "hangup",
                "answer",
                "send_dtmf",
                "start_recording",
                "stop_recording",
                "start_assist",
            ]:
                try:
                    hass.services.async_remove(DOMAIN, service)
                except Exception:
                    pass

    return unload_ok


async def async_register_services(hass: HomeAssistant) -> None:
    """Register services for the SIP integration."""
    if hass.services.has_service(DOMAIN, "dial"):
        return

    async def get_client_entries(call: ServiceCall) -> list[tuple[str, dict[str, Any]]]:
        """Helper to get all matched client and entry data based on targeted entity ID/device ID/area ID."""
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            LOGGER.error("No active SIP configurations found")
            return []

        # Resolve config entry IDs using Home Assistant's service helper
        try:
            entry_ids = await async_extract_config_entry_ids(call)
        except TypeError:
            entry_ids = await async_extract_config_entry_ids(hass, call)

        matched_entries = []
        if entry_ids:
            for eid in entry_ids:
                entry = hass.config_entries.async_get_entry(eid)
                if entry and entry.domain == DOMAIN and entry.state.value == "loaded":
                    matched_entries.append((entry.entry_id, entry.runtime_data))

        if not matched_entries:
            # Fallback to the first loaded entry
            loaded_entries = [e for e in entries if e.state.value == "loaded"]
            if loaded_entries:
                if len(loaded_entries) > 1:
                    LOGGER.warning(
                        "SIP service called without a target; defaulting to account '%s'. "
                        "Specify a target entity/device to control a specific account.",
                        loaded_entries[0].runtime_data["config"].username,
                    )
                matched_entries.append((loaded_entries[0].entry_id, loaded_entries[0].runtime_data))

        return matched_entries

    async def handle_dial(call: ServiceCall) -> None:
        number = call.data["number"]
        menu = call.data.get("menu")
        ring_timeout = call.data.get("ring_timeout")
        message = call.data.get("message")
        tts_engine = call.data.get("tts_engine")
        language = call.data.get("language")
        tts_options = call.data.get("tts_options")

        targets = await get_client_entries(call)
        if not targets:
            return

        if message:
            try:
                from homeassistant.helpers import template
                message = template.Template(message, hass).async_render()
            except Exception as err:
                LOGGER.error("Failed to render dial message template: %s", err)

        for entry_id, data in targets:
            client: SipClient = data["client"]

            # Track active call details
            data["call_start_time"] = time.time()
            data["call_connect_time"] = None
            data["call_direction"] = "outgoing"
            data["call_status"] = "canceled"
            data["call_number"] = number

            # Load contacts asynchronously and cache them
            contacts_data = await hass.async_add_executor_job(load_contacts, hass)
            data["contacts"] = contacts_data

            friendly_name, _ = get_contact_info_from_cache(contacts_data, number)
            data["last_caller"] = friendly_name

            target_menu = menu
            # Auto-create simple announcement menu if message is provided without a menu
            if not target_menu and message:
                target_menu = {
                    "id": f"simple_dial_announcement_{entry_id}",
                    "tts": {
                        "message": message,
                        "engine": tts_engine,
                        "language": language,
                        "options": tts_options or {},
                    },
                    "post_action": "hangup",
                }

            # Setup IVR Session if menu is provided
            if target_menu:
                session = IvrSession(
                    hass,
                    target_menu,
                    play_message_fn=data["play_message_fn"],
                    play_audio_file_fn=data["play_audio_file_fn"],
                    hangup_fn=client.hangup,
                    fire_event_fn=lambda event, payload, data=data: hass.bus.async_fire(
                        f"{DOMAIN}_{event}",
                        {"sip_account": data["config"].username, **payload},
                    ),
                    trigger_assist_fn=data["trigger_assist_fn"],
                )
                data["set_ivr"](session)
            else:
                data["set_ivr"](None)

            client.call(number, ring_timeout=ring_timeout)

    async def handle_hangup(call: ServiceCall) -> None:
        sip_code = call.data.get("sip_code")
        targets = await get_client_entries(call)
        for entry_id, data in targets:
            client: SipClient = data["client"]
            client.hangup(sip_code=sip_code)

    async def handle_answer(call: ServiceCall) -> None:
        menu = call.data.get("menu")
        message = call.data.get("message")
        tts_engine = call.data.get("tts_engine")
        language = call.data.get("language")
        tts_options = call.data.get("tts_options")

        targets = await get_client_entries(call)
        if not targets:
            return

        if message:
            try:
                from homeassistant.helpers import template
                message = template.Template(message, hass).async_render()
            except Exception as err:
                LOGGER.error("Failed to render answer message template: %s", err)

        for entry_id, data in targets:
            client: SipClient = data["client"]

            target_menu = menu
            # Auto-create simple announcement menu if message is provided without a menu
            if not target_menu and message:
                target_menu = {
                    "id": f"simple_answer_announcement_{entry_id}",
                    "tts": {
                        "message": message,
                        "engine": tts_engine,
                        "language": language,
                        "options": tts_options or {},
                    },
                    "post_action": "hangup",
                }

            if target_menu:
                session = IvrSession(
                    hass,
                    target_menu,
                    play_message_fn=data["play_message_fn"],
                    play_audio_file_fn=data["play_audio_file_fn"],
                    hangup_fn=client.hangup,
                    fire_event_fn=lambda event, payload, data=data: hass.bus.async_fire(
                        f"{DOMAIN}_{event}",
                        {"sip_account": data["config"].username, **payload},
                    ),
                    trigger_assist_fn=data["trigger_assist_fn"],
                )
                data["set_ivr"](session)
            else:
                data["set_ivr"](None)

            client.answer()

    async def handle_send_dtmf(call: ServiceCall) -> None:
        digits = call.data["digits"]
        targets = await get_client_entries(call)
        for entry_id, data in targets:
            client: SipClient = data["client"]
            client.send_dtmf(digits)

    async def handle_start_recording(call: ServiceCall) -> None:
        recording_file = call.data["recording_file"]
        targets = await get_client_entries(call)
        if not targets:
            return

        from .sip_client.audio import WavRecorderSink
        import os

        for entry_id, data in targets:
            client: SipClient = data["client"]
            target_file = recording_file

            # Render template per target so they can use target-specific variables like username
            if target_file:
                try:
                    from homeassistant.helpers import template
                    target_file = template.Template(target_file, hass).async_render(
                        variables={"username": data["config"].username, "entry_id": entry_id}
                    )
                except Exception as err:
                    LOGGER.error("Failed to render recording file path template: %s", err)

            # If there are multiple targets, append the username to avoid file clash
            if len(targets) > 1 and target_file:
                base, ext = os.path.splitext(target_file)
                if data["config"].username not in target_file:
                    target_file = f"{base}_{data['config'].username}{ext}"

            recorder = WavRecorderSink(target_file)
            client.set_sink(recorder)
            data["recorder"] = recorder
            hass.bus.async_fire(
                EVENT_SIP_RECORDING_STARTED,
                {"sip_account": data["config"].username, "recording_file": target_file},
            )
            async_dispatcher_send(
                hass,
                f"{DOMAIN}_event_{entry_id}",
                EVENT_SIP_RECORDING_STARTED,
                {"recording_file": target_file},
            )

    async def handle_stop_recording(call: ServiceCall) -> None:
        targets = await get_client_entries(call)
        for entry_id, data in targets:
            client: SipClient = data["client"]
            recorder = data.get("recorder")
            if recorder:
                recorder.close()
                from .sip_client.audio import NullSink

                client.set_sink(NullSink())
                data.pop("recorder")
                hass.bus.async_fire(
                    EVENT_SIP_RECORDING_STOPPED,
                    {"sip_account": data["config"].username},
                )
                async_dispatcher_send(
                    hass,
                    f"{DOMAIN}_event_{entry_id}",
                    EVENT_SIP_RECORDING_STOPPED,
                    None,
                )

    async def handle_start_assist(call: ServiceCall) -> None:
        targets = await get_client_entries(call)
        for entry_id, data in targets:
            await data["trigger_assist_fn"]()

    # Register all services
    hass.services.async_register(DOMAIN, "dial", handle_dial, schema=SERVICE_DIAL_SCHEMA)
    hass.services.async_register(DOMAIN, "hangup", handle_hangup, schema=SERVICE_HANGUP_SCHEMA)
    hass.services.async_register(DOMAIN, "answer", handle_answer, schema=SERVICE_ANSWER_SCHEMA)
    hass.services.async_register(DOMAIN, "send_dtmf", handle_send_dtmf, schema=SERVICE_SEND_DTMF_SCHEMA)

    hass.services.async_register(
        DOMAIN, "start_recording", handle_start_recording, schema=SERVICE_RECORDING_SCHEMA
    )
    hass.services.async_register(DOMAIN, "stop_recording", handle_stop_recording, schema=SERVICE_GENERIC_SCHEMA)
    hass.services.async_register(DOMAIN, "start_assist", handle_start_assist, schema=SERVICE_GENERIC_SCHEMA)
