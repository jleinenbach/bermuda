"""
Tests for filter module optimizations.

Tests cover:
- Phase 1: Optional fixes (input validation, factory, serialization)
- Phase 2: Time-aware filtering
- Phase 3: Performance optimizations (NumPy backend, sequential update)
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.filters import (
    DEFAULT_UPDATE_DT,
    FilterConfig,
    KalmanFilter,
    SignalFilter,
    UnscentedKalmanFilter,
    create_filter,
)
from custom_components.bermuda.filters.ukf_numpy import is_numpy_available


# =============================================================================
# Phase 1: Optional Fixes Tests
# =============================================================================


class TestInputValidation:
    """Tests for input validation in update_adaptive()."""

    def test_valid_ref_power_accepted(self) -> None:
        """Valid ref_power values should be accepted without warning."""
        kf = KalmanFilter()
        # All valid BLE ref_power values
        for ref_power in [-55, -70, -94, -100, 0]:
            result = kf.update_adaptive(-70.0, ref_power=float(ref_power))
            assert isinstance(result, float)

    def test_invalid_positive_ref_power_uses_default(self) -> None:
        """Positive ref_power should fall back to default -55."""
        kf = KalmanFilter()
        # Invalid: positive value
        result = kf.update_adaptive(-70.0, ref_power=10.0)
        assert isinstance(result, float)
        # Filter should still work (using default ref_power)
        assert kf.sample_count == 1

    def test_invalid_too_negative_ref_power_uses_default(self) -> None:
        """ref_power below -100 should fall back to default -55."""
        kf = KalmanFilter()
        result = kf.update_adaptive(-70.0, ref_power=-150.0)
        assert isinstance(result, float)
        assert kf.sample_count == 1


class TestFilterFactory:
    """Tests for filter factory method."""

    def test_create_kalman_filter(self) -> None:
        """Factory should create KalmanFilter for 'kalman' type."""
        f = create_filter("kalman")
        assert isinstance(f, KalmanFilter)
        assert isinstance(f, SignalFilter)

    def test_create_adaptive_filter(self) -> None:
        """Factory should create AdaptiveRobustFilter for 'adaptive' type."""
        from custom_components.bermuda.filters import AdaptiveRobustFilter

        f = create_filter("adaptive")
        assert isinstance(f, AdaptiveRobustFilter)

    def test_create_ukf(self) -> None:
        """Factory should create UnscentedKalmanFilter for 'ukf' type."""
        f = create_filter("ukf")
        assert isinstance(f, UnscentedKalmanFilter)

    def test_create_with_config(self) -> None:
        """Factory should use provided config."""
        config = FilterConfig(process_noise=0.1, measurement_noise=10.0)
        f = create_filter("kalman", config)
        assert isinstance(f, KalmanFilter)
        assert f.process_noise == 0.1
        assert f.measurement_noise == 10.0

    def test_invalid_type_raises_error(self) -> None:
        """Factory should raise ValueError for unknown filter type."""
        with pytest.raises(ValueError, match="Unknown filter type"):
            create_filter("invalid_type")


class TestKalmanSerialization:
    """Tests for KalmanFilter serialization."""

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict should contain all necessary fields."""
        kf = KalmanFilter()
        kf.update(-70.0)
        kf.update(-72.0)

        data = kf.to_dict()

        assert "estimate" in data
        assert "variance" in data
        assert "sample_count" in data
        assert "process_noise" in data
        assert "measurement_noise" in data

    def test_from_dict_restores_state(self) -> None:
        """from_dict should restore filter state exactly."""
        # Create and train a filter
        kf1 = KalmanFilter()
        for rssi in [-70, -72, -68, -71]:
            kf1.update(rssi)

        # Serialize and deserialize
        data = kf1.to_dict()
        kf2 = KalmanFilter.from_dict(data)

        # State should match
        assert kf2.estimate == kf1.estimate
        assert kf2.variance == kf1.variance
        assert kf2.sample_count == kf1.sample_count
        assert kf2.is_initialized

    def test_roundtrip_preserves_behavior(self) -> None:
        """Serialized filter should produce same results."""
        kf1 = KalmanFilter()
        kf1.update(-70.0)

        kf2 = KalmanFilter.from_dict(kf1.to_dict())

        # Both should produce same result for same measurement
        result1 = kf1.update(-72.0)
        result2 = kf2.update(-72.0)

        assert result1 == result2


# =============================================================================
# Phase 2: Time-Aware Filtering Tests
# =============================================================================


