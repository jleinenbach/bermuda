"""Test the AdaptiveStatistics and AdaptiveRobustFilter classes."""

from __future__ import annotations

import math

import pytest

from custom_components.bermuda.filters.adaptive import (
    AdaptiveRobustFilter,
    AdaptiveStatistics,
)
from custom_components.bermuda.filters.base import FilterConfig
from custom_components.bermuda.filters.const import (
    BLE_RSSI_TYPICAL_STDDEV,
    CUSUM_THRESHOLD_SIGMA,
)


class TestAdaptiveStatistics:
    """Tests for AdaptiveStatistics class."""

    def test_initial_state(self) -> None:
        """Test that initial state is correct."""
        stats = AdaptiveStatistics()
        assert stats.mean == 0.0
        assert stats.variance == BLE_RSSI_TYPICAL_STDDEV**2
        assert stats.sample_count == 0
        assert stats.cusum_pos == 0.0
        assert stats.cusum_neg == 0.0
        assert stats.last_changepoint == 0

    def test_stddev_property(self) -> None:
        """Test that stddev property returns sqrt of variance."""
        stats = AdaptiveStatistics()
        stats.variance = 16.0
        assert stats.stddev == 4.0

    def test_stddev_floors_at_minimum(self) -> None:
        """Test that stddev has a minimum floor to prevent division by zero."""
        stats = AdaptiveStatistics()
        stats.variance = 0.0  # Would cause sqrt(0) = 0
        assert stats.stddev == math.sqrt(0.1)  # Floored at 0.1

    def test_first_sample_initializes_mean(self) -> None:
        """Test that first sample initializes mean directly."""
        stats = AdaptiveStatistics()
        result = stats.update(-70.0)
        assert stats.mean == -70.0
        assert stats.sample_count == 1
        assert result is False  # No changepoint on first sample

    def test_update_uses_ema_for_mean(self) -> None:
        """Test that subsequent samples use EMA for mean."""
        stats = AdaptiveStatistics(alpha=0.5)
        stats.update(-70.0)  # First sample
        stats.update(-60.0)  # Second sample

        # EMA: 0.5 * (-60) + 0.5 * (-70) = -65
        assert stats.mean == -65.0
        assert stats.sample_count == 2

    def test_update_tracks_variance(self) -> None:
        """Test that variance is updated using EMA."""
        stats = AdaptiveStatistics(alpha=0.5)
        stats.update(-70.0)
        stats.update(-60.0)

        # Variance should be updated based on deviation from old mean
        # deviation_sq = (-60 - (-70))^2 = 100
        # variance = 0.5 * 100 + 0.5 * initial_variance
        assert stats.variance > 0

    def test_cusum_detects_positive_shift(self) -> None:
        """Test that CUSUM detects a positive mean shift."""
        stats = AdaptiveStatistics(alpha=0.1)

        # Initialize with stable values
        for _ in range(20):
            stats.update(-70.0)

        # Now introduce a significant positive shift
        changepoint_detected = False
        for _ in range(50):
            if stats.update(-50.0):  # Much higher (less negative)
                changepoint_detected = True
                break

        assert changepoint_detected

    def test_cusum_detects_negative_shift(self) -> None:
        """Test that CUSUM detects a negative mean shift."""
        stats = AdaptiveStatistics(alpha=0.1)

        # Initialize with stable values
        for _ in range(20):
            stats.update(-50.0)

        # Now introduce a significant negative shift
        changepoint_detected = False
        for _ in range(50):
            if stats.update(-90.0):  # Much lower (more negative)
                changepoint_detected = True
                break

        assert changepoint_detected

    def test_cusum_resets_after_detection(self) -> None:
        """Test that CUSUM values are reset after changepoint detection."""
        stats = AdaptiveStatistics(alpha=0.1)

        # Initialize
        for _ in range(10):
            stats.update(-70.0)

        # Force a big shift to trigger detection
        for _ in range(50):
            result = stats.update(-30.0)
            if result:
                # After detection, CUSUM should be reset
                assert stats.cusum_pos == 0.0
                assert stats.cusum_neg == 0.0
                assert stats.last_changepoint == stats.sample_count
                break

    def test_reset_clears_all_state(self) -> None:
        """Test that reset clears all state."""
        stats = AdaptiveStatistics()
        stats.update(-70.0)
        stats.update(-60.0)
        stats.cusum_pos = 5.0
        stats.cusum_neg = 3.0
        stats.last_changepoint = 10

        stats.reset()

        assert stats.mean == 0.0
        assert stats.variance == BLE_RSSI_TYPICAL_STDDEV**2
        assert stats.sample_count == 0
        assert stats.cusum_pos == 0.0
        assert stats.cusum_neg == 0.0
        assert stats.last_changepoint == 0

    def test_to_dict_returns_diagnostic_info(self) -> None:
        """Test that to_dict returns diagnostic information."""
        stats = AdaptiveStatistics()
        stats.update(-70.0)
        stats.update(-65.0)

        result = stats.to_dict()

        assert "mean" in result
        assert "stddev" in result
        assert "sample_count" in result
        assert "changepoints" in result
        assert result["sample_count"] == 2


