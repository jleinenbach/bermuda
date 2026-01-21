"""Create Button entities for manual fingerprint training."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import _LOGGER, SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator

# Number of training samples to apply for stronger fingerprint weight
TRAINING_SAMPLE_COUNT = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Button entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices: list[str] = []

    @callback
    def device_new(address: str) -> None:
        """Create entities for newly-found device."""
        if address not in created_devices:
            entities: list[ButtonEntity] = []
            entities.append(BermudaTrainingButton(coordinator, entry, address))
            entities.append(BermudaResetTrainingButton(coordinator, entry, address))
            async_add_devices(entities, False)
            created_devices.append(address)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaTrainingButton(BermudaEntity, ButtonEntity):
    """
    Button to trigger fingerprint training.

    This button is only available (enabled) when:
    1. A room has been selected in the Training Room dropdown
    2. The device has valid RSSI readings from scanners

    Pressing the button trains the fingerprint with the current RSSI readings
    for the selected room, applying multiple samples for stronger weight.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "training_learn"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:brain"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the training button."""
        super().__init__(coordinator, entry, address)
        _LOGGER.debug(
            "Training button created for %s (device id: %s)",
            self._device.name,
            id(self._device),
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass - verify listener registration."""
        await super().async_added_to_hass()
        _LOGGER.debug(
            "Training button added to hass for %s: floor=%s, area=%s (device id: %s)",
            self._device.name,
            self._device.training_target_floor_id,
            self._device.training_target_area_id,
            id(self._device),
        )

    @property
    def available(self) -> bool:
        """Return True if button should be enabled (floor AND room selected)."""
        # Check parent availability first
        if not super().available:
            _LOGGER.debug(
                "Training button unavailable for %s: coordinator not ready",
                self._device.name,
            )
            return False

        # Button available when BOTH training floor AND room have been selected.
        # Uses training_target_* fields which are ONLY set by select entities
        # and NEVER cleared by coordinator - ensuring button stays enabled.
        floor_ok = self._device.training_target_floor_id is not None
        area_ok = self._device.training_target_area_id is not None
        result = floor_ok and area_ok

        if not result:
            _LOGGER.debug(
                "Training button unavailable for %s: floor=%s, area=%s (device id: %s)",
                self._device.name,
                self._device.training_target_floor_id,
                self._device.training_target_area_id,
                id(self._device),
            )

        return result

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update - explicitly update availability."""
        # Compute availability directly here and log for debugging
        parent_available = self.coordinator.last_update_success
        floor_ok = self._device.training_target_floor_id is not None
        area_ok = self._device.training_target_area_id is not None
        should_be_available = parent_available and floor_ok and area_ok

        _LOGGER.debug(
            "Button coordinator update for %s: parent=%s, floor=%s, area=%s, available=%s (device id: %s)",
            self._device.name,
            parent_available,
            self._device.training_target_floor_id,
            self._device.training_target_area_id,
            should_be_available,
            id(self._device),
        )
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        """Handle the button press - trigger fingerprint training."""
        # Double-check that a training room is selected
        if self._device.training_target_area_id is None:
            _LOGGER.warning(
                "Training button pressed but no room selected for %s",
                self._device.name,
            )
            return

        # Check if device has any adverts (RSSI readings)
        if not self._device.adverts:
            _LOGGER.warning(
                "Training button pressed but no scanner data for %s",
                self._device.name,
            )
            return

        target_area_id = self._device.training_target_area_id
        target_area_name = self._device.area_locked_name or target_area_id

        _LOGGER.info(
            "Training fingerprint for %s in room %s (%d samples)...",
            self._device.name,
            target_area_name,
            TRAINING_SAMPLE_COUNT,
        )

        try:
            successful_samples = 0
            for i in range(TRAINING_SAMPLE_COUNT):
                success = await self.coordinator.async_train_fingerprint(
                    device_address=self.address,
                    target_area_id=target_area_id,
                )
                if success:
                    successful_samples += 1
                else:
                    _LOGGER.debug(
                        "Training sample %d/%d failed for %s",
                        i + 1,
                        TRAINING_SAMPLE_COUNT,
                        self._device.name,
                    )

            if successful_samples > 0:
                _LOGGER.info(
                    "Fingerprint training complete for %s in %s (%d/%d samples)",
                    self._device.name,
                    target_area_name,
                    successful_samples,
                    TRAINING_SAMPLE_COUNT,
                )
            else:
                _LOGGER.warning(
                    "Fingerprint training failed for %s - no valid samples",
                    self._device.name,
                )
        finally:
            # ALWAYS clear training fields, even if training fails or throws exception
            _LOGGER.debug(
                "Clearing training fields for %s (floor=%s, area=%s)",
                self._device.name,
                self._device.training_target_floor_id,
                self._device.training_target_area_id,
            )
            self._device.training_target_floor_id = None
            self._device.training_target_area_id = None
            # Also clear the area lock
            self._device.area_locked_id = None
            self._device.area_locked_name = None
            self._device.area_locked_scanner_addr = None

            # Trigger refresh so select entities clear their dropdowns
            await self.coordinator.async_request_refresh()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_learn"


class BermudaResetTrainingButton(BermudaEntity, ButtonEntity):
    """
    Button to reset all training data for a device.

    This is the "nuclear option" for fixing incorrect manual training.
    It clears ALL user-trained fingerprint data (Frozen Layers) for this device
    across ALL rooms, reverting to automatic learning (Shadow Learning) only.

    Use cases:
    - "Ghost Scanner" problem: Device was trained in wrong/invisible room
    - User wants to start fresh with automatic learning
    - Incorrect training that can't be fixed by re-training

    The auto-learned data is preserved, providing immediate fallback.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "reset_training"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:eraser"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the reset training button."""
        super().__init__(coordinator, entry, address)
        _LOGGER.debug(
            "Reset training button created for %s",
            self._device.name,
        )

    async def async_press(self) -> None:
        """Handle the button press - reset all training data for this device."""
        _LOGGER.info(
            "Resetting all training data for %s...",
            self._device.name,
        )

        success = await self.coordinator.async_reset_device_training(self.address)

        if success:
            _LOGGER.info(
                "Successfully reset all training data for %s",
                self._device.name,
            )
        else:
            _LOGGER.info(
                "No training data found for %s - nothing to reset",
                self._device.name,
            )

        # Trigger refresh to update entity states
        await self.coordinator.async_request_refresh()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_reset_training"
