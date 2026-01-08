#!/usr/bin/env python3
"""
Standalone test runner for scanner_calibration.py and filters module.

This script tests the scanner calibration module without requiring
the full Home Assistant / Bermuda package to be loaded.
"""

import sys
import os
import types
from abc import ABC, abstractmethod

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =============================================================================
# Create mock package hierarchy for relative imports
# =============================================================================

# Create the filters.const module with actual constants
filters_const = types.ModuleType('custom_components.bermuda.filters.const')
filters_const.BLE_RSSI_TYPICAL_STDDEV = 4.0
filters_const.KALMAN_PROCESS_NOISE = 0.008
filters_const.KALMAN_MEASUREMENT_NOISE = 4.0
filters_const.EMA_ALPHA_SLOW = 0.1
filters_const.EMA_ALPHA_FAST = 0.3
filters_const.CUSUM_THRESHOLD_SIGMA = 4.0
filters_const.CUSUM_DRIFT_SIGMA = 0.5
filters_const.CALIBRATION_MIN_SAMPLES = 50
filters_const.CALIBRATION_MAX_HISTORY = 100
filters_const.CALIBRATION_MIN_PAIRS = 1
filters_const.CALIBRATION_HYSTERESIS_DB = 3

# Create package hierarchy
custom_components = types.ModuleType('custom_components')
bermuda = types.ModuleType('custom_components.bermuda')
filters_pkg = types.ModuleType('custom_components.bermuda.filters')

# Register modules
sys.modules['custom_components'] = custom_components
sys.modules['custom_components.bermuda'] = bermuda
sys.modules['custom_components.bermuda.filters'] = filters_pkg
sys.modules['custom_components.bermuda.filters.const'] = filters_const

# =============================================================================
# Load the filters.base module
# =============================================================================

import importlib.util

base_spec = importlib.util.spec_from_file_location(
    'custom_components.bermuda.filters.base',
    'custom_components/bermuda/filters/base.py',
    submodule_search_locations=['custom_components/bermuda/filters']
)
base_module = importlib.util.module_from_spec(base_spec)
base_module.__package__ = 'custom_components.bermuda.filters'
sys.modules['custom_components.bermuda.filters.base'] = base_module
base_spec.loader.exec_module(base_module)

# =============================================================================
# Load the filters.adaptive module
# =============================================================================

adaptive_spec = importlib.util.spec_from_file_location(
    'custom_components.bermuda.filters.adaptive',
    'custom_components/bermuda/filters/adaptive.py',
    submodule_search_locations=['custom_components/bermuda/filters']
)
adaptive_module = importlib.util.module_from_spec(adaptive_spec)
adaptive_module.__package__ = 'custom_components.bermuda.filters'
sys.modules['custom_components.bermuda.filters.adaptive'] = adaptive_module
adaptive_spec.loader.exec_module(adaptive_module)

# =============================================================================
# Load the filters.kalman module
# =============================================================================

kalman_spec = importlib.util.spec_from_file_location(
    'custom_components.bermuda.filters.kalman',
    'custom_components/bermuda/filters/kalman.py',
    submodule_search_locations=['custom_components/bermuda/filters']
)
kalman_module = importlib.util.module_from_spec(kalman_spec)
kalman_module.__package__ = 'custom_components.bermuda.filters'
sys.modules['custom_components.bermuda.filters.kalman'] = kalman_module
kalman_spec.loader.exec_module(kalman_module)

# =============================================================================
# Set up filters package exports
# =============================================================================

