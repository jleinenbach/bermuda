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
        # Create an instance to test attributes (HA metaclass converts class attrs to properties)
        button = self._create_button()
        assert button.should_poll is False
        assert button.has_entity_name is True
        assert button.translation_key == "training_learn"
        assert button.entity_category == EntityCategory.CONFIG

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
        # Create an instance to test attributes (HA metaclass converts class attrs to properties)
        button = self._create_button()
        assert button.should_poll is False
        assert button.has_entity_name is True
        assert button.translation_key == "reset_training"
        assert button.entity_category == EntityCategory.CONFIG
        assert button.icon == "mdi:eraser"

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


class TestDeviceNewCallback:
    """Tests for the device_new callback in async_setup_entry."""

    @pytest.mark.asyncio
    async def test_device_new_creates_both_buttons(self, hass: HomeAssistant) -> None:
        """Test that device_new callback creates training and reset buttons."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = None
        mock_device.training_target_area_id = None
        mock_device.area_locked_name = None
        mock_device.adverts = {}

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        added_entities: list = []
        mock_add_devices = MagicMock(side_effect=lambda entities, _: added_entities.extend(entities))

        # Set up the entry, which registers the dispatcher
        with (
            patch("custom_components.bermuda.button.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            # Get the callback that was passed to dispatcher
            callback_func = mock_dispatcher.call_args[0][2]

            # Trigger the callback
            callback_func("aa:bb:cc:dd:ee:ff")

        # Verify both button types were created
        assert len(added_entities) == 2
        assert any(isinstance(e, BermudaTrainingButton) for e in added_entities)
        assert any(isinstance(e, BermudaResetTrainingButton) for e in added_entities)

    @pytest.mark.asyncio
    async def test_device_new_handles_duplicate_cleanup(self, hass: HomeAssistant) -> None:
        """Test that device_new handles duplicate entity cleanup."""
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = None
        mock_device.training_target_area_id = None
        mock_device.area_locked_name = None
        mock_device.adverts = {}

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        # Return an old address to trigger cleanup
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value="old:aa:bb:cc:dd:ee:ff")
        mock_coordinator.cleanup_old_entities_for_device = MagicMock()

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {}

        mock_add_devices = MagicMock()

        with (
            patch("custom_components.bermuda.button.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            # Get the callback
            callback_func = mock_dispatcher.call_args[0][2]
            callback_func("aa:bb:cc:dd:ee:ff")

        # Verify cleanup was called
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
        mock_device.training_target_floor_id = None
        mock_device.training_target_area_id = None
        mock_device.area_locked_name = None
        mock_device.adverts = {}

        mock_coordinator = MagicMock()
        mock_coordinator.hass = hass
        mock_coordinator.last_update_success = True
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}
        mock_coordinator.check_for_duplicate_entities = MagicMock(return_value=None)

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
            patch("custom_components.bermuda.button.async_dispatcher_connect") as mock_dispatcher,
            patch("custom_components.bermuda.entity.ar.async_get") as mock_ar,
            patch("custom_components.bermuda.entity.dr.async_get") as mock_dr,
        ):
            mock_ar.return_value = MagicMock()
            mock_dr.return_value = MagicMock()

            await async_setup_entry(hass, mock_entry, mock_add_devices)

            # Get the callback
            callback_func = mock_dispatcher.call_args[0][2]

            # Call twice with same address
            callback_func("aa:bb:cc:dd:ee:ff")
            callback_func("aa:bb:cc:dd:ee:ff")

        # Should only create entities once
        assert call_count == 1


class TestTrainingButtonAsyncPress:
    """Tests for BermudaTrainingButton.async_press complex training logic."""

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
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = training_floor_id
        mock_device.training_target_area_id = training_area_id
        mock_device.area_locked_name = "Test Room"
        mock_device.adverts = {"scanner1": MagicMock()} if has_adverts else {}
        mock_device.update_area_and_floor = MagicMock()
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

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

    @pytest.mark.asyncio
    async def test_async_press_validates_area_exists(self) -> None:
        """Test that async_press validates the area exists."""
        button = self._create_button(training_floor_id="floor1", training_area_id="nonexistent_area")

        # Mock area registry to return None (area doesn't exist)
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=None)

        with patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry):
            await button.async_press()

        # Training should not have started (area doesn't exist)
        assert button._is_training is False

    @pytest.mark.asyncio
    async def test_async_press_successful_training(self) -> None:
        """Test successful training flow."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        # Mock area registry
        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        # Track sample count
        sample_count = 0

        async def mock_train_fingerprint(*args, **kwargs):
            nonlocal sample_count
            sample_count += 1
            # Return success for each sample
            return (True, {"scanner1": sample_count * 1.0})

        button.coordinator.async_train_fingerprint = AsyncMock(side_effect=mock_train_fingerprint)

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create") as mock_notify,
            patch("custom_components.bermuda.button.async_dismiss") as mock_dismiss,
            patch("custom_components.bermuda.button.TRAINING_SAMPLE_COUNT", 3),  # Reduce samples for test
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.01),  # Speed up
        ):
            await button.async_press()

        # Verify training completed
        assert button._is_training is False
        assert button._attr_icon == BermudaTrainingButton.ICON_IDLE
        # Verify area was updated after successful training
        button._device.update_area_and_floor.assert_called_once_with("area1")
        # Verify notifications were created
        assert mock_notify.call_count >= 1  # At least start notification

    @pytest.mark.asyncio
    async def test_async_press_handles_timeout(self) -> None:
        """Test that async_press handles training timeout."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        # Always return no new data to trigger timeout
        button.coordinator.async_train_fingerprint = AsyncMock(return_value=(False, {}))

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create"),
            patch("custom_components.bermuda.button.async_dismiss"),
            patch("custom_components.bermuda.button.TRAINING_MAX_TIME_SECONDS", 0.1),  # Very short timeout
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.05),
        ):
            await button.async_press()

        # Training should have stopped
        assert button._is_training is False
        # Device area should NOT be updated (no successful samples)
        button._device.update_area_and_floor.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_press_handles_training_exception(self) -> None:
        """Test that async_press handles exceptions during training."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        call_count = 0

        async def mock_train_with_exception(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Test exception")
            return (True, {"scanner1": call_count * 1.0})

        button.coordinator.async_train_fingerprint = AsyncMock(side_effect=mock_train_with_exception)

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create"),
            patch("custom_components.bermuda.button.async_dismiss"),
            patch("custom_components.bermuda.button.TRAINING_SAMPLE_COUNT", 3),
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.01),
        ):
            await button.async_press()

        # Training should complete despite exception
        assert button._is_training is False

    @pytest.mark.asyncio
    async def test_async_press_handles_cancelled_error(self) -> None:
        """Test that async_press properly handles CancelledError."""
        import asyncio

        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        async def mock_train_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        button.coordinator.async_train_fingerprint = AsyncMock(side_effect=mock_train_cancelled)

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create"),
            patch("custom_components.bermuda.button.async_dismiss"),
            pytest.raises(asyncio.CancelledError),
        ):
            await button.async_press()

        # Even after CancelledError, cleanup should have happened
        assert button._is_training is False
        assert button._device.training_target_floor_id is None
        assert button._device.training_target_area_id is None

    @pytest.mark.asyncio
    async def test_async_press_clears_training_fields_on_completion(self) -> None:
        """Test that training fields are cleared after training."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        sample_count = 0

        async def mock_train(*args, **kwargs):
            nonlocal sample_count
            sample_count += 1
            return (True, {"scanner1": sample_count})

        button.coordinator.async_train_fingerprint = AsyncMock(side_effect=mock_train)

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create"),
            patch("custom_components.bermuda.button.async_dismiss"),
            patch("custom_components.bermuda.button.TRAINING_SAMPLE_COUNT", 2),
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.01),
        ):
            await button.async_press()

        # Verify all training fields are cleared
        assert button._device.training_target_floor_id is None
        assert button._device.training_target_area_id is None
        assert button._device.area_locked_id is None
        assert button._device.area_locked_name is None
        assert button._device.area_locked_scanner_addr is None

    @pytest.mark.asyncio
    async def test_async_press_no_valid_samples(self) -> None:
        """Test async_press when no valid samples are collected."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        # Always return failure
        button.coordinator.async_train_fingerprint = AsyncMock(return_value=(False, {}))

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create") as mock_notify,
            patch("custom_components.bermuda.button.async_dismiss"),
            patch("custom_components.bermuda.button.TRAINING_MAX_TIME_SECONDS", 0.1),
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.05),
        ):
            await button.async_press()

        # Verify failure notification was shown (at least 2 calls: start and failure)
        assert mock_notify.call_count >= 2

    @pytest.mark.asyncio
    async def test_async_press_quality_ratings(self) -> None:
        """Test that quality ratings are calculated correctly."""
        button = self._create_button(training_floor_id="floor1", training_area_id="area1")

        mock_area = MagicMock()
        mock_area_registry = MagicMock()
        mock_area_registry.async_get_area = MagicMock(return_value=mock_area)

        sample_count = 0

        async def mock_train(*args, **kwargs):
            nonlocal sample_count
            sample_count += 1
            return (True, {"scanner1": sample_count})

        button.coordinator.async_train_fingerprint = AsyncMock(side_effect=mock_train)

        notifications = []

        def capture_notification(*args, **kwargs):
            notifications.append(kwargs.get("message", ""))

        with (
            patch("custom_components.bermuda.button.ar.async_get", return_value=mock_area_registry),
            patch("custom_components.bermuda.button.async_create", side_effect=capture_notification),
            patch("custom_components.bermuda.button.async_dismiss"),
            patch("custom_components.bermuda.button.TRAINING_SAMPLE_COUNT", 60),  # Full count
            patch("custom_components.bermuda.button.TRAINING_POLL_INTERVAL", 0.001),
        ):
            await button.async_press()

        # Check that quality info was in the notification
        assert any("Quality" in n for n in notifications)


