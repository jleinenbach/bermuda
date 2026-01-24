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
