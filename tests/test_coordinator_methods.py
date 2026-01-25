"""Tests for coordinator methods to increase coverage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    CONF_USE_UKF_AREA_SELECTION,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.area_selection import AreaSelectionHandler
from custom_components.bermuda.services import BermudaServiceHandler


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_DEVICES: [],
        CONF_ATTENUATION: DEFAULT_ATTENUATION,
        CONF_REF_POWER: DEFAULT_REF_POWER,
        CONF_DEVTRACK_TIMEOUT: DEFAULT_DEVTRACK_TIMEOUT,
        CONF_SMOOTHING_SAMPLES: DEFAULT_SMOOTHING_SAMPLES,
        CONF_MAX_VELOCITY: DEFAULT_MAX_VELOCITY,
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.device_ukfs = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator._correlations_loaded = True
    coordinator._last_correlation_save = 0.0
    coordinator.correlation_store = MagicMock(async_save=AsyncMock())
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.dr = dr.async_get(hass)
    coordinator.er = er.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.service_handler = BermudaServiceHandler(coordinator)
    coordinator.pb_state_sources = {}
    coordinator.stamp_last_prune = 0
    coordinator.update_in_progress = False
    coordinator.last_update_success = False
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.sensor_interval = DEFAULT_UPDATE_INTERVAL
    coordinator.member_uuids = {}
    coordinator.company_uuids = {}
    coordinator.config_entry = SimpleNamespace(
        async_on_unload=lambda cb: cb,
        options={
            CONF_ATTENUATION: DEFAULT_ATTENUATION,
            CONF_REF_POWER: DEFAULT_REF_POWER,
            CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
        },
    )
    coordinator.area_selection = AreaSelectionHandler(coordinator)
    coordinator._do_fmdn_device_init = False
    coordinator._do_private_device_init = False
    coordinator.have_floors = False  # Will be set by init_floors()
    return coordinator


class TestScannerListManagement:
    """Tests for scanner list add/delete methods."""

    def test_scanner_list_add(self, hass: HomeAssistant) -> None:
        """Test adding a scanner to the list."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")
        device._is_scanner = True

        with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_dispatch:
            coordinator.scanner_list_add(device)

            assert device.address in coordinator._scanner_list
            assert device in coordinator._scanners
            mock_dispatch.assert_called_once()

    def test_scanner_list_del(self, hass: HomeAssistant) -> None:
        """Test removing a scanner from the list."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")
        device._is_scanner = True
        coordinator._scanner_list.add(device.address)
        coordinator._scanners.add(device)

        with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_dispatch:
            coordinator.scanner_list_del(device)

            assert device.address not in coordinator._scanner_list
            assert device not in coordinator._scanners
            mock_dispatch.assert_called_once()

    def test_scanner_list_del_nonexistent(self, hass: HomeAssistant) -> None:
        """Test removing a scanner that doesn't exist (should not error)."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")

        with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_dispatch:
            # Should not raise even though device is not in the lists
            coordinator.scanner_list_del(device)
            mock_dispatch.assert_called_once()