class TestResetTrainingButtonAsyncPress:
    """Additional tests for BermudaResetTrainingButton."""

    def _create_button(self, reset_returns: bool = True) -> BermudaResetTrainingButton:
        """Create a BermudaResetTrainingButton instance for testing."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.async_reset_device_training = AsyncMock(return_value=reset_returns)
        mock_coordinator.async_request_refresh = AsyncMock()

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
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

    @pytest.mark.asyncio
    async def test_async_press_handles_no_training_data(self) -> None:
        """Test async_press when no training data exists."""
        button = self._create_button(reset_returns=False)

        await button.async_press()

        # Should still trigger refresh
        button.coordinator.async_request_refresh.assert_called_once()


class TestTrainingButtonAddedToHass:
    """Tests for async_added_to_hass."""

    @pytest.mark.asyncio
    async def test_async_added_to_hass_logs_state(self) -> None:
        """Test that async_added_to_hass logs the current state."""
        mock_hass = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.hass = mock_hass
        mock_coordinator.last_update_success = True

        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.unique_id = "test_unique_id"
        mock_device.address = "aa:bb:cc:dd:ee:ff"
        mock_device.training_target_floor_id = "floor1"
        mock_device.training_target_area_id = "area1"
        mock_device.area_locked_name = None
        mock_device.adverts = {}
        mock_coordinator.devices = {"aa:bb:cc:dd:ee:ff": mock_device}

        mock_config_entry = MagicMock()
        mock_config_entry.options = {}

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
            button.hass = mock_hass

        # Mock the parent class method
        with patch.object(BermudaTrainingButton.__bases__[0], "async_added_to_hass", new_callable=AsyncMock):
            await button.async_added_to_hass()

        # Test passes if no exception is raised


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
