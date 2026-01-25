"""Test Bermuda BLE Trilateration switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.const import DEFAULT_NAME, ICON
from custom_components.bermuda.switch import (
    SWITCH,
    BermudaBinarySwitch,
    async_setup_entry,
)


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


class TestBermudaBinarySwitch:
    """Tests for BermudaBinarySwitch class."""

    def _create_switch(self) -> BermudaBinarySwitch:
        """Create a BermudaBinarySwitch instance for testing."""
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

        # Create the switch without calling __init__ to avoid CoordinatorEntity complexity
        switch = object.__new__(BermudaBinarySwitch)
        switch.coordinator = mock_coordinator
        switch.config_entry = mock_config_entry
        switch._device = mock_device

        return switch

    def test_name_property(self) -> None:
        """Test that name property returns correct format."""
        switch = self._create_switch()

        name = switch.name

        assert name == f"{DEFAULT_NAME}_{SWITCH}"

    def test_icon_property(self) -> None:
        """Test that icon property returns correct value."""
        switch = self._create_switch()

        icon = switch.icon

        assert icon == ICON

    def test_is_on_property_returns_true(self) -> None:
        """Test that is_on property returns True (default implementation)."""
        switch = self._create_switch()

        is_on = switch.is_on

        assert is_on is True

    @pytest.mark.asyncio
    async def test_async_turn_on_is_noop(self) -> None:
        """Test that async_turn_on is currently a no-op."""
        switch = self._create_switch()

        # Should not raise any exceptions
        await switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_async_turn_off_is_noop(self) -> None:
        """Test that async_turn_off is currently a no-op."""
        switch = self._create_switch()

        # Should not raise any exceptions
        await switch.async_turn_off()

    @pytest.mark.asyncio
    async def test_async_turn_on_with_kwargs(self) -> None:
        """Test that async_turn_on accepts kwargs."""
        switch = self._create_switch()

        # Should not raise any exceptions with kwargs
        await switch.async_turn_on(brightness=100, transition=5)

    @pytest.mark.asyncio
    async def test_async_turn_off_with_kwargs(self) -> None:
        """Test that async_turn_off accepts kwargs."""
        switch = self._create_switch()

        # Should not raise any exceptions with kwargs
        await switch.async_turn_off(transition=5)


class TestSwitchIntegration:
    """Integration tests for switch module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import switch

        assert hasattr(switch, "async_setup_entry")
        assert hasattr(switch, "BermudaBinarySwitch")
        assert hasattr(switch, "SWITCH")

    def test_switch_inherits_from_correct_classes(self) -> None:
        """Test that BermudaBinarySwitch inherits from required base classes."""
        from homeassistant.components.switch import SwitchEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaBinarySwitch, BermudaEntity)
        assert issubclass(BermudaBinarySwitch, SwitchEntity)

    def test_switch_constant_value(self) -> None:
        """Test that SWITCH constant has correct value."""
        assert SWITCH == "switch"