filters_pkg.SignalFilter = base_module.SignalFilter
filters_pkg.FilterConfig = base_module.FilterConfig
filters_pkg.AdaptiveStatistics = adaptive_module.AdaptiveStatistics
filters_pkg.AdaptiveRobustFilter = adaptive_module.AdaptiveRobustFilter
filters_pkg.KalmanFilter = kalman_module.KalmanFilter
filters_pkg.CALIBRATION_MIN_SAMPLES = filters_const.CALIBRATION_MIN_SAMPLES
filters_pkg.CALIBRATION_MAX_HISTORY = filters_const.CALIBRATION_MAX_HISTORY
filters_pkg.CALIBRATION_MIN_PAIRS = filters_const.CALIBRATION_MIN_PAIRS
filters_pkg.CALIBRATION_HYSTERESIS_DB = filters_const.CALIBRATION_HYSTERESIS_DB
filters_pkg.BLE_RSSI_TYPICAL_STDDEV = filters_const.BLE_RSSI_TYPICAL_STDDEV
filters_pkg.EMA_ALPHA_SLOW = filters_const.EMA_ALPHA_SLOW
filters_pkg.EMA_ALPHA_FAST = filters_const.EMA_ALPHA_FAST
filters_pkg.CUSUM_THRESHOLD_SIGMA = filters_const.CUSUM_THRESHOLD_SIGMA
filters_pkg.CUSUM_DRIFT_SIGMA = filters_const.CUSUM_DRIFT_SIGMA
filters_pkg.KALMAN_PROCESS_NOISE = filters_const.KALMAN_PROCESS_NOISE
filters_pkg.KALMAN_MEASUREMENT_NOISE = filters_const.KALMAN_MEASUREMENT_NOISE

# =============================================================================
# Load the scanner_calibration module
# =============================================================================

scanner_cal_spec = importlib.util.spec_from_file_location(
    'custom_components.bermuda.scanner_calibration',
    'custom_components/bermuda/scanner_calibration.py',
    submodule_search_locations=['custom_components/bermuda']
)
scanner_cal = importlib.util.module_from_spec(scanner_cal_spec)
scanner_cal.__package__ = 'custom_components.bermuda'
sys.modules['custom_components.bermuda.scanner_calibration'] = scanner_cal
scanner_cal_spec.loader.exec_module(scanner_cal)

# =============================================================================
# Import classes for testing
# =============================================================================

SignalFilter = base_module.SignalFilter
FilterConfig = base_module.FilterConfig
KalmanFilter = kalman_module.KalmanFilter
AdaptiveStatistics = adaptive_module.AdaptiveStatistics
AdaptiveRobustFilter = adaptive_module.AdaptiveRobustFilter
ScannerPairData = scanner_cal.ScannerPairData
ScannerCalibrationManager = scanner_cal.ScannerCalibrationManager
update_scanner_calibration = scanner_cal.update_scanner_calibration
CALIBRATION_MIN_SAMPLES = filters_const.CALIBRATION_MIN_SAMPLES


# =============================================================================
# SignalFilter Interface Tests
# =============================================================================

def test_signal_filter_is_abstract():
    """Test that SignalFilter cannot be instantiated directly."""
    try:
        SignalFilter()
        assert False, "Should have raised TypeError"
    except TypeError:
        pass
    print("  PASS: test_signal_filter_is_abstract")


def test_kalman_implements_signal_filter():
    """Test that KalmanFilter implements SignalFilter interface."""
    kf = KalmanFilter()
    assert isinstance(kf, SignalFilter)
    assert hasattr(kf, 'update')
    assert hasattr(kf, 'get_estimate')
    assert hasattr(kf, 'get_variance')
    assert hasattr(kf, 'reset')
    print("  PASS: test_kalman_implements_signal_filter")


def test_adaptive_robust_implements_signal_filter():
    """Test that AdaptiveRobustFilter implements SignalFilter interface."""
    af = AdaptiveRobustFilter()
    assert isinstance(af, SignalFilter)
    assert hasattr(af, 'update')
    assert hasattr(af, 'get_estimate')
    assert hasattr(af, 'get_variance')
    assert hasattr(af, 'reset')
    print("  PASS: test_adaptive_robust_implements_signal_filter")


# =============================================================================
# KalmanFilter Tests
# =============================================================================

def test_kalman_filter_initial_state():
    """Test KalmanFilter initial state."""
    kf = KalmanFilter()
    assert kf.sample_count == 0
    assert not kf._initialized
    print("  PASS: test_kalman_filter_initial_state")


def test_kalman_filter_first_update():
    """Test KalmanFilter first measurement initialization."""
    kf = KalmanFilter()
    result = kf.update(-60.0)
    assert result == -60.0
    assert kf.get_estimate() == -60.0
    assert kf._initialized
    print("  PASS: test_kalman_filter_first_update")


def test_kalman_filter_smoothing():
    """Test that KalmanFilter smooths noisy signal."""
    kf = KalmanFilter()

    # Feed noisy measurements around -60 dBm
    measurements = [-60, -58, -62, -59, -61, -60, -58, -62, -60, -59]
    for m in measurements:
        kf.update(float(m))

    # Estimate should be close to -60
    assert abs(kf.get_estimate() - (-60)) < 2.0
    print("  PASS: test_kalman_filter_smoothing")


