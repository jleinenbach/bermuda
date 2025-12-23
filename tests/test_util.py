"""Test util.py in Bermuda."""

from __future__ import annotations

# from homeassistant.core import HomeAssistant

from math import floor

import pytest

from custom_components.bermuda import util


def test_mac_math_offset():
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", 2) == "aa:bb:cc:dd:ee:f1"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", -3) == "aa:bb:cc:dd:ee:ec"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ff", 2) is None
    assert util.mac_math_offset("clearly_not:a-mac_address", 2) is None
    assert util.mac_math_offset(None, 4) is None


def test_normalize_mac_variants():
    assert util.normalize_mac("AA:bb:CC:88:Ff:00") == "aa:bb:cc:88:ff:00"
    assert util.normalize_mac("aa_bb_CC_dd_ee_ff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("aa-77-CC-dd-ee-ff") == "aa:77:cc:dd:ee:ff"
    assert util.normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_non_mac():
    with pytest.raises(ValueError):
        util.normalize_mac("fmdn:abc123")


def test_normalize_identifier_and_mac_dispatch():
    assert util.normalize_identifier("AABBCCDDEEFF") == "aabbccddeeff"
    assert util.normalize_identifier("12345678-1234-5678-9abc-def012345678_extra") == (
        "12345678123456789abcdef012345678_extra"
    )
    assert util.normalize_address("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_address("fmdn:Device-ID") == "fmdn:device-id"


def test_mac_explode_formats():
    ex = util.mac_explode_formats("aa:bb:cc:77:ee:ff")
    assert "aa:bb:cc:77:ee:ff" in ex
    assert "aa-bb-cc-77-ee-ff" in ex
    for e in ex:
        assert len(e) in [12, 17]


def test_mac_redact():
    assert util.mac_redact("aa:bb:cc:77:ee:ff", "tEstMe") == "aa::tEstMe::ff"
    assert util.mac_redact("howdy::doody::friend", "PLEASENOE") == "ho::PLEASENOE::nd"


def test_rssi_to_metres():
    assert floor(util.rssi_to_metres(-50, -20, 2)) == 31
    assert floor(util.rssi_to_metres(-80, -20, 2)) == 1000


def test_clean_charbuf():
    assert util.clean_charbuf("a Normal string.") == "a Normal string."
    assert util.clean_charbuf("Broken\000String\000Fixed\000\000\000") == "Broken"
