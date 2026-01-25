"""Test Bermuda select platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.bermuda.select import (
    BermudaTrainingFloorSelect,
    BermudaTrainingRoomSelect,
    async_setup_entry,
)


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_registers_dispatcher(self, hass: HomeAssistant) -> None:
        """Test that async_setup_entry registers a dispatcher listener."""
        mock_coordinator = MagicMock()
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)
        mock_coordinator.select_created = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()

        mock_add_devices = MagicMock()

        with patch("custom_components.bermuda.select.async_dispatcher_connect") as mock_dispatcher:
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        mock_dispatcher.assert_called_once()
        mock_entry.async_on_unload.assert_called_once()


class TestBermudaTrainingRoomSelect:
    """Tests for BermudaTrainingRoomSelect class."""

    def _create_room_select(
        self,
        training_area_id: str | None = None,
        floor_override_id: str | None = None,
    ) -> BermudaTrainingRoomSelect:
        """Create a BermudaTrainingRoomSelect instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_area_id = training_area_id
        mock_device.area_advert = None
        mock_device.adverts = {}
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        mock_floor_select = MagicMock()
        mock_floor_select.floor_override_id = floor_override_id

        # Create the select without calling __init__
        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar_entity,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
            patch("custom_components.bermuda.select.ar.async_get") as mock_ar_select,
        ):
            mock_ar_entity.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            # Create mock areas
            mock_area1 = MagicMock()
            mock_area1.name = "Living Room"
            mock_area1.id = "living_room"
            mock_area1.floor_id = "floor1"

            mock_area2 = MagicMock()
            mock_area2.name = "Kitchen"
            mock_area2.id = "kitchen"
            mock_area2.floor_id = "floor1"

            mock_area3 = MagicMock()
            mock_area3.name = "Bedroom"
            mock_area3.id = "bedroom"
            mock_area3.floor_id = "floor2"

            mock_area_registry = MagicMock()
            mock_area_registry.async_list_areas.return_value = [
                mock_area1,
                mock_area2,
                mock_area3,
            ]
            mock_ar_select.return_value = mock_area_registry

            select = object.__new__(BermudaTrainingRoomSelect)
            select.coordinator = mock_coordinator
            select.config_entry = mock_config_entry
            select.address = "aa:bb:cc:dd:ee:ff"
            select._device = mock_device
            select._lastname = mock_device.name
            select.ar = mock_ar_entity.return_value
            select.dr = mock_dr.return_value
            select.devreg_init_done = False
            select._area_registry = mock_area_registry
            select._floor_select = mock_floor_select
            select._room_override_name = None
            select._room_override_id = None
            select.async_write_ha_state = MagicMock()

        return select

    def test_room_select_has_correct_attributes(self) -> None:
        """Test that room select has correct entity attributes."""
        assert BermudaTrainingRoomSelect._attr_should_poll is False
        assert BermudaTrainingRoomSelect._attr_has_entity_name is True
        assert BermudaTrainingRoomSelect._attr_translation_key == "training_room"
        assert BermudaTrainingRoomSelect._attr_entity_category == EntityCategory.CONFIG
        assert BermudaTrainingRoomSelect._attr_icon == "mdi:map-marker-radius"

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        select = self._create_room_select()

        assert select.unique_id == "test_unique_id_training_room"

    def test_current_option_returns_none_initially(self) -> None:
        """Test that current_option returns None initially."""
        select = self._create_room_select()

        assert select.current_option is None

    def test_current_option_returns_override_when_set(self) -> None:
        """Test that current_option returns override when set."""
        select = self._create_room_select()
        select._room_override_name = "Living Room"

        assert select.current_option == "Living Room"

    def test_options_returns_all_areas_when_no_floor_selected(self) -> None:
        """Test that options returns all areas when no floor is selected."""
        select = self._create_room_select(floor_override_id=None)

        options = select.options

        assert "Living Room" in options
        assert "Kitchen" in options
        assert "Bedroom" in options

    def test_options_filters_by_floor_when_selected(self) -> None:
        """Test that options filters areas by floor when floor is selected."""
        select = self._create_room_select(floor_override_id="floor1")

        options = select.options

        assert "Living Room" in options
        assert "Kitchen" in options
        assert "Bedroom" not in options

    def test_effective_floor_id_returns_floor_override(self) -> None:
        """Test that _effective_floor_id returns floor override."""
        select = self._create_room_select(floor_override_id="floor1")

        assert select._effective_floor_id == "floor1"

    def test_effective_floor_id_returns_none_when_no_override(self) -> None:
        """Test that _effective_floor_id returns None when no override."""
        select = self._create_room_select(floor_override_id=None)

        assert select._effective_floor_id is None

    @pytest.mark.asyncio
    async def test_async_select_option_sets_device_attributes(self) -> None:
        """Test that async_select_option sets device attributes."""
        select = self._create_room_select()

        await select.async_select_option("Living Room")

        assert select._device.training_target_area_id == "living_room"
        assert select._device.area_locked_id == "living_room"
        assert select._device.area_locked_name == "Living Room"

    @pytest.mark.asyncio
    async def test_async_select_option_sets_local_state(self) -> None:
        """Test that async_select_option sets local state."""
        select = self._create_room_select()

        await select.async_select_option("Living Room")

        assert select._room_override_name == "Living Room"
        assert select._room_override_id == "living_room"

    @pytest.mark.asyncio
    async def test_async_select_option_triggers_refresh(self) -> None:
        """Test that async_select_option triggers coordinator refresh."""
        select = self._create_room_select()

        await select.async_select_option("Living Room")

        select.coordinator.async_request_refresh.assert_called_once()

    def test_on_floor_changed_calls_async_write_ha_state(self) -> None:
        """Test that on_floor_changed calls async_write_ha_state."""
        select = self._create_room_select()

        select.on_floor_changed()

        select.async_write_ha_state.assert_called_once()

    def test_handle_coordinator_update_clears_room_when_target_cleared(self) -> None:
        """Test that _handle_coordinator_update clears room when target is cleared."""
        select = self._create_room_select(training_area_id=None)
        select._room_override_name = "Living Room"
        select._room_override_id = "living_room"

        with patch.object(
            BermudaTrainingRoomSelect.__bases__[0],
            "_handle_coordinator_update",
        ):
            select._handle_coordinator_update()

        assert select._room_override_name is None
        assert select._room_override_id is None