def test_kalman_filter_reset():
    """Test KalmanFilter reset."""
    kf = KalmanFilter()
    kf.update(-60.0)
    kf.update(-55.0)

    kf.reset()

    assert kf.sample_count == 0
    assert not kf._initialized
    print("  PASS: test_kalman_filter_reset")


def test_kalman_filter_diagnostics():
    """Test KalmanFilter diagnostics output."""
    kf = KalmanFilter()
    kf.update(-60.0)
    kf.update(-55.0)

    diag = kf.get_diagnostics()

    assert "estimate" in diag
    assert "variance" in diag
    assert "kalman_gain" in diag
    assert "sample_count" in diag
    assert diag["sample_count"] == 2
    print("  PASS: test_kalman_filter_diagnostics")


# =============================================================================
# AdaptiveRobustFilter Tests
# =============================================================================

def test_adaptive_robust_filter_initial():
    """Test AdaptiveRobustFilter initial state."""
    af = AdaptiveRobustFilter()
    assert af.get_estimate() == 0.0
    assert not af.changepoint_detected()
    print("  PASS: test_adaptive_robust_filter_initial")


def test_adaptive_robust_filter_update():
    """Test AdaptiveRobustFilter updates estimate."""
    af = AdaptiveRobustFilter()

    result = af.update(-60.0)
    assert result == -60.0

    for _ in range(10):
        af.update(-60.0)

    assert abs(af.get_estimate() - (-60.0)) < 0.5
    print("  PASS: test_adaptive_robust_filter_update")


def test_adaptive_robust_filter_changepoint():
    """Test AdaptiveRobustFilter changepoint detection."""
    af = AdaptiveRobustFilter()

    # Stable signal
    for _ in range(20):
        af.update(-60.0)

    # Sudden shift
    detected = False
    for _ in range(20):
        af.update(-80.0)
        if af.changepoint_detected():
            detected = True
            break

    assert detected, "Expected changepoint detection for 20 dB shift"
    print("  PASS: test_adaptive_robust_filter_changepoint")


def test_adaptive_robust_filter_diagnostics():
    """Test AdaptiveRobustFilter diagnostics."""
    af = AdaptiveRobustFilter()
    for _ in range(10):
        af.update(-55.0)

    diag = af.get_diagnostics()

    assert "mean" in diag
    assert "stddev" in diag
    assert "cusum_pos" in diag
    assert "cusum_neg" in diag
    print("  PASS: test_adaptive_robust_filter_diagnostics")


# =============================================================================
# AdaptiveStatistics Tests
# =============================================================================

def test_adaptive_statistics_initial_state():
    """Test initial state of adaptive statistics."""
    stats = AdaptiveStatistics()
    assert stats.mean == 0.0
    assert stats.sample_count == 0
    assert stats.stddev > 0  # Should be initialized to BLE typical
    print("  PASS: test_adaptive_statistics_initial_state")


def test_adaptive_statistics_update():
    """Test updating adaptive statistics."""
    stats = AdaptiveStatistics()

    # First update initializes mean
    changed = stats.update(-60.0)
    assert stats.mean == -60.0
    assert stats.sample_count == 1
    assert not changed  # No changepoint on first sample

    # Subsequent updates use EMA
    for _ in range(10):
        changed = stats.update(-60.0)  # Stable signal
    assert abs(stats.mean - (-60.0)) < 0.5  # Should be close to -60
    assert not changed  # No changepoint for stable signal

    print("  PASS: test_adaptive_statistics_update")


def test_adaptive_statistics_changepoint_detection():
    """Test CUSUM changepoint detection."""
    stats = AdaptiveStatistics()

    # Initialize with stable signal around -60 dBm
    for _ in range(20):
        stats.update(-60.0)

    # Sudden shift to -80 dBm (large negative jump)
    # Should eventually trigger changepoint
    changepoint_detected = False
    for _ in range(20):
        if stats.update(-80.0):
            changepoint_detected = True
            break

    assert changepoint_detected, "Expected changepoint detection for 20 dB shift"
    print("  PASS: test_adaptive_statistics_changepoint_detection")


