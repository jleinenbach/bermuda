"""Test Bermuda number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.bermuda.number import (
    BermudaNumber,
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

        with patch("custom_components.bermuda.number.async_dispatcher_connect") as mock_dispatcher:
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        mock_dispatcher.assert_called_once()
        mock_entry.async_on_unload.assert_called_once()


class TestBermudaNumber:
    """Tests for BermudaNumber class."""

    def _create_number(self, ref_power: float | None = -65.0) -> BermudaNumber:
        """Create a BermudaNumber instance for testing."""
        mock_coordinator = MagicMock()
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.ref_power = ref_power
        mock_device.set_ref_power = MagicMock()
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            number = object.__new__(BermudaNumber)
            number.coordinator = mock_coordinator
            number.config_entry = mock_config_entry
            number.address = "aa:bb:cc:dd:ee:ff"
            number._device = mock_device
            number._lastname = mock_device.name
            number.ar = mock_ar.return_value
            number.dr = mock_dr.return_value
            number.devreg_init_done = False
            number.restored_data = None
            number.async_write_ha_state = MagicMock()

        return number

    def test_number_has_correct_attributes(self) -> None:
        """Test that number has correct entity attributes."""
        # Create an instance to test attributes (HA metaclass converts class attrs to properties)
        number = self._create_number()
        assert number.should_poll is False
        assert number.has_entity_name is True
        assert number.translation_key == "ref_power"
        assert number.device_class == NumberDeviceClass.SIGNAL_STRENGTH
        assert number.entity_category == EntityCategory.CONFIG
        assert number.native_min_value == -127
        assert number.native_max_value == 0
        assert number.native_step == 1
        assert number.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        assert number.mode == NumberMode.BOX

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        number = self._create_number()
        assert number.unique_id == "test_unique_id_ref_power"

    def test_native_value_returns_ref_power(self) -> None:
        """Test that native_value returns ref_power from device."""
        number = self._create_number(ref_power=-60.0)
        assert number.native_value == -60.0

    def test_native_value_returns_none_when_no_ref_power(self) -> None:
        """Test that native_value returns None when ref_power is None."""
        number = self._create_number(ref_power=None)
        assert number.native_value is None

    @pytest.mark.asyncio
    async def test_async_set_native_value_calls_set_ref_power(self) -> None:
        """Test that async_set_native_value calls set_ref_power on device."""
        number = self._create_number()

        await number.async_set_native_value(-70.0)

        number._device.set_ref_power.assert_called_once_with(-70.0)

    @pytest.mark.asyncio
    async def test_async_set_native_value_updates_state(self) -> None:
        """Test that async_set_native_value calls async_write_ha_state."""
        number = self._create_number()

        await number.async_set_native_value(-70.0)

        number.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_restores_value(self) -> None:
        """Test that async_added_to_hass restores saved value."""
        number = self._create_number()

        mock_sensor_data = MagicMock()
        mock_sensor_data.native_value = -55.0
        number.async_get_last_number_data = AsyncMock(return_value=mock_sensor_data)

        # Mock the parent class method
        with patch.object(BermudaNumber.__bases__[0], "async_added_to_hass", new_callable=AsyncMock):
            await number.async_added_to_hass()

        number._device.set_ref_power.assert_called_once_with(-55.0)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_handles_no_restored_data(self) -> None:
        """Test that async_added_to_hass handles missing restored data."""
        number = self._create_number()

        number.async_get_last_number_data = AsyncMock(return_value=None)

        # Mock the parent class method
        with patch.object(BermudaNumber.__bases__[0], "async_added_to_hass", new_callable=AsyncMock):
            await number.async_added_to_hass()

        # Should not call set_ref_power when no restored data
        number._device.set_ref_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_handles_none_native_value(self) -> None:
        """Test that async_added_to_hass handles None native_value."""
        number = self._create_number()

        mock_sensor_data = MagicMock()
        mock_sensor_data.native_value = None
        number.async_get_last_number_data = AsyncMock(return_value=mock_sensor_data)

        # Mock the parent class method
        with patch.object(BermudaNumber.__bases__[0], "async_added_to_hass", new_callable=AsyncMock):
            await number.async_added_to_hass()

        # Should not call set_ref_power when native_value is None
        number._device.set_ref_power.assert_not_called()


class TestDeviceNewCallback:
    """Tests for the device_new callback in async_setup_entry."""

    @pytest.mark.asyncio
    async def test_device_new_creates_number_entity(self, hass: HomeAssistant) -> None:
        """Test that device_new callback creates number entity."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.ref_power = -65.0

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.number_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        added_entities: list = []
        mock_add_devices = MagicMock(side_effect=lambda entities, _: added_entities.extend(entities))

        with (
            patch("custom_components.bermuda.number.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            callback_func = mock_dispatcher.call_args[0][2]
            callback_func("aa:bb:cc:dd:ee:ff")

        assert len(added_entities) == 1
        assert isinstance(added_entities[0], BermudaNumber)
        mock_coordinator.number_created.assert_called_once_with("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_device_new_handles_duplicate_cleanup(self, hass: HomeAssistant) -> None:
        """Test that device_new handles duplicate entity cleanup."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.ref_power = -65.0

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value="old:aa:bb:cc:dd:ee:ff")
        mock_coordinator.cleanup_old_entities_for_device = MagicMock()
        mock_coordinator.number_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        mock_add_devices = MagicMock()

        with (
            patch("custom_components.bermuda.number.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            callback_func = mock_dispatcher.call_args[0][2]
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
        mock_device.ref_power = -65.0

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.number_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        call_count = 0

        def track_calls(entities, _):
            nonlocal call_count
            call_count += 1

        mock_add_devices = MagicMock(side_effect=track_calls)

        with (
            patch("custom_components.bermuda.number.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            callback_func = mock_dispatcher.call_args[0][2]
            callback_func("aa:bb:cc:dd:ee:ff")
            callback_func("aa:bb:cc:dd:ee:ff")

        assert call_count == 1


class TestNumberIntegration:
    """Integration tests for number module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import number

        assert hasattr(number, "async_setup_entry")
        assert hasattr(number, "BermudaNumber")

    def test_number_inherits_from_correct_classes(self) -> None:
        """Test that BermudaNumber inherits from required base classes."""
        from homeassistant.components.number import RestoreNumber

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaNumber, BermudaEntity)
        assert issubclass(BermudaNumber, RestoreNumber)
