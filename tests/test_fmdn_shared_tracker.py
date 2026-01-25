"""
Tests for FMDN shared tracker support (same physical device in multiple Google accounts).

This test module verifies the fix for the shared tracker collision bug where:
- A physical tracker (e.g., Moto Tag) shared between multiple Google accounts
- Would collide into a single metadevice because canonical_id was used as primary key
- Instead of device_id (HA Device Registry ID) which is unique per account

BUG SCENARIO BEFORE FIX:
- Account A: device_id="ha_id_A", canonical_id="UUID-ABC"
- Account B: device_id="ha_id_B", canonical_id="UUID-ABC" (SAME canonical_id!)
- Both devices would produce metadevice address "fmdn:UUID-ABC"
- Result: Only ONE metadevice, wrong device_id, broken congealment

FIX:
- Prioritize device_id over canonical_id in format_metadevice_address()
- Account A: "fmdn:ha_id_A"
- Account B: "fmdn:ha_id_B"
- Result: SEPARATE metadevices, correct device congealment for BOTH accounts
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    ADDR_TYPE_FMDN_DEVICE,
    CONF_MAX_RADIUS,
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import FmdnIntegration
from custom_components.bermuda.metadevice_manager import MetadeviceManager


# Test constants for shared tracker scenario
SHARED_CANONICAL_ID = "shared-tracker-uuid-0000-1111-2222-333344445555"
ACCOUNT_A_DEVICE_ID = "account-a-ha-registry-id-aaa111"
ACCOUNT_B_DEVICE_ID = "account-b-ha-registry-id-bbb222"
ACCOUNT_A_SOURCE_MAC = "aa:aa:aa:aa:aa:aa"
ACCOUNT_B_SOURCE_MAC = "bb:bb:bb:bb:bb:bb"
TEST_SCANNER_MAC = "11:22:33:44:55:66"
TEST_AREA_NAME = "Living Room"
TEST_FLOOR_NAME = "Ground Floor"


@pytest.fixture
def fmdn_service_data() -> Mapping[str | int, Any]:
    """Create valid FMDN service data for advertisement."""
    eid_bytes = bytes([0x40]) + b"\x12\x34\x56\x78" * 5  # 20 bytes EID
    return {SERVICE_UUID_FMDN: eid_bytes}


@pytest.fixture
def lightweight_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a lightweight coordinator for unit tests."""
    floor_registry = fr.async_get(hass)
    area_registry = ar.async_get(hass)

    floor = floor_registry.async_create(TEST_FLOOR_NAME, level=0)
    area = area_registry.async_create(TEST_AREA_NAME)
    area_registry.async_update(area.id, floor_id=floor.floor_id)

    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {CONF_MAX_RADIUS: 20.0}
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator.device_ukfs = {}
    coordinator._correlations_loaded = True
    coordinator._last_correlation_save = 0.0
    coordinator.correlation_store = MagicMock(async_save=MagicMock())
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._do_private_device_init = False
    coordinator._do_fmdn_device_init = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.metadevice_manager = MetadeviceManager(coordinator)
    coordinator.er = er.async_get(hass)
    coordinator.dr = dr.async_get(hass)
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)

    scanner = coordinator._get_or_create_device(TEST_SCANNER_MAC)
    scanner._is_scanner = True
    scanner.area_id = area.id
    scanner.area_name = area.name
    scanner.area = area
    scanner.floor_id = floor.floor_id
    scanner.floor = floor
    scanner.floor_name = floor.name
    scanner.last_seen = monotonic_time_coarse()
    coordinator._scanners.add(scanner)
    coordinator._scanner_list.add(scanner.address)

    coordinator._test_area = area
    coordinator._test_floor = floor

    return coordinator