def test_adaptive_statistics_to_dict():
    """Test to_dict export."""
    stats = AdaptiveStatistics()
    for _ in range(10):
        stats.update(-55.0)

    d = stats.to_dict()
    assert "mean" in d
    assert "stddev" in d
    assert "sample_count" in d
    assert "changepoints" in d
    assert d["sample_count"] == 10
    print("  PASS: test_adaptive_statistics_to_dict")


# =============================================================================
# ScannerPairData Tests
# =============================================================================

def test_scanner_pair_data_initial_state():
    """Test initial state of pair data."""
    pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
    assert pair.rssi_a_sees_b is None
    assert pair.rssi_b_sees_a is None
    assert pair.sample_count_ab == 0
    assert pair.sample_count_ba == 0
    assert not pair.has_bidirectional_data
    assert pair.rssi_difference is None
    print("  PASS: test_scanner_pair_data_initial_state")


def test_scanner_pair_data_bidirectional():
    """Test bidirectional data with sufficient samples using Kalman filters."""
    pair = ScannerPairData(
        scanner_a="aa:bb:cc:dd:ee:01",
        scanner_b="aa:bb:cc:dd:ee:02",
    )
    # Use Kalman filter for RSSI smoothing
    for _ in range(CALIBRATION_MIN_SAMPLES + 5):
        pair.kalman_ab.update(-55.0)
        pair.kalman_ba.update(-65.0)

    assert pair.has_bidirectional_data
    # Kalman filter converges close to target value
    assert abs(pair.rssi_a_sees_b - (-55.0)) < 1.0
    assert abs(pair.rssi_b_sees_a - (-65.0)) < 1.0
    assert abs(pair.rssi_difference - 10.0) < 2.0  # A sees B ~10 dB stronger
    print("  PASS: test_scanner_pair_data_bidirectional")


def test_scanner_pair_data_insufficient_samples():
    """Test bidirectional data with insufficient samples using Kalman filters."""
    pair = ScannerPairData(
        scanner_a="aa:bb:cc:dd:ee:01",
        scanner_b="aa:bb:cc:dd:ee:02",
    )
    # Only populate one direction with enough samples
    for _ in range(CALIBRATION_MIN_SAMPLES - 1):
        pair.kalman_ab.update(-55.0)
    for _ in range(CALIBRATION_MIN_SAMPLES):
        pair.kalman_ba.update(-65.0)

    assert not pair.has_bidirectional_data
    assert pair.rssi_difference is None
    print("  PASS: test_scanner_pair_data_insufficient_samples")


# =============================================================================
# ScannerCalibrationManager Tests
# =============================================================================

def test_calibration_manager_initial_state():
    """Test initial state of calibration manager."""
    manager = ScannerCalibrationManager()
    assert len(manager.scanner_pairs) == 0
    assert len(manager.suggested_offsets) == 0
    assert len(manager.active_scanners) == 0
    print("  PASS: test_calibration_manager_initial_state")


def test_calibration_manager_pair_key_ordering():
    """Test that pair keys are always consistently ordered."""
    manager = ScannerCalibrationManager()
    key1 = manager._get_pair_key("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")
    key2 = manager._get_pair_key("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")
    assert key1 == key2
    assert key1[0] < key1[1]
    print("  PASS: test_calibration_manager_pair_key_ordering")


def test_calibration_manager_update_cross_visibility():
    """Test updating cross visibility with bidirectional data."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility(
            receiver_addr="aa:aa:aa:aa:aa:aa",
            sender_addr="bb:bb:bb:bb:bb:bb",
            rssi_raw=-55.0,
        )
        manager.update_cross_visibility(
            receiver_addr="bb:bb:bb:bb:bb:bb",
            sender_addr="aa:aa:aa:aa:aa:aa",
            rssi_raw=-65.0,
        )

    pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
    # EMA values converge close to target
    assert abs(pair.rssi_a_sees_b - (-55.0)) < 1.0
    assert abs(pair.rssi_b_sees_a - (-65.0)) < 1.0
    assert pair.sample_count_ab == num_samples
    assert pair.sample_count_ba == num_samples
    assert pair.has_bidirectional_data
    assert abs(pair.rssi_difference - 10.0) < 2.0
    print("  PASS: test_calibration_manager_update_cross_visibility")


def test_calibration_manager_calculate_offsets_symmetric():
    """Test offset calculation produces symmetric results."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)

    offsets = manager.calculate_suggested_offsets()

    assert "aa:aa:aa:aa:aa:aa" in offsets
    assert "bb:bb:bb:bb:bb:bb" in offsets
    # 10 dB difference -> -5/+5 offsets
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5
    print("  PASS: test_calibration_manager_calculate_offsets_symmetric")


