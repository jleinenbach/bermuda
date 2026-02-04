"""
Integration tests for auto-learning quality filters in AreaSelectionHandler.

These tests verify that Features 3 and 5 are correctly implemented at the
coordinator level in _update_device_correlations().

Feature 3: Confidence Filter (already tested at profile level, this tests wiring)
Feature 5: Quality Filters (velocity, RSSI variance, dwell time)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.area_selection import AreaSelectionHandler
from custom_components.bermuda.const import (
    AUTO_LEARNING_MAX_RSSI_VARIANCE,
    AUTO_LEARNING_MAX_VELOCITY,
    AUTO_LEARNING_MIN_CONFIDENCE,
    DWELL_TIME_MOVING_SECONDS,
    DWELL_TIME_SETTLING_SECONDS,
    MOVEMENT_STATE_MOVING,
    MOVEMENT_STATE_SETTLING,
    MOVEMENT_STATE_STATIONARY,
)


@dataclass
class FakeAdvert:
    """Fake advert for testing."""

    scanner_address: str | None = None
    rssi: float | None = None
    stamp: float | None = None
    hist_velocity: list[float] = field(default_factory=list)


@dataclass
class FakeDevice:
    """Fake device for testing."""

    address: str = "AA:BB:CC:DD:EE:FF"
    name: str = "Test Device"
    adverts: dict[str, FakeAdvert] = field(default_factory=dict)
    area_id: str | None = None
    area_changed_at: float = 0.0

    def get_dwell_time(self, stamp_now: float) -> float:
        """Return dwell time (time since area change)."""
        if self.area_changed_at == 0.0:
            return stamp_now  # Assume device has been in area "forever"
        return stamp_now - self.area_changed_at

    def get_movement_state(self, stamp_now: float) -> str:
        """Return movement state based on dwell time."""
        dwell_time = self.get_dwell_time(stamp_now)
        if dwell_time < DWELL_TIME_MOVING_SECONDS:
            return MOVEMENT_STATE_MOVING
        if dwell_time < DWELL_TIME_SETTLING_SECONDS:
            return MOVEMENT_STATE_SETTLING
        return MOVEMENT_STATE_STATIONARY


def make_coordinator_mock() -> MagicMock:
    """Create a mock coordinator with required attributes."""
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator.device_ukfs = {}
    coordinator._scanners = set()
    coordinator.ar = None
    coordinator.devices = {}
    return coordinator


def make_handler_with_mock() -> AreaSelectionHandler:
    """Create an AreaSelectionHandler with a mock coordinator."""
    coordinator = make_coordinator_mock()
    handler = AreaSelectionHandler(coordinator)
    # Ensure correlations dict is the same reference
    handler.coordinator.correlations = handler.coordinator.correlations
    return handler


# =============================================================================
# Feature 5: Velocity Filter Tests (Integration)
# =============================================================================


class TestVelocityFilterIntegration:
    """
    Feature 5: Velocity Filter at coordinator level.

    Tests that _update_device_correlations skips learning when device
    velocity exceeds AUTO_LEARNING_MAX_VELOCITY.
    """

    def test_high_velocity_skips_update(self) -> None:
        """Updates should be skipped when device is moving rapidly."""
        handler = make_handler_with_mock()
        device = FakeDevice()

        # Create adverts with high velocity history
        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[AUTO_LEARNING_MAX_VELOCITY + 0.5],  # Above threshold
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[AUTO_LEARNING_MAX_VELOCITY + 0.5],
        )

        # Call update
        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Should NOT create profile (update skipped due to velocity)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_low_velocity_allows_update(self) -> None:
        """Updates should proceed when device is stationary."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        # Create adverts with low velocity
        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],  # Below threshold
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        # Need to mock rssi variance since FakeAdvert doesn't have rssi_kalman
        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        # Should create profile
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]


# =============================================================================
# Feature 5: RSSI Variance Filter Tests (Integration)
# =============================================================================


class TestRssiVarianceFilterIntegration:
    """
    Feature 5: RSSI Variance Filter at coordinator level.

    Tests that _update_device_correlations skips learning when RSSI
    variance exceeds AUTO_LEARNING_MAX_RSSI_VARIANCE.
    """

    def test_high_rssi_variance_skips_update(self) -> None:
        """Updates should be skipped when RSSI is unstable."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        # Create adverts with low velocity but will mock high variance
        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        # Mock the _get_device_rssi_variance method to return high variance
        with patch.object(
            handler,
            "_get_device_rssi_variance",
            return_value=AUTO_LEARNING_MAX_RSSI_VARIANCE + 5.0,
        ):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        # Should NOT create profile
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_low_rssi_variance_allows_update(self) -> None:
        """Updates should proceed when RSSI is stable."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        # Mock low variance
        with patch.object(
            handler,
            "_get_device_rssi_variance",
            return_value=5.0,  # Below threshold
        ):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        # Should create profile
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]


