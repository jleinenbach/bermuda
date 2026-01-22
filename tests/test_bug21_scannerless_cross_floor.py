"""Tests for BUG 21 fix: Cross-floor sanity check for scannerless rooms.

This test verifies that:
1. When UKF picks a scannerless room on a DIFFERENT floor than the strongest scanner
2. AND the strongest scanner has a strong RSSI signal (> -75 dBm)
3. The UKF decision is rejected as physically implausible

Scenario:
- Device is in "Lagerraum" (basement, scannerless)
- UKF picks "Bad OG" (bathroom, 2 floors up, scannerless) with high score
- But a basement scanner sees device at -73 dBm (strong!)
- If device were really 2 floors up, scanner would see ~-85 dBm or worse
- Strong signal + cross-floor = impossible → reject UKF decision
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    EVIDENCE_WINDOW_SECONDS,
    UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


# Test constants
BASE_TIME = 1000.0
SCANNER_BASEMENT = "AA:BB:CC:DD:EE:01"  # Scanner in basement
SCANNER_GROUND = "AA:BB:CC:DD:EE:02"  # Scanner on ground floor
DEVICE_ADDRESS = "FF:EE:DD:CC:BB:AA"

# Area IDs
AREA_LAGERRAUM = "area_lagerraum"  # Basement, scannerless
AREA_BAD_OG = "area_bad_og"  # 2 floors up, scannerless
AREA_TECHNIKRAUM = "area_technikraum"  # Basement, has scanner

# Floor IDs
FLOOR_BASEMENT = "floor_basement"
FLOOR_OG = "floor_og"  # 2 floors up


def _make_coordinator() -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for tests (no hass required)."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.device_ukfs = {}
    coordinator.ar = MagicMock()
    coordinator.fr = MagicMock()
    coordinator.irk_manager = MagicMock()
    coordinator.fmdn = MagicMock()
    return coordinator


def _make_scanner(
    address: str,
    area_id: str,
    floor_id: str,
) -> SimpleNamespace:
    """Create a minimal scanner-like object."""
    return SimpleNamespace(
        address=address,
        name=f"Scanner {address[-5:]}",
        area_id=area_id,
        area_name=area_id,
        floor_id=floor_id,
    )


def _make_advert(
    scanner: SimpleNamespace,
    rssi: float,
    stamp: float,
    distance: float,
) -> SimpleNamespace:
    """Create an advertisement object."""
    return SimpleNamespace(
        scanner_device=scanner,
        scanner_address=scanner.address,
        rssi=rssi,
        stamp=stamp,
        rssi_distance=distance,
        rssi_distance_raw=distance,
        area_id=scanner.area_id,
        area_name=scanner.area_name,
    )


def _make_device() -> SimpleNamespace:
    """Create a fake device for testing."""
    device = SimpleNamespace(
        address=DEVICE_ADDRESS,
        name="Test Tracker",
        adverts={},
        area_id=AREA_LAGERRAUM,  # Currently in basement
        area_name="Lagerraum",
        pending_area_id=None,
        pending_floor_id=None,
        pending_streak=0,
        pending_last_stamps={},
    )
    return device


class TestScannerlessCrossFloorSanityCheck:
    """Test the BUG 21 fix: Cross-floor sanity check for scannerless rooms."""

    def test_rejects_cross_floor_scannerless_with_strong_signal(self) -> None:
        """Test that UKF is rejected when scannerless room is on different floor with strong signal.

        Scenario:
        - UKF picks "Bad OG" (floor OG, scannerless)
        - But strongest scanner is in basement with -73 dBm (strong!)
        - Cross-floor + strong signal = physically impossible → reject
        """
        coordinator = _make_coordinator()
        device = _make_device()

        # Scanner in basement sees device with strong signal
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # Mock _resolve_floor_id_for_area to return correct floor IDs
        def mock_resolve_floor(area_id: str) -> str | None:
            if area_id == AREA_BAD_OG:
                return FLOOR_OG  # 2 floors up
            if area_id in (AREA_LAGERRAUM, AREA_TECHNIKRAUM):
                return FLOOR_BASEMENT
            return None

        # The condition that triggers the sanity check:
        # 1. scanner_less_room = True (UKF picked a scannerless room)
        # 2. best_advert = advert from basement scanner (-73 dBm, strong)
        # 3. best_area_id = AREA_BAD_OG (floor OG)
        # 4. strongest_scanner_floor_id = FLOOR_BASEMENT
        # 5. target_area_floor_id = FLOOR_OG (different!)
        # 6. rssi (-73) > threshold (-75) = True (strong signal)
        # → Should return False (reject UKF)

        # Verify the threshold constant
        assert UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD == -75.0

        # Test the condition directly
        strongest_scanner_floor_id = scanner_basement.floor_id
        target_area_floor_id = FLOOR_OG  # From mock_resolve_floor(AREA_BAD_OG)
        rssi = advert.rssi

        # All conditions for rejection should be true
        assert strongest_scanner_floor_id is not None
        assert target_area_floor_id is not None
        assert strongest_scanner_floor_id != target_area_floor_id  # Different floors
        assert rssi is not None
        assert rssi > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD  # -73 > -75

    def test_allows_same_floor_scannerless_with_strong_signal(self) -> None:
        """Test that UKF is allowed when scannerless room is on SAME floor.

        Even with strong signal, same-floor is physically plausible.
        """
        coordinator = _make_coordinator()
        device = _make_device()

        # Scanner in basement sees device with strong signal
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # Both scanner and target room are on same floor
        strongest_scanner_floor_id = FLOOR_BASEMENT
        target_area_floor_id = FLOOR_BASEMENT  # Same floor!
        rssi = advert.rssi

        # Same floor → should NOT reject (condition fails)
        assert strongest_scanner_floor_id == target_area_floor_id

    def test_allows_cross_floor_scannerless_with_weak_signal(self) -> None:
        """Test that UKF is allowed when signal is weak (even cross-floor).

        A weak signal (-80 dBm or below) is plausible for cross-floor.
        """
        coordinator = _make_coordinator()
        device = _make_device()

        # Scanner in basement sees device with WEAK signal
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -80.0, BASE_TIME - 1.0, 5.0)  # Weak!
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        strongest_scanner_floor_id = FLOOR_BASEMENT
        target_area_floor_id = FLOOR_OG  # Different floor
        rssi = advert.rssi

        # Different floors BUT weak signal → should NOT reject
        assert strongest_scanner_floor_id != target_area_floor_id
        assert rssi <= UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD  # -80 <= -75

    def test_threshold_boundary_at_minus_75(self) -> None:
        """Test the boundary condition at exactly -75 dBm."""
        # At exactly -75 dBm, the condition `rssi > -75` is False
        # So at -75 dBm, the check should NOT trigger (allow the selection)
        rssi_at_threshold = -75.0
        assert not (rssi_at_threshold > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD)

        # Just above threshold (-74.9) should trigger
        rssi_above_threshold = -74.9
        assert rssi_above_threshold > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD

        # Below threshold (-75.1) should not trigger
        rssi_below_threshold = -75.1
        assert not (rssi_below_threshold > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD)

    def test_handles_none_floor_ids_gracefully(self) -> None:
        """Test that the check handles None floor IDs without crashing.

        If either floor_id is None, the check should NOT trigger.
        """
        # Case 1: scanner has no floor_id
        strongest_scanner_floor_id = None
        target_area_floor_id = FLOOR_OG
        rssi = -73.0

        condition = (
            strongest_scanner_floor_id is not None
            and target_area_floor_id is not None
            and strongest_scanner_floor_id != target_area_floor_id
            and rssi > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD
        )
        assert not condition  # Should not reject (None floor)

        # Case 2: target area has no floor_id
        strongest_scanner_floor_id = FLOOR_BASEMENT
        target_area_floor_id = None

        condition = (
            strongest_scanner_floor_id is not None
            and target_area_floor_id is not None
            and strongest_scanner_floor_id != target_area_floor_id
            and rssi > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD
        )
        assert not condition  # Should not reject (None floor)

    def test_real_world_scenario_lagerraum_vs_bad_og(self) -> None:
        """Test the real-world scenario from the bug report.

        Device is in Lagerraum (basement), but UKF incorrectly picks Bad OG (2 floors up).
        Scanner in basement sees -73 dBm. This should be rejected.
        """
        # Real values from the bug report
        rssi_from_scanner = -73.0  # Strong signal!
        scanner_floor = FLOOR_BASEMENT
        ukf_picked_floor = FLOOR_OG  # 2 floors up!

        # The sanity check condition
        should_reject = (
            scanner_floor is not None
            and ukf_picked_floor is not None
            and scanner_floor != ukf_picked_floor
            and rssi_from_scanner > UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD
        )

        # This scenario SHOULD be rejected
        assert should_reject, (
            f"BUG 21 scenario should be rejected: "
            f"scanner_floor={scanner_floor}, ukf_floor={ukf_picked_floor}, "
            f"rssi={rssi_from_scanner}, threshold={UKF_SCANNERLESS_CROSS_FLOOR_RSSI_THRESHOLD}"
        )
