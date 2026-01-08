"""Test util.py in Bermuda."""

from __future__ import annotations

# from homeassistant.core import HomeAssistant

from math import floor

import pytest

from custom_components.bermuda import util


def test_mac_math_offset():
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", 2) == "aa:bb:cc:dd:ee:f1"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", -3) == "aa:bb:cc:dd:ee:ec"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ff", 2) is None
    assert util.mac_math_offset("clearly_not:a-mac_address", 2) is None
    assert util.mac_math_offset(None, 4) is None


def test_normalize_mac_variants():
    assert util.normalize_mac("AA:bb:CC:88:Ff:00") == "aa:bb:cc:88:ff:00"
    assert util.normalize_mac("aa_bb_CC_dd_ee_ff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("aa-77-CC-dd-ee-ff") == "aa:77:cc:dd:ee:ff"
    assert util.normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_non_mac():
    with pytest.raises(ValueError):
        util.normalize_mac("fmdn:abc123")


def test_normalize_identifier_and_mac_dispatch():
    assert util.normalize_identifier("AABBCCDDEEFF") == "aabbccddeeff"
    assert util.normalize_identifier("12345678-1234-5678-9abc-def012345678_extra") == (
        "12345678123456789abcdef012345678_extra"
    )
    assert util.normalize_address("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_address("fmdn:Device-ID") == "fmdn:device-id"


def test_mac_explode_formats():
    ex = util.mac_explode_formats("aa:bb:cc:77:ee:ff")
    assert "aa:bb:cc:77:ee:ff" in ex
    assert "aa-bb-cc-77-ee-ff" in ex
    for e in ex:
        assert len(e) in [12, 17]


def test_mac_redact():
    assert util.mac_redact("aa:bb:cc:77:ee:ff", "tEstMe") == "aa::tEstMe::ff"
    assert util.mac_redact("howdy::doody::friend", "PLEASENOE") == "ho::PLEASENOE::nd"


def test_rssi_to_metres():
    """Test Two-Slope path loss model for RSSI to distance conversion.

    The Two-Slope model uses:
    - Near-field exponent 1.8 for distances < 6m
    - User-configured far-field exponent for distances >= 6m
    """
    # Far-field test cases (distance > 6m breakpoint)
    assert floor(util.rssi_to_metres(-50, -20, 2)) == 37
    assert floor(util.rssi_to_metres(-80, -20, 2)) == 1196

    # Near-field test case (distance < 6m)
    # At ref_power=-55, rssi=-55 should give ~1m (near-field exponent 1.8)
    assert 0.9 < util.rssi_to_metres(-55, -55, 3.5) < 1.1

    # Test minimum distance floor (0.1m)
    assert util.rssi_to_metres(-30, -55, 3.5) == 0.1  # Very strong signal


def test_clean_charbuf():
    assert util.clean_charbuf("a Normal string.") == "a Normal string."
    assert util.clean_charbuf("Broken\000String\000Fixed\000\000\000") == "Broken"


class TestKalmanFilter:
    """Tests for the KalmanFilter class."""

    def test_kalman_initialization(self):
        """Test that KalmanFilter initializes with correct defaults."""
        kf = util.KalmanFilter()
        assert kf.estimate is None
        assert kf.kalman_gain == 0.0
        assert not kf.is_initialized

    def test_kalman_first_measurement(self):
        """Test that first measurement initializes the filter."""
        kf = util.KalmanFilter()
        result = kf.update(-70.0)
        assert result == -70.0
        assert kf.estimate == -70.0
        assert kf.is_initialized
        assert kf.kalman_gain == 1.0  # First measurement is fully trusted

    def test_kalman_filters_spike(self):
        """Test that Kalman filter dampens signal spikes."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        # Establish baseline at -70 dBm
        for _ in range(5):
            kf.update(-70.0)
        baseline = kf.estimate

        # Introduce a strong spike (-45 dBm is stronger/closer than -70 dBm)
        result = kf.update(-45.0)

        # The result should be between baseline (-70) and spike (-45)
        # In RSSI: -45 > -70 numerically (stronger signal = less negative)
        assert baseline is not None
        assert result < -45.0  # Not fully following the spike
        assert result > baseline  # Moved toward spike but dampened

    def test_kalman_responds_to_approach(self):
        """Test that filter responds to genuine device approach."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        results = []
        # Simulate device approaching
        for rssi in [-80, -75, -70, -65, -60, -55]:
            results.append(kf.update(rssi))

        # Filtered values should follow the trend
        assert results[-1] > results[0]  # Getting stronger
        # But with smoothing lag
        assert results[-1] < -55  # Not fully caught up yet

    def test_kalman_reduces_variance(self):
        """Test that Kalman filter reduces measurement variance."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        raw = [-60, -61, -59, -60, -62, -58, -60, -61, -59, -60]
        filtered = [kf.update(r) for r in raw]

        raw_variance = max(raw) - min(raw)
        filtered_variance = max(filtered) - min(filtered)

        # Filtered variance should be significantly less
        assert filtered_variance < raw_variance * 0.5

    def test_kalman_gain_convergence(self):
        """Test that Kalman gain converges to steady state."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        gains = []
        for _ in range(20):
            kf.update(-60.0)
            gains.append(kf.kalman_gain)

        # Gain should decrease and converge
        assert gains[0] == 1.0  # First measurement
        assert gains[-1] < gains[1]  # Converging downward
        # Should reach approximate steady state
        assert abs(gains[-1] - gains[-2]) < 0.01

    def test_kalman_reset(self):
        """Test that reset clears filter state."""
        kf = util.KalmanFilter()
        kf.update(-70.0)
        kf.update(-65.0)

        assert kf.is_initialized

        kf.reset()
        assert not kf.is_initialized
        assert kf.estimate is None
        assert kf.kalman_gain == 0.0

    def test_kalman_reset_with_initial(self):
        """Test reset with initial estimate."""
        kf = util.KalmanFilter()
        kf.update(-70.0)
        kf.reset(initial_estimate=-80.0)

        assert kf.is_initialized
        assert kf.estimate == -80.0

    def test_kalman_parameter_tuning(self):
        """Test that parameters affect filter behavior."""
        # High measurement noise = trusts measurements less
        kf_high_r = util.KalmanFilter(process_noise=1.0, measurement_noise=100.0)
        kf_low_r = util.KalmanFilter(process_noise=1.0, measurement_noise=1.0)

        for rssi in [-70, -65, -60]:
            kf_high_r.update(rssi)
            kf_low_r.update(rssi)

        # High R should result in lower Kalman gain (trusts measurements less)
        assert kf_high_r.kalman_gain < kf_low_r.kalman_gain

    def test_kalman_adaptive_stronger_signal_more_influence(self):
        """Test that stronger signals have more influence with adaptive update."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)

        # Initialize with baseline
        kf.update(-70.0)
        baseline = kf.estimate

        # Reset and test with strong signal (-50 dBm)
        kf.reset(initial_estimate=-70.0)
        kf.update_adaptive(-50.0, rssi_strong_threshold=-50.0)
        strong_influence = abs(kf.estimate - (-70.0))

        # Reset and test with weak signal (-80 dBm)
        kf.reset(initial_estimate=-70.0)
        kf.update_adaptive(-80.0, rssi_strong_threshold=-50.0)
        weak_influence = abs(kf.estimate - (-70.0))

        # Strong signal should move estimate MORE (higher influence)
        # Weak signal should move estimate LESS (lower influence)
        assert strong_influence > weak_influence

    def test_kalman_adaptive_noise_scaling(self):
        """Test that adaptive noise scales correctly with signal strength."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)

        # Initialize
        kf.update(-60.0)

        # At threshold, gain should be similar to non-adaptive
        kf_reference = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        kf_reference.update(-60.0)

        kf.update_adaptive(-50.0, rssi_strong_threshold=-50.0)
        kf_reference.update(-50.0)

        # Gains should be similar at threshold (adaptive noise ≈ base noise)
        assert abs(kf.kalman_gain - kf_reference.kalman_gain) < 0.1

    def test_kalman_adaptive_weak_signal_dampened(self):
        """Test that very weak signals are heavily dampened."""
        kf = util.KalmanFilter(process_noise=1.0, measurement_noise=10.0)

        # Establish baseline at -60 dBm
        for _ in range(5):
            kf.update_adaptive(-60.0)

        baseline = kf.estimate

        # Apply very weak signal (-90 dBm, 40 dB below threshold)
        # This should have minimal influence due to high adaptive noise
        kf.update_adaptive(-90.0, rssi_strong_threshold=-50.0)

        # Estimate should barely change (weak signal heavily dampened)
        assert baseline is not None
        assert abs(kf.estimate - baseline) < 5.0  # Less than 5 dBm change