class TestReloadOptions:
    """Tests for reload_options method."""

    def test_reload_options_updates_settings(self, hass: HomeAssistant) -> None:
        """Test that reload_options updates coordinator options from config entry."""
        coordinator = _make_coordinator(hass)

        # Update config entry with new options
        coordinator.config_entry.options = {
            CONF_ATTENUATION: 4.0,
            CONF_REF_POWER: -60,
            CONF_MAX_RADIUS: 15.0,
            CONF_MAX_VELOCITY: 5.0,
            CONF_SMOOTHING_SAMPLES: 20,
            CONF_UPDATE_INTERVAL: 2.0,
        }

        coordinator.reload_options()

        assert coordinator.options[CONF_ATTENUATION] == 4.0
        assert coordinator.options[CONF_REF_POWER] == -60
        assert coordinator.options[CONF_MAX_RADIUS] == 15.0
        assert coordinator.options[CONF_MAX_VELOCITY] == 5.0
        assert coordinator.options[CONF_SMOOTHING_SAMPLES] == 20
        assert coordinator.sensor_interval == 2.0

    def test_reload_options_propagates_to_devices(self, hass: HomeAssistant) -> None:
        """Test that reload_options propagates options to existing devices."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")

        coordinator.config_entry.options = {
            CONF_MAX_RADIUS: 20.0,
        }

        coordinator.reload_options()

        assert device.options[CONF_MAX_RADIUS] == 20.0

    def test_reload_options_handles_none_config_entry(self, hass: HomeAssistant) -> None:
        """Test that reload_options handles None config entry gracefully."""
        coordinator = _make_coordinator(hass)
        coordinator.config_entry = None

        # Should not raise
        coordinator.reload_options()

    def test_reload_options_handles_missing_options_attr(self, hass: HomeAssistant) -> None:
        """Test reload_options handles config entry without options attribute."""
        coordinator = _make_coordinator(hass)
        coordinator.config_entry = SimpleNamespace()  # No options attribute

        # Should not raise
        coordinator.reload_options()


class TestGetManufacturerFromId:
    """Tests for get_manufacturer_from_id method."""

    def test_get_manufacturer_shelly(self, hass: HomeAssistant) -> None:
        """Test Shelly manufacturer lookup."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0x0BA9)

        assert name == "Shelly Devices"
        assert generic is False

    def test_get_manufacturer_apple(self, hass: HomeAssistant) -> None:
        """Test Apple manufacturer lookup (generic)."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0x004C)

        assert name == "Apple Inc."
        assert generic is True

    def test_get_manufacturer_bthome_v1_cleartext(self, hass: HomeAssistant) -> None:
        """Test BTHome v1 cleartext lookup."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0x181C)

        assert name == "BTHome v1 cleartext"
        assert generic is True

    def test_get_manufacturer_bthome_v1_encrypted(self, hass: HomeAssistant) -> None:
        """Test BTHome v1 encrypted lookup."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0x181E)

        assert name == "BTHome v1 encrypted"
        assert generic is True

    def test_get_manufacturer_bthome_v2(self, hass: HomeAssistant) -> None:
        """Test BTHome V2 lookup."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0xFCD2)

        assert name == "BTHome V2"
        assert generic is True

    def test_get_manufacturer_from_member_uuids(self, hass: HomeAssistant) -> None:
        """Test lookup from member_uuids dictionary."""
        coordinator = _make_coordinator(hass)
        coordinator.member_uuids = {0x1234: "Test Company"}

        name, generic = coordinator.get_manufacturer_from_id(0x1234)

        assert name == "Test Company"
        assert generic is False

    def test_get_manufacturer_from_member_uuids_google_generic(self, hass: HomeAssistant) -> None:
        """Test that Google in member_uuids is marked generic."""
        coordinator = _make_coordinator(hass)
        coordinator.member_uuids = {0x5678: "Google LLC"}

        name, generic = coordinator.get_manufacturer_from_id(0x5678)

        assert name == "Google LLC"
        assert generic is True

    def test_get_manufacturer_from_company_uuids(self, hass: HomeAssistant) -> None:
        """Test lookup from company_uuids dictionary."""
        coordinator = _make_coordinator(hass)
        coordinator.company_uuids = {0xABCD: "Custom Corp"}

        name, generic = coordinator.get_manufacturer_from_id(0xABCD)

        assert name == "Custom Corp"
        assert generic is False

    def test_get_manufacturer_not_found(self, hass: HomeAssistant) -> None:
        """Test when manufacturer is not found."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id(0xFFFF)

        assert name is None
        assert generic is None

    def test_get_manufacturer_from_string_uuid(self, hass: HomeAssistant) -> None:
        """Test lookup with string UUID."""
        coordinator = _make_coordinator(hass)

        name, generic = coordinator.get_manufacturer_from_id("0ba9")

        assert name == "Shelly Devices"
        assert generic is False

    def test_get_manufacturer_from_string_with_colon(self, hass: HomeAssistant) -> None:
        """Test lookup with string UUID containing colons."""
        coordinator = _make_coordinator(hass)

        # 0x0BA9 = 2985 decimal
        name, generic = coordinator.get_manufacturer_from_id("0b:a9")

        assert name == "Shelly Devices"


class TestCheckAddressExists:
    """Tests for _check_address_exists method."""

    def test_check_address_exists_direct_match(self, hass: HomeAssistant) -> None:
        """Test direct address match."""
        coordinator = _make_coordinator(hass)
        coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")

        assert coordinator._check_address_exists("aa:bb:cc:dd:ee:ff") is True

    def test_check_address_not_exists(self, hass: HomeAssistant) -> None:
        """Test non-existent address."""
        coordinator = _make_coordinator(hass)

        assert coordinator._check_address_exists("aa:bb:cc:dd:ee:ff") is False

    def test_check_fmdn_address_by_canonical_id(self, hass: HomeAssistant) -> None:
        """Test FMDN address lookup by canonical_id."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("fmdn:test-device-id")
        device.fmdn_canonical_id = "12345678-1234-5678-9abc-def012345678"

        assert coordinator._check_address_exists("fmdn:12345678-1234-5678-9abc-def012345678") is True

    def test_check_fmdn_address_by_device_id(self, hass: HomeAssistant) -> None:
        """Test FMDN address lookup by device_id."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("fmdn:some-address")
        device.fmdn_device_id = "ha-device-registry-id"

        assert coordinator._check_address_exists("fmdn:ha-device-registry-id") is True

    def test_check_fmdn_address_not_found(self, hass: HomeAssistant) -> None:
        """Test FMDN address not found."""
        coordinator = _make_coordinator(hass)
        coordinator._get_or_create_device("fmdn:different-device")

        assert coordinator._check_address_exists("fmdn:nonexistent") is False


class TestExtractBaseAddressFromUniqueId:
    """Tests for _extract_base_address_from_unique_id method."""

    def test_extract_with_floor_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _floor suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_floor")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_with_range_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _range suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_range")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_with_rssi_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _rssi suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_rssi")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_without_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction without known suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_per_scanner_entity(self, hass: HomeAssistant) -> None:
        """Test extraction from per-scanner entity unique_id."""
        coordinator = _make_coordinator(hass)
        coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_11:22:33:44:55:66_range")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_fmdn_device(self, hass: HomeAssistant) -> None:
        """Test extraction from FMDN device unique_id."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("fmdn:device-id_floor")

        assert result == "fmdn:device-id"


