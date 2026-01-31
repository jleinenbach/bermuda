"""Test util.py in Bermuda."""

from __future__ import annotations

# from homeassistant.core import HomeAssistant

from math import floor

import pytest

from custom_components.bermuda import util
from custom_components.bermuda.filters import KalmanFilter


def test_mac_math_offset() -> None:
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", 2) == "aa:bb:cc:dd:ee:f1"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", -3) == "aa:bb:cc:dd:ee:ec"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ff", 2) is None
    assert util.mac_math_offset("clearly_not:a-mac_address", 2) is None
    assert util.mac_math_offset(None, 4) is None


def test_normalize_mac_variants() -> None:
    assert util.normalize_mac("AA:bb:CC:88:Ff:00") == "aa:bb:cc:88:ff:00"
    assert util.normalize_mac("aa_bb_CC_dd_ee_ff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("aa-77-CC-dd-ee-ff") == "aa:77:cc:dd:ee:ff"
    assert util.normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_non_mac() -> None:
    with pytest.raises(ValueError):
        util.normalize_mac("fmdn:abc123")


def test_normalize_identifier_and_mac_dispatch() -> None:
    assert util.normalize_identifier("AABBCCDDEEFF") == "aabbccddeeff"
    assert util.normalize_identifier("12345678-1234-5678-9abc-def012345678_extra") == (
        "12345678123456789abcdef012345678_extra"
    )
    assert util.normalize_address("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert util.normalize_address("fmdn:Device-ID") == "fmdn:device-id"


def test_mac_explode_formats() -> None:
    ex = util.mac_explode_formats("aa:bb:cc:77:ee:ff")
    assert "aa:bb:cc:77:ee:ff" in ex
    assert "aa-bb-cc-77-ee-ff" in ex
    for e in ex:
        assert len(e) in [12, 17]


def test_mac_redact() -> None:
    assert util.mac_redact("aa:bb:cc:77:ee:ff", "tEstMe") == "aa::tEstMe::ff"
    assert util.mac_redact("howdy::doody::friend", "PLEASENOE") == "ho::PLEASENOE::nd"


def test_rssi_to_metres() -> None:
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


def test_clean_charbuf() -> None:
    assert util.clean_charbuf("a Normal string.") == "a Normal string."
    assert util.clean_charbuf("Broken\000String\000Fixed\000\000\000") == "Broken"


def test_clean_charbuf_none_input() -> None:
    """Test that clean_charbuf returns empty string for None input."""
    assert util.clean_charbuf(None) == ""


def test_rssi_to_metres_missing_ref_power() -> None:
    """Test that rssi_to_metres raises ValueError when ref_power is None."""
    with pytest.raises(ValueError, match="ref_power must be provided"):
        util.rssi_to_metres(-60, ref_power=None, attenuation=3.5)


def test_rssi_to_metres_missing_attenuation() -> None:
    """Test that rssi_to_metres raises ValueError when attenuation is None."""
    with pytest.raises(ValueError, match="attenuation must be provided"):
        util.rssi_to_metres(-60, ref_power=-55, attenuation=None)


def test_mac_norm_with_non_mac() -> None:
    """Test that mac_norm falls back to normalize_identifier for non-MAC inputs."""
    # Test with UUID-like identifier
    assert util.mac_norm("fmdn:DEVICE-123") == "fmdn:device-123"
    # Test with plain identifier
    assert util.mac_norm("some_identifier") == "some_identifier"
    # Test with UUID
    assert util.mac_norm("12345678-1234-5678-9ABC-DEF012345678") == "12345678123456789abcdef012345678"


def test_mac_norm_with_mac() -> None:
    """Test that mac_norm correctly normalizes MAC addresses."""
    assert util.mac_norm("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert util.mac_norm("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"


def test_mac_explode_formats_with_non_mac() -> None:
    """Test that mac_explode_formats returns only normalized identifier for non-MAC."""
    result = util.mac_explode_formats("fmdn:DEVICE-123")
    assert result == {"fmdn:device-123"}


def test_mac_explode_formats_with_uuid() -> None:
    """Test that mac_explode_formats handles UUID-like identifiers."""
    result = util.mac_explode_formats("12345678-1234-5678-9ABC-DEF012345678_suffix")
    assert result == {"12345678123456789abcdef012345678_suffix"}


def test_mac_hex_dotted_format() -> None:
    """Test that _mac_hex handles Cisco-style dotted MAC format."""
    # Directly test the _mac_hex function with dotted format
    result = util._mac_hex("aabb.ccdd.eeff")
    assert result == "aabbccddeeff"
    # Uppercase variant
    result = util._mac_hex("AABB.CCDD.EEFF")
    assert result == "aabbccddeeff"


class TestRssiToMetresEdgeCases:
    """Additional edge case tests for rssi_to_metres."""

    def test_rssi_to_metres_min_distance_floor(self) -> None:
        """Test that rssi_to_metres enforces minimum distance."""
        # Very strong signal should return MIN_DISTANCE (0.1m)
        result = util.rssi_to_metres(-20, -55, 3.5)
        assert result == 0.1

    def test_rssi_to_metres_far_field(self) -> None:
        """Test rssi_to_metres in far-field region (> 6m breakpoint)."""
        # Distance well beyond 6m breakpoint
        result = util.rssi_to_metres(-80, -55, 3.5)
        assert result > 6.0  # Should be in far-field

    def test_rssi_to_metres_near_field(self) -> None:
        """Test rssi_to_metres in near-field region (< 6m breakpoint)."""
        # Near-field: signal close to ref_power
        result = util.rssi_to_metres(-58, -55, 3.5)
        assert 1.0 < result < 6.0  # Should be in near-field


class TestKalmanFilter:
    """Tests for the KalmanFilter class from filters module.

    Note: KalmanFilter is in custom_components.bermuda.filters.kalman.
    The dataclass-based filter has different interface from the removed util.py version:
    - estimate starts at 0.0 (not None)
    - is_initialized property indicates if first measurement received
    - reset() clears state (no initial_estimate parameter)
    """

    def test_kalman_initialization(self) -> None:
        """Test that KalmanFilter initializes with correct defaults."""
        kf = KalmanFilter()
        assert kf.estimate == 0.0  # Dataclass default
        assert not kf.is_initialized

    def test_kalman_first_measurement(self) -> None:
        """Test that first measurement initializes the filter."""
        kf = KalmanFilter()
        result = kf.update(-70.0)
        assert result == -70.0
        assert kf.estimate == -70.0
        assert kf.is_initialized

    def test_kalman_filters_spike(self) -> None:
        """Test that Kalman filter dampens signal spikes."""
        kf = KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        # Establish baseline at -70 dBm
        for _ in range(5):
            kf.update(-70.0)
        baseline = kf.estimate

        # Introduce a strong spike (-45 dBm is stronger/closer than -70 dBm)
        result = kf.update(-45.0)

        # The result should be between baseline (-70) and spike (-45)
        # In RSSI: -45 > -70 numerically (stronger signal = less negative)
        assert result < -45.0  # Not fully following the spike
        assert result > baseline  # Moved toward spike but dampened

    def test_kalman_responds_to_approach(self) -> None:
        """Test that filter responds to genuine device approach."""
        kf = KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        results = []
        # Simulate device approaching
        for rssi in [-80, -75, -70, -65, -60, -55]:
            results.append(kf.update(rssi))

        # Filtered values should follow the trend
        assert results[-1] > results[0]  # Getting stronger
        # But with smoothing lag
        assert results[-1] < -55  # Not fully caught up yet

    def test_kalman_reduces_variance(self) -> None:
        """Test that Kalman filter reduces measurement variance."""
        kf = KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        raw = [-60, -61, -59, -60, -62, -58, -60, -61, -59, -60]
        filtered = [kf.update(r) for r in raw]

        raw_variance = max(raw) - min(raw)
        filtered_variance = max(filtered) - min(filtered)

        # Filtered variance should be significantly less
        assert filtered_variance < raw_variance * 0.5

    def test_kalman_reset(self) -> None:
        """Test that reset clears filter state."""
        kf = KalmanFilter()
        kf.update(-70.0)
        kf.update(-65.0)

        assert kf.is_initialized

        kf.reset()
        assert not kf.is_initialized

    def test_kalman_adaptive_stronger_signal_more_influence(self) -> None:
        """Test that stronger signals have more influence with adaptive update."""
        # ref_power of -55 dBm is typical for BLE at 1m
        ref_power = -55.0

        # Test with strong signal (-50 dBm, above threshold)
        kf_strong = KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        kf_strong.update(-70.0)  # Initialize at -70
        kf_strong.update_adaptive(-50.0, ref_power)  # Strong signal update
        strong_influence = abs(kf_strong.estimate - (-70.0))

        # Test with weak signal (-80 dBm, below threshold)
        kf_weak = KalmanFilter(process_noise=1.0, measurement_noise=10.0)
        kf_weak.update(-70.0)  # Initialize at -70
        kf_weak.update_adaptive(-80.0, ref_power)  # Weak signal update
        weak_influence = abs(kf_weak.estimate - (-70.0))

        # Strong signal should move estimate MORE (higher influence)
        # Weak signal should move estimate LESS (lower influence)
        assert strong_influence > weak_influence

    def test_kalman_adaptive_weak_signal_dampened(self) -> None:
        """Test that very weak signals are heavily dampened."""
        ref_power = -55.0  # typical BLE ref_power at 1m
        kf = KalmanFilter(process_noise=1.0, measurement_noise=10.0)

        # Establish baseline at -60 dBm
        for _ in range(5):
            kf.update_adaptive(-60.0, ref_power)

        baseline = kf.estimate

        # Apply very weak signal (-90 dBm, far below threshold)
        # This should have minimal influence due to high adaptive noise
        kf.update_adaptive(-90.0, ref_power)

        # Estimate should barely change (weak signal heavily dampened)
        assert abs(kf.estimate - baseline) < 5.0  # Less than 5 dBm change


class TestGetScanner:
    """Tests for BermudaDevice.get_scanner method."""

    def test_get_scanner_returns_none_when_no_adverts(self) -> None:
        """Test get_scanner returns None when no adverts exist."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.adverts = {}

        result = device.get_scanner("11:22:33:44:55:66")

        assert result is None

    def test_get_scanner_returns_matching_advert(self) -> None:
        """Test get_scanner returns matching advert via _adverts_by_scanner cache."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)

        # Create mock adverts
        mock_advert1 = MagicMock()
        mock_advert1.scanner_address = "11:22:33:44:55:66"
        mock_advert1.stamp = 100.0

        mock_advert2 = MagicMock()
        mock_advert2.scanner_address = "77:88:99:aa:bb:cc"
        mock_advert2.stamp = 200.0

        # Populate the scanner cache directly (normally done by calculate_data())
        device._adverts_by_scanner = {
            "11:22:33:44:55:66": mock_advert1,
            "77:88:99:aa:bb:cc": mock_advert2,
        }

        result = device.get_scanner("11:22:33:44:55:66")

        assert result == mock_advert1

    def test_get_scanner_returns_most_recent_advert(self) -> None:
        """Test get_scanner returns most recent advert when cache has latest."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)

        # Create mock adverts with same scanner but different timestamps
        mock_advert2 = MagicMock()
        mock_advert2.scanner_address = "11:22:33:44:55:66"
        mock_advert2.stamp = 200.0  # More recent

        # The cache stores only the most recent advert per scanner
        # (calculate_data() builds this by comparing stamps)
        device._adverts_by_scanner = {
            "11:22:33:44:55:66": mock_advert2,
        }

        result = device.get_scanner("11:22:33:44:55:66")

        assert result == mock_advert2

    def test_get_scanner_handles_none_stamp(self) -> None:
        """Test get_scanner handles advert with None stamp via cache."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)

        mock_advert = MagicMock()
        mock_advert.scanner_address = "11:22:33:44:55:66"
        mock_advert.stamp = None

        # Cache populated even for adverts with None stamp
        device._adverts_by_scanner = {"11:22:33:44:55:66": mock_advert}

        result = device.get_scanner("11:22:33:44:55:66")

        assert result == mock_advert


class TestProcessManufacturerData:
    """Tests for process_manufacturer_data method."""

    def test_process_manufacturer_data_updates_manufacturer(self) -> None:
        """Test process_manufacturer_data updates manufacturer from service uuids."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        mock_coordinator.get_manufacturer_from_id.return_value = ("Apple Inc.", False)
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.manufacturer = None

        mock_advert = MagicMock()
        mock_advert.service_uuids = ["0000abcd-0000-1000-8000-00805f9b34fb"]
        mock_advert.manufacturer_data = []

        device.process_manufacturer_data(mock_advert)

        assert device.manufacturer == "Apple Inc."

    def test_process_manufacturer_data_with_ibeacon(self) -> None:
        """Test process_manufacturer_data detects iBeacon data."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import METADEVICE_TYPE_IBEACON_SOURCE

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        mock_coordinator.get_manufacturer_from_id.return_value = ("Apple Inc.", False)
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)

        # iBeacon manufacturer data: company code 0x004C (Apple), type 0x02
        # Format: 0x02 + length + uuid (16 bytes) + major (2 bytes) + minor (2 bytes) + tx_power (1 byte)
        ibeacon_data = (
            b"\x02\x15"  # Type and length
            + b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"  # UUID (16 bytes)
            + b"\x00\x01"  # Major (1)
            + b"\x00\x02"  # Minor (2)
            + b"\xc8"  # TX Power
        )

        mock_advert = MagicMock()
        mock_advert.service_uuids = []
        mock_advert.manufacturer_data = [{0x004C: ibeacon_data}]

        device.process_manufacturer_data(mock_advert)

        assert METADEVICE_TYPE_IBEACON_SOURCE in device.metadevice_type
        assert device.beacon_uuid == "0102030405060708090a0b0c0d0e0f10"
        assert device.beacon_major == "1"
        assert device.beacon_minor == "2"

    def test_process_manufacturer_data_with_short_ibeacon(self) -> None:
        """Test process_manufacturer_data handles short iBeacon (22 bytes, no tx_power)."""
        from unittest.mock import MagicMock
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import METADEVICE_TYPE_IBEACON_SOURCE

        mock_coordinator = MagicMock()
        mock_coordinator.options = {}
        mock_coordinator.get_manufacturer_from_id.return_value = ("Apple Inc.", False)
        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)

        # Short iBeacon data (22 bytes, missing tx_power)
        ibeacon_data = (
            b"\x02\x15"  # Type and length
            + b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"  # UUID
            + b"\x00\x03"  # Major (3)
            + b"\x00\x04"  # Minor (4)
        )

        mock_advert = MagicMock()
        mock_advert.service_uuids = []
        mock_advert.manufacturer_data = [{0x004C: ibeacon_data}]

        device.process_manufacturer_data(mock_advert)

        assert METADEVICE_TYPE_IBEACON_SOURCE in device.metadevice_type
        assert device.beacon_major == "3"
        assert device.beacon_minor == "4"


class TestCalculateDataExtended:
    """Extended tests for calculate_data method."""

    def test_calculate_data_with_invalid_devices_option(self) -> None:
        """Test calculate_data handles non-list CONF_DEVICES."""
        from unittest.mock import MagicMock, patch
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import CONF_DEVICES, CONF_DEVTRACK_TIMEOUT

        mock_coordinator = MagicMock()
        mock_coordinator.options = {
            CONF_DEVICES: "not_a_list",  # Invalid - should be list
            CONF_DEVTRACK_TIMEOUT: 300,
        }
        mock_coordinator.count_active_scanners.return_value = 1

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.last_seen = 1.0

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Should not raise, create_sensor should be False since address not in empty set
        assert device.create_sensor is False

    def test_calculate_data_fmdn_mode_resolved_only(self) -> None:
        """Test calculate_data respects FMDN_MODE_RESOLVED_ONLY."""
        from unittest.mock import MagicMock, patch
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import (
            CONF_DEVICES,
            CONF_DEVTRACK_TIMEOUT,
            CONF_FMDN_MODE,
            FMDN_MODE_RESOLVED_ONLY,
            METADEVICE_TYPE_FMDN_SOURCE,
        )

        mock_coordinator = MagicMock()
        mock_coordinator.options = {
            CONF_DEVICES: [],
            CONF_DEVTRACK_TIMEOUT: 300,
            CONF_FMDN_MODE: FMDN_MODE_RESOLVED_ONLY,
        }
        mock_coordinator.count_active_scanners.return_value = 1

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)
        device.last_seen = 1.0

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # FMDN source with RESOLVED_ONLY mode should have create_sensor=False
        assert device.create_sensor is False

    def test_calculate_data_invalid_fmdn_mode_uses_default(self) -> None:
        """Test calculate_data uses default for invalid FMDN mode."""
        from unittest.mock import MagicMock, patch
        from custom_components.bermuda.bermuda_device import BermudaDevice
        from custom_components.bermuda.const import CONF_DEVICES, CONF_DEVTRACK_TIMEOUT, CONF_FMDN_MODE

        mock_coordinator = MagicMock()
        mock_coordinator.options = {
            CONF_DEVICES: [],
            CONF_DEVTRACK_TIMEOUT: 300,
            CONF_FMDN_MODE: "invalid_mode",
        }
        mock_coordinator.count_active_scanners.return_value = 1

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.last_seen = 1.0

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Should not raise
        assert device.zone == "not_home"