class TestSharedTrackerMetadeviceAddresses:
    """
    Test that shared trackers get SEPARATE metadevice addresses.

    This is the core fix: using device_id (unique per account) instead of
    canonical_id (shared across accounts) as the primary key.
    """

    def test_format_metadevice_address_prioritizes_device_id(
        self,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """format_metadevice_address should use device_id as primary key."""
        fmdn = lightweight_coordinator.fmdn

        # With both device_id and canonical_id provided
        address = fmdn.format_metadevice_address(
            device_id=ACCOUNT_A_DEVICE_ID,
            canonical_id=SHARED_CANONICAL_ID,
        )

        # Should use device_id, NOT canonical_id
        assert ACCOUNT_A_DEVICE_ID.lower() in address.lower()
        assert SHARED_CANONICAL_ID.lower() not in address.lower()
        assert address == f"fmdn:{ACCOUNT_A_DEVICE_ID}".lower()

    def test_shared_tracker_produces_different_addresses(
        self,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """Same canonical_id with different device_ids should produce different addresses."""
        fmdn = lightweight_coordinator.fmdn

        address_a = fmdn.format_metadevice_address(
            device_id=ACCOUNT_A_DEVICE_ID,
            canonical_id=SHARED_CANONICAL_ID,
        )
        address_b = fmdn.format_metadevice_address(
            device_id=ACCOUNT_B_DEVICE_ID,
            canonical_id=SHARED_CANONICAL_ID,  # SAME canonical_id!
        )

        # CRITICAL: Addresses MUST be different for shared trackers
        assert address_a != address_b, (
            f"SHARED TRACKER BUG: Same address '{address_a}' for different accounts! "
            f"This would cause Account B to overwrite Account A's metadevice."
        )

    def test_canonical_id_fallback_when_no_device_id(
        self,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """Should fall back to canonical_id only when device_id is unavailable."""
        fmdn = lightweight_coordinator.fmdn

        address = fmdn.format_metadevice_address(
            device_id=None,
            canonical_id=SHARED_CANONICAL_ID,
        )

        # Should use canonical_id as fallback
        assert SHARED_CANONICAL_ID.lower() in address.lower()


class TestSharedTrackerCacheLookup:
    """
    Test that cache lookup prioritizes device_id to prevent shared tracker collisions.
    """

    def test_cache_lookup_prioritizes_device_id(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """Cache lookup should find the correct metadevice by device_id, not canonical_id."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        # Create mock matches for two accounts with SAME canonical_id
        match_a = SimpleNamespace(
            device_id=ACCOUNT_A_DEVICE_ID,
            canonical_id=SHARED_CANONICAL_ID,
        )
        match_b = SimpleNamespace(
            device_id=ACCOUNT_B_DEVICE_ID,
            canonical_id=SHARED_CANONICAL_ID,  # SAME!
        )

        # Set up resolver to return different matches
        resolver = MagicMock()
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Process first account
        resolver.resolve_eid.return_value = match_a
        resolver.resolve_eid_all.return_value = [match_a]
        source_a = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_a, fmdn_service_data)

        # Process second account
        resolver.resolve_eid.return_value = match_b
        resolver.resolve_eid_all.return_value = [match_b]
        source_b = coordinator._get_or_create_device(ACCOUNT_B_SOURCE_MAC)
        fmdn.handle_advertisement(source_b, fmdn_service_data)

        # Get expected addresses
        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        # CRITICAL: Both metadevices should exist
        assert address_a in coordinator.metadevices, "Account A metadevice missing!"
        assert address_b in coordinator.metadevices, "Account B metadevice missing!"
        assert address_a != address_b, "Addresses should be different!"

    def test_cache_returns_correct_metadevice_for_each_account(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """Each account should get its own metadevice with correct fmdn_device_id."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        match_a = SimpleNamespace(device_id=ACCOUNT_A_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)
        match_b = SimpleNamespace(device_id=ACCOUNT_B_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)

        resolver = MagicMock()
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Process both accounts
        resolver.resolve_eid.return_value = match_a
        resolver.resolve_eid_all.return_value = [match_a]
        source_a = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_a, fmdn_service_data)

        resolver.resolve_eid.return_value = match_b
        resolver.resolve_eid_all.return_value = [match_b]
        source_b = coordinator._get_or_create_device(ACCOUNT_B_SOURCE_MAC)
        fmdn.handle_advertisement(source_b, fmdn_service_data)

        # Get metadevices
        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        metadevice_a = coordinator.metadevices[address_a]
        metadevice_b = coordinator.metadevices[address_b]

        # CRITICAL: Each metadevice should have the correct fmdn_device_id
        assert metadevice_a.fmdn_device_id == ACCOUNT_A_DEVICE_ID, (
            f"Account A metadevice has wrong device_id: {metadevice_a.fmdn_device_id}"
        )
        assert metadevice_b.fmdn_device_id == ACCOUNT_B_DEVICE_ID, (
            f"Account B metadevice has wrong device_id: {metadevice_b.fmdn_device_id}"
        )


class TestSharedTrackerConfigFlowVisibility:
    """
    Test that BOTH shared tracker metadevices appear in coordinator.devices.

    This is critical for config flow visibility - it iterates coordinator.devices.
    """

    def test_both_shared_trackers_in_coordinator_devices(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """Both shared tracker metadevices should be in coordinator.devices."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        match_a = SimpleNamespace(device_id=ACCOUNT_A_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)
        match_b = SimpleNamespace(device_id=ACCOUNT_B_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)

        resolver = MagicMock()
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Process both accounts
        resolver.resolve_eid.return_value = match_a
        resolver.resolve_eid_all.return_value = [match_a]
        source_a = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_a, fmdn_service_data)

        resolver.resolve_eid.return_value = match_b
        resolver.resolve_eid_all.return_value = [match_b]
        source_b = coordinator._get_or_create_device(ACCOUNT_B_SOURCE_MAC)
        fmdn.handle_advertisement(source_b, fmdn_service_data)

        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        # CRITICAL: Both must be in coordinator.devices for config flow visibility
        assert address_a in coordinator.devices, (
            f"Account A metadevice '{address_a}' NOT in coordinator.devices! "
            "Config flow iterates coordinator.devices - this device won't appear in UI."
        )
        assert address_b in coordinator.devices, (
            f"Account B metadevice '{address_b}' NOT in coordinator.devices! "
            "Config flow iterates coordinator.devices - this device won't appear in UI."
        )


class TestSharedTrackerSourceLinking:
    """
    Test that source devices are correctly linked to their respective metadevices.
    """

    def test_sources_linked_to_correct_metadevices(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """Each source should be linked to the correct account's metadevice."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        match_a = SimpleNamespace(device_id=ACCOUNT_A_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)
        match_b = SimpleNamespace(device_id=ACCOUNT_B_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)

        resolver = MagicMock()
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Process both accounts with different source MACs
        resolver.resolve_eid.return_value = match_a
        resolver.resolve_eid_all.return_value = [match_a]
        source_a = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_a, fmdn_service_data)

        resolver.resolve_eid.return_value = match_b
        resolver.resolve_eid_all.return_value = [match_b]
        source_b = coordinator._get_or_create_device(ACCOUNT_B_SOURCE_MAC)
        fmdn.handle_advertisement(source_b, fmdn_service_data)

        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        metadevice_a = coordinator.metadevices[address_a]
        metadevice_b = coordinator.metadevices[address_b]

        # Verify source linkage
        assert source_a.address in metadevice_a.metadevice_sources, (
            f"Source A '{source_a.address}' not linked to Account A metadevice"
        )
        assert source_b.address in metadevice_b.metadevice_sources, (
            f"Source B '{source_b.address}' not linked to Account B metadevice"
        )


class TestSharedTrackerMetadeviceAttributes:
    """
    Test that shared tracker metadevices have all required attributes.
    """

    def test_both_metadevices_have_required_attributes(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """Both metadevices should have create_sensor=True and correct types."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        match_a = SimpleNamespace(device_id=ACCOUNT_A_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)
        match_b = SimpleNamespace(device_id=ACCOUNT_B_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)

        resolver = MagicMock()
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        resolver.resolve_eid.return_value = match_a
        resolver.resolve_eid_all.return_value = [match_a]
        source_a = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_a, fmdn_service_data)

        resolver.resolve_eid.return_value = match_b
        resolver.resolve_eid_all.return_value = [match_b]
        source_b = coordinator._get_or_create_device(ACCOUNT_B_SOURCE_MAC)
        fmdn.handle_advertisement(source_b, fmdn_service_data)

        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        for address, label in [(address_a, "Account A"), (address_b, "Account B")]:
            metadevice = coordinator.metadevices[address]
            assert metadevice.create_sensor is True, f"{label} metadevice: create_sensor should be True"
            assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE, f"{label} metadevice: wrong address_type"
            assert METADEVICE_FMDN_DEVICE in metadevice.metadevice_type, f"{label} metadevice: missing metadevice_type"
            assert metadevice.fmdn_canonical_id == SHARED_CANONICAL_ID, f"{label} metadevice: wrong canonical_id"


class TestResolveEidAllWithSharedTrackers:
    """
    Test resolve_eid_all returns multiple matches for shared trackers.

    This tests the scenario where GoogleFindMy-HA's resolve_eid_all method
    returns multiple matches because the same physical tracker is registered
    in multiple Google accounts.
    """

    def test_resolve_eid_all_creates_multiple_metadevices(
        self,
        hass: HomeAssistant,
        lightweight_coordinator: BermudaDataUpdateCoordinator,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """resolve_eid_all returning multiple matches should create multiple metadevices."""
        coordinator = lightweight_coordinator
        fmdn = coordinator.fmdn

        # Both matches returned from single EID resolution (shared physical tracker)
        match_a = SimpleNamespace(device_id=ACCOUNT_A_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)
        match_b = SimpleNamespace(device_id=ACCOUNT_B_DEVICE_ID, canonical_id=SHARED_CANONICAL_ID)

        resolver = MagicMock()
        # resolve_eid_all returns BOTH matches for the same EID
        resolver.resolve_eid_all.return_value = [match_a, match_b]
        resolver.resolve_eid.return_value = match_a  # Fallback returns first match
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Single source device broadcasting EID
        source_device = coordinator._get_or_create_device(ACCOUNT_A_SOURCE_MAC)
        fmdn.handle_advertisement(source_device, fmdn_service_data)

        address_a = fmdn.format_metadevice_address(ACCOUNT_A_DEVICE_ID, SHARED_CANONICAL_ID)
        address_b = fmdn.format_metadevice_address(ACCOUNT_B_DEVICE_ID, SHARED_CANONICAL_ID)

        # CRITICAL: Both metadevices should be created from the single advertisement
        assert address_a in coordinator.metadevices, "Account A metadevice should be created"
        assert address_b in coordinator.metadevices, "Account B metadevice should be created"

        # Both should be in coordinator.devices for config flow visibility
        assert address_a in coordinator.devices, "Account A metadevice should be in devices"
        assert address_b in coordinator.devices, "Account B metadevice should be in devices"

        # The single source should be linked to BOTH metadevices
        metadevice_a = coordinator.metadevices[address_a]
        metadevice_b = coordinator.metadevices[address_b]

        assert source_device.address in metadevice_a.metadevice_sources, (
            "Source should be linked to Account A metadevice"
        )
        assert source_device.address in metadevice_b.metadevice_sources, (
            "Source should be linked to Account B metadevice"
        )
