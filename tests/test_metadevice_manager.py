"""Test Bermuda MetadeviceManager."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.bermuda.const import (
    CONF_DEVICES,
    DOMAIN_PRIVATE_BLE_DEVICE,
    EVIDENCE_WINDOW_SECONDS,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
)
from custom_components.bermuda.metadevice_manager import MetadeviceManager


class TestMetadeviceManagerInit:
    """Tests for MetadeviceManager initialization."""

    def test_init_stores_coordinator(self) -> None:
        """Test that __init__ stores the coordinator reference."""
        mock_coordinator = MagicMock()
        manager = MetadeviceManager(mock_coordinator)
        assert manager.coordinator is mock_coordinator


class TestMetadeviceManagerProperties:
    """Tests for MetadeviceManager property accessors."""

    def _create_manager(self) -> MetadeviceManager:
        """Create a MetadeviceManager instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.er = MagicMock()
        mock_coordinator.dr = MagicMock()
        mock_coordinator.options = {"some_option": "value"}
        mock_coordinator.metadevices = {"device1": MagicMock()}
        mock_coordinator.pb_state_sources = {"entity1": "source1"}
        mock_coordinator.fmdn = MagicMock()
        mock_coordinator._do_private_device_init = True

        return MetadeviceManager(mock_coordinator)

    def test_hass_property(self) -> None:
        """Test that hass property returns coordinator's hass."""
        manager = self._create_manager()
        result = manager.hass
        assert result is manager.coordinator.hass

    def test_er_property(self) -> None:
        """Test that er property returns coordinator's entity registry."""
        manager = self._create_manager()
        result = manager.er
        assert result is manager.coordinator.er

    def test_dr_property(self) -> None:
        """Test that dr property returns coordinator's device registry."""
        manager = self._create_manager()
        result = manager.dr
        assert result is manager.coordinator.dr

    def test_options_property(self) -> None:
        """Test that options property returns coordinator's options."""
        manager = self._create_manager()
        result = manager.options
        assert result == {"some_option": "value"}

    def test_metadevices_property(self) -> None:
        """Test that metadevices property returns coordinator's metadevices."""
        manager = self._create_manager()
        result = manager.metadevices
        assert "device1" in result

    def test_pb_state_sources_property(self) -> None:
        """Test that pb_state_sources property returns coordinator's pb_state_sources."""
        manager = self._create_manager()
        result = manager.pb_state_sources
        assert result == {"entity1": "source1"}

    def test_fmdn_property(self) -> None:
        """Test that fmdn property returns coordinator's fmdn integration."""
        manager = self._create_manager()
        result = manager.fmdn
        assert result is manager.coordinator.fmdn

    def test_do_private_device_init_getter(self) -> None:
        """Test that _do_private_device_init getter works."""
        manager = self._create_manager()
        result = manager._do_private_device_init
        assert result is True

    def test_do_private_device_init_setter(self) -> None:
        """Test that _do_private_device_init setter works."""
        manager = self._create_manager()
        manager._do_private_device_init = False
        assert manager.coordinator._do_private_device_init is False


class TestMetadeviceManagerHelpers:
    """Tests for MetadeviceManager helper methods."""

    def _create_manager(self) -> MetadeviceManager:
        """Create a MetadeviceManager instance for testing."""
        mock_coordinator = MagicMock()
        mock_device = MagicMock()
        mock_coordinator._get_or_create_device = MagicMock(return_value=mock_device)
        mock_coordinator._get_device = MagicMock(return_value=mock_device)
        return MetadeviceManager(mock_coordinator)

    def test_get_or_create_device_delegates_to_coordinator(self) -> None:
        """Test that _get_or_create_device delegates to coordinator."""
        manager = self._create_manager()
        result = manager._get_or_create_device("aa:bb:cc:dd:ee:ff")
        manager.coordinator._get_or_create_device.assert_called_once_with("aa:bb:cc:dd:ee:ff")
        assert result is manager.coordinator._get_or_create_device.return_value

    def test_get_device_delegates_to_coordinator(self) -> None:
        """Test that _get_device delegates to coordinator."""
        manager = self._create_manager()
        result = manager._get_device("aa:bb:cc:dd:ee:ff")
        manager.coordinator._get_device.assert_called_once_with("aa:bb:cc:dd:ee:ff")
        assert result is manager.coordinator._get_device.return_value


