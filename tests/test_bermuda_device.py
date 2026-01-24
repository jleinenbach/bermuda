"""
Tests for BermudaDevice class in bermuda_device.py.
"""

from typing import Any, Generator

import pytest
from unittest.mock import MagicMock, patch
from homeassistant.components.bluetooth import BaseHaScanner, BaseHaRemoteScanner
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DEFAULT_ATTENUATION,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    ICON_DEFAULT_AREA,
    ICON_DEFAULT_FLOOR,
)
from custom_components.bermuda.util import normalize_mac


@pytest.fixture
def mock_coordinator() -> Generator[MagicMock, None, None]:
    """Fixture for mocking BermudaDataUpdateCoordinator."""
    coordinator = MagicMock()
    coordinator.options = {
        CONF_ATTENUATION: DEFAULT_ATTENUATION,
        CONF_REF_POWER: DEFAULT_REF_POWER,
        CONF_MAX_VELOCITY: DEFAULT_MAX_VELOCITY,
        CONF_SMOOTHING_SAMPLES: DEFAULT_SMOOTHING_SAMPLES,
        CONF_RSSI_OFFSETS: {},
    }
    coordinator.hass_version_min_2025_4 = True
    yield coordinator


@pytest.fixture
def mock_scanner() -> Generator[MagicMock, None, None]:
    """Fixture for mocking BaseHaScanner."""
    scanner = MagicMock(spec=BaseHaScanner)
    scanner.time_since_last_detection.return_value = 5.0
    scanner.source = "mock_source"
    yield scanner


@pytest.fixture
def mock_remote_scanner() -> Generator[MagicMock, None, None]:
    """Fixture for mocking BaseHaRemoteScanner."""
    scanner = MagicMock(spec=BaseHaRemoteScanner)
    scanner.time_since_last_detection.return_value = 5.0
    scanner.source = "mock_source"
    yield scanner


@pytest.fixture
def bermuda_device(mock_coordinator: MagicMock) -> Generator[BermudaDevice, None, None]:
    """Fixture for creating a BermudaDevice instance."""
    yield BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)


@pytest.fixture
def bermuda_scanner(mock_coordinator: MagicMock) -> Generator[BermudaDevice, None, None]:
    """Fixture for creating a BermudaDevice Scanner instance."""
    yield BermudaDevice(address="11:22:33:44:55:66", coordinator=mock_coordinator)


def test_bermuda_device_initialization(bermuda_device: BermudaDevice) -> None:
    """Test BermudaDevice initialization."""
    assert bermuda_device.address == normalize_mac("aa:bb:cc:dd:ee:ff")
    assert bermuda_device.name.startswith("bermuda_")
    assert bermuda_device.area_icon == ICON_DEFAULT_AREA
    assert bermuda_device.floor_icon == ICON_DEFAULT_FLOOR
    assert bermuda_device.zone == "not_home"


def test_async_as_scanner_init(bermuda_scanner: BermudaDevice, mock_scanner: MagicMock) -> None:
    """Test async_as_scanner_init method."""
    bermuda_scanner.async_as_scanner_init(mock_scanner)
    assert bermuda_scanner._hascanner == mock_scanner
    assert bermuda_scanner.is_scanner is True
    assert bermuda_scanner.is_remote_scanner is False


def test_async_as_scanner_update(bermuda_scanner: BermudaDevice, mock_scanner: MagicMock) -> None:
    """Test async_as_scanner_update method."""
    bermuda_scanner.async_as_scanner_update(mock_scanner)
    assert bermuda_scanner.last_seen > 0


def test_async_as_scanner_get_stamp(bermuda_scanner: BermudaDevice, mock_scanner: MagicMock, mock_remote_scanner: MagicMock) -> None:
    """Test async_as_scanner_get_stamp method."""
    bermuda_scanner.async_as_scanner_init(mock_scanner)
    bermuda_scanner.stamps = {normalize_mac("aa:bb:cc:dd:ee:ff"): 123.45}

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:bb:CC:DD:EE:FF")
    assert stamp is None

    bermuda_scanner.async_as_scanner_init(mock_remote_scanner)

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:bb:CC:DD:EE:FF")
    assert stamp == 123.45

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:BB:CC:DD:E1:FF")
    assert stamp is None


