"""
Tests for BermudaAdvert class in bermuda_advert.py.
"""

import pytest
from unittest.mock import MagicMock, patch
from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
)
from custom_components.bermuda.util import normalize_mac
from bleak.backends.scanner import AdvertisementData


@pytest.fixture
def mock_parent_device():
    """Fixture for mocking the parent BermudaDevice."""
    device = MagicMock(spec=BermudaDevice)
    device.address = normalize_mac("aa:bb:cc:dd:ee:ff")
    device.ref_power = -59
    device.name_bt_local_name = None
    device.name = "mock parent name"
    return device


@pytest.fixture
def mock_scanner_device():
    """Fixture for mocking the scanner BermudaDevice."""
    scanner = MagicMock(spec=BermudaDevice)
    scanner.address = normalize_mac("11:22:33:44:55:66")
    scanner.name = "Mock Scanner"
    scanner.area_id = "server_room"
    scanner.area_name = "server room"
    scanner.is_remote_scanner = True
    scanner.last_seen = 0.0
    scanner.stamps = {normalize_mac("aa:bb:cc:dd:ee:ff"): 123.45}
    scanner.async_as_scanner_get_stamp.return_value = 123.45
    return scanner


@pytest.fixture
def mock_advertisement_data():
    """Fixture for mocking AdvertisementData."""
    advert = MagicMock(spec=AdvertisementData)
    advert.rssi = -70
    advert.tx_power = -20
    advert.local_name = "Mock advert Local Name"
    advert.name = "Mock advert name"
    advert.manufacturer_data = {76: b"\x02\x15"}
    advert.service_data = {"0000abcd-0000-1000-8000-00805f9b34fb": b"\x01\x02"}
    advert.service_uuids = ["0000abcd-0000-1000-8000-00805f9b34fb"]
    return advert


@pytest.fixture
def bermuda_advert(mock_parent_device, mock_advertisement_data, mock_scanner_device):
    """Fixture for creating a BermudaAdvert instance."""
    options = {
        CONF_RSSI_OFFSETS: {normalize_mac("11:22:33:44:55:66"): 5},
        CONF_REF_POWER: -59,
        CONF_ATTENUATION: 2.0,
        CONF_MAX_VELOCITY: 3.0,
        CONF_SMOOTHING_SAMPLES: 5,
    }
    ba = BermudaAdvert(
        parent_device=mock_parent_device,
        advertisementdata=mock_advertisement_data,
        options=options,
        scanner_device=mock_scanner_device,
    )
    ba.name = "foo name"
    return ba


def test_bermuda_advert_initialization(bermuda_advert):
    """Test BermudaAdvert initialization."""
    assert bermuda_advert.device_address == normalize_mac("aa:bb:cc:dd:ee:ff")
    assert bermuda_advert.scanner_address == normalize_mac("11:22:33:44:55:66")
    assert bermuda_advert.ref_power == -59
    assert bermuda_advert.stamp == 123.45
    assert bermuda_advert.rssi == -70


def test_apply_new_scanner(bermuda_advert, mock_scanner_device):
    """Test apply_new_scanner method."""
    bermuda_advert.apply_new_scanner(mock_scanner_device)
    assert bermuda_advert.scanner_device == mock_scanner_device
    assert bermuda_advert.scanner_sends_stamps is True


def test_update_advertisement(bermuda_advert, mock_advertisement_data, mock_scanner_device):
    """Test update_advertisement method."""
    bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)
    assert bermuda_advert.rssi == -70
    assert bermuda_advert.tx_power == -20
    assert bermuda_advert.local_name[0][0] == "Mock advert Local Name"
    assert bermuda_advert.manufacturer_data[0][76] == b"\x02\x15"
    assert bermuda_advert.service_data[0]["0000abcd-0000-1000-8000-00805f9b34fb"] == b"\x01\x02"


def test_set_ref_power(bermuda_advert):
    """Test set_ref_power method."""
    new_distance = bermuda_advert.set_ref_power(-65)
    assert bermuda_advert.ref_power == -65
    assert new_distance is not None


def test_calculate_data_device_arrived(bermuda_advert):
    """Test calculate_data method when device arrives."""
    bermuda_advert.new_stamp = 123.45
    bermuda_advert.rssi_distance_raw = 5.0
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance == 5.0


def test_calculate_data_device_away(bermuda_advert):
    """Test calculate_data method when device is away."""
    bermuda_advert.stamp = 0.0
    bermuda_advert.new_stamp = None
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance is None


def test_to_dict(bermuda_advert):
    """Test to_dict method."""
    advert_dict = bermuda_advert.to_dict()
    assert isinstance(advert_dict, dict)
    assert advert_dict["device_address"] == normalize_mac("aa:bb:cc:dd:ee:ff")
    assert advert_dict["scanner_address"] == normalize_mac("11:22:33:44:55:66")