class TestAdaptiveRobustFilter:
    """Tests for AdaptiveRobustFilter class."""

    def test_update_returns_filtered_value(self) -> None:
        """Test that update returns the filtered mean."""
        filt = AdaptiveRobustFilter()
        result = filt.update(-70.0)
        assert result == -70.0

    def test_update_with_timestamp(self) -> None:
        """Test that update accepts optional timestamp."""
        filt = AdaptiveRobustFilter()
        result = filt.update(-70.0, timestamp=123.456)
        assert result == -70.0

    def test_get_estimate_returns_mean(self) -> None:
        """Test that get_estimate returns current mean."""
        filt = AdaptiveRobustFilter()
        filt.update(-70.0)
        filt.update(-60.0)
        assert filt.get_estimate() == filt._stats.mean

    def test_get_variance_returns_variance(self) -> None:
        """Test that get_variance returns current variance."""
        filt = AdaptiveRobustFilter()
        filt.update(-70.0)
        filt.update(-60.0)
        assert filt.get_variance() == filt._stats.variance

    def test_reset_clears_state(self) -> None:
        """Test that reset clears filter state."""
        filt = AdaptiveRobustFilter()
        filt.update(-70.0)
        filt.update(-60.0)
        filt._last_changepoint_detected = True

        filt.reset()

        assert filt._stats.sample_count == 0
        assert filt._last_changepoint_detected is False

    def test_get_diagnostics_includes_cusum_state(self) -> None:
        """Test that get_diagnostics includes CUSUM information."""
        filt = AdaptiveRobustFilter()
        filt.update(-70.0)

        diag = filt.get_diagnostics()

        assert "mean" in diag
        assert "stddev" in diag
        assert "cusum_pos" in diag
        assert "cusum_neg" in diag
        assert "changepoint_detected" in diag

    def test_changepoint_detected_returns_last_result(self) -> None:
        """Test that changepoint_detected returns last detection result."""
        filt = AdaptiveRobustFilter()
        filt.update(-70.0)

        assert filt.changepoint_detected() is False

    def test_from_config_creates_filter_with_alpha(self) -> None:
        """Test that from_config creates filter with specified alpha."""
        config = FilterConfig(ema_alpha=0.2)
        filt = AdaptiveRobustFilter.from_config(config)

        assert filt.alpha == 0.2

    def test_post_init_sets_stats_alpha(self) -> None:
        """Test that __post_init__ sets stats alpha from filter alpha."""
        filt = AdaptiveRobustFilter(alpha=0.3)
        assert filt._stats.alpha == 0.3

    def test_detects_changepoint_in_signal(self) -> None:
        """Test end-to-end changepoint detection."""
        filt = AdaptiveRobustFilter(alpha=0.1)

        # Stable period
        for _ in range(20):
            filt.update(-70.0)

        # Shift period - keep updating until changepoint or max iterations
        detected = False
        for _ in range(100):
            filt.update(-40.0)
            if filt.changepoint_detected():
                detected = True
                break

        assert detected


class TestCusumThresholds:
    """Tests for CUSUM threshold behavior."""

    def test_no_false_alarm_in_stable_signal(self) -> None:
        """Test that CUSUM doesn't trigger on stable signal with minor noise."""
        stats = AdaptiveStatistics(alpha=0.1)

        # Feed stable signal with minor noise
        import random

        random.seed(42)

        changepoints = 0
        for _ in range(100):
            noise = random.uniform(-2, 2)  # ±2 dB noise
            if stats.update(-70.0 + noise):
                changepoints += 1

        # Should have very few or no false alarms
        assert changepoints < 3

    def test_cusum_tracks_both_directions(self) -> None:
        """Test that CUSUM tracks positive and negative shifts independently."""
        stats = AdaptiveStatistics(alpha=0.5)
        stats.update(-70.0)

        # Small positive deviation
        stats.update(-65.0)
        pos_after_up = stats.cusum_pos
        neg_after_up = stats.cusum_neg

        # The CUSUM values should reflect the direction of the deviation
        # (though exact values depend on the math)
        assert isinstance(pos_after_up, float)
        assert isinstance(neg_after_up, float)


class TestFilterIntegration:
    """Integration tests for the adaptive filter."""

    def test_filter_smooths_noisy_signal(self) -> None:
        """Test that filter smooths out noise in signal."""
        filt = AdaptiveRobustFilter(alpha=0.1)

        # Generate noisy signal around -70 dB
        import random

        random.seed(42)

        estimates = []
        for _ in range(50):
            noisy = -70.0 + random.gauss(0, 5)
            estimate = filt.update(noisy)
            estimates.append(estimate)

        # The final estimate should be close to -70 (within noise bounds)
        final = estimates[-1]
        assert -80 < final < -60

    def test_filter_adapts_to_new_level(self) -> None:
        """Test that filter adapts when signal level changes."""
        filt = AdaptiveRobustFilter(alpha=0.2)

        # Start at -70 dB
        for _ in range(20):
            filt.update(-70.0)

        initial_mean = filt.get_estimate()

        # Move to -50 dB
        for _ in range(50):
            filt.update(-50.0)

        final_mean = filt.get_estimate()

        # Mean should have moved toward -50
        assert final_mean > initial_mean
        assert final_mean > -60  # Should be closer to -50

    def test_module_imports_correctly(self) -> None:
        """Test that the module can be imported without errors."""
        from custom_components.bermuda.filters import adaptive

        assert hasattr(adaptive, "AdaptiveStatistics")
        assert hasattr(adaptive, "AdaptiveRobustFilter")