class TestGetEntityTypeKey:
    """Tests for _get_entity_type_key method."""

    def test_get_entity_type_with_floor_suffix(self, hass: HomeAssistant) -> None:
        """Test entity type key with _floor suffix."""
        coordinator = _make_coordinator(hass)
        entity = SimpleNamespace(domain="sensor", unique_id="aa:bb:cc:dd:ee:ff_floor")

        result = coordinator._get_entity_type_key(entity)

        assert result == "sensor_floor"

    def test_get_entity_type_with_range_suffix(self, hass: HomeAssistant) -> None:
        """Test entity type key with _range suffix."""
        coordinator = _make_coordinator(hass)
        entity = SimpleNamespace(domain="sensor", unique_id="aa:bb:cc:dd:ee:ff_range")

        result = coordinator._get_entity_type_key(entity)

        assert result == "sensor_range"

    def test_get_entity_type_without_suffix(self, hass: HomeAssistant) -> None:
        """Test entity type key without known suffix."""
        coordinator = _make_coordinator(hass)
        entity = SimpleNamespace(domain="device_tracker", unique_id="aa:bb:cc:dd:ee:ff")

        result = coordinator._get_entity_type_key(entity)

        assert result == "device_tracker"

    def test_get_entity_type_no_unique_id(self, hass: HomeAssistant) -> None:
        """Test entity type key when unique_id is None."""
        coordinator = _make_coordinator(hass)
        entity = SimpleNamespace(domain="sensor", unique_id=None)

        result = coordinator._get_entity_type_key(entity)

        assert result == "sensor"


class TestFindDuplicateEntities:
    """Tests for _find_duplicate_entities method."""

    def test_no_duplicates(self, hass: HomeAssistant) -> None:
        """Test when there are no duplicates."""
        coordinator = _make_coordinator(hass)
        entities = [
            SimpleNamespace(entity_id="sensor.test1", domain="sensor", unique_id="addr_floor", disabled_by=None),
            SimpleNamespace(entity_id="sensor.test2", domain="sensor", unique_id="addr_range", disabled_by=None),
        ]

        result = coordinator._find_duplicate_entities(entities)

        assert result == []

    def test_duplicates_same_type(self, hass: HomeAssistant) -> None:
        """Test finding duplicates of the same entity type."""
        coordinator = _make_coordinator(hass)
        coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        entities = [
            SimpleNamespace(entity_id="sensor.test1", domain="sensor", unique_id="aa:bb:cc:dd:ee:ff_floor", disabled_by=None),
            SimpleNamespace(entity_id="sensor.test2", domain="sensor", unique_id="other_floor", disabled_by=None),
        ]

        result = coordinator._find_duplicate_entities(entities)

        # One should be marked for removal
        assert len(result) == 1
        assert "sensor.test2" in result

    def test_duplicates_prefer_enabled(self, hass: HomeAssistant) -> None:
        """Test that enabled entities are preferred over disabled."""
        coordinator = _make_coordinator(hass)
        entities = [
            SimpleNamespace(entity_id="sensor.disabled", domain="sensor", unique_id="addr_floor", disabled_by="user"),
            SimpleNamespace(entity_id="sensor.enabled", domain="sensor", unique_id="addr2_floor", disabled_by=None),
        ]

        result = coordinator._find_duplicate_entities(entities)

        # Disabled one should be removed
        assert len(result) == 1
        assert "sensor.disabled" in result


