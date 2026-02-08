"""Binary sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    _LOGGER,
    SCANNER_ACTIVITY_TIMEOUT,
    SIGNAL_SCANNERS_CHANGED,
)
from .entity import BermudaEntity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary_sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_scanner_sensors: set[str] = set()

    def _create_scanner_online_sensors() -> None:
        """Create online/offline binary sensors for each scanner."""
        entities: list[BermudaScannerOnlineSensor] = []
        for scanner in coordinator.get_scanners:
            if scanner.address not in created_scanner_sensors:
                _LOGGER.debug(
                    "Creating scanner online binary sensor for %s",
                    scanner.address,
                )
                entities.append(BermudaScannerOnlineSensor(coordinator, entry, scanner.address))
                created_scanner_sensors.add(scanner.address)
        if entities:
            async_add_entities(entities, False)

    @callback
    def scanners_changed() -> None:
        """Handle scanner roster changes."""
        _create_scanner_online_sensors()

    # Create sensors for scanners that already exist
    _create_scanner_online_sensors()

    # Listen for new scanners being added
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_SCANNERS_CHANGED, scanners_changed))


class BermudaScannerOnlineSensor(BermudaEntity, BinarySensorEntity):
    """
    Binary sensor that tracks whether a BLE scanner/proxy is online.

    Reports ON when the scanner has sent BLE data within the activity
    timeout window (default 30 seconds), OFF otherwise.
    """

    _scanner_entity = True
    _attr_has_entity_name = True
    _attr_translation_key = "scanner_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_scanner_online"

    @property
    def is_on(self) -> bool | None:
        """Return true if the scanner is actively sending BLE data."""
        last_seen = self._device.last_seen
        if last_seen == 0:
            # Scanner has never sent data
            return None
        age = monotonic_time_coarse() - last_seen
        return age < SCANNER_ACTIVITY_TIMEOUT

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes."""
        last_seen = self._device.last_seen
        if last_seen == 0:
            return {
                "last_seen_age_seconds": None,
                "timeout_seconds": SCANNER_ACTIVITY_TIMEOUT,
            }
        age = round(monotonic_time_coarse() - last_seen, 1)
        return {
            "last_seen_age_seconds": age,
            "timeout_seconds": SCANNER_ACTIVITY_TIMEOUT,
        }
