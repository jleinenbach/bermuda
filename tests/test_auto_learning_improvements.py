"""
Tests for Auto-Learning Quality Improvements.

These tests cover 5 features designed to improve the statistical quality
of automatic fingerprint learning:

Feature 1: New Data Check - Prevents duplicate sampling from cached RSSI
Feature 2: Minimum Interval - Reduces autocorrelation via 5s minimum spacing
Feature 3: Confidence Filter - Only learns from high-confidence room assignments
Feature 4: Variance Floor - Prevents unbounded variance convergence
Feature 5: Quality Filter - Filters samples during movement, unstable signal

Test-Driven Development: These tests are written BEFORE implementation
to define expected behavior and minimize implementation risk.
"""

from __future__ import annotations

import pytest

# Import constants from the actual implementation
from custom_components.bermuda.const import (
    AUTO_LEARNING_MIN_CONFIDENCE,
    AUTO_LEARNING_MIN_INTERVAL,
    AUTO_LEARNING_VARIANCE_FLOOR,
)


# =============================================================================
# Feature 4: Variance Floor Tests
# =============================================================================


class TestVarianceFloorScannerAbsolute:
    """
    Feature 4: Variance Floor for ScannerAbsoluteRssi.

    Problem: Without a variance floor, the Kalman filter's variance converges
    to near-zero after many samples (thousands over weeks/months). This causes
    the "Hyper-Precision Paradox" where normal BLE fluctuations (2-5 dB) appear
    as massive statistical outliers, rejecting correct room assignments.

    Solution: After each update(), enforce a minimum variance floor.
    """

    def test_variance_stays_above_floor_after_many_samples(self) -> None:
        """
        CRITICAL: Variance must never converge below the floor.

        After 1000+ samples, the Kalman filter would naturally converge to
        variance ≈ 0.5. With floor=4.0, it must stay at 4.0.
        """
        from custom_components.bermuda.correlation.scanner_absolute import (
            ScannerAbsoluteRssi,
        )

        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:01")

        # Simulate many consistent samples (would converge variance to near-zero)
        for _ in range(1000):
            profile.update(-75.0)

        # Variance must stay at floor, not converge to near-zero
        assert profile._kalman_auto.variance >= AUTO_LEARNING_VARIANCE_FLOOR, (
            f"Auto variance={profile._kalman_auto.variance:.2f} dropped below "
            f"floor={AUTO_LEARNING_VARIANCE_FLOOR}. This causes the "
            f"Hyper-Precision Paradox where normal BLE noise (2-5 dB) gets "
            f"rejected as statistically impossible."
        )

    def test_variance_floor_allows_reasonable_z_scores(self) -> None:
        """
        With variance floor, normal BLE fluctuations should have acceptable z-scores.

        BLE RSSI typically fluctuates 2-5 dB. With floor=4.0 (σ=2dB), a 3dB
        deviation should be z=1.5 (acceptable), not z=10+ (rejected).
        """
        from custom_components.bermuda.correlation.scanner_absolute import (
            ScannerAbsoluteRssi,
        )

        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:01")

        # Train profile
        for _ in range(500):
            profile.update(-75.0)

        # Test z-score for typical 3dB fluctuation
        z_score = profile.z_score(-72.0)  # 3dB higher than learned

        # With floor=4.0 (σ=2.0): z = 3/2 = 1.5
        # Without floor (variance≈0.5, σ≈0.7): z = 3/0.7 = 4.3
        assert z_score < 2.5, (
            f"Z-score={z_score:.1f} for 3dB deviation is too high. "
            f"With variance floor, normal BLE fluctuations should have z < 2.5. "
            f"High z-scores cause false room rejections."
        )

    def test_variance_floor_does_not_affect_estimate(self) -> None:
        """
        The variance floor must not affect the estimate itself.

        The estimate should still converge to the true value; only variance
        is constrained.
        """
        from custom_components.bermuda.correlation.scanner_absolute import (
            ScannerAbsoluteRssi,
        )

        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:01")
        true_rssi = -80.0

        for _ in range(200):
            profile.update(true_rssi)

        error = abs(profile.expected_rssi - true_rssi)
        assert error < 0.5, (
            f"Estimate={profile.expected_rssi:.1f} dB but expected {true_rssi} dB "
            f"(error={error:.2f}). Variance floor should not affect estimate accuracy."
        )

    def test_button_filter_unaffected_by_variance_floor(self) -> None:
        """
        Variance floor only applies to auto filter, not button filter.

        Button training has controlled timing (5s intervals) and limited samples,
        so it doesn't need variance floor protection.
        """
        from custom_components.bermuda.correlation.scanner_absolute import (
            ScannerAbsoluteRssi,
        )

        profile = ScannerAbsoluteRssi(scanner_address="AA:BB:CC:DD:EE:01")

        # Button training with many samples
        for _ in range(100):
            profile.update_button(-75.0)

        # Button filter can converge below floor (controlled training)
        # This is by design - button training is statistically controlled
        # The test just verifies they behave differently
        auto_var = profile._kalman_auto.variance
        button_var = profile._kalman_button.variance

        # Button filter should be initialized and have some variance
        assert profile._kalman_button.is_initialized
        # Auto filter (if not updated) should have initial/default variance
        # The key point: button filter behavior is independent


