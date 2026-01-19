"""Create Select entities for manual fingerprint training."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import _LOGGER, SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Select entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices: list[str] = []

    @callback
    def device_new(address: str) -> None:
        """Create entities for newly-found device."""
        if address not in created_devices:
            entities: list[SelectEntity] = []
            entities.append(BermudaTrainingRoomSelect(coordinator, entry, address))
            entities.append(BermudaTrainingFloorSelect(coordinator, entry, address))
            async_add_devices(entities, False)
            created_devices.append(address)
        coordinator.select_created(address)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaTrainingRoomSelect(BermudaEntity, SelectEntity):
    """
    Select entity for manually training room fingerprints.

    Displays the currently detected room for a device.
    When the user selects a different room, the current RSSI readings
    are used to train the fingerprint for that room.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "training_room"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:map-marker-radius"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the room training select."""
        super().__init__(coordinator, entry, address)
        self._area_registry = ar.async_get(coordinator.hass)

    @property
    def options(self) -> list[str]:
        """Return the list of available areas."""
        areas = self._area_registry.async_list_areas()
        return sorted([area.name for area in areas])

    @property
    def current_option(self) -> str | None:
        """Return the currently detected area."""
        return self._device.area_name

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a room - train fingerprint for that room."""
        # Find the area_id for this area name
        areas = self._area_registry.async_list_areas()
        target_area = next((a for a in areas if a.name == option), None)

        if target_area is None:
            _LOGGER.warning("Could not find area '%s' for training", option)
            return

        # Train the fingerprint for this area
        await self.coordinator.async_train_fingerprint(
            device_address=self.address,
            target_area_id=target_area.id,
        )

        _LOGGER.info(
            "Trained fingerprint for device %s in room %s",
            self._device.name,
            option,
        )

        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_room"


class BermudaTrainingFloorSelect(BermudaEntity, SelectEntity):
    """
    Select entity for displaying/setting floor for fingerprint training.

    Displays the currently detected floor for a device.
    When the user selects a different floor, this provides context
    for the room training and helps validate cross-floor movements.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "training_floor"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:floor-plan"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the floor training select."""
        super().__init__(coordinator, entry, address)
        self._floor_registry = fr.async_get(coordinator.hass)

    @property
    def options(self) -> list[str]:
        """Return the list of available floors."""
        floors = self._floor_registry.async_list_floors()
        # Sort by level if available, otherwise by name
        sorted_floors = sorted(
            floors,
            key=lambda f: (f.level if f.level is not None else 999, f.name),
        )
        return [floor.name for floor in sorted_floors]

    @property
    def current_option(self) -> str | None:
        """Return the currently detected floor."""
        return self._device.floor_name

    async def async_select_option(self, option: str) -> None:
        """
        Handle user selecting a floor.

        This is informational context for fingerprint training.
        The actual fingerprint training happens via the room select.
        """
        _LOGGER.debug(
            "Floor override selected for %s: %s (informational only)",
            self._device.name,
            option,
        )
        # Floor selection is primarily informational for the user to confirm
        # the device is where they think it is. The room selection does the
        # actual fingerprint training.
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_floor"
