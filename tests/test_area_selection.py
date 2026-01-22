"""Tests for area selection heuristics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.const import STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_device import BermudaDevice

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
    EVIDENCE_WINDOW_SECONDS,
)
from custom_components.bermuda.area_selection import AreaSelectionHandler
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for area tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.correlations = {}  # Scanner correlation data for area confidence
    coordinator.room_profiles = {}  # Room-level scanner pair delta profiles
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.device_ukfs = {}  # UKF state for fingerprint matching
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.area_selection = AreaSelectionHandler(coordinator)
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
    advert = SimpleNamespace(
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
    # Add median_rssi method for physical RSSI priority feature
    advert.median_rssi = lambda: rssi
    return advert


def _patch_monotonic_time(monkeypatch: pytest.MonkeyPatch, current_time: list[float]) -> None:
    """Patch monotonic_time_coarse across modules for deterministic timing."""
    monkeypatch.setattr("bluetooth_data_tools.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: current_time[0])
    monkeypatch.setattr("custom_components.bermuda.area_selection.monotonic_time_coarse", lambda: current_time[0])
    # Also patch this test module's import so _make_advert uses the patched time
    monkeypatch.setattr("tests.test_area_selection.monotonic_time_coarse", lambda: current_time[0])


def _update_advert_stamps(adverts: dict, new_stamp: float) -> None:
    """Update the stamp attribute of all adverts to simulate new data arriving.

    This is needed for BUG 20 fix: streak counting now requires unique signals
    (different timestamps) to prevent cached values from being counted multiple times.
    """
    for advert in adverts.values():
        advert.stamp = new_stamp
        if hasattr(advert, "scanner_device") and advert.scanner_device is not None:
            advert.scanner_device.last_seen = new_stamp


@pytest.fixture
def coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Return a minimal coordinator for tests."""
    return _make_coordinator(hass)


def _configure_device(coordinator: BermudaDataUpdateCoordinator, address: str) -> BermudaDevice:
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


