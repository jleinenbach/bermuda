"""
Tests for the weighted learning system (Two-Pool Kalman Fusion with Inverse-Variance Weighting).

These tests verify that:
1. Auto and button learning use separate Kalman filters
2. Inverse-variance weighting gives more weight to lower-variance estimates
3. Both pools continue learning indefinitely (no hard caps)
4. The system adapts to environment changes while preserving manual corrections
5. Consistent button training naturally dominates over noisy auto learning
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.room_profile import RoomProfile
from custom_components.bermuda.correlation.scanner_absolute import ScannerAbsoluteRssi
from custom_components.bermuda.correlation.scanner_pair import (
    MIN_VARIANCE,
    ScannerPairCorrelation,
)


class TestScannerPairCorrelationDualFilter:
    """Tests for dual-filter weighted learning in ScannerPairCorrelation."""

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
        """With only button samples, estimate equals button filter estimate."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(30):
            corr.update_button(-15.0)

        # Estimate should be close to -15.0
        assert abs(corr.expected_delta - (-15.0)) < 1.0
        assert corr.auto_sample_count == 0
        assert corr.button_sample_count == 30

    def test_inverse_variance_weighting_converged_vs_unconverged(self) -> None:
        """Converged filter (low variance) gets more weight than unconverged filter.

        With inverse-variance weighting:
        - weight = 1 / variance
        - Kalman variance converges quickly (~20 samples to steady state ~2.6)
        - Unconverged filter has much higher variance

        This test uses extreme difference: converged (100 samples) vs just started (2 samples)
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add many auto samples at 0.0 (converged, variance ~2.6)
        for _ in range(100):
            corr.update(0.0)

        # Add just 2 button samples at 30.0 (unconverged, variance ~8.1)
        for _ in range(2):
            corr.update_button(30.0)

        # Auto filter is converged → lower variance → more weight
        auto_var = corr._kalman_auto.variance
        button_var = corr._kalman_button.variance
        assert auto_var < button_var, (
            f"Auto should have lower variance (converged): auto={auto_var:.2f}, button={button_var:.2f}"
        )

        # With lower variance, auto should dominate the fused estimate
        # Estimate should be closer to 0 (auto) than to 30 (button)
        estimate = corr.expected_delta
        assert estimate < 15.0, f"Converged auto should dominate, got {estimate}"

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
        assert estimate_after > estimate_before + 5.0, (
            f"Auto should adapt to changes: before={estimate_before}, after={estimate_after}"
        )

    def test_button_training_influences_estimate(self) -> None:
        """Button training should shift the fused estimate toward button value."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Auto learns wrong value
        for _ in range(100):
            corr.update(5.0)

        estimate_before_button = corr.expected_delta

        # User corrects with button
        for _ in range(50):
            corr.update_button(-10.0)

        estimate_after_button = corr.expected_delta

        # Estimate should move toward button value
        distance_to_button_before = abs(estimate_before_button - (-10.0))
        distance_to_button_after = abs(estimate_after_button - (-10.0))

        assert distance_to_button_after < distance_to_button_before, (
            f"Button should pull estimate toward -10: before={estimate_before_button}, after={estimate_after_button}"
        )

    def test_sample_count_is_sum_of_both_filters(self) -> None:
        """Total sample count should be simple sum (used for maturity checks)."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(30):
            corr.update(10.0)

        for _ in range(10):
            corr.update_button(10.0)

        # Total = 30 auto + 10 button = 40 (simple sum for maturity)
        assert corr.sample_count == 40
        assert corr.auto_sample_count == 30
        assert corr.button_sample_count == 10


class TestScannerAbsoluteRssiDualFilter:
    """Tests for dual-filter weighted learning in ScannerAbsoluteRssi."""

    def test_auto_only_returns_auto_estimate(self) -> None:
        """With only auto samples, estimate equals auto filter estimate."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        for _ in range(50):
            profile.update(-60.0)

        assert abs(profile.expected_rssi - (-60.0)) < 2.0
        assert profile.auto_sample_count == 50
        assert profile.button_sample_count == 0

    def test_button_only_returns_button_estimate(self) -> None:
        """With only button samples, estimate equals button filter estimate."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        for _ in range(30):
            profile.update_button(-75.0)

        assert abs(profile.expected_rssi - (-75.0)) < 2.0
        assert profile.button_sample_count == 30


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
        """update_button() should use the button Kalman filter."""
        profile = RoomProfile(area_id="test_area")

        readings = {"scanner_a": -50.0, "scanner_b": -60.0}
        profile.update_button(readings)

        for pair in profile._scanner_pairs.values():
            assert pair.auto_sample_count == 0
            assert pair.button_sample_count == 1


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
        """update_button() should use the button Kalman filter."""
        profile = AreaProfile(area_id="test_area")

        profile.update_button(
            primary_rssi=-50.0,
            other_readings={"scanner_b": -60.0},
            primary_scanner_addr="scanner_a",
        )

        for corr in profile._correlations.values():
            assert corr.auto_sample_count == 0
            assert corr.button_sample_count == 1

        for abs_profile in profile._absolute_profiles.values():
            assert abs_profile.auto_sample_count == 0
            assert abs_profile.button_sample_count == 1


class TestSerializationDualFilter:
    """Tests for persistence of dual-filter state."""

    def test_scanner_pair_roundtrip_preserves_both_filters(self) -> None:
        """Serialization should preserve both Kalman filter states."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add samples to both filters
        for _ in range(30):
            corr.update(5.0)
        for _ in range(20):
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
        for _ in range(15):
            profile.update_button(-70.0)

        data = profile.to_dict()
        restored = ScannerAbsoluteRssi.from_dict(data)

        assert restored.auto_sample_count == profile.auto_sample_count
        assert restored.button_sample_count == profile.button_sample_count