# =============================================================================
# Feature 5: Dwell Time Filter Tests (Integration)
# =============================================================================


class TestMovementStateFilterIntegration:
    """
    Feature 5: Movement State Filter at coordinator level.

    Tests that _update_device_correlations skips learning when device
    is not STATIONARY (requires 10+ min in same room). This replaces
    the previous 30s dwell time check.
    """

    def test_moving_state_skips_update(self) -> None:
        """Updates should be skipped when device just entered room (MOVING)."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        # Device just entered 10 seconds ago → MOVING state (< 120s)
        device.area_changed_at = 990.0  # nowstamp=1000, so dwell=10s

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Should NOT create profile (MOVING state, not STATIONARY)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_settling_state_skips_update(self) -> None:
        """Updates should be skipped when device is SETTLING (2-10 min)."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        # Device has been in room for 300 seconds → SETTLING state (120-600s)
        device.area_changed_at = 700.0  # nowstamp=1000, so dwell=300s

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Should NOT create profile (SETTLING state, not STATIONARY)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_stationary_state_allows_update(self) -> None:
        """Updates should proceed when device is STATIONARY (10+ min)."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        # Device has been in room for 999 seconds → STATIONARY state (>= 600s)
        device.area_changed_at = 1.0  # nowstamp=1000, so dwell=999s

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        # Need to mock rssi variance as well
        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        # Should create profile (STATIONARY state)
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]

    def test_uninitialized_area_changed_at_skips_update(self) -> None:
        """Updates should be skipped when area_changed_at == 0.0 (startup/first discovery).

        get_movement_state() returns STATIONARY for area_changed_at == 0.0
        to prevent area-selection flapping. But for auto-learning, this is
        dangerous: the initial assignment may be wrong and we have no evidence
        of sustained presence. The guard must block explicitly.
        """
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 0.0  # Uninitialized (startup/first discovery)

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Should NOT create profile (uninitialized area_changed_at)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )


# =============================================================================
# Feature 3: Confidence Filter Tests (Integration - verify wiring)
# =============================================================================


class TestConfidenceFilterIntegration:
    """
    Feature 3: Confidence Filter wiring verification.

    The confidence filter is implemented in AreaProfile.update() but this
    verifies that _update_device_correlations passes the confidence parameter
    correctly and that the coordinator-level check also works.
    """

    def test_low_confidence_skips_at_coordinator_level(self) -> None:
        """Low confidence should skip before even reaching profile update."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 0.0

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
            confidence=0.3,  # Below threshold (0.5)
        )

        # Should NOT create profile (low confidence)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_high_confidence_allows_update(self) -> None:
        """High confidence should allow update to proceed."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=0.8,  # Above threshold
            )

        # Should create profile
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]


# =============================================================================
# Feature 1: New Data Check Tests (Integration - verify wiring)
# =============================================================================


class TestNewDataCheckIntegration:
    """
    Feature 1: New Data Check wiring verification.

    Verifies that _update_device_correlations correctly collects timestamps
    from device adverts and tracks them between calls.
    """

    def test_first_update_always_succeeds(self) -> None:
        """First update for a device should always succeed."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        # First update should succeed
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]

        # Last stamps should be stored
        assert device.address in handler._device_last_stamps
        assert "scanner_a" in handler._device_last_stamps[device.address]

    def test_unchanged_stamps_rejected_by_profile(self) -> None:
        """Subsequent update with same stamps should be rejected by profile."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            # First update
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

            # Get sample count after first update
            profile = handler.correlations[device.address]["area.living_room"]
            first_update_count = sum(c.sample_count for c in profile._correlations.values())

            # Second update with SAME stamps (nowstamp advanced to pass interval check)
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1006.0,  # 6s later (passes interval check)
            )

            # Sample count should NOT increase (new data check rejected it)
            second_update_count = sum(c.sample_count for c in profile._correlations.values())
            assert second_update_count == first_update_count, (
                "Sample count increased despite no new advertisement data. "
                "Feature 1 new data check should have rejected the update."
            )

    def test_new_stamps_accepted(self) -> None:
        """Update with new stamps should be accepted."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            # First update
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

            profile = handler.correlations[device.address]["area.living_room"]
            first_update_count = sum(c.sample_count for c in profile._correlations.values())

            # Update advert stamps to simulate new data
            device.adverts["scanner_a"].stamp = 1006.0
            device.adverts["scanner_b"].stamp = 1006.0

            # Second update with NEW stamps
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1006.0,
            )

            # Sample count SHOULD increase (new data available)
            second_update_count = sum(c.sample_count for c in profile._correlations.values())
            assert second_update_count > first_update_count, (
                "Sample count did not increase despite new advertisement data. "
                "Feature 1 should have accepted the update."
            )


