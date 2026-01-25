"""
Tests for the Clamped Bayesian Fusion System (Controlled Evolution).

These tests verify that:
1. Auto and button learning use separate Kalman filters
2. Button training sets the "anchor" (user truth)
3. Auto-learning can "polish" the anchor but its influence is CLAMPED to max 30%
4. User always retains at least 70% authority over the final estimate
5. The system allows intelligent refinement while preventing anchor drift
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.room_profile import RoomProfile
from custom_components.bermuda.correlation.scanner_absolute import (
    MAX_AUTO_RATIO,
    ScannerAbsoluteRssi,
)
from custom_components.bermuda.correlation.scanner_pair import (
    MAX_AUTO_RATIO as PAIR_MAX_AUTO_RATIO,
    MIN_VARIANCE,
    ScannerPairCorrelation,
)


class TestScannerPairCorrelationDualFilter:
    """Tests for dual-filter Clamped Fusion in ScannerPairCorrelation."""

    def test_auto_only_returns_auto_estimate(self) -> None:
        """With only auto samples, estimate equals auto filter estimate."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(50):
            corr.update(10.0)

        # Estimate should be close to 10.0
        assert abs(corr.expected_delta - 10.0) < 1.0
        assert corr.auto_sample_count == 50
        assert corr.button_sample_count == 0

    def test_button_only_returns_button_estimate(self) -> None:
        """With only button samples (no auto), estimate equals button value.

        FIX: BUG 11 changed update_button() to use update() which accumulates samples.
        After 20 samples (like real training), the estimate converges to the button value.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Button training without any auto data (simulate 20 samples)
        for _ in range(20):
            corr.update_button(-15.0)

        # Without auto data, estimate should be very close to the button value
        assert abs(corr.expected_delta - (-15.0)) < 0.5
        assert corr.auto_sample_count == 0
        # Button accumulates samples (not 500 anymore)
        assert corr.button_sample_count == 20

    def test_button_dominates_with_clamped_auto_influence(self) -> None:
        """Button training dominates, but auto can have up to 30% influence.

        With Clamped Bayesian Fusion:
        - Button sets the anchor (at least 70% weight)
        - Auto can refine with max 30% influence
        - Result is between button and auto, but closer to button
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add many auto samples at 0.0 (converged, low variance)
        for _ in range(100):
            corr.update(0.0)

        # Button training at 30.0
        corr.update_button(30.0)

        # With clamped fusion, estimate should be:
        # - At least 70% button (30.0) = 21.0
        # - At most 30% auto (0.0) = 0.0
        # - Worst case: 0.7 * 30 + 0.3 * 0 = 21.0
        estimate = corr.expected_delta
        assert estimate >= 21.0, f"Button should have at least 70% weight, got estimate {estimate}"
        assert estimate <= 30.0, f"Estimate should not exceed button value, got {estimate}"

    def test_auto_continues_learning_indefinitely(self) -> None:
        """Auto learning should never stop - adapts to environment changes."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Learn initial value
        for _ in range(100):
            corr.update(0.0)

        estimate_before = corr.expected_delta

        # Simulate environment change (new furniture) - continue learning
        for _ in range(200):
            corr.update(10.0)

        estimate_after = corr.expected_delta

        # Estimate should have moved toward new value
        assert (
            estimate_after > estimate_before + 5.0
        ), f"Auto should adapt to changes: before={estimate_before}, after={estimate_after}"

    def test_button_sets_anchor_auto_refines(self) -> None:
        """Button sets the anchor, auto can refine within limits."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Auto learns one value
        for _ in range(100):
            corr.update(5.0)

        estimate_before_button = corr.expected_delta

        # User sets button anchor
        corr.update_button(-10.0)

        estimate_after_button = corr.expected_delta

        # Estimate should be much closer to button value (-10.0) than before
        distance_to_button_before = abs(estimate_before_button - (-10.0))
        distance_to_button_after = abs(estimate_after_button - (-10.0))

        assert distance_to_button_after < distance_to_button_before * 0.5, (
            f"Button should pull estimate strongly toward -10: "
            f"before={estimate_before_button}, after={estimate_after_button}"
        )

    def test_sample_count_reflects_button_accumulation(self) -> None:
        """Button training accumulates samples using update() (not reset_to_value).

        FIX: BUG 11 changed update_button() from reset_to_value() to update().
        Now each call adds 1 sample, not 500. This allows all training samples
        to contribute to the average instead of only the last one counting.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(30):
            corr.update(10.0)

        # Simulate button training (20 samples like real training)
        for _ in range(20):
            corr.update_button(20.0)

        # Auto samples are counted normally
        assert corr.auto_sample_count == 30
        # Button now accumulates samples (20 samples from button training)
        assert corr.button_sample_count == 20
        # Total reflects both
        assert corr.sample_count == 50


class TestScannerAbsoluteRssiDualFilter:
    """Tests for dual-filter Clamped Fusion in ScannerAbsoluteRssi."""

    def test_auto_only_returns_auto_estimate(self) -> None:
        """With only auto samples, estimate equals auto filter estimate."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        for _ in range(50):
            profile.update(-60.0)

        assert abs(profile.expected_rssi - (-60.0)) < 2.0
        assert profile.auto_sample_count == 50
        assert profile.button_sample_count == 0

    def test_button_only_returns_button_estimate(self) -> None:
        """With only button samples (no auto), estimate equals button value.

        FIX: BUG 11 changed update_button() to use update() which accumulates samples.
        After 20 samples (like real training), the estimate converges to the button value.
        """
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        # Button training without any auto data (simulate 20 samples)
        for _ in range(20):
            profile.update_button(-75.0)

        # Without auto data, estimate should be very close to the button value
        assert abs(profile.expected_rssi - (-75.0)) < 0.5
        # Button accumulates samples (not 500 anymore)
        assert profile.button_sample_count == 20


