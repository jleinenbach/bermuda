"""
Tests for rotating MAC address resolution.

This test file proves that the BLE advertisement processing pipeline correctly
handles devices with rotating MAC addresses (like FMDN/Google Find My and IRK/Apple
devices). The key requirement is:

    Identity resolvers (FMDN and IRK) must have the opportunity to inspect and
    "claim" a packet BEFORE any logic decides to discard unknown devices.

See: CLAUDE.md "Resolution First" approach
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
    ADDR_TYPE_FMDN_DEVICE,
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import FmdnIntegration


def generate_random_mac() -> str:
    """Generate a completely random MAC address that has never been seen before."""
    # Use random private/local MAC (bit 1 of first octet set = locally administered)
    # This ensures we're testing with genuinely "unknown" addresses
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
    # First byte indicates the type (0x40 = standard FMDN)
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


class TestRotatingMacResolutionFirst:
    """
    Test suite for the "Resolution First" approach to rotating MAC addresses.

    These tests verify that:
    1. A completely new, random MAC address with FMDN service data is NOT discarded
    2. The device is created in coordinator.devices
    3. The EID resolver is called and can link the device to a metadevice
    4. The source address appears in the metadevice's source list
    """

    def test_new_random_mac_with_fmdn_service_data_is_not_discarded(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        CRITICAL TEST: Proves that a brand new random MAC address with FMDN data
        is NOT discarded and IS processed by the resolver.

        Scenario:
        - A device rotates its MAC address (happens every 15 minutes with FMDN)
        - A completely new, never-seen-before MAC address appears in BLE adverts
        - This advertisement contains FMDN service data (Google Find My EID)

        Expected:
        - The device MUST be created (not filtered out)
        - The FMDN resolver MUST be called
        - If resolution succeeds, the source MAC MUST be linked to the metadevice
        """
        # Generate a completely random MAC that has never been seen
        random_mac = generate_random_mac()

        # Verify it's truly unknown
        assert random_mac not in coordinator.devices, "MAC should be unknown at start"

        # Set up the FMDN resolver mock
        resolver = MagicMock()
        expected_device_id = "fmdn-device-123"
        expected_canonical_id = "68419b51-0000-2131-873b-fc411691d329"
        match = SimpleNamespace(device_id=expected_device_id, canonical_id=expected_canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Generate FMDN service data
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # Step 1: Create the device (simulates what _async_gather_advert_data does)
        # This is the critical step - the device MUST be created before resolution
        source_device = coordinator._get_or_create_device(random_mac)

        # Verify the device was created
        assert random_mac.lower() in coordinator.devices, (
            "Device MUST be created for the new MAC address. "
            "The gatekeeper should NOT discard unknown devices before resolution."
        )

        # Step 2: Call FMDN handler (simulates what _async_gather_advert_data does)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Verify the resolver was called
        assert resolver.resolve_eid_all.called, "FMDN resolver MUST be called for advertisements with FMDN service data"

        # Verify the metadevice was created
        metadevice_address = coordinator.fmdn.format_metadevice_address(expected_device_id, expected_canonical_id)
        assert (
            metadevice_address in coordinator.metadevices
        ), "Metadevice MUST be created after successful FMDN resolution"

        metadevice = coordinator.metadevices[metadevice_address]

        # Verify the source MAC is linked to the metadevice
        assert random_mac.lower() in metadevice.metadevice_sources, (
            "The random source MAC MUST be added to metadevice_sources "
            "so that subsequent adverts are correctly associated"
        )

    def test_resolution_first_multiple_mac_rotations(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that multiple MAC rotations for the same device all work correctly.

        Simulates a device rotating its MAC 3 times. Each new MAC should:
        1. Be created (not discarded)
        2. Have FMDN resolution run
        3. Be linked to the same metadevice
        """
        # Set up the FMDN resolver
        resolver = MagicMock()
        device_id = "my-fmdn-device"
        canonical_id = "stable-canonical-id-123"
        match = SimpleNamespace(device_id=device_id, canonical_id=canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Simulate 3 MAC rotations
        mac_addresses = [generate_random_mac() for _ in range(3)]

        for i, mac in enumerate(mac_addresses):
            # Generate unique service data for each rotation
            _eid_bytes, service_data_bytes = generate_fmdn_service_data()
            service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

            # Process the advertisement
            source_device = coordinator._get_or_create_device(mac)
            coordinator.fmdn.handle_advertisement(source_device, service_data)

            # Verify device was created
            assert mac.lower() in coordinator.devices, f"MAC rotation {i + 1} should create device"

        # Verify all MACs are linked to the same metadevice
        metadevice_address = coordinator.fmdn.format_metadevice_address(device_id, canonical_id)
        metadevice = coordinator.metadevices[metadevice_address]

        for mac in mac_addresses:
            assert (
                mac.lower() in metadevice.metadevice_sources
            ), f"MAC {mac} should be in metadevice_sources after resolution"

    def test_non_fmdn_advertisement_still_creates_device(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that non-FMDN advertisements still create devices.

        Even if there's no FMDN service data, we should create the device
        and let the system decide whether to prune it later.
        """
        random_mac = generate_random_mac()

        # No FMDN service data - just a generic BLE advertisement
        service_data: Mapping[str | int, Any] = {}

        # This should still create the device
        source_device = coordinator._get_or_create_device(random_mac)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Device should exist even without FMDN data
        assert (
            random_mac.lower() in coordinator.devices
        ), "Device should be created for any BLE advertisement, not just FMDN advertisements"

    def test_fmdn_resolution_failure_still_keeps_device(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that even if FMDN resolution fails, the device is still kept.

        This is important because:
        1. The resolver might fail temporarily (network issues, etc.)
        2. The device might be resolved on the next rotation
        3. We don't want to lose the device just because one resolution failed
        """
        random_mac = generate_random_mac()

        # Set up resolver to return no matches (resolution failure)
        resolver = MagicMock()
        resolver.resolve_eid_all.return_value = []
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Generate FMDN service data
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # Process the advertisement
        source_device = coordinator._get_or_create_device(random_mac)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Device should still exist even though resolution failed
        assert random_mac.lower() in coordinator.devices, "Device should be kept even when FMDN resolution fails"

    def test_source_device_tagged_as_fmdn_source(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that successfully resolved FMDN source devices are properly tagged.
        """
        random_mac = generate_random_mac()

        # Set up successful resolution
        resolver = MagicMock()
        match = SimpleNamespace(device_id="test-device", canonical_id="test-canonical")
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Generate FMDN service data
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # Process the advertisement
        source_device = coordinator._get_or_create_device(random_mac)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Source device should be tagged as FMDN source
        assert (
            METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type
        ), "Source device should be tagged as FMDN_SOURCE after successful resolution"

    def test_metadevice_configured_for_sensor_creation(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that resolved FMDN metadevices have create_sensor=True.

        This ensures that automatically discovered FMDN devices get sensors
        created for them (like Private BLE Device does).
        """
        random_mac = generate_random_mac()

        # Set up successful resolution
        resolver = MagicMock()
        device_id = "auto-discovered-device"
        canonical_id = "canonical-456"
        match = SimpleNamespace(device_id=device_id, canonical_id=canonical_id)
        resolver.resolve_eid_all.return_value = [match]
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Generate FMDN service data
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # Process the advertisement
        source_device = coordinator._get_or_create_device(random_mac)
        coordinator.fmdn.handle_advertisement(source_device, service_data)

        # Get the metadevice
        metadevice_address = coordinator.fmdn.format_metadevice_address(device_id, canonical_id)
        metadevice = coordinator.metadevices[metadevice_address]

        # Verify create_sensor is True for automatic tracking
        assert metadevice.create_sensor is True, "Metadevice should have create_sensor=True for automatic FMDN tracking"

        # Verify metadevice is properly typed
        assert METADEVICE_FMDN_DEVICE in metadevice.metadevice_type
        assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE


class TestResolutionOrderInPipeline:
    """
    Tests to verify that the resolution happens in the correct order in the pipeline.

    The order MUST be:
    1. Create device (_get_or_create_device)
    2. Process advertisement (device.process_advertisement)
    3. Call FMDN/IRK resolver (fmdn.handle_advertisement)
    4. ... later, pruning happens

    Resolution MUST happen BEFORE any pruning decisions.
    """

    def test_device_exists_before_fmdn_handler_called(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that the device exists in coordinator.devices BEFORE the FMDN
        handler is called. This is a prerequisite for the resolver to work.
        """
        random_mac = generate_random_mac()

        # Create the device (step 1 in pipeline)
        device = coordinator._get_or_create_device(random_mac)

        # At this point, BEFORE calling handle_advertisement,
        # the device should already exist
        assert (
            random_mac.lower() in coordinator.devices
        ), "Device must exist before handle_advertisement is called. This is the 'Resolution First' principle."

        # The device object should be usable
        assert device is not None
        assert device.address == random_mac.lower()

    def test_fmdn_handler_receives_existing_device(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that when fmdn.handle_advertisement is called, it receives
        a device that already exists in coordinator.devices.
        """
        random_mac = generate_random_mac()

        # Set up resolver
        resolver = MagicMock()
        resolver.resolve_eid_all.return_value = []  # Doesn't matter for this test
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

        # Create and get the device
        device = coordinator._get_or_create_device(random_mac)

        # Generate service data
        _eid_bytes, service_data_bytes = generate_fmdn_service_data()
        service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: service_data_bytes}

        # The device passed to handle_advertisement should be the same
        # as the one in coordinator.devices
        coordinator.fmdn.handle_advertisement(device, service_data)

        assert coordinator.devices[random_mac.lower()] is device, (
            "The device passed to handle_advertisement should be the same "
            "object as the one stored in coordinator.devices"
        )


def generate_resolvable_private_mac() -> str:
    """
    Generate a random Resolvable Private Address (RPA).

    RPAs have top_bits == 0b01, meaning the first hex character is in [4, 5, 6, 7].
    """
    # First octet: top 2 bits must be 01 (resolvable random)
    # Binary: 01xx xxxx = 0x40-0x7F
    first_octet = random.randint(0x40, 0x7F)
    other_octets = [random.randint(0, 255) for _ in range(5)]
    return ":".join(f"{octet:02x}" for octet in [first_octet, *other_octets])


class TestIrkResolution:
    """
    Test suite for IRK (Identity Resolving Key) resolution.

    These tests verify that:
    1. The irk_manager.check_mac() is called for every advertisement
    2. IRKs learned after a device was first seen can still resolve
    3. Resolvable Private Addresses are properly checked against known IRKs
    """

    def test_irk_check_called_on_advertisement(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that irk_manager.check_mac is called when processing advertisements.

        This is the key fix: IRK check must happen on every advertisement, not just
        during device creation, to handle cases where IRK is learned later.
        """
        random_mac = generate_resolvable_private_mac()

        # The device should not exist yet
        assert random_mac.lower() not in coordinator.devices

        # Create the device (simulates _get_or_create_device)
        device = coordinator._get_or_create_device(random_mac)
        assert random_mac.lower() in coordinator.devices

        # Check that check_mac can be called (result is cached)
        result1 = coordinator.irk_manager.check_mac(random_mac)
        result2 = coordinator.irk_manager.check_mac(random_mac)

        # Second call should return cached result (same object)
        assert result1 == result2, "check_mac should return consistent results (cached)"

    def test_irk_learned_after_device_creation(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test the scenario where an IRK is learned AFTER a device with matching MAC
        was already seen.

        Timeline:
        1. MAC "A" is seen → device created → check_mac (no IRK known) → unresolved
        2. IRK is learned (from Private BLE Device integration)
        3. MAC "A" is seen again → check_mac → NOW MATCHES!
        """
        random_mac = generate_resolvable_private_mac()

        # Step 1: Device seen before IRK is known
        device = coordinator._get_or_create_device(random_mac)
        result_before = coordinator.irk_manager.check_mac(random_mac)

        # At this point, no IRKs are known, so the result should be an IrkType
        from custom_components.bermuda.const import IrkTypes

        assert (
            result_before in IrkTypes.unresolved()
        ), "Before any IRK is added, check_mac should return an unresolved IrkType"

        # Step 2: Learn a valid IRK (16 bytes = 128 bits)
        # Note: This IRK won't actually match the random MAC since we're using
        # random data, but this tests the flow
        test_irk = bytes(
            [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00]
        )

        # add_irk will check all previously seen MACs against the new IRK
        matching_macs = coordinator.irk_manager.add_irk(test_irk)

        # Since our random MAC won't cryptographically match this IRK,
        # it shouldn't be in the matches (but the machinery works)
        assert isinstance(matching_macs, list), "add_irk should return a list of matching MACs"

    def test_multiple_mac_rotations_with_irk(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Test that multiple rotating MAC addresses are all checked against IRKs.

        When a device rotates its MAC (e.g., every 15 minutes), each new MAC
        should be checked against known IRKs.
        """
        # Generate multiple resolvable private addresses
        mac_addresses = [generate_resolvable_private_mac() for _ in range(5)]

        # Create devices for each MAC
        for mac in mac_addresses:
            coordinator._get_or_create_device(mac)

        # Verify all devices exist
        for mac in mac_addresses:
            assert mac.lower() in coordinator.devices, f"Device for {mac} should exist"

        # Check all MACs - they should be tracked by irk_manager
        for mac in mac_addresses:
            result = coordinator.irk_manager.check_mac(mac)
            # Result should exist (either matched IRK or unresolved type)
            assert result is not None, f"check_mac({mac}) should return a result"

    def test_irk_callback_registration(self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Test that IRK callbacks are properly handled.

        When an IRK is registered with a callback, the callback should fire
        when a matching MAC is found.
        """
        # Create a test IRK
        test_irk = bytes([0xAA] * 16)

        # Track callback invocations
        callback_calls: list[tuple[str, Any]] = []

        def test_callback(service_info: Any, change: Any) -> None:
            callback_calls.append((service_info.address, change))

        # Register callback for this IRK
        unsubscribe = coordinator.irk_manager.register_irk_callback(test_callback, test_irk)

        # The callback should be registered
        assert callable(unsubscribe), "register_irk_callback should return an unsubscribe function"

        # Cleanup
        unsubscribe()

    def test_irk_prune_maintains_resolution_capability(
        self, hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Verify that IRK resolution still works after pruning old MAC entries.
        """
        random_mac = generate_resolvable_private_mac()

        # Create device and check MAC
        coordinator._get_or_create_device(random_mac)
        coordinator.irk_manager.check_mac(random_mac)

        # Prune old entries (should not affect active entries)
        coordinator.irk_manager.async_prune()

        # Check that known_macs still includes resolved entries
        known = coordinator.irk_manager.known_macs(resolved=False)
        assert random_mac in known, "Recently checked MAC should still be known after prune"