# =============================================================================
# Combined Quality Filter Tests (Integration)
# =============================================================================


class TestCombinedQualityFiltersIntegration:
    """
    Test that all quality filters work together correctly.
    """

    def test_all_filters_must_pass(self) -> None:
        """Update rejected if ANY quality filter fails."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        # Good velocity but high variance
        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],  # Good velocity
        )

        # Mock high variance
        with patch.object(
            handler,
            "_get_device_rssi_variance",
            return_value=AUTO_LEARNING_MAX_RSSI_VARIANCE + 5.0,
        ):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=0.8,  # Good confidence
            )

        # Should NOT create profile (high variance)
        assert device.address not in handler.correlations or "area.living_room" not in handler.correlations.get(
            device.address, {}
        )

    def test_all_filters_pass_allows_update(self) -> None:
        """Update proceeds when all quality filters pass."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],  # Good velocity
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):  # Good variance
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
                confidence=0.8,  # Good confidence
            )

        # Should create profile
        assert device.address in handler.correlations
        assert "area.living_room" in handler.correlations[device.address]


# =============================================================================
# Auto-Learning Stats Tests
# =============================================================================


class TestAutoLearningStats:
    """Test that auto-learning statistics are recorded correctly."""

    def test_stats_recorded_on_successful_update(self) -> None:
        """Successful updates should be recorded in stats."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        assert handler._auto_learning_stats.updates_performed >= 1

    def test_stats_recorded_on_skipped_update(self) -> None:
        """Skipped updates should be recorded with reason."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 0.0

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        # Skip due to low confidence
        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
            confidence=0.3,  # Low confidence
        )

        assert handler._auto_learning_stats.updates_skipped_confidence >= 1

    def test_diagnostics_output(self) -> None:
        """Diagnostic output should include all stats."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )
        device.adverts["scanner_b"] = FakeAdvert(
            scanner_address="scanner_b",
            rssi=-60.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with patch.object(handler, "_get_device_rssi_variance", return_value=5.0):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        diagnostics = handler.get_auto_learning_diagnostics()
        assert "updates_performed" in diagnostics
        assert "updates_skipped" in diagnostics
        assert "skip_ratio" in diagnostics

    def test_stats_skip_reason_not_stationary(self) -> None:
        """Movement state skip should be tracked as not_stationary."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 999.0  # dwell=1s → MOVING

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        assert handler._auto_learning_stats.updates_skipped_not_stationary >= 1
        # Must NOT fall through to interval counter
        assert handler._auto_learning_stats.updates_skipped_interval == 0

    def test_stats_skip_reason_uninitialized_dwell(self) -> None:
        """Uninitialized area_changed_at skip should be tracked correctly."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 0.0

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        handler._update_device_correlations(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
            nowstamp=1000.0,
        )

        assert handler._auto_learning_stats.updates_skipped_uninitialized >= 1
        assert handler._auto_learning_stats.updates_skipped_interval == 0

    def test_stats_skip_reason_ambiguous_signal(self) -> None:
        """Ambiguous signal skip should be tracked correctly."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        device.area_id = "area.living_room"
        device.area_changed_at = 1.0  # dwell=999s → STATIONARY

        device.adverts["scanner_a"] = FakeAdvert(
            scanner_address="scanner_a",
            rssi=-50.0,
            stamp=1000.0,
            hist_velocity=[0.1],
        )

        with (
            patch.object(handler, "_get_device_rssi_variance", return_value=5.0),
            patch.object(handler, "_is_signal_ambiguous", return_value=True),
        ):
            handler._update_device_correlations(
                device=device,
                area_id="area.living_room",
                primary_rssi=-50.0,
                primary_scanner_addr="scanner_a",
                other_readings={"scanner_b": -60.0},
                nowstamp=1000.0,
            )

        assert handler._auto_learning_stats.updates_skipped_ambiguous >= 1
        assert handler._auto_learning_stats.updates_skipped_interval == 0

    def test_diagnostics_output_includes_all_skip_reasons(self) -> None:
        """Diagnostics output should include all skip reason counters."""
        handler = make_handler_with_mock()
        diagnostics = handler.get_auto_learning_diagnostics()
        skipped = diagnostics["updates_skipped"]
        assert "interval" in skipped
        assert "low_confidence" in skipped
        assert "uninitialized_dwell" in skipped
        assert "not_stationary" in skipped
        assert "high_velocity" in skipped
        assert "high_rssi_variance" in skipped
        assert "ambiguous_signal" in skipped


# =============================================================================
# Feature 6: RoomProfile Ambiguity Check Tests
# =============================================================================


