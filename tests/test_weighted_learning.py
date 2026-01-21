"""
Tests for the Hierarchical Priority System (Frozen Layers & Shadow Learning).

These tests verify that:
1. Auto and button learning use separate Kalman filters
2. Button training ALWAYS overrides auto-learning (hierarchical priority, not fusion)
3. Auto learning continues in "shadow mode" but doesn't affect output when button data exists
4. The system preserves user calibration indefinitely against environment drift
5. User training creates a "frozen" state that cannot be corrupted by auto-learning
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
        """With only button samples, estimate equals button filter estimate (frozen state)."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Button training uses reset_to_value() which sets sample_count=500 for frozen state
        corr.update_button(-15.0)

        # Estimate should be exactly -15.0 (frozen value)
        assert abs(corr.expected_delta - (-15.0)) < 0.01
        assert corr.auto_sample_count == 0
        # Sample count is 500 due to reset_to_value() creating frozen state
        assert corr.button_sample_count == 500

    def test_button_training_overrides_converged_auto(self) -> None:
        """Button training inflates auto variance to allow user corrections to take effect.

        FIX for Fehler 2: When auto-filter is converged (low variance from thousands of
        samples), button training would previously be ineffective because inverse-variance
        weighting would favor the highly-confident auto estimate.

        Now, button training inflates the auto variance, giving button samples more weight.
        This ensures manual user corrections can override automatic learning.
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add many auto samples at 0.0 (converged, variance ~2.6)
        for _ in range(100):
            corr.update(0.0)

        # Add just 2 button samples at 30.0
        # FIX: The first button call inflates auto variance from ~2.6 to 15.0
        for _ in range(2):
            corr.update_button(30.0)

        # After button training, auto variance should be inflated (higher than before)
        auto_var = corr._kalman_auto.variance
        button_var = corr._kalman_button.variance
        assert auto_var > button_var, (
            f"Auto variance should be inflated after button training: auto={auto_var:.2f}, button={button_var:.2f}"
        )

        # With inflated auto variance, button training should have more influence
        # Estimate should be closer to 30 (button) than to 0 (auto)
        estimate = corr.expected_delta
        assert estimate > 15.0, f"Button training should have influence, got {estimate}"

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

    def test_sample_count_reflects_frozen_state(self) -> None:
        """Button training creates frozen state with high sample count for stability."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(30):
            corr.update(10.0)

        # Single button training creates frozen state
        corr.update_button(20.0)

        # Auto samples are counted normally
        assert corr.auto_sample_count == 30
        # Button uses reset_to_value() with sample_count=500 for frozen state
        assert corr.button_sample_count == 500
        # Total reflects frozen state
        assert corr.sample_count == 530


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
        """With only button samples, estimate equals button filter estimate (frozen state)."""
        profile = ScannerAbsoluteRssi(scanner_address="test_scanner")

        # Single button training creates frozen state
        profile.update_button(-75.0)

        # Estimate should be exactly -75.0 (frozen value)
        assert abs(profile.expected_rssi - (-75.0)) < 0.01
        # Sample count is 500 due to reset_to_value() creating frozen state
        assert profile.button_sample_count == 500


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
        """update_button() creates frozen state in button Kalman filter."""
        profile = RoomProfile(area_id="test_area")

        readings = {"scanner_a": -50.0, "scanner_b": -60.0}
        profile.update_button(readings)

        for pair in profile._scanner_pairs.values():
            assert pair.auto_sample_count == 0
            # Button training creates frozen state with sample_count=500
            assert pair.button_sample_count == 500


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
        """update_button() creates frozen state in button Kalman filters."""
        profile = AreaProfile(area_id="test_area")

        profile.update_button(
            primary_rssi=-50.0,
            other_readings={"scanner_b": -60.0},
            primary_scanner_addr="scanner_a",
        )

        for corr in profile._correlations.values():
            assert corr.auto_sample_count == 0
            # Button training creates frozen state with sample_count=500
            assert corr.button_sample_count == 500

        for abs_profile in profile._absolute_profiles.values():
            assert abs_profile.auto_sample_count == 0
            # Button training creates frozen state with sample_count=500
            assert abs_profile.button_sample_count == 500


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

    def test_button_training_dominates_after_convergence(self) -> None:
        """Button training dominates when auto was previously converged.

        FIX for Fehler 2: Even with many button samples, button training should
        dominate over auto-learning because auto variance is inflated when
        button training starts.

        The estimate should be closer to the button value (30.0) than the auto value (0.0).
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Train auto with many samples (converged, low variance)
        for _ in range(100):
            corr.update(0.0)

        # Train button - auto variance gets inflated on first call
        for _ in range(100):
            corr.update_button(30.0)

        # After button training, button filter is converged with low variance,
        # while auto filter has inflated variance. Button should dominate.
        estimate = corr.expected_delta
        # Button (30.0) should dominate over auto (0.0)
        assert estimate > 20.0, f"Button should dominate with estimate > 20, got {estimate}"

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

    def test_variance_uses_hierarchical_priority(self) -> None:
        """Variance uses hierarchical priority: button variance when button exists."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Train auto first
        for _ in range(50):
            corr.update(10.0)

        auto_var_before = corr.variance
        assert auto_var_before > 2.0, "Auto should have converged variance"

        # Train button - this should take priority
        corr.update_button(20.0)

        # After button training, variance should be button's frozen variance (0.01)
        actual_variance = corr.variance
        button_variance = corr._kalman_button.variance

        assert actual_variance == button_variance, (
            f"Variance should be button variance when button exists: actual={actual_variance}, button={button_variance}"
        )
        # Frozen state has very low variance
        assert actual_variance < 0.1, f"Button variance should be ~0.01, got {actual_variance}"

    def test_min_variance_prevents_division_by_zero(self) -> None:
        """MIN_VARIANCE constant prevents division by zero in edge cases."""
        assert MIN_VARIANCE > 0, "MIN_VARIANCE must be positive"
        assert MIN_VARIANCE < 1.0, "MIN_VARIANCE should be small"