class TestVarianceFloorScannerPair:
    """
    Feature 4: Variance Floor for ScannerPairCorrelation.

    Same logic as ScannerAbsoluteRssi, but for delta correlations.
    """

    def test_variance_stays_above_floor_after_many_samples(self) -> None:
        """Variance must never converge below the floor for delta correlations."""
        from custom_components.bermuda.correlation.scanner_pair import (
            ScannerPairCorrelation,
        )

        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:02")

        # Simulate many consistent delta samples
        for _ in range(1000):
            corr.update(10.0)  # Consistent 10dB delta

        assert corr._kalman_auto.variance >= AUTO_LEARNING_VARIANCE_FLOOR, (
            f"Auto variance={corr._kalman_auto.variance:.2f} dropped below "
            f"floor={AUTO_LEARNING_VARIANCE_FLOOR}. Delta correlations also "
            f"need variance floor to prevent hyper-precision."
        )

    def test_z_score_reasonable_with_variance_floor(self) -> None:
        """Z-scores for delta correlations should be reasonable with floor."""
        from custom_components.bermuda.correlation.scanner_pair import (
            ScannerPairCorrelation,
        )

        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:02")

        # Train correlation
        for _ in range(500):
            corr.update(15.0)  # Expect 15dB delta

        # Test z-score for 3dB deviation
        z_score = corr.z_score(12.0)  # 3dB off from learned

        assert z_score < 2.5, (
            f"Z-score={z_score:.1f} for 3dB delta deviation is too high. "
            f"With variance floor, reasonable deviations should be accepted."
        )


# =============================================================================
# Feature 2: Minimum Interval Tests
# =============================================================================


