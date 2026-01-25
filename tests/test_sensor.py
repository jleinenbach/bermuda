"""Test Bermuda sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfLength,
)
from homeassistant.core import HomeAssistant

from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
)
from custom_components.bermuda.sensor import (
    BermudaActiveProxyCount,
    BermudaSensor,
    BermudaSensorAreaLastSeen,
    BermudaSensorAreaSwitchReason,
    BermudaSensorFloor,
    BermudaSensorRange,
    BermudaSensorRssi,
    BermudaSensorScanner,
    BermudaSensorScannerRange,
    BermudaSensorScannerRangeRaw,
    BermudaTotalDeviceCount,
    BermudaTotalProxyCount,
    BermudaVisibleDeviceCount,
    async_setup_entry,
)


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_registers_dispatchers(self, hass: HomeAssistant) -> None:
        """Test that async_setup_entry registers dispatcher listeners."""
        mock_coordinator = MagicMock()
        mock_coordinator.have_floors = True
        mock_coordinator.scanner_list = []
        mock_coordinator.get_scanners = []
        mock_coordinator.devices = {}

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()

        mock_add_devices = MagicMock()

        with patch("custom_components.bermuda.sensor.async_dispatcher_connect") as mock_dispatcher:
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        # Should register both device_new and scanners_changed
        assert mock_dispatcher.call_count == 2
        mock_entry.async_on_unload.assert_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_global_sensors(self, hass: HomeAssistant) -> None:
        """Test that async_setup_entry creates global sensors."""
        mock_coordinator = MagicMock()
        mock_coordinator.have_floors = False
        mock_coordinator.scanner_list = []
        mock_coordinator.get_scanners = []
        mock_coordinator.devices = {}

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()

        mock_add_devices = MagicMock()

        with patch("custom_components.bermuda.sensor.async_dispatcher_connect"):
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        # Verify global sensors were added
        mock_add_devices.assert_called()


class TestBermudaSensor:
    """Tests for BermudaSensor class."""

    def _create_sensor(
        self,
        area_name: str | None = "Living Room",
        address_type: str = "public",
    ) -> BermudaSensor:
        """Create a BermudaSensor instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = area_name
        mock_device.area_id = "living_room"
        mock_device.floor_id = "floor1"
        mock_device.floor_name = "Ground Floor"
        mock_device.floor_level = 0
        mock_device.area_icon = "mdi:sofa"
        mock_device.area_last_seen_icon = "mdi:clock"
        mock_device.floor_icon = "mdi:home-floor-0"
        mock_device.address_type = address_type
        mock_device.adverts = {}
        mock_device.area_state_metadata = MagicMock(return_value={"metadata": "value"})
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensor)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correct."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id"

    def test_has_entity_name(self) -> None:
        """Test that has_entity_name returns True."""
        sensor = self._create_sensor()
        assert sensor.has_entity_name is True

    def test_name(self) -> None:
        """Test that name is Area."""
        sensor = self._create_sensor()
        assert sensor.name == "Area"

    def test_native_value(self) -> None:
        """Test that native_value returns area_name."""
        sensor = self._create_sensor(area_name="Kitchen")
        assert sensor.native_value == "Kitchen"

    def test_icon_for_area_sensor(self) -> None:
        """Test icon for Area sensor."""
        sensor = self._create_sensor()
        # name == "Area" so should return area_icon
        assert sensor.icon == "mdi:sofa"

    def test_entity_registry_enabled_default_for_area(self) -> None:
        """Test that Area sensor is enabled by default."""
        sensor = self._create_sensor()
        assert sensor.entity_registry_enabled_default is True

    def test_device_class(self) -> None:
        """Test that device_class returns custom class."""
        sensor = self._create_sensor()
        assert sensor.device_class == "bermuda__custom_device_class"

    def test_extra_state_attributes_for_area(self) -> None:
        """Test extra_state_attributes for Area sensor."""
        sensor = self._create_sensor()
        attrs = sensor.extra_state_attributes

        assert attrs["area_id"] == "living_room"
        assert attrs["area_name"] == "Living Room"
        assert attrs["floor_id"] == "floor1"
        assert attrs["floor_name"] == "Ground Floor"
        assert attrs["floor_level"] == 0
        assert attrs["current_mac"] == "aa:bb:cc:dd:ee:ff"

    def test_extra_state_attributes_for_metadevice(self) -> None:
        """Test extra_state_attributes returns current MAC for metadevice."""
        sensor = self._create_sensor(address_type=ADDR_TYPE_IBEACON)

        # Add an advert with a stamp
        mock_advert = MagicMock()
        mock_advert.stamp = 100.0
        mock_advert.device_address = "11:22:33:44:55:66"
        sensor._device.adverts = {"scanner1": mock_advert}

        attrs = sensor.extra_state_attributes
        assert attrs["current_mac"] == "11:22:33:44:55:66"

    def test_extra_state_attributes_metadevice_unavailable(self) -> None:
        """Test extra_state_attributes returns unavailable when no adverts."""
        sensor = self._create_sensor(address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE)
        sensor._device.adverts = {}

        attrs = sensor.extra_state_attributes
        assert attrs["current_mac"] == STATE_UNAVAILABLE


