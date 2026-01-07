#!/usr/bin/env python3
"""
Standalone test runner for scanner_calibration.py.

This script tests the scanner calibration module without requiring
the full Home Assistant / Bermuda package to be loaded.
"""

import sys
import importlib.util

# Load the scanner_calibration module directly
spec = importlib.util.spec_from_file_location(
    'scanner_calibration',
    'custom_components/bermuda/scanner_calibration.py'
)
scanner_cal = importlib.util.module_from_spec(spec)
sys.modules['scanner_calibration'] = scanner_cal
spec.loader.exec_module(scanner_cal)

# Import the classes we need to test
ScannerPairData = scanner_cal.ScannerPairData
ScannerCalibrationManager = scanner_cal.ScannerCalibrationManager
MIN_CROSS_VISIBILITY_SAMPLES = scanner_cal.MIN_CROSS_VISIBILITY_SAMPLES


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
    """Test bidirectional data with sufficient samples."""
    pair = ScannerPairData(
        scanner_a="aa:bb:cc:dd:ee:01",
        scanner_b="aa:bb:cc:dd:ee:02",
        rssi_a_sees_b=-55.0,
        rssi_b_sees_a=-65.0,
        sample_count_ab=MIN_CROSS_VISIBILITY_SAMPLES,
        sample_count_ba=MIN_CROSS_VISIBILITY_SAMPLES,
    )
    assert pair.has_bidirectional_data
    assert pair.rssi_difference == 10.0  # A sees B 10 dB stronger
    print("  PASS: test_scanner_pair_data_bidirectional")


def test_scanner_pair_data_insufficient_samples():
    """Test bidirectional data with insufficient samples."""
    pair = ScannerPairData(
        scanner_a="aa:bb:cc:dd:ee:01",
        scanner_b="aa:bb:cc:dd:ee:02",
        rssi_a_sees_b=-55.0,
        rssi_b_sees_a=-65.0,
        sample_count_ab=MIN_CROSS_VISIBILITY_SAMPLES - 1,
        sample_count_ba=MIN_CROSS_VISIBILITY_SAMPLES,
    )
    assert not pair.has_bidirectional_data
    assert pair.rssi_difference is None
    print("  PASS: test_scanner_pair_data_insufficient_samples")


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

    # A sees B
    manager.update_cross_visibility(
        receiver_addr="aa:aa:aa:aa:aa:aa",
        sender_addr="bb:bb:bb:bb:bb:bb",
        rssi_filtered=-55.0,
        sample_count=10,
    )

    # B sees A
    manager.update_cross_visibility(
        receiver_addr="bb:bb:bb:bb:bb:bb",
        sender_addr="aa:aa:aa:aa:aa:aa",
        rssi_filtered=-65.0,
        sample_count=10,
    )

    pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
    assert pair.rssi_a_sees_b == -55.0
    assert pair.rssi_b_sees_a == -65.0
    assert pair.has_bidirectional_data
    assert pair.rssi_difference == 10.0
    print("  PASS: test_calibration_manager_update_cross_visibility")


def test_calibration_manager_calculate_offsets_symmetric():
    """Test offset calculation produces symmetric results."""
    manager = ScannerCalibrationManager()

    # A sees B at -55, B sees A at -65
    # Difference is 10 dB, so A receives 5 dB stronger, B receives 5 dB weaker
    # A needs offset -5, B needs offset +5
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, 10)

    offsets = manager.calculate_suggested_offsets()

    assert "aa:aa:aa:aa:aa:aa" in offsets
    assert "bb:bb:bb:bb:bb:bb" in offsets
    # A receives stronger, so needs negative offset to compensate
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    # B receives weaker, so needs positive offset to compensate
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5
    print("  PASS: test_calibration_manager_calculate_offsets_symmetric")