class TestDeviceCleanup:
    """Tests for device cleanup methods."""

    def test_extract_with_training_learn_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _training_learn suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_training_learn")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_with_reset_training_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _reset_training suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_reset_training")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_with_training_room_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _training_room suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_training_room")

        assert result == "aa:bb:cc:dd:ee:ff"

    def test_extract_with_area_last_seen_suffix(self, hass: HomeAssistant) -> None:
        """Test extraction with _area_last_seen suffix."""
        coordinator = _make_coordinator(hass)

        result = coordinator._extract_base_address_from_unique_id("aa:bb:cc:dd:ee:ff_area_last_seen")

        assert result == "aa:bb:cc:dd:ee:ff"


class TestHandleDevregChanges:
    """Tests for handle_devreg_changes method."""

    def test_handle_create_without_device_id(self, hass: HomeAssistant) -> None:
        """Test handling create event without device_id."""
        coordinator = _make_coordinator(hass)

        event = SimpleNamespace(
            data={"action": "create", "device_id": None}
        )

        # Should not raise
        coordinator.handle_devreg_changes(event)

    def test_handle_update_with_scanner_device(self, hass: HomeAssistant) -> None:
        """Test handling update event for scanner device."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        device._is_scanner = True
        device.entry_id = "test-entry-id"

        with patch.object(coordinator, "_refresh_scanners") as mock_refresh:
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-entry-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

            mock_refresh.assert_called_once_with(force=True)

    def test_handle_update_event(self, hass: HomeAssistant) -> None:
        """Test handling update event logs correctly."""
        coordinator = _make_coordinator(hass)

        event = SimpleNamespace(
            data={"action": "update", "device_id": "some-id", "changes": {"name": "new name"}}
        )

        # Should not raise even with non-existent device_id
        coordinator.handle_devreg_changes(event)

    def test_handle_remove_event(self, hass: HomeAssistant) -> None:
        """Test handling remove event."""
        coordinator = _make_coordinator(hass)

        event = SimpleNamespace(
            data={"action": "remove", "device_id": "some-id"}
        )

        # Should not raise
        coordinator.handle_devreg_changes(event)


class TestInitFloors:
    """Tests for init_floors method."""

    def test_init_floors_true(self, hass: HomeAssistant) -> None:
        """Test init_floors returns True when floors exist."""
        coordinator = _make_coordinator(hass)
        floor = coordinator.fr.async_create("Test Floor")
        coordinator.ar.async_create("Test Area", floor_id=floor.floor_id)

        result = coordinator.init_floors()

        assert result is True

    def test_init_floors_false(self, hass: HomeAssistant) -> None:
        """Test init_floors returns False when no floors assigned."""
        coordinator = _make_coordinator(hass)
        # Create area without floor
        coordinator.ar.async_create("Test Area")

        result = coordinator.init_floors()

        assert result is False


class TestHandleDevregChangesExtended:
    """Extended tests for handle_devreg_changes method to increase coverage."""

    def test_handle_update_with_googlefindmy_identifier(self, hass: HomeAssistant) -> None:
        """Test handling update event for googlefindmy device triggers FMDN init."""
        from custom_components.bermuda.const import DOMAIN_GOOGLEFINDMY

        coordinator = _make_coordinator(hass)

        # Mock the device registry to return a device with googlefindmy identifier
        mock_device_entry = MagicMock()
        mock_device_entry.identifiers = {(DOMAIN_GOOGLEFINDMY, "test-fmdn-id")}
        mock_device_entry.connections = set()

        with patch.object(coordinator.dr, "async_get", return_value=mock_device_entry):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-device-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

        assert coordinator._do_fmdn_device_init is True

    def test_handle_update_with_bermuda_identifier(self, hass: HomeAssistant) -> None:
        """Test handling update event for Bermuda device updates name."""
        coordinator = _make_coordinator(hass)

        # Create a Bermuda device first
        device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        device.name_by_user = None

        # Mock the device registry to return a device with Bermuda identifier
        mock_device_entry = MagicMock()
        mock_device_entry.identifiers = {(DOMAIN, "aa:bb:cc:dd:ee:ff")}
        mock_device_entry.connections = set()
        mock_device_entry.name_by_user = "Custom Name"

        with patch.object(coordinator.dr, "async_get", return_value=mock_device_entry):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-device-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

        assert device.name_by_user == "Custom Name"

    def test_handle_update_with_private_ble_connection(self, hass: HomeAssistant) -> None:
        """Test handling update event with private_ble_device connection."""
        coordinator = _make_coordinator(hass)

        # Mock the device registry to return a device with private_ble_device connection
        mock_device_entry = MagicMock()
        mock_device_entry.identifiers = {("other", "test-id")}
        mock_device_entry.connections = {("private_ble_device", "test-pble-id")}

        with patch.object(coordinator.dr, "async_get", return_value=mock_device_entry):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-device-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

        assert coordinator._do_private_device_init is True

    def test_handle_update_with_ibeacon_connection(self, hass: HomeAssistant) -> None:
        """Test handling update event with ibeacon connection (no-op)."""
        coordinator = _make_coordinator(hass)

        # Mock the device registry to return a device with ibeacon connection
        mock_device_entry = MagicMock()
        mock_device_entry.identifiers = {("other", "test-id")}
        mock_device_entry.connections = {("ibeacon", "test-ibeacon-id")}

        coordinator._scanner_init_pending = False

        with patch.object(coordinator.dr, "async_get", return_value=mock_device_entry):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-device-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

        # ibeacon connection should NOT trigger scanner init
        assert coordinator._scanner_init_pending is False

    def test_handle_update_with_other_connection_triggers_scanner(self, hass: HomeAssistant) -> None:
        """Test handling update event with other connection triggers scanner init."""
        coordinator = _make_coordinator(hass)

        # Mock the device registry to return a device with bluetooth connection
        mock_device_entry = MagicMock()
        mock_device_entry.identifiers = {("other", "test-id")}
        mock_device_entry.connections = {("bluetooth", "aa:bb:cc:dd:ee:ff")}

        with patch.object(coordinator.dr, "async_get", return_value=mock_device_entry):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "test-device-id", "changes": {}}
            )

            coordinator.handle_devreg_changes(event)

        assert coordinator._scanner_init_pending is True

    def test_handle_remove_scanner_triggers_update(self, hass: HomeAssistant) -> None:
        """Test handling remove event for scanner device."""
        coordinator = _make_coordinator(hass)

        # Create a scanner device
        scanner = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        scanner._is_scanner = True
        scanner.entry_id = "scanner-entry-id"
        coordinator._scanners.add(scanner)

        event = SimpleNamespace(
            data={"action": "remove", "device_id": "scanner-entry-id"}
        )

        coordinator.handle_devreg_changes(event)

        assert coordinator._scanner_init_pending is True

    def test_handle_remove_non_scanner_triggers_pble_fmdn(self, hass: HomeAssistant) -> None:
        """Test handling remove event for non-scanner triggers PBLE and FMDN init."""
        coordinator = _make_coordinator(hass)
        coordinator._do_private_device_init = False
        coordinator._do_fmdn_device_init = False

        event = SimpleNamespace(
            data={"action": "remove", "device_id": "non-scanner-id"}
        )

        coordinator.handle_devreg_changes(event)

        assert coordinator._do_private_device_init is True
        assert coordinator._do_fmdn_device_init is True

    def test_handle_update_device_not_found_logs_error(self, hass: HomeAssistant) -> None:
        """Test handling update event when device doesn't exist in registry."""
        coordinator = _make_coordinator(hass)

        # Mock the device registry to return None (device not found)
        with patch.object(coordinator.dr, "async_get", return_value=None):
            event = SimpleNamespace(
                data={"action": "update", "device_id": "nonexistent-id", "changes": {}}
            )

            # Should not raise
            coordinator.handle_devreg_changes(event)


