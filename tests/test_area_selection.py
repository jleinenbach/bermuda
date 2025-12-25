"""Tests for area selection heuristics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.const import (
    AREA_MAX_AD_AGE,
    CONF_MAX_RADIUS,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    CROSS_FLOOR_STREAK,
    AREA_RETENTION_SECONDS,
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


def _make_scanner(
    name: str,
    area_id: str,
    stamp: float,
    *,
    floor_id: str | None = None,
    floor_level: int | None = None,
) -> SimpleNamespace:
    """Create a minimal scanner-like object."""
    return SimpleNamespace(
        address=f"scanner-{name}",
        name=name,
        area_id=area_id,
        area_name=area_id,
        last_seen=stamp,
        floor_id=floor_id,
        floor_level=floor_level,
    )


def _make_advert(
    name: str,
    area_id: str,
    distance: float | None,
    age: float = 0.0,
    *,
    hist_distance_by_interval: list[float] | None = None,
    floor_id: str | None = None,
    floor_level: int | None = None,
    rssi: float | None = -50.0,
) -> SimpleNamespace:
    """Create a minimal advert-like object with distance metadata."""
    now = monotonic_time_coarse()
    stamp = now - age
    hist = list(hist_distance_by_interval) if hist_distance_by_interval is not None else []
    scanner_device = _make_scanner(name, area_id, stamp, floor_id=floor_id, floor_level=floor_level)
    return SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id,
        scanner_address=scanner_device.address,
        rssi_distance=distance,
        rssi=rssi,
        stamp=stamp,
        scanner_device=scanner_device,
        hist_distance_by_interval=hist,
    )


def _patch_monotonic_time(monkeypatch, current_time: list[float]) -> None:
    """Patch monotonic_time_coarse across modules for deterministic timing."""
    monkeypatch.setattr("bluetooth_data_tools.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("tests.test_area_selection.monotonic_time_coarse", lambda: current_time[0])


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


def test_area_selected_when_only_rssi_available(coordinator: BermudaDataUpdateCoordinator):
    """Area should be chosen even when distances are unavailable."""
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:00")

    weaker = _make_advert("weak", "area-weak", distance=None, rssi=-80.0)
    stronger = _make_advert("strong", "area-strong", distance=None, rssi=-55.0)

    device.adverts = {"weak": weaker, "strong": stronger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is stronger
    assert device.area_distance is None
    assert device.area_id == "area-strong"


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

    for _ in range(CROSS_FLOOR_STREAK):
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


def test_transient_missing_distance_does_not_switch(coordinator: BermudaDataUpdateCoordinator):
    """A fresh but distance-less incumbent should not flip for a marginal challenger."""
    device = _configure_device(coordinator, "55:66:77:88:99:AA")

    incumbent = _make_advert(
        "inc",
        "area-stable",
        distance=None,
        hist_distance_by_interval=[2.0],
    )
    # Preserve the last known applied distance
    device.area_distance = 2.0

    challenger = _make_advert("chal", "area-new", distance=1.9)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent


def test_history_distance_used_when_rssi_distance_none(coordinator: BermudaDataUpdateCoordinator):
    """Historical distance should allow a challenger to win when live distance is missing."""
    device = _configure_device(coordinator, "55:66:77:88:99:AD")

    incumbent = _make_advert("inc", "area-old", distance=6.0, hist_distance_by_interval=[6.0])
    challenger = _make_advert(
        "chal",
        "area-new",
        distance=None,
        hist_distance_by_interval=[3.0],
        rssi=-45.0,
    )

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger
    assert device.area_distance is None


def test_soft_incumbent_does_not_block_valid_challenger(coordinator: BermudaDataUpdateCoordinator):
    """A soft incumbent with no distance should not prevent a valid challenger from winning."""
    device = _configure_device(coordinator, "55:66:77:88:99:AB")
    now = monotonic_time_coarse()

    soft_incumbent = _make_advert(
        "soft",
        "area-soft",
        distance=None,
    )
    soft_incumbent.stamp = now
    device.area_distance = 2.0

    challenger = _make_advert("chal", "area-new", distance=1.0)

    device.area_advert = soft_incumbent
    device.adverts = {"soft": soft_incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_soft_incumbent_no_distance_does_not_block_rssi_fallback(coordinator: BermudaDataUpdateCoordinator):
    """Soft incumbents without distance should yield to stronger RSSI challengers."""
    device = _configure_device(coordinator, "55:66:77:88:99:AC")

    soft_incumbent = _make_advert("soft", "area-soft", distance=None, rssi=-70.0)
    challenger = _make_advert("chal", "area-new", distance=None, rssi=-50.0)

    device.area_advert = soft_incumbent
    device.adverts = {"soft": soft_incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger
    assert device.area_distance is None


def test_soft_incumbent_holds_when_no_valid_challenger(coordinator: BermudaDataUpdateCoordinator):
    """Soft incumbent should hold position when no contender is eligible."""
    device = _configure_device(coordinator, "66:77:88:99:AA:BC")
    now = monotonic_time_coarse()

    soft_incumbent = _make_advert(
        "soft",
        "area-soft",
        distance=None,
    )
    soft_incumbent.stamp = now
    device.area_distance = 2.0

    # Challenger is invalid (no distance) and should not win.
    invalid_challenger = _make_advert("invalid", area_id="area-soft", distance=None)
    invalid_challenger.stamp = now
    device.area_advert = soft_incumbent
    device.adverts = {"soft": soft_incumbent, "invalid": invalid_challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is soft_incumbent
    assert device.area_distance == 2.0


def test_stale_incumbent_allows_switch(coordinator: BermudaDataUpdateCoordinator):
    """A stale incumbent should be replaced by a valid challenger."""
    device = _configure_device(coordinator, "66:77:88:99:AA:BB")

    stale_age = AREA_MAX_AD_AGE + 1
    stale_incumbent = _make_advert("inc", "area-old", distance=2.0, age=stale_age)
    challenger = _make_advert("chal", "area-new", distance=1.0)

    device.area_advert = stale_incumbent
    device.adverts = {"incumbent": stale_incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_distance_fallback_requires_fresh_advert(coordinator: BermudaDataUpdateCoordinator):
    """Cached distance should only be reused for fresh adverts."""
    device = _configure_device(coordinator, "77:88:99:AA:BB:CC")

    stale_soft = _make_advert("stale", "area-stale", distance=None, age=(AREA_MAX_AD_AGE * 2))
    device.area_advert = stale_soft
    device.area_distance = 3.0

    device.apply_scanner_selection(stale_soft)

    assert device.area_id == "area-stale"
    assert device.area_distance is None
    metadata = device.area_state_metadata(stamp_now=monotonic_time_coarse())
    assert metadata["area_is_stale"] is True


def test_legitimate_move_switches_to_better_challenger(coordinator: BermudaDataUpdateCoordinator):
    """A meaningfully closer challenger should still win."""
    device = _configure_device(coordinator, "77:88:99:AA:BB:CC")

    incumbent = _make_advert("inc", "area-old", distance=6.0)
    challenger = _make_advert("chal", "area-new", distance=2.5)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    for _ in range(CROSS_FLOOR_STREAK):
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_jitter_and_gaps_do_not_oscillate_selection(coordinator: BermudaDataUpdateCoordinator):
    """Minor jitter and a short gap should not cause rapid area flipping."""
    device = _configure_device(coordinator, "88:99:AA:BB:CC:DD")

    incumbent = _make_advert("inc", "area-stable", distance=2.0)
    challenger = _make_advert("chal", "area-new", distance=1.95)

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)
    assert device.area_advert is incumbent
    assert device.area_distance == 2.0

    # Simulate a transient missing distance reading while still recent.
    incumbent.rssi_distance = None
    incumbent.hist_distance_by_interval = [2.0]
    incumbent.stamp = monotonic_time_coarse()
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent

    # Jitter returns but stays within hysteresis margin.
    incumbent.rssi_distance = 1.98
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent


def test_same_floor_switch_behaviour_unaffected(coordinator: BermudaDataUpdateCoordinator):
    """Switching on the same floor should behave as before."""
    device = _configure_device(coordinator, "99:AA:BB:CC:DD:EE")

    incumbent = _make_advert(
        "inc",
        "area-same",
        distance=6.0,
        hist_distance_by_interval=[6.2, 6.1, 6.3, 6.0, 6.1],
        floor_id="floor-same",
    )
    challenger = _make_advert(
        "chal",
        "area-same",
        distance=4.0,
        hist_distance_by_interval=[4.3, 4.1, 4.2, 4.0, 4.2],
        floor_id="floor-same",
    )

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    for _ in range(CROSS_FLOOR_STREAK):
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_cross_floor_switch_blocked_without_history(coordinator: BermudaDataUpdateCoordinator):
    """Cross-floor changes should not occur on weak evidence."""
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:FF")

    incumbent = _make_advert(
        "inc",
        "area-floor-a",
        distance=3.0,
        hist_distance_by_interval=[3.0],
        floor_id="floor-a",
    )
    challenger = _make_advert(
        "chal",
        "area-floor-b",
        distance=2.0,
        hist_distance_by_interval=[2.0],
        floor_id="floor-b",
    )

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent


def test_cross_floor_switch_requires_sustained_advantage(coordinator: BermudaDataUpdateCoordinator):
    """Cross-floor switches should happen only with sustained superiority."""
    device = _configure_device(coordinator, "BB:CC:DD:EE:FF:00")

    incumbent = _make_advert(
        "inc",
        "area-floor-a",
        distance=5.0,
        hist_distance_by_interval=[5.0, 5.1, 5.2, 5.1, 5.0, 5.2, 5.1, 5.0, 5.0, 5.1],
        floor_id="floor-a",
    )
    challenger = _make_advert(
        "chal",
        "area-floor-b",
        distance=2.5,
        hist_distance_by_interval=[2.4, 2.5, 2.5, 2.6, 2.4, 2.5, 2.4, 2.6, 2.5, 2.4],
        floor_id="floor-b",
    )

    device.area_advert = incumbent
    device.adverts = {"incumbent": incumbent, "challenger": challenger}

    for _ in range(CROSS_FLOOR_STREAK):
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger


def test_area_selection_retained_when_no_winner(monkeypatch, coordinator: BermudaDataUpdateCoordinator):
    """Last known area should be retained across gaps shorter than the retention window."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "DD:EE:FF:00:11:22")

    incumbent = _make_advert("inc", "area-stable", distance=2.5)
    device.adverts = {"incumbent": incumbent}
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stable"
    current_time[0] += AREA_MAX_AD_AGE + 1
    device.adverts = {}
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stable"
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is True
    assert metadata["last_good_area_age_s"] == pytest.approx(AREA_MAX_AD_AGE + 1)