def test_calibration_manager_calculate_offsets_multiple_pairs():
    """Test offset calculation with multiple scanner pairs."""
    manager = ScannerCalibrationManager()

    # Scanner A, B, C
    # A sees B at -55, B sees A at -65 (diff +10, A is +5 stronger)
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, 10)

    # A sees C at -50, C sees A at -60 (diff +10, A is +5 stronger)
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "cc:cc:cc:cc:cc:cc", -50.0, 10)
    manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "aa:aa:aa:aa:aa:aa", -60.0, 10)

    # B sees C at -58, C sees B at -58 (diff 0, both equal)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "cc:cc:cc:cc:cc:cc", -58.0, 10)
    manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "bb:bb:bb:bb:bb:bb", -58.0, 10)

    offsets = manager.calculate_suggested_offsets()

    # A has two pairs, both showing +5 offset needed -> median(-5, -5) = -5
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    # B: from A pair: +5, from C pair: 0 -> median = 2.5 -> rounded to 2
    assert offsets["bb:bb:bb:bb:bb:bb"] == 2
    # C: from A pair: +5, from B pair: 0 -> median = 2.5 -> rounded to 2
    assert offsets["cc:cc:cc:cc:cc:cc"] == 2
    print("  PASS: test_calibration_manager_calculate_offsets_multiple_pairs")


def test_calibration_manager_offsets_rounded_to_integer():
    """Test that offsets are rounded to integers."""
    manager = ScannerCalibrationManager()

    # A sees B at -55, B sees A at -62 (diff +7, so offset = ±3.5)
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -62.0, 10)

    offsets = manager.calculate_suggested_offsets()

    # Check that values are integers (rounded)
    assert isinstance(offsets["aa:aa:aa:aa:aa:aa"], int)
    assert isinstance(offsets["bb:bb:bb:bb:bb:bb"], int)
    # 7/2 = 3.5, rounds to 4
    assert offsets["aa:aa:aa:aa:aa:aa"] == -4
    assert offsets["bb:bb:bb:bb:bb:bb"] == 4
    print("  PASS: test_calibration_manager_offsets_rounded_to_integer")


def test_calibration_manager_equal_rssi_zero_offset():
    """Test that equal RSSI values produce zero offset."""
    manager = ScannerCalibrationManager()

    # Both see each other at the same RSSI
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -60.0, 10)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -60.0, 10)

    offsets = manager.calculate_suggested_offsets()

    assert offsets.get("aa:aa:aa:aa:aa:aa") == 0
    assert offsets.get("bb:bb:bb:bb:bb:bb") == 0
    print("  PASS: test_calibration_manager_equal_rssi_zero_offset")


def test_calibration_manager_clear():
    """Test clearing calibration data."""
    manager = ScannerCalibrationManager()
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
    manager.calculate_suggested_offsets()

    manager.clear()

    assert len(manager.scanner_pairs) == 0
    assert len(manager.suggested_offsets) == 0
    assert len(manager.active_scanners) == 0
    print("  PASS: test_calibration_manager_clear")


def test_calibration_manager_get_scanner_pair_info():
    """Test getting scanner pair info for diagnostics."""
    manager = ScannerCalibrationManager()
    manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
    manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, 10)

    info = manager.get_scanner_pair_info()

    assert len(info) == 1
    pair_info = info[0]
    assert pair_info["scanner_a"] == "aa:aa:aa:aa:aa:aa"
    assert pair_info["scanner_b"] == "bb:bb:bb:bb:bb:bb"
    assert pair_info["rssi_a_sees_b"] == -55.0
    assert pair_info["rssi_b_sees_a"] == -65.0
    assert pair_info["bidirectional"] is True
    assert pair_info["difference"] == 10.0
    print("  PASS: test_calibration_manager_get_scanner_pair_info")


def test_calibration_manager_no_data():
    """Test offset calculation with no data."""
    manager = ScannerCalibrationManager()
    offsets = manager.calculate_suggested_offsets()
    assert len(offsets) == 0
    print("  PASS: test_calibration_manager_no_data")


# Import update_scanner_calibration for integration tests
update_scanner_calibration = scanner_cal.update_scanner_calibration


class MockAdvert:
    """Mock advert for testing."""
    def __init__(self, rssi=-60.0, rssi_filtered=-60.0):
        self.rssi = rssi
        self.rssi_filtered = rssi_filtered
        self.hist_rssi = [rssi] * 10  # 10 samples


class MockDevice:
    """Mock device for testing."""
    def __init__(self, address, metadevice_sources=None):
        self.address = address
        self.metadevice_sources = metadevice_sources or []
        self.adverts = {}


