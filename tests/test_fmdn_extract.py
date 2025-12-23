"""Tests for FMDN EID extraction helpers."""

from __future__ import annotations

import pytest

from custom_components.bermuda.const import (
    FMDN_EID_FORMAT_AUTO,
    FMDN_EID_FORMAT_STRIP_FRAME_20,
    FMDN_EID_FORMAT_STRIP_FRAME_ALL,
)
from custom_components.bermuda.fmdn import extract_fmdn_eids, is_fmdn_service_uuid


def test_is_fmdn_service_uuid_variants() -> None:
    assert is_fmdn_service_uuid(0xFEAA) is True
    assert is_fmdn_service_uuid("feaa") is True
    assert is_fmdn_service_uuid("0xFEAA") is True
    assert is_fmdn_service_uuid("0000feaa") is True
    assert is_fmdn_service_uuid("0000feaa-0000-1000-8000-00805f9b34fb") is True
    assert is_fmdn_service_uuid("0000abcd-0000-1000-8000-00805f9b34fb") is False


def test_extract_strip_frame_20_returns_first_20_after_frame() -> None:
    after_frame = bytes(range(1, 33))  # 32 bytes
    payload = b"\x40" + after_frame
    service_data = {0xFEAA: payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_STRIP_FRAME_20)
    assert candidates == {after_frame[:20]}


def test_extract_strip_frame_all_includes_full_payload_and_prefixes() -> None:
    after_frame = bytes(range(1, 33))  # 32 bytes
    payload = b"\x40" + after_frame
    service_data = {"0000feaa-0000-1000-8000-00805f9b34fb": payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_STRIP_FRAME_ALL)
    assert after_frame in candidates
    assert after_frame[:20] in candidates
    assert len(candidates) == 2


def test_extract_auto_trims_checksum_and_builds_sliding_windows() -> None:
    base = b"A" * 20
    checksum = b"\x99"
    after_frame = base + checksum  # 21 bytes
    payload = b"\x40" + after_frame
    service_data = {0xFEAA: payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_AUTO)

    assert base in candidates
    assert all(len(candidate) in (20, 32) for candidate in candidates)


def test_extract_ignores_non_fmdn_service_data() -> None:
    service_data = {"0000abcd-0000-1000-8000-00805f9b34fb": b"\x40" + (b"\x01" * 21)}
    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_AUTO)
    assert candidates == set()


@pytest.mark.parametrize(
    ("mode_value", "expected_nonempty"),
    [
        (None, True),
        ("unknown_mode", True),
        (FMDN_EID_FORMAT_STRIP_FRAME_20, True),
        (FMDN_EID_FORMAT_STRIP_FRAME_ALL, True),
        (FMDN_EID_FORMAT_AUTO, True),
    ],
)
def test_mode_defaults_and_fallback(mode_value: str | None, expected_nonempty: bool) -> None:
    payload = b"\x40" + (b"\x11" * 32)
    service_data = {0xFEAA: payload}
    candidates = extract_fmdn_eids(service_data, mode=mode_value)
    assert (len(candidates) > 0) is expected_nonempty


def test_extract_accepts_32_byte_eid_only() -> None:
    eid = bytes(range(32))
    service_data = {0xFEAA: eid}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_STRIP_FRAME_20)
    assert eid in candidates


def test_extract_trims_hashed_flags_after_frame() -> None:
    eid = bytes(range(1, 21))
    hashed_flags = b"\x99"
    payload = b"\x41" + eid + hashed_flags
    service_data = {0xFEAA: payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_STRIP_FRAME_ALL)
    assert eid in candidates
    assert all(len(candidate) in (20, 32) for candidate in candidates)


def test_extract_handles_32_byte_eid_with_frame_and_flags() -> None:
    eid = bytes(range(1, 33))
    payload = b"\x40" + eid + b"\x01"
    service_data = {0xFEAA: payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_AUTO)
    assert eid in candidates


def test_extract_from_embedded_uuid_marker() -> None:
    eid = bytes(range(20))
    payload = b"\x01\x02" + b"\xAA\xFE" + b"\x40" + eid + b"\xAA"
    service_data = {0xFEAA: payload}

    candidates = extract_fmdn_eids(service_data, mode=FMDN_EID_FORMAT_AUTO)
    assert eid in candidates