class TestGetManufacturerFromIdExtended:
    """Additional tests for get_manufacturer_from_id method."""

    def test_get_manufacturer_from_company_uuid(self, hass: HomeAssistant) -> None:
        """Test getting manufacturer from company UUID (non-Apple)."""
        coordinator = _make_coordinator(hass)
        coordinator.member_uuids = {}
        coordinator.company_uuids = {0x1234: "Test Company Inc."}

        result = coordinator.get_manufacturer_from_id(0x1234)

        # company_uuids return (name, False) since _generic is False for company
        assert result == ("Test Company Inc.", False)

    def test_get_manufacturer_from_member_uuid(self, hass: HomeAssistant) -> None:
        """Test getting manufacturer from member UUID (non-Google, non-Realtek)."""
        coordinator = _make_coordinator(hass)
        coordinator.member_uuids = {0x9999: "Random Manufacturer"}
        coordinator.company_uuids = {}

        result = coordinator.get_manufacturer_from_id(0x9999)

        # member_uuids return (name, None or True) - True if "Google" or "Realtek" in name
        # For "Random Manufacturer", it should be (name, None) since no special handling
        name, generic = result
        assert name == "Random Manufacturer"

    def test_get_manufacturer_not_found(self, hass: HomeAssistant) -> None:
        """Test getting manufacturer when UUID not in database."""
        coordinator = _make_coordinator(hass)
        coordinator.member_uuids = {}
        coordinator.company_uuids = {}

        result = coordinator.get_manufacturer_from_id(0x9999)

        assert result == (None, None)


