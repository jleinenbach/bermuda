"""Switch platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

from .const import DEFAULT_NAME, ICON
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

SWITCH = "switch"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    # coordinator = hass.data[DOMAIN][entry.entry_id]
    # AJG async_add_devices([BermudaBinarySwitch(coordinator, entry)])


class BermudaBinarySwitch(BermudaEntity, SwitchEntity):
    """Bermuda switch class."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        # await self.coordinator.api.async_set_title("bar")
        # await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        # await self.coordinator.api.async_set_title("foo")
        # await self.coordinator.async_request_refresh()

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return f"{DEFAULT_NAME}_{SWITCH}"

    @property
    def icon(self) -> str:
        """Return the icon of this switch."""
        return ICON

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        # return self.coordinator.data.get("title", "") == "foo"
        return True
