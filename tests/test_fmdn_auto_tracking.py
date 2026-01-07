"""Tests for automatic FMDN device tracking (like Private BLE Device)."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    ADDR_TYPE_FMDN_DEVICE,
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_PRIVATE_BLE_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


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
    coordinator._do_private_device_init = False
    coordinator._do_fmdn_device_init = False
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


def test_fmdn_resolution_sets_create_sensor_true(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """FMDN devices should have create_sensor = True after resolution."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-id", canonical_id="canon-1")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x01" * 20}

    source_device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    # Key assertion: create_sensor should be True for FMDN devices
    assert metadevice.create_sensor is True
    assert metadevice.fmdn_device_id == match.device_id
    assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE
    assert METADEVICE_FMDN_DEVICE in metadevice.metadevice_type


def test_fmdn_metadevice_address_type_is_fmdn_device(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """FMDN metadevices should have address_type = ADDR_TYPE_FMDN_DEVICE."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-2", canonical_id="canon-2")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x02" * 20}

    source_device = coordinator._get_or_create_device("11:22:33:44:55:66")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    assert metadevice.address_type == ADDR_TYPE_FMDN_DEVICE


def test_fmdn_calculate_data_preserves_create_sensor(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """calculate_data() should not overwrite create_sensor for FMDN devices."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-3", canonical_id="canon-3")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x03" * 20}

    source_device = coordinator._get_or_create_device("22:33:44:55:66:77")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    # Verify create_sensor is True before calculate_data
    assert metadevice.create_sensor is True

    # Call calculate_data (this should NOT overwrite create_sensor for FMDN devices)
    metadevice.calculate_data()

    # After calculate_data, create_sensor should still be True
    assert metadevice.create_sensor is True


def test_private_ble_device_calculate_data_preserves_create_sensor(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """calculate_data() should not overwrite create_sensor for Private BLE devices."""
    # Create a Private BLE metadevice
    metadevice = coordinator._get_or_create_device("test_irk_address")
    metadevice.metadevice_type.add(METADEVICE_PRIVATE_BLE_DEVICE)
    metadevice.create_sensor = True

    # Call calculate_data (this should NOT overwrite create_sensor for Private BLE devices)
    metadevice.calculate_data()

    # After calculate_data, create_sensor should still be True
    assert metadevice.create_sensor is True


def test_fmdn_do_init_flag_triggers_discovery(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """_do_fmdn_device_init flag should trigger discover_fmdn_metadevices."""
    # Initially, flag is False
    assert coordinator._do_fmdn_device_init is False

    # Set the flag
    coordinator._do_fmdn_device_init = True

    # Call discover_fmdn_metadevices
    coordinator.fmdn.discover_metadevices()

    # After discovery, flag should be reset to False
    assert coordinator._do_fmdn_device_init is False


def test_fmdn_device_not_in_configured_devices_still_tracked(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """FMDN devices should be tracked even without being in CONF_DEVICES."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-4", canonical_id="canon-4")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    # Ensure CONF_DEVICES is empty
    coordinator.options = {"configured_devices": []}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x04" * 20}

    source_device = coordinator._get_or_create_device("33:44:55:66:77:88")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    # Key assertion: create_sensor should be True even without manual configuration
    assert metadevice.create_sensor is True


def test_fmdn_device_has_fmdn_device_id(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """FMDN metadevices should have fmdn_device_id set for device congealment."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="googlefindmy-device-id", canonical_id="canonical-uuid")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x05" * 20}

    source_device = coordinator._get_or_create_device("44:55:66:77:88:99")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    # fmdn_device_id should be set for device registry congealment
    assert metadevice.fmdn_device_id == "googlefindmy-device-id"


def test_fmdn_device_has_fmdn_canonical_id(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """FMDN metadevices should have fmdn_canonical_id set for consistent addressing."""
    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-5", canonical_id="canonical-uuid-5")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x06" * 20}

    source_device = coordinator._get_or_create_device("55:66:77:88:99:aa")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    metadevice_key = coordinator.fmdn.format_metadevice_address(match.device_id, match.canonical_id)
    metadevice = coordinator.metadevices[metadevice_key]

    # fmdn_canonical_id should be set for consistent metadevice addressing
    assert metadevice.fmdn_canonical_id == "canonical-uuid-5"


def test_fmdn_deduplication_by_device_id_prevents_duplicates(
    hass: HomeAssistant, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Test that metadevices are deduplicated by fmdn_device_id.

    This prevents duplicate entities when register_source() and
    fmdn.discover_metadevices() use different canonical_id formats.
    """
    # First, simulate _register_fmdn_source creating a metadevice via BLE advertisement
    resolver = MagicMock()
    match = SimpleNamespace(device_id="shared-device-registry-id", canonical_id="entry_id:device_id")
    resolver.resolve_eid_all.return_value = [match]
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data: Mapping[str | int, Any] = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x07" * 20}
    source_device = coordinator._get_or_create_device("66:77:88:99:aa:bb")
    coordinator.fmdn.handle_advertisement(source_device, service_data)

    # Verify first metadevice was created
    assert len(coordinator.metadevices) == 1
    first_metadevice = next(iter(coordinator.metadevices.values()))
    assert first_metadevice.fmdn_device_id == "shared-device-registry-id"

    # Now simulate a second registration with the SAME device_id but DIFFERENT canonical_id
    # This simulates what happens when discover_fmdn_metadevices extracts a different
    # canonical_id format from the device registry identifiers
    match2 = SimpleNamespace(device_id="shared-device-registry-id", canonical_id="different_canonical_id")
    resolver.resolve_eid_all.return_value = [match2]

    source_device2 = coordinator._get_or_create_device("77:88:99:aa:bb:cc")
    coordinator.fmdn.handle_advertisement(source_device2, service_data)

    # CRITICAL ASSERTION: Should still be only ONE metadevice (not two!)
    # because they share the same fmdn_device_id
    assert len(coordinator.metadevices) == 1

    # The metadevice should now have both source devices
    metadevice = next(iter(coordinator.metadevices.values()))
    assert metadevice.fmdn_device_id == "shared-device-registry-id"
    assert source_device.address in metadevice.metadevice_sources
    assert source_device2.address in metadevice.metadevice_sources