class TestRoomProfileWeightedLearning:
    """Tests for weighted learning in RoomProfile."""

    def test_update_uses_auto_filter(self) -> None:
        """update() should use the auto Kalman filter."""
        profile = RoomProfile(area_id="test_area")

        readings = {"scanner_a": -50.0, "scanner_b": -60.0}
        profile.update(readings)

        for pair in profile._scanner_pairs.values():
            assert pair.auto_sample_count == 1
            assert pair.button_sample_count == 0

    def test_update_button_uses_button_filter(self) -> None:
        """update_button() accumulates samples in button Kalman filter.

        FIX: BUG 11 changed update_button() to use update() which accumulates samples.
        """
        profile = RoomProfile(area_id="test_area")

        readings = {"scanner_a": -50.0, "scanner_b": -60.0}
        # Simulate 20 button training samples
        for _ in range(20):
            profile.update_button(readings)

        for pair in profile._scanner_pairs.values():
            assert pair.auto_sample_count == 0
            # Button now accumulates samples (20 from training)
            assert pair.button_sample_count == 20


class TestAreaProfileWeightedLearning:
    """Tests for weighted learning in AreaProfile."""

    def test_update_uses_auto_filter(self) -> None:
        """update() should use the auto Kalman filter."""
        profile = AreaProfile(area_id="test_area")

        profile.update(
            primary_rssi=-50.0,
            other_readings={"scanner_b": -60.0},
            primary_scanner_addr="scanner_a",
        )

        for corr in profile._correlations.values():
            assert corr.auto_sample_count == 1
            assert corr.button_sample_count == 0

        for abs_profile in profile._absolute_profiles.values():
            assert abs_profile.auto_sample_count == 1
            assert abs_profile.button_sample_count == 0

    def test_update_button_uses_button_filter(self) -> None:
        """update_button() accumulates samples in button Kalman filters.

        FIX: BUG 11 changed update_button() to use update() which accumulates samples.
        """
        profile = AreaProfile(area_id="test_area")

        # Simulate 20 button training samples
        for _ in range(20):
            profile.update_button(
                primary_rssi=-50.0,
                other_readings={"scanner_b": -60.0},
                primary_scanner_addr="scanner_a",
            )

        for corr in profile._correlations.values():
            assert corr.auto_sample_count == 0
            # Button now accumulates samples (20 from training)
            assert corr.button_sample_count == 20

        for abs_profile in profile._absolute_profiles.values():
            assert abs_profile.auto_sample_count == 0
            # Button now accumulates samples (20 from training)
            assert abs_profile.button_sample_count == 20


class TestSerializationDualFilter:
    """Tests for persistence of dual-filter state."""

    def test_scanner_pair_roundtrip_preserves_both_filters(self) -> None:
        """Serialization should preserve both Kalman filter states."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add samples to both filters
        for _ in range(30):
            corr.update(5.0)
        corr.update_button(-10.0)

        # Serialize and deserialize
        data = corr.to_dict()
        restored = ScannerPairCorrelation.from_dict(data)

        # Both filter states should be preserved
        assert restored.auto_sample_count == corr.auto_sample_count
        assert restored.button_sample_count == corr.button_sample_count
        assert abs(restored.expected_delta - corr.expected_delta) < 0.01

    def test_scanner_pair_backward_compatibility(self) -> None:
        """Old data format should migrate to auto filter only."""
        old_data = {
            "scanner": "test_scanner",
            "estimate": -10.0,
            "variance": 4.0,
            "samples": 50,
        }

        restored = ScannerPairCorrelation.from_dict(old_data)

        # All samples should be in auto filter
        assert restored.auto_sample_count == 50
        assert restored.button_sample_count == 0
        assert abs(restored.expected_delta - (-10.0)) < 0.01

    def test_scanner_absolute_roundtrip(self) -> None:
        """ScannerAbsoluteRssi should preserve both filter states."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        for _ in range(25):
            profile.update(-60.0)
        profile.update_button(-70.0)

        data = profile.to_dict()
        restored = ScannerAbsoluteRssi.from_dict(data)

        assert restored.auto_sample_count == profile.auto_sample_count
        assert restored.button_sample_count == profile.button_sample_count


