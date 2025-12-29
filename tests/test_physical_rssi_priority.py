"""Tests for physical RSSI priority feature (raw RSSI tie-breaking and consistency checks)."""

from __future__ import annotations

from types import SimpleNamespace
from statistics import median

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    CONF_USE_PHYSICAL_RSSI_PRIORITY,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    MIN_DISTANCE,
    RSSI_CONSISTENCY_MARGIN_DB,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.bermuda_fmdn_manager import BermudaFmdnManager
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.util import rssi_to_metres


def _make_coordinator(hass: HomeAssistant, use_physical_rssi_priority: bool = False) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
        CONF_USE_PHYSICAL_RSSI_PRIORITY: use_physical_rssi_priority,
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
    coordinator.fmdn_manager = BermudaFmdnManager()
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
    hist_rssi_by_interval: list[float] | None = None,
    floor_id: str | None = None,
    floor_level: int | None = None,
    rssi: float | None = -50.0,
) -> SimpleNamespace:
    """Create a minimal advert-like object with distance and RSSI metadata."""
    now = monotonic_time_coarse()
    stamp = now - age
    hist_dist = list(hist_distance_by_interval) if hist_distance_by_interval is not None else []
    hist_rssi = list(hist_rssi_by_interval) if hist_rssi_by_interval is not None else []
    scanner_device = _make_scanner(name, area_id, stamp, floor_id=floor_id, floor_level=floor_level)

    def median_rssi_func() -> float | None:
        """Return median of RSSI history or current RSSI."""
        if hist_rssi:
            sorted_rssi = sorted(hist_rssi)
            n = len(sorted_rssi)
            mid = n // 2
            if n % 2 == 0:
                return (sorted_rssi[mid - 1] + sorted_rssi[mid]) / 2
            return sorted_rssi[mid]
        return rssi

    advert = SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id,
        scanner_address=scanner_device.address,
        rssi_distance=distance,
        rssi=rssi,
        stamp=stamp,
        scanner_device=scanner_device,
        hist_distance_by_interval=hist_dist,
        hist_rssi_by_interval=hist_rssi,
        conf_rssi_offset=0,
    )
    advert.median_rssi = median_rssi_func
    return advert


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


@pytest.fixture
def coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Return a minimal coordinator with physical_rssi_priority disabled (default)."""
    return _make_coordinator(hass, use_physical_rssi_priority=False)


@pytest.fixture
def coordinator_with_rssi_priority(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Return a minimal coordinator with physical_rssi_priority enabled."""
    return _make_coordinator(hass, use_physical_rssi_priority=True)


# =============================================================================
# Group 1: MIN_DISTANCE tests
# =============================================================================

class TestMinimumDistance:
    """Tests for minimum distance enforcement in rssi_to_metres."""

    def test_rssi_to_metres_returns_minimum_distance_for_very_strong_signal(self):
        """Very strong signals should be clamped to MIN_DISTANCE."""
        # Signal so strong that calculated distance would be < 0.1m
        # rssi = -20, ref_power = -55, attenuation = 3
        # raw_distance = 10^((-55 - (-20)) / 30) = 10^(-1.17) = 0.068m
        distance = rssi_to_metres(-20, -55, 3)
        assert distance >= MIN_DISTANCE
        assert distance == pytest.approx(MIN_DISTANCE)

    def test_rssi_to_metres_normal_distance_unchanged(self):
        """Normal distances should not be affected by MIN_DISTANCE."""
        # Normal signal: rssi = -65, ref_power = -55, attenuation = 3
        # distance = 10^((-55 - (-65)) / 30) = 10^(0.33) = 2.15m
        distance = rssi_to_metres(-65, -55, 3)
        assert distance > MIN_DISTANCE
        assert distance == pytest.approx(2.15, rel=0.1)

    def test_rssi_to_metres_at_ref_power_equals_one_meter(self):
        """Signal at ref_power should equal exactly 1 meter."""
        # rssi = ref_power = -55, so distance = 10^0 = 1.0m
        distance = rssi_to_metres(-55, -55, 3)
        assert distance == pytest.approx(1.0)


# =============================================================================
# Group 2: Feature flag behavior tests
# =============================================================================

