"""Tests for Unscented Kalman Filter implementation."""

from __future__ import annotations

import math

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.filters.ukf import (
    DEFAULT_RSSI,
    MIN_VARIANCE,
    UnscentedKalmanFilter,
    _cholesky_decompose,
    _identity_matrix,
    _matrix_add,
    _matrix_inverse,
    _matrix_multiply,
    _matrix_transpose,
    _outer_product,
)


class TestMatrixOperations:
    """Tests for matrix utility functions."""

    def test_identity_matrix(self) -> None:
        """Test identity matrix creation."""
        ident = _identity_matrix(3)
        assert ident == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    def test_identity_matrix_scaled(self) -> None:
        """Test scaled identity matrix."""
        ident = _identity_matrix(2, scale=5.0)
        assert ident == [[5.0, 0.0], [0.0, 5.0]]

    def test_matrix_add(self) -> None:
        """Test matrix addition."""
        a = [[1.0, 2.0], [3.0, 4.0]]
        b = [[5.0, 6.0], [7.0, 8.0]]
        result = _matrix_add(a, b)
        assert result == [[6.0, 8.0], [10.0, 12.0]]

    def test_matrix_add_scaled(self) -> None:
        """Test matrix addition with scaling."""
        a = [[1.0, 2.0], [3.0, 4.0]]
        b = [[1.0, 1.0], [1.0, 1.0]]
        result = _matrix_add(a, b, scale_b=-1.0)
        assert result == [[0.0, 1.0], [2.0, 3.0]]

    def test_matrix_multiply(self) -> None:
        """Test matrix multiplication."""
        a = [[1.0, 2.0], [3.0, 4.0]]
        b = [[5.0, 6.0], [7.0, 8.0]]
        result = _matrix_multiply(a, b)
        assert result == [[19.0, 22.0], [43.0, 50.0]]

    def test_matrix_transpose(self) -> None:
        """Test matrix transpose."""
        a = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        result = _matrix_transpose(a)
        assert result == [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]

    def test_matrix_inverse(self) -> None:
        """Test matrix inverse."""
        a = [[4.0, 7.0], [2.0, 6.0]]
        inv = _matrix_inverse(a)
        # a @ inv should be identity
        product = _matrix_multiply(a, inv)
        assert abs(product[0][0] - 1.0) < 1e-6
        assert abs(product[0][1]) < 1e-6
        assert abs(product[1][0]) < 1e-6
        assert abs(product[1][1] - 1.0) < 1e-6

    def test_outer_product(self) -> None:
        """Test outer product of vectors."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0]
        result = _outer_product(a, b)
        assert result == [[4.0, 5.0], [8.0, 10.0], [12.0, 15.0]]

    def test_cholesky_decompose(self) -> None:
        """Test Cholesky decomposition."""
        # Positive definite matrix
        a = [[4.0, 2.0], [2.0, 5.0]]
        lower = _cholesky_decompose(a)
        # lower @ lower.T should equal a
        lower_t = _matrix_transpose(lower)
        reconstructed = _matrix_multiply(lower, lower_t)
        assert abs(reconstructed[0][0] - 4.0) < 1e-6
        assert abs(reconstructed[0][1] - 2.0) < 1e-6
        assert abs(reconstructed[1][0] - 2.0) < 1e-6
        assert abs(reconstructed[1][1] - 5.0) < 1e-6


class TestUKFInitialization:
    """Tests for UKF initialization."""

    def test_empty_initialization(self) -> None:
        """Test UKF with no scanners."""
        ukf = UnscentedKalmanFilter()
        assert ukf.n_scanners == 0
        assert not ukf._initialized
        assert ukf.state == []

    def test_initialization_with_scanners(self) -> None:
        """Test UKF with initial scanners."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
        assert ukf.n_scanners == 2
        assert ukf._initialized
        assert len(ukf.state) == 2
        assert all(x == DEFAULT_RSSI for x in ukf.state)

    def test_add_scanner(self) -> None:
        """Test adding scanners dynamically."""
        ukf = UnscentedKalmanFilter()
        idx1 = ukf.add_scanner("AA:BB:CC:DD:EE:01")
        idx2 = ukf.add_scanner("AA:BB:CC:DD:EE:02")
        assert idx1 == 0
        assert idx2 == 1
        assert ukf.n_scanners == 2

    def test_add_duplicate_scanner(self) -> None:
        """Test adding the same scanner twice returns existing index."""
        ukf = UnscentedKalmanFilter()
        idx1 = ukf.add_scanner("AA:BB:CC:DD:EE:01")
        idx2 = ukf.add_scanner("AA:BB:CC:DD:EE:01")
        assert idx1 == idx2 == 0
        assert ukf.n_scanners == 1