class TestClampedBayesianFusion:
    """Tests for the Clamped Bayesian Fusion algorithm."""

    def test_max_auto_ratio_constant_is_thirty_percent(self) -> None:
        """MAX_AUTO_RATIO should be 0.30 (30%)."""
        assert MAX_AUTO_RATIO == 0.30
        assert PAIR_MAX_AUTO_RATIO == 0.30

    def test_button_dominates_even_with_massive_auto_data(self) -> None:
        """Button anchor should dominate even with millions of auto samples.

        This tests the core guarantee: auto influence is CLAMPED to 30%.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Simulate massive auto learning (would have extremely low variance)
        for _ in range(1000):
            corr.update(0.0)

        auto_estimate_before = corr.expected_delta
        assert abs(auto_estimate_before - 0.0) < 0.1, "Auto should converge to 0.0"

        # Button training at different value
        corr.update_button(100.0)

        estimate = corr.expected_delta

        # With clamping, auto gets max 30%:
        # Worst case: 0.7 * 100 + 0.3 * 0 = 70.0
        # Button anchor (100.0) should dominate
        assert (
            estimate >= 70.0
        ), f"Button should have at least 70% weight even with massive auto data. Expected >= 70.0, got {estimate}"

    def test_auto_influence_is_clamped_not_zero(self) -> None:
        """Auto should have SOME influence (up to 30%), not be completely ignored.

        This distinguishes Clamped Fusion from pure Hierarchical Priority.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Auto learns 0.0 with many samples (low variance)
        for _ in range(100):
            corr.update(0.0)

        # Button at 30.0
        corr.update_button(30.0)

        estimate = corr.expected_delta

        # If auto were completely ignored, estimate would be exactly 30.0
        # With clamped fusion, auto should pull it slightly toward 0.0
        # Expected: somewhere between 21.0 (70% button) and 30.0 (100% button)
        assert estimate < 30.0, f"Auto should have some influence (estimate should be < 30.0). Got {estimate}"
        assert estimate >= 21.0, f"Button should still dominate (estimate should be >= 21.0). Got {estimate}"

    def test_fused_variance_is_reduced(self) -> None:
        """Fused variance should be lower than either individual variance.

        This is the Bayesian benefit: combining information reduces uncertainty.

        FIX: BUG 11 changed update_button() to accumulate samples. With 20+ samples,
        the button filter builds enough confidence to contribute to variance reduction.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Get auto variance
        for _ in range(50):
            corr.update(10.0)
        auto_only_variance = corr.variance

        # Add button with 20 samples - fused variance should be lower
        for _ in range(20):
            corr.update_button(10.0)
        fused_variance = corr.variance

        assert (
            fused_variance < auto_only_variance
        ), f"Fused variance ({fused_variance}) should be < auto-only variance ({auto_only_variance})"

    def test_min_variance_prevents_division_by_zero(self) -> None:
        """MIN_VARIANCE constant prevents division by zero in edge cases."""
        assert MIN_VARIANCE > 0, "MIN_VARIANCE must be positive"
        assert MIN_VARIANCE < 1.0, "MIN_VARIANCE should be small"


class TestControlledEvolution:
    """Tests for the 'polishing' behavior of Clamped Fusion."""

    def test_auto_can_polish_button_anchor_slightly(self) -> None:
        """Auto-learning can refine ('polish') the button anchor within limits.

        The user sets the anchor, auto can adjust it slightly based on
        environmental observations, but cannot move it dramatically.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Button sets anchor at 50.0
        corr.update_button(50.0)
        initial_estimate = corr.expected_delta

        # Auto suggests the value should be 45.0 (slight refinement)
        for _ in range(100):
            corr.update(45.0)

        polished_estimate = corr.expected_delta

        # The estimate should move slightly toward auto (45.0) but stay close to button (50.0)
        assert (
            polished_estimate < initial_estimate
        ), f"Auto should polish anchor downward. Initial: {initial_estimate}, Polished: {polished_estimate}"
        assert (
            polished_estimate > 45.0
        ), f"Anchor should not move all the way to auto value. Polished: {polished_estimate}"

    def test_reset_training_clears_all_data(self) -> None:
        """reset_training() should clear BOTH button AND auto filters for clean slate.

        Why reset both? The auto-learned data may be "poisoned" by incorrect
        room selection. After reset, new button training establishes correct
        patterns, and auto-learning starts fresh in the correct context.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Auto learns 10.0
        for _ in range(50):
            corr.update(10.0)

        # Button sets anchor at 50.0
        corr.update_button(50.0)

        # Estimate is dominated by button
        assert corr.expected_delta > 30.0

        # Reset training - should clear BOTH filters
        corr.reset_training()

        # Both filters should be cleared (clean slate)
        assert corr.expected_delta == 0.0, f"After reset, both filters should be cleared. Got {corr.expected_delta}"
        assert not corr.has_button_training, "Button filter should be cleared"
        assert corr.auto_sample_count == 0, "Auto filter should be cleared"
        assert corr.button_sample_count == 0, "Button sample count should be 0"

        # New training can now start fresh
        corr.update_button(30.0)
        assert corr.has_button_training, "New button training should work"
        assert abs(corr.expected_delta - 30.0) < 1.0, "New training should set correct value"


class TestHyperPrecisionParadoxFix:
    """Tests for the Hyper-Precision Paradox fix.

    The Bug: Using variance=0.1 (σ≈0.3dB) for button training caused normal
    BLE fluctuations (2-3dB) to be rejected as "20 sigma events" by Z-score
    matching, even though the room was correct.

    The Fix: Button training now uses variance=2.0 (σ≈1.4dB), which:
    - Still dominates in fusion (2.0 << auto variance ~16-25)
    - But allows realistic BLE fluctuations in Z-score matching
    """

    def test_button_variance_allows_realistic_ble_fluctuations(self) -> None:
        """Z-score for normal BLE fluctuation should be reasonable, not absurd.

        Typical BLE fluctuation: 2-5 dB
        With variance=0.1 (OLD): 2dB deviation = 2/0.316 ≈ 6.3 sigma (REJECT!)
        With variance=2.0 (NEW): 2dB deviation = 2/1.41 ≈ 1.4 sigma (OK!)
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # User trains room with button at -80dB delta
        corr.update_button(-80.0)

        # Verify the fused variance is realistic (variance=2.0 from button)
        # Note: Without auto data, fused variance equals button variance
        assert corr.variance >= 1.0, f"Button variance should be >= 1.0 for realistic matching. Got {corr.variance}"

        # Normal BLE fluctuation: signal drifts to -82dB (2dB change)
        observed = -82.0
        z_score = corr.z_score(observed)

        # Z-score should be reasonable (< 3 sigma), not absurd (> 6 sigma)
        assert z_score < 3.0, f"Normal 2dB BLE fluctuation should have z-score < 3.0. Got {z_score}"

    def test_button_variance_still_provides_high_confidence(self) -> None:
        """Button variance should be comparable to auto variance after training.

        FIX: BUG 11 changed update_button() to accumulate samples. With 20+ samples,
        the button filter builds enough confidence to have low variance.

        With Clamped Fusion, button dominance is guaranteed by the MAX_AUTO_RATIO cap
        (30%), not just by variance differences.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Let auto learn with typical noise
        for _ in range(100):
            corr.update(10.0)

        auto_variance = corr._kalman_auto.variance

        # Button training with 20 samples (like real training)
        for _ in range(20):
            corr.update_button(10.0)
        button_variance = corr._kalman_button.variance

        # After 20 samples, button variance should be reasonably low
        # (not necessarily lower than auto, but not extremely high either)
        assert button_variance < 10.0, f"Button variance ({button_variance}) should be reasonably low after 20 samples"

        # The key behavior: estimate should still be dominated by button due to clamping
        # Even if variances are similar, clamping limits auto to 30%
        assert (
            abs(corr.expected_delta - 10.0) < 1.0
        ), f"Button should still dominate estimate. Got {corr.expected_delta}, expected ~10.0"

    def test_absolute_rssi_z_score_accepts_normal_fluctuations(self) -> None:
        """ScannerAbsoluteRssi should also accept normal BLE fluctuations."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        # User trains at -70dB
        profile.update_button(-70.0)

        # Normal fluctuation: -73dB (3dB change)
        observed = -73.0
        z_score = profile.z_score(observed)

        # Z-score should be reasonable (< 3 sigma)
        assert z_score < 3.0, f"Normal 3dB BLE fluctuation should have z-score < 3.0. Got {z_score}"