def test_calibration_manager_calculate_offsets_multiple_pairs():
    """Test offset calculation with multiple scanner pairs."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "cc:cc:cc:cc:cc:cc", -50.0)
        manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "aa:aa:aa:aa:aa:aa", -60.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "cc:cc:cc:cc:cc:cc", -58.0)
        manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "bb:bb:bb:bb:bb:bb", -58.0)

    offsets = manager.calculate_suggested_offsets()

    # A sees B stronger by 10 dB, A sees C stronger by 10 dB, B and C are equal
    # So A is strongest receiver -> needs negative offset
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    # B and C should have similar positive offsets (with rounding)
    assert abs(offsets["bb:bb:bb:bb:bb:bb"]) <= 3
    assert abs(offsets["cc:cc:cc:cc:cc:cc"]) <= 3
    print("  PASS: test_calibration_manager_calculate_offsets_multiple_pairs")


def test_calibration_manager_offsets_rounded_to_integer():
    """Test that offsets are rounded to integers."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -62.0)

    offsets = manager.calculate_suggested_offsets()

    assert isinstance(offsets["aa:aa:aa:aa:aa:aa"], int)
    assert isinstance(offsets["bb:bb:bb:bb:bb:bb"], int)
    # 7 dB difference -> ~-3.5/+3.5 -> rounds to -4/+4
    assert offsets["aa:aa:aa:aa:aa:aa"] == -4
    assert offsets["bb:bb:bb:bb:bb:bb"] == 4
    print("  PASS: test_calibration_manager_offsets_rounded_to_integer")


def test_calibration_manager_equal_rssi_zero_offset():
    """Test that equal RSSI values produce zero offset."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -60.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -60.0)

    offsets = manager.calculate_suggested_offsets()

    assert offsets.get("aa:aa:aa:aa:aa:aa") == 0
    assert offsets.get("bb:bb:bb:bb:bb:bb") == 0
    print("  PASS: test_calibration_manager_equal_rssi_zero_offset")


def test_calibration_manager_clear():
    """Test clearing calibration data."""
    manager = ScannerCalibrationManager()
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
    manager.calculate_suggested_offsets()

    manager.clear()

    assert len(manager.scanner_pairs) == 0
    assert len(manager.suggested_offsets) == 0
    assert len(manager.active_scanners) == 0
    print("  PASS: test_calibration_manager_clear")


def test_calibration_manager_get_scanner_pair_info():
    """Test getting scanner pair info for diagnostics."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    for _ in range(num_samples):
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)

    info = manager.get_scanner_pair_info()

    assert len(info) == 1
    pair_info = info[0]
    assert pair_info["scanner_a"] == "aa:aa:aa:aa:aa:aa"
    assert pair_info["scanner_b"] == "bb:bb:bb:bb:bb:bb"
    # Kalman-filtered values converge close to target
    assert abs(pair_info["rssi_a_sees_b"] - (-55.0)) < 1.0
    assert abs(pair_info["rssi_b_sees_a"] - (-65.0)) < 1.0
    assert pair_info["bidirectional"] is True
    assert abs(pair_info["difference"] - 10.0) < 2.0
    # Check Kalman filter diagnostics are included
    assert "kalman_ab" in pair_info
    assert "kalman_ba" in pair_info
    assert "estimate" in pair_info["kalman_ab"]
    print("  PASS: test_calibration_manager_get_scanner_pair_info")


def test_calibration_manager_no_data():
    """Test offset calculation with no data."""
    manager = ScannerCalibrationManager()
    offsets = manager.calculate_suggested_offsets()
    assert len(offsets) == 0
    print("  PASS: test_calibration_manager_no_data")


# =============================================================================
# Integration Tests
# =============================================================================

class MockAdvert:
    """Mock advert for testing."""
    def __init__(self, rssi=-60.0, rssi_filtered=-60.0):
        self.rssi = rssi
        self.rssi_filtered = rssi_filtered
        self.hist_rssi = [rssi] * 10