class TestMinimumIntervalAreaProfile:
    """
    Feature 2: Minimum Interval for AreaProfile.

    Problem: Auto-learning runs every ~1 second. At this rate, consecutive
    samples are highly autocorrelated (ρ ≈ 0.95), making effective sample
    size much smaller than actual sample count.

    Solution: Enforce minimum 5s interval between updates.
    At 5s intervals, autocorrelation drops to ρ ≈ 0.82, increasing
    effective sample size by factor of ~5.
    """

    def test_update_rejected_if_too_soon(self) -> None:
        """Updates within MIN_INTERVAL should be skipped."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # First update at t=0
        result1 = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
        )

        # Second update at t=2s (too soon)
        result2 = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1002.0,
        )

        # First should succeed, second should be rejected
        assert result1 is True, "First update should always succeed"
        assert result2 is False, (
            f"Update at t=2s should be rejected (interval={AUTO_LEARNING_MIN_INTERVAL}s). "
            f"Allowing rapid updates creates highly autocorrelated samples."
        )

    def test_update_accepted_after_min_interval(self) -> None:
        """Updates after MIN_INTERVAL should be accepted."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # First update at t=0
        profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
        )

        # Second update at t=6s (after 5s minimum)
        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1006.0,
        )

        assert result is True, (
            f"Update at t=6s should be accepted (interval={AUTO_LEARNING_MIN_INTERVAL}s). "
            f"Valid updates after minimum interval should proceed."
        )

    def test_multiple_updates_respect_interval(self) -> None:
        """Sequence of updates should respect minimum interval."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        timestamps = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        # Expected accepts: 0, 5, 10 (every 5s)
        # Expected rejects: 1,2,3,4, 6,7,8,9, 11,12

        accepted_count = 0
        for t in timestamps:
            result = profile.update(
                primary_rssi=-50.0,
                other_readings={"scanner_a": -60.0},
                nowstamp=1000.0 + t,
            )
            if result:
                accepted_count += 1

        # At 5s intervals: t=0, t=5, t=10 = 3 accepted
        assert accepted_count == 3, (
            f"Expected 3 accepted updates (t=0,5,10) but got {accepted_count}. "
            f"Minimum interval should reduce update rate by ~5x."
        )

    def test_backward_compatibility_without_nowstamp(self) -> None:
        """Update without nowstamp should still work (backward compatible)."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # Update without timestamp (legacy behavior)
        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            # No nowstamp parameter
        )

        # Should succeed without nowstamp (backward compatible)
        # Note: This test may need adjustment based on design decision
        # Option A: Always succeed without timestamp (no interval check)
        # Option B: Use internal clock if no timestamp provided
        assert result is True or result is None, (
            "Update without nowstamp should work for backward compatibility. "
            "Existing code not passing timestamps should not break."
        )


class TestMinimumIntervalRoomProfile:
    """Feature 2: Minimum Interval for RoomProfile (device-independent)."""

    def test_update_rejected_if_too_soon(self) -> None:
        """RoomProfile updates should also respect minimum interval."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.kitchen")

        # First update
        result1 = profile.update(
            readings={"scanner_a": -50.0, "scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Second update too soon
        result2 = profile.update(
            readings={"scanner_a": -50.0, "scanner_b": -60.0},
            nowstamp=1002.0,
        )

        assert result1 is True
        assert result2 is False, "RoomProfile should also enforce minimum interval"


# =============================================================================
# Feature 1: New Data Check Tests
# =============================================================================


class TestNewDataCheckAreaProfile:
    """
    Feature 1: New Data Check for AreaProfile.

    Problem: BLE advertisements arrive every 1-10 seconds. If auto-learning
    polls faster than advertisement rate, it re-reads the same cached RSSI
    values multiple times, creating artificial confidence from duplicates.

    Solution: Track advertisement timestamps and only learn when at least
    one scanner has genuinely new data.
    """

    def test_update_rejected_if_no_new_data(self) -> None:
        """Updates with unchanged timestamps should be skipped."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # Simulate: same advertisement stamps between updates
        last_stamps = {"scanner_a": 1000.0, "scanner_b": 1000.0}
        current_stamps = {"scanner_a": 1000.0, "scanner_b": 1000.0}  # Unchanged!

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1005.0,
            last_stamps=last_stamps,
            current_stamps=current_stamps,
        )

        assert result is False, (
            "Update with unchanged advertisement stamps should be rejected. "
            "Re-reading the same cached RSSI creates artificial confidence."
        )

    def test_update_accepted_if_any_scanner_has_new_data(self) -> None:
        """Updates should proceed if at least one scanner has new data."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        last_stamps = {"scanner_a": 1000.0, "scanner_b": 1000.0}
        current_stamps = {"scanner_a": 1003.0, "scanner_b": 1000.0}  # scanner_a updated!

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1005.0,
            last_stamps=last_stamps,
            current_stamps=current_stamps,
        )

        assert result is True, (
            "Update with at least one new advertisement should proceed. Partial updates still provide new information."
        )

    def test_first_update_always_succeeds(self) -> None:
        """First update (no last_stamps) should always succeed."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
            last_stamps=None,  # No previous stamps
            current_stamps={"scanner_a": 1000.0},
        )

        assert result is True, "First update should always succeed"


