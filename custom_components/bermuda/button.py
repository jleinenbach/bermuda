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

    @property
    def available(self) -> bool:
        """Return True if button should be enabled (room is selected)."""
        # Check parent availability first
        if not super().available:
            return False

        # Button only available when a room has been selected for training
        # The room selection is stored in device.area_locked_id
        return self._device.area_locked_id is not None

    async def async_press(self) -> None:
        """Handle the button press - trigger fingerprint training."""
        # Double-check that a room is selected
        if self._device.area_locked_id is None:
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

        target_area_id = self._device.area_locked_id
        target_area_name = self._device.area_locked_name or target_area_id

        _LOGGER.info(
            "Training fingerprint for %s in room %s (%d samples)...",
            self._device.name,
            target_area_name,
            TRAINING_SAMPLE_COUNT,
        )

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

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_learn"