def test_repr(bermuda_advert):
    """Test __repr__ method."""
    repr_str = repr(bermuda_advert)
    assert repr_str == f"{normalize_mac('aa:bb:cc:dd:ee:ff')}__Mock Scanner"


def test_adaptive_stale_timeout_with_frequent_updates(bermuda_advert):
    """Test that adaptive timeout stays at minimum (60s) for frequently updating devices."""
    # Simulate a device that updates every 1 second
    base_time = 1000.0
    bermuda_advert.hist_stamp = [
        base_time,
        base_time - 1.0,
        base_time - 2.0,
        base_time - 3.0,
        base_time - 4.0,
    ]
    bermuda_advert.stamp = base_time
    bermuda_advert.new_stamp = None
    bermuda_advert.rssi_distance = 5.0

    # Device has 1s average interval, so adaptive timeout = max(60, 1*3) = 60s
    # At time base_time + 59, device should still be considered valid
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 59):
        bermuda_advert.calculate_data()
        # Should NOT be cleared because stamp (1000) is >= (1059 - 60) = 999
        assert bermuda_advert.rssi_distance == 5.0

    # Reset for next test
    bermuda_advert.rssi_distance = 5.0

    # At time base_time + 61, device should be considered stale
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 61):
        bermuda_advert.calculate_data()
        # Should be cleared because stamp (1000) < (1061 - 60) = 1001
        assert bermuda_advert.rssi_distance is None


def test_adaptive_stale_timeout_with_slow_updates(bermuda_advert):
    """Test that adaptive timeout increases for slow-updating devices (e.g., FMDN tags)."""
    # Simulate a device that updates every 30 seconds
    base_time = 2000.0
    bermuda_advert.hist_stamp = [
        base_time,
        base_time - 30.0,
        base_time - 60.0,
        base_time - 90.0,
        base_time - 120.0,
    ]
    bermuda_advert.stamp = base_time
    bermuda_advert.new_stamp = None
    bermuda_advert.rssi_distance = 5.0

    # Device has 30s average interval, so adaptive timeout = max(60, 30*3) = 90s
    # At time base_time + 89, device should still be considered valid
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 89):
        bermuda_advert.calculate_data()
        # Should NOT be cleared because stamp (2000) >= (2089 - 90) = 1999
        assert bermuda_advert.rssi_distance == 5.0

    # Reset for next test
    bermuda_advert.rssi_distance = 5.0

    # At time base_time + 91, device should be considered stale
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 91):
        bermuda_advert.calculate_data()
        # Should be cleared because stamp (2000) < (2091 - 90) = 2001
        assert bermuda_advert.rssi_distance is None


def test_adaptive_stale_timeout_capped_at_180s(bermuda_advert):
    """Test that adaptive timeout is capped at 180 seconds."""
    # Simulate a device that updates every 90 seconds
    base_time = 3000.0
    bermuda_advert.hist_stamp = [
        base_time,
        base_time - 90.0,
        base_time - 180.0,
        base_time - 270.0,
    ]
    bermuda_advert.stamp = base_time
    bermuda_advert.new_stamp = None
    bermuda_advert.rssi_distance = 5.0

    # Device has 90s average interval, so adaptive timeout would be 270s,
    # but it's capped at 180s
    # At time base_time + 179, device should still be considered valid
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 179):
        bermuda_advert.calculate_data()
        # Should NOT be cleared because stamp (3000) >= (3179 - 180) = 2999
        assert bermuda_advert.rssi_distance == 5.0

    # Reset for next test
    bermuda_advert.rssi_distance = 5.0

    # At time base_time + 181, device should be considered stale (capped at 180s)
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 181):
        bermuda_advert.calculate_data()
        # Should be cleared because stamp (3000) < (3181 - 180) = 3001
        assert bermuda_advert.rssi_distance is None


def test_adaptive_stale_timeout_with_insufficient_history(bermuda_advert):
    """Test that adaptive timeout uses default 60s when history is insufficient."""
    base_time = 4000.0
    # Only one timestamp - not enough to calculate intervals
    bermuda_advert.hist_stamp = [base_time]
    bermuda_advert.stamp = base_time
    bermuda_advert.new_stamp = None
    bermuda_advert.rssi_distance = 5.0

    # With insufficient history, default timeout of 60s should be used
    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 59):
        bermuda_advert.calculate_data()
        assert bermuda_advert.rssi_distance == 5.0

    bermuda_advert.rssi_distance = 5.0

    with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=base_time + 61):
        bermuda_advert.calculate_data()
        assert bermuda_advert.rssi_distance is None