def test_update_scanner_calibration_with_ibeacon():
    """Test update_scanner_calibration with iBeacon-based scanner visibility."""
    manager = ScannerCalibrationManager()

    # Scanner A (MAC: aa:aa:aa:aa:aa:aa) broadcasts iBeacon with UUID-based address
    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    ibeacon_a = MockDevice(
        "ibeacon_uuid_a",
        metadevice_sources=["aa:aa:aa:aa:aa:aa"]
    )

    # Scanner B (MAC: bb:bb:bb:bb:bb:bb) broadcasts iBeacon with UUID-based address
    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")
    ibeacon_b = MockDevice(
        "ibeacon_uuid_b",
        metadevice_sources=["bb:bb:bb:bb:bb:bb"]
    )

    # IMPORTANT: Adverts are stored on the SENDING device (the iBeacon), not the receiver!
    # Key format: (source_mac, receiver_scanner_addr)

    # Scanner A sees iBeacon B -> stored on ibeacon_b
    ibeacon_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi_filtered=-55.0)

    # Scanner B sees iBeacon A -> stored on ibeacon_a
    ibeacon_a.adverts[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")] = MockAdvert(rssi_filtered=-65.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
        "ibeacon_uuid_a": ibeacon_a,
        "ibeacon_uuid_b": ibeacon_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    offsets = update_scanner_calibration(manager, scanner_list, devices)

    # A sees B at -55, B sees A at -65 (diff +10)
    # A should get -5, B should get +5
    assert "aa:aa:aa:aa:aa:aa" in offsets, f"Scanner A not in offsets: {offsets}"
    assert "bb:bb:bb:bb:bb:bb" in offsets, f"Scanner B not in offsets: {offsets}"
    assert offsets["aa:aa:aa:aa:aa:aa"] == -5, f"Expected A=-5, got {offsets['aa:aa:aa:aa:aa:aa']}"
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5, f"Expected B=5, got {offsets['bb:bb:bb:bb:bb:bb']}"
    print("  PASS: test_update_scanner_calibration_with_ibeacon")


def test_update_scanner_calibration_direct_mac():
    """Test update_scanner_calibration with direct MAC visibility (no iBeacon)."""
    manager = ScannerCalibrationManager()

    # Scanner A and B see each other directly by MAC
    # IMPORTANT: Adverts are stored on the SENDING device!
    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")

    # A sees B's MAC directly -> stored on scanner_b (the sender)
    scanner_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi_filtered=-55.0)

    # B sees A's MAC directly -> stored on scanner_a (the sender)
    scanner_a.adverts[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")] = MockAdvert(rssi_filtered=-65.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    offsets = update_scanner_calibration(manager, scanner_list, devices)

    assert offsets["aa:aa:aa:aa:aa:aa"] == -5
    assert offsets["bb:bb:bb:bb:bb:bb"] == 5
    print("  PASS: test_update_scanner_calibration_direct_mac")


def test_update_scanner_calibration_unidirectional():
    """Test that unidirectional visibility does not produce offsets."""
    manager = ScannerCalibrationManager()

    scanner_a = MockDevice("aa:aa:aa:aa:aa:aa")
    scanner_b = MockDevice("bb:bb:bb:bb:bb:bb")
    ibeacon_b = MockDevice(
        "ibeacon_uuid_b",
        metadevice_sources=["bb:bb:bb:bb:bb:bb"]
    )

    # Only A sees B's iBeacon (B does not see A)
    # Adverts stored on the sending device (ibeacon_b)
    ibeacon_b.adverts[("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")] = MockAdvert(rssi_filtered=-55.0)

    devices = {
        "aa:aa:aa:aa:aa:aa": scanner_a,
        "bb:bb:bb:bb:bb:bb": scanner_b,
        "ibeacon_uuid_b": ibeacon_b,
    }

    scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

    offsets = update_scanner_calibration(manager, scanner_list, devices)

    # No bidirectional data -> no offsets
    assert len(offsets) == 0, f"Expected no offsets, got {offsets}"
    print("  PASS: test_update_scanner_calibration_unidirectional")


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Running Scanner Calibration Tests")
    print("=" * 60 + "\n")

    tests = [
        test_scanner_pair_data_initial_state,
        test_scanner_pair_data_bidirectional,
        test_scanner_pair_data_insufficient_samples,
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
        # Integration tests with update_scanner_calibration
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
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
