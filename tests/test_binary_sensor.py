"""Test Bermuda BLE Trilateration binary_sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.binary_sensor import (
    BermudaBinarySensor,
    async_setup_entry,
)
from custom_components.bermuda.const import BINARY_SENSOR_DEVICE_CLASS, DEFAULT_NAME


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_does_not_add_entities(
        self, hass: HomeAssistant
    ) -> None:
        """Test that async_setup_entry does not add any entities (currently disabled)."""
        mock_entry = MagicMock()
        mock_add_devices = MagicMock()

        await async_setup_entry(hass, mock_entry, mock_add_devices)

        # Currently the function is a no-op (entities are commented out)
        mock_add_devices.assert_not_called()


class TestBermudaBinarySensor:
    """Tests for BermudaBinarySensor class."""

    def _create_sensor(self) -> BermudaBinarySensor:
        """Create a BermudaBinarySensor instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.data = {}
        mock_coordinator.devices = {"test_address": MagicMock()}
        mock_coordinator.hass = MagicMock()

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_coordinator.devices["test_address"] = mock_device

        # Create the sensor without calling __init__ to avoid CoordinatorEntity complexity
        sensor = object.__new__(BermudaBinarySensor)
        sensor.coordinator = mock_coordinator
        sensor.config_entry = mock_config_entry
        sensor._device = mock_device

        return sensor

    def test_name_property(self) -> None:
        """Test that name property returns correct format."""
        sensor = self._create_sensor()

        name = sensor.name

        assert name == f"{DEFAULT_NAME}_binary_sensor"

    def test_device_class_property(self) -> None:
        """Test that device_class property returns correct value."""
        sensor = self._create_sensor()

        device_class = sensor.device_class

        assert device_class == BINARY_SENSOR_DEVICE_CLASS

    def test_is_on_property_returns_true(self) -> None:
        """Test that is_on property returns True (default implementation)."""
        sensor = self._create_sensor()

        is_on = sensor.is_on

        assert is_on is True


class TestBinarySensorIntegration:
    """Integration tests for binary_sensor module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import binary_sensor

        assert hasattr(binary_sensor, "async_setup_entry")
        assert hasattr(binary_sensor, "BermudaBinarySensor")

    def test_binary_sensor_inherits_from_correct_classes(self) -> None:
        """Test that BermudaBinarySensor inherits from required base classes."""
        from homeassistant.components.binary_sensor import BinarySensorEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaBinarySensor, BermudaEntity)
        assert issubclass(BermudaBinarySensor, BinarySensorEntity)
