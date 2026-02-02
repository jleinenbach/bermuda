"""Test Bermuda sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
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
        # current_mac is now pre-computed in BermudaDevice.calculate_data()
        sensor._device.current_mac = "aa:bb:cc:dd:ee:ff"
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
        # current_mac is pre-computed by calculate_data() from the most recent advert
        sensor._device.current_mac = "11:22:33:44:55:66"

        attrs = sensor.extra_state_attributes
        assert attrs["current_mac"] == "11:22:33:44:55:66"

    def test_extra_state_attributes_metadevice_unavailable(self) -> None:
        """Test extra_state_attributes returns device address when no adverts."""
        sensor = self._create_sensor(address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE)
        # When no adverts, current_mac defaults to the device address
        sensor._device.current_mac = "aa:bb:cc:dd:ee:ff"

        attrs = sensor.extra_state_attributes
        assert attrs["current_mac"] == "aa:bb:cc:dd:ee:ff"


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


class TestBermudaSensorScannerRange:
    """Tests for BermudaSensorScannerRange class."""

    def _create_sensor(
        self,
        rssi_distance: float | None = 3.5,
    ) -> BermudaSensorScannerRange:
        """Create a BermudaSensorScannerRange instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"

        mock_advert = MagicMock()
        mock_advert.rssi_distance = rssi_distance
        mock_device.adverts = {"scanner:addr": mock_advert}
        mock_device.get_scanner = MagicMock(return_value=mock_advert)

        mock_scanner = MagicMock()
        mock_scanner.name = "Test Scanner"
        mock_scanner.address = "scanner:addr"
        mock_scanner.address_wifi_mac = None
        mock_scanner.area_id = "scanner_area"
        mock_scanner.area_name = "Scanner Room"
        mock_coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": mock_device,
            "scanner:addr": mock_scanner,
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorScannerRange)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._scanner = mock_scanner
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
        assert sensor.unique_id == "test_unique_id_scanner:addr_range"

    def test_name(self) -> None:
        """Test that name includes scanner name."""
        sensor = self._create_sensor()
        assert sensor.name == "Distance to Test Scanner"

    def test_device_class(self) -> None:
        """Test that device_class is DISTANCE."""
        sensor = self._create_sensor()
        assert sensor.device_class == SensorDeviceClass.DISTANCE

    def test_native_value_returns_distance(self) -> None:
        """Test that native_value returns rssi_distance."""
        sensor = self._create_sensor(rssi_distance=4.256)
        assert sensor.native_value == 4.256

    def test_native_value_returns_none_when_no_advert(self) -> None:
        """Test that native_value returns None when advert missing."""
        sensor = self._create_sensor()
        sensor._device.get_scanner = MagicMock(return_value=None)
        assert sensor.native_value is None