def test_retained_area_expires_after_window(monkeypatch, coordinator: BermudaDataUpdateCoordinator):
    """Retained selections must eventually clear when the retention window elapses."""
    current_time = [2000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "EE:FF:00:11:22:33")

    incumbent = _make_advert("inc", "area-once", distance=3.0)
    device.adverts = {"incumbent": incumbent}
    coordinator._refresh_area_by_min_distance(device)

    current_time[0] += AREA_RETENTION_SECONDS + 5
    device.adverts = {}
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id is None
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is False
    assert metadata["last_good_area_age_s"] is None


def test_fresh_advert_replaces_retained_state(monkeypatch, coordinator: BermudaDataUpdateCoordinator):
    """A new contender should override retained state and reset staleness metadata."""
    current_time = [3000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "FF:00:11:22:33:44")

    incumbent = _make_advert("inc", "area-old", distance=4.0)
    device.adverts = {"incumbent": incumbent}
    coordinator._refresh_area_by_min_distance(device)

    current_time[0] += AREA_MAX_AD_AGE + 2
    coordinator._refresh_area_by_min_distance(device)
    assert device.area_state_metadata()["area_retained"] is True

    challenger = _make_advert("chal", "area-new", distance=1.2)
    device.adverts = {"incumbent": incumbent, "challenger": challenger}
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-new"
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is False
    assert metadata["last_good_area_age_s"] == pytest.approx(0.0)