def test_make_name(bermuda_device: BermudaDevice) -> None:
    """Test make_name method."""
    bermuda_device.name_by_user = "Custom Name"
    name = bermuda_device.make_name()
    assert name == "Custom Name"
    assert bermuda_device.name == "Custom Name"


def test_process_advertisement(bermuda_device: BermudaDevice, bermuda_scanner: BermudaDevice) -> None:
    """Test process_advertisement method."""
    advertisement_data = MagicMock()
    advertisement_data.rssi = -60  # Set a realistic RSSI value
    bermuda_device.process_advertisement(bermuda_scanner, advertisement_data)
    assert len(bermuda_device.adverts) == 1


# def test_process_manufacturer_data(bermuda_device):
#     """Test process_manufacturer_data method."""
#     mock_advert = MagicMock()
#     mock_advert.service_uuids = ["0000abcd-0000-1000-8000-00805f9b34fb"]
#     mock_advert.manufacturer_data = [{"004C": b"\x02\x15"}]
#     bermuda_device.process_manufacturer_data(mock_advert)
#     assert bermuda_device.manufacturer == "Apple Inc."


def test_to_dict(bermuda_device: BermudaDevice) -> None:
    """Test to_dict method."""
    device_dict = bermuda_device.to_dict()
    assert isinstance(device_dict, dict)
    assert device_dict["address"] == normalize_mac("aa:bb:cc:dd:ee:ff")


def test_repr(bermuda_device: BermudaDevice) -> None:
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"


def test_apply_scanner_selection_accepts_nowstamp(bermuda_device: BermudaDevice) -> None:
    """Ensure apply_scanner_selection accepts and uses nowstamp."""
    advert = MagicMock()
    advert.area_id = "area-new"
    advert.stamp = 100.0
    advert.rssi_distance = 1.5
    advert.rssi = -60.0
    advert.scanner_device = MagicMock()

    bermuda_device.apply_scanner_selection(advert, nowstamp=105.0)

    assert bermuda_device.area_id == "area-new"
    assert bermuda_device.last_seen == pytest.approx(100.0)


class TestScannerAwareAwayLogic:
    """Tests for scanner-aware away logic during network outages."""

    def test_device_stays_home_when_all_scanners_offline(self, mock_coordinator: MagicMock) -> None:
        """Test that device stays 'home' when stale but all scanners are offline."""
        from custom_components.bermuda.const import CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT

        mock_coordinator.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        mock_coordinator.count_active_scanners.return_value = 0  # All scanners offline

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.zone = "home"  # Device was home before network outage
        device.last_seen = 1.0  # Very old timestamp (stale)

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Device should remain "home" because no scanners are active
        assert device.zone == "home"

    def test_device_becomes_away_when_scanners_active(self, mock_coordinator: MagicMock) -> None:
        """Test that device becomes 'not_home' when stale and scanners are active."""
        from custom_components.bermuda.const import CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT

        mock_coordinator.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        mock_coordinator.count_active_scanners.return_value = 2  # Scanners are active

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.zone = "home"  # Device was home
        device.last_seen = 1.0  # Very old timestamp (stale)

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Device should be "not_home" because scanners are active but device is stale
        assert device.zone == "not_home"

    def test_device_stays_home_when_recently_seen(self, mock_coordinator: MagicMock) -> None:
        """Test that device stays 'home' when recently seen regardless of scanner count."""
        from custom_components.bermuda.const import CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT

        mock_coordinator.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        mock_coordinator.count_active_scanners.return_value = 2  # Scanners are active

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.zone = "not_home"  # Start as not_home
        device.last_seen = 995.0  # Recently seen (within timeout)

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Device should be "home" because it was recently seen
        assert device.zone == "home"

    def test_device_away_when_never_seen(self, mock_coordinator: MagicMock) -> None:
        """Test that device is 'not_home' when never seen (last_seen is 0)."""
        from custom_components.bermuda.const import CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT

        mock_coordinator.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        mock_coordinator.count_active_scanners.return_value = 0  # Even with no scanners

        device = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device.zone = "home"  # Start as home
        device.last_seen = 0  # Never seen (falsy value)

        with patch("custom_components.bermuda.bermuda_device.monotonic_time_coarse", return_value=1000.0):
            device.calculate_data()

        # Device should be "not_home" because it was never actually seen
        assert device.zone == "not_home"
