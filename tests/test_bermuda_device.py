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


def test_async_as_scanner_get_stamp(
    bermuda_scanner: BermudaDevice, mock_scanner: MagicMock, mock_remote_scanner: MagicMock
) -> None:
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


class TestBermudaDeviceHash:
    """Tests for __hash__ method."""

    def test_hash_returns_consistent_value(self, bermuda_device: BermudaDevice) -> None:
        """Test that hash returns consistent value for same device."""
        h1 = hash(bermuda_device)
        h2 = hash(bermuda_device)
        assert h1 == h2

    def test_hash_differs_for_different_addresses(self, mock_coordinator: MagicMock) -> None:
        """Test that different addresses produce different hashes."""
        device1 = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        device2 = BermudaDevice(address="11:22:33:44:55:66", coordinator=mock_coordinator)
        assert hash(device1) != hash(device2)


class TestAddressTypeProcessing:
    """Tests for _async_process_address_type."""

    def test_ibeacon_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test iBeacon address detection (32 hex + major + minor)."""
        from custom_components.bermuda.const import ADDR_TYPE_IBEACON, METADEVICE_IBEACON_DEVICE

        # iBeacon format: uuid_major_minor (32 hex chars + _ + hex + _ + hex)
        ibeacon_addr = "aabbccddeeff00112233445566778899_1234_5678"
        device = BermudaDevice(address=ibeacon_addr, coordinator=mock_coordinator)
        assert device.address_type == ADDR_TYPE_IBEACON
        assert METADEVICE_IBEACON_DEVICE in device.metadevice_type

    def test_irk_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test IRK address detection (32 hex chars)."""
        from custom_components.bermuda.const import ADDR_TYPE_PRIVATE_BLE_DEVICE, METADEVICE_PRIVATE_BLE_DEVICE

        # IRK format: 32 hex chars
        irk_addr = "aabbccddeeff00112233445566778899"
        device = BermudaDevice(address=irk_addr, coordinator=mock_coordinator)
        assert device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE
        assert METADEVICE_PRIVATE_BLE_DEVICE in device.metadevice_type

    def test_random_unresolvable_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test random unresolvable address (first char 0-3)."""
        from custom_components.bermuda.const import BDADDR_TYPE_RANDOM_UNRESOLVABLE

        # First char 0-3 means random unresolvable (top bits 00)
        device = BermudaDevice(address="0A:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        assert device.address_type == BDADDR_TYPE_RANDOM_UNRESOLVABLE

    def test_random_resolvable_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test random resolvable address (first char 4-7)."""
        from custom_components.bermuda.const import BDADDR_TYPE_RANDOM_RESOLVABLE

        # First char 4-7 means random resolvable (top bits 01)
        device = BermudaDevice(address="4A:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        assert device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE

    def test_reserved_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test reserved address type (first char 8-B)."""
        from custom_components.bermuda.const import BDADDR_TYPE_RESERVED

        # First char 8-B means reserved (top bits 10)
        device = BermudaDevice(address="8A:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        assert device.address_type == BDADDR_TYPE_RESERVED

    def test_random_static_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test random static address (first char C-F)."""
        from custom_components.bermuda.const import BDADDR_TYPE_RANDOM_STATIC

        # First char C-F means random static (top bits 11)
        device = BermudaDevice(address="CA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)
        assert device.address_type == BDADDR_TYPE_RANDOM_STATIC

    def test_not_mac48_address_type(self, mock_coordinator: MagicMock) -> None:
        """Test non-MAC48 address detection."""
        from custom_components.bermuda.const import BDADDR_TYPE_NOT_MAC48

        # Some random non-MAC address
        device = BermudaDevice(address="fmdn:some-device-id", coordinator=mock_coordinator)
        assert device.address_type == BDADDR_TYPE_NOT_MAC48


class TestUpdateAreaAndFloor:
    """Tests for update_area_and_floor method."""

    def test_update_area_and_floor_with_none(self, bermuda_device: BermudaDevice) -> None:
        """Test update_area_and_floor with None clears all area/floor data."""
        # Set some initial values
        bermuda_device.area_id = "test_area"
        bermuda_device.floor_id = "test_floor"

        bermuda_device.update_area_and_floor(None)

        assert bermuda_device.area is None
        assert bermuda_device.area_id is None
        assert bermuda_device.area_name is None
        assert bermuda_device.floor is None
        assert bermuda_device.floor_id is None
        assert bermuda_device.floor_name is None

    def test_update_area_and_floor_with_valid_area(self, bermuda_device: BermudaDevice) -> None:
        """Test update_area_and_floor with valid area."""
        mock_area = MagicMock()
        mock_area.name = "Living Room"
        mock_area.icon = "mdi:sofa"
        mock_area.floor_id = "floor_1"

        mock_floor = MagicMock()
        mock_floor.name = "Ground Floor"
        mock_floor.icon = "mdi:floor-plan"
        mock_floor.level = 0

        bermuda_device.ar.async_get_area = MagicMock(return_value=mock_area)
        bermuda_device.fr.async_get_floor = MagicMock(return_value=mock_floor)

        bermuda_device.update_area_and_floor("test_area_id")

        assert bermuda_device.area_id == "test_area_id"
        assert bermuda_device.area_name == "Living Room"
        assert bermuda_device.floor_id == "floor_1"
        assert bermuda_device.floor_name == "Ground Floor"

    def test_update_area_and_floor_with_invalid_floor(self, bermuda_device: BermudaDevice) -> None:
        """Test update_area_and_floor with valid area but invalid floor."""
        mock_area = MagicMock()
        mock_area.name = "Living Room"
        mock_area.icon = None
        mock_area.floor_id = "invalid_floor"

        bermuda_device.ar.async_get_area = MagicMock(return_value=mock_area)
        bermuda_device.fr.async_get_floor = MagicMock(return_value=None)

        bermuda_device.update_area_and_floor("test_area_id")

        assert bermuda_device.area_id == "test_area_id"
        assert bermuda_device.floor_id is None
        assert bermuda_device.floor_name == "Invalid Floor ID"

    def test_update_area_and_floor_with_invalid_area(self, bermuda_device: BermudaDevice) -> None:
        """Test update_area_and_floor with invalid area ID."""
        bermuda_device.ar.async_get_area = MagicMock(return_value=None)

        bermuda_device.update_area_and_floor("invalid_area_id")

        assert bermuda_device.area is None
        assert bermuda_device.area_id == "invalid_area_id"
        assert "Invalid Area" in bermuda_device.area_name


class TestMovementState:
    """Tests for get_movement_state and get_dwell_time."""

    def test_get_movement_state_stationary_when_never_moved(self, bermuda_device: BermudaDevice) -> None:
        """Test device is stationary when area_changed_at is 0."""
        from custom_components.bermuda.const import MOVEMENT_STATE_STATIONARY

        bermuda_device.area_changed_at = 0.0
        result = bermuda_device.get_movement_state(stamp_now=1000.0)
        assert result == MOVEMENT_STATE_STATIONARY

    def test_get_movement_state_moving(self, bermuda_device: BermudaDevice) -> None:
        """Test device is moving when just changed area."""
        from custom_components.bermuda.const import MOVEMENT_STATE_MOVING, DWELL_TIME_MOVING_SECONDS

        bermuda_device.area_changed_at = 1000.0
        # Test with a small dwell time
        result = bermuda_device.get_movement_state(stamp_now=1000.0 + DWELL_TIME_MOVING_SECONDS - 1)
        assert result == MOVEMENT_STATE_MOVING

    def test_get_movement_state_settling(self, bermuda_device: BermudaDevice) -> None:
        """Test device is settling after moving period."""
        from custom_components.bermuda.const import (
            MOVEMENT_STATE_SETTLING,
            DWELL_TIME_MOVING_SECONDS,
            DWELL_TIME_SETTLING_SECONDS,
        )

        bermuda_device.area_changed_at = 1000.0
        # Test in settling window
        result = bermuda_device.get_movement_state(stamp_now=1000.0 + DWELL_TIME_MOVING_SECONDS + 10)
        assert result == MOVEMENT_STATE_SETTLING

    def test_get_movement_state_stationary_after_settling(self, bermuda_device: BermudaDevice) -> None:
        """Test device is stationary after settling period."""
        from custom_components.bermuda.const import MOVEMENT_STATE_STATIONARY, DWELL_TIME_SETTLING_SECONDS

        bermuda_device.area_changed_at = 1000.0
        result = bermuda_device.get_movement_state(stamp_now=1000.0 + DWELL_TIME_SETTLING_SECONDS + 100)
        assert result == MOVEMENT_STATE_STATIONARY

    def test_get_dwell_time_zero_when_never_moved(self, bermuda_device: BermudaDevice) -> None:
        """Test dwell time is 0 when area_changed_at is 0."""
        bermuda_device.area_changed_at = 0.0
        result = bermuda_device.get_dwell_time(stamp_now=1000.0)
        assert result == 0.0

    def test_get_dwell_time_calculates_correctly(self, bermuda_device: BermudaDevice) -> None:
        """Test dwell time calculation."""
        bermuda_device.area_changed_at = 1000.0
        result = bermuda_device.get_dwell_time(stamp_now=1500.0)
        assert result == 500.0


class TestResetVelocityHistory:
    """Tests for reset_velocity_history method."""

    def test_reset_velocity_history_clears_advert_history(self, bermuda_device: BermudaDevice) -> None:
        """Test reset_velocity_history clears all advert history."""
        # Create a mock advert with MagicMock lists that have clear() method
        mock_advert = MagicMock()
        mock_advert.hist_velocity = MagicMock()
        mock_advert.hist_distance = MagicMock()
        mock_advert.hist_distance_by_interval = MagicMock()
        mock_advert.hist_stamp = MagicMock()
        mock_advert.rssi_kalman = MagicMock()
        mock_advert.rssi_filtered = -70.0
        mock_advert.velocity_blocked_count = 5

        bermuda_device.adverts = {("scanner", "device"): mock_advert}

        bermuda_device.reset_velocity_history()

        mock_advert.hist_velocity.clear.assert_called_once()
        mock_advert.hist_distance.clear.assert_called_once()
        mock_advert.hist_distance_by_interval.clear.assert_called_once()
        mock_advert.hist_stamp.clear.assert_called_once()
        mock_advert.rssi_kalman.reset.assert_called_once()
        assert mock_advert.rssi_filtered is None
        assert mock_advert.velocity_blocked_count == 0


class TestResetPendingState:
    """Tests for reset_pending_state method."""

    def test_reset_pending_state_clears_all_pending(self, bermuda_device: BermudaDevice) -> None:
        """Test reset_pending_state clears all pending state."""
        bermuda_device.pending_area_id = "test_area"
        bermuda_device.pending_floor_id = "test_floor"
        bermuda_device.pending_streak = 5
        bermuda_device.pending_last_stamps = {"scanner": 100.0}

        bermuda_device.reset_pending_state()

        assert bermuda_device.pending_area_id is None
        assert bermuda_device.pending_floor_id is None
        assert bermuda_device.pending_streak == 0
        assert bermuda_device.pending_last_stamps == {}


class TestCoVisibility:
    """Tests for co-visibility methods."""

    def test_update_co_visibility_creates_new_area(self, bermuda_device: BermudaDevice) -> None:
        """Test update_co_visibility creates stats for new area."""
        visible = {"scanner1", "scanner2"}
        candidates = {"scanner1", "scanner2", "scanner3"}

        bermuda_device.update_co_visibility("area1", visible, candidates)

        assert "area1" in bermuda_device.co_visibility_stats
        assert "scanner1" in bermuda_device.co_visibility_stats["area1"]
        assert bermuda_device.co_visibility_stats["area1"]["scanner1"]["seen"] == 1
        assert bermuda_device.co_visibility_stats["area1"]["scanner1"]["total"] == 1

    def test_update_co_visibility_updates_existing(self, bermuda_device: BermudaDevice) -> None:
        """Test update_co_visibility updates existing stats."""
        bermuda_device.co_visibility_stats["area1"] = {"scanner1": {"seen": 5, "total": 10}}

        visible = {"scanner1"}
        candidates = {"scanner1"}

        bermuda_device.update_co_visibility("area1", visible, candidates)

        assert bermuda_device.co_visibility_stats["area1"]["scanner1"]["seen"] == 6
        assert bermuda_device.co_visibility_stats["area1"]["scanner1"]["total"] == 11

    def test_get_co_visibility_confidence_no_data(self, bermuda_device: BermudaDevice) -> None:
        """Test get_co_visibility_confidence returns 1.0 with no data."""
        result = bermuda_device.get_co_visibility_confidence("area1", {"scanner1"})
        assert result == 1.0

    def test_get_co_visibility_confidence_not_enough_samples(self, bermuda_device: BermudaDevice) -> None:
        """Test get_co_visibility_confidence returns 1.0 with insufficient samples."""
        bermuda_device.co_visibility_stats["area1"] = {"scanner1": {"seen": 5, "total": 10}}
        bermuda_device.co_visibility_min_samples = 50

        result = bermuda_device.get_co_visibility_confidence("area1", {"scanner1"})
        assert result == 1.0

    def test_get_co_visibility_confidence_with_sufficient_samples(self, bermuda_device: BermudaDevice) -> None:
        """Test get_co_visibility_confidence with sufficient data."""
        bermuda_device.co_visibility_stats["area1"] = {
            "scanner1": {"seen": 40, "total": 60},  # 67% visibility
            "scanner2": {"seen": 30, "total": 60},  # 50% visibility
        }
        bermuda_device.co_visibility_min_samples = 50

        # Both scanners visible
        result = bermuda_device.get_co_visibility_confidence("area1", {"scanner1", "scanner2"})
        assert result == 1.0

        # Only scanner1 visible
        result = bermuda_device.get_co_visibility_confidence("area1", {"scanner1"})
        assert 0 < result < 1.0


class TestParseTrackerTimeout:
    """Tests for _parse_tracker_timeout method."""

    def test_parse_tracker_timeout_with_int(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing integer timeout."""
        assert bermuda_device._parse_tracker_timeout(300) == 300.0

    def test_parse_tracker_timeout_with_float(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing float timeout."""
        assert bermuda_device._parse_tracker_timeout(300.5) == 300.5

    def test_parse_tracker_timeout_with_string(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing string timeout."""
        assert bermuda_device._parse_tracker_timeout("300") == 300.0

    def test_parse_tracker_timeout_with_invalid_string(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing invalid string returns default."""
        from custom_components.bermuda.const import DEFAULT_DEVTRACK_TIMEOUT

        assert bermuda_device._parse_tracker_timeout("invalid") == float(DEFAULT_DEVTRACK_TIMEOUT)

    def test_parse_tracker_timeout_with_negative(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing negative value returns default."""
        from custom_components.bermuda.const import DEFAULT_DEVTRACK_TIMEOUT

        assert bermuda_device._parse_tracker_timeout(-100) == float(DEFAULT_DEVTRACK_TIMEOUT)

    def test_parse_tracker_timeout_with_zero(self, bermuda_device: BermudaDevice) -> None:
        """Test parsing zero returns default."""
        from custom_components.bermuda.const import DEFAULT_DEVTRACK_TIMEOUT

        assert bermuda_device._parse_tracker_timeout(0) == float(DEFAULT_DEVTRACK_TIMEOUT)


class TestAreaStateMetadata:
    """Tests for area state metadata methods."""

    def test_area_state_age_with_none_stamp(self, bermuda_device: BermudaDevice) -> None:
        """Test _area_state_age returns None when stamp is None."""
        bermuda_device.area_state_stamp = None
        result = bermuda_device._area_state_age(1000.0)
        assert result is None

    def test_area_state_age_calculates_correctly(self, bermuda_device: BermudaDevice) -> None:
        """Test _area_state_age calculates age correctly."""
        bermuda_device.area_state_stamp = 900.0
        result = bermuda_device._area_state_age(1000.0)
        assert result == 100.0

    def test_area_is_retained_false_when_no_stamp(self, bermuda_device: BermudaDevice) -> None:
        """Test area_is_retained returns False when no stamp."""
        bermuda_device.area_state_stamp = None
        result = bermuda_device.area_is_retained(stamp_now=1000.0)
        assert result is False

    def test_area_state_metadata_returns_dict(self, bermuda_device: BermudaDevice) -> None:
        """Test area_state_metadata returns expected dict structure."""
        bermuda_device.area_state_stamp = 990.0
        bermuda_device.area_distance_stamp = 995.0
        bermuda_device.area_state_source = "test"

        result = bermuda_device.area_state_metadata(stamp_now=1000.0)

        assert "last_good_area_age_s" in result
        assert "last_good_distance_age_s" in result
        assert "area_is_stale" in result
        assert "area_retained" in result
        assert result["area_source"] == "test"


class TestSetRefPower:
    """Tests for set_ref_power method."""

    def test_set_ref_power_unchanged_does_nothing(self, bermuda_device: BermudaDevice) -> None:
        """Test set_ref_power does nothing when value unchanged."""
        bermuda_device.ref_power = -55.0
        initial_stamp = bermuda_device.ref_power_changed

        bermuda_device.set_ref_power(-55.0)

        assert bermuda_device.ref_power_changed == initial_stamp

    def test_set_ref_power_updates_adverts(self, bermuda_device: BermudaDevice) -> None:
        """Test set_ref_power updates all adverts."""
        mock_advert = MagicMock()
        mock_advert.set_ref_power = MagicMock(return_value=5.0)
        bermuda_device.adverts = {("scanner", "device"): mock_advert}
        bermuda_device.ref_power = -60.0

        with patch.object(bermuda_device, "apply_scanner_selection"):
            bermuda_device.set_ref_power(-55.0)

        assert bermuda_device.ref_power == -55.0
        mock_advert.set_ref_power.assert_called_once_with(-55.0)


class TestMakeName:
    """Tests for make_name method."""

    def test_make_name_prefers_user_name(self, bermuda_device: BermudaDevice) -> None:
        """Test make_name prefers user-defined name."""
        bermuda_device.name_by_user = "User Name"
        bermuda_device.name_devreg = "Registry Name"

        result = bermuda_device.make_name()

        assert result == "User Name"

    def test_make_name_uses_devreg_name(self, bermuda_device: BermudaDevice) -> None:
        """Test make_name uses device registry name when no user name."""
        bermuda_device.name_by_user = None
        bermuda_device.name_devreg = "Registry Name"

        result = bermuda_device.make_name()

        assert result == "Registry Name"

    def test_make_name_uses_local_name(self, bermuda_device: BermudaDevice) -> None:
        """Test make_name uses local name from BT advertisement."""
        bermuda_device.name_by_user = None
        bermuda_device.name_devreg = None
        bermuda_device.name_bt_local_name = "Local Name"

        result = bermuda_device.make_name()

        assert result == "Local Name"

    def test_make_name_uses_manufacturer_prefix(self, bermuda_device: BermudaDevice) -> None:
        """Test make_name uses manufacturer prefix when no other name."""
        from custom_components.bermuda.const import BDADDR_TYPE_NOT_MAC48

        bermuda_device.name_by_user = None
        bermuda_device.name_devreg = None
        bermuda_device.name_bt_local_name = None
        bermuda_device.name_bt_serviceinfo = None
        bermuda_device.beacon_unique_id = None
        bermuda_device.manufacturer = "Apple Inc."
        # Must NOT be BDADDR_TYPE_NOT_MAC48 for the manufacturer prefix logic to trigger
        bermuda_device.address_type = "bd_addr_other"

        result = bermuda_device.make_name()

        assert "apple_inc" in result.lower()


class TestUkfScannerlessArea:
    """Tests for ukf_scannerless_area property."""

    def test_ukf_scannerless_area_getter(self, bermuda_device: BermudaDevice) -> None:
        """Test ukf_scannerless_area getter."""
        bermuda_device._ukf_scannerless_area = True
        assert bermuda_device.ukf_scannerless_area is True

    def test_ukf_scannerless_area_setter(self, bermuda_device: BermudaDevice) -> None:
        """Test ukf_scannerless_area setter."""
        bermuda_device.ukf_scannerless_area = True
        assert bermuda_device._ukf_scannerless_area is True


class TestAsyncAsScannerNolonger:
    """Tests for async_as_scanner_nolonger method."""

    def test_async_as_scanner_nolonger(self, bermuda_scanner: BermudaDevice, mock_scanner: MagicMock) -> None:
        """Test async_as_scanner_nolonger resets scanner state."""
        bermuda_scanner.async_as_scanner_init(mock_scanner)
        assert bermuda_scanner.is_scanner is True

        bermuda_scanner.async_as_scanner_nolonger()

        assert bermuda_scanner._is_scanner is False
        assert bermuda_scanner._is_remote_scanner is False
