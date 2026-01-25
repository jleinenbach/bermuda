"""Test Bermuda button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.bermuda.button import (
    TRAINING_MAX_TIME_SECONDS,
    TRAINING_POLL_INTERVAL,
    TRAINING_SAMPLE_COUNT,
    BermudaResetTrainingButton,
    BermudaTrainingButton,
    async_setup_entry,
)
from custom_components.bermuda.const import DOMAIN


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry_registers_dispatcher(self, hass: HomeAssistant) -> None:
        """Test that async_setup_entry registers a dispatcher listener."""
        mock_coordinator = MagicMock()
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()

        mock_add_devices = MagicMock()

        with patch("custom_components.bermuda.button.async_dispatcher_connect") as mock_dispatcher:
            await async_setup_entry(hass, mock_entry, mock_add_devices)

        mock_dispatcher.assert_called_once()
        mock_entry.async_on_unload.assert_called_once()


class TestBermudaTrainingButton:
    """Tests for BermudaTrainingButton class."""

    def _create_button(
        self,
        training_floor_id: str | None = None,
        training_area_id: str | None = None,
        has_adverts: bool = True,
    ) -> BermudaTrainingButton:
        """Create a BermudaTrainingButton instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = training_floor_id
        mock_device.training_target_area_id = training_area_id
        mock_device.area_locked_name = None
        mock_device.adverts = {"scanner1": MagicMock()} if has_adverts else {}
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        # Create the button without calling __init__
        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            button = object.__new__(BermudaTrainingButton)
            button.coordinator = mock_coordinator
            button.config_entry = mock_config_entry
            button.address = "aa:bb:cc:dd:ee:ff"
            button._device = mock_device
            button._lastname = mock_device.name
            button.ar = mock_ar.return_value
            button.dr = mock_dr.return_value
            button.devreg_init_done = False
            button._is_training = False
            button._attr_icon = BermudaTrainingButton.ICON_IDLE
            button.async_write_ha_state = MagicMock()

        return button

    def test_button_has_correct_attributes(self) -> None:
        """Test that button has correct entity attributes."""
        # Check class __dict__ to verify attributes are defined on the class itself
        assert BermudaTrainingButton.__dict__.get("_attr_should_poll") is False
        assert BermudaTrainingButton._attr_has_entity_name is True
        assert BermudaTrainingButton._attr_translation_key == "training_learn"
        assert BermudaTrainingButton._attr_entity_category == EntityCategory.CONFIG

    def test_button_icons(self) -> None:
        """Test that button has correct icons defined."""
        assert BermudaTrainingButton.ICON_IDLE == "mdi:brain"
        assert BermudaTrainingButton.ICON_TRAINING == "mdi:timer-sand"

    def test_available_returns_false_when_no_floor_selected(self) -> None:
        """Test that button is unavailable when no floor is selected."""
        button = self._create_button(training_floor_id=None, training_area_id="area1")

        assert button.available is False

    def test_available_returns_false_when_no_area_selected(self) -> None:
        """Test that button is unavailable when no area is selected."""
        button = self._create_button(training_floor_id="floor1", training_area_id=None)

        assert button.available is False

    def test_available_returns_true_when_floor_and_area_selected(self) -> None:
        """Test that button is available when floor and area are selected."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        assert button.available is True

    def test_available_returns_false_when_training(self) -> None:
        """Test that button is unavailable during training."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")
        button._is_training = True

        assert button.available is False

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        button = self._create_button()

        assert button.unique_id == "test_unique_id_training_learn"

    @pytest.mark.asyncio
    async def test_async_press_returns_early_when_training(self) -> None:
        """Test that async_press returns early if already training."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")
        button._is_training = True

        await button.async_press()

        # Button should not have changed state
        assert button._is_training is True

    @pytest.mark.asyncio
    async def test_async_press_returns_early_when_no_room_selected(self) -> None:
        """Test that async_press returns early if no room selected."""
        button = self._create_button(training_area_id=None)

        await button.async_press()

        # Training should not have started
        assert button._is_training is False

    @pytest.mark.asyncio
    async def test_async_press_returns_early_when_no_adverts(self) -> None:
        """Test that async_press returns early if no adverts available."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1", has_adverts=False)

        await button.async_press()

        # Training should not have started
        assert button._is_training is False

    def test_handle_coordinator_update_updates_state(self) -> None:
        """Test that _handle_coordinator_update updates entity state."""
        button = self._create_button()

        # Call the method - it should trigger state update
        button._handle_coordinator_update()

        # Verify state update was triggered
        button.async_write_ha_state.assert_called()


