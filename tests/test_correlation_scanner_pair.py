"""
Tests for ScannerPairCorrelation.

These tests verify that Kalman-based delta tracking works correctly
for learning typical RSSI relationships between scanner pairs.
"""

from __future__ import annotations

import random

import pytest

from custom_components.bermuda.correlation.scanner_pair import (
    MIN_SAMPLES_FOR_MATURITY,
    ScannerPairCorrelation,
)


class TestScannerPairCorrelationMaturity:
    """Tests for correlation maturity lifecycle."""

    def test_initial_state_is_immature(self) -> None:
        """A fresh correlation must not be trusted for area decisions."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        assert not corr.is_mature, (
            f"Fresh correlation returned is_mature=True with only "
            f"{corr.sample_count} samples. Need {MIN_SAMPLES_FOR_MATURITY}. "
            f"Using immature correlations would cause unreliable area detection "
            f"based on statistically insignificant data."
        )
        assert corr.sample_count == 0

    def test_becomes_mature_after_min_samples(self) -> None:
        """Correlation transitions to mature after MIN_SAMPLES_FOR_MATURITY updates."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        for i in range(MIN_SAMPLES_FOR_MATURITY):
            corr.update(10.0)

            if i < MIN_SAMPLES_FOR_MATURITY - 1:
                assert not corr.is_mature, (
                    f"Correlation became mature prematurely at sample {i + 1} "
                    f"(need {MIN_SAMPLES_FOR_MATURITY}). Premature maturity would "
                    f"cause area decisions based on insufficient statistical data."
                )

        assert corr.is_mature, (
            f"Correlation still immature after {MIN_SAMPLES_FOR_MATURITY} samples "
            f"(sample_count={corr.sample_count}). Mature correlations should "
            f"contribute to area confidence calculations."
        )