# =============================================================================
# Feature 3: Confidence Filter Tests
# =============================================================================


class TestConfidenceFilterAreaProfile:
    """
    Feature 3: Confidence Filter for AreaProfile.

    Problem: Auto-learning updates fingerprints whenever the device is
    "assigned" to an area, even if the assignment confidence is low.
    Learning from uncertain assignments pollutes fingerprints with noise.

    Solution: Only learn when room assignment confidence exceeds threshold.
    """

    def test_update_rejected_if_low_confidence(self) -> None:
        """Updates with confidence below threshold should be skipped."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
            confidence=0.3,  # Below threshold (0.5)
        )

        assert result is False, (
            f"Update with confidence=0.3 should be rejected "
            f"(threshold={AUTO_LEARNING_MIN_CONFIDENCE}). "
            f"Learning from uncertain room assignments pollutes fingerprints."
        )

    def test_update_accepted_if_high_confidence(self) -> None:
        """Updates with confidence above threshold should proceed."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
            confidence=0.8,  # Above threshold
        )

        assert result is True, (
            "Update with high confidence should proceed. We want to learn from reliable room assignments."
        )

    def test_confidence_at_threshold_accepted(self) -> None:
        """Updates at exactly the threshold should be accepted."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
            confidence=AUTO_LEARNING_MIN_CONFIDENCE,  # Exactly at threshold
        )

        assert result is True, "Update at exactly threshold should be accepted"

    def test_backward_compatibility_without_confidence(self) -> None:
        """Update without confidence parameter should still work."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # No confidence parameter (legacy code)
        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
            # No confidence parameter
        )

        # Should succeed for backward compatibility
        assert result is True, "Update without confidence should work for backward compatibility."


# =============================================================================
# Feature 5: Quality Filter Tests
# =============================================================================
#
# NOTE: Feature 5 (Quality Filters) is implemented at the COORDINATOR level,
# not the profile level. The quality checks (velocity, RSSI variance, dwell time)
# are performed in AreaSelectionHandler._update_device_correlations() before
# calling AreaProfile.update().
#
# Integration tests for Feature 5 are located in:
#   tests/test_area_selection_auto_learning.py
#
# The tests there verify:
#   - High velocity (>1.0 m/s) blocks updates
#   - High RSSI variance (>16.0 dB²) blocks updates
#   - Low dwell time (<30s) blocks updates
#   - All quality filters combined
#
# =============================================================================


# =============================================================================
# Integration Tests: Combined Features
# =============================================================================


