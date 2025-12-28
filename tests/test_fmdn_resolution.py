from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.core import HomeAssistant

from custom_components.bermuda.bermuda_fmdn_manager import BermudaFmdnManager
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    DATA_EID_RESOLVER,
    DEFAULT_FMDN_EID_FORMAT,
    DOMAIN_GOOGLEFINDMY,
    FMDN_EID_FORMAT_AUTO,
    FMDN_EID_FORMAT_STRIP_FRAME_ALL,
    FMDN_EID_FORMAT_STRIP_FRAME_20,
    METADEVICE_TYPE_FMDN_SOURCE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import extract_fmdn_eids
from custom_components.bermuda.util import normalize_address, normalize_mac


@pytest.fixture
def coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a lightweight coordinator for testing."""

    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {}
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn_manager = BermudaFmdnManager()
    coordinator.er = er.async_get(hass)
    coordinator.dr = dr.async_get(hass)
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    return coordinator


def test_format_fmdn_metadevice_key_stable(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure FMDN metadevice keys use device_id for stability.

    Previously used canonical_id which caused duplicate entities after reboots
    when execution order changed between _register_fmdn_source() and
    discover_fmdn_metadevices(). Now always uses device_id (HA Device Registry ID).
    """
    # device_id is always used, canonical_id is ignored for address generation
    key = coordinator._format_fmdn_metadevice_address("DEVICE-ID", "CANONICAL-01")
    assert key == "fmdn:device-id"
    assert key.startswith("fmdn:")

    # Even without canonical_id, the device_id is used
    fallback_key = coordinator._format_fmdn_metadevice_address("Device-Only", None)
    assert fallback_key == "fmdn:device-only"


def test_fmdn_resolution_registers_metadevice(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Resolve an FMDN frame and register the rotating source."""

    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-id", canonical_id="canon-1")
    resolver.resolve_eid.return_value = match
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x01" * 20}

    source_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    coordinator._handle_fmdn_advertisement(source_device, service_data)

    resolver.resolve_eid.assert_called_once_with(b"\x01" * 20)

    metadevice_key = coordinator._format_fmdn_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]
    assert metadevice.create_sensor is True  # FMDN devices auto-create sensors
    assert metadevice.fmdn_device_id == match.device_id
    assert normalize_mac("aa:bb:cc:dd:ee:ff") in metadevice.metadevice_sources

    created_source = coordinator._get_device("aa:bb:cc:dd:ee:ff")
    assert created_source is not None
    assert METADEVICE_TYPE_FMDN_SOURCE in created_source.metadevice_type


def test_fmdn_resolution_without_googlefindmy(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ignore FMDN adverts when the resolver integration is absent."""

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x02" * 20}

    source_device = coordinator._get_or_create_device("11:22:33:44:55:66")
    coordinator._handle_fmdn_advertisement(source_device, service_data)

    assert coordinator.metadevices == {}
    assert DOMAIN_GOOGLEFINDMY not in hass.data
    assert METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type


def test_fmdn_resolution_handles_missing_resolver_api(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Return None when a resolver object lacks the expected API."""

    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: object()}
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x03" * 20}

    source_device = coordinator._get_or_create_device("22:33:44:55:66:77")
    coordinator._handle_fmdn_advertisement(source_device, service_data)

    assert coordinator.metadevices == {}


def test_extract_fmdn_eid_ignores_unknown_frame_types() -> None:
    """Return candidates even when frame types are unexpected."""

    payload = bytes([0x41]) + b"\x04" * 20
    candidates = extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=DEFAULT_FMDN_EID_FORMAT)
    assert bytes([0x04] * 20) in candidates


def test_extract_fmdn_eid_supports_strip_frame_all() -> None:
    """Return the full payload after removing the frame byte."""

    payload = bytes([0x40]) + b"\x05\x06\x07"
    assert extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=FMDN_EID_FORMAT_STRIP_FRAME_ALL) == {b"\x05\x06\x07"}


def test_extract_fmdn_eid_auto_trims_checksum_byte() -> None:
    """Drop a trailing checksum-like byte when the payload length matches 21 bytes after the frame."""

    payload = bytes([0x40]) + b"\x08" * 21
    candidates = extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=FMDN_EID_FORMAT_AUTO)
    assert b"\x08" * 20 in candidates


def test_extract_fmdn_eid_auto_falls_back_to_twenty_bytes() -> None:
    """Return the first 20 bytes when the payload is exactly 20 bytes after the frame."""

    payload = bytes([0x40]) + bytes(range(1, 21))
    candidates = extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=FMDN_EID_FORMAT_AUTO)
    assert bytes(range(1, 21)) in candidates


def test_extract_fmdn_eid_rejects_short_payload() -> None:
    """Return None for payloads without enough data after the frame byte."""

    assert extract_fmdn_eids({SERVICE_UUID_FMDN: b"\x40"}, mode=FMDN_EID_FORMAT_STRIP_FRAME_20) == set()


def test_normalize_address_collapses_duplicate_formats(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure coordinator keys devices by canonical MAC addresses."""

    first = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    second = coordinator._get_or_create_device("AA-BB-CC-DD-EE-FF")

    assert first is second
    assert first.address == "aa:bb:cc:dd:ee:ff"
    assert normalize_address("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


def test_extract_fmdn_eids_handles_embedded_lengths() -> None:
    """Generate candidates for embedded 20- and 32-byte payloads."""

    eid20 = bytes(range(1, 21))
    eid32 = bytes(range(1, 33))
    payload = b"\x40" + b"\xaa\xbb" + eid20 + b"\xcc" + eid32 + b"\xdd"

    candidates = extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=FMDN_EID_FORMAT_AUTO)

    assert eid20 in candidates
    assert eid32 in candidates


def test_extract_fmdn_eids_sliding_window_without_frame() -> None:
    """Ensure sliding window detection finds EIDs even without frame byte."""

    eid20 = b"\x12" * 20
    payload = b"\x99" + eid20 + b"\x00\x01"

    candidates = extract_fmdn_eids({SERVICE_UUID_FMDN: payload}, mode=FMDN_EID_FORMAT_AUTO)
    assert eid20 in candidates


def test_shared_match_without_identifiers_skipped(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Shared matches lacking identifiers should not create metadevices."""

    resolver = MagicMock()
    resolver.resolve_eid.return_value = SimpleNamespace(shared=True, device_id=None, canonical_id=None)
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    source_device = coordinator._get_or_create_device("33:44:55:66:77:88")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x09" * 20}

    coordinator._handle_fmdn_advertisement(source_device, service_data)

    assert coordinator.metadevices == {}
    assert METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type


def test_deduplicates_metadevices_by_device_id(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Ensure multiple sources map to the same metadevice via device_id.

    The metadevice address is now based solely on device_id (HA Device Registry ID)
    for stability across reboots, regardless of canonical_id format variations.
    """
    resolver = MagicMock()

    def _resolver(payload: bytes) -> SimpleNamespace:
        return SimpleNamespace(device_id="owned", canonical_id="shared-uuid")

    resolver.resolve_eid.side_effect = _resolver
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    first_source = coordinator._get_or_create_device("00:11:22:33:44:55")
    second_source = coordinator._get_or_create_device("00:11:22:33:44:56")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\xaa" * 20}

    coordinator._handle_fmdn_advertisement(first_source, service_data)
    coordinator._handle_fmdn_advertisement(second_source, service_data)

    assert len(coordinator.metadevices) == 1
    metadevice = next(iter(coordinator.metadevices.values()))
    assert set(metadevice.metadevice_sources) == {first_source.address, second_source.address}
