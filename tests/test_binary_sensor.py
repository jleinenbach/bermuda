"""Test Bermuda BLE Trilateration binary_sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.bermuda.binary_sensor import (
    BermudaScannerOnlineSensor,
    async_setup_entry,
)
from custom_components.bermuda.const import SCANNER_ACTIVITY_TIMEOUT


def _make_scanner_sensor(last_seen: float = 0.0) -> BermudaScannerOnlineSensor:
    """Create a BermudaScannerOnlineSensor instance for testing."""
    mock_device = MagicMock()
    mock_device.name = "Test Scanner"
    mock_device.unique_id = "test_scanner_id"
    mock_device.last_seen = last_seen
    mock_device.address = "aa:bb:cc:dd:ee:ff"

    mock_coordinator = MagicMock()
    mock_coordinator.data = {}
    mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
    mock_coordinator.hass = MagicMock()

    mock_config_entry = MagicMock()
    mock_config_entry.options = {}

    sensor = object.__new__(BermudaScannerOnlineSensor)
    sensor.coordinator = mock_coordinator
    sensor.config_entry = mock_config_entry
    sensor._device = mock_device

    return sensor


class TestBermudaScannerOnlineSensor:
    """Tests for BermudaScannerOnlineSensor class."""

    def test_unique_id(self) -> None:
        """Test unique_id format."""
        sensor = _make_scanner_sensor()
        assert sensor.unique_id == "test_scanner_id_scanner_online"

    def test_device_class_is_connectivity(self) -> None:
        """Test device class."""
        sensor = _make_scanner_sensor()
        assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_entity_category_is_diagnostic(self) -> None:
        """Test entity category."""
        sensor = _make_scanner_sensor()
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_translation_key(self) -> None:
        """Test translation key."""
        sensor = _make_scanner_sensor()
        assert sensor.translation_key == "scanner_online"

    def test_is_on_returns_none_when_never_seen(self) -> None:
        """Test that is_on returns None when scanner has never sent data."""
        sensor = _make_scanner_sensor(last_seen=0.0)
        assert sensor.is_on is None

    @patch("custom_components.bermuda.binary_sensor.monotonic_time_coarse")
    def test_is_on_returns_true_when_recently_seen(self, mock_time: MagicMock) -> None:
        """Test that is_on returns True when scanner recently sent data."""
        mock_time.return_value = 1000.0
        sensor = _make_scanner_sensor(last_seen=990.0)  # 10s ago
        assert sensor.is_on is True

    @patch("custom_components.bermuda.binary_sensor.monotonic_time_coarse")
    def test_is_on_returns_false_when_stale(self, mock_time: MagicMock) -> None:
        """Test that is_on returns False when scanner data is stale."""
        mock_time.return_value = 1000.0
        # Last seen 60s ago, timeout is 30s
        sensor = _make_scanner_sensor(last_seen=940.0)
        assert sensor.is_on is False

    @patch("custom_components.bermuda.binary_sensor.monotonic_time_coarse")
    def test_is_on_boundary_just_within_timeout(self, mock_time: MagicMock) -> None:
        """Test boundary: exactly at timeout minus epsilon."""
        mock_time.return_value = 1000.0
        # last_seen = 1000 - 29.9 = 970.1 -> age=29.9 < 30 -> True
        sensor = _make_scanner_sensor(last_seen=970.1)
        assert sensor.is_on is True

    @patch("custom_components.bermuda.binary_sensor.monotonic_time_coarse")
    def test_is_on_boundary_just_over_timeout(self, mock_time: MagicMock) -> None:
        """Test boundary: exactly at timeout."""
        mock_time.return_value = 1000.0
        # last_seen = 1000 - 30.0 = 970.0 -> age=30.0, NOT < 30 -> False
        sensor = _make_scanner_sensor(last_seen=970.0)
        assert sensor.is_on is False

    def test_extra_state_attributes_never_seen(self) -> None:
        """Test attributes when scanner never seen."""
        sensor = _make_scanner_sensor(last_seen=0.0)
        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["last_seen_age_seconds"] is None
        assert attrs["timeout_seconds"] == SCANNER_ACTIVITY_TIMEOUT

    @patch("custom_components.bermuda.binary_sensor.monotonic_time_coarse")
    def test_extra_state_attributes_with_data(self, mock_time: MagicMock) -> None:
        """Test attributes with actual scanner data."""
        mock_time.return_value = 1000.0
        sensor = _make_scanner_sensor(last_seen=990.0)
        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["last_seen_age_seconds"] == 10.0
        assert attrs["timeout_seconds"] == SCANNER_ACTIVITY_TIMEOUT


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_creates_sensors_for_existing_scanners(self) -> None:
        """Test that setup creates sensors for existing scanners."""
        mock_scanner = MagicMock()
        mock_scanner.address = "aa:bb:cc:dd:ee:ff"
        mock_scanner.name = "Test Scanner"
        mock_scanner.unique_id = "test_id"
        mock_scanner.last_seen = 100.0

        mock_coordinator = MagicMock()
        mock_coordinator.get_scanners = {mock_scanner}
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_scanner}

        mock_entry = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        mock_add_entities = MagicMock()

        with patch("custom_components.bermuda.binary_sensor.async_dispatcher_connect"):
            await async_setup_entry(MagicMock(), mock_entry, mock_add_entities)

        # Should have been called once with the scanner entity
        mock_add_entities.assert_called_once()
        entities = mock_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], BermudaScannerOnlineSensor)

    @pytest.mark.asyncio
    async def test_no_entities_when_no_scanners(self) -> None:
        """Test that setup creates no sensors when there are no scanners."""
        mock_coordinator = MagicMock()
        mock_coordinator.get_scanners = set()

        mock_entry = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        mock_add_entities = MagicMock()

        with patch("custom_components.bermuda.binary_sensor.async_dispatcher_connect"):
            await async_setup_entry(MagicMock(), mock_entry, mock_add_entities)

        mock_add_entities.assert_not_called()


class TestBinarySensorIntegration:
    """Integration tests for binary_sensor module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import binary_sensor

        assert hasattr(binary_sensor, "async_setup_entry")
        assert hasattr(binary_sensor, "BermudaScannerOnlineSensor")

    def test_inherits_from_correct_classes(self) -> None:
        """Test class inheritance."""
        from homeassistant.components.binary_sensor import BinarySensorEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaScannerOnlineSensor, BermudaEntity)
        assert issubclass(BermudaScannerOnlineSensor, BinarySensorEntity)
