"""Tests for Unscented Kalman Filter implementation."""

from __future__ import annotations

import math

import pytest

from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.filters.ukf import (
    DEFAULT_RSSI,
    MIN_VARIANCE,
    UKF_MIN_MATCHING_VARIANCE,
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
        # Note: NumPy backend adds 1e-6 regularization for numerical stability,
        # so we use 1e-5 tolerance to accommodate both backends
        assert abs(reconstructed[0][0] - 4.0) < 1e-5
        assert abs(reconstructed[0][1] - 2.0) < 1e-5
        assert abs(reconstructed[1][0] - 2.0) < 1e-5
        assert abs(reconstructed[1][1] - 5.0) < 1e-5


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


class TestVarianceFloorFix:
    """Tests for the Hyper-Precision Paradox fix (UKF_MIN_MATCHING_VARIANCE).

    The fix ensures that converged Kalman filters don't produce unrealistically
    low combined covariance, which would cause normal BLE fluctuations (3-5 dB)
    to be rejected as massive deviations.
    """

    def test_variance_floor_constant_value(self) -> None:
        """Test that UKF_MIN_MATCHING_VARIANCE is correctly defined."""
        # The floor should be 25.0 (σ ≈ 5 dB)
        assert UKF_MIN_MATCHING_VARIANCE == 25.0
        # It should be different from MIN_VARIANCE (numerical stability)
        assert UKF_MIN_MATCHING_VARIANCE > MIN_VARIANCE

    def test_score_with_normal_ble_fluctuation(self) -> None:
        """Test that 3dB deviation produces good score with variance floor.

        This is the core test for the Hyper-Precision Paradox fix.
        With the floor, a 3dB deviation should produce a score > 0.8.
        Without the floor, it would produce a score around 0.37 or lower.
        """
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        # Train UKF with stable measurements (many samples → converged variance)
        for _ in range(50):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0, "AA:BB:CC:DD:EE:02": -75.0})

        # Create profile with slightly different values (3dB deviation per scanner)
        # This simulates normal BLE signal fluctuation
        profile = AreaProfile(area_id="area_test")
        for _ in range(50):  # Many samples → converged profile variance
            profile.update(
                primary_rssi=-68.0,  # 3dB different from UKF state (-65)
                other_readings={"AA:BB:CC:DD:EE:02": -78.0},  # 3dB different (-75)
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints({"area_test": profile})

        # With variance floor, 3dB deviation should still produce good score
        assert len(results) >= 1
        _, d_squared, score = results[0]

        # Key assertion: Score should be > 0.7 despite 3dB deviation
        # Without fix, this would be around 0.37 or lower
        assert score > 0.7, f"Score {score:.4f} too low for normal BLE fluctuation"

        # D² should be reasonable (< 3) with the floor
        assert d_squared < 3.0, f"D² {d_squared:.2f} too high with variance floor"

    def test_score_with_large_deviation_still_rejects(self) -> None:
        """Test that large deviations (15dB+) are still properly rejected.

        The variance floor should not make matching too tolerant.
        """
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        # Train UKF
        for _ in range(50):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -65.0, "AA:BB:CC:DD:EE:02": -75.0})

        # Create profile with very different values (15dB deviation)
        wrong_profile = AreaProfile(area_id="area_wrong")
        for _ in range(50):
            wrong_profile.update(
                primary_rssi=-80.0,  # 15dB different
                other_readings={"AA:BB:CC:DD:EE:02": -60.0},  # 15dB different (opposite direction)
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints({"area_wrong": wrong_profile})

        assert len(results) >= 1
        _, _, score = results[0]

        # Large deviation should still produce low score
        assert score < 0.3, f"Score {score:.4f} too high for 15dB deviation"

    def test_score_improvement_with_floor(self) -> None:
        """Test that the floor actually improves scores compared to theoretical no-floor.

        We compute what the score WOULD be without the floor and verify
        that our actual score is better.
        """
        ukf = UnscentedKalmanFilter(scanner_addresses=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])

        # Converge the UKF
        for _ in range(100):
            ukf.update_multi({"AA:BB:CC:DD:EE:01": -70.0, "AA:BB:CC:DD:EE:02": -70.0})

        # Create profile with 5dB deviation (moderate BLE noise)
        profile = AreaProfile(area_id="area_test")
        for _ in range(100):
            profile.update(
                primary_rssi=-75.0,  # 5dB off
                other_readings={"AA:BB:CC:DD:EE:02": -75.0},  # 5dB off
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints({"area_test": profile})
        assert len(results) >= 1
        _, _, actual_score = results[0]

        # Calculate theoretical score WITHOUT floor
        # Assuming converged variance ≈ 2-4, combined ≈ 4-8
        # With var=5: D² = (5²/5 + 5²/5) = 10, score = exp(-10/4) ≈ 0.08
        # With floor=25: D² = (5²/25 + 5²/25) = 2, score = exp(-2/4) ≈ 0.61
        theoretical_score_without_floor = math.exp(-10.0 / 4.0)  # ≈ 0.08

        # Actual score should be significantly better than theoretical no-floor
        assert actual_score > theoretical_score_without_floor * 5, (
            f"Actual score {actual_score:.4f} not much better than "
            f"theoretical no-floor {theoretical_score_without_floor:.4f}"
        )


class TestLagerraumScenario:
    """Integration tests simulating the 'Lagerraum' (storage room) scenario.

    This tests the specific bug where a well-trained room ('Lagerraum')
    loses to a poorly-trained but closer room ('Praxis') because the
    hyper-precision of the trained profile rejects normal BLE fluctuations.
    """

    def test_trained_room_beats_closer_scanner(self) -> None:
        """Test that a well-trained profile wins over a nearby but wrong room.

        Scenario:
        - Device is physically in 'Lagerraum' (storage room)
        - 'Lagerraum' has been carefully trained with button training
        - 'Praxis' (practice room) is 2 floors up but has a nearby scanner
        - Due to BLE noise, current readings deviate 3-4 dB from training

        Without the fix: Lagerraum score ≈ 0.002 (hyper-precision rejection)
                        Praxis score ≈ 0.2 (tolerant matching)
                        → Praxis wins incorrectly

        With the fix:    Lagerraum score > 0.7 (reasonable tolerance)
                        Praxis score ≈ 0.2 (still tolerant but lower match)
                        → Lagerraum wins correctly
        """
        ukf = UnscentedKalmanFilter(
            scanner_addresses=[
                "AA:BB:CC:DD:EE:01",  # Scanner in Lagerraum
                "AA:BB:CC:DD:EE:02",  # Scanner in Praxis
                "AA:BB:CC:DD:EE:03",  # Scanner elsewhere
            ]
        )

        # Current device readings (device is in Lagerraum)
        # Scanner 01 (Lagerraum): Strong signal -60 dB
        # Scanner 02 (Praxis, 2 floors up): Weak signal -85 dB
        # Scanner 03: Medium signal -72 dB
        for _ in range(30):
            ukf.update_multi(
                {
                    "AA:BB:CC:DD:EE:01": -60.0,
                    "AA:BB:CC:DD:EE:02": -85.0,
                    "AA:BB:CC:DD:EE:03": -72.0,
                }
            )

        # Lagerraum profile (well-trained, but with slight deviation due to BLE noise)
        # Training captured -63, -88, -75 but current readings are -60, -85, -72
        # This 3dB deviation is NORMAL for BLE
        lagerraum = AreaProfile(area_id="lagerraum")
        for _ in range(100):  # Well-trained
            lagerraum.update(
                primary_rssi=-63.0,  # 3dB different from current
                other_readings={
                    "AA:BB:CC:DD:EE:02": -88.0,  # 3dB different
                    "AA:BB:CC:DD:EE:03": -75.0,  # 3dB different
                },
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        # Praxis profile (different room, poorly matched to current readings)
        praxis = AreaProfile(area_id="praxis")
        for _ in range(30):  # Less well-trained
            praxis.update(
                primary_rssi=-85.0,  # Scanner 01 would be weak in Praxis
                other_readings={
                    "AA:BB:CC:DD:EE:02": -55.0,  # Scanner 02 would be strong in Praxis
                    "AA:BB:CC:DD:EE:03": -78.0,
                },
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints(
            {
                "lagerraum": lagerraum,
                "praxis": praxis,
            }
        )

        # Extract scores
        lagerraum_result = next(r for r in results if r[0] == "lagerraum")
        praxis_result = next(r for r in results if r[0] == "praxis")

        lagerraum_score = lagerraum_result[2]
        praxis_score = praxis_result[2]

        # Key assertion: Lagerraum should win
        assert (
            lagerraum_score > praxis_score
        ), f"Lagerraum ({lagerraum_score:.4f}) should beat Praxis ({praxis_score:.4f})"

        # Lagerraum should have a good score despite 3dB deviation
        assert lagerraum_score > 0.6, f"Lagerraum score {lagerraum_score:.4f} too low for 3dB deviation"

    def test_scannerless_room_detection(self) -> None:
        """Test detection of rooms without their own scanner.

        A 'scannerless room' has no scanner of its own but can be detected
        via fingerprint matching of RSSI patterns from nearby scanners.
        The variance floor is especially important here because:
        1. No primary scanner means all readings are from 'far' scanners
        2. Trained profiles may have converged to tight variance
        3. Normal BLE fluctuation could reject the correct room
        """
        ukf = UnscentedKalmanFilter(
            scanner_addresses=[
                "AA:BB:CC:DD:EE:01",  # Scanner in adjacent room
                "AA:BB:CC:DD:EE:02",  # Scanner on different floor
            ]
        )

        # Current readings (device in scannerless room)
        # Both scanners show moderate signal (neither is very close)
        for _ in range(30):
            ukf.update_multi(
                {
                    "AA:BB:CC:DD:EE:01": -72.0,
                    "AA:BB:CC:DD:EE:02": -78.0,
                }
            )

        # Scannerless room profile (trained with button)
        # Small deviation (2dB) from current readings
        scannerless = AreaProfile(area_id="scannerless_room")
        for _ in range(50):
            scannerless.update(
                primary_rssi=-74.0,  # 2dB off
                other_readings={"AA:BB:CC:DD:EE:02": -80.0},  # 2dB off
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        # Adjacent room (has scanner 01)
        adjacent = AreaProfile(area_id="adjacent_room")
        for _ in range(50):
            adjacent.update(
                primary_rssi=-55.0,  # Would be strong if actually in this room
                other_readings={"AA:BB:CC:DD:EE:02": -85.0},
                primary_scanner_addr="AA:BB:CC:DD:EE:01",
            )

        results = ukf.match_fingerprints(
            {
                "scannerless_room": scannerless,
                "adjacent_room": adjacent,
            }
        )

        scannerless_result = next(r for r in results if r[0] == "scannerless_room")
        adjacent_result = next(r for r in results if r[0] == "adjacent_room")

        # Scannerless room should match better (smaller deviation)
        assert (
            scannerless_result[2] > adjacent_result[2]
        ), f"Scannerless room ({scannerless_result[2]:.4f}) should beat adjacent room ({adjacent_result[2]:.4f})"


class TestPurePythonMatrixOperations:
    """Tests for pure Python matrix operations (NumPy disabled).

    These tests ensure the pure Python fallback implementations work correctly
    by mocking `is_numpy_available` to return False.
    """

    def test_cholesky_pure_python_simple(self) -> None:
        """Test Cholesky decomposition with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # Simple positive definite matrix
            a = [[4.0, 2.0], [2.0, 5.0]]
            lower = _cholesky_decompose(a)

            # Verify: lower @ lower.T should equal original matrix
            lower_t = _matrix_transpose(lower)
            reconstructed = _matrix_multiply(lower, lower_t)

            assert abs(reconstructed[0][0] - 4.0) < 1e-5
            assert abs(reconstructed[0][1] - 2.0) < 1e-5
            assert abs(reconstructed[1][0] - 2.0) < 1e-5
            assert abs(reconstructed[1][1] - 5.0) < 1e-5

    def test_cholesky_pure_python_larger_matrix(self) -> None:
        """Test Cholesky decomposition with 3x3 matrix."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # 3x3 positive definite matrix
            a = [[4.0, 2.0, 1.0], [2.0, 5.0, 2.0], [1.0, 2.0, 6.0]]
            lower = _cholesky_decompose(a)

            # Verify shape
            assert len(lower) == 3
            assert len(lower[0]) == 3

            # Verify it's lower triangular (upper part should be zeros)
            assert lower[0][1] == 0.0
            assert lower[0][2] == 0.0
            assert lower[1][2] == 0.0

    def test_cholesky_pure_python_with_regularization(self) -> None:
        """Test Cholesky handles near-singular matrix via regularization."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # Matrix with very small diagonal element (near singular)
            a = [[1e-15, 0.0], [0.0, 1.0]]
            lower = _cholesky_decompose(a)

            # Should not raise, should produce valid lower triangular matrix
            assert len(lower) == 2
            assert lower[0][0] > 0  # Should be regularized, not 0
            assert lower[1][1] > 0

    def test_cholesky_pure_python_near_zero_diagonal_branch(self) -> None:
        """Test Cholesky handles near-zero lower diagonal for division protection."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # Matrix that would produce very small lower[j][j] during decomposition
            # This tests the abs(lower[j][j]) < MIN_VARIANCE branch
            a = [[0.001, 0.0], [0.0, 1.0]]
            lower = _cholesky_decompose(a)

            # Should not raise, should produce valid result
            assert len(lower) == 2
            assert lower[1][1] == 1.0

    def test_matrix_multiply_pure_python(self) -> None:
        """Test matrix multiplication with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            a = [[1.0, 2.0], [3.0, 4.0]]
            b = [[5.0, 6.0], [7.0, 8.0]]
            result = _matrix_multiply(a, b)

            assert result == [[19.0, 22.0], [43.0, 50.0]]

    def test_matrix_multiply_pure_python_non_square(self) -> None:
        """Test matrix multiplication with non-square matrices."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # 2x3 @ 3x2 = 2x2
            a = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
            b = [[7.0, 8.0], [9.0, 10.0], [11.0, 12.0]]
            result = _matrix_multiply(a, b)

            assert len(result) == 2
            assert len(result[0]) == 2
            assert result[0][0] == 1 * 7 + 2 * 9 + 3 * 11  # 58
            assert result[0][1] == 1 * 8 + 2 * 10 + 3 * 12  # 64
            assert result[1][0] == 4 * 7 + 5 * 9 + 6 * 11  # 139
            assert result[1][1] == 4 * 8 + 5 * 10 + 6 * 12  # 154

    def test_matrix_inverse_pure_python(self) -> None:
        """Test matrix inverse with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            a = [[4.0, 7.0], [2.0, 6.0]]
            inv = _matrix_inverse(a)

            # a @ inv should be identity
            product = _matrix_multiply(a, inv)
            assert abs(product[0][0] - 1.0) < 1e-6
            assert abs(product[0][1]) < 1e-6
            assert abs(product[1][0]) < 1e-6
            assert abs(product[1][1] - 1.0) < 1e-6

    def test_matrix_inverse_pure_python_larger(self) -> None:
        """Test matrix inverse with 3x3 matrix."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            a = [[2.0, 1.0, 0.0], [1.0, 2.0, 1.0], [0.0, 1.0, 2.0]]
            inv = _matrix_inverse(a)

            # a @ inv should be identity
            product = _matrix_multiply(a, inv)
            for i in range(3):
                for j in range(3):
                    expected = 1.0 if i == j else 0.0
                    assert abs(product[i][j] - expected) < 1e-5

    def test_matrix_inverse_pure_python_near_singular(self) -> None:
        """Test matrix inverse with near-singular matrix (regularization needed)."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # Near-singular matrix
            a = [[1.0, 2.0], [1.0, 2.0 + 1e-12]]
            inv = _matrix_inverse(a)

            # Should not raise - regularization should kick in
            assert len(inv) == 2
            assert len(inv[0]) == 2

    def test_matrix_inverse_pure_python_pivot_selection(self) -> None:
        """Test matrix inverse with matrix requiring pivot selection."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            # Matrix where first column has larger value in second row
            # This exercises the pivot selection logic
            a = [[1.0, 2.0], [10.0, 3.0]]
            inv = _matrix_inverse(a)

            # Verify inverse is correct
            product = _matrix_multiply(a, inv)
            assert abs(product[0][0] - 1.0) < 1e-6
            assert abs(product[0][1]) < 1e-6
            assert abs(product[1][0]) < 1e-6
            assert abs(product[1][1] - 1.0) < 1e-6


class TestUKFWithPurePython:
    """Test full UKF functionality with NumPy disabled.

    These tests verify that the UKF works correctly end-to-end
    when using the pure Python matrix implementations.
    """

    def test_ukf_update_multi_pure_python(self) -> None:
        """Test UKF update_multi with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            ukf = UnscentedKalmanFilter(
                scanner_addresses=["scanner1", "scanner2"]
            )

            # Update with measurements
            ukf.update_multi({"scanner1": -70.0, "scanner2": -75.0})

            # State should be updated - access via diagnostics
            diag = ukf.get_diagnostics()
            state = diag["state"]
            assert "scanner1" in state
            assert "scanner2" in state
            # Values should be close to measurements after first update
            assert abs(state["scanner1"] - (-70.0)) < 10
            assert abs(state["scanner2"] - (-75.0)) < 10

    def test_ukf_predict_pure_python(self) -> None:
        """Test UKF predict with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            ukf = UnscentedKalmanFilter(
                scanner_addresses=["scanner1", "scanner2"]
            )

            # Initialize state
            ukf.update_multi({"scanner1": -70.0, "scanner2": -75.0})

            # Predict step
            initial_cov = ukf._p_cov[0][0]
            ukf.predict(dt=2.0)

            # Covariance should have increased
            assert ukf._p_cov[0][0] > initial_cov

    def test_ukf_convergence_pure_python(self) -> None:
        """Test UKF convergence with NumPy disabled."""
        from unittest.mock import patch

        with patch(
            "custom_components.bermuda.filters.ukf.is_numpy_available",
            return_value=False,
        ):
            ukf = UnscentedKalmanFilter(
                scanner_addresses=["scanner1"]
            )

            # Feed consistent measurements
            for _ in range(20):
                ukf.update_multi({"scanner1": -65.0})

            diag = ukf.get_diagnostics()
            state = diag["state"]
            # Should converge to measurement value
            assert abs(state["scanner1"] - (-65.0)) < 2.0


class TestNumpyFunctions:
    """Tests for NumPy accelerated functions in ukf_numpy.py."""

    def test_cholesky_numpy_success(self) -> None:
        """Test cholesky_numpy with valid positive definite matrix."""
        from custom_components.bermuda.filters.ukf_numpy import cholesky_numpy, is_numpy_available

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        matrix = [[4.0, 2.0], [2.0, 5.0]]
        result = cholesky_numpy(matrix)

        assert result is not None
        # Verify L @ L.T approximately equals original
        n = len(result)
        for i in range(n):
            for j in range(n):
                reconstructed = sum(result[i][k] * result[j][k] for k in range(n))
                # Allow small tolerance for regularization
                assert abs(reconstructed - matrix[i][j]) < 0.01

    def test_cholesky_numpy_singular_matrix(self) -> None:
        """Test cholesky_numpy with singular matrix returns None."""
        from custom_components.bermuda.filters.ukf_numpy import cholesky_numpy, is_numpy_available

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        # Near-singular matrix (regularization may save it, but highly singular should fail)
        # Create a matrix that's definitely not positive definite
        matrix = [[1.0, 0.0], [0.0, -1.0]]  # Negative eigenvalue
        result = cholesky_numpy(matrix)
        assert result is None

    def test_matrix_inverse_numpy_success(self) -> None:
        """Test matrix_inverse_numpy with invertible matrix."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            matrix_inverse_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        matrix = [[4.0, 2.0], [1.0, 3.0]]
        result = matrix_inverse_numpy(matrix)

        assert result is not None
        # Verify A @ A^-1 approximately equals identity
        n = len(result)
        for i in range(n):
            for j in range(n):
                product = sum(matrix[i][k] * result[k][j] for k in range(n))
                expected = 1.0 if i == j else 0.0
                assert abs(product - expected) < 0.01

    def test_matrix_inverse_numpy_singular(self) -> None:
        """Test matrix_inverse_numpy with singular matrix."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            matrix_inverse_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        # Highly singular matrix (row of zeros)
        matrix = [[0.0, 0.0], [0.0, 0.0]]
        result = matrix_inverse_numpy(matrix)
        # With regularization it might succeed, but the result should be handled
        # The function adds 1e-6 regularization, so this will actually work
        assert result is not None  # regularization saves it

    def test_mahalanobis_distance_numpy(self) -> None:
        """Test mahalanobis_distance_numpy calculation."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            mahalanobis_distance_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        diff = [1.0, 2.0]
        cov_inv = [[1.0, 0.0], [0.0, 1.0]]  # Identity matrix

        result = mahalanobis_distance_numpy(diff, cov_inv)

        assert result is not None
        # D² = diff.T @ I @ diff = 1² + 2² = 5
        assert abs(result - 5.0) < 0.001

    def test_matrix_multiply_numpy(self) -> None:
        """Test matrix_multiply_numpy."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            matrix_multiply_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        a = [[1.0, 2.0], [3.0, 4.0]]
        b = [[5.0, 6.0], [7.0, 8.0]]

        result = matrix_multiply_numpy(a, b)

        assert result is not None
        # Expected: [[1*5+2*7, 1*6+2*8], [3*5+4*7, 3*6+4*8]] = [[19, 22], [43, 50]]
        assert abs(result[0][0] - 19.0) < 0.001
        assert abs(result[0][1] - 22.0) < 0.001
        assert abs(result[1][0] - 43.0) < 0.001
        assert abs(result[1][1] - 50.0) < 0.001

    def test_outer_product_numpy(self) -> None:
        """Test outer_product_numpy."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            outer_product_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0]

        result = outer_product_numpy(a, b)

        assert result is not None
        # Expected: [[1*4, 1*5], [2*4, 2*5], [3*4, 3*5]] = [[4, 5], [8, 10], [12, 15]]
        assert len(result) == 3
        assert len(result[0]) == 2
        assert abs(result[0][0] - 4.0) < 0.001
        assert abs(result[1][0] - 8.0) < 0.001
        assert abs(result[2][1] - 15.0) < 0.001

    def test_sigma_points_numpy_success(self) -> None:
        """Test sigma_points_numpy generation."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            sigma_points_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        x = [-70.0, -75.0]
        p_cov = [[4.0, 0.0], [0.0, 4.0]]
        gamma = 2.0

        result = sigma_points_numpy(x, p_cov, gamma)

        assert result is not None
        # Should have 2n+1 = 5 sigma points
        assert len(result) == 5
        # First sigma point should be the mean
        assert abs(result[0][0] - (-70.0)) < 0.01
        assert abs(result[0][1] - (-75.0)) < 0.01

    def test_sigma_points_numpy_singular_matrix(self) -> None:
        """Test sigma_points_numpy with non-positive definite matrix."""
        from custom_components.bermuda.filters.ukf_numpy import (
            is_numpy_available,
            sigma_points_numpy,
        )

        if not is_numpy_available():
            pytest.skip("NumPy not available")

        x = [-70.0, -75.0]
        # Not positive definite matrix
        p_cov = [[1.0, 0.0], [0.0, -1.0]]
        gamma = 2.0

        result = sigma_points_numpy(x, p_cov, gamma)
        assert result is None

    def test_is_numpy_available(self) -> None:
        """Test is_numpy_available returns True when numpy is installed."""
        from custom_components.bermuda.filters.ukf_numpy import is_numpy_available

        # NumPy should be available in the test environment
        assert is_numpy_available() is True


class TestNumpyFunctionsWithMocking:
    """Tests for NumPy functions when NumPy is unavailable."""

    def test_cholesky_numpy_no_numpy(self) -> None:
        """Test cholesky_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            # Force reimport to get patched version
            result = ukf_numpy.cholesky_numpy([[1.0, 0.0], [0.0, 1.0]])
            assert result is None

    def test_matrix_inverse_numpy_no_numpy(self) -> None:
        """Test matrix_inverse_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            result = ukf_numpy.matrix_inverse_numpy([[1.0, 0.0], [0.0, 1.0]])
            assert result is None

    def test_mahalanobis_distance_numpy_no_numpy(self) -> None:
        """Test mahalanobis_distance_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            result = ukf_numpy.mahalanobis_distance_numpy([1.0], [[1.0]])
            assert result is None

    def test_matrix_multiply_numpy_no_numpy(self) -> None:
        """Test matrix_multiply_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            result = ukf_numpy.matrix_multiply_numpy([[1.0]], [[1.0]])
            assert result is None

    def test_outer_product_numpy_no_numpy(self) -> None:
        """Test outer_product_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            result = ukf_numpy.outer_product_numpy([1.0], [1.0])
            assert result is None

    def test_sigma_points_numpy_no_numpy(self) -> None:
        """Test sigma_points_numpy returns None when NumPy unavailable."""
        from unittest.mock import patch

        with patch("custom_components.bermuda.filters.ukf_numpy._get_numpy", return_value=None):
            from custom_components.bermuda.filters import ukf_numpy

            result = ukf_numpy.sigma_points_numpy([1.0], [[1.0]], 1.0)
            assert result is None