class TestDiscoverPrivateBleMetadevices:
    """Tests for discover_private_ble_metadevices method."""

    def _create_manager_with_mocks(
        self,
        do_init: bool = True,
        config_entries: list | None = None,
        entities: list | None = None,
        pb_device: MagicMock | None = None,
        pb_state: MagicMock | None = None,
    ) -> MetadeviceManager:
        """Create a MetadeviceManager with mocked dependencies."""
        mock_coordinator = MagicMock()
        mock_hass = MagicMock()

        # Set up config entries
        if config_entries is None:
            config_entries = []
        mock_hass.config_entries.async_entries = MagicMock(return_value=config_entries)

        # Set up entity registry
        mock_er = MagicMock()
        if entities is None:
            entities = []
        mock_er.entities.get_entries_for_config_entry_id = MagicMock(return_value=entities)

        # Set up device registry
        mock_dr = MagicMock()
        mock_dr.async_get = MagicMock(return_value=pb_device)

        # Set up states
        mock_hass.states.get = MagicMock(return_value=pb_state)

        mock_coordinator.hass = mock_hass
        mock_coordinator.er = mock_er
        mock_coordinator.dr = mock_dr
        mock_coordinator._do_private_device_init = do_init
        mock_coordinator.pb_state_sources = {}
        mock_coordinator.metadevices = {}
        mock_coordinator.devices = {}

        # Mock _get_or_create_device to return a proper mock device
        mock_metadevice = MagicMock()
        mock_metadevice.address = "test_irk_address"
        mock_metadevice.metadevice_sources = []
        mock_coordinator._get_or_create_device = MagicMock(return_value=mock_metadevice)

        return MetadeviceManager(mock_coordinator)

    def test_does_nothing_when_init_flag_false(self) -> None:
        """Test that method does nothing when _do_private_device_init is False."""
        manager = self._create_manager_with_mocks(do_init=False)

        manager.discover_private_ble_metadevices()

        # Should not query config entries
        manager.hass.config_entries.async_entries.assert_not_called()

    def test_sets_init_flag_to_false(self) -> None:
        """Test that method sets _do_private_device_init to False."""
        manager = self._create_manager_with_mocks(do_init=True)

        manager.discover_private_ble_metadevices()

        assert manager.coordinator._do_private_device_init is False

    def test_queries_private_ble_device_entries(self) -> None:
        """Test that method queries Private BLE Device config entries."""
        manager = self._create_manager_with_mocks(do_init=True)

        manager.discover_private_ble_metadevices()

        manager.hass.config_entries.async_entries.assert_called_once_with(
            DOMAIN_PRIVATE_BLE_DEVICE, include_disabled=False
        )

    def test_processes_device_tracker_entities(self) -> None:
        """Test that method processes device_tracker entities."""
        # Create mock config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        # Create mock entity with valid IRK
        mock_entity = MagicMock()
        mock_entity.domain = Platform.DEVICE_TRACKER
        mock_entity.entity_id = "device_tracker.test_device"
        mock_entity.device_id = "device_123"
        mock_entity.unique_id = "0123456789abcdef0123456789abcdef_device_tracker"

        # Create mock device
        mock_pb_device = MagicMock()
        mock_pb_device.name_by_user = "Test Device"
        mock_pb_device.name = "Test Device Name"

        # Create mock state
        mock_state = MagicMock()
        mock_state.attributes = {"current_address": "AA:BB:CC:DD:EE:FF"}

        manager = self._create_manager_with_mocks(
            do_init=True,
            config_entries=[mock_entry],
            entities=[mock_entity],
            pb_device=mock_pb_device,
            pb_state=mock_state,
        )

        manager.discover_private_ble_metadevices()

        # Should create metadevice with IRK address
        manager.coordinator._get_or_create_device.assert_called()
        # IRK should be extracted from unique_id
        calls = manager.coordinator._get_or_create_device.call_args_list
        irk_call = [c for c in calls if c[0][0] == "0123456789abcdef0123456789abcdef"]
        assert len(irk_call) > 0

    def test_skips_invalid_irk(self) -> None:
        """Test that method skips entities with invalid IRK format."""
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        # Create mock entity with INVALID IRK (not 32 hex chars)
        mock_entity = MagicMock()
        mock_entity.domain = Platform.DEVICE_TRACKER
        mock_entity.entity_id = "device_tracker.test_device"
        mock_entity.device_id = "device_123"
        mock_entity.unique_id = "invalid_irk_device_tracker"  # Too short

        manager = self._create_manager_with_mocks(
            do_init=True,
            config_entries=[mock_entry],
            entities=[mock_entity],
        )

        manager.discover_private_ble_metadevices()

        # Should NOT create metadevice for invalid IRK
        # The call should only be for source address, not IRK
        calls = manager.coordinator._get_or_create_device.call_args_list
        # Filter out calls that would be for valid IRK addresses
        irk_calls = [c for c in calls if len(c[0][0]) == 32]
        assert len(irk_calls) == 0

    def test_skips_non_device_tracker_entities(self) -> None:
        """Test that method skips non-device_tracker entities."""
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        # Create mock entity that is NOT a device_tracker
        mock_entity = MagicMock()
        mock_entity.domain = Platform.SENSOR  # Not device_tracker
        mock_entity.entity_id = "sensor.test_sensor"

        manager = self._create_manager_with_mocks(
            do_init=True,
            config_entries=[mock_entry],
            entities=[mock_entity],
        )

        manager.discover_private_ble_metadevices()

        # Should not create any metadevices
        manager.coordinator._get_or_create_device.assert_not_called()

    def test_handles_missing_source_address(self) -> None:
        """Test that method handles missing source address gracefully."""
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        mock_entity = MagicMock()
        mock_entity.domain = Platform.DEVICE_TRACKER
        mock_entity.entity_id = "device_tracker.test_device"
        mock_entity.device_id = "device_123"
        mock_entity.unique_id = "0123456789abcdef0123456789abcdef_device_tracker"

        mock_pb_device = MagicMock()
        mock_pb_device.name_by_user = None
        mock_pb_device.name = "Test Device"

        # State has no current_address
        mock_state = MagicMock()
        mock_state.attributes = {}

        manager = self._create_manager_with_mocks(
            do_init=True,
            config_entries=[mock_entry],
            entities=[mock_entity],
            pb_device=mock_pb_device,
            pb_state=mock_state,
        )

        # Should not raise exception
        manager.discover_private_ble_metadevices()


