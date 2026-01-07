"""Tests for scanner auto-calibration functionality."""

from __future__ import annotations

import pytest

from custom_components.bermuda.scanner_calibration import (
    MIN_CROSS_VISIBILITY_SAMPLES,
    MIN_SCANNER_PAIRS,
    ScannerCalibrationManager,
    ScannerPairData,
    update_scanner_calibration,
)


class TestScannerPairData:
    """Test ScannerPairData dataclass."""

    def test_initial_state(self):
        """Test initial state of pair data."""
        pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
        assert pair.rssi_a_sees_b is None
        assert pair.rssi_b_sees_a is None
        assert pair.sample_count_ab == 0
        assert pair.sample_count_ba == 0
        assert not pair.has_bidirectional_data
        assert pair.rssi_difference is None

    def test_unidirectional_data(self):
        """Test with only one direction of visibility."""
        pair = ScannerPairData(
            scanner_a="aa:bb:cc:dd:ee:01",
            scanner_b="aa:bb:cc:dd:ee:02",
            rssi_a_sees_b=-55.0,
            sample_count_ab=10,
        )
        assert not pair.has_bidirectional_data
        assert pair.rssi_difference is None

    def test_bidirectional_data_insufficient_samples(self):
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

    def test_bidirectional_data_sufficient_samples(self):
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

    def test_rssi_difference_negative(self):
        """Test negative RSSI difference (B receives stronger)."""
        pair = ScannerPairData(
            scanner_a="aa:bb:cc:dd:ee:01",
            scanner_b="aa:bb:cc:dd:ee:02",
            rssi_a_sees_b=-70.0,
            rssi_b_sees_a=-55.0,
            sample_count_ab=10,
            sample_count_ba=10,
        )
        assert pair.rssi_difference == -15.0  # B sees A 15 dB stronger


class TestScannerCalibrationManager:
    """Test ScannerCalibrationManager class."""

    def test_initial_state(self):
        """Test initial state of calibration manager."""
        manager = ScannerCalibrationManager()
        assert len(manager.scanner_pairs) == 0
        assert len(manager.suggested_offsets) == 0
        assert len(manager.active_scanners) == 0

    def test_get_pair_key_ordering(self):
        """Test that pair keys are always consistently ordered."""
        manager = ScannerCalibrationManager()
        key1 = manager._get_pair_key("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa")
        key2 = manager._get_pair_key("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")
        assert key1 == key2
        assert key1[0] < key1[1]

    def test_update_cross_visibility_single_direction(self):
        """Test updating cross visibility with single direction."""
        manager = ScannerCalibrationManager()
        manager.update_cross_visibility(
            receiver_addr="aa:aa:aa:aa:aa:aa",
            sender_addr="bb:bb:bb:bb:bb:bb",
            rssi_filtered=-55.0,
            sample_count=10,
        )

        assert len(manager.scanner_pairs) == 1
        assert "aa:aa:aa:aa:aa:aa" in manager.active_scanners
        assert "bb:bb:bb:bb:bb:bb" in manager.active_scanners

        pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
        assert pair.rssi_a_sees_b == -55.0
        assert pair.rssi_b_sees_a is None

    def test_update_cross_visibility_bidirectional(self):
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

    def test_calculate_suggested_offsets_no_data(self):
        """Test offset calculation with no data."""
        manager = ScannerCalibrationManager()
        offsets = manager.calculate_suggested_offsets()
        assert len(offsets) == 0

    def test_calculate_suggested_offsets_insufficient_samples(self):
        """Test offset calculation with insufficient samples."""
        manager = ScannerCalibrationManager()
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 2)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, 2)

        offsets = manager.calculate_suggested_offsets()
        # Should be empty because sample counts are below MIN_CROSS_VISIBILITY_SAMPLES
        assert len(offsets) == 0

    def test_calculate_suggested_offsets_symmetric(self):
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

    def test_calculate_suggested_offsets_multiple_pairs(self):
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

        # A has two pairs, both showing +5 offset needed
        assert offsets["aa:aa:aa:aa:aa:aa"] == -5
        # B: from A pair: +5, from C pair: 0, median = round((5+0)/2) but we use median
        # Actually B has: [+5 (from A pair), 0 (from C pair)] -> median = 2.5 -> rounded to 2
        # Wait, let me recalculate:
        # A-B: diff=10, A gets -5, B gets +5
        # A-C: diff=10, A gets -5, C gets +5
        # B-C: diff=0, B gets 0, C gets 0
        # So B contributions: [+5, 0] -> median = 2.5 -> round to 2
        # C contributions: [+5, 0] -> median = 2.5 -> round to 2
        assert offsets["bb:bb:bb:bb:bb:bb"] == 2
        assert offsets["cc:cc:cc:cc:cc:cc"] == 2

    def test_calculate_suggested_offsets_rounds_to_integer(self):
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

    def test_get_scanner_pair_info(self):
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

    def test_clear(self):
        """Test clearing calibration data."""
        manager = ScannerCalibrationManager()
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, 10)
        manager.calculate_suggested_offsets()

        manager.clear()

        assert len(manager.scanner_pairs) == 0
        assert len(manager.suggested_offsets) == 0
        assert len(manager.active_scanners) == 0