class TestBermudaResetTrainingButton:
    """Tests for BermudaResetTrainingButton class."""

    def _create_button(self) -> BermudaResetTrainingButton:
        """Create a BermudaResetTrainingButton instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.async_reset_device_training = AsyncMock(return_value=True)
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

        # Create the button without calling __init__
        with (
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            button = object.__new__(BermudaResetTrainingButton)
            button.coordinator = mock_coordinator
            button.config_entry = mock_config_entry
            button.address = "aa:bb:cc:dd:ee:ff"
            button._device = mock_device
            button._lastname = mock_device.name
            button.ar = mock_ar.return_value
            button.dr = mock_dr.return_value
            button.devreg_init_done = False

        return button

    def test_button_has_correct_attributes(self) -> None:
        """Test that button has correct entity attributes."""
        # Check class __dict__ to verify attributes are defined on the class itself
        assert BermudaResetTrainingButton.__dict__.get("_attr_should_poll") is False
        assert BermudaResetTrainingButton._attr_has_entity_name is True
        assert BermudaResetTrainingButton._attr_translation_key == "reset_training"
        assert BermudaResetTrainingButton._attr_entity_category == EntityCategory.CONFIG
        assert BermudaResetTrainingButton._attr_icon == "mdi:eraser"

    def test_unique_id(self) -> None:
        """Test that unique_id is correctly formatted."""
        button = self._create_button()

        assert button.unique_id == "test_unique_id_reset_training"

    @pytest.mark.asyncio
    async def test_async_press_calls_reset_device_training(self) -> None:
        """Test that async_press calls coordinator's reset method."""
        button = self._create_button()

        await button.async_press()

        button.coordinator.async_reset_device_training.assert_called_once_with("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_async_press_triggers_refresh(self) -> None:
        """Test that async_press triggers coordinator refresh."""
        button = self._create_button()

        await button.async_press()

        button.coordinator.async_request_refresh.assert_called_once()


class TestTrainingConstants:
    """Tests for training constants."""

    def test_training_sample_count(self) -> None:
        """Test that training sample count is reasonable."""
        # 60 samples provides good statistical significance
        assert TRAINING_SAMPLE_COUNT == 60
        assert TRAINING_SAMPLE_COUNT > 30  # CLT threshold

    def test_training_max_time(self) -> None:
        """Test that training max time is reasonable."""
        # 300 seconds (5 minutes) should be enough for 60 samples
        assert TRAINING_MAX_TIME_SECONDS == 300.0

    def test_training_poll_interval(self) -> None:
        """Test that poll interval is reasonable."""
        # 0.3 seconds for responsive polling
        assert TRAINING_POLL_INTERVAL == 0.3


class TestButtonIntegration:
    """Integration tests for button module."""

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda import button

        assert hasattr(button, "async_setup_entry")
        assert hasattr(button, "BermudaTrainingButton")
        assert hasattr(button, "BermudaResetTrainingButton")

    def test_training_button_inherits_from_correct_classes(self) -> None:
        """Test that BermudaTrainingButton inherits from required base classes."""
        from homeassistant.components.button import ButtonEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaTrainingButton, BermudaEntity)
        assert issubclass(BermudaTrainingButton, ButtonEntity)

    def test_reset_button_inherits_from_correct_classes(self) -> None:
        """Test that BermudaResetTrainingButton inherits from required base classes."""
        from homeassistant.components.button import ButtonEntity

        from custom_components.bermuda.entity import BermudaEntity

        assert issubclass(BermudaResetTrainingButton, BermudaEntity)
        assert issubclass(BermudaResetTrainingButton, ButtonEntity)
