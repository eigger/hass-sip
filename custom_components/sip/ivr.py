"""IVR (Interactive Voice Response) menu engine for SIP Client.

Menu schema (canonical, flat — matches the service TTS parameters):

    id: main                 # optional, target for "goto"
    message: "Welcome"       # TTS text to speak
    audio_file: /media/x.wav # OR an audio file / URL to play
    template: true           # render `message` as a Jinja template
    tts_engine: tts.piper    # TTS engine entity
    language: ko             # TTS language
    tts_options: {...}       # extra TTS voice/speech options
    wait_for_audio: true     # collect input only after playback finishes
    timeout: 10              # seconds to wait for input
    input: digit             # "digit" (single key) or "pin" (multi-key + #)
    choices:                 # input key -> target (pure digits/pins only)
      "1": { ... }
    on_invalid: { ... }      # target when input matches no choice
    on_timeout: { ... }      # target when no input arrives in time
    action: { ... }          # HA service to call on entry
    assist: true             # hand the call to Home Assistant Voice Assist
    post_action: hangup      # terminal action: hangup | repeat | back [n] | goto <id> | wait

A target ("choices" value, on_invalid, on_timeout) is either a nested menu dict
or a bare post_action string.
"""
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
        play_message_fn: Callable[
            [str, str | None, str | None, dict[str, Any] | None], Coroutine[Any, Any, None]
        ],
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

    # -- input handling -------------------------------------------------
    async def handle_dtmf(self, digit: str) -> None:
        """Process a received DTMF digit."""
        if not self.is_active or not self.waiting_for_dtmf:
            return

        self._reset_timeout()
        self.digit_buffer += digit
        LOGGER.debug("IVR digit buffer: %s", self.digit_buffer)

        choices = self.current_menu.get("choices", {})

        if self.current_menu.get("input") == "pin":
            if digit == "#":
                await self._resolve_input(self.digit_buffer[:-1])  # strip '#'
            elif self.digit_buffer in choices:
                await self._resolve_input(self.digit_buffer)
            else:
                max_len = max([len(k) for k in choices] or [0])
                if len(self.digit_buffer) >= max_len:
                    await self._resolve_input(self.digit_buffer)
        else:  # single-digit mode
            await self._resolve_input(self.digit_buffer)

    async def _resolve_input(self, key: str) -> None:
        """Route a completed input to its choice / on_invalid / keep waiting."""
        self.digit_buffer = ""
        choices = self.current_menu.get("choices", {})
        if key in choices:
            await self._execute_choice(choices[key])
        elif "on_invalid" in self.current_menu:
            await self._execute_choice(self.current_menu["on_invalid"])
        else:
            LOGGER.debug("IVR: no choice for '%s'; awaiting further input", key)
            self._start_dtmf_collection()

    # -- menu playback --------------------------------------------------
    async def _enter_menu(self, menu: dict[str, Any]) -> None:
        """Enter a menu: run its action, hand off to Assist, or play its prompt."""
        if not self.is_active:
            return

        self.current_menu = menu
        menu_id = menu.get("id")
        if menu_id:
            self.fire_event("entered_menu", {"menu_id": menu_id})

        action = menu.get("action")
        if action:
            await self._run_ha_action(action)

        if menu.get("assist"):
            self.is_active = False
            self._reset_timeout()
            await self.trigger_assist()
            return

        message = menu.get("message", "")
        audio_file = menu.get("audio_file", "")
        if menu.get("template") and message:
            message = self._render(message)

        self.waiting_for_dtmf = False

        if audio_file:
            await self.play_audio_file(audio_file)
        elif message:
            await self.play_message(
                message,
                menu.get("language"),
                menu.get("tts_engine"),
                menu.get("tts_options"),
            )
        else:
            # Nothing to play; collect input right away.
            self._start_dtmf_collection()
            return

        # With playback, wait for on_playback_done unless told not to.
        if not menu.get("wait_for_audio", True):
            self._start_dtmf_collection()

    def on_playback_done(self) -> None:
        """Called when audio playback finishes."""
        if not self.is_active:
            return

        if not self.current_menu.get("choices"):
            # An announcement (no choices): run its terminal action immediately.
            post_action = self.current_menu.get("post_action", "hangup")
            self.hass.async_create_task(self._execute_post_action(post_action))
            return

        if self.waiting_for_dtmf:
            return
        self._start_dtmf_collection()

    # -- input collection / timeout ------------------------------------
    def _start_dtmf_collection(self) -> None:
        """Begin collecting DTMF input and (re)arm the timeout."""
        self.waiting_for_dtmf = True
        self.digit_buffer = ""
        self._reset_timeout()

    def _reset_timeout(self) -> None:
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
        on_timeout = self.current_menu.get("on_timeout")
        if on_timeout is not None:
            await self._execute_choice(on_timeout)
        else:
            await self._execute_post_action(self.current_menu.get("post_action", "hangup"))

    # -- choices / actions ---------------------------------------------
    async def _execute_choice(self, choice: dict[str, Any] | str) -> None:
        """Execute a choice: a bare post_action string, a sub-menu, or an action."""
        if isinstance(choice, str):
            await self._execute_post_action(choice)
            return

        if choice.get("assist"):
            self.is_active = False
            self._reset_timeout()
            await self.trigger_assist()
            return

        # A sub-menu / prompt handles its own playback and terminal action.
        if "choices" in choice or "message" in choice or "audio_file" in choice:
            self.menu_stack.append(self.current_menu)
            await self._enter_menu(choice)
            return

        # An action-only choice: run it, then apply its post_action.
        if "action" in choice:
            await self._run_ha_action(choice["action"])
        await self._execute_post_action(choice.get("post_action", "wait"))

    async def _execute_post_action(self, post_action: str) -> None:
        """Terminal keywords: hangup | repeat | back [n] | goto <id> | wait."""
        if not self.is_active:
            return

        parts = post_action.split()
        cmd = parts[0].lower() if parts else "wait"

        if cmd == "hangup":
            self.close()
            self.hangup()
        elif cmd == "back":
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
        elif cmd == "repeat":
            await self._enter_menu(self.current_menu)
        elif cmd == "goto":
            if len(parts) > 1:
                target = self._find_menu_by_id(self.root_menu, parts[1])
                if target:
                    await self._enter_menu(target)
                else:
                    LOGGER.error("IVR goto target '%s' not found", parts[1])
                    await self._enter_menu(self.current_menu)
        elif cmd == "wait":
            if not self.current_menu.get("choices"):
                LOGGER.warning(
                    "IVR menu has no choices but post_action is 'wait'; the call "
                    "will stay open with no way to proceed."
                )
            self._start_dtmf_collection()

    def _find_menu_by_id(self, menu: dict[str, Any], menu_id: str) -> dict[str, Any] | None:
        """Search recursively (choices, on_invalid, on_timeout) for a menu id."""
        if menu.get("id") == menu_id:
            return menu
        candidates = list(menu.get("choices", {}).values())
        candidates.extend(menu.get(key) for key in ("on_invalid", "on_timeout"))
        for candidate in candidates:
            if isinstance(candidate, dict):
                found = self._find_menu_by_id(candidate, menu_id)
                if found:
                    return found
        return None

    async def _run_ha_action(self, action: dict[str, Any]) -> None:
        """Call a Home Assistant service."""
        domain = action.get("domain")
        service = action.get("service")
        entity_id = action.get("entity_id")

        if not domain or not service:
            LOGGER.error("IVR action missing domain or service: %s", action)
            return

        data = dict(action.get("data") or {})
        if entity_id:
            data["entity_id"] = entity_id

        LOGGER.info("IVR action: %s.%s with %s", domain, service, data)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception as err:  # noqa: BLE001
            LOGGER.error("IVR action service call failed: %s", err)

    # -- helpers --------------------------------------------------------
    def _render(self, text: str) -> str:
        try:
            return template.Template(text, self.hass).async_render()
        except Exception as err:  # noqa: BLE001
            LOGGER.error("Failed to render IVR template: %s", err)
            return text

    def close(self) -> None:
        """Close the IVR session and clean up resources."""
        self.is_active = False
        self.waiting_for_dtmf = False
        self._reset_timeout()
