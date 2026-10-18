"""Assist allow-list and PIN gate (P4-1): pure gate logic.

The sip.start_assist service path (targets, permissions, PIN over DTMF, IVR
hand-over) runs on a real Home Assistant in test_services.py.
"""

from __future__ import annotations

import asyncio

from .common import _load_component_module

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
