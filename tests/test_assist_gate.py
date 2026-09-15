"""Assist allow-list and PIN gate (P4-1)."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import voluptuous as vol

from test_pure import _assist_ctx, _load_component_module

gate = _load_component_module("assist_gate")


def test_normalize_caller_strips_sip_uri():
    assert gate.normalize_caller("sip:100@pbx.local") == "100"
    assert gate.normalize_caller("<sip:101@192.0.2.1>") == "101"
    assert gate.normalize_caller(" 102 ") == "102"
    assert gate.normalize_caller("+82-10-1234-5678") == "+821012345678"


def test_normalize_caller_keeps_alphanumeric_user_part():
    assert gate.normalize_caller("cam2") == "cam2"
    assert gate.normalize_caller("Doorbird1") == "doorbird1"
    assert gate.normalize_caller("sip:kitchen1@pbx") == "kitchen1"
    assert gate.normalize_caller("<sip:CAM2@192.0.2.1>") == "cam2"


def test_alphanumeric_callers_do_not_collide_on_trailing_digit():
    assert gate.caller_is_allowed("cam2", allowed_callers=["door2"]) is False
    assert gate.caller_is_allowed("cam2", allowed_callers=["cam2"]) is True
    assert gate.caller_is_allowed("sip:CAM2@pbx", allowed_callers=["cam2"]) is True
    contacts = {"door2": "Gate", "kitchen1": "Kitchen"}
    assert (
        gate.caller_is_allowed("cam2", contacts=contacts, contacts_only=True) is False
    )
    assert (
        gate.caller_is_allowed("kitchen1", contacts=contacts, contacts_only=True)
        is True
    )


def test_caller_allowed_when_no_restriction():
    assert gate.caller_is_allowed("sip:999@pbx") is True
    assert gate.caller_is_allowed("") is True


def test_empty_allow_list_rejects_everyone():
    assert gate.caller_is_allowed("100", allowed_callers=[]) is False
    assert gate.caller_is_allowed("", allowed_callers=[]) is False


def test_caller_rejected_when_not_on_allow_list():
    assert (
        gate.caller_is_allowed(
            "sip:200@pbx",
            allowed_callers=["100", "101"],
        )
        is False
    )
    assert (
        gate.caller_is_allowed(
            "sip:100@pbx",
            allowed_callers=["100", "101"],
        )
        is True
    )


def test_caller_rejected_when_contacts_only_and_unknown():
    contacts = {"100": "Dad", "102": {"name": "Gate", "auto_answer": True}}
    assert (
        gate.caller_is_allowed(
            "200",
            contacts=contacts,
            contacts_only=True,
        )
        is False
    )
    assert (
        gate.caller_is_allowed(
            "sip:102@pbx",
            contacts=contacts,
            contacts_only=True,
        )
        is True
    )


def test_caller_allow_list_and_contacts_only_intersect():
    contacts = {"100": "Dad", "101": "Mom"}
    assert (
        gate.caller_is_allowed(
            "100",
            allowed_callers=["100", "200"],
            contacts=contacts,
            contacts_only=True,
        )
        is True
    )
    assert (
        gate.caller_is_allowed(
            "200",
            allowed_callers=["100", "200"],
            contacts=contacts,
            contacts_only=True,
        )
        is False
    )


def test_pin_collector_accepts_matching_digits():
    async def run():
        collector = gate.PinCollector("1234")
        collector.handle_digit("1")
        collector.handle_digit("2")
        collector.handle_digit("3")
        collector.handle_digit("4")
        return await collector.wait(timeout=1)

    assert asyncio.run(run()) == "ok"


def test_pin_collector_hash_submits_early():
    async def run():
        collector = gate.PinCollector("1234")
        collector.handle_digit("1")
        collector.handle_digit("#")
        return await collector.wait(timeout=1)

    assert asyncio.run(run()) == gate.REASON_PIN_MISMATCH


def test_pin_collector_rejects_mismatch_without_exposing_digits():
    async def run():
        collector = gate.PinCollector("12")
        collector.handle_digit("9")
        collector.handle_digit("9")
        result = await collector.wait(timeout=1)
        assert not hasattr(result, "pin")
        return result

    assert asyncio.run(run()) == gate.REASON_PIN_MISMATCH


def test_pin_collector_times_out():
    async def run():
        collector = gate.PinCollector("12")
        return await collector.wait(timeout=0.05)

    assert asyncio.run(run()) == gate.REASON_PIN_TIMEOUT


def test_pin_collector_fail_is_mismatch():
    async def run():
        collector = gate.PinCollector("12")
        collector.fail()
        return await collector.wait(timeout=1)

    assert asyncio.run(run()) == gate.REASON_PIN_MISMATCH


def test_take_pin_digit_consumes_without_exposing():
    async def run():
        collector = gate.PinCollector("47")
        assert gate.take_pin_digit(collector, "4") is True
        assert gate.take_pin_digit(collector, "7") is True
        return await collector.wait(timeout=1)

    assert asyncio.run(run()) == "ok"
    assert gate.take_pin_digit(None, "4") is False


def _load_sip_init():
    """Load __init__.py with a cv stub that includes ensure_list (P4-1 schema)."""
    _assist_ctx()
    helpers = sys.modules["homeassistant.helpers"]
    cv_stub = types.SimpleNamespace(
        boolean=bool,
        match_all=lambda value: value,
        positive_int=int,
        string=str,
        ensure_list=lambda v: list(v) if isinstance(v, (list, tuple)) else [v],
        make_entity_service_schema=lambda schema: vol.Schema(schema),
    )
    helpers.config_validation = cv_stub
    sys.modules["homeassistant.helpers.config_validation"] = cv_stub
    service_stub = types.ModuleType("homeassistant.helpers.service")
    service_stub.async_extract_config_entry_ids = AsyncMock()
    sys.modules["homeassistant.helpers.service"] = service_stub

    module_name = "custom_components.sip._p4_1_init_test"
    existing = sys.modules.get(module_name)
    if existing is not None and hasattr(existing, "async_register_services"):
        return existing
    sys.modules.pop(module_name, None)

    component = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "sip"
    )
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(os.path.abspath(component), "__init__.py")
    )
    integration = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = integration
    spec.loader.exec_module(integration)
    return integration


def _rejected_events(hass):
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args and call.args[0] == "sip_assist_rejected"
    ]


async def _register_start_assist(integration, *, caller, contacts=None):
    trigger_assist = AsyncMock()
    entry = MagicMock()
    entry.domain = "sip"
    entry.entry_id = "entry-1"
    entry.state.value = "loaded"
    entry.runtime_data = {
        "trigger_assist_fn": trigger_assist,
        "call_number": caller,
        "config": types.SimpleNamespace(username="100"),
        "contacts": contacts or {},
        "pin_collector": None,
    }
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.config_entries.async_entries.return_value = [entry]
    hass.config_entries.async_get_entry.return_value = entry
    integration.async_extract_config_entry_ids = AsyncMock(return_value={"entry-1"})
    await integration.async_register_services(hass)
    handler = next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[:2] == ("sip", "start_assist")
    )
    return hass, trigger_assist, entry, handler


def test_start_assist_rejects_unknown_caller():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="sip:200@pbx"
        )
        data = integration.SERVICE_ASSIST_SCHEMA({"allowed_callers": ["100"]})
        await handler(_service_call(data))
        return hass, trigger

    hass, trigger = asyncio.run(run())
    trigger.assert_not_awaited()
    rejected = _rejected_events(hass)
    assert len(rejected) == 1
    assert rejected[0]["caller"] == "sip:200@pbx"
    assert rejected[0]["reason"] == gate.REASON_NOT_ALLOWED
    assert "pin" not in rejected[0]


def test_start_assist_allows_listed_caller():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="sip:100@pbx"
        )
        data = integration.SERVICE_ASSIST_SCHEMA({"allowed_callers": ["100"]})
        await handler(_service_call(data))
        return hass, trigger

    hass, trigger = asyncio.run(run())
    trigger.assert_awaited_once()
    assert _rejected_events(hass) == []


# ---------------------------------------------- service target / permissions
def _service_call(data, *, user_id=None):
    """Shape a ServiceCall like HA does: ``context`` is always present."""
    return types.SimpleNamespace(
        data=data, context=types.SimpleNamespace(user_id=user_id)
    )


def _phone_line_entity():
    return types.SimpleNamespace(
        entity_id="media_player.phone_line", domain="media_player", platform="sip"
    )


def _user(*, control: bool, is_admin: bool = False):
    user = MagicMock()
    user.is_admin = is_admin
    user.permissions.check_entity.return_value = control
    return user


def test_explicit_unmatched_target_does_not_fall_back_to_first_account():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        integration.async_extract_config_entry_ids = AsyncMock(return_value=set())
        try:
            await handler(
                _service_call({"entity_id": ["media_player.missing_phone_line"]})
            )
        except integration.ServiceValidationError:
            return trigger
        raise AssertionError("expected ServiceValidationError")

    asyncio.run(run()).assert_not_awaited()


def test_untargeted_call_falls_back_to_first_account():
    integration = _load_sip_init()

    async def run():
        _hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        integration.async_extract_config_entry_ids = AsyncMock(return_value=set())
        await handler(_service_call({}))
        return trigger

    asyncio.run(run()).assert_awaited_once()


def test_service_user_must_control_target_phone_line():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        hass.auth.async_get_user = AsyncMock(return_value=_user(control=False))
        integration.er.async_entries_for_config_entry.return_value = [
            _phone_line_entity()
        ]
        try:
            await handler(
                _service_call(
                    {"entity_id": ["media_player.phone_line"]}, user_id="ordinary"
                )
            )
        except integration.Unauthorized as err:
            assert err.permission == "control"
            return trigger
        raise AssertionError("expected unauthorized service call")

    asyncio.run(run()).assert_not_awaited()


def test_untargeted_fallback_still_checks_user_permission():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        integration.async_extract_config_entry_ids = AsyncMock(return_value=set())
        hass.auth.async_get_user = AsyncMock(return_value=_user(control=False))
        integration.er.async_entries_for_config_entry.return_value = [
            _phone_line_entity()
        ]
        try:
            await handler(_service_call({}, user_id="ordinary"))
        except integration.Unauthorized:
            return trigger
        raise AssertionError("expected unauthorized service call")

    asyncio.run(run()).assert_not_awaited()


def test_authorized_service_user_can_control_target_phone_line():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        hass.auth.async_get_user = AsyncMock(return_value=_user(control=True))
        integration.er.async_entries_for_config_entry.return_value = [
            _phone_line_entity()
        ]
        await handler(
            _service_call(
                {"entity_id": ["media_player.phone_line"]}, user_id="ordinary"
            )
        )
        return trigger

    asyncio.run(run()).assert_awaited_once()


def test_admin_is_not_locked_out_when_phone_line_entity_missing():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        hass.auth.async_get_user = AsyncMock(
            return_value=_user(control=False, is_admin=True)
        )
        integration.er.async_entries_for_config_entry.return_value = []
        await handler(
            _service_call(
                {"entity_id": ["media_player.phone_line"]}, user_id="admin"
            )
        )
        return trigger

    asyncio.run(run()).assert_awaited_once()


def test_restricted_user_denied_when_phone_line_entity_missing():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        hass.auth.async_get_user = AsyncMock(return_value=_user(control=True))
        integration.er.async_entries_for_config_entry.return_value = []
        try:
            await handler(
                _service_call(
                    {"entity_id": ["media_player.phone_line"]}, user_id="ordinary"
                )
            )
        except integration.Unauthorized:
            return trigger
        raise AssertionError("expected fail-closed for restricted user")

    asyncio.run(run()).assert_not_awaited()


def test_unknown_service_user_is_rejected():
    integration = _load_sip_init()

    async def run():
        hass, trigger, _entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        hass.auth.async_get_user = AsyncMock(return_value=None)
        try:
            await handler(
                _service_call(
                    {"entity_id": ["media_player.phone_line"]}, user_id="ghost"
                )
            )
        except integration.UnknownUser:
            return trigger
        raise AssertionError("expected UnknownUser")

    asyncio.run(run()).assert_not_awaited()


def test_start_assist_pin_mismatch_does_not_start():
    integration = _load_sip_init()

    async def run():
        hass, trigger, entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        data = integration.SERVICE_ASSIST_SCHEMA({"pin": "1234"})
        task = asyncio.create_task(handler(_service_call(data)))
        collector = None
        for _ in range(50):
            collector = entry.runtime_data.get("pin_collector")
            if collector is not None:
                break
            await asyncio.sleep(0)
        assert collector is not None
        for digit in "9999":
            collector.handle_digit(digit)
        await task
        return hass, trigger

    hass, trigger = asyncio.run(run())
    trigger.assert_not_awaited()
    rejected = _rejected_events(hass)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == gate.REASON_PIN_MISMATCH
    assert "1234" not in str(rejected[0])
    assert "9999" not in str(rejected[0])


def test_start_assist_pin_ok_starts_assist():
    integration = _load_sip_init()

    async def run():
        hass, trigger, entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        data = integration.SERVICE_ASSIST_SCHEMA({"pin": "1234"})
        task = asyncio.create_task(handler(_service_call(data)))
        collector = None
        for _ in range(50):
            collector = entry.runtime_data.get("pin_collector")
            if collector is not None:
                break
            await asyncio.sleep(0)
        assert collector is not None
        for digit in "1234":
            collector.handle_digit(digit)
        await task
        return hass, trigger

    hass, trigger = asyncio.run(run())
    trigger.assert_awaited_once()
    assert _rejected_events(hass) == []


def test_start_assist_closes_active_ivr_session():
    """sip.dial/answer ``message`` arms an announcement IVR (post_action:
    hangup); sip.start_assist must retire it so Assist's own playback_done
    does not hang up after the first reply (#92)."""
    integration = _load_sip_init()

    async def run():
        hass, trigger, entry, handler = await _register_start_assist(
            integration, caller="100"
        )
        ivr = MagicMock()
        set_ivr = MagicMock()
        entry.runtime_data["get_ivr"] = lambda: ivr
        entry.runtime_data["set_ivr"] = set_ivr
        data = integration.SERVICE_ASSIST_SCHEMA({})
        await handler(_service_call(data))
        return trigger, ivr, set_ivr

    trigger, ivr, set_ivr = asyncio.run(run())
    ivr.close.assert_called_once()
    set_ivr.assert_called_once_with(None)
    trigger.assert_awaited_once()


def test_start_assist_rejected_leaves_ivr_session_alone():
    integration = _load_sip_init()

    async def run():
        hass, trigger, entry, handler = await _register_start_assist(
            integration, caller="200"
        )
        ivr = MagicMock()
        entry.runtime_data["get_ivr"] = lambda: ivr
        entry.runtime_data["set_ivr"] = MagicMock()
        data = integration.SERVICE_ASSIST_SCHEMA({"allowed_callers": ["100"]})
        await handler(_service_call(data))
        return trigger, ivr

    trigger, ivr = asyncio.run(run())
    ivr.close.assert_not_called()
    trigger.assert_not_awaited()
