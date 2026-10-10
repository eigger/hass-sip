"""Per-call SIP tools for the Home Assistant Assist LLM API."""

from __future__ import annotations

from typing import override

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm

from .assist import AssistBridge
from .const import (
    ASSIST_HANGUP_MERGED_TOOL_NAME,
    ASSIST_HANGUP_TOOL_NAME,
    DOMAIN,
)
from .sip_client.sip_client import SipClient, SipState


class SipHangUpCurrentCallTool(llm.Tool):
    """Queue hang-up for the SIP call that created this Assist request."""

    name = ASSIST_HANGUP_TOOL_NAME
    title = "Hang up current SIP call"
    description = (
        "End this phone call after the current Assist reply finishes. "
        "Call this when the caller asks to hang up. You may complete other "
        "requested actions and say a brief goodbye in this turn."
    )
    integration = DOMAIN
    annotations = llm.ToolAnnotations(
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=False,
    )

    def __init__(
        self,
        entry_data: dict,
        bridge: AssistBridge,
        client: SipClient,
    ) -> None:
        """Bind the tool to one bridge and one SIP call."""
        self._entry_data = entry_data
        self._bridge = bridge
        self._client = client

    def _is_current_call(self, llm_context: llm.LLMContext) -> bool:
        """Reject a tool retained after the SIP call or Assist bridge changes."""
        get_assist = self._entry_data.get("get_assist")
        return bool(
            get_assist is not None
            and get_assist() is self._bridge
            and self._bridge.allow_llm_hangup
            and self._bridge.is_running
            and llm_context.context is not None
            and self._bridge.matches_llm_context(
                llm_context.device_id, llm_context.context.user_id
            )
            and self._client.state in (SipState.IN_CALL, SipState.ANSWERING)
        )

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Schedule BYE after intent processing and outbound audio finish."""
        if not self._is_current_call(llm_context):
            return llm.ToolResult(
                data={"error": "SIP call is no longer active"}, error=True
            )
        if not self._bridge.request_hangup_after_turn():
            return llm.ToolResult(
                data={"error": "SIP hang-up is unavailable"}, error=True
            )
        return llm.ToolResult(data={"status": "scheduled_after_turn"})


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str
) -> LLMTools | None:
    """Expose Hang Up only to an opted-in, active SIP Assist call."""
    if api_id != llm.LLM_API_ASSIST or llm_context.device_id is None:
        return None

    for entry in hass.config_entries.async_entries(DOMAIN):
        data = entry.runtime_data
        get_assist = data.get("get_assist")
        if get_assist is None:
            continue
        bridge = get_assist()
        client = data.get("client")
        if bridge is not None and client is not None:
            tool = SipHangUpCurrentCallTool(data, bridge, client)
        else:
            continue
        if tool._is_current_call(llm_context):
            return LLMTools(
                tools=[tool],
                prompt=(
                    "For this SIP phone call, use the Hang Up tool when the "
                    "caller asks to hang up. If the available tools include "
                    f"{ASSIST_HANGUP_MERGED_TOOL_NAME}, use that exact name. "
                    f"Otherwise use {ASSIST_HANGUP_TOOL_NAME} if available. "
                    "The tool schedules BYE "
                    "after this turn and its spoken reply. Finish any other "
                    "requested tool actions, then give a brief goodbye. "
                    "Do not claim the call already ended when the tool only "
                    "reports that hang-up was scheduled."
                ),
            )
    return None
