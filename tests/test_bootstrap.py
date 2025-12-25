"""Bootstrap tests for area selection."""

from __future__ import annotations

import pytest

from custom_components.bermuda.const import CONF_MAX_RADIUS
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .test_area_selection import _configure_device, _make_advert, _make_coordinator


@pytest.fixture
def coordinator(hass):
    """Return a minimal coordinator for bootstrap tests."""
    return _make_coordinator(hass)


def test_bootstrap_wins_immediately(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure a device with no area accepts the first valid winner immediately."""
    coordinator.options[CONF_MAX_RADIUS] = 20.0
    area = coordinator.ar.async_create("Kitchen")
    area_id = area.id
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:FF")

    device.area_advert = None
    device.area_id = None

    scanner = _make_advert("scanner1", area_id, distance=5.0)
    device.adverts = {"scanner1": scanner}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is scanner
    assert device.area_id == area_id
    assert device.pending_streak == 0
