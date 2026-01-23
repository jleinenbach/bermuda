"""Tests for scanner auto-calibration functionality."""

from __future__ import annotations

import pytest

from custom_components.bermuda.filters import (
    CALIBRATION_MIN_PAIRS,
    CALIBRATION_MIN_SAMPLES,
    CALIBRATION_SCANNER_TIMEOUT,
)
from custom_components.bermuda.scanner_calibration import (
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
        pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
        # Add samples in one direction only
        for _ in range(10):
            pair.kalman_ab.update(-55.0)
        assert not pair.has_bidirectional_data
        assert pair.rssi_difference is None

    def test_bidirectional_data_insufficient_samples(self):
        """Test bidirectional data with insufficient samples."""
        pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
        # Add fewer samples than required
        for _ in range(CALIBRATION_MIN_SAMPLES - 1):
            pair.kalman_ab.update(-55.0)
        for _ in range(CALIBRATION_MIN_SAMPLES):
            pair.kalman_ba.update(-65.0)
        assert not pair.has_bidirectional_data
        assert pair.rssi_difference is None

    def test_bidirectional_data_sufficient_samples(self):
        """Test bidirectional data with sufficient samples."""
        pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
        # Add enough samples in both directions
        for _ in range(CALIBRATION_MIN_SAMPLES):
            pair.kalman_ab.update(-55.0)
            pair.kalman_ba.update(-65.0)
        assert pair.has_bidirectional_data
        # Check difference is approximately 10 (Kalman may have slight variation)
        assert pair.rssi_difference is not None
        assert abs(pair.rssi_difference - 10.0) < 1.0  # A sees B 10 dB stronger

    def test_rssi_difference_negative(self):
        """Test negative RSSI difference (B receives stronger)."""
        pair = ScannerPairData(scanner_a="aa:bb:cc:dd:ee:01", scanner_b="aa:bb:cc:dd:ee:02")
        # Add enough samples with B seeing stronger
        for _ in range(CALIBRATION_MIN_SAMPLES):
            pair.kalman_ab.update(-70.0)
            pair.kalman_ba.update(-55.0)
        # B sees A 15 dB stronger, so difference is negative
        assert pair.rssi_difference is not None
        assert abs(pair.rssi_difference - (-15.0)) < 1.0


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
            rssi_raw=-55.0,
        )

        assert len(manager.scanner_pairs) == 1
        assert "aa:aa:aa:aa:aa:aa" in manager.active_scanners
        assert "bb:bb:bb:bb:bb:bb" in manager.active_scanners

        pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
        assert pair.sample_count_ab == 1
        assert pair.sample_count_ba == 0

    def test_update_cross_visibility_bidirectional(self):
        """Test updating cross visibility with bidirectional data."""
        manager = ScannerCalibrationManager()

        # Add enough samples for calibration
        for _ in range(CALIBRATION_MIN_SAMPLES):
            # A sees B
            manager.update_cross_visibility(
                receiver_addr="aa:aa:aa:aa:aa:aa",
                sender_addr="bb:bb:bb:bb:bb:bb",
                rssi_raw=-55.0,
            )
            # B sees A
            manager.update_cross_visibility(
                receiver_addr="bb:bb:bb:bb:bb:bb",
                sender_addr="aa:aa:aa:aa:aa:aa",
                rssi_raw=-65.0,
            )

        pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
        assert pair.has_bidirectional_data
        assert pair.rssi_difference is not None
        assert abs(pair.rssi_difference - 10.0) < 1.0

    def test_calculate_suggested_offsets_no_data(self):
        """Test offset calculation with no data."""
        manager = ScannerCalibrationManager()
        offsets = manager.calculate_suggested_offsets()
        assert len(offsets) == 0

    def test_calculate_suggested_offsets_insufficient_samples(self):
        """Test offset calculation with insufficient samples."""
        manager = ScannerCalibrationManager()
        # Only add 2 samples - not enough
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)

        offsets = manager.calculate_suggested_offsets()
        # Should be empty because sample counts are below CALIBRATION_MIN_SAMPLES
        assert len(offsets) == 0

    def test_calculate_suggested_offsets_symmetric(self):
        """Test offset calculation produces symmetric results."""
        manager = ScannerCalibrationManager()

        # A sees B at -55, B sees A at -65
        # Difference is 10 dB, so A receives 5 dB stronger, B receives 5 dB weaker
        # A needs offset -5, B needs offset +5
        for _ in range(CALIBRATION_MIN_SAMPLES):
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)

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
        for _ in range(CALIBRATION_MIN_SAMPLES):
            # A sees B at -55, B sees A at -65 (diff +10, A is +5 stronger)
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)
            # A sees C at -50, C sees A at -60 (diff +10, A is +5 stronger)
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "cc:cc:cc:cc:cc:cc", -50.0)
            manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "aa:aa:aa:aa:aa:aa", -60.0)
            # B sees C at -58, C sees B at -58 (diff 0, both equal)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "cc:cc:cc:cc:cc:cc", -58.0)
            manager.update_cross_visibility("cc:cc:cc:cc:cc:cc", "bb:bb:bb:bb:bb:bb", -58.0)

        offsets = manager.calculate_suggested_offsets()

        # A has two pairs, both showing +5 offset needed
        assert offsets["aa:aa:aa:aa:aa:aa"] == -5
        # B: from A pair: +5, from C pair: 0, median = 2.5 -> rounded to 2
        # C: from A pair: +5, from B pair: 0, median = 2.5 -> rounded to 2
        assert offsets["bb:bb:bb:bb:bb:bb"] == 2
        assert offsets["cc:cc:cc:cc:cc:cc"] == 2

    def test_calculate_suggested_offsets_rounds_to_integer(self):
        """Test that offsets are rounded to integers."""
        manager = ScannerCalibrationManager()

        # A sees B at -55, B sees A at -62 (diff +7, so offset = ±3.5)
        for _ in range(CALIBRATION_MIN_SAMPLES):
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -62.0)

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
        for _ in range(CALIBRATION_MIN_SAMPLES):
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0)

        info = manager.get_scanner_pair_info()

        assert len(info) == 1
        pair_info = info[0]
        assert pair_info["scanner_a"] == "aa:aa:aa:aa:aa:aa"
        assert pair_info["scanner_b"] == "bb:bb:bb:bb:bb:bb"
        assert pair_info["rssi_a_sees_b"] is not None
        assert pair_info["rssi_b_sees_a"] is not None
        assert pair_info["bidirectional"] is True
        assert pair_info["difference"] is not None
        assert abs(pair_info["difference"] - 10.0) < 1.0

    def test_clear(self):
        """Test clearing calibration data."""
        manager = ScannerCalibrationManager()
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0)
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
        advert_a_sees_b = MockAdvert(rssi_filtered=-55.0, hist_rssi=[-55] * CALIBRATION_MIN_SAMPLES)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa", adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
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

        # Adverts are stored on the SENDING device with key (sender_mac, receiver_scanner)
        # A sees B at -55: advert stored on B's device with key (B, A)
        # Note: update_scanner_calibration uses raw rssi, not rssi_filtered
        advert_a_sees_b = MockAdvert(rssi=-55.0, rssi_filtered=-55.0, hist_rssi=[-55] * CALIBRATION_MIN_SAMPLES)
        # B sees A at -65: advert stored on A's device with key (A, B)
        advert_b_sees_a = MockAdvert(rssi=-65.0, rssi_filtered=-65.0, hist_rssi=[-65] * CALIBRATION_MIN_SAMPLES)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                # A's adverts: when B sees A, the advert is stored here
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a},
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                # B's adverts: when A sees B, the advert is stored here
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b},
            ),
        }

        # Need to call update_scanner_calibration CALIBRATION_MIN_SAMPLES times
        # to accumulate enough samples for calibration
        for _ in range(CALIBRATION_MIN_SAMPLES):
            offsets = update_scanner_calibration(manager, scanner_list, devices)

        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets
        assert offsets["aa:aa:aa:aa:aa:aa"] == -5
        assert offsets["bb:bb:bb:bb:bb:bb"] == 5

    def test_fallback_to_raw_rssi(self):
        """Test fallback to raw RSSI when filtered is not available."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # Adverts stored on SENDING device
        # A sees B (no filtered RSSI, use raw): stored on B with key (B, A)
        # Note: update_scanner_calibration uses raw rssi
        advert_a_sees_b = MockAdvert(rssi=-55.0, rssi_filtered=None, hist_rssi=[-55] * CALIBRATION_MIN_SAMPLES)
        # B sees A: stored on A with key (A, B)
        advert_b_sees_a = MockAdvert(rssi=-65.0, rssi_filtered=-65.0, hist_rssi=[-65] * CALIBRATION_MIN_SAMPLES)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa", adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb", adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
            ),
        }

        # Need to call CALIBRATION_MIN_SAMPLES times to accumulate enough samples
        for _ in range(CALIBRATION_MIN_SAMPLES):
            offsets = update_scanner_calibration(manager, scanner_list, devices)

        # Should still work with raw RSSI fallback
        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets

    def test_metadevice_sources_lookup(self):
        """Test that metadevice_sources are checked for scanner visibility."""
        manager = ScannerCalibrationManager()
        scanner_list = {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}

        # Scanner B broadcasts as iBeacon with different MAC "cc:cc:cc:cc:cc:cc"
        # Advert stored on the iBeacon device (cc:cc) with key (cc, A)
        # Note: update_scanner_calibration uses raw rssi
        advert_a_sees_b_via_ibeacon = MockAdvert(
            rssi=-55.0, rssi_filtered=-55.0, hist_rssi=[-55] * CALIBRATION_MIN_SAMPLES
        )
        # B sees A: stored on A with key (A, B)
        advert_b_sees_a = MockAdvert(rssi=-65.0, rssi_filtered=-65.0, hist_rssi=[-65] * CALIBRATION_MIN_SAMPLES)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa", adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a}
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b_via_ibeacon},
                metadevice_sources=["cc:cc:cc:cc:cc:cc"],  # B's iBeacon MAC
            ),
        }

        # Need to call CALIBRATION_MIN_SAMPLES times to accumulate enough samples
        for _ in range(CALIBRATION_MIN_SAMPLES):
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
                "aa:aa:aa:aa:aa:aa", adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b}
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

        # Both see each other at the same RSSI
        # Adverts stored on SENDING device with key (sender_mac, receiver_scanner)
        # A sees B: advert stored on B's device with key (B, A)
        # Note: update_scanner_calibration uses raw rssi
        advert_a_sees_b = MockAdvert(rssi=-60.0, rssi_filtered=-60.0, hist_rssi=[-60] * CALIBRATION_MIN_SAMPLES)
        # B sees A: advert stored on A's device with key (A, B)
        advert_b_sees_a = MockAdvert(rssi=-60.0, rssi_filtered=-60.0, hist_rssi=[-60] * CALIBRATION_MIN_SAMPLES)

        devices = {
            "aa:aa:aa:aa:aa:aa": MockDevice(
                "aa:aa:aa:aa:aa:aa",
                # A's adverts: when B sees A, the advert is stored here
                adverts={("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"): advert_b_sees_a},
            ),
            "bb:bb:bb:bb:bb:bb": MockDevice(
                "bb:bb:bb:bb:bb:bb",
                # B's adverts: when A sees B, the advert is stored here
                adverts={("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa"): advert_a_sees_b},
            ),
        }

        # Need to call CALIBRATION_MIN_SAMPLES times to accumulate enough samples
        for _ in range(CALIBRATION_MIN_SAMPLES):
            offsets = update_scanner_calibration(manager, scanner_list, devices)

        assert offsets.get("aa:aa:aa:aa:aa:aa") == 0
        assert offsets.get("bb:bb:bb:bb:bb:bb") == 0


class TestKalmanTimestampIntegration:
    """Test Kalman filter timestamp integration for scanner calibration."""

    def test_kalman_receives_timestamp(self):
        """Verify Kalman filter receives timestamp for dt calculation."""
        manager = ScannerCalibrationManager()
        ts1 = 1000.0
        ts2 = 1005.0  # 5 seconds later

        manager.update_cross_visibility(
            receiver_addr="aa:aa:aa:aa:aa:aa",
            sender_addr="bb:bb:bb:bb:bb:bb",
            rssi_raw=-60.0,
            timestamp=ts1,
        )
        manager.update_cross_visibility(
            receiver_addr="aa:aa:aa:aa:aa:aa",
            sender_addr="bb:bb:bb:bb:bb:bb",
            rssi_raw=-62.0,
            timestamp=ts2,
        )

        # Get the pair and verify Kalman filter has the timestamp
        pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
        # The Kalman filter should have stored the last timestamp internally
        assert pair.kalman_ab._last_timestamp == ts2
        # Pair should also track last update time
        assert pair.last_update_ab == ts2

    def test_pair_tracks_update_timestamps(self):
        """Verify ScannerPairData tracks timestamps for both directions."""
        manager = ScannerCalibrationManager()
        ts1 = 1000.0
        ts2 = 1010.0

        # A sees B
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=ts1)
        # B sees A
        manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, timestamp=ts2)

        pair = manager.scanner_pairs[("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")]
        assert pair.last_update_ab == ts1  # A sees B
        assert pair.last_update_ba == ts2  # B sees A


class TestScannerOnlineDetection:
    """Test scanner online/offline detection for calibration."""

    def test_scanner_online_within_timeout(self):
        """Verify scanner is considered online within timeout."""
        manager = ScannerCalibrationManager()
        nowstamp = 1000.0

        # Update with timestamp
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=nowstamp)

        # Check immediately - should be online
        assert manager._is_scanner_online("aa:aa:aa:aa:aa:aa", nowstamp)
        assert manager._is_scanner_online("bb:bb:bb:bb:bb:bb", nowstamp)

        # Check just before timeout - should still be online
        check_time = nowstamp + CALIBRATION_SCANNER_TIMEOUT - 1
        assert manager._is_scanner_online("aa:aa:aa:aa:aa:aa", check_time)

    def test_scanner_offline_after_timeout(self):
        """Verify scanner is considered offline after timeout."""
        manager = ScannerCalibrationManager()
        nowstamp = 1000.0

        # Update with timestamp
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=nowstamp)

        # Check after timeout - should be offline
        check_time = nowstamp + CALIBRATION_SCANNER_TIMEOUT + 1
        assert not manager._is_scanner_online("aa:aa:aa:aa:aa:aa", check_time)
        assert not manager._is_scanner_online("bb:bb:bb:bb:bb:bb", check_time)

    def test_unknown_scanner_is_offline(self):
        """Verify unknown scanner is considered offline."""
        manager = ScannerCalibrationManager()
        assert not manager._is_scanner_online("unknown:scanner", 1000.0)

    def test_offline_scanner_excluded_from_offset(self):
        """Verify offline scanners don't contribute to offset calculation."""
        manager = ScannerCalibrationManager()
        nowstamp = 1000.0

        # Build calibration data
        for i in range(CALIBRATION_MIN_SAMPLES):
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=nowstamp + i)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, timestamp=nowstamp + i)

        # Current time just after last update - both scanners online
        check_time = nowstamp + CALIBRATION_MIN_SAMPLES

        # Verify offsets are calculated when online
        offsets = manager.calculate_suggested_offsets(nowstamp=check_time)
        assert "aa:aa:aa:aa:aa:aa" in offsets
        assert "bb:bb:bb:bb:bb:bb" in offsets

        # Simulate time passing beyond timeout (scanner A goes offline)
        offline_check_time = nowstamp + CALIBRATION_MIN_SAMPLES + CALIBRATION_SCANNER_TIMEOUT + 100

        # Clear existing offsets to force recalculation
        manager.suggested_offsets.clear()

        # Recalculate at offline time - pair should be skipped due to offline scanner
        offsets = manager.calculate_suggested_offsets(nowstamp=offline_check_time)
        # Neither scanner should get an offset since the pair is skipped
        assert "aa:aa:aa:aa:aa:aa" not in offsets
        assert "bb:bb:bb:bb:bb:bb" not in offsets

    def test_scanner_comes_back_online(self):
        """Verify scanner is included again after coming back online."""
        manager = ScannerCalibrationManager()
        nowstamp = 1000.0

        # Initial calibration
        for i in range(CALIBRATION_MIN_SAMPLES):
            manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=nowstamp + i)
            manager.update_cross_visibility("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa", -65.0, timestamp=nowstamp + i)

        # Simulate scanner going offline
        offline_time = nowstamp + CALIBRATION_SCANNER_TIMEOUT + 100
        manager.scanner_last_seen["aa:aa:aa:aa:aa:aa"] = nowstamp  # Old timestamp
        assert not manager._is_scanner_online("aa:aa:aa:aa:aa:aa", offline_time)

        # Scanner comes back online
        comeback_time = offline_time + 10
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -56.0, timestamp=comeback_time)
        assert manager._is_scanner_online("aa:aa:aa:aa:aa:aa", comeback_time)

    def test_get_scanner_pair_info_includes_online_status(self):
        """Verify diagnostic info includes online status."""
        manager = ScannerCalibrationManager()
        nowstamp = 1000.0

        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=nowstamp)

        # Pass nowstamp to ensure scanners appear online (they were just seen)
        info = manager.get_scanner_pair_info(nowstamp=nowstamp)
        assert len(info) == 1

        pair_info = info[0]
        assert "scanner_a_online" in pair_info
        assert "scanner_b_online" in pair_info
        assert "last_update_ab" in pair_info
        assert "last_update_ba" in pair_info
        assert pair_info["scanner_a_online"] is True
        assert pair_info["scanner_b_online"] is True
        assert pair_info["last_update_ab"] == nowstamp

    def test_clear_also_clears_scanner_last_seen(self):
        """Verify clear() also clears scanner_last_seen tracking."""
        manager = ScannerCalibrationManager()
        manager.update_cross_visibility("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb", -55.0, timestamp=1000.0)

        assert len(manager.scanner_last_seen) > 0

        manager.clear()

        assert len(manager.scanner_last_seen) == 0