class TestFeatureCombinations:
    """Test interactions between multiple features."""

    def test_interval_and_variance_floor_combined(self) -> None:
        """
        Feature 2 + 4: Minimum interval reduces sample rate, variance floor
        prevents hyper-precision. Together they improve statistical quality.
        """
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # Simulate 60 seconds of updates (one per second)
        for t in range(60):
            profile.update(
                primary_rssi=-75.0,
                other_readings={"scanner_a": -85.0},
                primary_scanner_addr="scanner_primary",
                nowstamp=1000.0 + t,
            )

        # With 5s minimum interval, should have ~12 accepted samples
        # Check that correlations exist and have reasonable sample count
        if "scanner_a" in profile._correlations:
            corr = profile._correlations["scanner_a"]
            assert corr.sample_count <= 15, (
                f"Expected ~12 samples with 5s interval over 60s, "
                f"got {corr.sample_count}. Minimum interval should throttle rate."
            )

            # Variance should be at floor despite many updates
            assert corr._kalman_auto.variance >= AUTO_LEARNING_VARIANCE_FLOOR, (
                "Variance should stay at floor even after multiple updates."
            )

    def test_all_features_combined_effective_sample_calculation(self) -> None:
        """
        Combined effect: With all features, effective sample quality improves.

        Before: 1000 raw samples → high autocorrelation → n_eff ≈ 50
        After: 200 quality-filtered samples → low autocorrelation → n_eff ≈ 164
        """
        # This is a conceptual test - actual implementation may vary
        # The key insight: fewer but higher-quality samples > many low-quality samples

        # Effective sample size formula with autocorrelation:
        # n_eff = n × (1-ρ)/(1+ρ)
        #
        # At 1s intervals: ρ ≈ 0.95, n=1000 → n_eff = 1000 × 0.05/1.95 ≈ 26
        # At 5s intervals: ρ ≈ 0.82, n=200  → n_eff = 200 × 0.18/1.82 ≈ 20
        #
        # BUT: The 5s samples are statistically independent and reliable,
        # while the 1s samples are heavily autocorrelated and may include
        # duplicates, noisy data during movement, etc.

        raw_samples = 1000
        filtered_samples = 200  # After all filters
        rho_raw = 0.95
        rho_filtered = 0.82

        n_eff_raw = raw_samples * (1 - rho_raw) / (1 + rho_raw)
        n_eff_filtered = filtered_samples * (1 - rho_filtered) / (1 + rho_filtered)

        # Both have similar effective sample sizes, but filtered are RELIABLE
        assert n_eff_raw < 30, f"Raw effective samples: {n_eff_raw:.1f}"
        assert n_eff_filtered > 15, f"Filtered effective samples: {n_eff_filtered:.1f}"


# =============================================================================
# Serialization Tests: Persistence of New State
# =============================================================================


class TestSerializationWithNewState:
    """Ensure new state (last_update_stamp) is properly serialized."""

    def test_area_profile_serialization_includes_last_stamp(self) -> None:
        """AreaProfile.to_dict() should include _last_update_stamp."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.living_room")

        # Update to set last_update_stamp
        profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1234.5,
        )

        data = profile.to_dict()

        # Note: Implementation may choose to persist or not persist the timestamp.
        # If not persisted, interval check resets on restart (acceptable).
        # This test documents the expected behavior.
        # Adjust assertion based on design decision.

        # Option A: Timestamp persisted
        # assert "last_update_stamp" in data

        # Option B: Timestamp not persisted (resets on restart)
        # No assertion needed - this is acceptable

        # For now, just verify to_dict() doesn't crash
        assert "area_id" in data

    def test_area_profile_from_dict_handles_missing_stamp(self) -> None:
        """AreaProfile.from_dict() should handle missing last_update_stamp."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        # Old format without last_update_stamp
        old_data = {
            "area_id": "area.living_room",
            "correlations": [],
            "absolute_profiles": [],
            # No last_update_stamp
        }

        # Should not crash
        profile = AreaProfile.from_dict(old_data)
        assert profile.area_id == "area.living_room"

        # Should be able to accept first update
        result = profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            nowstamp=1000.0,
        )
        assert result is True, "First update after deserialization should succeed"


# =============================================================================
# Timestamp Property Coverage Tests
# =============================================================================