class TestBermudaSensorFloor:
    """Tests for BermudaSensorFloor class."""

    def _create_sensor(self, floor_name: str | None = "Ground Floor") -> BermudaSensorFloor:
        """Create a BermudaSensorFloor instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.floor_name = floor_name
        mock_device.floor_icon = "mdi:home-floor-0"
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorFloor)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_floor"

    def test_name(self) -> None:
        """Test that name is Floor."""
        sensor = self._create_sensor()
        assert sensor.name == "Floor"

    def test_native_value(self) -> None:
        """Test that native_value returns floor_name."""
        sensor = self._create_sensor(floor_name="First Floor")
        assert sensor.native_value == "First Floor"


class TestBermudaSensorScanner:
    """Tests for BermudaSensorScanner class."""

    def _create_sensor(self) -> BermudaSensorScanner:
        """Create a BermudaSensorScanner instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_advert = None
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorScanner)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_scanner"

    def test_name(self) -> None:
        """Test that name is Nearest Scanner."""
        sensor = self._create_sensor()
        assert sensor.name == "Nearest Scanner"

    def test_native_value_returns_none_when_no_advert(self) -> None:
        """Test that native_value returns None when no advert."""
        sensor = self._create_sensor()
        sensor._device.area_advert = None
        assert sensor.native_value is None

    def test_native_value_returns_scanner_name(self) -> None:
        """Test that native_value returns scanner name."""
        sensor = self._create_sensor()

        mock_advert = MagicMock()
        mock_advert.scanner_address = "scanner:address"
        sensor._device.area_advert = mock_advert

        mock_scanner = MagicMock()
        mock_scanner.name = "Living Room Scanner"
        sensor.coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": sensor._device,
            "scanner:address": mock_scanner,
        }

        assert sensor.native_value == "Living Room Scanner"

    def test_native_value_returns_none_when_scanner_not_found(self) -> None:
        """Test that native_value returns None when scanner not in devices."""
        sensor = self._create_sensor()

        mock_advert = MagicMock()
        mock_advert.scanner_address = "unknown:scanner"
        sensor._device.area_advert = mock_advert
        sensor.coordinator.devices = {"aa:bb:cc:dd:ee:ff": sensor._device}

        assert sensor.native_value is None


