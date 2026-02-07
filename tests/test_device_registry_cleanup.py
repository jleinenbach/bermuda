"""Tests for device registry connection canonicalisation."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.util import normalize_mac

pytestmark = pytest.mark.asyncio


@pytest.fixture
def coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a minimal coordinator for registry cleanup tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.dr = dr.async_get(hass)
    return coordinator


async def test_cleanup_normalizes_and_deduplicates_connections(
    coordinator: BermudaDataUpdateCoordinator, mock_bermuda_entry: MockConfigEntry
) -> None:
    """Normalize MACs and drop duplicate Bluetooth connections."""
    registry = coordinator.dr

    mac_upper = "AA:BB:CC:DD:EE:FF"
    mac_lower = normalize_mac(mac_upper)

    device = registry.async_get_or_create(
        config_entry_id=mock_bermuda_entry.entry_id,
        identifiers={(DOMAIN, "device-id")},
        connections={
            (dr.CONNECTION_BLUETOOTH, mac_upper),
            (dr.CONNECTION_BLUETOOTH, mac_lower),
            ("mac", "aa-bb-cc-dd-ee-ff"),
        },
    )

    await coordinator.async_cleanup_device_registry_connections()

    updated = registry.async_get(device.id)
    assert updated is not None
    assert updated.connections == {
        (dr.CONNECTION_BLUETOOTH, mac_lower),
        (dr.CONNECTION_NETWORK_MAC, mac_lower),
    }


async def test_cleanup_ignores_non_bermuda_devices(
    coordinator: BermudaDataUpdateCoordinator, mock_bermuda_entry: MockConfigEntry
) -> None:
    """Ensure cleanup does not mutate devices outside the Bermuda domain."""
    registry = coordinator.dr
    original = registry.async_get_or_create(
        config_entry_id=mock_bermuda_entry.entry_id,
        identifiers={("other", "device-id")},
        connections={
            (dr.CONNECTION_BLUETOOTH, "AA:BB:CC:DD:EE:FF"),
            ("mac", "AA:BB:CC:DD:EE:FF"),
        },
    )
    original_connections = set(original.connections)

    await coordinator.async_cleanup_device_registry_connections()

    refreshed = registry.async_get(original.id)
    assert refreshed is not None
    assert refreshed.connections == original_connections


async def test_cleanup_handles_connection_collision_gracefully(
    coordinator: BermudaDataUpdateCoordinator, mock_bermuda_entry: MockConfigEntry
) -> None:
    """Regression: DeviceConnectionCollisionError must not crash startup.

    When normalizing 'mac' -> CONNECTION_NETWORK_MAC creates a collision with
    another device that already owns the same canonical connection, the cleanup
    must skip the colliding device and continue processing the remaining ones.
    """
    registry = coordinator.dr

    # Device A (non-Bermuda) already owns the canonical MAC connection.
    registry.async_get_or_create(
        config_entry_id=mock_bermuda_entry.entry_id,
        identifiers={("esphome", "scanner-wifi")},
        connections={
            (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff"),
        },
    )

    # Device B (Bermuda) has the same MAC under the legacy 'mac' type.
    # Normalization will try 'mac' -> CONNECTION_NETWORK_MAC which collides
    # with Device A.
    colliding_device = registry.async_get_or_create(
        config_entry_id=mock_bermuda_entry.entry_id,
        identifiers={(DOMAIN, "colliding-device")},
        connections={
            ("mac", "AA:BB:CC:DD:EE:FF"),
        },
    )
    colliding_original = set(colliding_device.connections)

    # Device C (Bermuda) should still be normalized despite B's collision.
    other_device = registry.async_get_or_create(
        config_entry_id=mock_bermuda_entry.entry_id,
        identifiers={(DOMAIN, "other-device")},
        connections={
            ("mac", "11:22:33:44:55:66"),
        },
    )

    # Must NOT raise DeviceConnectionCollisionError.
    await coordinator.async_cleanup_device_registry_connections()

    # Colliding device: update was skipped, connections unchanged.
    refreshed_colliding = registry.async_get(colliding_device.id)
    assert refreshed_colliding is not None
    assert refreshed_colliding.connections == colliding_original

    # Other device: successfully normalized.
    refreshed_other = registry.async_get(other_device.id)
    assert refreshed_other is not None
    assert refreshed_other.connections == {
        (dr.CONNECTION_NETWORK_MAC, normalize_mac("11:22:33:44:55:66")),
    }