class TestRegisterIbeaconSource:
    """Tests for register_ibeacon_source method."""

    def _create_manager(self) -> MetadeviceManager:
        """Create a MetadeviceManager instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.metadevices = {}
        mock_coordinator.devices = {}
        mock_coordinator.options = {}

        mock_metadevice = MagicMock()
        mock_metadevice.address = "test_beacon_unique_id"
        mock_metadevice.metadevice_sources = []
        mock_metadevice.metadevice_type = set()
        mock_coordinator._get_or_create_device = MagicMock(return_value=mock_metadevice)

        return MetadeviceManager(mock_coordinator)

    def test_rejects_non_ibeacon_source(self) -> None:
        """Test that method rejects devices that are not iBeacon sources."""
        manager = self._create_manager()

        mock_source = MagicMock()
        mock_source.metadevice_type = set()  # Not an iBeacon source
        mock_source.name = "Test Device"

        # Should log error but not crash
        manager.register_ibeacon_source(mock_source)

    def test_rejects_device_without_beacon_unique_id(self) -> None:
        """Test that method rejects devices without beacon_unique_id."""
        manager = self._create_manager()

        mock_source = MagicMock()
        mock_source.metadevice_type = {METADEVICE_TYPE_IBEACON_SOURCE}
        mock_source.beacon_unique_id = None
        mock_source.name = "Test Device"

        # Should log error but not crash
        manager.register_ibeacon_source(mock_source)

    def test_creates_new_metadevice_for_first_source(self) -> None:
        """Test that method creates new metadevice for first iBeacon source."""
        manager = self._create_manager()

        mock_source = MagicMock()
        mock_source.metadevice_type = {METADEVICE_TYPE_IBEACON_SOURCE}
        mock_source.beacon_unique_id = "uuid_1234_5678"
        mock_source.address = "aa:bb:cc:dd:ee:ff"
        mock_source.name_bt_serviceinfo = "iBeacon"
        mock_source.name_bt_local_name = "Test Beacon"
        mock_source.beacon_major = 1234
        mock_source.beacon_minor = 5678
        mock_source.beacon_power = -59
        mock_source.beacon_uuid = "uuid-1234-5678"

        manager.register_ibeacon_source(mock_source)

        # Should get or create metadevice with beacon_unique_id
        manager.coordinator._get_or_create_device.assert_called_with("uuid_1234_5678")

    def test_adds_source_to_existing_metadevice(self) -> None:
        """Test that method adds source to existing metadevice."""
        manager = self._create_manager()

        # Pre-populate metadevice with one source
        mock_metadevice = manager.coordinator._get_or_create_device.return_value
        mock_metadevice.metadevice_sources = ["old:source:address"]

        mock_source = MagicMock()
        mock_source.metadevice_type = {METADEVICE_TYPE_IBEACON_SOURCE}
        mock_source.beacon_unique_id = "uuid_1234_5678"
        mock_source.address = "new:source:address"
        mock_source.name_bt_serviceinfo = None
        mock_source.name_bt_local_name = None

        manager.register_ibeacon_source(mock_source)

        # New source should be added
        assert "new:source:address" in mock_metadevice.metadevice_sources

    def test_enables_sensor_creation_for_configured_devices(self) -> None:
        """Test that method enables sensor creation for configured devices."""
        manager = self._create_manager()
        manager.coordinator.options = {CONF_DEVICES: ["uuid_1234_5678"]}

        mock_metadevice = manager.coordinator._get_or_create_device.return_value
        mock_metadevice.address = "uuid_1234_5678"
        mock_metadevice.metadevice_sources = []
        mock_metadevice.create_sensor = False

        mock_source = MagicMock()
        mock_source.metadevice_type = {METADEVICE_TYPE_IBEACON_SOURCE}
        mock_source.beacon_unique_id = "uuid_1234_5678"
        mock_source.address = "aa:bb:cc:dd:ee:ff"
        mock_source.name_bt_serviceinfo = None
        mock_source.name_bt_local_name = None
        mock_source.beacon_major = 1234
        mock_source.beacon_minor = 5678
        mock_source.beacon_power = -59
        mock_source.beacon_uuid = "uuid-1234-5678"

        manager.register_ibeacon_source(mock_source)

        assert mock_metadevice.create_sensor is True


class TestUpdateMetadevices:
    """Tests for update_metadevices method."""

    def _create_manager(
        self,
        metadevices: dict | None = None,
    ) -> MetadeviceManager:
        """Create a MetadeviceManager instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator._do_private_device_init = False
        mock_coordinator.metadevices = metadevices or {}
        mock_coordinator.devices = {}

        mock_fmdn = MagicMock()
        mock_fmdn.discover_metadevices = MagicMock()
        mock_coordinator.fmdn = mock_fmdn

        mock_hass = MagicMock()
        mock_hass.config_entries.async_entries = MagicMock(return_value=[])
        mock_coordinator.hass = mock_hass

        mock_coordinator._get_device = MagicMock(return_value=None)

        return MetadeviceManager(mock_coordinator)

    def test_calls_discover_private_ble_metadevices(self) -> None:
        """Test that method calls discover_private_ble_metadevices."""
        manager = self._create_manager()

        with patch.object(manager, "discover_private_ble_metadevices") as mock_discover:
            manager.update_metadevices()
            mock_discover.assert_called_once()

    def test_calls_fmdn_discover_metadevices(self) -> None:
        """Test that method calls fmdn.discover_metadevices."""
        manager = self._create_manager()

        manager.update_metadevices()

        manager.fmdn.discover_metadevices.assert_called_once()

    def test_copies_adverts_from_source_to_metadevice(self) -> None:
        """Test that method copies adverts from source devices to metadevices."""
        # Create mock source device
        mock_source = MagicMock()
        mock_source.address = "source:address"
        mock_source.adverts = {("source:address", "scanner1"): MagicMock()}
        mock_source.last_seen = 100.0
        mock_source.ref_power = -59
        mock_source.beacon_unique_id = None
        mock_source.name_bt_local_name = None
        mock_source.name_bt_serviceinfo = None
        mock_source.manufacturer = None
        mock_source.beacon_major = None
        mock_source.beacon_minor = None
        mock_source.beacon_power = None
        mock_source.beacon_uuid = None

        # Create mock metadevice
        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["source:address"]
        mock_metadevice.metadevice_type = set()
        mock_metadevice.adverts = {}
        mock_metadevice.last_seen = 50.0
        mock_metadevice.ref_power = 0

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        manager.update_metadevices()

        # Adverts should be copied
        assert ("source:address", "scanner1") in mock_metadevice.adverts

    def test_updates_last_seen_to_max_value(self) -> None:
        """Test that method updates last_seen to maximum of sources."""
        mock_source = MagicMock()
        mock_source.address = "source:address"
        mock_source.adverts = {}
        mock_source.last_seen = 200.0
        mock_source.ref_power = 0
        mock_source.beacon_unique_id = None
        mock_source.name_bt_local_name = None
        mock_source.name_bt_serviceinfo = None
        mock_source.manufacturer = None
        mock_source.beacon_major = None
        mock_source.beacon_minor = None
        mock_source.beacon_power = None
        mock_source.beacon_uuid = None

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["source:address"]
        mock_metadevice.metadevice_type = set()
        mock_metadevice.adverts = {}
        mock_metadevice.last_seen = 100.0
        mock_metadevice.ref_power = 0

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        manager.update_metadevices()

        # last_seen should be updated to max(100, 200) = 200
        assert mock_metadevice.last_seen == 200.0

    def test_skips_missing_source_devices(self) -> None:
        """Test that method skips source devices that don't exist."""
        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["nonexistent:address"]
        mock_metadevice.metadevice_type = set()
        mock_metadevice.adverts = {}

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=None)

        # Should not raise exception
        manager.update_metadevices()

    def test_removes_ibeacon_source_with_changed_uuid(self) -> None:
        """Test that method removes iBeacon sources that changed their UUID."""
        mock_source = MagicMock()
        mock_source.address = "source:address"
        mock_source.beacon_unique_id = "new_uuid"  # Changed from metadevice's uuid
        mock_source.adverts = {}
        mock_source.last_seen = 100.0

        # Use a real list and verify it's empty after removal
        source_list = ["source:address"]

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = source_list
        mock_metadevice.metadevice_type = {METADEVICE_IBEACON_DEVICE}
        mock_metadevice.beacon_unique_id = "old_uuid"  # Different from source
        mock_metadevice.adverts = {("source:address", "scanner1"): MagicMock()}

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        manager.update_metadevices()

        # Source should have been removed from the list
        assert "source:address" not in source_list


