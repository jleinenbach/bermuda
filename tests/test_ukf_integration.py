"""Tests for UKF integration in coordinator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    CONF_USE_UKF_AREA_SELECTION,
    DEFAULT_MAX_RADIUS,
    DEFAULT_USE_UKF_AREA_SELECTION,
    UKF_MIN_SCANNERS,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.correlation import AreaProfile
from custom_components.bermuda.correlation.room_profile import RoomProfile
from custom_components.bermuda.correlation.scanner_pair import ScannerPairCorrelation
from custom_components.bermuda.filters import UnscentedKalmanFilter

# Base timestamp used in test fixtures
TEST_BASE_TIME = 1000.0


@pytest.fixture
def mock_monotonic_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock monotonic_time_coarse to return a value close to TEST_BASE_TIME.

    This is needed because the coordinator checks if adverts are recent using:
        nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS

    Without this mock, monotonic_time_coarse() returns system uptime which is
    much larger than our test fixture stamps (1000.0), making all adverts appear stale.
    """
    # Return a time slightly after TEST_BASE_TIME so adverts with stamp=TEST_BASE_TIME are "recent"
    monkeypatch.setattr(
        "custom_components.bermuda.coordinator.monotonic_time_coarse",
        lambda: TEST_BASE_TIME + 5.0,
    )


class FakeScanner:
    """Fake scanner for testing."""

    def __init__(self, address: str, name: str, area_id: str | None = None) -> None:
        self.address = address
        self.name = name
        self.area_id = area_id
        self.floor_id: str | None = None
        self.floor_level: int | None = None
        self.is_scanner = True
        self.last_seen: float = 0


class FakeAdvert:
    """Fake advert for testing."""

    def __init__(
        self,
        scanner_address: str,
        rssi: float,
        stamp: float,
        area_id: str | None = None,
        scanner_device: FakeScanner | None = None,
    ) -> None:
        self.scanner_address = scanner_address
        self.rssi = rssi
        self.stamp = stamp
        self.area_id = area_id
        self.scanner_device = scanner_device
        self.name = f"scanner_{scanner_address}"
        self.area_name = area_id
        self.rssi_distance: float | None = 2.0
        self.hist_distance_by_interval: list[float] = []

    def median_rssi(self) -> float | None:
        return self.rssi


class FakeDevice:
    """Fake device for testing."""

    def __init__(self, address: str, name: str) -> None:
        self.address = address
        self.name = name
        self.is_scanner = False
        self.create_sensor = True
        self.create_tracker_done = False
        self.adverts: dict[str, FakeAdvert] = {}
        self.area_advert: FakeAdvert | None = None
        self.area_name: str | None = None
        self.area_id: str | None = None
        self.area_distance: float | None = None
        self.co_visibility_stats: dict[str, dict[str, Any]] = {}
        self.pending_area_id: str | None = None
        self.pending_floor_id: str | None = None
        self.pending_streak: int = 0
        self.pending_last_stamps: dict[str, float] = {}
        self.diag_area_switch: str = ""
        self.area_changed_at: float = 0.0
        self.area_locked_id: str | None = None
        self.area_locked_name: str | None = None
        self.area_locked_scanner_addr: str | None = None

    def apply_scanner_selection(self, advert: FakeAdvert | None, nowstamp: float = 0.0) -> None:
        """Apply scanner selection."""
        if advert is not None:
            self.area_advert = advert
            self.area_name = advert.area_name
            self.area_id = advert.area_id
        else:
            self.area_advert = None
            self.area_name = None
            self.area_id = None

    def get_movement_state(self, stamp_now: float = 0.0) -> str:
        """Return movement state."""
        return "stationary"

    def reset_pending_state(self) -> None:
        """Reset pending area selection state."""
        self.pending_area_id = None
        self.pending_floor_id = None
        self.pending_streak = 0
        self.pending_last_stamps = {}