class TestTimeAwareKalman:
    """Tests for time-aware Kalman filtering."""

    def test_longer_gap_increases_variance(self) -> None:
        """Longer time gaps should increase variance before update."""
        # Filter with 1s gap
        kf1 = KalmanFilter()
        kf1.update(-70.0, timestamp=0.0)
        kf1.update(-70.0, timestamp=1.0)
        var_1s = kf1.variance

        # Filter with 10s gap
        kf2 = KalmanFilter()
        kf2.update(-70.0, timestamp=0.0)
        kf2.update(-70.0, timestamp=10.0)
        var_10s = kf2.variance

        # Longer gap should result in higher variance (more uncertainty)
        # Note: After update, variance decreases, but 10s gap should still be higher
        # due to process noise scaling
        assert var_10s > var_1s

    def test_no_timestamp_uses_default_dt(self) -> None:
        """Without timestamp, filter should use DEFAULT_UPDATE_DT."""
        kf = KalmanFilter()
        kf.update(-70.0)
        kf.update(-70.0)
        # Should not crash
        assert kf.sample_count == 2

    def test_very_short_gap_clamped(self) -> None:
        """Very short time gaps should be clamped to MIN_UPDATE_DT."""
        kf = KalmanFilter()
        kf.update(-70.0, timestamp=0.0)
        kf.update(-70.0, timestamp=0.001)  # 1ms gap
        # Should not crash, dt clamped to MIN_UPDATE_DT
        assert kf.sample_count == 2

    def test_very_long_gap_clamped(self) -> None:
        """Very long time gaps should be clamped to MAX_UPDATE_DT."""
        kf = KalmanFilter()
        kf.update(-70.0, timestamp=0.0)
        kf.update(-70.0, timestamp=1000.0)  # 1000s gap
        # Variance should not explode
        assert kf.variance < 1000  # Reasonable bound

    def test_reset_clears_timestamp(self) -> None:
        """Reset should clear last timestamp."""
        kf = KalmanFilter()
        kf.update(-70.0, timestamp=100.0)
        kf.reset()

        assert kf._last_timestamp is None


class TestTimeAwareUKF:
    """Tests for time-aware UKF filtering."""

    def test_update_multi_with_timestamp(self) -> None:
        """update_multi should accept and use timestamp."""
        ukf = UnscentedKalmanFilter()
        measurements = {"scanner1": -70.0, "scanner2": -75.0}

        ukf.update_multi(measurements, timestamp=0.0)
        ukf.update_multi(measurements, timestamp=1.0)

        assert ukf.sample_count == 2
        assert ukf._last_timestamp == 1.0

    def test_longer_gap_increases_ukf_variance(self) -> None:
        """Longer time gaps should increase UKF covariance."""
        measurements = {"s1": -70.0, "s2": -75.0}

        # UKF with 1s gap
        ukf1 = UnscentedKalmanFilter()
        ukf1.update_multi(measurements, timestamp=0.0)
        ukf1.update_multi(measurements, timestamp=1.0)
        var1 = ukf1.get_variance()

        # UKF with 10s gap
        ukf2 = UnscentedKalmanFilter()
        ukf2.update_multi(measurements, timestamp=0.0)
        ukf2.update_multi(measurements, timestamp=10.0)
        var2 = ukf2.get_variance()

        # Longer gap should have higher average variance
        assert var2 > var1

    def test_reset_clears_ukf_timestamp(self) -> None:
        """UKF reset should clear last timestamp."""
        ukf = UnscentedKalmanFilter()
        ukf.update_multi({"s1": -70.0}, timestamp=100.0)
        ukf.reset()

        assert ukf._last_timestamp is None


# =============================================================================
# Phase 3: Performance Optimization Tests
# =============================================================================


class TestNumPyBackend:
    """Tests for optional NumPy backend."""

    def test_is_numpy_available_returns_bool(self) -> None:
        """is_numpy_available should return bool."""
        result = is_numpy_available()
        assert isinstance(result, bool)

    @pytest.mark.skipif(not is_numpy_available(), reason="NumPy not installed")
    def test_numpy_cholesky_matches_pure_python(self) -> None:
        """NumPy Cholesky should produce same result as pure Python."""
        from custom_components.bermuda.filters.ukf import _cholesky_decompose
        from custom_components.bermuda.filters.ukf_numpy import cholesky_numpy

        # Create a positive definite matrix
        matrix = [
            [4.0, 2.0, 0.5],
            [2.0, 5.0, 1.0],
            [0.5, 1.0, 3.0],
        ]

        result_py = _cholesky_decompose(matrix)
        result_np = cholesky_numpy(matrix)

        assert result_np is not None

        # Compare results (allow small numerical differences)
        for i in range(3):
            for j in range(3):
                assert abs(result_py[i][j] - result_np[i][j]) < 1e-6

    @pytest.mark.skipif(not is_numpy_available(), reason="NumPy not installed")
    def test_numpy_matrix_inverse_matches_pure_python(self) -> None:
        """NumPy matrix inverse should produce same result as pure Python."""
        from custom_components.bermuda.filters.ukf import _matrix_inverse
        from custom_components.bermuda.filters.ukf_numpy import matrix_inverse_numpy

        matrix = [
            [4.0, 2.0, 0.5],
            [2.0, 5.0, 1.0],
            [0.5, 1.0, 3.0],
        ]

        result_py = _matrix_inverse(matrix)
        result_np = matrix_inverse_numpy(matrix)

        assert result_np is not None

        for i in range(3):
            for j in range(3):
                assert abs(result_py[i][j] - result_np[i][j]) < 1e-5