def test_rssi_winner_does_not_keep_old_distance(coordinator: BermudaDataUpdateCoordinator):
    """RSSI-only switches must not carry distance from the prior area."""
    device = _configure_device(coordinator, "FD:FE:FF:00:11:22")

    incumbent = _make_advert("inc", "area-old", distance=None, rssi=-70.0)
    challenger = _make_advert("chal", "area-new", distance=None, rssi=-40.0)

    device.area_advert = incumbent
    device.area_distance = 3.0
    device.adverts = {"inc": incumbent, "chal": challenger}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger
    assert device.area_distance is None


def test_stale_advert_still_applies_area(monkeypatch, coordinator: BermudaDataUpdateCoordinator):
    """Initial stale adverts should still populate area/floor and mark stale metadata."""
    current_time = [5000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:00:00:00:00:01")

    stale_age = AREA_MAX_AD_AGE + 5
    stale_advert = _make_advert("stale", "area-stale", distance=None, age=stale_age)
    device.adverts = {"stale": stale_advert}

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stale"
    metadata = device.area_state_metadata(stamp_now=current_time[0])
    assert metadata["area_is_stale"] is True


def test_floor_level_populated_from_floor_registry(coordinator: BermudaDataUpdateCoordinator):
    """Ensure floor_level is sourced from the floor registry when available."""
    device = _configure_device(coordinator, "CC:DD:EE:FF:00:11")

    class DummyFloor:
        def __init__(self) -> None:
            self.floor_id = "floor-l1"
            self.name = "Level 1"
            self.icon = "mdi:home-floor-1"
            self.level = 1

    class DummyArea:
        def __init__(self) -> None:
            self.floor_id = "floor-l1"
            self.name = "Kitchen"
            self.icon = "mdi:home"

    dummy_floor = DummyFloor()
    dummy_area = DummyArea()

    device.fr = SimpleNamespace(async_get_floor=lambda floor_id: dummy_floor if floor_id == dummy_floor.floor_id else None)
    device.ar = SimpleNamespace(async_get_area=lambda area_id: dummy_area if area_id == "area-kitchen" else None)

    device._update_area_and_floor("area-kitchen")

    assert device.floor_level == 1
    assert device.floor_name == "Level 1"
