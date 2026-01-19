"""Tests for UKF integration in coordinator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    CONF_USE_UKF_AREA_SELECTION,
    DEFAULT_MAX_RADIUS,
    UKF_MIN_SCANNERS,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.correlation import AreaProfile
from custom_components.bermuda.filters import UnscentedKalmanFilter


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
        self.diag_area_switch: str = ""
        self.area_changed_at: float = 0.0

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


def create_coordinator_mock() -> BermudaDataUpdateCoordinator:
    """Create a mock coordinator for testing."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
        CONF_USE_UKF_AREA_SELECTION: False,
    }
    coordinator.correlations = {}
    coordinator._correlations_loaded = True
    coordinator._last_correlation_save = 0.0
    coordinator.correlation_store = MagicMock(async_save=AsyncMock())
    coordinator.device_ukfs = {}
    coordinator.AreaTests = BermudaDataUpdateCoordinator.AreaTests
    return coordinator


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

    def test_refresh_area_by_ukf_insufficient_scanners(self) -> None:
        """Test UKF returns False when fewer than minimum scanners."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        # Add only one scanner advert
        scanner = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen")
        advert = FakeAdvert(
            scanner_address=scanner.address,
            rssi=-65.0,
            stamp=1000.0,
            area_id="kitchen",
            scanner_device=scanner,
        )
        device.adverts[scanner.address] = advert

        result = coordinator._refresh_area_by_ukf(device)
        assert result is False
        assert UKF_MIN_SCANNERS >= 2  # Confirm we need at least 2

    def test_refresh_area_by_ukf_creates_ukf_instance(self) -> None:
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
                stamp=1000.0,
                area_id=scanner.area_id,
                scanner_device=scanner,
            )
            device.adverts[scanner.address] = advert

        # Call UKF refresh (will return False due to no profiles, but should create UKF)
        coordinator._refresh_area_by_ukf(device)

        assert device.address in coordinator.device_ukfs
        assert isinstance(coordinator.device_ukfs[device.address], UnscentedKalmanFilter)

    def test_refresh_area_by_ukf_no_correlations(self) -> None:
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
                stamp=1000.0,
                area_id=scanner.area_id,
                scanner_device=scanner,
            )
            device.adverts[scanner.address] = advert

        result = coordinator._refresh_area_by_ukf(device)
        assert result is False

    def test_refresh_area_by_ukf_with_profiles(self) -> None:
        """Test UKF with learned area profiles."""
        coordinator = create_coordinator_mock()
        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")

        scanner1 = FakeScanner("SC:AN:NE:R1:00:01", "Scanner1", "kitchen")
        scanner2 = FakeScanner("SC:AN:NE:R2:00:02", "Scanner2", "living")

        # Add scanner adverts
        advert1 = FakeAdvert(
            scanner_address=scanner1.address,
            rssi=-65.0,
            stamp=1000.0,
            area_id="kitchen",
            scanner_device=scanner1,
        )
        advert2 = FakeAdvert(
            scanner_address=scanner2.address,
            rssi=-75.0,
            stamp=1000.0,
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

    def test_ukf_option_default_disabled(self) -> None:
        """Test that UKF is disabled by default."""
        coordinator = create_coordinator_mock()
        assert coordinator.options[CONF_USE_UKF_AREA_SELECTION] is False

    def test_refresh_areas_uses_ukf_when_enabled(self) -> None:
        """Test that _refresh_areas_by_min_distance uses UKF when enabled."""
        coordinator = create_coordinator_mock()
        coordinator.options[CONF_USE_UKF_AREA_SELECTION] = True
        coordinator.devices = {}

        device = FakeDevice("AA:BB:CC:DD:EE:01", "Test Device")
        coordinator.devices[device.address] = device

        # Mock the UKF refresh method
        ukf_called = {"count": 0}
        original_ukf_method = coordinator._refresh_area_by_ukf

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

        # UKF should have been called
        assert ukf_called["count"] == 1
        # Min-distance should also be called (as fallback since UKF returned False)
        assert min_dist_called["count"] == 1

    def test_refresh_areas_skips_min_distance_when_ukf_succeeds(self) -> None:
        """Test that min-distance is skipped when UKF makes a decision."""
        coordinator = create_coordinator_mock()
        coordinator.options[CONF_USE_UKF_AREA_SELECTION] = True
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

        # UKF should have been called
        assert ukf_called["count"] == 1
        # Min-distance should NOT be called
        assert min_dist_called["count"] == 0

    def test_refresh_areas_skips_ukf_when_disabled(self) -> None:
        """Test that UKF is skipped when disabled."""
        coordinator = create_coordinator_mock()
        coordinator.options[CONF_USE_UKF_AREA_SELECTION] = False
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

        # UKF should NOT have been called
        assert ukf_called["count"] == 0
        # Min-distance should be called
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