class TestBermudaTrainingFloorSelect:
    """Tests for BermudaTrainingFloorSelect class."""

    def _create_floor_select(
        self,
        training_floor_id: str | None = None,
    ) -> BermudaTrainingFloorSelect:
        """Create a BermudaTrainingFloorSelect instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = training_floor_id
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        # Create the select without calling __init__
        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
            patch("custom_components.bermuda.select.fr.async_get") as mock_fr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            # Create mock floors
            mock_floor1 = MagicMock()
            mock_floor1.name = "Ground Floor"
            mock_floor1.floor_id = "ground"
            mock_floor1.level = 0

            mock_floor2 = MagicMock()
            mock_floor2.name = "First Floor"
            mock_floor2.floor_id = "first"
            mock_floor2.level = 1

            mock_floor_registry = MagicMock()
            mock_floor_registry.async_list_floors.return_value = [
                mock_floor1,
                mock_floor2,
            ]
            mock_fr.return_value = mock_floor_registry

            select = object.__new__(BermudaTrainingFloorSelect)
            select.coordinator = mock_coordinator
            select.config_entry = mock_config_entry
            select.address = "aa:bb:cc:dd:ee:ff"
            select._device = mock_device
            select._lastname = mock_device.name
            select.ar = mock_ar.return_value
            select.dr = mock_dr.return_value
            select.devreg_init_done = False
            select._floor_registry = mock_floor_registry
            select.floor_override_id = None
            select._floor_override_name = None
            select._room_select = None
            select.async_write_ha_state = MagicMock()

        return select

    def test_floor_select_has_correct_attributes(self) -> None:
        """Test that floor select has correct entity attributes."""
        assert BermudaTrainingFloorSelect._attr_should_poll is False
        assert BermudaTrainingFloorSelect._attr_has_entity_name is True
        assert BermudaTrainingFloorSelect._attr_translation_key == "training_floor"
        assert BermudaTrainingFloorSelect._attr_entity_category == EntityCategory.CONFIG
        assert BermudaTrainingFloorSelect._attr_icon == "mdi:floor-plan"

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        select = self._create_floor_select()

        assert select.unique_id == "test_unique_id_training_floor"

    def test_current_option_returns_none_initially(self) -> None:
        """Test that current_option returns None initially."""
        select = self._create_floor_select()

        assert select.current_option is None

    def test_current_option_returns_override_when_set(self) -> None:
        """Test that current_option returns override when set."""
        select = self._create_floor_select()
        select._floor_override_name = "Ground Floor"

        assert select.current_option == "Ground Floor"

    def test_options_returns_sorted_floors(self) -> None:
        """Test that options returns floors sorted by level."""
        select = self._create_floor_select()

        options = select.options

        assert options == ["Ground Floor", "First Floor"]

    def test_set_room_select(self) -> None:
        """Test that set_room_select sets the room select reference."""
        select = self._create_floor_select()
        mock_room_select = MagicMock()

        select.set_room_select(mock_room_select)

        assert select._room_select is mock_room_select

    @pytest.mark.asyncio
    async def test_async_select_option_sets_device_attributes(self) -> None:
        """Test that async_select_option sets device attributes."""
        select = self._create_floor_select()

        await select.async_select_option("Ground Floor")

        assert select._device.training_target_floor_id == "ground"

    @pytest.mark.asyncio
    async def test_async_select_option_sets_local_state(self) -> None:
        """Test that async_select_option sets local state."""
        select = self._create_floor_select()

        await select.async_select_option("Ground Floor")

        assert select._floor_override_name == "Ground Floor"
        assert select.floor_override_id == "ground"

    @pytest.mark.asyncio
    async def test_async_select_option_notifies_room_select(self) -> None:
        """Test that async_select_option notifies room select."""
        select = self._create_floor_select()
        mock_room_select = MagicMock()
        select._room_select = mock_room_select

        await select.async_select_option("Ground Floor")

        mock_room_select.on_floor_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_select_option_triggers_refresh(self) -> None:
        """Test that async_select_option triggers coordinator refresh."""
        select = self._create_floor_select()

        await select.async_select_option("Ground Floor")

        select.coordinator.async_request_refresh.assert_called_once()

    def test_handle_coordinator_update_clears_floor_when_target_cleared(self) -> None:
        """Test that _handle_coordinator_update clears floor when target is cleared."""
        select = self._create_floor_select(training_floor_id=None)
        select._floor_override_name = "Ground Floor"
        select.floor_override_id = "ground"

        with patch.object(
            BermudaTrainingFloorSelect.__bases__[0],
            "_handle_coordinator_update",
        ):
            select._handle_coordinator_update()

        assert select._floor_override_name is None
        assert select.floor_override_id is None


class TestSelectIntegration:
    """Integration tests for select module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import select

        assert hasattr(select, "async_setup_entry")
        assert hasattr(select, "BermudaTrainingRoomSelect")
        assert hasattr(select, "BermudaTrainingFloorSelect")

    def test_room_select_inherits_from_correct_classes(self) -> None:
        """Test that BermudaTrainingRoomSelect inherits from required base classes."""
        from homeassistant.components.select import SelectEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaTrainingRoomSelect, BermudaEntity)
        assert issubclass(BermudaTrainingRoomSelect, SelectEntity)

    def test_floor_select_inherits_from_correct_classes(self) -> None:
        """Test that BermudaTrainingFloorSelect inherits from required base classes."""
        from homeassistant.components.select import SelectEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaTrainingFloorSelect, BermudaEntity)
        assert issubclass(BermudaTrainingFloorSelect, SelectEntity)
