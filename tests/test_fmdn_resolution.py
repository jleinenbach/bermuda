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

from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
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
    coordinator.correlations = {}  # Scanner correlation data for area confidence
    coordinator.room_profiles = {}  # Room-level scanner pair delta profiles
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.er = er.async_get(hass)
    coordinator.dr = dr.async_get(hass)
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    return coordinator


def test_format_fmdn_metadevice_key_stable(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure FMDN metadevice keys prefer device_id for account uniqueness.

    Per Lesson #61: device_id (HA Device Registry ID) is now the primary identifier
    because it's unique per account. canonical_id (shared across accounts) is only
    used as a fallback when device_id is unavailable. This prevents shared tracker
    collisions when the same physical tracker is shared between multiple Google accounts.
    """
    # device_id is preferred when available (per Lesson #61)
    key = coordinator.fmdn.format_metadevice_address("device-id", "CANONICAL-01")
    assert key == "fmdn:device-id"
    assert key.startswith("fmdn:")

    # Fallback to canonical_id when device_id is unavailable
    fallback_key = coordinator.fmdn.format_metadevice_address(None, "Canonical-Only")
    assert fallback_key == "fmdn:canonical-only"

    # device_id takes priority even when canonical_id looks like a proper UUID
    priority_key = coordinator.fmdn.format_metadevice_address("simple-id", "68e69eca-0000-1111-2222-333344445555")
    assert priority_key == "fmdn:simple-id"


def test_fmdn_resolution_registers_metadevice(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Resolve an FMDN frame and register the rotating source."""

    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-id", canonical_id="canon-1")
    # Code uses resolve_eid_all first (returns list of matches for shared trackers)
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x01" * 20}

    source_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    resolver.resolve_eid_all.assert_called_once_with(b"\x01" * 20)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
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
    coordinator.fmdn.handle_advertisement(source_device, service_data)

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
    coordinator.fmdn.handle_advertisement(source_device, service_data)

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


def test_match_without_device_id_skipped(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Matches lacking device_id should not create metadevices.

    The GoogleFindMy-HA EIDMatch always includes device_id when valid,
    so a None value indicates an invalid or incomplete match.
    """
    resolver = MagicMock()
    # Simulate a match without device_id (invalid/incomplete match)
    resolver.resolve_eid_all.return_value = [SimpleNamespace(device_id=None, canonical_id=None)]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    source_device = coordinator._get_or_create_device("33:44:55:66:77:88")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x09" * 20}

    coordinator.fmdn.handle_advertisement(source_device, service_data)

    assert coordinator.metadevices == {}
    assert METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type


def test_deduplicates_metadevices_by_device_id(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure multiple sources map to the same metadevice via device_id.

    The metadevice address is now based solely on device_id (HA Device Registry ID)
    for stability across reboots, regardless of canonical_id format variations.
    """
    resolver = MagicMock()

    def _resolver(payload: bytes) -> list[SimpleNamespace]:
        return [SimpleNamespace(device_id="owned", canonical_id="shared-uuid")]

    resolver.resolve_eid_all.side_effect = _resolver
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    first_source = coordinator._get_or_create_device("00:11:22:33:44:55")
    second_source = coordinator._get_or_create_device("00:11:22:33:44:56")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\xaa" * 20}

    coordinator.fmdn.handle_advertisement(first_source, service_data)
    coordinator.fmdn.handle_advertisement(second_source, service_data)

    assert len(coordinator.metadevices) == 1
    metadevice = next(iter(coordinator.metadevices.values()))
    assert set(metadevice.metadevice_sources) == {first_source.address, second_source.address}


def test_shared_tracker_creates_metadevices_for_all_accounts(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Ensure shared trackers create metadevices for ALL matching accounts.

    When a physical FMDN tracker (e.g., Moto Tag) is shared between multiple
    Google accounts, resolve_eid_all returns all matching devices. Bermuda
    should create sensors for each account's device, not just the first one.
    """
    resolver = MagicMock()

    # Simulate shared tracker: same EID resolves to devices in two different accounts
    account1_match = SimpleNamespace(device_id="account1-device-id", canonical_id="entry1:tracker1")
    account2_match = SimpleNamespace(device_id="account2-device-id", canonical_id="entry2:tracker2")

    # resolve_eid_all returns ALL matches for shared trackers
    resolver.resolve_eid_all.return_value = [account1_match, account2_match]
    # resolve_eid should not be called when resolve_eid_all is available
    resolver.resolve_eid.return_value = account1_match

    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    source_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x01" * 20}

    coordinator.fmdn.handle_advertisement(source_device, service_data)

    # Should use resolve_eid_all, not resolve_eid
    resolver.resolve_eid_all.assert_called_once_with(b"\x01" * 20)
    resolver.resolve_eid.assert_not_called()

    # Should create metadevices for BOTH accounts
    assert len(coordinator.metadevices) == 2

    # Verify both metadevices exist with correct device IDs
    device_ids = {m.fmdn_device_id for m in coordinator.metadevices.values()}
    assert device_ids == {"account1-device-id", "account2-device-id"}

    # Both metadevices should have the same source (same BLE advertisement)
    for metadevice in coordinator.metadevices.values():
        assert source_device.address in metadevice.metadevice_sources
        assert metadevice.create_sensor is True


def test_shared_tracker_fallback_to_resolve_eid(hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure fallback to resolve_eid when resolve_eid_all is not available.

    For older versions of GoogleFindMy that don't have resolve_eid_all,
    Bermuda should fall back to using resolve_eid (single match behavior).
    """
    resolver = MagicMock()

    # Simulate older GoogleFindMy without resolve_eid_all
    del resolver.resolve_eid_all
    resolver.resolve_eid.return_value = SimpleNamespace(device_id="legacy-device-id", canonical_id="legacy-canonical")

    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    source_device = coordinator._get_or_create_device("11:22:33:44:55:66")
    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x02" * 20}

    coordinator.fmdn.handle_advertisement(source_device, service_data)

    # Should fall back to resolve_eid
    resolver.resolve_eid.assert_called_once_with(b"\x02" * 20)

    # Should create single metadevice (legacy behavior)
    assert len(coordinator.metadevices) == 1
    metadevice = next(iter(coordinator.metadevices.values()))
    assert metadevice.fmdn_device_id == "legacy-device-id"