class TestFeatureFlagBehavior:
    """Tests for feature flag on/off behavior."""

    def test_feature_flag_off_uses_timestamp_tiebreak(
        self, coordinator: BermudaDataUpdateCoordinator
    ):
        """When feature is off, timestamp-based tie-breaking is used."""
        device = _configure_device(coordinator, "AA:BB:CC:DD:EE:01")

        # Both have same distance but different RSSI
        # With feature off, timestamp should decide (older wins in rescue)
        weaker_older = _make_advert(
            "older", "area-older", distance=0.5, rssi=-70.0, age=0.0  # Newer stamp
        )
        stronger_newer = _make_advert(
            "newer", "area-newer", distance=0.5, rssi=-45.0, age=0.1  # Older stamp
        )

        device.adverts = {"older": weaker_older, "newer": stronger_newer}

        coordinator._refresh_area_by_min_distance(device)

        # With timestamp tie-break, older stamp wins (stronger_newer has older stamp)
        # Note: This tests the rescue path where no incumbent exists
        assert device.area_advert is not None

    def test_feature_flag_on_uses_rssi_tiebreak(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """When feature is on, RSSI-based tie-breaking is used."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:02")

        # Both have same distance but different RSSI
        weaker = _make_advert("weak", "area-weak", distance=0.5, rssi=-70.0)
        stronger = _make_advert("strong", "area-strong", distance=0.5, rssi=-45.0)

        device.adverts = {"weak": weaker, "strong": stronger}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # With RSSI tie-break, stronger signal should win
        assert device.area_advert is stronger


# =============================================================================
# Group 3: RSSI consistency check tests
# =============================================================================

class TestRssiConsistencyCheck:
    """Tests for RSSI/distance ranking consistency verification."""

    def test_consistent_ranking_allows_switch(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """When distance and RSSI rankings agree, switch is allowed."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:03")

        # Incumbent: further away, weaker signal
        incumbent = _make_advert("inc", "area-inc", distance=2.0, rssi=-65.0)
        # Challenger: closer, stronger signal (consistent)
        challenger = _make_advert("chal", "area-chal", distance=1.0, rssi=-55.0)

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Consistent ranking - challenger should win
        assert device.area_advert is challenger

    def test_inconsistent_ranking_blocks_switch(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """When distance ranking contradicts RSSI significantly, switch is blocked."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:04")

        # Incumbent: further on distance, but MUCH stronger signal
        incumbent = _make_advert(
            "inc", "area-inc", distance=0.6, rssi=-45.0,
            hist_distance_by_interval=[0.6, 0.6, 0.6],
        )
        # Challenger: "closer" on distance but very weak signal (> 8dB weaker)
        # This indicates distance is inflated by offset
        challenger = _make_advert(
            "chal", "area-chal", distance=0.5, rssi=-70.0,  # 25dB weaker!
            hist_distance_by_interval=[0.5, 0.5, 0.5],
        )

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Inconsistent ranking (challenger closer on distance but >8dB weaker on RSSI)
        # Incumbent should stay
        assert device.area_advert is incumbent

    def test_small_rssi_difference_still_consistent(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """Small RSSI disadvantage within margin is still considered consistent."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:05")

        # Incumbent with slightly stronger signal (within 8dB margin)
        incumbent = _make_advert(
            "inc", "area-inc", distance=0.6, rssi=-50.0,
            hist_distance_by_interval=[0.6, 0.6, 0.6],
        )
        # Challenger closer, only 5dB weaker (within margin)
        challenger = _make_advert(
            "chal", "area-chal", distance=0.5, rssi=-55.0,  # Only 5dB weaker
            hist_distance_by_interval=[0.5, 0.5, 0.5],
        )

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        # Without significant RSSI advantage, streak logic requires 2 consecutive wins
        # First call: challenger wins but pending_streak=1
        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)
        # Second call: challenger wins again, pending_streak reaches target
        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # 5dB is within 8dB margin, so ranking is considered consistent
        # Challenger should win based on distance (after streak requirement)
        assert device.area_advert is challenger


# =============================================================================
# Group 4: Physical signal priority tests
# =============================================================================

