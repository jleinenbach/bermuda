"""
Tests for the weighted learning system (Two-Pool Kalman Fusion).

These tests verify that:
1. Auto and button learning use separate Kalman filters
2. Button training has 2x weight in the fused estimate
3. Both pools continue learning indefinitely (no hard caps)
4. The system adapts to environment changes while preserving manual corrections
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.correlation.room_profile import RoomProfile
from custom_components.bermuda.correlation.scanner_absolute import ScannerAbsoluteRssi
from custom_components.bermuda.correlation.scanner_pair import (
    BUTTON_WEIGHT_MULTIPLIER,
    ScannerPairCorrelation,
)


class TestWeightedLearningConstants:
    """Tests for weighted learning configuration constants."""

    def test_button_weight_multiplier(self) -> None:
        """Button samples should count as 2x for weighted fusion."""
        assert BUTTON_WEIGHT_MULTIPLIER == 2


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

    def test_weighted_fusion_button_dominates(self) -> None:
        """Button samples should have 2x weight in the fused estimate.

        Example: 100 auto samples at 0.0, 100 button samples at 30.0
        - auto_weight = 100
        - button_weight = 100 * 2 = 200
        - Expected fused estimate = (0 * 100 + 30 * 200) / 300 = 20.0
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Add auto samples at 0.0
        for _ in range(100):
            corr.update(0.0)

        # Add button samples at 30.0
        for _ in range(100):
            corr.update_button(30.0)

        # With 2x button weight: (0*100 + 30*200) / 300 = 20.0
        # Note: Kalman filters converge asymptotically, so allow some tolerance
        assert corr.expected_delta > 15.0, f"Button should dominate, got {corr.expected_delta}"
        assert corr.expected_delta < 25.0, f"Auto should still have influence, got {corr.expected_delta}"

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

    def test_sample_count_weighted(self) -> None:
        """Total sample count should use button weight multiplier."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        for _ in range(30):
            corr.update(10.0)

        for _ in range(10):
            corr.update_button(10.0)

        # Total = 30 auto + 10 button * 2 = 50
        assert corr.sample_count == 30 + 10 * BUTTON_WEIGHT_MULTIPLIER


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


class TestWeightedFusionMath:
    """Tests for the mathematical correctness of weighted fusion."""

    def test_equal_samples_button_has_two_thirds_influence(self) -> None:
        """With equal sample counts, button should have 2/3 influence.

        100 auto at 0, 100 button at 30:
        - auto_weight = 100
        - button_weight = 100 * 2 = 200
        - fused = (0*100 + 30*200) / 300 = 20
        """
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Use enough samples for filters to converge
        for _ in range(200):
            corr.update(0.0)

        for _ in range(200):
            corr.update_button(30.0)

        # Button has 2/3 influence: estimate should be closer to 30 than to 0
        # With perfect weighting: (0 * 200 + 30 * 400) / 600 = 20
        estimate = corr.expected_delta
        assert estimate > 15.0, f"Expected > 15, got {estimate}"
        assert estimate < 25.0, f"Expected < 25, got {estimate}"

    def test_more_auto_samples_reduces_button_influence(self) -> None:
        """More auto samples should reduce button's relative influence."""
        corr = ScannerPairCorrelation(scanner_address="test_scanner")

        # Heavy auto learning
        for _ in range(1000):
            corr.update(0.0)

        # Light button training
        for _ in range(50):
            corr.update_button(30.0)

        # 1000 auto + 50*2 button = 1000 + 100 = 1100 total weight
        # Button influence: 100/1100 = 9%
        # Expected: closer to 0 than to 30
        estimate = corr.expected_delta
        assert estimate < 10.0, f"Heavy auto should dominate, got {estimate}"