class TestSequentialUpdate:
    """Tests for sequential update method."""

    def test_sequential_update_produces_valid_state(self) -> None:
        """Sequential update should produce valid state vector."""
        ukf = UnscentedKalmanFilter()
        measurements = {"s1": -70.0, "s2": -75.0, "s3": -80.0}

        result = ukf.update_sequential(measurements)

        assert len(result) == 3
        assert ukf.sample_count == 1
        assert ukf.n_scanners == 3

    def test_sequential_update_with_timestamp(self) -> None:
        """Sequential update should support timestamps."""
        ukf = UnscentedKalmanFilter()
        measurements = {"s1": -70.0, "s2": -75.0}

        ukf.update_sequential(measurements, timestamp=0.0)
        ukf.update_sequential(measurements, timestamp=1.0)

        assert ukf._last_timestamp == 1.0
        assert ukf.sample_count == 2

    def test_sequential_vs_multi_similar_results(self) -> None:
        """Sequential and multi update should produce similar results."""
        measurements = {"s1": -70.0, "s2": -75.0}

        # Multi update
        ukf1 = UnscentedKalmanFilter()
        ukf1.update_multi(measurements)
        state1 = ukf1.state

        # Sequential update
        ukf2 = UnscentedKalmanFilter()
        ukf2.update_sequential(measurements)
        state2 = ukf2.state

        # Results should be similar (not exact due to different algorithms)
        # The key is that both converge to reasonable estimates
        for i in range(len(state1)):
            assert abs(state1[i] - state2[i]) < 5.0  # Within 5 dB

    def test_partial_observations_work(self) -> None:
        """Sequential update should handle partial observations."""
        ukf = UnscentedKalmanFilter()

        # First update with 3 scanners
        ukf.update_sequential({"s1": -70.0, "s2": -75.0, "s3": -80.0})

        # Second update with only 2 scanners
        ukf.update_sequential({"s1": -71.0, "s3": -79.0})

        assert ukf.n_scanners == 3
        assert ukf.sample_count == 2


class TestUKFPerformance:
    """Performance-related tests for UKF."""

    @pytest.mark.parametrize("n_scanners", [5, 10, 15])
    def test_update_completes(self, n_scanners: int) -> None:
        """UKF update should complete for various scanner counts."""
        ukf = UnscentedKalmanFilter()
        measurements = {f"scanner_{i}": -70.0 - i for i in range(n_scanners)}

        # Run a few updates
        for _ in range(5):
            result = ukf.update_multi(measurements)

        assert len(result) == n_scanners
        assert ukf.sample_count == 5

    def test_numpy_used_consistently_if_available(self) -> None:
        """NumPy should be used for ALL scanner counts if available (consistent behavior)."""
        from custom_components.bermuda.filters.ukf import USE_NUMPY_IF_AVAILABLE
        from custom_components.bermuda.filters.ukf_numpy import is_numpy_available

        # Verify the flag is set correctly
        assert USE_NUMPY_IF_AVAILABLE is True

        # Test with small scanner count (3)
        ukf_small = UnscentedKalmanFilter()
        measurements_small = {f"s{i}": -70.0 for i in range(3)}
        ukf_small.update_multi(measurements_small)
        assert ukf_small.n_scanners == 3

        # Test with large scanner count (25)
        ukf_large = UnscentedKalmanFilter()
        measurements_large = {f"s{i}": -70.0 for i in range(25)}
        ukf_large.update_multi(measurements_large)
        assert ukf_large.n_scanners == 25

        # Both should work identically - NumPy used for both if available
        # This ensures consistent behavior across all installations


# =============================================================================
# Integration Tests
# =============================================================================


class TestFilterIntegration:
    """Integration tests combining multiple features."""

    def test_full_workflow_with_serialization(self) -> None:
        """Test complete workflow: create, train, serialize, restore, continue."""
        # Create via factory
        kf1 = create_filter("kalman")
        assert isinstance(kf1, KalmanFilter)

        # Train with timestamps
        for i, rssi in enumerate([-70, -72, -68, -71]):
            kf1.update(rssi, timestamp=float(i))

        # Serialize
        data = kf1.to_dict()

        # Restore
        kf2 = KalmanFilter.from_dict(data)

        # Continue training
        result1 = kf1.update(-73.0, timestamp=5.0)
        result2 = kf2.update(-73.0, timestamp=5.0)

        assert result1 == result2

    def test_ukf_with_time_aware_sequential(self) -> None:
        """Test UKF with time-aware sequential updates."""
        ukf = UnscentedKalmanFilter()

        # Simulate irregular BLE advertisements
        times = [0.0, 1.2, 2.1, 5.5, 6.0]  # Irregular intervals
        for t in times:
            measurements = {"s1": -70 + (t % 3), "s2": -75 - (t % 2)}
            ukf.update_sequential(measurements, timestamp=t)

        assert ukf.sample_count == len(times)
        assert ukf._last_timestamp == times[-1]