class TestInverseVarianceFusionMath:
    """Tests for the mathematical correctness of inverse-variance fusion."""

    def test_equal_variance_equal_weight(self) -> None:
        """With equal variance, both filters contribute equally.

        Inverse-variance weighting: weight = 1 / variance
        If both have same variance → equal weights → midpoint estimate
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Train both with consistent values (similar variance)
        for _ in range(100):
            corr.update(0.0)

        for _ in range(100):
            corr.update_button(30.0)

        # Both trained with consistent values → similar variance → ~equal weight
        # Estimate should be roughly the midpoint (15.0)
        estimate = corr.expected_delta
        assert 10.0 < estimate < 20.0, f"Expected ~15 (midpoint), got {estimate}"

    def test_lower_variance_gets_more_weight(self) -> None:
        """Converged filter (lower variance) gets more weight than unconverged."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Train auto with few samples (high variance ~5.6)
        for _ in range(3):
            corr.update(0.0)

        # Train button with many samples (converged, low variance ~2.6)
        for _ in range(100):
            corr.update_button(30.0)

        # Button is converged → lower variance → more weight → estimate closer to 30
        estimate = corr.expected_delta
        assert estimate > 20.0, f"Converged button should dominate, got {estimate}"

    def test_variance_fusion_formula(self) -> None:
        """Combined variance follows inverse-variance fusion: 1/(1/v1 + 1/v2)."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Train both filters
        for _ in range(50):
            corr.update(10.0)
        for _ in range(50):
            corr.update_button(20.0)

        auto_var = max(corr._kalman_auto.variance, MIN_VARIANCE)
        button_var = max(corr._kalman_button.variance, MIN_VARIANCE)

        # Expected combined variance
        expected_combined = 1.0 / (1.0 / auto_var + 1.0 / button_var)
        actual_combined = corr.variance

        assert abs(actual_combined - expected_combined) < 0.01, (
            f"Combined variance {actual_combined} != expected {expected_combined}"
        )

    def test_min_variance_prevents_division_by_zero(self) -> None:
        """MIN_VARIANCE constant prevents division by zero in edge cases."""
        assert MIN_VARIANCE > 0, "MIN_VARIANCE must be positive"
        assert MIN_VARIANCE < 1.0, "MIN_VARIANCE should be small"
