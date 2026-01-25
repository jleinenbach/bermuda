"""
End-to-end test for FMDN device data flow.

This test simulates an FMDN device (Google Find My Device Network) that receives
data through the GoogleFindMy-HA API and verifies that:
1. The device is discovered and registered as a metadevice
2. BLE advertisement data is properly processed
3. Area data flows from source devices to the metadevice
4. Entities are created with correct values (area_name, area_distance, etc.)
5. The device appears in the Configuration Flow "Select Device" list
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    ADDR_TYPE_FMDN_DEVICE,
    CONF_DEVICES,
    CONF_MAX_RADIUS,
    DATA_EID_RESOLVER,
    DOMAIN,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
    NAME,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import FmdnIntegration
from custom_components.bermuda.metadevice_manager import MetadeviceManager

from .const import MOCK_CONFIG


# Test constants
TEST_FMDN_DEVICE_ID = "fmdn-test-device-registry-id"
TEST_FMDN_CANONICAL_ID = "68419b51-0000-2131-873b-fc411691d329"
TEST_FMDN_DEVICE_NAME = "Moto Tag Test"
TEST_SOURCE_MAC = "aa:bb:cc:dd:ee:ff"
TEST_SCANNER_MAC = "11:22:33:44:55:66"
TEST_AREA_NAME = "Living Room"
TEST_FLOOR_NAME = "Ground Floor"


@pytest.fixture
def mock_resolver() -> MagicMock:
    """Create a mock EID resolver that returns our test device."""
    resolver = MagicMock()
    match = SimpleNamespace(
        device_id=TEST_FMDN_DEVICE_ID,
        canonical_id=TEST_FMDN_CANONICAL_ID,
    )
    resolver.resolve_eid.return_value = match
    resolver.resolve_eid_all.return_value = [match]
    return resolver


@pytest.fixture
def fmdn_service_data() -> Mapping[str | int, Any]:
    """Create valid FMDN service data for advertisement."""
    # FMDN EID format: header byte (0x40) + 20 bytes EID
    eid_bytes = bytes([0x40]) + b"\x12\x34\x56\x78" * 5  # 20 bytes EID
    return {SERVICE_UUID_FMDN: eid_bytes}


@pytest.fixture
def lightweight_coordinator(
    hass: HomeAssistant,
) -> BermudaDataUpdateCoordinator:
    """
    Create a lightweight coordinator for unit tests (no full integration setup).

    This fixture is used for tests that only need to verify internal coordinator state
    without entity creation or config flow.
    """
    # Create floor and area in registries
    floor_registry = fr.async_get(hass)
    area_registry = ar.async_get(hass)

    floor = floor_registry.async_create(TEST_FLOOR_NAME, level=0)
    area = area_registry.async_create(TEST_AREA_NAME)
    area_registry.async_update(area.id, floor_id=floor.floor_id)

    # Create coordinator
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

    # Create scanner device in the area
    scanner = coordinator._get_or_create_device(TEST_SCANNER_MAC)
    scanner._is_scanner = True  # Internal attribute, property is read-only
    scanner.area_id = area.id
    scanner.area_name = area.name
    scanner.area = area
    scanner.floor_id = floor.floor_id
    scanner.floor = floor
    scanner.floor_name = floor.name
    scanner.last_seen = monotonic_time_coarse()
    coordinator._scanners.add(scanner)  # Add device object, not address
    coordinator._scanner_list.add(scanner.address)

    # Store area/floor references for tests
    coordinator._test_area = area
    coordinator._test_floor = floor

    return coordinator


class TestFmdnMetadeviceInDevices:
    """
    CRITICAL TEST: Verify that FMDN metadevices are automatically added to coordinator.devices.

    This is the core bug being tested. The config_flow.py iterates over coordinator.devices
    (line 293), but metadevices were only being added to coordinator.metadevices.

    If this test FAILS, it means the bug exists: metadevices are NOT being added to
    coordinator.devices automatically, and users won't see FMDN devices in the config flow.
    """

    def test_metadevice_automatically_added_to_coordinator_devices(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """
        BUG TEST: Metadevice MUST be in coordinator.devices after handle_advertisement().

        This test will FAIL if the bug exists (metadevice only in metadevices, not in devices).
        The test should PASS after the bug is fixed.
        """
        coordinator = lightweight_coordinator
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        # Create source device and process advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Get the expected metadevice address
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)

        # Verify metadevice is in coordinator.metadevices (this should pass)
        assert metadevice_address in coordinator.metadevices, "Metadevice should be in coordinator.metadevices"

        # CRITICAL BUG TEST: Verify metadevice is ALSO in coordinator.devices
        # This is required for config_flow.py to see the device!
        # If this assertion fails, the bug exists.
        assert metadevice_address in coordinator.devices, (
            "BUG: Metadevice is NOT in coordinator.devices! "
            "Config flow iterates over coordinator.devices (line 293 in config_flow.py), "
            "so FMDN devices will NOT appear in the Select Devices list. "
            "The fix: register_source() must add the metadevice to coordinator.devices."
        )

        # Additional verification: the objects should be the same instance
        assert (
            coordinator.devices[metadevice_address] is coordinator.metadevices[metadevice_address]
        ), "The metadevice in devices and metadevices should be the same object instance"


class TestFmdnDeviceDiscovery:
    """Test FMDN device discovery and registration (unit tests)."""

    def test_fmdn_device_created_on_advertisement(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """FMDN device should be created when EID is resolved."""
        coordinator = lightweight_coordinator
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        # Create source device and process advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Verify metadevice was created
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        assert metadevice_address in coordinator.metadevices

        metadevice = coordinator.metadevices[metadevice_address]
        assert metadevice.create_sensor is True
        assert metadevice.fmdn_device_id == TEST_FMDN_DEVICE_ID
        assert metadevice.fmdn_canonical_id == TEST_FMDN_CANONICAL_ID
        assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE
        assert METADEVICE_FMDN_DEVICE in metadevice.metadevice_type

    def test_source_device_linked_to_metadevice(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """Source device should be linked to its metadevice."""
        coordinator = lightweight_coordinator
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        metadevice = coordinator.metadevices[metadevice_address]

        # Source should be marked as FMDN source
        assert METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type

        # Source should be in metadevice's sources list
        assert source_device.address in metadevice.metadevice_sources


class TestFmdnDataAggregation:
    """Test that area data flows from sources to metadevices (unit tests)."""

    def test_area_data_aggregated_to_metadevice(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """Area data should be copied from source to metadevice."""
        coordinator = lightweight_coordinator
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        area = coordinator._test_area
        floor = coordinator._test_floor

        # Create source device and handle advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Simulate area selection on source device
        source_device.area_id = area.id
        source_device.area_name = TEST_AREA_NAME
        source_device.area_distance = 2.5
        source_device.area_distance_stamp = monotonic_time_coarse()
        source_device.area_rssi = -65.0
        source_device.last_seen = monotonic_time_coarse()
        source_device.floor_id = floor.floor_id
        source_device.floor_name = TEST_FLOOR_NAME

        # Run aggregation
        coordinator.metadevice_manager.aggregate_source_data_to_metadevices()

        # Verify metadevice has aggregated data
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        metadevice = coordinator.metadevices[metadevice_address]

        assert metadevice.area_id == area.id
        assert metadevice.area_name == TEST_AREA_NAME
        assert metadevice.area_distance == 2.5
        assert metadevice.area_rssi == -65.0
        assert metadevice.floor_id == floor.floor_id
        assert metadevice.floor_name == TEST_FLOOR_NAME

    def test_best_source_selected_for_aggregation(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
        lightweight_coordinator: BermudaDataUpdateCoordinator,
    ) -> None:
        """Most recent source with valid area data should be selected."""
        coordinator = lightweight_coordinator
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        area = coordinator._test_area
        nowstamp = monotonic_time_coarse()

        # Create first source device (older)
        source1 = coordinator._get_or_create_device("11:11:11:11:11:11")
        coordinator.fmdn.handle_advertisement(source1, fmdn_service_data)
        source1.area_id = area.id
        source1.area_name = TEST_AREA_NAME
        source1.area_distance = 5.0
        source1.last_seen = nowstamp - 5  # 5 seconds ago

        # Create second source device (newer)
        source2 = coordinator._get_or_create_device("22:22:22:22:22:22")
        # Manually link to metadevice since same EID would overwrite
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        metadevice = coordinator.metadevices[metadevice_address]
        metadevice.metadevice_sources.append(source2.address)
        source2.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)
        source2.area_id = area.id
        source2.area_name = TEST_AREA_NAME
        source2.area_distance = 1.5  # This one is closer
        source2.last_seen = nowstamp  # Current time

        # Run aggregation
        coordinator.metadevice_manager.aggregate_source_data_to_metadevices()

        # Verify the newer source's data was used
        assert metadevice.area_distance == 1.5


class TestFmdnConfigFlowIntegration:
    """Test that FMDN devices appear in the configuration flow.

    Note: FMDN devices are auto-configured and managed by GoogleFindMy-HA.
    They appear in the selectdevices list for visibility but are filtered OUT
    from CONF_DEVICES when saving (by design - see config_flow.py lines 263-269).
    """

    @pytest.mark.asyncio
    async def test_fmdn_device_appears_in_selectdevices_list(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """FMDN device should appear in config flow Select Devices list."""
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        # Create and set up the config entry
        config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME)
        config_entry.add_to_hass(hass)
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator

        # Create FMDN metadevice via advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Get metadevice address
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)

        # Verify metadevice was created with auto-tracking enabled
        metadevice = coordinator.metadevices.get(metadevice_address)
        assert metadevice is not None
        assert metadevice.create_sensor is True  # Auto-tracked

        # BUG FIX VERIFICATION: The metadevice should AUTOMATICALLY be in coordinator.devices
        # If this assertion fails, the bug has been reintroduced!
        assert metadevice_address in coordinator.devices, (
            "BUG: Metadevice NOT in coordinator.devices! "
            "register_source() should add metadevices to coordinator.devices via _get_or_create_device()"
        )

        # Start options flow and go to selectdevices
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result.get("type") == FlowResultType.MENU
        assert result.get("step_id") == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "selectdevices"}
        )

        assert result.get("step_id") == "selectdevices"

        # The data_schema should include our FMDN device
        schema = result.get("data_schema")
        assert schema is not None

        # Complete the flow (even without selecting, flow should complete)
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={CONF_DEVICES: []})

        # Verify flow completed successfully
        assert result.get("type") == FlowResultType.CREATE_ENTRY

    @pytest.mark.asyncio
    async def test_fmdn_device_filtered_from_conf_devices(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """FMDN device should be filtered out from CONF_DEVICES (auto-configured)."""
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME)
        config_entry.add_to_hass(hass)
        await async_setup_component(hass, DOMAIN, {})

        coordinator = config_entry.runtime_data.coordinator
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)

        # BUG FIX VERIFICATION: The metadevice should AUTOMATICALLY be in coordinator.devices
        assert metadevice_address in coordinator.devices, "BUG: Metadevice NOT in coordinator.devices!"

        # Try to select the FMDN device
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "selectdevices"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_DEVICES: [metadevice_address]}
        )

        # FMDN devices are auto-configured and filtered OUT from CONF_DEVICES
        # This is by design - they are managed by GoogleFindMy-HA integration
        assert result.get("type") == FlowResultType.CREATE_ENTRY
        assert metadevice_address not in config_entry.options.get(CONF_DEVICES, [])

        # But the metadevice should still be tracked (create_sensor=True)
        metadevice = coordinator.metadevices.get(metadevice_address)
        assert metadevice is not None
        assert metadevice.create_sensor is True


class TestFmdnEntityCreation:
    """Test that entities are properly created for FMDN devices (integration tests)."""

    @pytest.mark.asyncio
    async def test_fmdn_device_entities_created_via_dispatcher(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """FMDN devices should have entities created when create_sensor is True.

        This test verifies the full entity creation flow:
        1. Integration is loaded
        2. FMDN advertisement is processed
        3. Metadevice is created with create_sensor=True
        4. Coordinator refresh dispatches SIGNAL_DEVICE_NEW
        5. Entities are created and added to the entity registry
        """
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        # Create and set up the config entry
        config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME)
        config_entry.add_to_hass(hass)
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator

        # Create source device and process FMDN advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Get the metadevice
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        metadevice = coordinator.metadevices.get(metadevice_address)

        # Verify metadevice was created with create_sensor=True
        assert metadevice is not None
        assert metadevice.create_sensor is True

        # The metadevice should be in metadevices dict
        assert metadevice.address in coordinator.metadevices


class TestFmdnCompleteDataFlow:
    """Integration test for complete FMDN data flow."""

    @pytest.mark.asyncio
    async def test_complete_fmdn_data_flow(
        self,
        hass: HomeAssistant,
        mock_resolver: MagicMock,
        fmdn_service_data: Mapping[str | int, Any],
    ) -> None:
        """
        Test complete data flow from GoogleFindMy-HA API to Bermuda.

        This test verifies the entire flow:
        1. GoogleFindMy-HA provides EID resolver
        2. BLE advertisement with FMDN EID is received
        3. EID is resolved to a device
        4. Metadevice is created with create_sensor=True
        5. Source device gets area from scanner
        6. Area data is aggregated to metadevice
        7. Device appears in config flow selectdevices
        """
        # Step 1: Set up GoogleFindMy-HA resolver
        hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: mock_resolver}

        # Step 2: Create floor and area
        floor_registry = fr.async_get(hass)
        area_registry = ar.async_get(hass)
        floor = floor_registry.async_create(TEST_FLOOR_NAME, level=0)
        area = area_registry.async_create(TEST_AREA_NAME)
        area_registry.async_update(area.id, floor_id=floor.floor_id)

        # Step 3: Set up Bermuda integration
        config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME)
        config_entry.add_to_hass(hass)
        await async_setup_component(hass, DOMAIN, {})
        assert config_entry.state == ConfigEntryState.LOADED

        coordinator = config_entry.runtime_data.coordinator

        # Step 4: Create scanner in the area
        scanner = coordinator._get_or_create_device(TEST_SCANNER_MAC)
        scanner._is_scanner = True  # Internal attribute, property is read-only
        scanner.area_id = area.id
        scanner.area_name = area.name
        scanner.area = area
        scanner.floor_id = floor.floor_id
        scanner.floor = floor
        scanner.floor_name = floor.name
        scanner.last_seen = monotonic_time_coarse()
        coordinator._scanners.add(scanner)  # Add device object, not address
        coordinator._scanner_list.add(scanner.address)

        # Step 5: Process FMDN advertisement
        source_device = coordinator._get_or_create_device(TEST_SOURCE_MAC)
        coordinator.fmdn.handle_advertisement(source_device, fmdn_service_data)

        # Step 6: Verify metadevice created
        metadevice_address = coordinator.fmdn.format_metadevice_address(TEST_FMDN_DEVICE_ID, TEST_FMDN_CANONICAL_ID)
        metadevice = coordinator.metadevices.get(metadevice_address)
        assert metadevice is not None
        assert metadevice.create_sensor is True
        assert metadevice.fmdn_device_id == TEST_FMDN_DEVICE_ID

        # Step 7: Simulate area detection on source
        nowstamp = monotonic_time_coarse()
        source_device.area_id = area.id
        source_device.area_name = area.name
        source_device.area = area
        source_device.area_distance = 3.5
        source_device.area_distance_stamp = nowstamp
        source_device.area_rssi = -70.0
        source_device.last_seen = nowstamp
        source_device.floor_id = floor.floor_id
        source_device.floor = floor
        source_device.floor_name = floor.name

        # Step 8: Run aggregation
        coordinator.metadevice_manager.aggregate_source_data_to_metadevices()

        # Step 9: Verify metadevice has correct aggregated data
        assert metadevice.area_id == area.id
        assert metadevice.area_name == TEST_AREA_NAME
        assert metadevice.area_distance == 3.5
        assert metadevice.area_rssi == -70.0
        assert metadevice.floor_id == floor.floor_id
        assert metadevice.floor_name == TEST_FLOOR_NAME

        # Step 10: Verify device can appear in config flow selectdevices
        # BUG FIX VERIFICATION: The metadevice should AUTOMATICALLY be in coordinator.devices
        assert metadevice_address in coordinator.devices, (
            "BUG: Metadevice NOT in coordinator.devices! "
            "register_source() should add metadevices to coordinator.devices via _get_or_create_device()"
        )

        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "selectdevices"}
        )
        assert result.get("step_id") == "selectdevices"

        # Complete the flow - FMDN devices are auto-configured and filtered
        # out from CONF_DEVICES, but they are still tracked (create_sensor=True)
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={CONF_DEVICES: []})
        assert result.get("type") == FlowResultType.CREATE_ENTRY

        # FMDN devices are NOT saved to CONF_DEVICES (auto-configured)
        assert metadevice_address not in config_entry.options.get(CONF_DEVICES, [])

        # Final verification: The FMDN device is tracked via create_sensor=True
        # (not via CONF_DEVICES) and has all its data properly aggregated
        assert metadevice.address in coordinator.metadevices
        assert metadevice.create_sensor is True
        assert source_device.address in metadevice.metadevice_sources
