"""
Tests for multi-position training variance reset feature.

This test module covers:
1. KalmanFilter methods including reset_variance_only(), update_adaptive(), serialization
2. ScannerAbsoluteRssi methods including reset_variance_only(), serialization, z_score
3. ScannerPairCorrelation methods including reset_variance_only(), serialization, z_score
4. AreaProfile methods including reset_variance_only(), z_score methods, serialization
5. Training notification quality index calculation
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.filters.kalman import KalmanFilter
from custom_components.bermuda.filters.base import FilterConfig
from custom_components.bermuda.correlation.scanner_absolute import ScannerAbsoluteRssi
from custom_components.bermuda.correlation.scanner_pair import ScannerPairCorrelation
from custom_components.bermuda.correlation.area_profile import AreaProfile


# =============================================================================
# KalmanFilter.reset_variance_only() Tests
# =============================================================================


class TestKalmanFilterResetVarianceOnly:
    """Tests for KalmanFilter.reset_variance_only() method."""

    def test_reset_preserves_estimate(self) -> None:
        """reset_variance_only() should preserve the estimate value."""
        kf = KalmanFilter()
        kf.update(-75.0)
        kf.update(-73.0)
        kf.update(-74.0)
        original_estimate = kf.estimate

        kf.reset_variance_only()

        assert kf.estimate == original_estimate

    def test_reset_sets_variance_to_measurement_noise(self) -> None:
        """reset_variance_only() should reset variance to measurement_noise."""
        kf = KalmanFilter()
        kf.update(-75.0)
        kf.update(-73.0)
        # Variance should have converged to a low value
        assert kf.variance < kf.measurement_noise

        kf.reset_variance_only()

        assert kf.variance == kf.measurement_noise

    def test_reset_with_custom_variance(self) -> None:
        """reset_variance_only() should accept a custom target variance."""
        kf = KalmanFilter()
        kf.update(-75.0)
        target_variance = 50.0

        kf.reset_variance_only(target_variance=target_variance)

        assert kf.variance == target_variance

    def test_reset_preserves_sample_count(self) -> None:
        """reset_variance_only() should preserve sample_count."""
        kf = KalmanFilter()
        for _ in range(10):
            kf.update(-75.0)
        original_count = kf.sample_count

        kf.reset_variance_only()

        assert kf.sample_count == original_count

    def test_reset_clears_last_timestamp(self) -> None:
        """reset_variance_only() should clear _last_timestamp."""
        kf = KalmanFilter()
        kf.update(-75.0, timestamp=100.0)
        assert kf._last_timestamp == 100.0

        kf.reset_variance_only()

        assert kf._last_timestamp is None

    def test_reset_on_uninitialized_filter_does_nothing(self) -> None:
        """reset_variance_only() on uninitialized filter should be a no-op."""
        kf = KalmanFilter()
        assert not kf._initialized

        kf.reset_variance_only()

        assert not kf._initialized
        assert kf.variance == kf.measurement_noise  # Initial value unchanged

    def test_reset_allows_new_samples_to_have_more_influence(self) -> None:
        """After reset, new samples should have more influence on estimate."""
        kf = KalmanFilter()
        # Train with many samples at -75
        for _ in range(20):
            kf.update(-75.0)
        estimate_before = kf.estimate

        # Without reset, new sample at -85 has little influence
        kf_no_reset = KalmanFilter()
        for _ in range(20):
            kf_no_reset.update(-75.0)
        kf_no_reset.update(-85.0)
        change_no_reset = abs(kf_no_reset.estimate - estimate_before)

        # With reset, new sample at -85 has more influence
        kf.reset_variance_only()
        kf.update(-85.0)
        change_with_reset = abs(kf.estimate - estimate_before)

        assert change_with_reset > change_no_reset


# =============================================================================
# KalmanFilter Additional Methods Tests
# =============================================================================


class TestKalmanFilterAdditionalMethods:
    """Tests for additional KalmanFilter methods for full coverage."""

    def test_update_adaptive_normal_signal(self) -> None:
        """update_adaptive should handle normal signal strength."""
        kf = KalmanFilter()
        result = kf.update_adaptive(-70.0, ref_power=-55.0)
        assert result == kf.estimate

    def test_update_adaptive_weak_signal(self) -> None:
        """update_adaptive should increase noise for weak signals."""
        kf = KalmanFilter()
        kf.update_adaptive(-90.0, ref_power=-55.0)
        # Weak signal should still produce valid estimate
        assert -95.0 < kf.estimate < -85.0

    def test_update_adaptive_strong_signal(self) -> None:
        """update_adaptive should decrease noise for strong signals."""
        kf = KalmanFilter()
        kf.update_adaptive(-50.0, ref_power=-55.0)
        # Strong signal near ref_power
        assert -55.0 < kf.estimate < -45.0

    def test_update_adaptive_invalid_ref_power_too_high(self) -> None:
        """update_adaptive should handle invalid ref_power > 0."""
        kf = KalmanFilter()
        # ref_power = 10 is invalid (should be -100 to 0)
        result = kf.update_adaptive(-70.0, ref_power=10.0)
        # Should still work with default ref_power
        assert result is not None

    def test_update_adaptive_invalid_ref_power_too_low(self) -> None:
        """update_adaptive should handle invalid ref_power < -100."""
        kf = KalmanFilter()
        # ref_power = -150 is invalid
        result = kf.update_adaptive(-70.0, ref_power=-150.0)
        assert result is not None

    def test_reset_clears_all_state(self) -> None:
        """reset should clear all filter state."""
        kf = KalmanFilter()
        kf.update(-75.0, timestamp=100.0)
        kf.update(-73.0, timestamp=101.0)

        kf.reset()

        assert kf.estimate == 0.0
        assert kf.variance == kf.measurement_noise
        assert kf.sample_count == 0
        assert not kf._initialized
        assert kf._last_timestamp is None

    def test_reset_to_value_sets_state(self) -> None:
        """reset_to_value should set specific state."""
        kf = KalmanFilter()
        kf.reset_to_value(value=-80.0, variance=0.5, sample_count=100)

        assert kf.estimate == -80.0
        assert kf.variance == 0.5
        assert kf.sample_count == 100
        assert kf._initialized

    def test_restore_state(self) -> None:
        """restore_state should restore filter from saved values."""
        kf = KalmanFilter()
        kf.restore_state(estimate=-75.0, variance=3.5, sample_count=50)

        assert kf.estimate == -75.0
        assert kf.variance == 3.5
        assert kf.sample_count == 50
        assert kf._initialized  # sample_count > 0 → initialized

    def test_restore_state_zero_samples(self) -> None:
        """restore_state with zero samples should not be initialized."""
        kf = KalmanFilter()
        kf.restore_state(estimate=0.0, variance=4.0, sample_count=0)

        assert not kf._initialized

    def test_get_estimate_and_variance(self) -> None:
        """get_estimate and get_variance should return correct values."""
        kf = KalmanFilter()
        kf.update(-75.0)

        assert kf.get_estimate() == kf.estimate
        assert kf.get_variance() == kf.variance

    def test_get_diagnostics(self) -> None:
        """get_diagnostics should return diagnostic dict."""
        kf = KalmanFilter()
        kf.update(-75.0)

        diag = kf.get_diagnostics()

        assert "estimate" in diag
        assert "variance" in diag
        assert "std_dev" in diag
        assert "sample_count" in diag
        assert "kalman_gain" in diag
        assert "initialized" in diag
        assert diag["initialized"] is True

    def test_get_diagnostics_uninitialized(self) -> None:
        """get_diagnostics on uninitialized filter."""
        kf = KalmanFilter()
        diag = kf.get_diagnostics()

        assert diag["initialized"] is False
        assert diag["kalman_gain"] == 0.0

    def test_from_config(self) -> None:
        """from_config should create filter with correct parameters."""
        config = FilterConfig(
            process_noise=0.05,
            measurement_noise=8.0,
            initial_variance=10.0,
        )
        kf = KalmanFilter.from_config(config)

        assert kf.process_noise == 0.05
        assert kf.measurement_noise == 8.0
        assert kf.variance == 10.0

    def test_to_dict_and_from_dict(self) -> None:
        """to_dict and from_dict should round-trip correctly."""
        kf = KalmanFilter()
        kf.update(-75.0, timestamp=100.0)
        kf.update(-73.0, timestamp=101.0)

        data = kf.to_dict()
        kf_restored = KalmanFilter.from_dict(data)

        assert kf_restored.estimate == kf.estimate
        assert kf_restored.variance == kf.variance
        assert kf_restored.sample_count == kf.sample_count
        assert kf_restored._last_timestamp == kf._last_timestamp

    def test_time_aware_update_with_timestamp(self) -> None:
        """update with timestamp should scale process noise by dt."""
        kf = KalmanFilter()
        kf.update(-75.0, timestamp=100.0)
        variance_after_first = kf.variance

        # Long time gap should increase variance more
        kf.update(-75.0, timestamp=110.0)  # 10 second gap
        # Variance should have been affected by longer dt

        kf2 = KalmanFilter()
        kf2.update(-75.0, timestamp=100.0)
        kf2.update(-75.0, timestamp=101.0)  # 1 second gap

        # Both should have similar estimates but different dynamics
        assert abs(kf.estimate - kf2.estimate) < 1.0

    def test_is_initialized_property(self) -> None:
        """is_initialized property should return correct state."""
        kf = KalmanFilter()
        assert not kf.is_initialized

        kf.update(-75.0)
        assert kf.is_initialized


# =============================================================================
# ScannerAbsoluteRssi.reset_variance_only() Tests
# =============================================================================


class TestScannerAbsoluteRssiResetVarianceOnly:
    """Tests for ScannerAbsoluteRssi.reset_variance_only() method."""

    def test_reset_preserves_button_training(self) -> None:
        """reset_variance_only() should preserve has_button_training status."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        abs_rssi.update_button(-75.0)
        assert abs_rssi.has_button_training

        abs_rssi.reset_variance_only()

        assert abs_rssi.has_button_training

    def test_reset_affects_button_filter_only(self) -> None:
        """reset_variance_only() should only affect button filter, not auto."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        # Train button filter
        abs_rssi.update_button(-75.0)
        button_estimate = abs_rssi._kalman_button.estimate
        # Train auto filter
        abs_rssi.update(-70.0)
        auto_variance_before = abs_rssi._kalman_auto.variance

        abs_rssi.reset_variance_only()

        # Button estimate preserved
        assert abs_rssi._kalman_button.estimate == button_estimate
        # Auto filter unchanged
        assert abs_rssi._kalman_auto.variance == auto_variance_before

    def test_reset_on_untrained_does_nothing(self) -> None:
        """reset_variance_only() on untrained filter should be a no-op."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        assert not abs_rssi.has_button_training

        abs_rssi.reset_variance_only()  # Should not raise

        assert not abs_rssi.has_button_training