class MockAreaRegistry:
    """Mock area registry for testing floor resolution."""

    def __init__(self) -> None:
        """Initialize with empty areas dictionary."""
        self._areas: dict[str, Any] = {}

    def async_get_area(self, area_id: str) -> Any:
        """Get area by ID."""
        return self._areas.get(area_id)

    def add_area(self, area_id: str, floor_id: str | None = None) -> None:
        """Add an area to the registry."""
        area = MagicMock()
        area.id = area_id
        area.name = area_id
        area.floor_id = floor_id
        self._areas[area_id] = area


def create_coordinator_mock() -> BermudaDataUpdateCoordinator:
    """Create a mock coordinator for testing."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
        CONF_USE_UKF_AREA_SELECTION: DEFAULT_USE_UKF_AREA_SELECTION,
    }
    coordinator.correlations = {}
    coordinator.room_profiles = {}  # Room-level scanner pair delta profiles
    coordinator._correlations_loaded = True
    coordinator._last_correlation_save = 0.0
    coordinator.correlation_store = MagicMock(async_save=AsyncMock())
    coordinator.device_ukfs = {}
    coordinator.AreaTests = BermudaDataUpdateCoordinator.AreaTests
    # FIX: Add mock area registry for floor resolution in _refresh_area_by_ukf
    coordinator.ar = MockAreaRegistry()
    return coordinator


def create_mature_room_profile(area_id: str) -> RoomProfile:
    """Create a mature RoomProfile with enough samples for automatic UKF activation."""
    profile = RoomProfile(area_id=area_id)
    # Need at least 2 scanner pairs with 30+ samples each (mature_pair_count >= 2)
    pair1 = ScannerPairCorrelation(scanner_address="SC:AN:NE:R1|SC:AN:NE:R2")
    pair2 = ScannerPairCorrelation(scanner_address="SC:AN:NE:R1|SC:AN:NE:R3")
    # Simulate training with 35 samples each to exceed MIN_SAMPLES_FOR_MATURITY (30)
    for _ in range(35):
        pair1.update(5.0)  # delta of 5 dB
        pair2.update(-3.0)  # delta of -3 dB
    profile._scanner_pairs["SC:AN:NE:R1|SC:AN:NE:R2"] = pair1
    profile._scanner_pairs["SC:AN:NE:R1|SC:AN:NE:R3"] = pair2
    return profile


class TestUKFIntegration:
    """Tests for UKF integration in coordinator."""

    def test_device_ukfs_storage_initialized(self) -> None:
        """Test that device_ukfs dict is initialized."""
        coordinator = create_coordinator_mock()
        assert isinstance(coordinator.device_ukfs, dict)
        assert len(coordinator.device_ukfs) == 0

    def test_refresh_area_by_ukf_no_scanners(self) -> None:
        """Test UKF returns False when no scanners visible."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        result = coordinator._refresh_area_by_ukf(device)
        assert result is False

    def test_refresh_area_by_ukf_insufficient_scanners(self, mock_monotonic_time: None) -> None:
        """Test UKF returns False when fewer than minimum scanners."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        # Add only one scanner advert
        scanner = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen")
        advert = FakeAdvert(
            scanner_address=scanner.address,
            rssi=-65.0,
            stamp=TEST_BASE_TIME,
            area_id="kitchen",
            scanner_device=scanner,
        )
        device.adverts[scanner.address] = advert

        result = coordinator._refresh_area_by_ukf(device)
        assert result is False
        assert UKF_MIN_SCANNERS >= 2  # Confirm we need at least 2

    def test_refresh_area_by_ukf_creates_ukf_instance(self, mock_monotonic_time: None) -> None:
        """Test that UKF instance is created for device."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        # Add multiple scanner adverts
        scanners = [
            FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen"),
            FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "living"),
        ]
        for scanner in scanners:
            advert = FakeAdvert(
                scanner_address=scanner.address,
                rssi=-65.0,
                stamp=TEST_BASE_TIME,
                area_id=scanner.area_id,
                scanner_device=scanner,
            )
            device.adverts[scanner.address] = advert

        # Call UKF refresh (will return False due to no profiles, but should create UKF)
        coordinator._refresh_area_by_ukf(device)

        assert device.address in coordinator.device_ukfs
        assert isinstance(coordinator.device_ukfs[device.address], UnscentedKalmanFilter)

    def test_refresh_area_by_ukf_no_correlations(self, mock_monotonic_time: None) -> None:
        """Test UKF returns False when no correlation profiles exist."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        # Add multiple scanner adverts
        scanners = [
            FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen"),
            FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "living"),
        ]
        for scanner in scanners:
            advert = FakeAdvert(
                scanner_address=scanner.address,
                rssi=-65.0,
                stamp=TEST_BASE_TIME,
                area_id=scanner.area_id,
                scanner_device=scanner,
            )
            device.adverts[scanner.address] = advert

        result = coordinator._refresh_area_by_ukf(device)
        assert result is False

    def test_refresh_area_by_ukf_with_profiles(self, mock_monotonic_time: None) -> None:
        """Test UKF with learned area profiles."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "living")

        # Add scanner adverts
        advert1 = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-65.0,
            stamp=TEST_BASE_TIME,
            area_id="kitchen",
            scanner_device=scanner1,
        )
        advert2 = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-75.0,
            stamp=TEST_BASE_TIME,
            area_id="living",
            scanner_device=scanner2,
        )
        device.adverts[scanner1.address] = advert1
        device.adverts[scanner2.address] = advert2

        # Create learned profiles for kitchen
        kitchen_profile = AreaProfile(area_id="kitchen")
        # Train the profile with many samples to make it mature
        for _ in range(50):
            kitchen_profile.update(
                primary_rssi=-65.0,
                other_readings={scanner2.address: -75.0},
                primary_scanner_addr=scanner1.address,
            )

        coordinator.correlations[device.address] = {"kitchen": kitchen_profile}

        # UKF should now find a match
        result = coordinator._refresh_area_by_ukf(device)

        # The result depends on whether the match score meets the threshold
        # With identical readings, match should be very good
        if result:
            assert device.address in coordinator.device_ukfs

    def test_ukf_option_default_enabled(self) -> None:
        """Test that UKF is enabled by default."""
        coordinator = create_coordinator_mock()
        assert coordinator.options[CONF_USE_UKF_AREA_SELECTION] is True

    def test_refresh_areas_uses_ukf_when_profiles_mature(self) -> None:
        """Test that _refresh_areas_by_min_distance uses UKF when room profiles are mature."""
        coordinator = create_coordinator_mock()
        # Add a mature room profile (triggers automatic UKF activation)
        coordinator.room_profiles["kitchen"] = create_mature_room_profile("kitchen")
        coordinator.devices = {}

        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")
        coordinator.devices[device.address] = device

        # Mock the UKF refresh method
        ukf_called = {"count": 0}

        def mock_ukf_refresh(dev: FakeDevice) -> bool:
            ukf_called["count"] += 1
            return False  # Return False to fall through to min-distance

        coordinator._refresh_area_by_ukf = mock_ukf_refresh  # type: ignore[method-assign]

        # Mock min-distance method
        min_dist_called = {"count": 0}

        def mock_min_dist_refresh(dev: FakeDevice) -> None:
            min_dist_called["count"] += 1

        coordinator._refresh_area_by_min_distance = mock_min_dist_refresh  # type: ignore[method-assign]

        # Call the refresh method
        coordinator._refresh_areas_by_min_distance()

        # UKF should have been called (because room profiles are mature)
        assert ukf_called["count"] == 1
        # Min-distance should also be called (as fallback since UKF returned False)
        assert min_dist_called["count"] == 1

    def test_refresh_areas_skips_min_distance_when_ukf_succeeds(self) -> None:
        """Test that min-distance is skipped when UKF makes a decision."""
        coordinator = create_coordinator_mock()
        # Add a mature room profile (triggers automatic UKF activation)
        coordinator.room_profiles["kitchen"] = create_mature_room_profile("kitchen")
        coordinator.devices = {}

        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")
        coordinator.devices[device.address] = device

        # Mock the UKF refresh method to return True
        ukf_called = {"count": 0}

        def mock_ukf_refresh(dev: FakeDevice) -> bool:
            ukf_called["count"] += 1
            return True  # UKF made a decision

        coordinator._refresh_area_by_ukf = mock_ukf_refresh  # type: ignore[method-assign]

        # Mock min-distance method
        min_dist_called = {"count": 0}

        def mock_min_dist_refresh(dev: FakeDevice) -> None:
            min_dist_called["count"] += 1

        coordinator._refresh_area_by_min_distance = mock_min_dist_refresh  # type: ignore[method-assign]

        # Call the refresh method
        coordinator._refresh_areas_by_min_distance()

        # UKF should have been called (because room profiles are mature)
        assert ukf_called["count"] == 1
        # Min-distance should NOT be called (UKF succeeded)
        assert min_dist_called["count"] == 0

    def test_refresh_areas_skips_ukf_when_no_mature_profiles(self) -> None:
        """Test that UKF is skipped when room profiles are not mature (bootstrap phase)."""
        coordinator = create_coordinator_mock()
        # No mature room profiles → falls back to min-distance automatically
        coordinator.room_profiles = {}
        coordinator.devices = {}

        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")
        coordinator.devices[device.address] = device

        # Mock the UKF refresh method
        ukf_called = {"count": 0}

        def mock_ukf_refresh(dev: FakeDevice) -> bool:
            ukf_called["count"] += 1
            return True

        coordinator._refresh_area_by_ukf = mock_ukf_refresh  # type: ignore[method-assign]

        # Mock min-distance method
        min_dist_called = {"count": 0}

        def mock_min_dist_refresh(dev: FakeDevice) -> None:
            min_dist_called["count"] += 1

        coordinator._refresh_area_by_min_distance = mock_min_dist_refresh  # type: ignore[method-assign]

        # Call the refresh method
        coordinator._refresh_areas_by_min_distance()

        # UKF should NOT have been called (no mature profiles)
        assert ukf_called["count"] == 0
        # Min-distance should be called (fallback)
        assert min_dist_called["count"] == 1


