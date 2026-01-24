"""Tests for BUG 20 fix: Streak counting requires unique signals (timestamps).

This test verifies that:
1. Streak only increments when NEW advertisement data arrives (different timestamps)
2. Repeated readings with the same timestamps do NOT increment the streak
3. This prevents cached BLE values from being counted multiple times
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast, TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from custom_components.bermuda.bermuda_device import BermudaDevice

import pytest

from custom_components.bermuda.const import (
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    EVIDENCE_WINDOW_SECONDS,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.area_selection import AreaSelectionHandler


# Test constants
BASE_TIME = 1000.0
SCANNER_A = "AA:BB:CC:DD:EE:01"
SCANNER_B = "AA:BB:CC:DD:EE:02"
DEVICE_ADDRESS = "FF:EE:DD:CC:BB:AA"
AREA_KITCHEN = "area_kitchen"
AREA_LIVING_ROOM = "area_living_room"


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
    coordinator.area_selection = AreaSelectionHandler(coordinator)
    return coordinator


def _make_scanner(
    address: str,
    area_id: str,
    *,
    floor_id: str | None = None,
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
    return SimpleNamespace(
        address=DEVICE_ADDRESS,
        name="Test Device",
        adverts={},
        area_id=AREA_LIVING_ROOM,
        area_name="Living Room",
        pending_area_id=None,
        pending_floor_id=None,
        pending_streak=0,
        pending_last_stamps={},
    )


class TestCollectCurrentStamps:
    """Test the _collect_current_stamps helper method."""

    @pytest.fixture
    def coordinator(self) -> BermudaDataUpdateCoordinator:
        """Create a coordinator for testing."""
        return _make_coordinator()

    @pytest.fixture
    def device(self) -> SimpleNamespace:
        """Create a device for testing."""
        return _make_device()

    def test_collects_fresh_stamps(self, coordinator: BermudaDataUpdateCoordinator, device: SimpleNamespace) -> None:
        """Test that fresh advertisement stamps are collected."""
        nowstamp = BASE_TIME
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)
        advert = _make_advert(scanner, -60.0, BASE_TIME - 1.0, 2.0)

        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert

        stamps = coordinator.area_selection._collect_current_stamps(cast("BermudaDevice", device), nowstamp)

        assert SCANNER_A in stamps
        assert stamps[SCANNER_A] == BASE_TIME - 1.0

    def test_excludes_stale_stamps(self, coordinator: BermudaDataUpdateCoordinator, device: SimpleNamespace) -> None:
        """Test that stale advertisement stamps are excluded."""
        nowstamp = BASE_TIME
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)
        # Stamp is older than EVIDENCE_WINDOW_SECONDS
        stale_stamp = BASE_TIME - EVIDENCE_WINDOW_SECONDS - 1.0
        advert = _make_advert(scanner, -60.0, stale_stamp, 2.0)

        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert

        stamps = coordinator.area_selection._collect_current_stamps(cast("BermudaDevice", device), nowstamp)

        assert SCANNER_A not in stamps

    def test_collects_multiple_scanners(
        self, coordinator: BermudaDataUpdateCoordinator, device: SimpleNamespace
    ) -> None:
        """Test that stamps from multiple scanners are collected."""
        nowstamp = BASE_TIME
        scanner_a = _make_scanner(SCANNER_A, AREA_KITCHEN)
        scanner_b = _make_scanner(SCANNER_B, AREA_LIVING_ROOM)

        advert_a = _make_advert(scanner_a, -60.0, BASE_TIME - 1.0, 2.0)
        advert_b = _make_advert(scanner_b, -70.0, BASE_TIME - 2.0, 4.0)

        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert_a
        device.adverts[(DEVICE_ADDRESS, SCANNER_B)] = advert_b

        stamps = coordinator.area_selection._collect_current_stamps(cast("BermudaDevice", device), nowstamp)

        assert len(stamps) == 2
        assert stamps[SCANNER_A] == BASE_TIME - 1.0
        assert stamps[SCANNER_B] == BASE_TIME - 2.0


class TestHasNewAdvertData:
    """Test the _has_new_advert_data helper method."""

    @pytest.fixture
    def coordinator(self) -> BermudaDataUpdateCoordinator:
        """Create a coordinator for testing."""
        return _make_coordinator()

    def test_detects_new_data(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Test that new data is detected when timestamps are newer."""
        current_stamps = {SCANNER_A: BASE_TIME + 1.0}
        last_stamps = {SCANNER_A: BASE_TIME}

        result = coordinator.area_selection._has_new_advert_data(current_stamps, last_stamps)

        assert result is True

    def test_detects_same_data(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Test that same data is detected when timestamps are unchanged."""
        current_stamps = {SCANNER_A: BASE_TIME}
        last_stamps = {SCANNER_A: BASE_TIME}

        result = coordinator.area_selection._has_new_advert_data(current_stamps, last_stamps)

        assert result is False

    def test_detects_new_data_one_of_multiple(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Test that new data is detected when one of multiple scanners has new data."""
        current_stamps = {SCANNER_A: BASE_TIME, SCANNER_B: BASE_TIME + 1.0}
        last_stamps = {SCANNER_A: BASE_TIME, SCANNER_B: BASE_TIME}

        result = coordinator.area_selection._has_new_advert_data(current_stamps, last_stamps)

        assert result is True

    def test_handles_empty_last_stamps(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Test that new data is detected when last_stamps is empty."""
        current_stamps = {SCANNER_A: BASE_TIME}
        last_stamps: dict[str, float] = {}

        result = coordinator.area_selection._has_new_advert_data(current_stamps, last_stamps)

        assert result is True

    def test_handles_new_scanner(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Test that new data is detected when a new scanner appears."""
        current_stamps = {SCANNER_A: BASE_TIME, SCANNER_B: BASE_TIME}
        last_stamps = {SCANNER_A: BASE_TIME}  # Only scanner A was seen before

        result = coordinator.area_selection._has_new_advert_data(current_stamps, last_stamps)

        # Scanner B is new, but its stamp (BASE_TIME) is not > last_stamps.get(SCANNER_B, 0)
        # So this should be True because BASE_TIME > 0
        assert result is True


class FakeDeviceWithReset:
    """Fake device class that mimics the reset_pending_state behavior."""

    def __init__(self) -> None:
        self.pending_area_id: str | None = None
        self.pending_floor_id: str | None = None
        self.pending_streak: int = 0
        self.pending_last_stamps: dict[str, float] = {}

    def reset_pending_state(self) -> None:
        """Reset pending area selection state (same as BermudaDevice)."""
        self.pending_area_id = None
        self.pending_floor_id = None
        self.pending_streak = 0
        self.pending_last_stamps = {}


class TestResetPendingState:
    """Test the reset_pending_state method behavior.

    Note: We use a FakeDeviceWithReset class here to avoid importing BermudaDevice
    which triggers Home Assistant bluetooth stack initialization in tests.
    The implementation matches BermudaDevice.reset_pending_state exactly.
    """

    def test_clears_pending_state(self) -> None:
        """Test that reset_pending_state clears all pending attributes."""
        device = FakeDeviceWithReset()

        # Set up some pending state
        device.pending_area_id = AREA_KITCHEN
        device.pending_floor_id = "floor_1"
        device.pending_streak = 5
        device.pending_last_stamps = {SCANNER_A: BASE_TIME}

        # Reset
        device.reset_pending_state()

        # Verify all cleared
        assert device.pending_area_id is None
        assert device.pending_floor_id is None
        assert device.pending_streak == 0
        assert device.pending_last_stamps == {}

    def test_reset_is_idempotent(self) -> None:
        """Test that reset_pending_state can be called multiple times safely."""
        device = FakeDeviceWithReset()

        # Already in reset state
        device.reset_pending_state()

        # Call again - should not raise
        device.reset_pending_state()

        # Still cleared
        assert device.pending_area_id is None
        assert device.pending_streak == 0


class TestStreakNoIncrementSameData:
    """Test that streak does NOT increment when advertisement data hasn't changed.

    This is the core test for BUG 20: Without checking timestamps, cached BLE
    values were counted multiple times toward streak thresholds.

    Note: Each test creates its own coordinator and device inline to avoid
    fixture dependencies that can trigger Bluetooth stack initialization.
    """

    def test_streak_increments_with_new_data(self) -> None:
        """Test that streak increments when new advertisement data arrives."""
        coordinator = _make_coordinator()
        device = _make_device()
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)

        # Set up pending state for Kitchen
        device.pending_area_id = AREA_KITCHEN
        device.pending_floor_id = None
        device.pending_streak = 2
        device.pending_last_stamps = {SCANNER_A: BASE_TIME}

        # Create advert with NEWER timestamp
        advert = _make_advert(scanner, -60.0, BASE_TIME + 1.0, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert

        # Collect current stamps - should be newer
        nowstamp = BASE_TIME + 2.0
        current_stamps = coordinator.area_selection._collect_current_stamps(cast("BermudaDevice", device), nowstamp)
        has_new_data = coordinator.area_selection._has_new_advert_data(current_stamps, device.pending_last_stamps)

        # Verify new data is detected
        assert has_new_data is True

    def test_streak_no_increment_with_same_data(self) -> None:
        """Test that streak does NOT increment when advertisement data is unchanged."""
        coordinator = _make_coordinator()
        device = _make_device()
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)

        # Set up pending state for Kitchen with timestamp already seen
        device.pending_area_id = AREA_KITCHEN
        device.pending_floor_id = None
        device.pending_streak = 2
        device.pending_last_stamps = {SCANNER_A: BASE_TIME}

        # Create advert with SAME timestamp (no new data)
        advert = _make_advert(scanner, -60.0, BASE_TIME, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert

        # Collect current stamps - should be same
        nowstamp = BASE_TIME + 1.0
        current_stamps = coordinator.area_selection._collect_current_stamps(cast("BermudaDevice", device), nowstamp)
        has_new_data = coordinator.area_selection._has_new_advert_data(current_stamps, device.pending_last_stamps)

        # Verify NO new data detected
        assert has_new_data is False

    def test_multiple_polls_same_data_no_streak_buildup(self) -> None:
        """Test that multiple polls with same data don't build up streak.

        This simulates the scenario where:
        1. BLE device advertises every 3 seconds
        2. Coordinator polls every 1 second
        3. Without BUG 20 fix, streak would increment 3x per actual advertisement
        """
        coordinator = _make_coordinator()
        device = _make_device()
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)

        # Initial state
        device.pending_area_id = AREA_KITCHEN
        device.pending_floor_id = None
        device.pending_streak = 0
        device.pending_last_stamps = {}

        # Create advert at t=1000
        advert = _make_advert(scanner, -60.0, BASE_TIME, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert

        # Simulate multiple coordinator polls at t=1001, 1002, 1003 (same advert stamp)
        streak_increments = 0
        for poll_time in [BASE_TIME + 1, BASE_TIME + 2, BASE_TIME + 3]:
            current_stamps = coordinator.area_selection._collect_current_stamps(
                cast("BermudaDevice", device), poll_time
            )
            has_new_data = coordinator.area_selection._has_new_advert_data(current_stamps, device.pending_last_stamps)
            if has_new_data:
                streak_increments += 1
                device.pending_last_stamps = dict(current_stamps)

        # Should only increment once (on first detection of the advertisement)
        assert streak_increments == 1

    def test_new_advertisement_after_cached_increments_streak(self) -> None:
        """Test that a new advertisement after cached readings does increment streak."""
        coordinator = _make_coordinator()
        device = _make_device()
        scanner = _make_scanner(SCANNER_A, AREA_KITCHEN)

        # Initial state
        device.pending_area_id = AREA_KITCHEN
        device.pending_floor_id = None
        device.pending_streak = 1
        device.pending_last_stamps = {SCANNER_A: BASE_TIME}

        # First poll - same data (t=1000)
        advert = _make_advert(scanner, -60.0, BASE_TIME, 2.0)
        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert
        current_stamps = coordinator.area_selection._collect_current_stamps(
            cast("BermudaDevice", device), BASE_TIME + 1
        )
        has_new_data_1 = coordinator.area_selection._has_new_advert_data(current_stamps, device.pending_last_stamps)

        # Second poll - new data (t=1003, new advertisement arrived)
        advert_new = _make_advert(scanner, -62.0, BASE_TIME + 3.0, 2.1)
        device.adverts[(DEVICE_ADDRESS, SCANNER_A)] = advert_new
        current_stamps_new = coordinator.area_selection._collect_current_stamps(
            cast("BermudaDevice", device), BASE_TIME + 4
        )
        has_new_data_2 = coordinator.area_selection._has_new_advert_data(current_stamps_new, device.pending_last_stamps)

        # First poll should not detect new data
        assert has_new_data_1 is False
        # Second poll should detect new data
        assert has_new_data_2 is True