def test_out_of_radius_incumbent_is_dropped(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Ensure an out-of-range incumbent is discarded."""
    coordinator.options[CONF_MAX_RADIUS] = 5.0
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:FF")

    far_incumbent = _make_advert("far", "area-far", distance=10.0)
    near_challenger = _make_advert("near", "area-near", distance=3.0)

    device.area_advert = far_incumbent  # type: ignore[assignment]
    device.adverts = {
        "incumbent": far_incumbent,  # type: ignore[dict-item]
        "challenger": near_challenger,  # type: ignore[dict-item]
    }

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is near_challenger  # type: ignore[comparison-overlap]


def test_area_selected_when_only_rssi_available(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Area should be chosen even when distances are unavailable."""
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:00")

    weaker = _make_advert("weak", "area-weak", distance=None, rssi=-80.0)
    stronger = _make_advert("strong", "area-strong", distance=None, rssi=-55.0)

    device.adverts = {"weak": weaker, "strong": stronger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is stronger  # type: ignore[comparison-overlap]
    assert device.area_distance is None
    assert device.area_id == "area-strong"


def test_out_of_radius_incumbent_without_valid_challenger_clears_selection(
    coordinator: BermudaDataUpdateCoordinator,
) -> None:
    """When no contender is within radius, selection clears."""
    coordinator.options[CONF_MAX_RADIUS] = 5.0
    device = _configure_device(coordinator, "11:22:33:44:55:66")

    far_incumbent = _make_advert("far", "area-far", distance=10.0)
    far_challenger = _make_advert("far2", "area-far2", distance=8.0)

    device.area_advert = far_incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": far_incumbent, "challenger": far_challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is None


def test_near_field_absolute_improvement_wins(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Allow meaningful absolute improvement in the near field to switch areas."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)

    coordinator.options[CONF_MAX_RADIUS] = 10.0
    device = _configure_device(coordinator, "22:33:44:55:66:77")

    incumbent = _make_advert("inc", "area-old", distance=0.5)
    challenger = _make_advert("chal", "area-new", distance=0.4)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    # BUG 20 FIX: Streak counting now requires unique signals (different timestamps)
    for i in range(CROSS_FLOOR_STREAK):
        current_time[0] += 1.0
        _update_advert_stamps(device.adverts, current_time[0])
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_near_field_tiny_improvement_does_not_flip(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Small near-field deltas should not churn selection."""
    coordinator.options[CONF_MAX_RADIUS] = 10.0
    device = _configure_device(coordinator, "33:44:55:66:77:88")

    incumbent = _make_advert("inc", "area-old", distance=0.5)
    challenger = _make_advert("chal", "area-new", distance=0.49)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]


def test_far_field_small_relative_change_sticks(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Far-field small relative changes should not cause churn."""
    coordinator.options[CONF_MAX_RADIUS] = 20.0
    device = _configure_device(coordinator, "44:55:66:77:88:99")

    incumbent = _make_advert("inc", "area-old", distance=6.0)
    challenger = _make_advert("chal", "area-new", distance=5.8)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]


def test_transient_missing_distance_does_not_switch(coordinator: BermudaDataUpdateCoordinator) -> None:
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

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]


def test_history_distance_used_when_rssi_distance_none(coordinator: BermudaDataUpdateCoordinator) -> None:
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

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]
    assert device.area_distance is None


def test_soft_incumbent_does_not_block_valid_challenger(coordinator: BermudaDataUpdateCoordinator) -> None:
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
    device.area_distance_stamp = now

    challenger = _make_advert("chal", "area-new", distance=1.0)

    device.area_advert = soft_incumbent  # type: ignore[assignment]
    device.adverts = {"soft": soft_incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_soft_incumbent_no_distance_does_not_block_rssi_fallback(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Soft incumbents without distance should yield to stronger RSSI challengers."""
    device = _configure_device(coordinator, "55:66:77:88:99:AC")

    soft_incumbent = _make_advert("soft", "area-soft", distance=None, rssi=-70.0)
    challenger = _make_advert("chal", "area-new", distance=None, rssi=-50.0)

    device.area_advert = soft_incumbent  # type: ignore[assignment]
    device.adverts = {"soft": soft_incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]
    assert device.area_distance is None


def test_soft_incumbent_holds_when_no_valid_challenger(coordinator: BermudaDataUpdateCoordinator) -> None:
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
    device.area_distance_stamp = now

    # Challenger is invalid (no distance) and should not win.
    invalid_challenger = _make_advert("invalid", area_id="area-soft", distance=None, rssi=-80.0)
    invalid_challenger.stamp = now
    device.area_advert = soft_incumbent  # type: ignore[assignment]
    device.adverts = {"soft": soft_incumbent, "invalid": invalid_challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is soft_incumbent  # type: ignore[comparison-overlap]
    assert device.area_distance == 2.0


def test_stale_incumbent_allows_switch(coordinator: BermudaDataUpdateCoordinator) -> None:
    """A stale incumbent should be replaced by a valid challenger."""
    device = _configure_device(coordinator, "66:77:88:99:AA:BB")

    stale_age = AREA_MAX_AD_AGE + 1
    stale_incumbent = _make_advert("inc", "area-old", distance=2.0, age=stale_age)
    challenger = _make_advert("chal", "area-new", distance=1.0)

    device.area_advert = stale_incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": stale_incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_distance_fallback_requires_fresh_advert(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Cached distance should only be reused for fresh adverts."""
    device = _configure_device(coordinator, "77:88:99:AA:BB:CC")

    stale_soft = _make_advert("stale", "area-stale", distance=None, age=(AREA_MAX_AD_AGE * 2))
    device.area_advert = stale_soft  # type: ignore[assignment]
    device.area_distance = 3.0

    device.apply_scanner_selection(stale_soft)  # type: ignore[arg-type]

    assert device.area_id == "area-stale"
    assert device.area_distance is None
    metadata = device.area_state_metadata(stamp_now=monotonic_time_coarse())
    assert metadata["area_is_stale"] is True


def test_legitimate_move_switches_to_better_challenger(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """A meaningfully closer challenger should still win."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)

    device = _configure_device(coordinator, "77:88:99:AA:BB:CC")

    incumbent = _make_advert("inc", "area-old", distance=6.0)
    challenger = _make_advert("chal", "area-new", distance=2.5)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    # BUG 20 FIX: Streak counting now requires unique signals (different timestamps)
    for i in range(CROSS_FLOOR_STREAK):
        current_time[0] += 1.0
        _update_advert_stamps(device.adverts, current_time[0])
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_jitter_and_gaps_do_not_oscillate_selection(coordinator: BermudaDataUpdateCoordinator) -> None:
    """Minor jitter and a short gap should not cause rapid area flipping."""
    device = _configure_device(coordinator, "88:99:AA:BB:CC:DD")

    incumbent = _make_advert("inc", "area-stable", distance=2.0)
    challenger = _make_advert("chal", "area-new", distance=1.95)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)
    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]
    assert device.area_distance == 2.0

    # Simulate a transient missing distance reading while still recent.
    incumbent.rssi_distance = None
    incumbent.hist_distance_by_interval = [2.0]
    incumbent.stamp = monotonic_time_coarse()
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]

    # Jitter returns but stays within hysteresis margin.
    incumbent.rssi_distance = 1.98
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]


def test_same_floor_switch_behaviour_unaffected(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Switching on the same floor should behave as before."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)

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

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    # BUG 20 FIX: Streak counting now requires unique signals (different timestamps)
    for i in range(CROSS_FLOOR_STREAK):
        current_time[0] += 1.0
        _update_advert_stamps(device.adverts, current_time[0])
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_cross_floor_switch_blocked_without_history(coordinator: BermudaDataUpdateCoordinator) -> None:
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

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is incumbent  # type: ignore[comparison-overlap]


def test_cross_floor_switch_requires_sustained_advantage(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Cross-floor switches should happen only with sustained superiority."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)

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

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    # BUG 20 FIX: Streak counting now requires unique signals (different timestamps)
    for i in range(CROSS_FLOOR_STREAK):
        current_time[0] += 1.0
        _update_advert_stamps(device.adverts, current_time[0])
        coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_area_selection_retained_when_no_winner(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Last known area should be retained across gaps shorter than the retention window."""
    current_time = [1000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "DD:EE:FF:00:11:22")

    incumbent = _make_advert("inc", "area-stable", distance=2.5)
    device.adverts = {"incumbent": incumbent}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stable"
    current_time[0] += AREA_MAX_AD_AGE + 1
    device.adverts = {}
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stable"
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is True
    assert metadata["last_good_area_age_s"] == pytest.approx(AREA_MAX_AD_AGE + 1)


def test_retained_area_expires_after_window(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Retained selections must eventually clear when the retention window elapses."""
    current_time = [2000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "EE:FF:00:11:22:33")

    incumbent = _make_advert("inc", "area-once", distance=3.0)
    device.adverts = {"incumbent": incumbent}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    current_time[0] += AREA_RETENTION_SECONDS + 5
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id is None
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is False
    assert metadata["last_good_area_age_s"] is None


def test_fresh_advert_replaces_retained_state(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """A new contender should override retained state and reset staleness metadata."""
    current_time = [3000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "FF:00:11:22:33:44")

    incumbent = _make_advert("inc", "area-old", distance=4.0)
    device.adverts = {"incumbent": incumbent}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    current_time[0] += AREA_MAX_AD_AGE + 2
    coordinator._refresh_area_by_min_distance(device)
    assert device.area_state_metadata()["area_retained"] is True

    challenger = _make_advert("chal", "area-new", distance=1.2)
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-new"
    metadata = device.area_state_metadata()
    assert metadata["area_retained"] is False
    assert metadata["last_good_area_age_s"] == pytest.approx(0.0)


def test_rssi_winner_does_not_keep_old_distance(coordinator: BermudaDataUpdateCoordinator) -> None:
    """RSSI-only switches must not carry distance from the prior area."""
    device = _configure_device(coordinator, "FD:FE:FF:00:11:22")

    incumbent = _make_advert("inc", "area-old", distance=None, rssi=-70.0)
    challenger = _make_advert("chal", "area-new", distance=None, rssi=-40.0)

    device.area_advert = incumbent  # type: ignore[assignment]
    device.area_distance = 3.0
    device.adverts = {"inc": incumbent, "chal": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]
    assert device.area_distance is None


def test_stale_advert_still_applies_area(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Initial stale adverts should still populate area/floor and mark stale metadata."""
    current_time = [5000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:00:00:00:00:01")

    stale_age = AREA_MAX_AD_AGE + 5
    stale_advert = _make_advert("stale", "area-stale", distance=None, age=stale_age)
    device.adverts = {"stale": stale_advert}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-stale"
    metadata = device.area_state_metadata(stamp_now=current_time[0])
    assert metadata["area_is_stale"] is True


def test_stale_incumbent_ignored_by_rssi_fallback(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Stale incumbent should not block a fresh RSSI-only challenger within evidence window."""
    current_time = [7000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:02")

    stale_age = EVIDENCE_WINDOW_SECONDS + 5
    stale_incumbent = _make_advert("stale", "area-stale", distance=None, rssi=-40.0, age=stale_age)
    fresh_challenger = _make_advert("fresh", "area-fresh", distance=None, rssi=-35.0)

    device.area_advert = stale_incumbent  # type: ignore[assignment]
    device.adverts = {"stale": stale_incumbent, "fresh": fresh_challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-fresh"
    assert device.area_distance is None


def test_all_stale_adverts_result_in_no_winner(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """When all adverts are outside the evidence window, winner should be None."""
    current_time = [8000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:03")

    stale = _make_advert("stale", "area-stale", distance=None, rssi=-50.0, age=EVIDENCE_WINDOW_SECONDS + 20)
    device.area_advert = stale  # type: ignore[assignment]
    device.adverts = {"stale": stale}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id is None
    metadata = device.area_state_metadata(stamp_now=current_time[0])
    assert metadata["area_retained"] is False


def test_rssi_hysteresis_respected_within_evidence(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """RSSI hysteresis should keep the incumbent when both adverts are fresh and close."""
    current_time = [9000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:04")

    incumbent = _make_advert("inc", "area-inc", distance=None, rssi=-50.0)
    challenger = _make_advert("chal", "area-chal", distance=None, rssi=-48.5)
    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"inc": incumbent, "chal": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id == "area-inc"
    assert device.area_distance is None


def test_set_ref_power_fast_acquire_when_lost(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Ref power recalcs may fast-acquire when no current area is set."""
    current_time = [10000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:05")

    stale_stamp = current_time[0] - (EVIDENCE_WINDOW_SECONDS + 100)

    class DummyAdvert(SimpleNamespace):
        def set_ref_power(self, new_ref_power: float) -> float | None:
            return self.rssi_distance  # type: ignore[no-any-return]

    stale = DummyAdvert(
        name="stale",
        area_id="area-stale",
        area_name="area-stale",
        rssi_distance=2.5,
        rssi=-60.0,
        stamp=stale_stamp,
        scanner_address="scanner-stale",
        scanner_device=None,
    )
    device.adverts = {"stale": stale}  # type: ignore[dict-item]
    device.area_advert = None
    device.area_state_stamp = None
    device.last_seen = stale_stamp

    device.set_ref_power(-70.0)

    assert device.area_id == "area-stale"
    assert device.area_state_stamp is None
    assert device.last_seen == stale_stamp


def test_set_ref_power_updates_distance_only_with_evidence(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Ref power recalcs may refresh distance when evidence is fresh without minting new presence."""
    current_time = [11000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:06")

    class DummyAdvert(SimpleNamespace):
        def set_ref_power(self, new_ref_power: float) -> float | None:
            self.rssi_distance = 1.5
            return self.rssi_distance

    fresh_stamp = current_time[0] - 5
    advert = DummyAdvert(
        name="fresh",
        area_id="area-fresh",
        area_name="area-fresh",
        rssi_distance=2.5,
        rssi=-55.0,
        stamp=fresh_stamp,
        scanner_address="scanner-fresh",
        scanner_device=None,
    )
    device.adverts = {"fresh": advert}  # type: ignore[dict-item]
    device.apply_scanner_selection(advert)  # type: ignore[arg-type]
    assert device.area_id == "area-fresh"
    assert device.area_distance == 2.5
    baseline_last_seen = device.last_seen
    baseline_stamp = device.area_state_stamp

    device.set_ref_power(-65.0)

    assert device.area_id == "area-fresh"
    assert device.area_distance == 1.5
    assert device.last_seen == baseline_last_seen
    assert device.area_state_stamp == baseline_stamp


def test_last_seen_not_refreshed_without_new_advert(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """last_seen must not advance when re-applying the same advert without new evidence."""
    current_time = [12000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:07")

    advert = _make_advert("inc", "area-stale", distance=3.0, age=0.0)
    device.adverts = {"inc": advert}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)
    initial_last_seen = device.last_seen
    assert initial_last_seen == pytest.approx(advert.stamp)

    current_time[0] += 300.0
    coordinator._refresh_area_by_min_distance(device)

    assert device.last_seen == pytest.approx(initial_last_seen)
    assert device.area_id == "area-stale"


def test_presence_respects_devtracker_timeout(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Device tracker presence should time out based on last_seen, not retention."""
    current_time = [13000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:08")

    advert = _make_advert("inc", "area-presence", distance=2.0, age=0.0)
    device.adverts = {"inc": advert}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    current_time[0] += DEFAULT_DEVTRACK_TIMEOUT + 5
    coordinator._refresh_area_by_min_distance(device)
    device.calculate_data()

    assert device.last_seen == pytest.approx(advert.stamp)
    assert device.zone == STATE_NOT_HOME


def test_last_seen_advances_only_with_newer_advert(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """last_seen should advance when a newer advert arrives, not on repeated cycles."""
    current_time = [14000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:09")

    first = _make_advert("inc", "area-one", distance=1.0, age=0.0)
    device.adverts = {"inc": first}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)
    first_seen = device.last_seen

    current_time[0] += 10.0
    second = _make_advert("inc", "area-one", distance=0.8, age=0.0)
    device.adverts = {"inc": second}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    assert device.last_seen > first_seen
    assert device.last_seen == pytest.approx(second.stamp)


def test_stale_advert_expires_after_evidence_window(
    monkeypatch: pytest.MonkeyPatch, coordinator: BermudaDataUpdateCoordinator
) -> None:
    """Stale adverts left in memory must not prevent expiry after retention window."""
    current_time = [6000.0]
    _patch_monotonic_time(monkeypatch, current_time)
    device = _configure_device(coordinator, "AA:BB:CC:DD:EE:01")

    advert = _make_advert("inc", "area-once", distance=3.0)
    device.adverts = {"inc": advert}  # type: ignore[dict-item]
    coordinator._refresh_area_by_min_distance(device)

    initial_stamp = device.area_state_stamp
    assert device.area_id == "area-once"

    current_time[0] += AREA_RETENTION_SECONDS + 10
    coordinator._refresh_area_by_min_distance(device)

    assert device.area_id is None
    if device.area_state_stamp is not None:
        assert device.area_state_stamp == pytest.approx(initial_stamp)
    metadata = device.area_state_metadata(stamp_now=current_time[0])
    assert metadata["area_retained"] is False
    assert metadata["last_good_area_age_s"] is None


def test_floor_level_populated_from_floor_registry(coordinator: BermudaDataUpdateCoordinator) -> None:
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

    device.fr = SimpleNamespace(  # type: ignore[assignment]
        async_get_floor=lambda floor_id: dummy_floor if floor_id == dummy_floor.floor_id else None
    )
    device.ar = SimpleNamespace(async_get_area=lambda area_id: dummy_area if area_id == "area-kitchen" else None)  # type: ignore[assignment]

    device.update_area_and_floor("area-kitchen")

    assert device.floor_level == 1  # type: ignore[comparison-overlap]
    assert device.floor_name == "Level 1"