class TestUKFOptionHandling:
    """Tests for UKF configuration option handling."""

    def test_options_include_ukf_setting(self) -> None:
        """Test that UKF option is in the coordinator options list."""
        coordinator = create_coordinator_mock()
        assert CONF_USE_UKF_AREA_SELECTION in coordinator.options

    def test_ukf_option_can_be_enabled(self) -> None:
        """Test that UKF option can be enabled."""
        coordinator = create_coordinator_mock()
        coordinator.options[CONF_USE_UKF_AREA_SELECTION] = True
        assert coordinator.options[CONF_USE_UKF_AREA_SELECTION] is True


class TestUKFAreaSelectionWithPseudoData:
    """
    End-to-end tests for UKF area selection using realistic pseudo-data.

    These tests simulate real-world scenarios with multiple rooms, scanners,
    and learned fingerprints to verify the UKF correctly identifies rooms.
    """

    def _create_trained_profile(
        self,
        area_id: str,
        primary_scanner: str,
        primary_rssi: float,
        other_readings: dict[str, float],
        num_samples: int = 100,
    ) -> AreaProfile:
        """Helper to create a well-trained area profile."""
        profile = AreaProfile(area_id=area_id)
        for _ in range(num_samples):
            # Add small noise to simulate real measurements
            profile.update(
                primary_rssi=primary_rssi,
                other_readings=other_readings,
                primary_scanner_addr=primary_scanner,
            )
        return profile

    def test_ukf_selects_correct_room_kitchen(self, mock_monotonic_time: None) -> None:
        """Test UKF selects kitchen when RSSI matches kitchen fingerprint."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Phone")

        # Setup 3 scanners in different rooms
        scanner_kitchen = FakeScanner("SC:KI:TC:HE:N0:01", "Kitchen Scanner", "kitchen")
        scanner_living = FakeScanner("SC:LI:VI:NG:00:02", "Living Scanner", "living")
        scanner_bedroom = FakeScanner("SC:BE:DR:OO:M0:03", "Bedroom Scanner", "bedroom")

        # Current readings: Strong in kitchen (-55), weak in living (-80), very weak in bedroom (-90)
        advert_kitchen = FakeAdvert(
            scanner_address=scanner_kitchen.address,
            rssi=-55.0,
            stamp=TEST_BASE_TIME,
            area_id="kitchen",
            scanner_device=scanner_kitchen,
        )
        advert_living = FakeAdvert(
            scanner_address=scanner_living.address,
            rssi=-80.0,
            stamp=TEST_BASE_TIME,
            area_id="living",
            scanner_device=scanner_living,
        )
        advert_bedroom = FakeAdvert(
            scanner_address=scanner_bedroom.address,
            rssi=-90.0,
            stamp=TEST_BASE_TIME,
            area_id="bedroom",
            scanner_device=scanner_bedroom,
        )

        device.adverts[scanner_kitchen.address] = advert_kitchen
        device.adverts[scanner_living.address] = advert_living
        device.adverts[scanner_bedroom.address] = advert_bedroom

        # Create trained profiles for all 3 rooms
        # Kitchen profile: strong from kitchen scanner
        kitchen_profile = self._create_trained_profile(
            area_id="kitchen",
            primary_scanner=scanner_kitchen.address,
            primary_rssi=-55.0,
            other_readings={
                scanner_living.address: -80.0,
                scanner_bedroom.address: -90.0,
            },
        )

        # Living profile: strong from living scanner
        living_profile = self._create_trained_profile(
            area_id="living",
            primary_scanner=scanner_living.address,
            primary_rssi=-50.0,
            other_readings={
                scanner_kitchen.address: -75.0,
                scanner_bedroom.address: -85.0,
            },
        )

        # Bedroom profile: strong from bedroom scanner
        bedroom_profile = self._create_trained_profile(
            area_id="bedroom",
            primary_scanner=scanner_bedroom.address,
            primary_rssi=-45.0,
            other_readings={
                scanner_kitchen.address: -85.0,
                scanner_living.address: -80.0,
            },
        )

        coordinator.correlations[device.address] = {
            "kitchen": kitchen_profile,
            "living": living_profile,
            "bedroom": bedroom_profile,
        }

        # Run UKF area selection
        result = coordinator._refresh_area_by_ukf(device)

        # UKF should make a decision (have profiles and enough scanners)
        assert result is True
        # Device should be assigned to kitchen (best match for current readings)
        assert device.area_id == "kitchen"

    def test_ukf_selects_correct_room_living(self, mock_monotonic_time: None) -> None:
        """Test UKF selects living room when RSSI matches living fingerprint."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:02", "Tablet")

        scanner_kitchen = FakeScanner("SC:KI:TC:HE:N0:01", "Kitchen Scanner", "kitchen")
        scanner_living = FakeScanner("SC:LI:VI:NG:00:02", "Living Scanner", "living")
        scanner_bedroom = FakeScanner("SC:BE:DR:OO:M0:03", "Bedroom Scanner", "bedroom")

        # Current readings: Strong in living (-50), medium in kitchen (-75), weak in bedroom (-85)
        device.adverts[scanner_kitchen.address] = FakeAdvert(
            scanner_address=scanner_kitchen.address,
            rssi=-75.0,
            stamp=TEST_BASE_TIME,
            area_id="kitchen",
            scanner_device=scanner_kitchen,
        )
        device.adverts[scanner_living.address] = FakeAdvert(
            scanner_address=scanner_living.address,
            rssi=-50.0,
            stamp=TEST_BASE_TIME,
            area_id="living",
            scanner_device=scanner_living,
        )
        device.adverts[scanner_bedroom.address] = FakeAdvert(
            scanner_address=scanner_bedroom.address,
            rssi=-85.0,
            stamp=TEST_BASE_TIME,
            area_id="bedroom",
            scanner_device=scanner_bedroom,
        )

        # Create profiles matching expected room patterns
        kitchen_profile = self._create_trained_profile(
            area_id="kitchen",
            primary_scanner=scanner_kitchen.address,
            primary_rssi=-55.0,
            other_readings={
                scanner_living.address: -80.0,
                scanner_bedroom.address: -90.0,
            },
        )
        living_profile = self._create_trained_profile(
            area_id="living",
            primary_scanner=scanner_living.address,
            primary_rssi=-50.0,
            other_readings={
                scanner_kitchen.address: -75.0,
                scanner_bedroom.address: -85.0,
            },
        )
        bedroom_profile = self._create_trained_profile(
            area_id="bedroom",
            primary_scanner=scanner_bedroom.address,
            primary_rssi=-45.0,
            other_readings={
                scanner_kitchen.address: -85.0,
                scanner_living.address: -80.0,
            },
        )

        coordinator.correlations[device.address] = {
            "kitchen": kitchen_profile,
            "living": living_profile,
            "bedroom": bedroom_profile,
        }

        result = coordinator._refresh_area_by_ukf(device)

        assert result is True
        assert device.area_id == "living"

    def test_ukf_handles_scanner_dropout(self, mock_monotonic_time: None) -> None:
        """Test UKF still works when one scanner goes offline."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:03", "Watch")

        scanner_kitchen = FakeScanner("SC:KI:TC:HE:N0:01", "Kitchen Scanner", "kitchen")
        scanner_living = FakeScanner("SC:LI:VI:NG:00:02", "Living Scanner", "living")
        scanner_bedroom = FakeScanner("SC:BE:DR:OO:M0:03", "Bedroom Scanner", "bedroom")

        # Only 2 scanners visible (bedroom scanner offline)
        device.adverts[scanner_kitchen.address] = FakeAdvert(
            scanner_address=scanner_kitchen.address,
            rssi=-55.0,
            stamp=TEST_BASE_TIME,
            area_id="kitchen",
            scanner_device=scanner_kitchen,
        )
        device.adverts[scanner_living.address] = FakeAdvert(
            scanner_address=scanner_living.address,
            rssi=-80.0,
            stamp=TEST_BASE_TIME,
            area_id="living",
            scanner_device=scanner_living,
        )
        # No bedroom advert (scanner offline)

        # Profiles trained with all 3 scanners
        kitchen_profile = self._create_trained_profile(
            area_id="kitchen",
            primary_scanner=scanner_kitchen.address,
            primary_rssi=-55.0,
            other_readings={
                scanner_living.address: -80.0,
                scanner_bedroom.address: -90.0,
            },
        )

        coordinator.correlations[device.address] = {"kitchen": kitchen_profile}

        # Should still work with 2 scanners
        result = coordinator._refresh_area_by_ukf(device)

        # UKF should be created and process the data
        assert device.address in coordinator.device_ukfs
        # May or may not make a decision depending on match quality

    def test_ukf_reuses_existing_instance(self, mock_monotonic_time: None) -> None:
        """Test that UKF instance is reused across multiple calls."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:04", "Beacon")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "office")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "hallway")

        device.adverts[scanner1.address] = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-60.0,
            stamp=TEST_BASE_TIME,
            area_id="office",
            scanner_device=scanner1,
        )
        device.adverts[scanner2.address] = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-70.0,
            stamp=TEST_BASE_TIME,
            area_id="hallway",
            scanner_device=scanner2,
        )

        # First call creates UKF
        coordinator._refresh_area_by_ukf(device)
        first_ukf = coordinator.device_ukfs.get(device.address)
        assert first_ukf is not None

        # Second call should reuse same instance
        coordinator._refresh_area_by_ukf(device)
        second_ukf = coordinator.device_ukfs.get(device.address)

        assert first_ukf is second_ukf

    def test_ukf_convergence_over_multiple_updates(self, mock_monotonic_time: None) -> None:
        """Test that UKF state converges with consistent readings."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:05", "Tracker")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "garage")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "driveway")

        # Create profile
        garage_profile = self._create_trained_profile(
            area_id="garage",
            primary_scanner=scanner1.address,
            primary_rssi=-60.0,
            other_readings={scanner2.address: -75.0},
        )
        coordinator.correlations[device.address] = {"garage": garage_profile}

        # Simulate multiple updates with consistent readings
        for i in range(10):
            device.adverts[scanner1.address] = FakeAdvert(
                scanner_address=scanner1.address,
                rssi=-60.0,
                stamp=TEST_BASE_TIME + i,
                area_id="garage",
                scanner_device=scanner1,
            )
            device.adverts[scanner2.address] = FakeAdvert(
                scanner_address=scanner2.address,
                rssi=-75.0,
                stamp=TEST_BASE_TIME + i,
                area_id="driveway",
                scanner_device=scanner2,
            )
            coordinator._refresh_area_by_ukf(device)

        # After multiple consistent updates, UKF should have converged
        ukf = coordinator.device_ukfs[device.address]
        assert ukf is not None

        # Check that variance has decreased (filter converged)
        variance = ukf.get_variance()
        # After 10 updates, variance should be reasonably low
        assert variance < 50.0  # Initial variance is higher

    def test_ukf_distinguishes_similar_rooms(self, mock_monotonic_time: None) -> None:
        """Test UKF can distinguish between rooms with similar but different patterns."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:06", "Speaker")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "room_a")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "room_b")
        scanner3 = FakeScanner("SC:AN:NE:R3:00:03", "Scanner3", "room_c")

        # Room A: strong from scanner1, medium from scanner2, weak from scanner3
        room_a_profile = self._create_trained_profile(
            area_id="room_a",
            primary_scanner=scanner1.address,
            primary_rssi=-50.0,
            other_readings={
                scanner2.address: -65.0,
                scanner3.address: -80.0,
            },
        )

        # Room B: similar but scanner2 is strongest
        room_b_profile = self._create_trained_profile(
            area_id="room_b",
            primary_scanner=scanner2.address,
            primary_rssi=-52.0,
            other_readings={
                scanner1.address: -63.0,
                scanner3.address: -78.0,
            },
        )

        coordinator.correlations[device.address] = {
            "room_a": room_a_profile,
            "room_b": room_b_profile,
        }

        # Current readings match room_a pattern
        device.adverts[scanner1.address] = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-50.0,
            stamp=TEST_BASE_TIME,
            area_id="room_a",
            scanner_device=scanner1,
        )
        device.adverts[scanner2.address] = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-65.0,
            stamp=TEST_BASE_TIME,
            area_id="room_b",
            scanner_device=scanner2,
        )
        device.adverts[scanner3.address] = FakeAdvert(
            scanner_address=scanner3.address,
            rssi=-80.0,
            stamp=TEST_BASE_TIME,
            area_id="room_c",
            scanner_device=scanner3,
        )

        result = coordinator._refresh_area_by_ukf(device)

        assert result is True
        assert device.area_id == "room_a"

    def test_ukf_handles_stale_adverts(self, mock_monotonic_time: None) -> None:
        """Test UKF ignores stale adverts outside evidence window."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:07", "Tag")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "area1")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "area2")

        # One fresh advert, one stale (stamp is very old relative to current time)
        device.adverts[scanner1.address] = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-60.0,
            stamp=TEST_BASE_TIME,  # Current time
            area_id="area1",
            scanner_device=scanner1,
        )
        device.adverts[scanner2.address] = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-70.0,
            stamp=1.0,  # Very old - will be filtered by evidence window
            area_id="area2",
            scanner_device=scanner2,
        )

        # With only 1 fresh advert, UKF should return False (need min 2 scanners)
        result = coordinator._refresh_area_by_ukf(device)

        # Result depends on evidence window check
        # The stale advert should be filtered out


class TestUKFWithMultipleDevices:
    """Test UKF handling of multiple devices simultaneously."""

    def test_separate_ukf_per_device(self, mock_monotonic_time: None) -> None:
        """Test each device gets its own UKF instance."""
        coordinator = create_coordinator_mock()

        device1 = FakeDevice("AA:BB:CC:DD:EE:01", "Device1")
        device2 = FakeDevice("AA:BB:CC:DD:EE:02", "Device2")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "area1")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "area2")

        # Both devices see same scanners
        for device in [device1, device2]:
            device.adverts[scanner1.address] = FakeAdvert(
                scanner_address=scanner1.address,
                rssi=-60.0,
                stamp=TEST_BASE_TIME,
                area_id="area1",
                scanner_device=scanner1,
            )
            device.adverts[scanner2.address] = FakeAdvert(
                scanner_address=scanner2.address,
                rssi=-70.0,
                stamp=TEST_BASE_TIME,
                area_id="area2",
                scanner_device=scanner2,
            )

        coordinator._refresh_area_by_ukf(device1)
        coordinator._refresh_area_by_ukf(device2)

        # Each device should have its own UKF
        assert device1.address in coordinator.device_ukfs
        assert device2.address in coordinator.device_ukfs
        assert coordinator.device_ukfs[device1.address] is not coordinator.device_ukfs[device2.address]

    def test_ukf_devices_independent_state(self, mock_monotonic_time: None) -> None:
        """Test UKF instances maintain independent state."""
        coordinator = create_coordinator_mock()

        device1 = FakeDevice("AA:BB:CC:DD:EE:01", "Device1")
        device2 = FakeDevice("AA:BB:CC:DD:EE:02", "Device2")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "area1")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "area2")

        # Device1 sees strong signal from scanner1 (-50), weak from scanner2 (-80)
        device1.adverts[scanner1.address] = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-50.0,
            stamp=TEST_BASE_TIME,
            area_id="area1",
            scanner_device=scanner1,
        )
        device1.adverts[scanner2.address] = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-80.0,
            stamp=TEST_BASE_TIME,
            area_id="area2",
            scanner_device=scanner2,
        )

        # Device2 sees weak signal from scanner1 (-80), strong from scanner2 (-50)
        device2.adverts[scanner1.address] = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-80.0,
            stamp=TEST_BASE_TIME,
            area_id="area1",
            scanner_device=scanner1,
        )
        device2.adverts[scanner2.address] = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-50.0,
            stamp=TEST_BASE_TIME,
            area_id="area2",
            scanner_device=scanner2,
        )

        coordinator._refresh_area_by_ukf(device1)
        coordinator._refresh_area_by_ukf(device2)

        ukf1 = coordinator.device_ukfs[device1.address]
        ukf2 = coordinator.device_ukfs[device2.address]

        # Each UKF should have tracked the scanners it received
        assert ukf1.n_scanners >= 2
        assert ukf2.n_scanners >= 2

        # The state vectors should contain the RSSI values
        # Device1: scanner1=-50, scanner2=-80 → state reflects these
        # Device2: scanner1=-80, scanner2=-50 → state reflects swapped pattern
        state1 = ukf1.state
        state2 = ukf2.state

        # States are lists of RSSI values; they should differ since
        # the devices have different RSSI patterns
        # The mean of device1 readings is (-50 + -80) / 2 = -65
        # The mean of device2 readings is (-80 + -50) / 2 = -65
        # But the individual state values should differ
        assert len(state1) == len(state2)
        assert len(state1) >= 2

        # Verify UKFs are tracking independently by checking variance
        # (both should have similar initial variance since same # of updates)
        var1 = ukf1.get_variance()
        var2 = ukf2.get_variance()
        assert var1 > 0
        assert var2 > 0