class MockAdvert:
    """Mock BermudaAdvert for testing."""

    def __init__(self, rssi_filtered=None, rssi=None, hist_rssi=None):
        self.rssi_filtered = rssi_filtered
        self.rssi = rssi
        self.hist_rssi = hist_rssi or []


class MockDevice:
    """Mock BermudaDevice for testing.

    Note: adverts dict uses tuple keys (device_addr, scanner_addr)
    where device_addr is the sender and scanner_addr is the receiver.
    """

    def __init__(self, address: str, adverts: dict | None = None, metadevice_sources: list | None = None):
        self.address = address
        self.adverts: dict[tuple[str, str], MockAdvert] = adverts or {}
        self.metadevice_sources = metadevice_sources or []


class TestUpdateScannerCalibration:
    """Test the update_scanner_calibration function."""

    def test_no_scanners(self):
        """Test with no scanners."""
        manager = ScannerCalibrationManager()
        offsets = update_scanner_calibration(manager, set(), {})
        assert len(offsets) == 0

    def test_single_scanner(self):
        """Test with single scanner (no cross-visibility possible)."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa"}
        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice("aa:aa:aa:aa:aa:aa"),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)
        assert len(offsets) == 0

    def test_two_scanners_no_visibility(self):
        """Test with two scanners that don't see each other."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}
        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice("aa:aa:aa:aa:aa:aa"),
            "bb:bb:bb:bb:bb:bb": MockDevice("bb:bb:bb:bb:bb:bb"),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)
        assert len(offsets) == 0

    def test_two_scanners_unidirectional(self):
        """Test with two scanners where only one sees the other."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # A sees B - key is (sender=B, receiver=A)
        advert_a_sees_b = MockAdvert(rssi_filtered=-55.0, hist_rssi=[-55, -56, -54, -55, -55])

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice("bb:bb:bb:bb:bb:bb"),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)
        # Not enough bidirectional data yet
        assert len(offsets) == 0

    def test_two_scanners_bidirectional(self):
        """Test with two scanners that see each other."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # A sees B at -55 - key is (sender=B, receiver=A)
        advert_a_sees_b = MockAdvert(rssi_filtered=-55.0, hist_rssi=[-55] * 10)
        # B sees A at -65 - key is (sender=A, receiver=B)
        advert_b_sees_a = MockAdvert(rssi_filtered=-65.0, hist_rssi=[-65] * 10)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a}
            ),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)

        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets
        assert offsets["aa:aa:aa:aa:aa:aa"] == -5
        assert offsets["bb:bb:bb:bb:bb:bb"] == 5

    def test_fallback_to_raw_rssi(self):
        """Test fallback to raw RSSI when filtered is not available."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # A sees B (no filtered RSSI, use raw) - key is (sender=B, receiver=A)
        advert_a_sees_b = MockAdvert(rssi_filtered=None, rssi=-55.0, hist_rssi=[-55] * 10)
        # B sees A - key is (sender=A, receiver=B)
        advert_b_sees_a = MockAdvert(rssi_filtered=-65.0, hist_rssi=[-65] * 10)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a}
            ),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)

        # Should still work with raw RSSI fallback
        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets

    def test_metadevice_sources_lookup(self):
        """Test that metadevice_sources are checked for scanner visibility."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # Scanner B broadcasts as iBeacon with different MAC "cc:cc:cc:cc:cc:cc"
        # A sees the iBeacon MAC - key is (sender=cc, receiver=A)
        advert_a_sees_b_via_ibeacon = MockAdvert(rssi_filtered=-55.0, hist_rssi=[-55] * 10)
        # B sees A directly - key is (sender=A, receiver=B)
        advert_b_sees_a = MockAdvert(rssi_filtered=-65.0, hist_rssi=[-65] * 10)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("cc:cc:cc:cc:cc:cc", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b_via_ibeacon}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a},
                metadevice_sources=["cc:cc:cc:cc:cc:cc"]  # B's iBeacon MAC
            ),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)

        # Should find cross-visibility via metadevice_sources
        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_none_rssi_values_ignored(self):
        """Test that None RSSI values are properly ignored."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # A sees B with None RSSI - key is (sender=B, receiver=A)
        advert_a_sees_b = MockAdvert(rssi_filtered=None, rssi=None)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice("bb:bb:bb:bb:bb:bb"),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)
        assert len(offsets) == 0

    def test_missing_device_in_devices_dict(self):
        """Test handling of scanner in list but not in devices dict."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # Only A exists in devices
        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice("aa:aa:aa:aa:aa:aa"),
        }

        # Should not crash
        offsets = update_scanner_calibration(manager, scanner_list, devices)
        assert len(offsets) == 0

    def test_equal_rssi_produces_zero_offset(self):
        """Test that equal RSSI values produce zero offset."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # Both see each other at the same RSSI - keys are (sender, receiver)
        advert_a_sees_b = MockAdvert(rssi_filtered=-60.0, hist_rssi=[-60] * 10)
        advert_b_sees_a = MockAdvert(rssi_filtered=-60.0, hist_rssi=[-60] * 10)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a}
            ),
        }

        offsets = update_scanner_calibration(manager, scanner_list, devices)

        assert offsets.get("aa:aa:aa:aa:aa:aa") == 0
        assert offsets.get("bb:bb:bb:bb:bb:bb") == 0
