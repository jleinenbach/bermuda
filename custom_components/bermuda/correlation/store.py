"""
Persistence for scanner correlations.

Handles loading and saving correlation data to survive HA restarts.
Uses Home Assistant's Store API for reliable JSON persistence.

Storage structure:
- "devices": Device-specific profiles {device_addr: {area_id: AreaProfile}}
- "rooms": Room-level profiles {area_id: RoomProfile}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from .area_profile import AreaProfile
from .room_profile import RoomProfile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

STORAGE_KEY = "bermuda.scanner_correlations"
STORAGE_VERSION = 1


class CorrelationData(NamedTuple):
    """Container for all correlation data."""

    device_profiles: dict[str, dict[str, AreaProfile]]
    room_profiles: dict[str, RoomProfile]


class CorrelationStore:
    """
    Handles persistence of scanner correlation data.

    Uses Home Assistant's Store API which provides:
    - Atomic writes (no corruption on crash)
    - Automatic JSON serialization
    - Version migration support

    Stores two types of profiles:
    - Device profiles: Per-device, per-area absolute RSSI and delta patterns
    - Room profiles: Device-independent scanner-pair delta patterns

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
        Load device correlations from persistent storage.

        For backward compatibility, returns only device profiles.
        Use async_load_all() to get both device and room profiles.

        Returns:
            Nested dict: {device_address: {area_id: AreaProfile}}.
            Empty dict on first run or if storage is empty.

        """
        data = await self.async_load_all()
        return data.device_profiles

    async def async_load_all(self) -> CorrelationData:
        """
        Load all correlation data from persistent storage.

        Returns:
            CorrelationData with device_profiles and room_profiles.

        """
        # pylint: disable=import-outside-toplevel
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        self._store = Store(
            self._hass,
            STORAGE_VERSION,
            STORAGE_KEY,
        )

        data = await self._store.async_load()

        if not data:
            return CorrelationData(device_profiles={}, room_profiles={})

        return self._deserialize_all(data)

    async def async_save(
        self,
        correlations: dict[str, dict[str, AreaProfile]],
        room_profiles: dict[str, RoomProfile] | None = None,
    ) -> None:
        """
        Save correlations to persistent storage.

        Args:
            correlations: Nested dict of device -> area -> profile.
            room_profiles: Optional dict of area_id -> RoomProfile.

        """
        if self._store is None:
            # pylint: disable=import-outside-toplevel
            from homeassistant.helpers.storage import Store  # noqa: PLC0415

            self._store = Store(
                self._hass,
                STORAGE_VERSION,
                STORAGE_KEY,
            )

        await self._store.async_save(
            self._serialize(correlations, room_profiles or {})
        )

    def _serialize(
        self,
        device_profiles: dict[str, dict[str, AreaProfile]],
        room_profiles: dict[str, RoomProfile],
    ) -> dict[str, Any]:
        """
        Convert to JSON-serializable format.

        Args:
            device_profiles: Nested dict of device -> area -> profile.
            room_profiles: Dict of area_id -> RoomProfile.

        Returns:
            Dictionary suitable for JSON storage.

        """
        return {
            "devices": {
                device_addr: {
                    area_id: profile.to_dict()
                    for area_id, profile in areas.items()
                }
                for device_addr, areas in device_profiles.items()
            },
            "rooms": {
                area_id: profile.to_dict()
                for area_id, profile in room_profiles.items()
            },
        }

    def _deserialize_all(
        self,
        data: dict[str, Any],
    ) -> CorrelationData:
        """
        Convert from stored JSON format.

        Handles migration from version 1 (devices only) to version 2
        (devices + rooms).

        Args:
            data: Dictionary from storage.

        Returns:
            CorrelationData with device and room profiles.

        """
        # Deserialize device profiles
        device_profiles: dict[str, dict[str, AreaProfile]] = {}
        for device_addr, areas in data.get("devices", {}).items():
            device_profiles[device_addr] = {}
            for area_id, profile_data in areas.items():
                device_profiles[device_addr][area_id] = AreaProfile.from_dict(
                    profile_data
                )

        # Deserialize room profiles (new in version 2)
        room_profiles: dict[str, RoomProfile] = {}
        for area_id, profile_data in data.get("rooms", {}).items():
            room_profiles[area_id] = RoomProfile.from_dict(profile_data)

        return CorrelationData(
            device_profiles=device_profiles,
            room_profiles=room_profiles,
        )

    def _deserialize(
        self,
        data: dict[str, Any],
    ) -> dict[str, dict[str, AreaProfile]]:
        """
        Convert from stored JSON format (device profiles only).

        For backward compatibility.

        Args:
            data: Dictionary from storage.

        Returns:
            Nested dict of device -> area -> AreaProfile.

        """
        return self._deserialize_all(data).device_profiles
