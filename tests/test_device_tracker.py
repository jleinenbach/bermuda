"""Test Bermuda device_tracker platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.const import STATE_HOME
from homeassistant.core import HomeAssistant

from custom_components.bermuda.device_tracker import (
    BermudaDeviceTracker,
    async_setup_entry,
)


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_registers_dispatcher(self, hass: HomeAssistant) -> None:
        """Test that async_setup_entry registers a dispatcher listener."""
        mock_coordinator = MagicMock()
        mock_coordinator.devices = {}

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()

        mock_add_devices = MagicMock()

        with patch("custom_components.bermuda.device_tracker.async_dispatcher_connect") as mock_dispatcher:
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        mock_dispatcher.assert_called_once()
        mock_entry.async_on_unload.assert_called_once()


class TestBermudaDeviceTracker:
    """Tests for BermudaDeviceTracker class."""

    def _create_tracker(
        self,
        zone: str = STATE_HOME,
        area_name: str | None = "Living Room",
        area_advert: MagicMock | None = None,
    ) -> BermudaDeviceTracker:
        """Create a BermudaDeviceTracker instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.zone = zone
        mock_device.area_name = area_name
        mock_device.area_advert = area_advert
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            tracker = object.__new__(BermudaDeviceTracker)
            tracker.coordinator = mock_coordinator
            tracker.config_entry = mock_config_entry
            tracker.address = "aa:bb:cc:dd:ee:ff"
            tracker._device = mock_device
            tracker._lastname = mock_device.name
            tracker.ar = mock_ar.return_value
            tracker.dr = mock_dr.return_value
            tracker.devreg_init_done = False

        return tracker

    def test_tracker_has_correct_attributes(self) -> None:
        """Test that tracker has correct entity attributes."""
        # Create an instance to test attributes (HA metaclass converts class attrs to properties)
        tracker = self._create_tracker()
        assert tracker.should_poll is False
        assert tracker.has_entity_name is True
        assert tracker.name == "Bermuda Tracker"

    def test_unique_id(self) -> None:
        """Test that unique_id is correct."""
        tracker = self._create_tracker()
        assert tracker.unique_id == "test_unique_id"

    def test_state_returns_zone(self) -> None:
        """Test that state returns the device zone."""
        tracker = self._create_tracker(zone=STATE_HOME)
        assert tracker.state == STATE_HOME

    def test_source_type(self) -> None:
        """Test that source_type is BLUETOOTH_LE."""
        tracker = self._create_tracker()
        assert tracker.source_type == SourceType.BLUETOOTH_LE

    def test_icon_when_home(self) -> None:
        """Test icon when device is home."""
        tracker = self._create_tracker(zone=STATE_HOME)
        assert tracker.icon == "mdi:bluetooth-connect"

    def test_icon_when_not_home(self) -> None:
        """Test icon when device is not home."""
        tracker = self._create_tracker(zone="not_home")
        assert tracker.icon == "mdi:bluetooth-off"

    def test_extra_state_attributes_with_advert(self) -> None:
        """Test extra_state_attributes when advert exists."""
        mock_advert = MagicMock()
        mock_advert.name = "Living Room Scanner"
        tracker = self._create_tracker(area_name="Kitchen", area_advert=mock_advert)

        attrs = tracker.extra_state_attributes
        assert attrs["scanner"] == "Living Room Scanner"
        assert attrs["area"] == "Kitchen"

    def test_extra_state_attributes_without_advert(self) -> None:
        """Test extra_state_attributes when no advert."""
        tracker = self._create_tracker(area_name="Kitchen", area_advert=None)

        attrs = tracker.extra_state_attributes
        assert attrs["scanner"] is None
        assert attrs["area"] == "Kitchen"


class TestDeviceTrackerIntegration:
    """Integration tests for device_tracker module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import device_tracker

        assert hasattr(device_tracker, "async_setup_entry")
        assert hasattr(device_tracker, "BermudaDeviceTracker")

    def test_tracker_inherits_from_correct_classes(self) -> None:
        """Test that BermudaDeviceTracker inherits from required base classes."""
        from homeassistant.components.device_tracker.config_entry import BaseTrackerEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaDeviceTracker, BermudaEntity)
        assert issubclass(BermudaDeviceTracker, BaseTrackerEntity)