class TestBermudaSensorScannerRangeRaw:
    """Tests for BermudaSensorScannerRangeRaw class."""

    def _create_sensor(
        self,
        rssi_distance_raw: float | None = 5.0,
    ) -> BermudaSensorScannerRangeRaw:
        """Create a BermudaSensorScannerRangeRaw instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"

        mock_advert = MagicMock()
        mock_advert.rssi_distance_raw = rssi_distance_raw
        mock_device.adverts = {"scanner:addr": mock_advert}
        mock_device.get_scanner = MagicMock(return_value=mock_advert)

        mock_scanner = MagicMock()
        mock_scanner.name = "Test Scanner"
        mock_scanner.address = "scanner:addr"
        mock_scanner.address_wifi_mac = None
        mock_coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": mock_device,
            "scanner:addr": mock_scanner,
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorScannerRangeRaw)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._scanner = mock_scanner
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
        assert sensor.unique_id == "test_unique_id_scanner:addr_range_raw"

    def test_name(self) -> None:
        """Test that name includes scanner name."""
        sensor = self._create_sensor()
        assert sensor.name == "Unfiltered Distance to Test Scanner"

    def test_native_value_returns_raw_distance(self) -> None:
        """Test that native_value returns rssi_distance_raw."""
        sensor = self._create_sensor(rssi_distance_raw=6.345)
        assert sensor.native_value == 6.345

    def test_native_value_returns_none_when_no_advert(self) -> None:
        """Test that native_value returns None when advert missing."""
        sensor = self._create_sensor()
        sensor._device.get_scanner = MagicMock(return_value=None)
        assert sensor.native_value is None


class TestDeviceNewCallback:
    """Tests for the device_new callback in async_setup_entry."""

    @pytest.mark.asyncio
    async def test_device_new_creates_sensors(self, hass: HomeAssistant) -> None:
        """Test that device_new callback creates sensor entities."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = "Living Room"
        mock_device.area_id = "living_room"
        mock_device.floor_id = "floor1"
        mock_device.floor_name = "Ground Floor"
        mock_device.floor_level = 0
        mock_device.area_icon = "mdi:sofa"
        mock_device.area_last_seen_icon = "mdi:clock"
        mock_device.floor_icon = "mdi:home-floor-0"
        mock_device.address_type = "public"
        mock_device.adverts = {}
        mock_device.area_state_metadata = MagicMock(return_value={})

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.have_floors = True
        mock_coordinator.scanner_list = []
        mock_coordinator.get_scanners = []
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.sensor_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        added_entities: list = []
        mock_add_devices = MagicMock(side_effect=lambda entities, update=False: added_entities.extend(entities))

        with (
            patch("custom_components.bermuda.sensor.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            # Get the device_new callback (first dispatcher call)
            callback_func = mock_dispatcher.call_args_list[0][0][2]
            callback_func("aa:bb:cc:dd:ee:ff")

        # Should have created multiple sensors (with floors enabled: 7 sensors)
        # BermudaSensor, BermudaSensorFloor, BermudaSensorRange, BermudaSensorScanner,
        # BermudaSensorRssi, BermudaSensorAreaLastSeen, BermudaSensorAreaSwitchReason
        assert len(added_entities) >= 7
        mock_coordinator.sensor_created.assert_called_once_with("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_device_new_handles_duplicate_cleanup(self, hass: HomeAssistant) -> None:
        """Test that device_new handles duplicate entity cleanup."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = "Living Room"
        mock_device.area_id = "living_room"
        mock_device.floor_id = None
        mock_device.floor_name = None
        mock_device.area_icon = "mdi:sofa"
        mock_device.area_last_seen_icon = "mdi:clock"
        mock_device.address_type = "public"
        mock_device.adverts = {}
        mock_device.area_state_metadata = MagicMock(return_value={})

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.have_floors = False
        mock_coordinator.scanner_list = []
        mock_coordinator.get_scanners = []
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value="old:aa:bb:cc:dd:ee:ff")
        mock_coordinator.cleanup_old_entities_for_device = MagicMock()
        mock_coordinator.sensor_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        mock_add_devices = MagicMock()

        with (
            patch("custom_components.bermuda.sensor.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            callback_func = mock_dispatcher.call_args_list[0][0][2]
            callback_func("aa:bb:cc:dd:ee:ff")

        mock_coordinator.cleanup_old_entities_for_device.assert_called_once_with(
            "old:aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"
        )

    @pytest.mark.asyncio
    async def test_device_new_skips_duplicates(self, hass: HomeAssistant) -> None:
        """Test that device_new skips already-created devices."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = "Living Room"
        mock_device.area_id = "living_room"
        mock_device.floor_id = None
        mock_device.floor_name = None
        mock_device.area_icon = "mdi:sofa"
        mock_device.area_last_seen_icon = "mdi:clock"
        mock_device.address_type = "public"
        mock_device.adverts = {}
        mock_device.area_state_metadata = MagicMock(return_value={})

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.have_floors = False
        mock_coordinator.scanner_list = []
        mock_coordinator.get_scanners = []
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.sensor_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        # Track how many times entities are added
        call_count = 0

        def count_calls(entities, update=False):
            nonlocal call_count
            call_count += 1

        mock_add_devices = MagicMock(side_effect=count_calls)

        with (
            patch("custom_components.bermuda.sensor.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            callback_func = mock_dispatcher.call_args_list[0][0][2]
            callback_func("aa:bb:cc:dd:ee:ff")
            callback_func("aa:bb:cc:dd:ee:ff")

        # First call creates entities + global sensors, second should be skipped
        # But sensor_created is called twice
        assert mock_coordinator.sensor_created.call_count == 2

    @pytest.mark.asyncio
    async def test_scanners_changed_creates_scanner_entities(self, hass: HomeAssistant) -> None:
        """Test that scanners_changed callback creates scanner entities."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = "Living Room"
        mock_device.area_id = "living_room"
        mock_device.floor_id = None
        mock_device.floor_name = None
        mock_device.area_icon = "mdi:sofa"
        mock_device.area_last_seen_icon = "mdi:clock"
        mock_device.address_type = "public"
        mock_device.adverts = {}
        mock_device.area_state_metadata = MagicMock(return_value={})

        mock_scanner = MagicMock()
        mock_scanner.is_remote_scanner = False
        mock_scanner.address_wifi_mac = "11:22:33:44:55:66"
        mock_scanner.address = "11:22:33:44:55:66"
        mock_scanner.name = "Scanner Device"
        mock_scanner.unique_id = "scanner_unique_id"

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        # Include both the device and scanner in devices dict
        mock_coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": mock_device,
            "11:22:33:44:55:66": mock_scanner,
        }
        mock_coordinator.have_floors = False
        mock_coordinator.scanner_list = ["11:22:33:44:55:66"]
        mock_coordinator.get_scanners = [mock_scanner]
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.sensor_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        added_entities: list = []
        mock_add_devices = MagicMock(side_effect=lambda entities, update=False: added_entities.extend(entities))

        with (
            patch("custom_components.bermuda.sensor.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            # First create the device
            device_new_callback = mock_dispatcher.call_args_list[0][0][2]
            device_new_callback("aa:bb:cc:dd:ee:ff")

            # Get scanners_changed callback (second dispatcher call)
            scanners_changed_callback = mock_dispatcher.call_args_list[1][0][2]
            # Call it to trigger scanner entity creation
            scanners_changed_callback()

        # Should have scanner entities for the device
        assert any(isinstance(e, BermudaSensorScannerRange) for e in added_entities)
        assert any(isinstance(e, BermudaSensorScannerRangeRaw) for e in added_entities)


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


class TestRecorderBaseline:
    """
    Baseline tests for recorder database optimization (Stufe 0).

    These tests capture the CURRENT behavior of sensor entities regarding:
    - _unrecorded_attributes (which attributes are excluded from HA recorder)
    - extra_state_attributes (which attributes are exposed)
    - state_class (whether long-term statistics are generated)
    - Rate-limiting behavior (which sensors use _cached_ratelimit)

    These tests will be UPDATED in Stages 1-3 as optimizations are applied.
    When a test changes, the old assertion documents what changed.
    """

    # --- Helpers ---

    def _create_area_sensor(self, name_override: str = "Area") -> BermudaSensor:
        """Create a BermudaSensor (or subclass) for baseline testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.area_name = "Living Room"
        mock_device.area_id = "living_room"
        mock_device.floor_id = "floor1"
        mock_device.floor_name = "Ground Floor"
        mock_device.floor_level = 0
        mock_device.current_mac = "aa:bb:cc:dd:ee:ff"
        mock_device.area_distance = 2.5
        mock_device.area_state_metadata = MagicMock(
            return_value={
                "last_good_area_age_s": 1.5,
                "last_good_distance_age_s": 0.8,
                "area_retention_seconds_remaining": 898.2,
                "area_is_stale": False,
                "area_retained": False,
                "area_source": "min_distance",
            }
        )
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        # Select the appropriate sensor class
        sensor_class: type
        if name_override == "Floor":
            sensor_class = BermudaSensorFloor
        elif name_override == "Distance":
            sensor_class = BermudaSensorRange
        else:
            sensor_class = BermudaSensor

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(sensor_class)
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

    def _create_scanner_range_sensor(
        self,
        rssi_distance: float = 5.123,
    ) -> BermudaSensorScannerRange:
        """Create a BermudaSensorScannerRange for baseline testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.ref_power_changed = 0  # Explicit: prevent MagicMock truthy bypass

        mock_advert = MagicMock()
        mock_advert.rssi_distance = rssi_distance
        mock_advert.source = True  # hasattr check in extra_state_attributes
        mock_device.get_scanner = MagicMock(return_value=mock_advert)

        mock_scanner = MagicMock()
        mock_scanner.name = "Test Scanner"
        mock_scanner.address = "scanner:addr"
        mock_scanner.address_wifi_mac = None
        mock_scanner.area_id = "scanner_area"
        mock_scanner.area_name = "Scanner Room"
        mock_coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": mock_device,
            "scanner:addr": mock_scanner,
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorScannerRange)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._scanner = mock_scanner
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False
            sensor.bermuda_last_state = None
            sensor.bermuda_last_stamp = 0.0
            sensor.bermuda_update_interval = 1.0

        return sensor

    def _create_scanner_range_raw_sensor(
        self,
        rssi_distance_raw: float = 5.123,
    ) -> BermudaSensorScannerRangeRaw:
        """Create a BermudaSensorScannerRangeRaw for baseline testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.ref_power_changed = 0

        mock_advert = MagicMock()
        mock_advert.rssi_distance_raw = rssi_distance_raw
        mock_advert.source = True
        mock_device.get_scanner = MagicMock(return_value=mock_advert)

        mock_scanner = MagicMock()
        mock_scanner.name = "Test Scanner"
        mock_scanner.address = "scanner:addr"
        mock_scanner.address_wifi_mac = None
        mock_scanner.area_id = "scanner_area"
        mock_scanner.area_name = "Scanner Room"
        mock_coordinator.devices = {
            "aa:bb:cc:dd:ee:ff": mock_device,
            "scanner:addr": mock_scanner,
        }

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            sensor = object.__new__(BermudaSensorScannerRangeRaw)
            sensor.coordinator = mock_coordinator
            sensor.config_entry = mock_config_entry
            sensor.address = "aa:bb:cc:dd:ee:ff"
            sensor._device = mock_device
            sensor._scanner = mock_scanner
            sensor._lastname = mock_device.name
            sensor.ar = mock_ar.return_value
            sensor.dr = mock_dr.return_value
            sensor.devreg_init_done = False
            sensor.bermuda_last_state = None
            sensor.bermuda_last_stamp = 0.0
            sensor.bermuda_update_interval = 1.0

        return sensor

    def _create_global_sensor(self, sensor_class: type) -> object:
        """Create a global sensor for baseline testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True
        mock_coordinator.scanner_list = ["scanner1", "scanner2"]
        mock_coordinator.devices = {"dev1": MagicMock()}
        mock_coordinator.count_active_scanners = MagicMock(return_value=2)
        mock_coordinator.count_active_devices = MagicMock(return_value=5)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        sensor = object.__new__(sensor_class)
        sensor.coordinator = mock_coordinator
        sensor.config_entry = mock_entry
        # Global sensors use _cache_ratelimit_* (from BermudaGlobalEntity)
        sensor._cache_ratelimit_value = None
        sensor._cache_ratelimit_stamp = 0
        sensor._cache_ratelimit_interval = 60

        return sensor

    # --- Stage 1 Baselines: Time-based metadata currently recorded ---

    def test_area_sensor_time_metadata_excluded_from_recorder(self) -> None:
        """STAGE 1: Area sensor excludes time-based metadata from recorder.

        These 3 attributes change every coordinator cycle (~1.05s) and have
        no historical value. They are still available live in UI/automations.
        """
        sensor = self._create_area_sensor("Area")
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())

        # Stage 1: All 3 time-based attributes are excluded from recorder
        assert "last_good_area_age_s" in unrecorded
        assert "last_good_distance_age_s" in unrecorded
        assert "area_retention_seconds_remaining" in unrecorded

    def test_area_sensor_stable_attributes_not_excluded(self) -> None:
        """STAGE 1: Stable attributes are NOT excluded from recorder.

        area_is_stale, area_retained, area_source change rarely and are
        valuable for history analysis.
        """
        sensor = self._create_area_sensor("Area")
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())

        assert "area_is_stale" not in unrecorded
        assert "area_retained" not in unrecorded
        assert "area_source" not in unrecorded

    def test_area_sensor_includes_time_metadata_in_attributes(self) -> None:
        """BASELINE: Area sensor includes time-based metadata in extra_state_attributes.

        Verifies area_state_metadata() keys are present. After Stage 1, these
        keys will STILL be in extra_state_attributes (live), but excluded
        from the recorder via _unrecorded_attributes.
        """
        sensor = self._create_area_sensor("Area")
        attrs = sensor.extra_state_attributes

        # Time-based attributes (problematic for DB)
        assert "last_good_area_age_s" in attrs
        assert "last_good_distance_age_s" in attrs
        assert "area_retention_seconds_remaining" in attrs
        # Stable attributes (valuable for history)
        assert "area_is_stale" in attrs
        assert "area_retained" in attrs
        assert "area_source" in attrs

    def test_floor_sensor_time_metadata_excluded_from_recorder(self) -> None:
        """STAGE 1: Floor sensor inherits _unrecorded_attributes from BermudaSensor."""
        sensor = self._create_area_sensor("Floor")
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())

        assert "last_good_area_age_s" in unrecorded
        assert "last_good_distance_age_s" in unrecorded
        assert "area_retention_seconds_remaining" in unrecorded

    def test_floor_sensor_still_exposes_time_metadata_live(self) -> None:
        """STAGE 1: Floor sensor still has time metadata in extra_state_attributes (live).

        _unrecorded_attributes only affects the recorder DB, not live state.
        """
        sensor = self._create_area_sensor("Floor")
        attrs = sensor.extra_state_attributes

        assert "last_good_area_age_s" in attrs
        assert "last_good_distance_age_s" in attrs
        assert "area_retention_seconds_remaining" in attrs

    def test_distance_sensor_time_metadata_excluded_from_recorder(self) -> None:
        """STAGE 1: Distance sensor inherits _unrecorded_attributes from BermudaSensor."""
        sensor = self._create_area_sensor("Distance")
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())

        assert "last_good_area_age_s" in unrecorded
        assert "last_good_distance_age_s" in unrecorded
        assert "area_retention_seconds_remaining" in unrecorded

    def test_distance_sensor_still_exposes_time_metadata_live(self) -> None:
        """STAGE 1: Distance sensor still has time metadata in extra_state_attributes (live)."""
        sensor = self._create_area_sensor("Distance")
        attrs = sensor.extra_state_attributes

        assert "last_good_area_age_s" in attrs
        assert "last_good_distance_age_s" in attrs
        assert "area_retention_seconds_remaining" in attrs

    def test_area_sensor_stable_attributes_present(self) -> None:
        """BASELINE: Stable attributes (area_id, floor_*) are in extra_state_attributes.

        These should NEVER be excluded from the recorder.
        """
        sensor = self._create_area_sensor("Area")
        attrs = sensor.extra_state_attributes

        assert attrs["area_id"] == "living_room"
        assert attrs["area_name"] == "Living Room"
        assert attrs["floor_id"] == "floor1"
        assert attrs["floor_name"] == "Ground Floor"
        assert attrs["floor_level"] == 0
        assert attrs["current_mac"] == "aa:bb:cc:dd:ee:ff"

    # --- Stage 2 Baselines: Per-scanner attributes and rate-limiting ---

    def test_scanner_range_inherits_unrecorded_attributes(self) -> None:
        """STAGE 1: ScannerRange inherits time-based exclusions from BermudaSensor.

        These attributes don't appear in ScannerRange's extra_state_attributes
        (it has its own override), so this is a harmless no-op.
        After Stage 2 (recorder_friendly=True): Will be overridden with MATCH_ALL.
        """
        sensor = self._create_scanner_range_sensor()
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())
        assert "last_good_area_age_s" in unrecorded
        assert "last_good_distance_age_s" in unrecorded
        assert "area_retention_seconds_remaining" in unrecorded

    def test_scanner_range_raw_inherits_unrecorded_attributes(self) -> None:
        """STAGE 1: ScannerRangeRaw inherits time-based exclusions from BermudaSensor.

        Same as ScannerRange — harmless no-op since these attributes aren't
        in ScannerRangeRaw's extra_state_attributes.
        After Stage 2 (recorder_friendly=True): Will be overridden with MATCH_ALL.
        """
        sensor = self._create_scanner_range_raw_sensor()
        unrecorded = getattr(sensor, "_unrecorded_attributes", frozenset())
        assert "last_good_area_age_s" in unrecorded
        assert "last_good_distance_age_s" in unrecorded
        assert "area_retention_seconds_remaining" in unrecorded

    def test_scanner_range_has_extra_state_attributes(self) -> None:
        """BASELINE: ScannerRange exposes 4 scanner-specific attributes."""
        sensor = self._create_scanner_range_sensor()
        attrs = sensor.extra_state_attributes

        assert attrs is not None
        assert attrs["area_id"] == "scanner_area"
        assert attrs["area_name"] == "Scanner Room"
        assert attrs["area_scanner_mac"] == "scanner:addr"
        assert attrs["area_scanner_name"] == "Test Scanner"

    def test_scanner_range_uses_rate_limiting(self) -> None:
        """BASELINE: ScannerRange uses _cached_ratelimit() for native_value.

        Set up cache to return old value for a rising distance.
        With fast_falling=True, fast_rising=False: a rising value (5.123 > 1.0)
        does not trigger any bypass, so the cached value 1.0 is returned.
        """
        sensor = self._create_scanner_range_sensor(rssi_distance=5.123)
        # Pre-set cache: value=1.0, timestamp far future (never stale)
        sensor.bermuda_last_state = 1.0
        sensor.bermuda_last_stamp = 1e18
        sensor._device.ref_power_changed = 0

        value = sensor.native_value
        assert value == 1.0, "ScannerRange should return cached value when rate-limited"

    def test_scanner_range_raw_does_not_use_rate_limiting(self) -> None:
        """BASELINE: ScannerRangeRaw does NOT use _cached_ratelimit().

        Even with a cached value set, Raw returns the actual raw distance.
        """
        sensor = self._create_scanner_range_raw_sensor(rssi_distance_raw=5.123)
        # Same cache setup that would return 1.0 if rate-limited
        sensor.bermuda_last_state = 1.0
        sensor.bermuda_last_stamp = 1e18
        sensor._device.ref_power_changed = 0

        value = sensor.native_value
        assert value == 5.123, "ScannerRangeRaw should return actual value, not cached"

    # --- Stage 3 Baselines: state_class is currently MEASUREMENT ---

    def test_scanner_range_state_class_is_measurement(self) -> None:
        """BASELINE: ScannerRange inherits MEASUREMENT from BermudaSensorRange.

        After Stage 3 (recorder_friendly=True): Will be None.
        """
        sensor = self._create_scanner_range_sensor()
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_scanner_range_raw_state_class_is_measurement(self) -> None:
        """BASELINE: ScannerRangeRaw inherits MEASUREMENT.

        After Stage 3 (recorder_friendly=True): Will be None.
        """
        sensor = self._create_scanner_range_raw_sensor()
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_rssi_sensor_state_class_is_measurement(self) -> None:
        """BASELINE: RSSI sensor has MEASUREMENT state_class.

        After Stage 3 (recorder_friendly=True): Will be None.
        """
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True
        mock_device = MagicMock()
        mock_device.name = "Test"
        mock_device.unique_id = "uid"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
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
            sensor._device = mock_device

        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_range_sensor_state_class_is_measurement(self) -> None:
        """BASELINE: Distance sensor has MEASUREMENT state_class.

        After Stage 3 (recorder_friendly=True): Will be None.
        """
        sensor = self._create_area_sensor("Distance")
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_global_total_proxy_state_class_is_measurement(self) -> None:
        """BASELINE: Total proxy count has MEASUREMENT state_class."""
        sensor = self._create_global_sensor(BermudaTotalProxyCount)
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_global_active_proxy_state_class_is_measurement(self) -> None:
        """BASELINE: Active proxy count has MEASUREMENT state_class."""
        sensor = self._create_global_sensor(BermudaActiveProxyCount)
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_global_total_device_state_class_is_measurement(self) -> None:
        """BASELINE: Total device count has MEASUREMENT state_class."""
        sensor = self._create_global_sensor(BermudaTotalDeviceCount)
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_global_visible_device_state_class_is_measurement(self) -> None:
        """BASELINE: Visible device count has MEASUREMENT state_class."""
        sensor = self._create_global_sensor(BermudaVisibleDeviceCount)
        assert sensor.state_class == SensorStateClass.MEASUREMENT
