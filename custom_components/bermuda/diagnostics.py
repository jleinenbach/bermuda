"""Diagnostics support for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN

if TYPE_CHECKING:
    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BermudaConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    # We can call this with our own config_entry because the diags step doesn't
    # actually use it.

    bt_diags = await coordinator._manager.async_diagnostics()

    # Collect raw data first and redact it once, in the executor: the device
    # dump alone can be several megabytes, and redacting it on the event loop
    # froze Home Assistant for minutes.
    call = ServiceCall(hass, DOMAIN, "dump_devices", {"redact": False})

    data: dict[str, Any] = {
        "active_devices": f"{coordinator.count_active_devices()}/{len(coordinator.devices)}",
        "active_scanners": f"{coordinator.count_active_scanners()}/{len(coordinator.scanner_list)}",
        "irk_manager": coordinator.irk_manager.get_diagnostics_no_redactions(),
        "fmdn_manager": coordinator.fmdn.manager.get_diagnostics_no_redactions(),
        "auto_learning": coordinator.area_selection.get_auto_learning_diagnostics(),
        "reference_trackers": coordinator.area_selection.get_reference_tracker_diagnostics(),
        "devices": await coordinator.service_dump_devices(call),
        "bt_manager": bt_diags,
    }
    return cast("dict[str, Any]", await coordinator.service_handler.async_redact_data(data))
