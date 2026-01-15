"""
Persistence for scanner correlations.

Handles loading and saving correlation data to survive HA restarts.
Uses Home Assistant's Store API for reliable JSON persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .area_profile import AreaProfile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

STORAGE_KEY = "bermuda.scanner_correlations"
STORAGE_VERSION = 1


class CorrelationStore:
    """
    Handles persistence of scanner correlation data.

    Uses Home Assistant's Store API which provides:
    - Atomic writes (no corruption on crash)
    - Automatic JSON serialization
    - Version migration support

    """

    def __init__(self, hass: HomeAssistant) -> None:
        """
        Initialize the correlation store.

        Args:
            hass: Home Assistant instance.

        """
        self._hass = hass
        self._store: Store[dict[str, Any]] | None = None

    async def async_load(self) -> dict[str, dict[str, AreaProfile]]:
        """
        Load correlations from persistent storage.

        Returns:
            Nested dict: {device_address: {area_id: AreaProfile}}.
            Empty dict on first run or if storage is empty.

        """
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        self._store = Store(
            self._hass,
            STORAGE_VERSION,
            STORAGE_KEY,
        )

        data = await self._store.async_load()

        if not data:
            return {}

        return self._deserialize(data)

    async def async_save(
        self,
        correlations: dict[str, dict[str, AreaProfile]],
    ) -> None:
        """
        Save correlations to persistent storage.

        Args:
            correlations: Nested dict of device -> area -> profile.

        """
        if self._store is None:
            from homeassistant.helpers.storage import Store  # noqa: PLC0415

            self._store = Store(
                self._hass,
                STORAGE_VERSION,
                STORAGE_KEY,
            )

        await self._store.async_save(self._serialize(correlations))

    def _serialize(
        self,
        correlations: dict[str, dict[str, AreaProfile]],
    ) -> dict[str, Any]:
        """
        Convert to JSON-serializable format.

        Args:
            correlations: Nested dict of device -> area -> profile.

        Returns:
            Dictionary suitable for JSON storage.

        """
        return {
            "devices": {
                device_addr: {area_id: profile.to_dict() for area_id, profile in areas.items()}
                for device_addr, areas in correlations.items()
            }
        }

    def _deserialize(
        self,
        data: dict[str, Any],
    ) -> dict[str, dict[str, AreaProfile]]:
        """
        Convert from stored JSON format.

        Args:
            data: Dictionary from storage.

        Returns:
            Nested dict of device -> area -> AreaProfile.

        """
        result: dict[str, dict[str, AreaProfile]] = {}

        for device_addr, areas in data.get("devices", {}).items():
            result[device_addr] = {}
            for area_id, profile_data in areas.items():
                result[device_addr][area_id] = AreaProfile.from_dict(profile_data)

        return result