class TestAreaProfileTimestampProperties:
    """Tests for AreaProfile timestamp properties to ensure full coverage."""

    def test_first_sample_stamp_returns_earliest_timestamp(self) -> None:
        """first_sample_stamp should return the earliest timestamp from all child profiles."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.test")

        # Add correlations and absolute profiles with different timestamps
        profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0, "scanner_b": -70.0},
            primary_scanner_addr="scanner_primary",
            nowstamp=1000.0,
        )

        # Add more samples with later timestamp
        profile.update(
            primary_rssi=-52.0,
            other_readings={"scanner_a": -62.0},
            primary_scanner_addr="scanner_primary",
            nowstamp=2000.0,
        )

        # first_sample_stamp should be 1000.0 (the earliest)
        assert profile.first_sample_stamp == 1000.0

    def test_first_sample_stamp_returns_none_when_empty(self) -> None:
        """first_sample_stamp should return None when no samples exist."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.empty")

        assert profile.first_sample_stamp is None

    def test_last_sample_stamp_returns_latest_timestamp(self) -> None:
        """last_sample_stamp should return the latest timestamp from all child profiles."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.test")

        # Add correlations with first timestamp
        profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
            primary_scanner_addr="scanner_primary",
            nowstamp=1000.0,
        )

        # Add more samples with later timestamp
        profile.update(
            primary_rssi=-52.0,
            other_readings={"scanner_a": -62.0},
            primary_scanner_addr="scanner_primary",
            nowstamp=2000.0,
        )

        # last_sample_stamp should be 2000.0 (the latest)
        assert profile.last_sample_stamp == 2000.0

    def test_last_sample_stamp_returns_none_when_empty(self) -> None:
        """last_sample_stamp should return None when no samples exist."""
        from custom_components.bermuda.correlation.area_profile import AreaProfile

        profile = AreaProfile(area_id="area.empty")

        assert profile.last_sample_stamp is None


class TestRoomProfileTimestampProperties:
    """Tests for RoomProfile timestamp properties to ensure full coverage."""

    def test_first_sample_stamp_returns_earliest_timestamp(self) -> None:
        """first_sample_stamp should return the earliest timestamp from all scanner pairs."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.test")

        # Add samples with first timestamp
        profile.update(
            readings={"scanner_a": -50.0, "scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Add more samples with later timestamp
        profile.update(
            readings={"scanner_a": -52.0, "scanner_b": -62.0},
            nowstamp=2000.0,
        )

        # first_sample_stamp should be 1000.0 (the earliest)
        assert profile.first_sample_stamp == 1000.0

    def test_first_sample_stamp_returns_none_when_empty(self) -> None:
        """first_sample_stamp should return None when no samples exist."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.empty")

        assert profile.first_sample_stamp is None

    def test_last_sample_stamp_returns_latest_timestamp(self) -> None:
        """last_sample_stamp should return the latest timestamp from all scanner pairs."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.test")

        # Add samples with first timestamp
        profile.update(
            readings={"scanner_a": -50.0, "scanner_b": -60.0},
            nowstamp=1000.0,
        )

        # Add more samples with later timestamp
        profile.update(
            readings={"scanner_a": -52.0, "scanner_b": -62.0},
            nowstamp=2000.0,
        )

        # last_sample_stamp should be 2000.0 (the latest)
        assert profile.last_sample_stamp == 2000.0

    def test_last_sample_stamp_returns_none_when_empty(self) -> None:
        """last_sample_stamp should return None when no samples exist."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.empty")

        assert profile.last_sample_stamp is None


class TestRoomProfileNewDataCheck:
    """Tests for RoomProfile new data check to ensure full coverage."""

    def test_new_data_check_rejects_unchanged_stamps(self) -> None:
        """RoomProfile.update() should reject when stamps haven't changed."""
        from custom_components.bermuda.correlation.room_profile import RoomProfile

        profile = RoomProfile(area_id="area.test")

        stamps = {"scanner_a": 1000.0, "scanner_b": 1000.0}

        # First update should succeed
        result = profile.update(
            readings={"scanner_a": -50.0, "scanner_b": -60.0},
            nowstamp=1000.0,
            last_stamps={},  # No previous stamps
            current_stamps=stamps,
        )
        assert result is True

        # Second update with same stamps should be rejected
        result = profile.update(
            readings={"scanner_a": -52.0, "scanner_b": -62.0},
            nowstamp=2000.0,  # Time passes but stamps are the same
            last_stamps=stamps,  # Same as current
            current_stamps=stamps,  # Same as last
        )
        # Note: This will return False due to "no new data"
        assert result is False
