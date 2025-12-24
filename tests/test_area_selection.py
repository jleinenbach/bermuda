"""Tests for area selection heuristics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.bermuda_irk import BermudaIrkManager


def _make_coordinator(hass) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for area tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    return coordinator


def _make_scanner(name: str, area_id: str, stamp: float) -> SimpleNamespace:
    """Create a minimal scanner-like object."""
    return SimpleNamespace(
        address=f"scanner-{name}",
        name=name,
        area_id=area_id,
        area_name=area_id,
        last_seen=stamp,
    )


def _make_advert(name: str, area_id: str, distance: float, age: float = 0.0) -> SimpleNamespace:
    """Create a minimal advert-like object with distance metadata."""
    now = monotonic_time_coarse()
    stamp = now - age
    scanner_device = _make_scanner(name, area_id, stamp)
    return SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id,
        rssi_distance=distance,
        rssi=-50.0,
        stamp=stamp,
        scanner_device=scanner_device,
        hist_distance_by_interval=[],
    )


@pytest.fixture
def coordinator(hass):
    """Return a minimal coordinator for tests."""
    return _make_coordinator(hass)


def _configure_device(coordinator: BermudaDataUpdateCoordinator, address: str):
    """Create a BermudaDevice with default distance options."""
    device = coordinator._get_or_create_device(address)
    device.options.update(
        {
            CONF_MAX_RADIUS: coordinator.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS),
            "attenuation": DEFAULT_ATTENUATION,
            "ref_power": DEFAULT_REF_POWER,
            "devtracker_nothome_timeout": DEFAULT_DEVTRACK_TIMEOUT,
            "smoothing_samples": DEFAULT_SMOOTHING_SAMPLES,
            "max_velocity": DEFAULT_MAX_VELOCITY,
        }
    )
    return device


def test_out_of_radius_incumbent_is_dropped(coordinator: BermudaDataUpdateCoordinator):
    """Ensure an out-of-range incumbent is discarded."""
    coordinator.options[CONF_MAX_RADIUS] = 5.0
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:FF")

    far_incumbent = _make_advert("far", "area-far", distance=10.0)
    near_challenger = _make_advert("near", "area-near", distance=3.0)

    device.area_advert = far_incumbent
    device.adverts = {
        "incumbent": far_incumbent,
        "challenger": near_challenger,
    }

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is near_challenger


def test_out_of_radius_incumbent_without_valid_challenger_clears_selection(
    coordinator: BermudaDataUpdateCoordinator,
):
    """When no contender is within radius, selection clears."""
    coordinator.options[CONF_MAX_RADIUS] = 5.0
    device = _configure_device(coordinator, "11:22:33:44:55:66")

    far_incumbent = _make_advert("far", "area-far", distance=10.0)
    far_challenger = _make_advert("far2", "area-far2", distance=8.0)

    device.area_advert = far_incumbent
    device.adverts = {"incumbent": far_incumbent, "challenger": far_challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is None


def test_near_field_absolute_improvement_wins(coordinator: BermudaDataUpdateCoordinator):
    """Allow meaningful absolute improvement in the near field to switch areas."""
    coordinator.options[CONF_MAX_RADIUS] = 10.0
    device = _configure_device(coordinator, "22:33:44:55:66:77")

    incumbent = _make_advert("inc", "area-old", distance=0.5)
    challenger = _make_advert("chal", "area-new", distance=0.4)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_near_field_tiny_improvement_does_not_flip(coordinator: BermudaDataUpdateCoordinator):
    """Small near-field deltas should not churn selection."""
    coordinator.options[CONF_MAX_RADIUS] = 10.0
    device = _configure_device(coordinator, "33:44:55:66:77:88")

    incumbent = _make_advert("inc", "area-old", distance=0.5)
    challenger = _make_advert("chal", "area-new", distance=0.49)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent


def test_far_field_small_relative_change_sticks(coordinator: BermudaDataUpdateCoordinator):
    """Far-field small relative changes should not cause churn."""
    coordinator.options[CONF_MAX_RADIUS] = 20.0
    device = _configure_device(coordinator, "44:55:66:77:88:99")

    incumbent = _make_advert("inc", "area-old", distance=6.0)
    challenger = _make_advert("chal", "area-new", distance=5.8)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent
