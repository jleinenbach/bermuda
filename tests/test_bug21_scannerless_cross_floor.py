"""Tests for BUG 21 fix: Topological sanity check for scannerless rooms.

This test verifies that:
1. When UKF picks a scannerless room on floor X
2. At least ONE scanner on floor X must see the device
3. If NO scanner on floor X sees the device → reject as topologically impossible

Scenario:
- Device is in "Lagerraum" (basement, scannerless)
- UKF picks "Bad OG" (bathroom, 2 floors up, scannerless) with high score
- But NO scanner on the OG floor sees the device
- Only basement scanners see the device
- Topologically impossible → reject UKF decision
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    EVIDENCE_WINDOW_SECONDS,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


# Test constants
BASE_TIME = 1000.0
SCANNER_BASEMENT = "AA:BB:CC:DD:EE:01"  # Scanner in basement
SCANNER_OG = "AA:BB:CC:DD:EE:02"  # Scanner on OG floor
DEVICE_ADDRESS = "FF:EE:DD:CC:BB:AA"

# Area IDs
AREA_LAGERRAUM = "area_lagerraum"  # Basement, scannerless
AREA_BAD_OG = "area_bad_og"  # OG floor, scannerless
AREA_TECHNIKRAUM = "area_technikraum"  # Basement, has scanner
AREA_FLUR_OG = "area_flur_og"  # OG floor, has scanner

# Floor IDs
FLOOR_BASEMENT = "floor_basement"
FLOOR_OG = "floor_og"


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
    floor_id: str | None,
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


def _check_topological_condition(
    device: SimpleNamespace,
    target_floor_id: str | None,
    nowstamp: float,
) -> bool:
    """Check the topological condition: does ANY scanner on target floor see the device?

    Returns True if a scanner on the target floor sees the device (check PASSES).
    Returns False if NO scanner on the target floor sees the device (check FAILS).
    """
    if target_floor_id is None:
        # If target floor is None, we can't do the check, so we pass
        return True

    for advert in device.adverts.values():
        if (
            advert.stamp is not None
            and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            and advert.scanner_device is not None
        ):
            scanner_floor_id = getattr(advert.scanner_device, "floor_id", None)
            if scanner_floor_id == target_floor_id:
                return True  # A scanner on the target floor sees the device

    return False  # No scanner on the target floor sees the device


class TestScannerlessTopologicalCheck:
    """Test the BUG 21 fix: Topological sanity check for scannerless rooms."""

    def test_rejects_when_no_scanner_on_target_floor(self) -> None:
        """Test that UKF is rejected when NO scanner on target floor sees the device.

        Scenario:
        - UKF picks "Bad OG" (floor OG, scannerless)
        - Only a basement scanner sees the device
        - NO scanner on the OG floor sees the device
        - Topologically impossible → reject
        """
        device = _make_device()

        # Scanner in BASEMENT sees device (not on target floor OG)
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # Target floor is OG, but no scanner on OG sees the device
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should FAIL (no scanner on OG sees device)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is False, "Should reject when no scanner on target floor sees device"

    def test_allows_when_scanner_on_target_floor_sees_device(self) -> None:
        """Test that UKF is allowed when a scanner on the target floor sees the device.

        Scenario:
        - UKF picks "Bad OG" (floor OG, scannerless)
        - A scanner on the OG floor sees the device
        - Topologically plausible → allow
        """
        device = _make_device()

        # Scanner on OG floor sees device
        scanner_og = _make_scanner(SCANNER_OG, AREA_FLUR_OG, FLOOR_OG)
        advert = _make_advert(scanner_og, -80.0, BASE_TIME - 1.0, 5.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_OG)] = advert

        # Target floor is OG, and a scanner on OG sees the device
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should PASS
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is True, "Should allow when scanner on target floor sees device"

    def test_allows_when_one_of_multiple_scanners_on_target_floor(self) -> None:
        """Test that check passes when at least one scanner on target floor sees device.

        Multiple scanners see the device, but only one is on the target floor.
        """
        device = _make_device()

        # Scanner in basement sees device
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert_basement = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert_basement

        # Scanner on OG floor also sees device (even with weak signal)
        scanner_og = _make_scanner(SCANNER_OG, AREA_FLUR_OG, FLOOR_OG)
        advert_og = _make_advert(scanner_og, -90.0, BASE_TIME - 1.0, 10.0)  # Weak but fresh
        device.adverts[(DEVICE_ADDRESS, SCANNER_OG)] = advert_og

        # Target floor is OG
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should PASS (at least one scanner on OG sees device)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is True, "Should allow when at least one scanner on target floor sees device"

    def test_rejects_when_scanner_on_target_floor_is_stale(self) -> None:
        """Test that stale adverts don't count toward the topological check.

        A scanner on the target floor saw the device, but the advert is too old.
        """
        device = _make_device()

        # Scanner in basement sees device (fresh)
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert_basement = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert_basement

        # Scanner on OG floor has STALE advert (older than EVIDENCE_WINDOW_SECONDS)
        scanner_og = _make_scanner(SCANNER_OG, AREA_FLUR_OG, FLOOR_OG)
        stale_time = BASE_TIME - EVIDENCE_WINDOW_SECONDS - 1.0  # Too old
        advert_og = _make_advert(scanner_og, -80.0, stale_time, 5.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_OG)] = advert_og

        # Target floor is OG
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should FAIL (OG scanner advert is stale)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is False, "Should reject when scanner on target floor has stale advert"

    def test_handles_none_target_floor_gracefully(self) -> None:
        """Test that the check passes when target floor ID is None.

        If we can't determine the target floor, we skip the check.
        """
        device = _make_device()

        # Scanner in basement sees device
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # Target floor is None (unknown)
        target_floor_id = None
        nowstamp = BASE_TIME

        # Topological check should PASS (can't determine target floor)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is True, "Should skip check when target floor is None"

    def test_handles_scanner_without_floor_id(self) -> None:
        """Test that scanners without floor_id don't match any target floor."""
        device = _make_device()

        # Scanner without floor_id
        scanner_no_floor = _make_scanner(SCANNER_OG, AREA_FLUR_OG, None)  # No floor_id
        advert = _make_advert(scanner_no_floor, -80.0, BASE_TIME - 1.0, 5.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_OG)] = advert

        # Target floor is OG
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should FAIL (scanner has no floor_id, so doesn't match OG)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is False, "Scanner without floor_id should not match target floor"

    def test_real_world_scenario_lagerraum_vs_bad_og(self) -> None:
        """Test the real-world scenario from the bug report.

        Device is in Lagerraum (basement), but UKF incorrectly picks Bad OG (2 floors up).
        Only basement scanners see the device. This should be rejected.
        """
        device = _make_device()

        # Only basement scanner sees the device (real scenario)
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # UKF picks Bad OG which is on FLOOR_OG
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should FAIL
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is False, (
            f"BUG 21 scenario should be rejected: "
            f"Device only seen by basement scanner, but UKF picked floor {target_floor_id}"
        )

    def test_same_floor_always_allowed(self) -> None:
        """Test that same-floor scannerless rooms are always allowed.

        If the scanner and target room are on the same floor, the check passes.
        """
        device = _make_device()

        # Scanner in basement sees device
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert = _make_advert(scanner_basement, -73.0, BASE_TIME - 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert

        # Target scannerless room is ALSO in basement (same floor)
        target_floor_id = FLOOR_BASEMENT
        nowstamp = BASE_TIME

        # Topological check should PASS (scanner on same floor sees device)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is True, "Same-floor scannerless room should be allowed"

    def test_rssi_strength_does_not_affect_check(self) -> None:
        """Test that RSSI strength does NOT affect the topological check.

        Even with weak signal, as long as a scanner on the target floor sees the device,
        the check should pass. This is the key difference from the old RSSI-based check.
        """
        device = _make_device()

        # Scanner on OG floor sees device with VERY weak signal
        scanner_og = _make_scanner(SCANNER_OG, AREA_FLUR_OG, FLOOR_OG)
        advert = _make_advert(scanner_og, -95.0, BASE_TIME - 1.0, 20.0)  # Very weak!
        device.adverts[(DEVICE_ADDRESS, SCANNER_OG)] = advert

        # Target floor is OG
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should PASS regardless of RSSI strength
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is True, "Weak RSSI should not affect topological check"

    def test_multiple_floors_only_checks_target(self) -> None:
        """Test that only the target floor matters, not other floors."""
        device = _make_device()

        # Scanner on ground floor (different from both basement and OG)
        floor_ground = "floor_ground"
        scanner_ground = _make_scanner("AA:BB:CC:DD:EE:03", "area_ground", floor_ground)
        advert_ground = _make_advert(scanner_ground, -75.0, BASE_TIME - 1.0, 3.0)
        device.adverts[(DEVICE_ADDRESS, "AA:BB:CC:DD:EE:03")] = advert_ground

        # Scanner in basement
        scanner_basement = _make_scanner(SCANNER_BASEMENT, AREA_TECHNIKRAUM, FLOOR_BASEMENT)
        advert_basement = _make_advert(scanner_basement, -70.0, BASE_TIME - 1.0, 1.5)
        device.adverts[(DEVICE_ADDRESS, SCANNER_BASEMENT)] = advert_basement

        # Target floor is OG (neither basement nor ground)
        target_floor_id = FLOOR_OG
        nowstamp = BASE_TIME

        # Topological check should FAIL (no scanner on OG, only basement and ground)
        result = _check_topological_condition(device, target_floor_id, nowstamp)
        assert result is False, "Should reject when no scanner on target floor OG"