class MockDevice:
    """Mock device for testing."""
    def __init__(self, address, metadevice_sources=None):
        self.address = address
        self.metadevice_sources = metadevice_sources or []
        self.adverts = {}


def test_update_scanner_calibration_with_ibeacon():
    """Test update_scanner_calibration with iBeacon-based scanner visibility."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    ibeacon_a = MockDevice(
        "ibeacon_uuid_a",
        metadevice_sources=["aa:aa:aa:aa:aa:aa"]
    )

    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")
    ibeacon_b = MockDevice(
        "ibeacon_uuid_b",
        metadevice_sources=["bb:bb:bb:bb:bb:bb"]
    )

    # Use rssi (raw) for calibration - rssi_filtered would include offset!
    ibeacon_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi=-55.0)
    ibeacon_a.adverts[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")] = MockAdvert(rssi=-65.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
        "ibeacon_uuid_a": ibeacon_a,
        "ibeacon_uuid_b": ibeacon_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    for _ in range(num_samples):
        offsets = update_scanner_calibration(manager, scanner_list, devices)

    assert "aa:aa:aa:aa:aa:aa" in offsets
    assert "bb:bb:bb:bb:bb:bb" in offsets
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5
    print("  PASS: test_update_scanner_calibration_with_ibeacon")


def test_update_scanner_calibration_direct_mac():
    """Test update_scanner_calibration with direct MAC visibility."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")

    # Use rssi (raw) for calibration - rssi_filtered would include offset!
    scanner_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi=-55.0)
    scanner_a.adverts[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")] = MockAdvert(rssi=-65.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    for _ in range(num_samples):
        offsets = update_scanner_calibration(manager, scanner_list, devices)

    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5
    print("  PASS: test_update_scanner_calibration_direct_mac")


def test_update_scanner_calibration_unidirectional():
    """Test that unidirectional visibility does not produce offsets."""
    manager = ScannerCalibrationManager()
    num_samples = CALIBRATION_MIN_SAMPLES + 5

    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")
    ibeacon_b = MockDevice(
        "ibeacon_uuid_b",
        metadevice_sources=["bb:bb:bb:bb:bb:bb"]
    )

    # Use rssi (raw) for calibration
    ibeacon_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi=-55.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
        "ibeacon_uuid_b": ibeacon_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    for _ in range(num_samples):
        offsets = update_scanner_calibration(manager, scanner_list, devices)

    assert len(offsets) == 0
    print("  PASS: test_update_scanner_calibration_unidirectional")


# =============================================================================
# Test Runner
# =============================================================================

def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Running Scanner Calibration & Filters Module Tests")
    print("=" * 60 + "\n")

    tests = [
        # SignalFilter interface tests
        test_signal_filter_is_abstract,
        test_kalman_implements_signal_filter,
        test_adaptive_robust_implements_signal_filter,
        # KalmanFilter tests
        test_kalman_filter_initial_state,
        test_kalman_filter_first_update,
        test_kalman_filter_smoothing,
        test_kalman_filter_reset,
        test_kalman_filter_diagnostics,
        # AdaptiveRobustFilter tests
        test_adaptive_robust_filter_initial,
        test_adaptive_robust_filter_update,
        test_adaptive_robust_filter_changepoint,
        test_adaptive_robust_filter_diagnostics,
        # AdaptiveStatistics tests
        test_adaptive_statistics_initial_state,
        test_adaptive_statistics_update,
        test_adaptive_statistics_changepoint_detection,
        test_adaptive_statistics_to_dict,
        # ScannerPairData tests
        test_scanner_pair_data_initial_state,
        test_scanner_pair_data_bidirectional,
        test_scanner_pair_data_insufficient_samples,
        # ScannerCalibrationManager tests
        test_calibration_manager_initial_state,
        test_calibration_manager_pair_key_ordering,
        test_calibration_manager_update_cross_visibility,
        test_calibration_manager_calculate_offsets_symmetric,
        test_calibration_manager_calculate_offsets_multiple_pairs,
        test_calibration_manager_offsets_rounded_to_integer,
        test_calibration_manager_equal_rssi_zero_offset,
        test_calibration_manager_clear,
        test_calibration_manager_get_scanner_pair_info,
        test_calibration_manager_no_data,
        # Integration tests
        test_update_scanner_calibration_with_ibeacon,
        test_update_scanner_calibration_direct_mac,
        test_update_scanner_calibration_unidirectional,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