class TestBermudaSensorRssi:
    """Tests for BermudaSensorRssi class."""

    def _create_sensor(self, area_rssi: float | None = -65.0) -> BermudaSensorRssi:
        """Create a BermudaSensorRssi instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_rssi = area_rssi
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorRssi)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False
            sensor.bermuda_last_state = None
            sensor.bermuda_last_stamp = 0.0
            sensor.bermuda_update_interval = 1.0

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_rssi"

    def test_name(self) -> None:
        """Test that name is Nearest RSSI."""
        sensor = self._create_sensor()
        assert sensor.name == "Nearest RSSI"

    def test_device_class(self) -> None:
        """Test that device_class is SIGNAL_STRENGTH."""
        sensor = self._create_sensor()
        assert sensor.device_class == SensorDeviceClass.SIGNAL_STRENGTH

    def test_native_unit_of_measurement(self) -> None:
        """Test that native_unit_of_measurement is dBm."""
        sensor = self._create_sensor()
        assert sensor.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    def test_state_class(self) -> None:
        """Test that state_class is MEASUREMENT."""
        sensor = self._create_sensor()
        assert sensor.state_class == SensorStateClass.MEASUREMENT


class TestBermudaSensorRange:
    """Tests for BermudaSensorRange class."""

    def _create_sensor(self, area_distance: float | None = 2.5) -> BermudaSensorRange:
        """Create a BermudaSensorRange instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_distance = area_distance
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorRange)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False
            sensor.bermuda_last_state = None
            sensor.bermuda_last_stamp = 0.0
            sensor.bermuda_update_interval = 1.0

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_range"

    def test_name(self) -> None:
        """Test that name is Distance."""
        sensor = self._create_sensor()
        assert sensor.name == "Distance"

    def test_device_class(self) -> None:
        """Test that device_class is DISTANCE."""
        sensor = self._create_sensor()
        assert sensor.device_class == SensorDeviceClass.DISTANCE

    def test_native_unit_of_measurement(self) -> None:
        """Test that native_unit_of_measurement is METERS."""
        sensor = self._create_sensor()
        assert sensor.native_unit_of_measurement == UnitOfLength.METERS

    def test_state_class(self) -> None:
        """Test that state_class is MEASUREMENT."""
        sensor = self._create_sensor()
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_native_value_returns_none_when_distance_is_none(self) -> None:
        """Test that native_value returns None when distance is None."""
        sensor = self._create_sensor(area_distance=None)
        assert sensor.native_value is None


class TestBermudaSensorAreaSwitchReason:
    """Tests for BermudaSensorAreaSwitchReason class."""

    def _create_sensor(self, diag_area_switch: str | None = None) -> BermudaSensorAreaSwitchReason:
        """Create a BermudaSensorAreaSwitchReason instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.diag_area_switch = diag_area_switch
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorAreaSwitchReason)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False

        return sensor

    def test_entity_category(self) -> None:
        """Test that entity_category is DIAGNOSTIC."""
        # Create an instance to test attributes (HA metaclass converts class attrs to properties)
        sensor = self._create_sensor()
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_area_switch_reason"

    def test_name(self) -> None:
        """Test that name is Area Switch Diagnostic."""
        sensor = self._create_sensor()
        assert sensor.name == "Area Switch Diagnostic"

    def test_entity_registry_enabled_default(self) -> None:
        """Test that entity is disabled by default."""
        sensor = self._create_sensor()
        assert sensor.entity_registry_enabled_default is False

    def test_native_value_returns_none_when_not_set(self) -> None:
        """Test that native_value returns None when diag_area_switch is None."""
        sensor = self._create_sensor(diag_area_switch=None)
        assert sensor.native_value is None

    def test_native_value_truncates_long_string(self) -> None:
        """Test that native_value truncates strings longer than 255 chars."""
        long_reason = "x" * 300
        sensor = self._create_sensor(diag_area_switch=long_reason)
        assert sensor.native_value == long_reason[:255]


class TestBermudaSensorAreaLastSeen:
    """Tests for BermudaSensorAreaLastSeen class."""

    def _create_sensor(self, area_last_seen: str | None = "Kitchen") -> BermudaSensorAreaLastSeen:
        """Create a BermudaSensorAreaLastSeen instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_last_seen = area_last_seen
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorAreaLastSeen)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False

        return sensor

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        sensor = self._create_sensor()
        assert sensor.unique_id == "test_unique_id_area_last_seen"

    def test_name(self) -> None:
        """Test that name is Area Last Seen."""
        sensor = self._create_sensor()
        assert sensor.name == "Area Last Seen"

    def test_native_value(self) -> None:
        """Test that native_value returns area_last_seen."""
        sensor = self._create_sensor(area_last_seen="Bedroom")
        assert sensor.native_value == "Bedroom"