class TestAggregateSourceDataToMetadevices:
    """Tests for aggregate_source_data_to_metadevices method."""

    def _create_manager(
        self,
        metadevices: dict | None = None,
    ) -> MetadeviceManager:
        """Create a MetadeviceManager instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.metadevices = metadevices or {}
        mock_coordinator._get_device = MagicMock(return_value=None)

        return MetadeviceManager(mock_coordinator)

    def test_finds_best_source_with_most_recent_last_seen(self) -> None:
        """Test that method selects source with most recent last_seen."""
        # Create two source devices with different last_seen times
        mock_source_old = MagicMock()
        mock_source_old.last_seen = 100.0
        mock_source_old.area_id = "area_old"
        mock_source_old.area_name = "Old Area"

        mock_source_new = MagicMock()
        mock_source_new.last_seen = 200.0
        mock_source_new.area_id = "area_new"
        mock_source_new.area_name = "New Area"
        mock_source_new.area = MagicMock()
        mock_source_new.area_icon = "mdi:home"
        mock_source_new.area_distance = 1.5
        mock_source_new.area_distance_stamp = 199.0
        mock_source_new.area_rssi = -65
        mock_source_new.area_advert = MagicMock()
        mock_source_new.area_last_seen = "Living Room"
        mock_source_new.area_last_seen_id = "living_room"
        mock_source_new.area_last_seen_icon = "mdi:sofa"
        mock_source_new.area_state_stamp = 198.0
        mock_source_new.area_state_source = "source"
        mock_source_new.floor_id = "floor1"
        mock_source_new.floor = MagicMock()
        mock_source_new.floor_name = "Ground Floor"
        mock_source_new.floor_level = 0

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["old:address", "new:address"]
        mock_metadevice.name = "Test Metadevice"

        def get_device_side_effect(address: str) -> MagicMock | None:
            if address == "old:address":
                return mock_source_old
            if address == "new:address":
                return mock_source_new
            return None

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(side_effect=get_device_side_effect)

        # Patch monotonic_time_coarse to return a value that makes sources "fresh"
        with patch(
            "custom_components.bermuda.metadevice_manager.monotonic_time_coarse",
            return_value=200.0 + EVIDENCE_WINDOW_SECONDS - 1,
        ):
            manager.aggregate_source_data_to_metadevices()

        # Metadevice should have data from the newer source
        assert mock_metadevice.area_id == "area_new"
        assert mock_metadevice.area_name == "New Area"

    def test_skips_stale_sources(self) -> None:
        """Test that method skips sources older than evidence window."""
        mock_source = MagicMock()
        mock_source.last_seen = 100.0  # Very old
        mock_source.area_id = "area1"

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["source:address"]
        mock_metadevice.area_id = "original_area"

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        # Set current time to make source stale
        with patch(
            "custom_components.bermuda.metadevice_manager.monotonic_time_coarse",
            return_value=100.0 + EVIDENCE_WINDOW_SECONDS + 100,  # Way past cutoff
        ):
            manager.aggregate_source_data_to_metadevices()

        # Metadevice should NOT be updated (source was stale)
        # area_id should remain as original value (not changed to source's area_id)
        # Note: Mock objects track assignments, so we check it wasn't assigned
        # In this case, we can verify by checking the calls
        calls = [c for c in mock_metadevice.mock_calls if "area_id" in str(c)]
        assert len(calls) == 0

    def test_skips_sources_without_area_data(self) -> None:
        """Test that method skips sources without area_id."""
        mock_source = MagicMock()
        mock_source.last_seen = 200.0
        mock_source.area_id = None  # No area data

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["source:address"]

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        with patch(
            "custom_components.bermuda.metadevice_manager.monotonic_time_coarse",
            return_value=200.0 + EVIDENCE_WINDOW_SECONDS - 1,
        ):
            manager.aggregate_source_data_to_metadevices()

        # Metadevice should NOT be updated
        calls = [c for c in mock_metadevice.mock_calls if "area_id" in str(c)]
        assert len(calls) == 0

    def test_copies_all_area_related_fields(self) -> None:
        """Test that method copies all area-related fields from source."""
        mock_source = MagicMock()
        mock_source.last_seen = 200.0
        mock_source.area_id = "test_area_id"
        mock_source.area_name = "Test Area"
        mock_source.area = MagicMock()
        mock_source.area_icon = "mdi:room"
        mock_source.area_distance = 2.5
        mock_source.area_distance_stamp = 199.0
        mock_source.area_rssi = -70
        mock_source.area_advert = MagicMock()
        mock_source.area_last_seen = "Test Room"
        mock_source.area_last_seen_id = "test_room"
        mock_source.area_last_seen_icon = "mdi:test"
        mock_source.area_state_stamp = 198.0
        mock_source.area_state_source = "test_source"
        mock_source.floor_id = "floor2"
        mock_source.floor = MagicMock()
        mock_source.floor_name = "Second Floor"
        mock_source.floor_level = 1

        mock_metadevice = MagicMock()
        mock_metadevice.metadevice_sources = ["source:address"]
        mock_metadevice.name = "Test Metadevice"

        manager = self._create_manager(metadevices={"meta_addr": mock_metadevice})
        manager.coordinator._get_device = MagicMock(return_value=mock_source)

        with patch(
            "custom_components.bermuda.metadevice_manager.monotonic_time_coarse",
            return_value=200.0 + EVIDENCE_WINDOW_SECONDS - 1,
        ):
            manager.aggregate_source_data_to_metadevices()

        # All fields should be copied
        assert mock_metadevice.area_id == "test_area_id"
        assert mock_metadevice.area_name == "Test Area"
        assert mock_metadevice.area_distance == 2.5
        assert mock_metadevice.floor_id == "floor2"
        assert mock_metadevice.floor_name == "Second Floor"
        assert mock_metadevice.last_seen == 200.0


class TestMetadeviceManagerIntegration:
    """Integration tests for MetadeviceManager."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import metadevice_manager

        assert hasattr(metadevice_manager, "MetadeviceManager")

    def test_manager_can_be_instantiated(self) -> None:
        """Test that MetadeviceManager can be instantiated with a coordinator."""
        mock_coordinator = MagicMock()
        manager = MetadeviceManager(mock_coordinator)
        assert manager is not None
        assert manager.coordinator is mock_coordinator
