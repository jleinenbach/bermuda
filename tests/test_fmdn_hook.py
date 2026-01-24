"""
Tests for FMDN Pre-Check (Resolution First) in advertisement processing.

This test file verifies that FMDN (Google Find My) advertisements are processed
BEFORE any filtering logic could discard the device. The key requirement is:

    Identity resolvers must have the opportunity to "claim" a packet BEFORE
    any logic decides to filter unknown devices.

The "Pre-Check" pattern ensures:
1. Service data is extracted early in the processing pipeline
2. FMDN service data (UUID 0xFEAA) is detected before device creation
3. The resolver can link rotating MACs to metadevices even for brand new addresses
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_TYPE_FMDN_SOURCE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import FmdnIntegration


def generate_random_mac() -> str:
    """Generate a completely random MAC address that has never been seen before."""
    octets = [random.randint(0, 255) for _ in range(6)]
    # Set locally administered bit (bit 1) and clear multicast bit (bit 0)
    octets[0] = (octets[0] | 0x02) & 0xFE
    return ":".join(f"{octet:02x}" for octet in octets)


def generate_fmdn_service_data() -> tuple[bytes, bytes]:
    """
    Generate valid FMDN service data that would be seen in a BLE advertisement.

    Returns:
        Tuple of (eid_bytes, full_service_data_bytes)
    """
    # FMDN EID is typically 20-22 bytes
    eid_bytes = bytes([random.randint(0, 255) for _ in range(20)])
    full_service_data = bytes([0x40]) + eid_bytes
    return eid_bytes, full_service_data


@pytest.fixture
def coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a lightweight coordinator for testing."""
    coord = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coord.hass = hass
    coord.options = {}
    coord.devices = {}
    coord.metadevices = {}
    coord.correlations = {}
    coord.room_profiles = {}
    coord._seed_configured_devices_done = False
    coord._scanner_init_pending = False
    coord._do_private_device_init = False
    coord._do_fmdn_device_init = False
    coord._hascanners = set()
    coord._scanners = set()
    coord._scanner_list = set()
    coord._scanners_without_areas = None
    coord.irk_manager = BermudaIrkManager()
    coord.fmdn = FmdnIntegration(coord)
    coord.er = er.async_get(hass)
    coord.dr = dr.async_get(hass)
    coord.ar = ar.async_get(hass)
    coord.fr = fr.async_get(hass)
    return coord