class TestGlobalSensors:
    """Tests for global sensor classes."""

    def _create_global_sensor(self, sensor_class: type) -> object:
        """Create a global sensor instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True
        mock_coordinator.scanner_list = ["scanner1", "scanner2", "scanner3"]
        mock_coordinator.devices = {"dev1": MagicMock(), "dev2": MagicMock()}
        mock_coordinator.count_active_scanners = MagicMock(return_value=2)
        mock_coordinator.count_active_devices = MagicMock(return_value=5)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        sensor = object.__new__(sensor_class)
        sensor.coordinator = mock_coordinator
        sensor.config_entry = mock_entry
        sensor.bermuda_last_state = None
        sensor.bermuda_last_stamp = 0.0
        sensor.bermuda_update_interval = 1.0

        return sensor

    def test_total_proxy_count_unique_id(self) -> None:
        """Test BermudaTotalProxyCount unique_id."""
        sensor = self._create_global_sensor(BermudaTotalProxyCount)
        assert sensor.unique_id == "BERMUDA_GLOBAL_PROXY_COUNT"

    def test_total_proxy_count_name(self) -> None:
        """Test BermudaTotalProxyCount name."""
        sensor = self._create_global_sensor(BermudaTotalProxyCount)
        assert sensor.name == "Total proxy count"

    def test_active_proxy_count_unique_id(self) -> None:
        """Test BermudaActiveProxyCount unique_id."""
        sensor = self._create_global_sensor(BermudaActiveProxyCount)
        assert sensor.unique_id == "BERMUDA_GLOBAL_ACTIVE_PROXY_COUNT"

    def test_active_proxy_count_name(self) -> None:
        """Test BermudaActiveProxyCount name."""
        sensor = self._create_global_sensor(BermudaActiveProxyCount)
        assert sensor.name == "Active proxy count"

    def test_total_device_count_unique_id(self) -> None:
        """Test BermudaTotalDeviceCount unique_id."""
        sensor = self._create_global_sensor(BermudaTotalDeviceCount)
        assert sensor.unique_id == "BERMUDA_GLOBAL_DEVICE_COUNT"

    def test_total_device_count_name(self) -> None:
        """Test BermudaTotalDeviceCount name."""
        sensor = self._create_global_sensor(BermudaTotalDeviceCount)
        assert sensor.name == "Total device count"

    def test_visible_device_count_unique_id(self) -> None:
        """Test BermudaVisibleDeviceCount unique_id."""
        sensor = self._create_global_sensor(BermudaVisibleDeviceCount)
        assert sensor.unique_id == "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"

    def test_visible_device_count_name(self) -> None:
        """Test BermudaVisibleDeviceCount name."""
        sensor = self._create_global_sensor(BermudaVisibleDeviceCount)
        assert sensor.name == "Visible device count"

    def test_global_sensors_have_diagnostic_category(self) -> None:
        """Test that global sensors have diagnostic category."""
        # Create instances to test attributes (HA metaclass converts class attrs to properties)
        total_proxy = self._create_global_sensor(BermudaTotalProxyCount)
        active_proxy = self._create_global_sensor(BermudaActiveProxyCount)
        total_device = self._create_global_sensor(BermudaTotalDeviceCount)
        visible_device = self._create_global_sensor(BermudaVisibleDeviceCount)
        assert total_proxy.entity_category == EntityCategory.DIAGNOSTIC
        assert active_proxy.entity_category == EntityCategory.DIAGNOSTIC
        assert total_device.entity_category == EntityCategory.DIAGNOSTIC
        assert visible_device.entity_category == EntityCategory.DIAGNOSTIC

    def test_global_sensors_have_measurement_state_class(self) -> None:
        """Test that global sensors have measurement state class."""
        # Create instances to test attributes (HA metaclass converts class attrs to properties)
        total_proxy = self._create_global_sensor(BermudaTotalProxyCount)
        active_proxy = self._create_global_sensor(BermudaActiveProxyCount)
        total_device = self._create_global_sensor(BermudaTotalDeviceCount)
        visible_device = self._create_global_sensor(BermudaVisibleDeviceCount)
        assert total_proxy.state_class == SensorStateClass.MEASUREMENT
        assert active_proxy.state_class == SensorStateClass.MEASUREMENT
        assert total_device.state_class == SensorStateClass.MEASUREMENT
        assert visible_device.state_class == SensorStateClass.MEASUREMENT


class TestSensorIntegration:
    """Integration tests for sensor module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import sensor

        assert hasattr(sensor, "async_setup_entry")
        assert hasattr(sensor, "BermudaSensor")
        assert hasattr(sensor, "BermudaSensorFloor")
        assert hasattr(sensor, "BermudaSensorRange")
        assert hasattr(sensor, "BermudaSensorScanner")
        assert hasattr(sensor, "BermudaSensorRssi")