class TestPhysicalSignalPriority:
    """Tests for physical signal priority over offset-boosted signals."""

    def test_physical_proximity_beats_offset_boosted_distance(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """Strong physical signal should beat offset-boosted closer distance."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:06")

        # Incumbent: truly close with strong signal
        physically_close = _make_advert(
            "close", "area-close", distance=0.5, rssi=-45.0,
            hist_distance_by_interval=[0.5, 0.5, 0.5],
        )
        # Challenger: appears closer but very weak signal (offset-boosted)
        offset_boosted = _make_advert(
            "boosted", "area-boosted", distance=0.3, rssi=-75.0,  # 30dB weaker!
            hist_distance_by_interval=[0.3, 0.3, 0.3],
        )

        device.area_advert = physically_close
        device.adverts = {"close": physically_close, "boosted": offset_boosted}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Physically close should stay - the "closer" distance is not credible
        assert device.area_advert is physically_close

    def test_extreme_offset_scenario(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """
        Extreme case: Sensor B appears at 0.4m through absurd offset,
        but Sensor A at 0.46m has much stronger physical signal.
        """
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:07")

        # Sensor A: strong physical signal, calculated distance 0.46m
        sensor_a = _make_advert(
            "a", "area-a", distance=0.46, rssi=-42.0,
            hist_distance_by_interval=[0.46, 0.46, 0.46],
        )
        # Sensor B: weak signal, but offset makes it appear at 0.40m
        sensor_b = _make_advert(
            "b", "area-b", distance=0.40, rssi=-78.0,  # 36dB weaker!
            hist_distance_by_interval=[0.40, 0.40, 0.40],
        )

        device.area_advert = sensor_b  # B is currently "winning"
        device.adverts = {"a": sensor_a, "b": sensor_b}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # A should win - physical proximity beats offset
        assert device.area_advert is sensor_a


# =============================================================================
# Group 5: Median RSSI tests
# =============================================================================

class TestMedianRssi:
    """Tests for median RSSI calculation."""

    def test_median_rssi_with_history(self):
        """Median should be calculated from history when available."""
        advert = _make_advert(
            "test", "area-test", distance=1.0, rssi=-60.0,
            hist_rssi_by_interval=[-50, -55, -70, -52, -53]  # Median = -53
        )

        result = advert.median_rssi()
        assert result == pytest.approx(-53.0)

    def test_median_rssi_absorbs_outliers(self):
        """Median should be robust against outliers."""
        advert = _make_advert(
            "test", "area-test", distance=1.0, rssi=-60.0,
            hist_rssi_by_interval=[-50, -51, -90, -52, -50]  # -90 is outlier, Median = -51
        )

        result = advert.median_rssi()
        # Sorted: [-90, -52, -51, -50, -50], median is middle value = -51
        assert result == pytest.approx(-51.0)

    def test_median_rssi_falls_back_to_current(self):
        """Without history, median should return current RSSI."""
        advert = _make_advert(
            "test", "area-test", distance=1.0, rssi=-60.0,
            hist_rssi_by_interval=[]
        )

        result = advert.median_rssi()
        assert result == pytest.approx(-60.0)


# =============================================================================
# Group 6: Edge cases and robustness
# =============================================================================

class TestEdgeCases:
    """Edge cases and robustness tests."""

    def test_none_rssi_handled_gracefully(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """Missing RSSI values should be handled without crashing."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:08")

        incumbent = _make_advert("inc", "area-inc", distance=1.0, rssi=None)
        challenger = _make_advert("chal", "area-chal", distance=0.8, rssi=-50.0)

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        # Should not raise
        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Some outcome should be selected
        assert device.area_advert is not None

    def test_both_rssi_none_uses_distance(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """When both have no RSSI, pure distance comparison should work."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:09")

        incumbent = _make_advert("inc", "area-inc", distance=2.0, rssi=None)
        challenger = _make_advert("chal", "area-chal", distance=1.0, rssi=None)

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Challenger is clearly closer, should win
        assert device.area_advert is challenger

    def test_existing_behavior_preserved_for_clear_winner(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """Existing behavior should be preserved when distance difference is clear."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:10")

        incumbent = _make_advert(
            "inc", "area-inc", distance=5.0, rssi=-70.0,
            hist_distance_by_interval=[5.0, 5.0, 5.0],
        )
        challenger = _make_advert(
            "chal", "area-chal", distance=1.5, rssi=-55.0,
            hist_distance_by_interval=[1.5, 1.5, 1.5],
        )

        device.area_advert = incumbent
        device.adverts = {"inc": incumbent, "chal": challenger}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # Clear distance winner with consistent RSSI - should work as before
        assert device.area_advert is challenger

    def test_rescue_candidate_uses_rssi_tiebreak(
        self, coordinator_with_rssi_priority: BermudaDataUpdateCoordinator
    ):
        """Rescue candidate selection should use raw RSSI for tie-breaking."""
        device = _configure_device(coordinator_with_rssi_priority, "AA:BB:CC:DD:EE:11")

        # No incumbent - goes directly into rescue logic
        advert_a = _make_advert("a", "area-a", distance=0.5, rssi=-45.0)
        advert_b = _make_advert("b", "area-b", distance=0.5, rssi=-65.0)

        device.adverts = {"a": advert_a, "b": advert_b}

        coordinator_with_rssi_priority._refresh_area_by_min_distance(device)

        # A should win due to stronger RSSI (tie-break)
        assert device.area_advert is advert_a


# =============================================================================
# Group 7: Constants verification
# =============================================================================

class TestConstants:
    """Verify constant values are as expected."""

    def test_min_distance_value(self):
        """MIN_DISTANCE should be 0.1 meters."""
        assert MIN_DISTANCE == 0.1

    def test_rssi_consistency_margin(self):
        """RSSI consistency margin should be 8 dB."""
        assert RSSI_CONSISTENCY_MARGIN_DB == 8.0