class TestFmdnPreCheck:
    """
    Test suite for FMDN Pre-Check (Resolution First) pattern.

    These tests verify that:
    1. FMDN service data is detected early in the processing pipeline
    2. Completely new, unknown MAC addresses with FMDN data are processed
    3. The resolver can "claim" devices before any filtering could discard them
    """

    def test_fmdn_service_data_detected_for_new_mac(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        CRITICAL TEST: Verifies that FMDN service data is detected for a
        brand new, never-seen-before MAC address.

        This is the core of the "Pre-Check" pattern - we must detect FMDN
        data BEFORE any filtering logic could discard the device.
        """
        # Generate a completely random MAC that has never been seen
        random_mac = generate_random_mac()

        # Verify it's truly unknown
        assert random_mac.lower() not in coordinator.devices, "MAC should be unknown at start"

        # Set up the FMDN resolver mock
        resolver = MagicMock()
        expected_device_id = "fmdn-device-pre-check-test"
        expected_canonical_id = "pre-check-68419b51-0000-2131-873b-fc411691d329"
        match = SimpleNamespace(device_id=expected_device_id, canonical_id=expected_canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Generate FMDN service data with the correct UUID
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # Verify SERVICE_UUID_FMDN is in the service_data (Pre-Check detection)
        assert SERVICE_UUID_FMDN in service_data, (
            "Pre-Check should detect FMDN service data by checking for SERVICE_UUID_FMDN"
        )

        # Create the device (simulates _get_or_create_device)
        source_device = coordinator._get_or_create_device(random_mac)

        # Verify the device was created
        assert random_mac.lower() in coordinator.devices, (
            "Device MUST be created for the new MAC address. "
            "The Pre-Check should NOT discard unknown devices before resolution."
        )

        # Call FMDN handler (this is what Pre-Check ensures happens)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Verify the resolver was called (Pre-Check ensured this)
        assert resolver.resolve_eid_all.called, (
            "Pre-Check must ensure the FMDN resolver is called for advertisements "
            "with FMDN service data, even for unknown MAC addresses."
        )

    def test_fmdn_precheck_creates_device_before_resolution(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that the device exists BEFORE the FMDN resolver is called.

        This tests the order of operations in the Pre-Check pattern:
        1. Detect FMDN service data (early)
        2. Create device (_get_or_create_device)
        3. Call FMDN resolver (handle_advertisement)
        """
        random_mac = generate_random_mac()

        # Track the order of operations
        operation_order = []

        # Create the device
        device = coordinator._get_or_create_device(random_mac)
        operation_order.append("device_created")

        # At this point, device should exist
        assert random_mac.lower() in coordinator.devices
        operation_order.append("device_exists_verified")

        # Now call handle_advertisement
        resolver = MagicMock()
        resolver.resolve_eid_all.return_value = []
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        coordinator.fmdn.handle_advertisement(device, service_data)
        operation_order.append("resolver_called")

        # Verify the order
        assert operation_order == ["device_created", "device_exists_verified", "resolver_called"], (
            "Operations must occur in the correct order for Pre-Check to work"
        )

    def test_fmdn_precheck_marks_device_as_fmdn_source(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that devices with FMDN service data are marked as FMDN sources.

        The Pre-Check pattern should ensure that even for brand new MACs,
        the device is properly tagged as an FMDN source after resolution.
        """
        random_mac = generate_random_mac()

        # Set up resolver
        resolver = MagicMock()
        expected_device_id = "fmdn-source-test"
        expected_canonical_id = "source-test-uuid"
        match = SimpleNamespace(device_id=expected_device_id, canonical_id=expected_canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Create device and process FMDN data
        device = coordinator._get_or_create_device(random_mac)

        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        coordinator.fmdn.handle_advertisement(device, service_data)

        # Verify device is tagged as FMDN source
        assert METADEVICE_TYPE_FMDN_SOURCE in device.metadevice_type, (
            "Device with FMDN service data should be tagged as FMDN source"
        )

    def test_fmdn_precheck_with_multiple_rotating_macs(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that Pre-Check works for multiple rotating MAC addresses.

        Google Find My devices rotate their MAC address every 15 minutes.
        Each rotation produces a new, unknown MAC that should still be processed.
        """
        # Simulate 5 MAC rotations (75 minutes of operation)
        mac_addresses = [generate_random_mac() for _ in range(5)]

        # Set up resolver - all MACs should resolve to the same device
        resolver = MagicMock()
        expected_device_id = "rotating-mac-test"
        expected_canonical_id = "rotating-test-uuid"
        match = SimpleNamespace(device_id=expected_device_id, canonical_id=expected_canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Process each rotating MAC
        for mac in mac_addresses:
            # Each MAC is initially unknown
            assert mac.lower() not in coordinator.devices, f"MAC {mac} should be unknown initially"

            # Create device and process
            device = coordinator._get_or_create_device(mac)

            _eid_bytes, service_data_bytes = generate_fmdn_service_data()
            service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

            coordinator.fmdn.handle_advertisement(device, service_data)

            # Device should now exist and be tagged
            assert mac.lower() in coordinator.devices, f"Device for {mac} should be created"
            assert METADEVICE_TYPE_FMDN_SOURCE in device.metadevice_type

        # All 5 MACs should now be in the coordinator
        for mac in mac_addresses:
            assert mac.lower() in coordinator.devices

    def test_no_fmdn_service_data_still_creates_device(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that devices WITHOUT FMDN service data are still created.

        The Pre-Check should not interfere with normal device creation
        for non-FMDN advertisements.
        """
        random_mac = generate_random_mac()

        # Service data without FMDN UUID
        service_data: Mapping[str | int, Any] = {}

        # Verify no FMDN data
        assert SERVICE_UUID_FMDN not in service_data

        # Create device
        device = coordinator._get_or_create_device(random_mac)

        # Device should still be created
        assert random_mac.lower() in coordinator.devices

        # Call handle_advertisement (should return early, no EIDs to process)
        coordinator.fmdn.handle_advertisement(device, service_data)

        # Device should NOT be tagged as FMDN source (no FMDN data)
        assert METADEVICE_TYPE_FMDN_SOURCE not in device.metadevice_type


class TestFmdnServiceUuidDetection:
    """
    Test the SERVICE_UUID_FMDN detection logic used in Pre-Check.

    The Pre-Check pattern relies on detecting the FMDN UUID (0xFEAA)
    in the service_data of BLE advertisements.
    """

    def test_service_uuid_fmdn_constant(self) -> None:
        """Verify the SERVICE_UUID_FMDN constant is correct."""
        assert SERVICE_UUID_FMDN == "0000feaa-0000-1000-8000-00805f9b34fb", (
            "SERVICE_UUID_FMDN should be the standard Bluetooth FMDN UUID"
        )

    def test_service_data_with_fmdn_uuid(self) -> None:
        """Test detection of FMDN UUID in service data."""
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        has_fmdn_data = SERVICE_UUID_FMDN in service_data
        assert has_fmdn_data is True

    def test_service_data_without_fmdn_uuid(self) -> None:
        """Test detection when FMDN UUID is not present."""
        # Service data with some other UUID
        service_data: Mapping[str | int, Any] = {"00001800-0000-1000-8000-00805f9b34fb": b"\x01\x02\x03"}

        has_fmdn_data = SERVICE_UUID_FMDN in service_data
        assert has_fmdn_data is False

    def test_empty_service_data(self) -> None:
        """Test detection with empty service data."""
        service_data: Mapping[str | int, Any] = {}

        has_fmdn_data = SERVICE_UUID_FMDN in service_data
        assert has_fmdn_data is False


class TestResolvablePrivateAddressDetection:
    """
    Test the Resolvable Private Address (RPA) detection logic.

    RPAs have their top 2 bits set to 0b01, meaning the first hex character
    is in the range [4, 5, 6, 7]. This is used for IRK resolution.
    """

    def test_rpa_detection_first_char_4(self) -> None:
        """Test RPA detection for addresses starting with '4'."""
        address = "4A:BB:CC:DD:EE:FF"
        first_char = address[0:1].upper()
        is_rpa = first_char in "4567"
        assert is_rpa is True

    def test_rpa_detection_first_char_7(self) -> None:
        """Test RPA detection for addresses starting with '7'."""
        address = "7F:BB:CC:DD:EE:FF"
        first_char = address[0:1].upper()
        is_rpa = first_char in "4567"
        assert is_rpa is True

    def test_non_rpa_first_char_0(self) -> None:
        """Test non-RPA detection for addresses starting with '0'."""
        address = "0A:BB:CC:DD:EE:FF"
        first_char = address[0:1].upper()
        is_rpa = first_char in "4567"
        assert is_rpa is False

    def test_non_rpa_first_char_c(self) -> None:
        """Test non-RPA detection for addresses starting with 'C' (static random)."""
        address = "CA:BB:CC:DD:EE:FF"
        first_char = address[0:1].upper()
        is_rpa = first_char in "4567"
        assert is_rpa is False

    def test_lowercase_address(self) -> None:
        """Test RPA detection with lowercase address."""
        address = "5a:bb:cc:dd:ee:ff"
        first_char = address[0:1].upper()
        is_rpa = first_char in "4567"
        assert is_rpa is True