class TestUKFPredictUpdate:
    """Tests for UKF predict and update steps."""

    def test_predict_increases_covariance(self) -> None:
        """Test that predict step increases uncertainty."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        initial_var = ukf.covariance[0][0]

        ukf.predict(dt=1.0)
        after_predict_var = ukf.covariance[0][0]

        assert after_predict_var > initial_var

    def test_update_multi_converges(self) -> None:
        """Test that update_multi converges state to measurements."""
        ukf = UnscentedKalmanFilter()

        # Feed same measurement multiple times
        for _ in range(20):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0, "AA:BB:CC:DD:EE:02": -75.0})

        state = ukf.state
        # State should converge toward measurements
        assert abs(state[0] - (-65.0)) < 5.0
        assert abs(state[1] - (-75.0)) < 5.0

    def test_update_multi_reduces_variance(self) -> None:
        """Test that measurements reduce uncertainty."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        initial_var = ukf.covariance[0][0]

        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})
        after_var = ukf.covariance[0][0]

        assert after_var < initial_var

    def test_partial_observation(self) -> None:
        """Test update with partial observations."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"])

        # Only observe first scanner
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -60.0})

        # First scanner should have updated, others should retain default
        state = ukf.state
        assert state[0] != DEFAULT_RSSI  # Updated
        # Second and third should be closer to default (uncertainty increased slightly)

    def test_sample_count_increments(self) -> None:
        """Test that sample count tracks updates."""
        ukf = UnscentedKalmanFilter()
        assert ukf.sample_count == 0

        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})
        assert ukf.sample_count == 1

        ukf.update_multi({"AA:BB:CC:DD:EE:01": -66.0})
        assert ukf.sample_count == 2


class TestUKFInterface:
    """Tests for SignalFilter interface compliance."""

    def test_get_estimate(self) -> None:
        """Test get_estimate returns mean of state."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
        # Default state is all DEFAULT_RSSI
        assert ukf.get_estimate() == DEFAULT_RSSI

    def test_get_variance(self) -> None:
        """Test get_variance returns average diagonal variance."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        var = ukf.get_variance()
        assert var > 0

    def test_reset(self) -> None:
        """Test reset clears all state."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})

        ukf.reset()

        assert ukf.n_scanners == 0
        assert ukf.sample_count == 0
        assert not ukf._initialized

    def test_get_diagnostics(self) -> None:
        """Test diagnostics output."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})

        diag = ukf.get_diagnostics()

        assert "n_scanners" in diag
        assert diag["n_scanners"] == 1
        assert "state" in diag
        assert "variances" in diag
        assert "avg_variance" in diag


class TestUKFFingerprintMatching:
    """Tests for fingerprint matching functionality."""

    def test_match_fingerprints_empty(self) -> None:
        """Test matching with no profiles."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})

        results = ukf.match_fingerprints({})
        assert results == []

    def test_match_fingerprints_requires_overlap(self) -> None:
        """Test that matching requires at least 2 overlapping scanners."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0})

        # Create profile with only 1 scanner (not enough overlap)
        profile = AreaProfile(area_id="area_kitchen")
        profile.update(primary_rssi=-65.0, other_readings={}, primary_scanner_addr="AA:BB:CC:DD:EE:01")

        # Even after multiple updates, 1 scanner isn't enough
        for _ in range(30):
            profile.update(primary_rssi=-65.0, other_readings={}, primary_scanner_addr="AA:BB:CC:DD:EE:01")

        results = ukf.match_fingerprints({"area_kitchen": profile})
        assert results == []  # Not enough overlap (need 2+ scanners)

    def test_match_fingerprints_with_overlap(self) -> None:
        """Test matching with sufficient scanner overlap."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        # Train UKF with measurements
        for _ in range(10):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0, "AA:BB:CC:DD:EE:02": -75.0})

        # Create profile with same scanners
        profile = AreaProfile(area_id="area_kitchen")
        for _ in range(30):  # Enough samples to be mature
            profile.update(
                primary_rssi=-65.0,
                other_readings={"AA:BB:CC:DD:EE:02": -75.0},
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints({"area_kitchen": profile})

        # Should match well since measurements match learned profile
        assert len(results) >= 1
        area_id, d_squared, match_score = results[0]
        assert area_id == "area_kitchen"
        assert match_score > 0.5  # Good match

    def test_match_fingerprints_distinguishes_areas(self) -> None:
        """Test that matching can distinguish between different areas."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        # Train UKF to look like kitchen
        for _ in range(10):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0, "AA:BB:CC:DD:EE:02": -75.0})

        # Kitchen profile (matches UKF state)
        kitchen = AreaProfile(area_id="area_kitchen")
        for _ in range(30):
            kitchen.update(
                primary_rssi=-65.0,
                other_readings={"AA:BB:CC:DD:EE:02": -75.0},
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        # Bedroom profile (very different RSSI pattern)
        bedroom = AreaProfile(area_id="area_bedroom")
        for _ in range(30):
            bedroom.update(
                primary_rssi=-85.0,  # Much weaker
                other_readings={"AA:BB:CC:DD:EE:02": -55.0},  # Much stronger
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints({"area_kitchen": kitchen, "area_bedroom": bedroom})

        # Kitchen should be best match
        assert len(results) == 2
        best_area, _, best_score = results[0]
        assert best_area == "area_kitchen"

        # Kitchen score should be much higher than bedroom
        kitchen_score = next(r[2] for r in results if r[0] == "area_kitchen")
        bedroom_score = next(r[2] for r in results if r[0] == "area_bedroom")
        assert kitchen_score > bedroom_score


class TestUKFNumericalStability:
    """Tests for numerical stability edge cases."""

    def test_covariance_stays_positive(self) -> None:
        """Test covariance diagonal stays positive after many updates."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])

        for i in range(100):
            ukf.predict(dt=0.1)
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0 + (i % 10) - 5})

        # All diagonal elements should be positive
        cov = ukf.covariance
        for i in range(ukf.n_scanners):
            assert cov[i][i] >= MIN_VARIANCE

    def test_empty_measurements(self) -> None:
        """Test update with empty measurements."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])
        initial_state = ukf.state.copy()

        result = ukf.update_multi({})

        assert result == initial_state

    def test_large_innovation(self) -> None:
        """Test handling of large measurement jumps."""
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01"])

        # Start with one measurement
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -50.0})

        # Jump to very different measurement
        ukf.update_multi({"AA:BB:CC:DD:EE:01": -90.0})

        # Should not crash and state should move toward new measurement
        state = ukf.state
        assert state[0] < -50.0  # Moved toward -90
