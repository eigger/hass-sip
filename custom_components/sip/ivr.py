"""IVR (Interactive Voice Response) Menu Engine for SIP Client."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import template

from .const import LOGGER


class IvrSession:
    """Manages the state and execution of a single IVR call session."""

    def __init__(
        self,
        hass: HomeAssistant,
        menu_config: dict[str, Any],
        play_message_fn: Callable[[str, str | None, str | None, dict[str, Any] | None], Coroutine[Any, Any, None]],
        play_audio_file_fn: Callable[[str], Coroutine[Any, Any, None]],
        hangup_fn: Callable[[], None],
        fire_event_fn: Callable[[str, dict[str, Any]], None],
        trigger_assist_fn: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Initialize the IVR session."""
        self.hass = hass
        self.root_menu = menu_config
        self.play_message = play_message_fn
        self.play_audio_file = play_audio_file_fn
        self.hangup = hangup_fn
        self.fire_event = fire_event_fn
        self.trigger_assist = trigger_assist_fn

        self.current_menu = menu_config
        self.menu_stack: list[dict[str, Any]] = []
        self.digit_buffer = ""
        self.timeout_task: asyncio.Task | None = None
        self.waiting_for_dtmf = False
        self.is_active = True

    async def start(self) -> None:
        """Start the IVR session."""
        await self._enter_menu(self.root_menu)

    async def handle_dtmf(self, digit: str) -> None:
        """Process a received DTMF digit."""
        if not self.is_active or not self.waiting_for_dtmf:
            return

        self._reset_timeout()
        self.digit_buffer += digit
        LOGGER.debug("IVR digit buffer: %s", self.digit_buffer)

        choices = self.current_menu.get("choices", {})
        choices_are_pin = self.current_menu.get("choices_are_pin", False)

        if choices_are_pin:
            # PIN logic: check for # delimiter or match
            if digit == "#":
                pin = self.digit_buffer[:-1]  # remove '#'
                await self._process_pin(pin)
            else:
                # If it exactly matches one of the PIN keys, process it immediately
                if self.digit_buffer in choices:
                    await self._process_pin(self.digit_buffer)
                else:
                    # If the buffer has grown longer than the longest PIN in choices, trigger default
                    max_len = max([len(k) for k in choices.keys() if k != "default"] or [0])
                    if len(self.digit_buffer) >= max_len:
                        await self._process_pin(self.digit_buffer)
        else:
            # Single digit menu logic
            if self.digit_buffer in choices:
                choice = choices[self.digit_buffer]
                self.digit_buffer = ""
                await self._execute_choice(choice)
            elif "default" in choices:
                # If there's no match for this single digit, use default
                choice = choices["default"]
                self.digit_buffer = ""
                await self._execute_choice(choice)

    async def _process_pin(self, pin: str) -> None:
        choices = self.current_menu.get("choices", {})
        self.digit_buffer = ""
        if pin in choices:
            await self._execute_choice(choices[pin])
        elif "default" in choices:
            await self._execute_choice(choices["default"])
        else:
            # No matching pin and no default choice: repeat message or return
            LOGGER.warning("No choice matched PIN '%s' and no default choice", pin)
            await self._enter_menu(self.current_menu)

    async def _enter_menu(self, menu: dict[str, Any]) -> None:
        """Enter a new menu or sub-menu."""
        if not self.is_active:
            return

        self.current_menu = menu
        menu_id = menu.get("id")
        if menu_id:
            self.fire_event("entered_menu", {"menu_id": menu_id})

        # Run HA Action if defined
        action = menu.get("action")
        if action:
            await self._run_ha_action(action)

        # Handle message rendering and playback
        tts_config = menu.get("tts", {}) if isinstance(menu.get("tts"), dict) else {}
        msg = tts_config.get("message") or tts_config.get("text") or menu.get("message", "")
        audio_file = menu.get("audio_file", "")
        lang = tts_config.get("language") or tts_config.get("lang") or menu.get("language")
        engine = tts_config.get("engine") or tts_config.get("tts_engine") or menu.get("tts_engine") or menu.get("engine")
        options = tts_config.get("options") or tts_config.get("tts_options") or menu.get("tts_options") or menu.get("options")

        if (menu.get("handle_as_template", False) or tts_config.get("handle_as_template", False)) and msg:
            try:
                msg = template.Template(msg, self.hass).async_render()
            except Exception as err:
                LOGGER.error("Failed to render IVR message template: %s", err)

        self.waiting_for_dtmf = False

        if audio_file:
            await self.play_audio_file(audio_file)
        elif msg:
            await self.play_message(msg, lang, engine, options)
        else:
            # No playback, jump straight to DTMF collection
            self._start_dtmf_collection()
            return

        # If we do not wait for audio to finish, start DTMF collection immediately.
        # Otherwise, the client's on_call_playback_done callback will trigger
        # on_playback_done() when playing finishes.
        if not menu.get("wait_for_audio_to_finish", True):
            self._start_dtmf_collection()

    def on_playback_done(self) -> None:
        """Called when audio playback completes."""
        if self.is_active and not self.waiting_for_dtmf:
            if not self.current_menu.get("choices"):
                post_action = self.current_menu.get("post_action", "hangup")
                self.hass.async_create_task(self._execute_post_action(post_action))
            else:
                self._start_dtmf_collection()

    def _start_dtmf_collection(self) -> None:
        """Start collecting DTMF inputs and set a timeout timer."""
        self.waiting_for_dtmf = True
        self.digit_buffer = ""
        self._reset_timeout()

    def _reset_timeout(self) -> None:
        """Reset the menu timeout timer."""
        if self.timeout_task:
            self.timeout_task.cancel()
            self.timeout_task = None

        timeout_sec = self.current_menu.get("timeout", 10)
        self.timeout_task = asyncio.create_task(self._timeout_timer(timeout_sec))

    async def _timeout_timer(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            await self._handle_timeout()
        except asyncio.CancelledError:
            pass

    async def _handle_timeout(self) -> None:
        LOGGER.info("IVR menu timeout reached")
        choices = self.current_menu.get("choices", {})
        if "timeout" in choices:
            await self._execute_choice(choices["timeout"])
        else:
            # Default timeout behavior: execute post_action if any, otherwise hang up
            post_action = self.current_menu.get("post_action", "hangup")
            await self._execute_post_action(post_action)

    async def _execute_choice(self, choice: dict[str, Any] | str) -> None:
        """Execute the selected choice."""
        if isinstance(choice, str):
            await self._execute_post_action(choice)
            return

        # Check if the choice itself is a Voice Assist trigger
        action = choice.get("action", {})
        if action and action.get("domain") == "assist_pipeline":
            # Jump to HA Voice Assist
            self.is_active = False
            self._reset_timeout()
            await self.trigger_assist()
            return

        # If it is a full sub-menu (or has choices/messages), push to stack and enter it
        if "choices" in choice or "message" in choice or "audio_file" in choice:
            self.menu_stack.append(self.current_menu)
            await self._enter_menu(choice)
            return

        # If the choice has its own action, run it
        if "action" in choice:
            await self._run_ha_action(choice["action"])

        # Execute choice message or audio file if present
        tts_config = choice.get("tts", {}) if isinstance(choice.get("tts"), dict) else {}
        msg = tts_config.get("message") or tts_config.get("text") or choice.get("message", "")
        audio_file = choice.get("audio_file", "")
        lang = tts_config.get("language") or tts_config.get("lang") or choice.get("language")
        engine = tts_config.get("engine") or tts_config.get("tts_engine") or choice.get("tts_engine") or choice.get("engine")
        options = tts_config.get("options") or tts_config.get("tts_options") or choice.get("tts_options") or choice.get("options")

        if (choice.get("handle_as_template", False) or tts_config.get("handle_as_template", False)) and msg:
            try:
                msg = template.Template(msg, self.hass).async_render()
            except Exception as err:
                LOGGER.error("Failed to render IVR choice template: %s", err)

        if audio_file:
            await self.play_audio_file(audio_file)
        elif msg:
            await self.play_message(msg, lang, engine, options)

        post_action = choice.get("post_action", "noop")
        await self._execute_post_action(post_action)

    async def _execute_post_action(self, post_action: str) -> None:
        """Process post_action keywords like return, hangup, repeat_message, jump."""
        if not self.is_active:
            return

        parts = post_action.split()
        cmd = parts[0].lower() if parts else "noop"

        if cmd == "hangup":
            self.close()
            self.hangup()
        elif cmd == "return":
            levels = 1
            if len(parts) > 1:
                try:
                    levels = int(parts[1])
                except ValueError:
                    pass
            for _ in range(levels):
                if self.menu_stack:
                    self.current_menu = self.menu_stack.pop()
            await self._enter_menu(self.current_menu)
        elif cmd == "repeat_message":
            await self._enter_menu(self.current_menu)
        elif cmd == "jump":
            if len(parts) > 1:
                menu_id = parts[1]
                target = self._find_menu_by_id(self.root_menu, menu_id)
                if target:
                    await self._enter_menu(target)
                else:
                    LOGGER.error("IVR jump target menu ID '%s' not found", menu_id)
                    await self._enter_menu(self.current_menu)
        elif cmd == "noop":
            if not self.current_menu.get("choices"):
                LOGGER.warning("IVR menu has no choices but post_action is 'noop'. This will keep the call open indefinitely without any interaction options.")
            # Just keep waiting for DTMF in the current menu
            self._start_dtmf_collection()

    def _find_menu_by_id(self, menu: dict[str, Any], menu_id: str) -> dict[str, Any] | None:
        """Search recursively for a menu or choice matching the given menu_id."""
        if menu.get("id") == menu_id:
            return menu

        choices = menu.get("choices", {})
        for _, choice in choices.items():
            if isinstance(choice, dict):
                res = self._find_menu_by_id(choice, menu_id)
                if res:
                    return res
        return None

    async def _run_ha_action(self, action: dict[str, Any]) -> None:
        """Trigger a native Home Assistant service call."""
        domain = action.get("domain")
        service = action.get("service")
        entity_id = action.get("entity_id")
        service_data = action.get("service_data") or action.get("data") or {}

        if not domain or not service:
            LOGGER.error("IVR action missing domain or service: %s", action)
            return

        data = dict(service_data)
        if entity_id:
            data["entity_id"] = entity_id

        LOGGER.info("IVR action calling service: %s.%s with data %s", domain, service, data)
        try:
            await self.hass.services.async_call(
                domain, service, data, blocking=False
            )
        except Exception as err:
            LOGGER.error("Failed to run IVR action service call: %s", err)

    def close(self) -> None:
        """Close the IVR session and clean up resources."""
        self.is_active = False
        self.waiting_for_dtmf = False
        self._reset_timeout()
