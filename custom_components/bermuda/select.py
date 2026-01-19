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
            # Create floor select first so room select can reference it
            floor_select = BermudaTrainingFloorSelect(coordinator, entry, address)
            room_select = BermudaTrainingRoomSelect(coordinator, entry, address, floor_select)
            # Set bidirectional reference
            floor_select.set_room_select(room_select)
            entities.append(room_select)
            entities.append(floor_select)
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

    Room options are filtered based on the selected floor to prevent
    training fingerprints for rooms on the wrong floor.
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
        floor_select: BermudaTrainingFloorSelect,
    ) -> None:
        """Initialize the room training select."""
        super().__init__(coordinator, entry, address)
        self._area_registry = ar.async_get(coordinator.hass)
        self._floor_select = floor_select
        # Track if room needs explicit selection after floor change
        self._awaiting_room_selection = False

    @property
    def _effective_floor_id(self) -> str | None:
        """Return the floor ID to filter rooms by (override or detected)."""
        # Use floor override if set, otherwise use detected floor
        if self._floor_select.floor_override_id is not None:
            return self._floor_select.floor_override_id
        return self._device.floor_id

    @property
    def options(self) -> list[str]:
        """Return the list of available areas, filtered by floor."""
        areas = self._area_registry.async_list_areas()
        floor_id = self._effective_floor_id

        if floor_id is not None:
            # Filter to only rooms on the selected floor
            filtered_areas = [a for a in areas if a.floor_id == floor_id]
        else:
            # No floor filter - show all rooms
            filtered_areas = list(areas)

        return sorted([area.name for area in filtered_areas])

    @property
    def current_option(self) -> str | None:
        """Return the currently detected area, or None if awaiting selection."""
        # If floor was changed and room not yet selected, show no selection
        if self._awaiting_room_selection:
            return None
        return self._device.area_name

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a room - train fingerprint for that room."""
        # Find the area_id for this area name
        areas = self._area_registry.async_list_areas()
        target_area = next((a for a in areas if a.name == option), None)

        if target_area is None:
            _LOGGER.warning("Could not find area '%s' for training", option)
            return

        # Verify room is on the correct floor (safety check)
        expected_floor_id = self._effective_floor_id
        if expected_floor_id is not None and target_area.floor_id != expected_floor_id:
            _LOGGER.warning(
                "Room '%s' is not on the selected floor - skipping training",
                option,
            )
            return

        # Clear the awaiting flag since user explicitly selected a room
        self._awaiting_room_selection = False

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

    def on_floor_changed(self) -> None:
        """Called by floor select when floor is changed by user."""
        # Mark that we're awaiting explicit room selection
        self._awaiting_room_selection = True
        # Update state to reflect the cleared room
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_room"


class BermudaTrainingFloorSelect(BermudaEntity, SelectEntity):
    """
    Select entity for setting floor for fingerprint training.

    Displays the currently detected floor for a device.
    When the user selects a different floor, the room selection is cleared
    to prevent training fingerprints for the wrong room.
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
        # Track floor override (None = use detected floor)
        self.floor_override_id: str | None = None
        self._floor_override_name: str | None = None
        # Reference to room select (set after creation)
        self._room_select: BermudaTrainingRoomSelect | None = None

    def set_room_select(self, room_select: BermudaTrainingRoomSelect) -> None:
        """Set reference to the room select entity."""
        self._room_select = room_select

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
        """Return the floor override or detected floor."""
        if self._floor_override_name is not None:
            return self._floor_override_name
        return self._device.floor_name

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting a floor - clear room and set override."""
        # Find the floor_id for this floor name
        floors = self._floor_registry.async_list_floors()
        target_floor = next((f for f in floors if f.name == option), None)

        if target_floor is None:
            _LOGGER.warning("Could not find floor '%s'", option)
            return

        # Check if this is actually a change from current
        current_floor_id = self.floor_override_id or self._device.floor_id
        if target_floor.floor_id == current_floor_id:
            # Same floor, no change needed
            return

        # Set the floor override
        self.floor_override_id = target_floor.floor_id
        self._floor_override_name = option

        _LOGGER.info(
            "Floor override set for %s: %s - room selection cleared",
            self._device.name,
            option,
        )

        # Notify room select that floor changed (clears room selection)
        if self._room_select is not None:
            self._room_select.on_floor_changed()

        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_floor"