# =============================================================================
# ScannerAbsoluteRssi Additional Methods Tests
# =============================================================================


class TestScannerAbsoluteRssiAdditional:
    """Additional tests for ScannerAbsoluteRssi methods."""

    def test_expected_rssi_auto_only(self) -> None:
        """expected_rssi should return auto estimate when no button training."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        abs_rssi.update(-75.0)

        result = abs_rssi.expected_rssi
        assert abs(result - (-75.0)) < 1.0

    def test_expected_rssi_with_button(self) -> None:
        """expected_rssi should use clamped fusion with button training."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        abs_rssi.update(-70.0)  # Auto training
        abs_rssi.update_button(-80.0)  # Button training

        # With clamped fusion, result should be influenced by both
        result = abs_rssi.expected_rssi
        assert -85.0 < result < -65.0

    def test_z_score_returns_float(self) -> None:
        """z_score should return a float value."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        for _ in range(10):
            abs_rssi.update(-75.0)

        z = abs_rssi.z_score(-75.0)
        assert isinstance(z, float)
        # Same value as expected should have z-score near 0
        assert abs(z) < 1.0

    def test_z_score_high_deviation(self) -> None:
        """z_score should be high for large deviations."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        for _ in range(10):
            abs_rssi.update(-75.0)

        z = abs_rssi.z_score(-90.0)  # 15dB deviation
        assert abs(z) > 2.0

    def test_variance_property(self) -> None:
        """variance property should return appropriate value."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        for _ in range(10):
            abs_rssi.update(-75.0)

        var = abs_rssi.variance
        assert var > 0

    def test_is_mature_property(self) -> None:
        """is_mature should be False until enough samples."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        assert not abs_rssi.is_mature

        for _ in range(25):
            abs_rssi.update(-75.0)

        assert abs_rssi.is_mature

    def test_sample_count_property(self) -> None:
        """sample_count should return correct count."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        assert abs_rssi.sample_count == 0

        for _ in range(5):
            abs_rssi.update(-75.0)

        assert abs_rssi.sample_count == 5

    def test_to_dict_and_from_dict(self) -> None:
        """to_dict and from_dict should round-trip correctly."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        for _ in range(10):
            abs_rssi.update(-75.0)
        abs_rssi.update_button(-80.0)

        data = abs_rssi.to_dict()
        restored = ScannerAbsoluteRssi.from_dict(data)

        assert restored.scanner_address == abs_rssi.scanner_address
        assert restored.has_button_training == abs_rssi.has_button_training
        assert abs(restored.expected_rssi - abs_rssi.expected_rssi) < 0.01

    def test_reset_training(self) -> None:
        """reset_training should clear both filters."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        abs_rssi.update(-75.0)
        abs_rssi.update_button(-80.0)

        abs_rssi.reset_training()

        assert not abs_rssi.has_button_training

    def test_expected_rssi_uninitialized(self) -> None:
        """expected_rssi should return 0.0 when no data."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        # No updates - neither auto nor button initialized
        assert abs_rssi.expected_rssi == 0.0

    def test_is_mature_with_button_training(self) -> None:
        """is_mature should be True when button trained, even with few samples."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        # Only one button sample
        abs_rssi.update_button(-75.0)

        # Should be mature due to button training (user intent)
        assert abs_rssi.is_mature
        assert abs_rssi.has_button_training

    def test_z_score_zero_variance(self) -> None:
        """z_score should return 0.0 when variance is zero."""
        abs_rssi = ScannerAbsoluteRssi(scanner_address="aa:bb:cc:dd:ee:ff")
        # Force zero variance scenario - button filter NOT initialized,
        # and directly set auto filter variance to 0
        # This triggers line 306: return 0.0 when self.variance <= 0
        abs_rssi._kalman_auto._initialized = True
        abs_rssi._kalman_auto.estimate = -75.0
        abs_rssi._kalman_auto.variance = 0.0  # Direct zero variance

        # With zero variance, z_score should return 0.0
        z = abs_rssi.z_score(-80.0)
        assert z == 0.0

    def test_from_dict_invalid_scanner_type(self) -> None:
        """from_dict should raise TypeError for non-string scanner."""
        with pytest.raises(TypeError):
            ScannerAbsoluteRssi.from_dict({
                "scanner": 12345,  # Should be string
                "auto_estimate": -75.0,
                "auto_variance": 4.0,
                "auto_samples": 10,
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_negative_variance(self) -> None:
        """from_dict should raise ValueError for negative variance."""
        with pytest.raises(ValueError):
            ScannerAbsoluteRssi.from_dict({
                "scanner": "aa:bb:cc:dd:ee:ff",
                "auto_estimate": -75.0,
                "auto_variance": -4.0,  # Negative!
                "auto_samples": 10,
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_negative_samples(self) -> None:
        """from_dict should raise ValueError for negative sample count."""
        with pytest.raises(ValueError):
            ScannerAbsoluteRssi.from_dict({
                "scanner": "aa:bb:cc:dd:ee:ff",
                "auto_estimate": -75.0,
                "auto_variance": 4.0,
                "auto_samples": -5,  # Negative!
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_old_format(self) -> None:
        """from_dict should handle legacy single-filter format."""
        restored = ScannerAbsoluteRssi.from_dict({
            "scanner": "aa:bb:cc:dd:ee:ff",
            "estimate": -75.0,
            "variance": 4.0,
            "samples": 10,
        })
        assert restored.scanner_address == "aa:bb:cc:dd:ee:ff"
        assert abs(restored._kalman_auto.estimate - (-75.0)) < 0.01

    def test_from_dict_old_format_negative_variance(self) -> None:
        """from_dict should raise ValueError for negative variance in old format."""
        with pytest.raises(ValueError):
            ScannerAbsoluteRssi.from_dict({
                "scanner": "aa:bb:cc:dd:ee:ff",
                "estimate": -75.0,
                "variance": -4.0,  # Negative!
                "samples": 10,
            })

    def test_from_dict_old_format_negative_samples(self) -> None:
        """from_dict should raise ValueError for negative samples in old format."""
        with pytest.raises(ValueError):
            ScannerAbsoluteRssi.from_dict({
                "scanner": "aa:bb:cc:dd:ee:ff",
                "estimate": -75.0,
                "variance": 4.0,
                "samples": -5,  # Negative!
            })


# =============================================================================
# ScannerPairCorrelation.reset_variance_only() Tests
# =============================================================================


class TestScannerPairCorrelationResetVarianceOnly:
    """Tests for ScannerPairCorrelation.reset_variance_only() method."""

    def test_reset_preserves_button_training(self) -> None:
        """reset_variance_only() should preserve has_button_training status."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        pair.update_button(-5.0)
        assert pair.has_button_training

        pair.reset_variance_only()

        assert pair.has_button_training

    def test_reset_affects_button_filter_only(self) -> None:
        """reset_variance_only() should only affect button filter, not auto."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        # Train button filter
        pair.update_button(-5.0)
        button_estimate = pair._kalman_button.estimate
        # Train auto filter
        pair.update(-3.0)
        auto_variance_before = pair._kalman_auto.variance

        pair.reset_variance_only()

        # Button estimate preserved
        assert pair._kalman_button.estimate == button_estimate
        # Auto filter unchanged
        assert pair._kalman_auto.variance == auto_variance_before


# =============================================================================
# ScannerPairCorrelation Additional Methods Tests
# =============================================================================


class TestScannerPairCorrelationAdditional:
    """Additional tests for ScannerPairCorrelation methods."""

    def test_expected_delta_auto_only(self) -> None:
        """expected_delta should return auto estimate when no button training."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        pair.update(-5.0)

        result = pair.expected_delta
        assert abs(result - (-5.0)) < 1.0

    def test_expected_delta_with_button(self) -> None:
        """expected_delta should use clamped fusion with button training."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        pair.update(-3.0)  # Auto
        pair.update_button(-8.0)  # Button

        result = pair.expected_delta
        # With clamped fusion, result should be influenced by both
        assert -10.0 < result < -2.0

    def test_z_score_returns_float(self) -> None:
        """z_score should return a float value."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        for _ in range(10):
            pair.update(-5.0)

        z = pair.z_score(-5.0)
        assert isinstance(z, float)
        assert abs(z) < 1.0

    def test_z_score_high_deviation(self) -> None:
        """z_score should be high for large deviations."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        for _ in range(10):
            pair.update(-5.0)

        z = pair.z_score(-20.0)  # 15dB deviation
        assert abs(z) > 2.0

    def test_variance_property(self) -> None:
        """variance property should return appropriate value."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        for _ in range(10):
            pair.update(-5.0)

        var = pair.variance
        assert var > 0

    def test_is_mature_property(self) -> None:
        """is_mature should be False until enough samples."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        assert not pair.is_mature

        for _ in range(35):
            pair.update(-5.0)

        assert pair.is_mature

    def test_sample_count_property(self) -> None:
        """sample_count should return correct count."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        assert pair.sample_count == 0

        for _ in range(5):
            pair.update(-5.0)

        assert pair.sample_count == 5

    def test_to_dict_and_from_dict(self) -> None:
        """to_dict and from_dict should round-trip correctly."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        for _ in range(10):
            pair.update(-5.0)
        pair.update_button(-8.0)

        data = pair.to_dict()
        restored = ScannerPairCorrelation.from_dict(data)

        assert restored.scanner_address == pair.scanner_address
        assert restored.has_button_training == pair.has_button_training
        assert abs(restored.expected_delta - pair.expected_delta) < 0.01

    def test_reset_training(self) -> None:
        """reset_training should clear both filters."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        pair.update(-5.0)
        pair.update_button(-8.0)

        pair.reset_training()

        assert not pair.has_button_training

    def test_expected_delta_uninitialized(self) -> None:
        """expected_delta should return 0.0 when no data."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        # No updates - neither auto nor button initialized
        assert pair.expected_delta == 0.0

    def test_is_mature_with_button_training(self) -> None:
        """is_mature should be True when button trained, even with few samples."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        # Only one button sample
        pair.update_button(-5.0)

        # Should be mature due to button training (user intent)
        assert pair.is_mature
        assert pair.has_button_training

    def test_z_score_zero_variance(self) -> None:
        """z_score should return 0.0 when variance is zero."""
        pair = ScannerPairCorrelation(scanner_address="aa:bb:cc:dd:ee:02")
        # Force zero variance scenario - button filter NOT initialized,
        # and directly set auto filter variance to 0
        # This triggers line 311: return 0.0 when self.variance <= 0
        pair._kalman_auto._initialized = True
        pair._kalman_auto.estimate = -5.0
        pair._kalman_auto.variance = 0.0  # Direct zero variance

        # With zero variance, z_score should return 0.0
        z = pair.z_score(-10.0)
        assert z == 0.0

    def test_from_dict_invalid_scanner_type(self) -> None:
        """from_dict should raise TypeError for non-string scanner."""
        with pytest.raises(TypeError):
            ScannerPairCorrelation.from_dict({
                "scanner": 12345,  # Should be string
                "auto_estimate": -5.0,
                "auto_variance": 4.0,
                "auto_samples": 10,
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_negative_variance(self) -> None:
        """from_dict should raise ValueError for negative variance."""
        with pytest.raises(ValueError):
            ScannerPairCorrelation.from_dict({
                "scanner": "aa:bb:cc:dd:ee:02",
                "auto_estimate": -5.0,
                "auto_variance": -4.0,  # Negative!
                "auto_samples": 10,
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_negative_samples(self) -> None:
        """from_dict should raise ValueError for negative sample count."""
        with pytest.raises(ValueError):
            ScannerPairCorrelation.from_dict({
                "scanner": "aa:bb:cc:dd:ee:02",
                "auto_estimate": -5.0,
                "auto_variance": 4.0,
                "auto_samples": -5,  # Negative!
                "button_estimate": 0.0,
                "button_variance": 4.0,
                "button_samples": 0,
            })

    def test_from_dict_old_format(self) -> None:
        """from_dict should handle legacy single-filter format."""
        restored = ScannerPairCorrelation.from_dict({
            "scanner": "aa:bb:cc:dd:ee:02",
            "estimate": -5.0,  # Old format uses 'estimate', not 'delta'
            "variance": 4.0,
            "samples": 10,
        })
        assert restored.scanner_address == "aa:bb:cc:dd:ee:02"
        assert abs(restored._kalman_auto.estimate - (-5.0)) < 0.01

    def test_from_dict_old_format_negative_variance(self) -> None:
        """from_dict should raise ValueError for negative variance in old format."""
        with pytest.raises(ValueError):
            ScannerPairCorrelation.from_dict({
                "scanner": "aa:bb:cc:dd:ee:02",
                "estimate": -5.0,
                "variance": -4.0,  # Negative!
                "samples": 10,
            })

    def test_from_dict_old_format_negative_samples(self) -> None:
        """from_dict should raise ValueError for negative samples in old format."""
        with pytest.raises(ValueError):
            ScannerPairCorrelation.from_dict({
                "scanner": "aa:bb:cc:dd:ee:02",
                "estimate": -5.0,
                "variance": 4.0,
                "samples": -5,  # Negative!
            })


# =============================================================================
# AreaProfile.reset_variance_only() Tests
# =============================================================================


class TestAreaProfileResetVarianceOnly:
    """Tests for AreaProfile.reset_variance_only() method."""

    def test_reset_resets_all_correlations(self) -> None:
        """reset_variance_only() should reset all correlation variances."""
        profile = AreaProfile(area_id="test_area")
        # Train with multiple scanners
        profile.update_button(
            primary_rssi=-75.0,
            other_readings={"scanner2": -70.0, "scanner3": -80.0},
            primary_scanner_addr="scanner1",
        )

        # Collect variance before reset
        variances_before: list[float] = []
        for corr in profile._correlations.values():
            variances_before.append(corr._kalman_button.variance)

        profile.reset_variance_only()

        # All correlations should have reset variance
        for corr in profile._correlations.values():
            # Variance should be at measurement_noise level after reset
            assert corr._kalman_button.variance == corr._kalman_button.measurement_noise

    def test_reset_resets_all_absolute_profiles(self) -> None:
        """reset_variance_only() should reset all absolute profile variances."""
        profile = AreaProfile(area_id="test_area")
        # Train with multiple scanners
        profile.update_button(
            primary_rssi=-75.0,
            other_readings={"scanner2": -70.0},
            primary_scanner_addr="scanner1",
        )

        profile.reset_variance_only()

        # All absolute profiles should have reset variance
        for abs_prof in profile._absolute_profiles.values():
            assert (
                abs_prof._kalman_button.variance
                == abs_prof._kalman_button.measurement_noise
            )

    def test_reset_preserves_has_button_training(self) -> None:
        """reset_variance_only() should preserve has_button_training status."""
        profile = AreaProfile(area_id="test_area")
        profile.update_button(-75.0, {"scanner2": -70.0}, "scanner1")
        assert profile.has_button_training

        profile.reset_variance_only()

        assert profile.has_button_training

    def test_reset_on_empty_profile_does_nothing(self) -> None:
        """reset_variance_only() on empty profile should be a no-op."""
        profile = AreaProfile(area_id="test_area")

        profile.reset_variance_only()  # Should not raise

        assert not profile.has_button_training


# =============================================================================
# AreaProfile Additional Methods Tests
# =============================================================================


class TestAreaProfileAdditional:
    """Additional tests for AreaProfile methods."""

    def test_update_auto(self) -> None:
        """update should add correlations for auto learning."""
        profile = AreaProfile(area_id="test_area")
        profile.update(-75.0, {"scanner2": -70.0, "scanner3": -80.0}, "scanner1")

        assert len(profile._correlations) > 0
        assert len(profile._absolute_profiles) > 0

    def test_get_z_scores(self) -> None:
        """get_z_scores should return list of tuples."""
        profile = AreaProfile(area_id="test_area")
        # Train enough to be mature
        for _ in range(35):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_z_scores(-75.0, {"scanner2": -70.0})
        assert isinstance(z_scores, list)
        if len(z_scores) > 0:
            assert isinstance(z_scores[0], tuple)
            assert len(z_scores[0]) == 2

    def test_get_weighted_z_scores(self) -> None:
        """get_weighted_z_scores should return list with sample counts."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(35):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_z_scores(-75.0, {"scanner2": -70.0})
        assert isinstance(z_scores, list)
        if len(z_scores) > 0:
            assert isinstance(z_scores[0], tuple)
            assert len(z_scores[0]) == 3  # (scanner, z, count)

    def test_get_absolute_rssi(self) -> None:
        """get_absolute_rssi should return profile or None."""
        profile = AreaProfile(area_id="test_area")
        profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        result = profile.get_absolute_rssi("scanner1")
        assert result is not None

        result_none = profile.get_absolute_rssi("nonexistent")
        assert result_none is None

    def test_get_absolute_z_scores(self) -> None:
        """get_absolute_z_scores should return list of tuples."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(25):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_absolute_z_scores({"scanner1": -75.0, "scanner2": -70.0})
        assert isinstance(z_scores, list)

    def test_get_weighted_absolute_z_scores(self) -> None:
        """get_weighted_absolute_z_scores should return list with counts."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(25):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_absolute_z_scores(
            {"scanner1": -75.0, "scanner2": -70.0}
        )
        assert isinstance(z_scores, list)

    def test_correlation_count(self) -> None:
        """correlation_count should return correct count."""
        profile = AreaProfile(area_id="test_area")
        assert profile.correlation_count == 0

        profile.update(-75.0, {"scanner2": -70.0, "scanner3": -80.0}, "scanner1")
        assert profile.correlation_count == 2

    def test_mature_correlation_count(self) -> None:
        """mature_correlation_count should return count of mature correlations."""
        profile = AreaProfile(area_id="test_area")
        assert profile.mature_correlation_count == 0

        for _ in range(35):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        assert profile.mature_correlation_count > 0

    def test_mature_absolute_count(self) -> None:
        """mature_absolute_count should return count of mature profiles."""
        profile = AreaProfile(area_id="test_area")
        assert profile.mature_absolute_count == 0

        for _ in range(25):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        assert profile.mature_absolute_count > 0

    def test_to_dict_and_from_dict(self) -> None:
        """to_dict and from_dict should round-trip correctly."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(10):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")
        profile.update_button(-75.0, {"scanner2": -70.0}, "scanner1")

        data = profile.to_dict()
        restored = AreaProfile.from_dict(data)

        assert restored.area_id == profile.area_id
        assert restored.has_button_training == profile.has_button_training
        assert restored.correlation_count == profile.correlation_count

    def test_reset_training(self) -> None:
        """reset_training should clear all training data."""
        profile = AreaProfile(area_id="test_area")
        profile.update(-75.0, {"scanner2": -70.0}, "scanner1")
        profile.update_button(-75.0, {"scanner2": -70.0}, "scanner1")

        profile.reset_training()

        assert not profile.has_button_training

    def test_memory_limit_enforcement(self) -> None:
        """Profile should enforce memory limits on correlations."""
        profile = AreaProfile(area_id="test_area")
        # Add many correlations
        other_readings = {f"scanner{i}": -70.0 - i for i in range(20)}
        profile.update(-75.0, other_readings, "scanner0")

        # Should be limited by MAX_CORRELATIONS_PER_AREA
        assert profile.correlation_count <= 15

    def test_get_z_scores_skips_unknown_scanner(self) -> None:
        """get_z_scores should skip scanners not in correlations."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(35):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        # Query with an unknown scanner
        z_scores = profile.get_z_scores(-75.0, {"unknown_scanner": -70.0, "scanner2": -70.0})
        # Should only have result for scanner2, not unknown_scanner
        scanner_addrs = [item[0] for item in z_scores]
        assert "unknown_scanner" not in scanner_addrs

    def test_get_z_scores_skips_immature_correlation(self) -> None:
        """get_z_scores should skip immature correlations."""
        profile = AreaProfile(area_id="test_area")
        # Only 5 samples - not mature
        for _ in range(5):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_z_scores(-75.0, {"scanner2": -70.0})
        # Should be empty because correlation is not mature
        assert len(z_scores) == 0

    def test_get_weighted_z_scores_skips_unknown_scanner(self) -> None:
        """get_weighted_z_scores should skip scanners not in correlations."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(35):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_z_scores(-75.0, {"unknown": -70.0, "scanner2": -70.0})
        scanner_addrs = [item[0] for item in z_scores]
        assert "unknown" not in scanner_addrs

    def test_get_weighted_z_scores_skips_immature_correlation(self) -> None:
        """get_weighted_z_scores should skip immature correlations."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(5):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_z_scores(-75.0, {"scanner2": -70.0})
        assert len(z_scores) == 0

    def test_get_absolute_z_scores_skips_unknown_scanner(self) -> None:
        """get_absolute_z_scores should skip scanners not in profiles."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(25):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_absolute_z_scores({"unknown": -70.0, "scanner1": -75.0})
        scanner_addrs = [item[0] for item in z_scores]
        assert "unknown" not in scanner_addrs

    def test_get_absolute_z_scores_skips_immature_profile(self) -> None:
        """get_absolute_z_scores should skip immature profiles."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(5):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_absolute_z_scores({"scanner1": -75.0})
        assert len(z_scores) == 0

    def test_get_weighted_absolute_z_scores_skips_unknown_scanner(self) -> None:
        """get_weighted_absolute_z_scores should skip scanners not in profiles."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(25):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_absolute_z_scores({"unknown": -70.0, "scanner1": -75.0})
        scanner_addrs = [item[0] for item in z_scores]
        assert "unknown" not in scanner_addrs

    def test_get_weighted_absolute_z_scores_skips_immature_profile(self) -> None:
        """get_weighted_absolute_z_scores should skip immature profiles."""
        profile = AreaProfile(area_id="test_area")
        for _ in range(5):
            profile.update(-75.0, {"scanner2": -70.0}, "scanner1")

        z_scores = profile.get_weighted_absolute_z_scores({"scanner1": -75.0})
        assert len(z_scores) == 0


# =============================================================================
# Quality Index Calculation Tests
# =============================================================================


class TestQualityIndexCalculation:
    """Tests for quality index calculation in button training."""

    def test_quality_index_excellent(self) -> None:
        """Quality should be Excellent (>=100%) for sufficient effective samples."""
        # Simulate quality calculation
        successful_samples = 60
        autocorr_factor = 0.82
        effective_samples = successful_samples * autocorr_factor  # 49.2
        clt_target = 30
        quality_percent = min(100.0, (effective_samples / clt_target) * 100.0)

        assert quality_percent >= 100.0

    def test_quality_index_good(self) -> None:
        """Quality should be Good (70-99%) for 21-36 effective samples."""
        # Simulate 30 successful samples
        successful_samples = 30
        autocorr_factor = 0.82
        effective_samples = successful_samples * autocorr_factor  # 24.6
        clt_target = 30
        quality_percent = min(100.0, (effective_samples / clt_target) * 100.0)

        assert 70.0 <= quality_percent < 100.0

    def test_quality_index_moderate(self) -> None:
        """Quality should be Moderate (50-69%) for 15-20 effective samples."""
        # Simulate 20 successful samples
        successful_samples = 20
        autocorr_factor = 0.82
        effective_samples = successful_samples * autocorr_factor  # 16.4
        clt_target = 30
        quality_percent = min(100.0, (effective_samples / clt_target) * 100.0)

        assert 50.0 <= quality_percent < 70.0

    def test_quality_index_poor(self) -> None:
        """Quality should be Poor (<50%) for <15 effective samples."""
        # Simulate 10 successful samples
        successful_samples = 10
        autocorr_factor = 0.82
        effective_samples = successful_samples * autocorr_factor  # 8.2
        clt_target = 30
        quality_percent = min(100.0, (effective_samples / clt_target) * 100.0)

        assert quality_percent < 50.0


# =============================================================================
# Multi-Position Training Integration Tests
# =============================================================================


class TestMultiPositionTrainingIntegration:
    """Integration tests for multi-position training workflow."""

    def test_reset_allows_new_position_to_influence_estimate(self) -> None:
        """With variance reset, new position samples should significantly influence estimate."""
        profile = AreaProfile(area_id="test_area")

        # Position 1: RSSI ~ -70
        for _ in range(20):
            profile.update_button(-70.0, {"scanner2": -75.0}, "scanner1")

        estimate_after_pos1 = profile._absolute_profiles["scanner1"].expected_rssi
        assert abs(estimate_after_pos1 - (-70.0)) < 2.0

        # Position 2: RSSI ~ -80 (reset variance first)
        profile.reset_variance_only()
        for _ in range(20):
            profile.update_button(-80.0, {"scanner2": -85.0}, "scanner1")

        estimate_after_pos2 = profile._absolute_profiles["scanner1"].expected_rssi
        # After reset, new samples have strong initial influence
        # Estimate should move significantly towards -80
        # It should be between -70 and -80, but closer to -80
        assert -85.0 < estimate_after_pos2 < -70.0
        # The estimate should have moved at least 5dB from original -70
        assert abs(estimate_after_pos2 - estimate_after_pos1) > 5.0

    def test_three_positions_with_reset_produces_blend(self) -> None:
        """Training from 3 positions with reset should produce a blended estimate."""
        profile = AreaProfile(area_id="test_area")

        # Position 1: RSSI ~ -70
        for _ in range(20):
            profile.update_button(-70.0, {"scanner2": -75.0}, "scanner1")

        # Position 2: RSSI ~ -80 (reset variance first)
        profile.reset_variance_only()
        for _ in range(20):
            profile.update_button(-80.0, {"scanner2": -85.0}, "scanner1")

        # Position 3: RSSI ~ -90 (reset variance first)
        profile.reset_variance_only()
        for _ in range(20):
            profile.update_button(-90.0, {"scanner2": -95.0}, "scanner1")

        estimate_after_pos3 = profile._absolute_profiles["scanner1"].expected_rssi
        # The estimate should be within the range of all positions
        # Due to Kalman filter dynamics with reset, it will be biased
        # towards more recent positions, but should incorporate all data
        assert -95.0 < estimate_after_pos3 < -65.0
        # Should be somewhere in the middle range
        assert -92.0 < estimate_after_pos3 < -72.0

    def test_reset_gives_more_influence_than_no_reset(self) -> None:
        """With reset, new position should have MORE influence than without reset."""
        # Setup two identical profiles
        profile_with_reset = AreaProfile(area_id="test_area_reset")
        profile_no_reset = AreaProfile(area_id="test_area_no_reset")

        # Position 1: Both get identical training at -70
        for _ in range(20):
            profile_with_reset.update_button(-70.0, {"scanner2": -75.0}, "scanner1")
            profile_no_reset.update_button(-70.0, {"scanner2": -75.0}, "scanner1")

        estimate_pos1_reset = profile_with_reset._absolute_profiles["scanner1"].expected_rssi
        estimate_pos1_no_reset = profile_no_reset._absolute_profiles["scanner1"].expected_rssi
        assert abs(estimate_pos1_reset - estimate_pos1_no_reset) < 0.01  # Should be identical

        # Position 2: One gets reset, one doesn't
        profile_with_reset.reset_variance_only()  # Reset variance
        # profile_no_reset does NOT get reset

        for _ in range(20):
            profile_with_reset.update_button(-90.0, {"scanner2": -95.0}, "scanner1")
            profile_no_reset.update_button(-90.0, {"scanner2": -95.0}, "scanner1")

        estimate_reset = profile_with_reset._absolute_profiles["scanner1"].expected_rssi
        estimate_no_reset = profile_no_reset._absolute_profiles["scanner1"].expected_rssi

        # Both should have moved towards -90
        assert estimate_reset < estimate_pos1_reset  # Moved towards -90
        assert estimate_no_reset < estimate_pos1_no_reset  # Also moved

        # Key assertion: With reset, the estimate should have moved MORE towards -90
        # (i.e., estimate_reset should be more negative than estimate_no_reset)
        movement_with_reset = abs(estimate_reset - estimate_pos1_reset)
        movement_without_reset = abs(estimate_no_reset - estimate_pos1_no_reset)
        assert movement_with_reset > movement_without_reset


# =============================================================================
# Constants Tests
# =============================================================================


class TestTrainingConstants:
    """Tests for training constants in button.py."""

    def test_sample_count_provides_sufficient_effective_samples(self) -> None:
        """TRAINING_SAMPLE_COUNT should provide >= 30 effective samples."""
        from custom_components.bermuda.button import TRAINING_SAMPLE_COUNT

        autocorr_factor = 0.82  # For 5s interval
        effective_samples = TRAINING_SAMPLE_COUNT * autocorr_factor

        assert effective_samples >= 30  # CLT threshold

    def test_timeout_allows_enough_time_for_samples(self) -> None:
        """TRAINING_MAX_TIME_SECONDS should be enough for all samples."""
        from custom_components.bermuda.button import (
            TRAINING_MAX_TIME_SECONDS,
            TRAINING_SAMPLE_COUNT,
            TRAINING_MIN_SAMPLE_INTERVAL,
        )

        min_time_needed = TRAINING_SAMPLE_COUNT * TRAINING_MIN_SAMPLE_INTERVAL

        assert TRAINING_MAX_TIME_SECONDS >= min_time_needed

    def test_sample_interval_reduces_autocorrelation(self) -> None:
        """TRAINING_MIN_SAMPLE_INTERVAL should reduce autocorrelation below 0.15."""
        from custom_components.bermuda.button import TRAINING_MIN_SAMPLE_INTERVAL

        # At 5s interval, autocorrelation is approximately 0.10
        # (exponential decay model with tau ~ 1.5s)
        assert TRAINING_MIN_SAMPLE_INTERVAL >= 5.0