class TestScannerPairCorrelationLearning:
    """Tests for delta learning behavior."""

    def test_learns_consistent_delta(self) -> None:
        """With consistent input, estimate converges to that value."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")
        expected_delta = 12.5

        for _ in range(50):
            corr.update(expected_delta)

        error = abs(corr.expected_delta - expected_delta)
        assert error < 0.5, (
            f"Kalman filter learned delta={corr.expected_delta:.2f} dB but input "
            f"was consistently {expected_delta} dB (error={error:.2f} dB). "
            f"Poor convergence means the baseline for anomaly detection is wrong, "
            f"causing false positives or missed anomalies."
        )

    def test_handles_noisy_input(self) -> None:
        """Kalman filter extracts true delta from noisy measurements."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        true_delta = 15.0
        noise_std = 4.0  # Realistic BLE RSSI noise

        random.seed(42)  # Reproducible test
        for _ in range(100):
            noisy_delta = true_delta + random.gauss(0, noise_std)
            corr.update(noisy_delta)

        error = abs(corr.expected_delta - true_delta)
        assert error < 2.0, (
            f"Kalman filter learned delta={corr.expected_delta:.2f} dB from noisy "
            f"input with true_delta={true_delta} dB and noise_std={noise_std} dB "
            f"(error={error:.2f} dB). Poor noise rejection causes unstable "
            f"area detection as the learned baseline drifts with noise."
        )

    def test_negative_delta_supported(self) -> None:
        """Negative deltas work correctly (other scanner closer to device)."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        # Other scanner sees 5 dB STRONGER than primary
        for _ in range(50):
            corr.update(-5.0)

        assert corr.expected_delta < 0, (
            f"Learned delta={corr.expected_delta:.2f} dB is positive but input "
            f"was consistently -5.0 dB (other scanner stronger). Failing to "
            f"preserve negative deltas breaks detection for adjacent rooms where "
            f"the 'other' scanner may be closer to the device."
        )
        assert abs(corr.expected_delta - (-5.0)) < 0.5


class TestScannerPairCorrelationZScore:
    """Tests for anomaly detection via z-score."""

    def test_z_score_zero_for_matching_observation(self) -> None:
        """Z-score is near zero when observation matches expectation."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        for _ in range(50):
            corr.update(10.0)

        z = corr.z_score(10.0)

        assert z < 0.5, (
            f"Z-score={z:.2f} for observation matching learned delta "
            f"(expected ~0). High z-score for matching observations would "
            f"incorrectly penalize devices that are actually in the right area."
        )

    def test_z_score_high_for_anomalous_observation(self) -> None:
        """Z-score is high when observation deviates significantly."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        # Learn: other scanner typically 10 dB weaker
        for _ in range(50):
            corr.update(10.0)

        # Anomaly: other scanner suddenly 30 dB weaker
        z = corr.z_score(30.0)

        assert z > 2.0, (
            f"Z-score={z:.2f} for 20 dB deviation from learned delta "
            f"(learned={corr.expected_delta:.1f}, observed=30.0). Low z-score "
            f"for large deviations would miss area misdetections, leaving "
            f"devices assigned to wrong areas."
        )

    def test_z_score_zero_variance_safe(self) -> None:
        """Z-score returns 0 when variance is zero (prevents division by zero)."""
        corr = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")
        # Force edge case: both filters have zero variance
        corr._kalman_auto.variance = 0.0
        corr._kalman_button.variance = 0.0

        z = corr.z_score(100.0)

        assert z == 0.0, (
            f"Z-score={z} with zero variance (expected 0.0). Non-zero return "
            f"with zero variance indicates division by zero was not handled, "
            f"which would cause crashes or NaN propagation."
        )


class TestScannerPairCorrelationPersistence:
    """Tests for serialization and persistence."""

    def test_serialization_roundtrip_preserves_state(self) -> None:
        """All Kalman state survives serialization roundtrip."""
        original = ScannerPairCorrelation(scanner_address="AA:BB:CC:DD:EE:FF")

        for _ in range(50):
            original.update(8.5)

        # Roundtrip
        data = original.to_dict()
        restored = ScannerPairCorrelation.from_dict(data)

        assert restored.scanner_address == original.scanner_address, (
            f"Scanner address changed: '{original.scanner_address}' -> "
            f"'{restored.scanner_address}'. Correlations would be associated "
            f"with wrong scanners after restart."
        )
        assert restored.expected_delta == original.expected_delta, (
            f"Estimate changed: {original.expected_delta} -> "
            f"{restored.expected_delta}. Learned correlations would be lost "
            f"on every HA restart, requiring re-learning."
        )
        assert restored.variance == original.variance, (
            f"Variance changed: {original.variance} -> {restored.variance}. "
            f"Uncertainty information would be lost, affecting confidence "
            f"calculations after restart."
        )
        assert restored.sample_count == original.sample_count, (
            f"Sample count changed: {original.sample_count} -> "
            f"{restored.sample_count}. Maturity state would reset on restart."
        )
        assert restored.is_mature == original.is_mature, (
            f"Maturity changed: {original.is_mature} -> {restored.is_mature}. "
            f"Mature correlations would become immature after restart."
        )

    def test_to_dict_structure(self) -> None:
        """Serialized dict has expected structure for storage."""
        corr = ScannerPairCorrelation(scanner_address="11:22:33:44:55:66")
        corr.update(5.0)

        data = corr.to_dict()

        required_keys = {"scanner", "estimate", "variance", "samples"}
        missing = required_keys - set(data.keys())
        assert not missing, (
            f"Missing keys in serialized data: {missing}. Storage format "
            f"is incomplete and would fail to restore correlation state."
        )

        assert data["scanner"] == "11:22:33:44:55:66"
        assert isinstance(data["estimate"], float), (
            f"estimate is {type(data['estimate']).__name__}, expected float. "
            f"Wrong type would cause JSON serialization issues."
        )
        assert isinstance(data["variance"], float), f"variance is {type(data['variance']).__name__}, expected float."
        assert isinstance(data["samples"], int), f"samples is {type(data['samples']).__name__}, expected int."