class TestRoomProfileAmbiguityCheck:
    """
    Feature 6: RoomProfile-based ambiguity check for new devices.

    Tests that _is_signal_ambiguous correctly uses RoomProfiles as fallback
    when no AreaProfiles exist for a device. This prevents the self-reinforcing
    feedback loop where wrong assignments corrupt RoomProfiles for ALL devices.
    """

    def test_no_room_profiles_returns_false(self) -> None:
        """When no RoomProfiles exist, should return False (allow learning)."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        # Ensure no correlations and no room_profiles
        handler.coordinator.correlations = {}
        handler.coordinator.room_profiles = {}

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False

    def test_no_target_room_profile_returns_false(self) -> None:
        """When target area has no RoomProfile, should return False."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        # Only another room has a profile, not the target
        mock_other_profile = MagicMock()
        mock_other_profile.get_match_score.return_value = 0.8
        handler.coordinator.room_profiles = {"area.kitchen": mock_other_profile}

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",  # Target area has no profile
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False

    def test_low_target_score_returns_false(self) -> None:
        """When target RoomProfile has poor match, should return False."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        # Target has low score (below ROOM_AMBIGUITY_MIN_SCORE = 0.4)
        mock_target = MagicMock()
        mock_target.get_match_score.return_value = 0.3

        mock_other = MagicMock()
        mock_other.get_match_score.return_value = 0.8

        handler.coordinator.room_profiles = {
            "area.living_room": mock_target,
            "area.kitchen": mock_other,
        }

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False

    def test_ambiguous_scores_returns_true(self) -> None:
        """When another RoomProfile scores close to target, should return True."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        # Target: 0.6, Competitor: 0.55 → diff = 0.05 < ROOM_AMBIGUITY_MAX_DIFF (0.15)
        mock_target = MagicMock()
        mock_target.get_match_score.return_value = 0.6

        mock_competitor = MagicMock()
        mock_competitor.get_match_score.return_value = 0.55

        handler.coordinator.room_profiles = {
            "area.living_room": mock_target,
            "area.kitchen": mock_competitor,
        }

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is True

    def test_clear_winner_returns_false(self) -> None:
        """When target significantly outscores others, should return False."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        # Target: 0.8, Competitor: 0.5 → diff = 0.3 > ROOM_AMBIGUITY_MAX_DIFF (0.15)
        mock_target = MagicMock()
        mock_target.get_match_score.return_value = 0.8

        mock_competitor = MagicMock()
        mock_competitor.get_match_score.return_value = 0.5

        handler.coordinator.room_profiles = {
            "area.living_room": mock_target,
            "area.kitchen": mock_competitor,
        }

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False

    def test_competitor_below_min_score_returns_false(self) -> None:
        """When competitor score is below minimum, it's not considered."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        # Target: 0.5, Competitor: 0.35 (below ROOM_AMBIGUITY_MIN_SCORE)
        # Even though diff = 0.15 = ROOM_AMBIGUITY_MAX_DIFF, competitor is ignored
        mock_target = MagicMock()
        mock_target.get_match_score.return_value = 0.5

        mock_competitor = MagicMock()
        mock_competitor.get_match_score.return_value = 0.35  # Below 0.4

        handler.coordinator.room_profiles = {
            "area.living_room": mock_target,
            "area.kitchen": mock_competitor,
        }

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False

    def test_area_profiles_checked_first(self) -> None:
        """When AreaProfiles exist, they should be checked (not RoomProfiles)."""
        handler = make_handler_with_mock()
        device = FakeDevice()

        # Create AreaProfile correlations for the device
        mock_area_profile = MagicMock()
        mock_area_profile.get_absolute_z_scores.return_value = [
            ("scanner_a", 1.5),  # z < 2.0 → ambiguous in AreaProfile check
        ]

        handler.coordinator.correlations = {
            device.address: {
                "area.kitchen": mock_area_profile,  # Another area matches too well
            }
        }

        # RoomProfiles also exist but shouldn't be used
        mock_room_profile = MagicMock()
        mock_room_profile.get_match_score.return_value = 0.9
        handler.coordinator.room_profiles = {
            "area.living_room": mock_room_profile,
            "area.kitchen": mock_room_profile,
        }

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )

        # Should return True because AreaProfile check found ambiguity
        assert result is True
        # RoomProfile.get_match_score should NOT have been called
        mock_room_profile.get_match_score.assert_not_called()

    def test_single_room_profile_returns_false(self) -> None:
        """When only one RoomProfile exists, no ambiguity possible."""
        handler = make_handler_with_mock()
        device = FakeDevice()
        handler.coordinator.correlations = {}

        mock_target = MagicMock()
        mock_target.get_match_score.return_value = 0.8

        # Only one room profile exists
        handler.coordinator.room_profiles = {"area.living_room": mock_target}

        result = handler._is_signal_ambiguous(
            device=device,
            area_id="area.living_room",
            primary_rssi=-50.0,
            primary_scanner_addr="scanner_a",
            other_readings={"scanner_b": -60.0},
        )
        assert result is False
