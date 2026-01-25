"""Tests for virtual distance calculation for scannerless rooms.

This test module covers the "Virtual Min-Distance" feature that allows
scannerless rooms (rooms without their own Bluetooth scanner) to compete
with scanner-equipped rooms in the min-distance area selection algorithm.

The key scenarios tested:
1. Virtual distance calculation from UKF scores
2. Scannerless room winning against distant physical scanner
3. Physical scanner winning against poorly-matched scannerless room
4. Only button-trained rooms get virtual distances
5. Integration with the full min-distance algorithm
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, AsyncMock

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    UKF_MIN_SCANNERS,
    VIRTUAL_DISTANCE_MIN_SCORE,
    VIRTUAL_DISTANCE_SCALE,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.area_selection import AreaSelectionHandler
from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.scanner_absolute import ScannerAbsoluteRssi
from custom_components.bermuda.filters import UnscentedKalmanFilter
from custom_components.bermuda.fmdn import FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager


# =============================================================================
# Test Fixtures and Helpers
# =============================================================================


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for virtual distance tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator.device_ukfs = {}  # UKF instances per device
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.correlation_store = MagicMock(async_save=AsyncMock())
    coordinator._correlations_loaded = True
    coordinator._last_correlation_save = 0.0
    coordinator.area_selection = AreaSelectionHandler(coordinator)
    return coordinator


def _make_scanner_device(
    name: str,
    area_id: str,
    *,
    floor_id: str | None = None,
    floor_level: int | None = None,
) -> MagicMock:
    """Create a minimal scanner device object (MagicMock is hashable for set membership)."""
    scanner = MagicMock()
    scanner.address = f"scanner-{name}"
    scanner.name = name
    scanner.area_id = area_id
    scanner.area_name = area_id
    scanner.last_seen = monotonic_time_coarse()
    scanner.floor_id = floor_id
    scanner.floor_level = floor_level
    return scanner


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
    scanner_device = _make_scanner_device(name, area_id, floor_id=floor_id, floor_level=floor_level)
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
    advert.median_rssi = lambda: rssi
    return advert


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


def _create_button_trained_profile(
    area_id: str,
    scanner_addresses: list[str],
    rssi_values: list[float],
) -> AreaProfile:
    """
    Create an AreaProfile with button-trained absolute profiles.

    Simulates what happens when a user clicks the "Train" button for a room.
    """
    profile = AreaProfile(area_id=area_id)

    for scanner_addr, rssi in zip(scanner_addresses, rssi_values, strict=True):
        abs_profile = ScannerAbsoluteRssi(scanner_address=scanner_addr)
        # Simulate button training: multiple samples to build up confidence
        for _ in range(20):
            abs_profile.update_button(rssi)
        profile._absolute_profiles[scanner_addr] = abs_profile

    return profile


def _setup_ukf_with_readings(
    coordinator: BermudaDataUpdateCoordinator,
    device_address: str,
    rssi_readings: dict[str, float],
) -> UnscentedKalmanFilter:
    """
    Set up a UKF with the given RSSI readings.

    Returns the UKF instance for further assertions.
    """
    ukf = UnscentedKalmanFilter()
    # Initialize UKF with readings
    ukf.update_multi(rssi_readings)
    coordinator.device_ukfs[device_address] = ukf
    return ukf


# =============================================================================
# Unit Tests: Virtual Distance Calculation
# =============================================================================


class TestVirtualDistanceCalculation:
    """Tests for the _calculate_virtual_distance() helper method."""

    def test_perfect_score_gives_zero_distance(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """A perfect UKF score (1.0) should give minimal virtual distance."""
        max_radius = 10.0
        score = 1.0

        virtual_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)

        # With score 1.0: (1 - 1.0)² * 0.7 * 10 = 0
        assert virtual_dist == pytest.approx(0.0, abs=0.01)

    def test_medium_score_gives_competitive_distance(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """A medium score (0.5) should give a competitive distance."""
        max_radius = 10.0
        score = 0.5

        virtual_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)

        # With score 0.5: (1 - 0.5)² * 0.7 * 10 = 0.25 * 7 = 1.75m
        expected = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score) ** 2)
        assert virtual_dist == pytest.approx(expected, abs=0.01)
        assert virtual_dist == pytest.approx(1.75, abs=0.01)

    def test_low_score_gives_larger_distance(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """A low score (0.25) should give a larger but still usable distance."""
        max_radius = 10.0
        score = 0.25

        virtual_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)

        # With score 0.25: (1 - 0.25)² * 0.7 * 10 = 0.5625 * 7 = 3.9375m
        expected = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score) ** 2)
        assert virtual_dist == pytest.approx(expected, abs=0.01)
        assert virtual_dist == pytest.approx(3.9375, abs=0.01)

    def test_threshold_score_0_3_beats_5m_scanner(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Score of 0.3 should produce distance < 5m to beat distant scanner."""
        max_radius = 10.0
        score = 0.3

        virtual_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)

        # With score 0.3: (1 - 0.3)² * 0.7 * 10 = 0.49 * 7 = 3.43m
        # This should beat a scanner at 5.2m (Yunas Zimmer scenario)
        assert virtual_dist < 5.0
        assert virtual_dist == pytest.approx(3.43, abs=0.01)

    def test_very_low_score_clamped_to_minimum(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Scores below minimum should be clamped."""
        max_radius = 10.0
        score = 0.01  # Below VIRTUAL_DISTANCE_MIN_SCORE (0.05)

        virtual_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)

        # Should be clamped to minimum score of 0.05
        expected_clamped = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - VIRTUAL_DISTANCE_MIN_SCORE) ** 2)
        assert virtual_dist == pytest.approx(expected_clamped, abs=0.01)

    def test_quadratic_vs_linear_comparison(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Verify quadratic formula gives shorter distances than linear for medium scores."""
        max_radius = 10.0

        for score in [0.3, 0.4, 0.5, 0.6]:
            quadratic_dist = coordinator.area_selection._calculate_virtual_distance(score, max_radius)
            linear_dist = max_radius * (1 - score)  # What linear would give

            # Quadratic should be shorter (more competitive)
            assert quadratic_dist < linear_dist, f"Score {score}: quadratic should be shorter"


# =============================================================================
# Unit Tests: Area Has Scanner Check
# =============================================================================


class TestAreaHasScanner:
    """Tests for the _area_has_scanner() helper method."""

    def test_area_with_scanner_returns_true(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Area containing a scanner should return True."""
        scanner = _make_scanner_device("kitchen-scanner", "area-kitchen")
        coordinator._scanners.add(scanner)

        assert coordinator.area_selection._area_has_scanner("area-kitchen") is True

    def test_area_without_scanner_returns_false(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Area without a scanner should return False."""
        scanner = _make_scanner_device("kitchen-scanner", "area-kitchen")
        coordinator._scanners.add(scanner)

        # Different area should return False
        assert coordinator.area_selection._area_has_scanner("area-basement") is False

    def test_empty_scanners_returns_false(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Empty scanner set should return False for any area."""
        assert coordinator.area_selection._area_has_scanner("any-area") is False


# =============================================================================
# Integration Tests: Virtual Distance with Min-Distance
# =============================================================================


class TestScannerlessRoomScenarios:
    """
    Integration tests for the Lagerraum (storage room) scenario.

    These tests simulate the real-world problem:
    - Device is in Lagerraum (basement, no scanner)
    - Device was button-trained for Lagerraum
    - Nearby scanners see the device (Technikraum, upper floors)
    - Without virtual distance: Yunas Zimmer (upper floor) wins by min-distance
    - With virtual distance: Lagerraum should compete based on fingerprint match
    """

    def test_scannerless_room_wins_with_good_fingerprint_match(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Scannerless room with good fingerprint match should beat distant scanner.

        Scenario:
        - Lagerraum (scannerless) has button-trained profile
        - UKF score for Lagerraum: 0.35 (good match)
        - Virtual distance: ~3m
        - Yunas Zimmer scanner: 5.2m away
        - Expected: Lagerraum wins

        Note: We need at least 2 scanners for UKF_MIN_SCANNERS requirement.
        """
        device_addr = "AA:BB:CC:DD:EE:01"
        lagerraum_area = "area-lagerraum"
        yunas_area = "area-yunas-zimmer"
        technik_area = "area-technikraum"

        device = _configure_device(coordinator, device_addr)

        # Set up two scanners (required by UKF_MIN_SCANNERS = 2)
        yunas_scanner = _make_scanner_device("yunas-scanner", yunas_area, floor_id="floor-og", floor_level=1)
        technik_scanner = _make_scanner_device("technik-scanner", technik_area, floor_id="floor-kg", floor_level=-1)
        coordinator._scanners.add(yunas_scanner)
        coordinator._scanners.add(technik_scanner)

        # Create button-trained profile for Lagerraum (with readings from both scanners)
        scanner_addrs = [yunas_scanner.address, technik_scanner.address]
        trained_rssi = [-75.0, -72.0]  # What we see when in Lagerraum
        profile = _create_button_trained_profile(lagerraum_area, scanner_addrs, trained_rssi)
        # Use device.address (lowercase) as key - BermudaDevice normalizes addresses
        coordinator.correlations[device.address] = {lagerraum_area: profile}

        # Set up UKF with current readings that match the trained profile well
        current_readings = {
            yunas_scanner.address: -76.0,  # Close to trained -75
            technik_scanner.address: -73.0,  # Close to trained -72
        }
        _setup_ukf_with_readings(coordinator, device.address, current_readings)

        # Set up Yunas Zimmer as the current physical scanner option (5.2m away)
        yunas_advert = _make_advert(
            "yunas-scanner",
            yunas_area,
            distance=5.2,
            rssi=-76.0,
            floor_id="floor-og",
            floor_level=1,
            hist_distance_by_interval=[5.2] * 10,
        )
        technik_advert = _make_advert(
            "technik-scanner",
            technik_area,
            distance=6.5,
            rssi=-73.0,
            floor_id="floor-kg",
            floor_level=-1,
            hist_distance_by_interval=[6.5] * 10,
        )
        device.adverts = {
            "yunas": yunas_advert,  # type: ignore[dict-item]
            "technik": technik_advert,  # type: ignore[dict-item]
        }

        # Run min-distance algorithm
        coordinator._refresh_area_by_min_distance(device)

        # Verify: Device should be in Lagerraum due to virtual distance
        assert device.area_id == lagerraum_area, (
            f"Expected area {lagerraum_area}, got {device.area_id}. Scannerless room with good fingerprint should win."
        )

    def test_physical_scanner_wins_with_poor_fingerprint_match(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Physical scanner should win when fingerprint match is poor.

        Scenario:
        - Lagerraum has button-trained profile at -85dB
        - Current RSSI is -55dB (very different = device moved!)
        - Virtual distance: large due to poor match
        - Yunas Zimmer scanner: 3m away
        - Expected: Yunas Zimmer wins (device probably not in Lagerraum)

        Note: We need at least 2 scanners for UKF_MIN_SCANNERS requirement.
        """
        device_addr = "AA:BB:CC:DD:EE:02"
        lagerraum_area = "area-lagerraum"
        yunas_area = "area-yunas-zimmer"
        technik_area = "area-technikraum"

        device = _configure_device(coordinator, device_addr)

        # Set up two scanners (required by UKF_MIN_SCANNERS = 2)
        yunas_scanner = _make_scanner_device("yunas-scanner", yunas_area)
        technik_scanner = _make_scanner_device("technik-scanner", technik_area)
        coordinator._scanners.add(yunas_scanner)
        coordinator._scanners.add(technik_scanner)

        # Create button-trained profile for Lagerraum with weak expected RSSI
        scanner_addrs = [yunas_scanner.address, technik_scanner.address]
        trained_rssi = [-85.0, -88.0]  # Trained at weak signal (device was far from scanner)
        profile = _create_button_trained_profile(lagerraum_area, scanner_addrs, trained_rssi)
        # Use device.address (lowercase) as key - BermudaDevice normalizes addresses
        coordinator.correlations[device.address] = {lagerraum_area: profile}

        # Set up UKF with current readings that DON'T match (device moved to Yunas Zimmer)
        current_readings = {
            yunas_scanner.address: -55.0,  # Much stronger = closer to scanner
            technik_scanner.address: -60.0,  # Also much stronger
        }
        _setup_ukf_with_readings(coordinator, device.address, current_readings)

        # Set up Yunas Zimmer as the physical scanner option (3m away - closer than virtual)
        yunas_advert = _make_advert(
            "yunas-scanner",
            yunas_area,
            distance=3.0,
            rssi=-55.0,
            hist_distance_by_interval=[3.0] * 10,
        )
        technik_advert = _make_advert(
            "technik-scanner",
            technik_area,
            distance=4.0,
            rssi=-60.0,
            hist_distance_by_interval=[4.0] * 10,
        )
        device.adverts = {
            "yunas": yunas_advert,  # type: ignore[dict-item]
            "technik": technik_advert,  # type: ignore[dict-item]
        }

        # Run min-distance algorithm
        coordinator._refresh_area_by_min_distance(device)

        # Verify: Device should be in Yunas Zimmer (physical scanner wins)
        assert device.area_id == yunas_area, (
            f"Expected area {yunas_area}, got {device.area_id}. "
            "Physical scanner should win when fingerprint match is poor."
        )

    def test_only_button_trained_rooms_get_virtual_distance(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Auto-learned profiles should NOT get virtual distances.

        Only button-trained (explicit user intent) profiles should compete.

        Note: We need at least 2 scanners for UKF_MIN_SCANNERS requirement.
        """
        device_addr = "AA:BB:CC:DD:EE:03"
        auto_area = "area-auto-learned"
        physical_area = "area-physical"
        secondary_area = "area-secondary"

        device = _configure_device(coordinator, device_addr)

        # Set up two physical scanners (required by UKF_MIN_SCANNERS = 2)
        physical_scanner = _make_scanner_device("physical-scanner", physical_area)
        secondary_scanner = _make_scanner_device("secondary-scanner", secondary_area)
        coordinator._scanners.add(physical_scanner)
        coordinator._scanners.add(secondary_scanner)

        # Create AUTO-learned profile (not button-trained) for the scannerless area
        profile = AreaProfile(area_id=auto_area)
        for scanner_addr, rssi in [
            (physical_scanner.address, -75.0),
            (secondary_scanner.address, -78.0),
        ]:
            abs_profile = ScannerAbsoluteRssi(scanner_address=scanner_addr)
            # Only use update() not update_button() - this is auto-learning
            for _ in range(30):
                abs_profile.update(rssi)
            profile._absolute_profiles[scanner_addr] = abs_profile
        coordinator.correlations[device.address] = {auto_area: profile}

        # Verify the profile is NOT button-trained
        assert profile.has_button_training is False

        # Set up UKF with 2 scanner readings
        current_readings = {
            physical_scanner.address: -75.0,
            secondary_scanner.address: -78.0,
        }
        _setup_ukf_with_readings(coordinator, device.address, current_readings)

        # Calculate virtual distances
        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, current_readings
        )

        # Should be empty - auto-learned profiles don't get virtual distances
        assert len(virtual_distances) == 0, "Auto-learned profiles should not get virtual distances"

    def test_rooms_with_scanners_excluded_from_virtual_distance(
        self, coordinator: BermudaDataUpdateCoordinator
    ) -> None:
        """
        Rooms that have physical scanners should not get virtual distances.

        They use real measured distances instead.

        Note: We need at least 2 scanners for UKF_MIN_SCANNERS requirement.
        """
        device_addr = "AA:BB:CC:DD:EE:04"
        room_with_scanner = "area-with-scanner"
        other_room = "area-other"

        device = _configure_device(coordinator, device_addr)

        # Set up two scanners (required by UKF_MIN_SCANNERS = 2)
        scanner = _make_scanner_device("room-scanner", room_with_scanner)
        other_scanner = _make_scanner_device("other-scanner", other_room)
        coordinator._scanners.add(scanner)
        coordinator._scanners.add(other_scanner)

        # Create button-trained profile for the room (even though it has a scanner)
        profile = _create_button_trained_profile(
            room_with_scanner,
            [scanner.address, other_scanner.address],
            [-60.0, -65.0],
        )
        coordinator.correlations[device.address] = {room_with_scanner: profile}

        # Set up UKF with 2 scanner readings
        current_readings = {
            scanner.address: -60.0,
            other_scanner.address: -65.0,
        }
        _setup_ukf_with_readings(coordinator, device.address, current_readings)

        # Calculate virtual distances
        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, current_readings
        )

        # Should be empty - room has a scanner, so uses real distance
        assert len(virtual_distances) == 0, "Rooms with scanners should not get virtual distances"


class TestVirtualDistanceEdgeCases:
    """Edge case tests for virtual distance calculation."""

    def test_no_correlations_returns_empty(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Device with no correlations should get empty virtual distances."""
        device_addr = "AA:BB:CC:DD:EE:10"
        device = _configure_device(coordinator, device_addr)

        rssi_readings = {"scanner-1": -70.0, "scanner-2": -75.0}

        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, rssi_readings
        )

        assert len(virtual_distances) == 0

    def test_insufficient_scanners_returns_empty(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """With fewer than UKF_MIN_SCANNERS, should return empty."""
        device_addr = "AA:BB:CC:DD:EE:11"
        device = _configure_device(coordinator, device_addr)

        # Only one scanner reading (below UKF_MIN_SCANNERS=2)
        rssi_readings = {"scanner-1": -70.0}

        # Even with correlations, should return empty
        profile = _create_button_trained_profile("some-area", ["scanner-1"], [-70.0])
        coordinator.correlations[device.address] = {"some-area": profile}

        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, rssi_readings
        )

        assert len(virtual_distances) == 0

    def test_ukf_created_dynamically_when_missing(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """UKF should be created automatically if missing when virtual distances are requested.

        FIX: BUG 16 - Previously, if _refresh_area_by_ukf() returned early (e.g., single scanner),
        no UKF was created, and virtual distances would return empty. Now the UKF is created
        dynamically in _get_virtual_distances_for_scannerless_rooms() when needed.
        """
        device_addr = "AA:BB:CC:DD:EE:12"
        device = _configure_device(coordinator, device_addr)

        # Set up correlations but NO UKF state initially
        profile = _create_button_trained_profile(
            "scannerless-room",
            ["scanner-1", "scanner-2"],
            [-70.0, -75.0],
        )
        coordinator.correlations[device.address] = {"scannerless-room": profile}
        # Deliberately don't add to device_ukfs - it should be created automatically

        rssi_readings = {"scanner-1": -70.0, "scanner-2": -75.0}

        # Before: no UKF exists
        assert device.address not in coordinator.device_ukfs

        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, rssi_readings
        )

        # After: UKF should have been created
        assert device.address in coordinator.device_ukfs

        # And we should get a virtual distance for the scannerless room
        assert len(virtual_distances) == 1
        assert "scannerless-room" in virtual_distances

    def test_multiple_scannerless_rooms_compete(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Multiple scannerless rooms should all get virtual distances."""
        device_addr = "AA:BB:CC:DD:EE:13"
        device = _configure_device(coordinator, device_addr)

        # No scanners in these rooms
        room1 = "area-basement-1"
        room2 = "area-basement-2"

        # Create button-trained profiles for both
        scanner_addrs = ["scanner-upstairs-1", "scanner-upstairs-2"]
        profile1 = _create_button_trained_profile(room1, scanner_addrs, [-80.0, -85.0])
        profile2 = _create_button_trained_profile(room2, scanner_addrs, [-75.0, -80.0])

        coordinator.correlations[device.address] = {
            room1: profile1,
            room2: profile2,
        }

        # Set up UKF
        rssi_readings = {"scanner-upstairs-1": -78.0, "scanner-upstairs-2": -82.0}
        _setup_ukf_with_readings(coordinator, device.address, rssi_readings)

        virtual_distances = coordinator.area_selection._get_virtual_distances_for_scannerless_rooms(
            device, rssi_readings
        )

        # Both rooms should have virtual distances
        assert room1 in virtual_distances or room2 in virtual_distances, (
            "At least one scannerless room should have a virtual distance"
        )


class TestAreaProfileHasButtonTraining:
    """Tests for the has_button_training property on AreaProfile."""

    def test_empty_profile_returns_false(self) -> None:
        """Empty profile should return False."""
        profile = AreaProfile(area_id="test-area")
        assert profile.has_button_training is False

    def test_auto_learned_only_returns_false(self) -> None:
        """Profile with only auto-learned data should return False."""
        profile = AreaProfile(area_id="test-area")
        abs_profile = ScannerAbsoluteRssi(scanner_address="scanner-1")
        for _ in range(30):
            abs_profile.update(-70.0)  # Auto-learning only
        profile._absolute_profiles["scanner-1"] = abs_profile

        assert profile.has_button_training is False

    def test_button_trained_absolute_returns_true(self) -> None:
        """Profile with button-trained absolute profile should return True."""
        profile = AreaProfile(area_id="test-area")
        abs_profile = ScannerAbsoluteRssi(scanner_address="scanner-1")
        abs_profile.update_button(-70.0)  # Button training
        profile._absolute_profiles["scanner-1"] = abs_profile

        assert profile.has_button_training is True

    def test_mixed_profiles_returns_true(self) -> None:
        """Profile with mix of auto and button should return True."""
        profile = AreaProfile(area_id="test-area")

        # Auto-learned profile
        auto_profile = ScannerAbsoluteRssi(scanner_address="scanner-1")
        for _ in range(30):
            auto_profile.update(-70.0)
        profile._absolute_profiles["scanner-1"] = auto_profile

        # Button-trained profile
        button_profile = ScannerAbsoluteRssi(scanner_address="scanner-2")
        button_profile.update_button(-75.0)
        profile._absolute_profiles["scanner-2"] = button_profile

        # Should be True because at least one is button-trained
        assert profile.has_button_training is True