class TestCheckAddressExists:
    """Tests for _check_address_exists method."""

    def test_check_address_exists_direct(self, hass: HomeAssistant) -> None:
        """Test checking if address exists directly."""
        coordinator = _make_coordinator(hass)
        coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")

        result = coordinator._check_address_exists("aa:bb:cc:dd:ee:ff")

        assert result is True

    def test_check_address_not_exists(self, hass: HomeAssistant) -> None:
        """Test checking if address does not exist."""
        coordinator = _make_coordinator(hass)

        result = coordinator._check_address_exists("aa:bb:cc:dd:ee:ff")

        assert result is False

    def test_check_fmdn_address_with_prefix(self, hass: HomeAssistant) -> None:
        """Test checking FMDN address with fmdn: prefix."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("fmdn:test-canonical-id")
        device.fmdn_canonical_id = "test-canonical-id"

        result = coordinator._check_address_exists("fmdn:test-canonical-id")

        assert result is True

    def test_check_fmdn_address_by_canonical_id(self, hass: HomeAssistant) -> None:
        """Test checking FMDN address by matching canonical_id."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("fmdn:other-address")
        device.fmdn_canonical_id = "search-id"

        # Check with the canonical_id that's in the device attribute
        result = coordinator._check_address_exists("fmdn:search-id")

        assert result is True


class TestFmdnCheckAddressExists:
    """Additional tests for FMDN address checking."""

    def test_check_fmdn_by_device_id(self, hass: HomeAssistant) -> None:
        """Test checking FMDN address by matching device_id."""
        coordinator = _make_coordinator(hass)
        device = coordinator._get_or_create_device("fmdn:main-address")
        device.fmdn_device_id = "matching-device-id"

        result = coordinator._check_address_exists("fmdn:matching-device-id")

        assert result is True

    def test_check_fmdn_address_not_found(self, hass: HomeAssistant) -> None:
        """Test FMDN address not found when no match."""
        coordinator = _make_coordinator(hass)

        result = coordinator._check_address_exists("fmdn:non-existent-id")

        assert result is False


class TestPruneDevicesAdditional:
    """Additional tests for prune_devices method."""

    def test_prune_devices_respects_metadevice_sources(self, hass: HomeAssistant) -> None:
        """Test that prune_devices doesn't prune metadevice sources."""
        from custom_components.bermuda import coordinator as coord_mod

        coordinator = _make_coordinator(hass)

        # Create a metadevice with a source
        metadevice = coordinator._get_or_create_device("meta:test-device")
        metadevice.create_sensor = True
        metadevice.metadevice_sources = ["aa:bb:cc:dd:ee:ff"]
        coordinator.metadevices["meta:test-device"] = metadevice

        # Create the source device
        source = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
        source.create_sensor = False
        source.last_seen = 0  # Very old

        with patch.object(coord_mod, "monotonic_time_coarse", return_value=10000.0):
            coordinator.prune_devices(force_pruning=True)

        # Source should still exist (protected by metadevice)
        assert "aa:bb:cc:dd:ee:ff" in coordinator.devices
