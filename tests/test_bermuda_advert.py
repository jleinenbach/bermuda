"""
Tests for BermudaAdvert class in bermuda_advert.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from bleak.backends.scanner import AdvertisementData

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


@pytest.fixture
def mock_parent_device() -> MagicMock:
    """Fixture for mocking the parent BermudaDevice."""
    device = MagicMock(spec=BermudaDevice)
    device.address = normalize_mac("aa:bb:cc:dd:ee:ff")
    device.ref_power = -59
    device.name_bt_local_name = None
    device.name = "mock parent name"
    return device


@pytest.fixture
def mock_scanner_device() -> MagicMock:
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
def mock_advertisement_data() -> MagicMock:
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
def bermuda_advert(
    mock_parent_device: MagicMock,
    mock_advertisement_data: MagicMock,
    mock_scanner_device: MagicMock,
) -> BermudaAdvert:
    """Fixture for creating a BermudaAdvert instance."""
    options: dict[str, Any] = {
        CONF_RSSI_OFFSETS: {normalize_mac("11:22:33:44:55:66"): 5},
        CONF_REF_POWER: -59,
        CONF_ATTENUATION: 2.0,
        CONF_MAX_VELOCITY: 3.0,
        CONF_SMOOTHING_SAMPLES: 5,
    }
    # Mock monotonic_time_coarse to return a time that makes the scanner's
    # stamp (123.45) valid (not "in the future")
    with patch(
        "custom_components.bermuda.bermuda_advert.monotonic_time_coarse",
        return_value=125.0,
    ):
        ba = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )
    ba.name = "foo name"
    return ba


def test_bermuda_advert_initialization(bermuda_advert: BermudaAdvert) -> None:
    """Test BermudaAdvert initialization."""
    assert bermuda_advert.device_address == normalize_mac("aa:bb:cc:dd:ee:ff")
    assert bermuda_advert.scanner_address == normalize_mac("11:22:33:44:55:66")
    assert bermuda_advert.ref_power == -59
    assert bermuda_advert.stamp == 123.45
    assert bermuda_advert.rssi == -70


def test_apply_new_scanner(bermuda_advert: BermudaAdvert, mock_scanner_device: MagicMock) -> None:
    """Test apply_new_scanner method."""
    bermuda_advert.apply_new_scanner(mock_scanner_device)
    assert bermuda_advert.scanner_device == mock_scanner_device
    assert bermuda_advert.scanner_sends_stamps is True


def test_update_advertisement(
    bermuda_advert: BermudaAdvert, mock_advertisement_data: MagicMock, mock_scanner_device: MagicMock
) -> None:
    """Test update_advertisement method."""
    bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)
    assert bermuda_advert.rssi == -70
    assert bermuda_advert.tx_power == -20
    assert bermuda_advert.local_name[0][0] == "Mock advert Local Name"
    assert bermuda_advert.manufacturer_data[0][76] == b"\x02\x15"
    assert bermuda_advert.service_data[0]["0000abcd-0000-1000-8000-00805f9b34fb"] == b"\x01\x02"


def test_set_ref_power(bermuda_advert: BermudaAdvert) -> None:
    """Test set_ref_power method."""
    new_distance = bermuda_advert.set_ref_power(-65)
    assert bermuda_advert.ref_power == -65
    assert new_distance is not None


def test_set_ref_power_clears_all_history_lists(bermuda_advert: BermudaAdvert) -> None:
    """Test that set_ref_power clears all paired history lists to maintain sync.

    This is a regression test for a bug where set_ref_power only cleared
    hist_distance but not hist_stamp, causing an IndexError when calculate_data()
    tried to access hist_distance[1] after checking len(hist_stamp) > 1.
    """
    # Set up history in all lists that should stay in sync
    bermuda_advert.hist_stamp = [100.0, 99.0, 98.0]
    bermuda_advert.hist_rssi = [-70, -71, -72]
    bermuda_advert.hist_distance = [5.0, 5.1, 5.2]
    bermuda_advert.hist_distance_by_interval = [5.0, 5.1]
    bermuda_advert.hist_interval = [1.0, 1.0]
    bermuda_advert.hist_velocity = [0.1, 0.1]

    # Change ref_power
    bermuda_advert.set_ref_power(-65)

    # All paired history lists should be cleared
    assert len(bermuda_advert.hist_stamp) == 0, "hist_stamp should be cleared"
    assert len(bermuda_advert.hist_rssi) == 0, "hist_rssi should be cleared"
    assert len(bermuda_advert.hist_distance) == 0, "hist_distance should be cleared"
    assert len(bermuda_advert.hist_distance_by_interval) == 0, "hist_distance_by_interval should be cleared"
    assert len(bermuda_advert.hist_interval) == 0, "hist_interval should be cleared"
    assert len(bermuda_advert.hist_velocity) == 0, "hist_velocity should be cleared"


def test_calculate_data_handles_mismatched_history_lists(bermuda_advert: BermudaAdvert) -> None:
    """Test that calculate_data handles mismatched hist_stamp and hist_distance gracefully.

    This is a defensive test to ensure that even if hist_stamp and hist_distance
    somehow get out of sync, calculate_data() doesn't crash with IndexError.
    """
    # Set up mismatched lists: hist_stamp has 2+ entries but hist_distance has only 1
    bermuda_advert.hist_stamp = [100.0, 99.0, 98.0]
    bermuda_advert.hist_distance = [5.0]  # Only one entry!
    bermuda_advert.new_stamp = 101.0
    bermuda_advert.stamp = 100.0
    bermuda_advert.rssi_distance_raw = 4.5

    # This should NOT raise IndexError
    bermuda_advert.calculate_data()

    # Verify it completed without crashing
    assert bermuda_advert.rssi_distance is not None


def test_calculate_data_device_arrived(bermuda_advert: BermudaAdvert) -> None:
    """Test calculate_data method when device arrives."""
    bermuda_advert.new_stamp = 123.45
    bermuda_advert.rssi_distance_raw = 5.0
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance == 5.0


def test_calculate_data_device_away(bermuda_advert: BermudaAdvert) -> None:
    """Test calculate_data method when device is away."""
    bermuda_advert.stamp = 0.0
    bermuda_advert.new_stamp = None
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance is None


def test_to_dict(bermuda_advert: BermudaAdvert) -> None:
    """Test to_dict method."""
    advert_dict = bermuda_advert.to_dict()
    assert isinstance(advert_dict, dict)
    assert advert_dict["device_address"] == normalize_mac("aa:bb:cc:dd:ee:ff")
    assert advert_dict["scanner_address"] == normalize_mac("11:22:33:44:55:66")


def test_repr(bermuda_advert: BermudaAdvert) -> None:
    """Test __repr__ method."""
    repr_str = repr(bermuda_advert)
    assert repr_str == f"{normalize_mac('aa:bb:cc:dd:ee:ff')}__Mock Scanner"


def test_adaptive_stale_timeout_with_frequent_updates(bermuda_advert: BermudaAdvert) -> None:
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


def test_adaptive_stale_timeout_with_slow_updates(bermuda_advert: BermudaAdvert) -> None:
    """Test that adaptive timeout increases for slow-updating devices (e.g., FMDN tags)."""
    # Simulate a device that updates every 45 seconds (max interval)
    # The code uses 2x maximum interval, clamped between 60s and 360s
    base_time = 2000.0
    bermuda_advert.hist_stamp = [
        base_time,
        base_time - 45.0,
        base_time - 90.0,
        base_time - 135.0,
        base_time - 180.0,
    ]
    bermuda_advert.stamp = base_time
    bermuda_advert.new_stamp = None
    bermuda_advert.rssi_distance = 5.0

    # Device has 45s max interval, so adaptive timeout = max(60, 45*2) = 90s
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


def test_adaptive_stale_timeout_capped_at_180s(bermuda_advert: BermudaAdvert) -> None:
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


def test_adaptive_stale_timeout_with_insufficient_history(bermuda_advert: BermudaAdvert) -> None:
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


def test_median_smoothing_filters_spikes(bermuda_advert: BermudaAdvert) -> None:
    """Test that median-based smoothing filters out signal spikes effectively.

    The old 'moving minimum' algorithm would produce unrealistically small distances
    when signal spikes occurred. The new median-based algorithm should be robust
    against individual outliers.
    """
    # Set up initial state
    bermuda_advert.new_stamp = 100.0
    bermuda_advert.stamp = 100.0
    bermuda_advert.rssi_distance_raw = 4.0

    # Simulate history with mostly stable readings around 4m and one spike at 0.5m
    bermuda_advert.hist_distance_by_interval = [4.0, 3.8, 0.5, 4.2, 4.1]

    bermuda_advert.calculate_data()

    # Median of [0.5, 3.8, 4.0, 4.1, 4.2] is 4.0
    # Since raw (4.0) >= median (4.0), result should be median
    assert bermuda_advert.rssi_distance is not None
    assert 3.5 <= bermuda_advert.rssi_distance <= 4.5, (
        f"Expected median ~4.0m but got {bermuda_advert.rssi_distance}m. "
        "Spike at 0.5m should not significantly affect the result."
    )


def test_median_smoothing_responds_to_approach(bermuda_advert: BermudaAdvert) -> None:
    """Test that median smoothing still responds quickly when device approaches.

    When the raw distance is smaller than the median (device getting closer),
    the algorithm should use the raw distance for quick response.
    """
    bermuda_advert.new_stamp = 100.0
    bermuda_advert.stamp = 100.0
    bermuda_advert.rssi_distance_raw = 1.5  # Device suddenly closer

    # History shows device was previously around 5m
    bermuda_advert.hist_distance_by_interval = [5.0, 5.2, 4.8, 5.1, 4.9]

    bermuda_advert.calculate_data()

    # Raw (1.5m) < median (~5.0m), so should use raw for quick approach response
    assert bermuda_advert.rssi_distance is not None
    assert bermuda_advert.rssi_distance == 1.5, (
        f"Expected raw distance 1.5m for quick approach response, got {bermuda_advert.rssi_distance}m"
    )


def test_median_smoothing_stable_readings(bermuda_advert: BermudaAdvert) -> None:
    """Test that median smoothing produces stable output for stable input."""
    bermuda_advert.new_stamp = 100.0
    bermuda_advert.stamp = 100.0
    bermuda_advert.rssi_distance_raw = 3.0

    # Stable readings around 3m
    bermuda_advert.hist_distance_by_interval = [3.1, 2.9, 3.0, 3.2, 2.8]

    bermuda_advert.calculate_data()

    # Median of [2.8, 2.9, 3.0, 3.1, 3.2] is 3.0
    assert bermuda_advert.rssi_distance is not None
    assert 2.9 <= bermuda_advert.rssi_distance <= 3.1, (
        f"Expected stable median ~3.0m, got {bermuda_advert.rssi_distance}m"
    )


class TestConfigOptionsDynamicReading:
    """Tests for dynamic reading of config options from the options dict.

    These tests verify the fix for the bug where RSSI offsets and other
    config options were cached at BermudaAdvert initialization time,
    causing settings changes to not take effect until a full restart.
    The fix converts these attributes to properties that read dynamically
    from the options dictionary.
    """

    def test_conf_rssi_offset_reads_dynamically(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that conf_rssi_offset reads from options dynamically.

        This verifies that changing RSSI offsets in options takes effect
        immediately without requiring a restart.
        """
        scanner_address = normalize_mac("11:22:33:44:55:66")
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {scanner_address: 5},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )

        # Initial value should be 5
        assert advert.conf_rssi_offset == 5

        # Change the offset in options - should take effect immediately
        options[CONF_RSSI_OFFSETS][scanner_address] = 10
        assert advert.conf_rssi_offset == 10

        # Change to negative value
        options[CONF_RSSI_OFFSETS][scanner_address] = -15
        assert advert.conf_rssi_offset == -15

        # Remove the scanner from offsets - should default to 0
        del options[CONF_RSSI_OFFSETS][scanner_address]
        assert advert.conf_rssi_offset == 0

        # Remove RSSI_OFFSETS entirely - should default to 0
        del options[CONF_RSSI_OFFSETS]
        assert advert.conf_rssi_offset == 0

    def test_conf_ref_power_reads_dynamically(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that conf_ref_power reads from options dynamically."""
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )

        assert advert.conf_ref_power == -59

        # Change ref_power - should take effect immediately
        options[CONF_REF_POWER] = -65
        assert advert.conf_ref_power == -65

    def test_conf_attenuation_reads_dynamically(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that conf_attenuation reads from options dynamically."""
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )

        assert advert.conf_attenuation == 2.0

        # Change attenuation - should take effect immediately
        options[CONF_ATTENUATION] = 3.5
        assert advert.conf_attenuation == 3.5

    def test_conf_max_velocity_reads_dynamically(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that conf_max_velocity reads from options dynamically."""
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )

        assert advert.conf_max_velocity == 3.0

        # Change max_velocity - should take effect immediately
        options[CONF_MAX_VELOCITY] = 5.0
        assert advert.conf_max_velocity == 5.0

    def test_conf_smoothing_samples_reads_dynamically(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that conf_smoothing_samples reads from options dynamically."""
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=options,
            scanner_device=mock_scanner_device,
        )

        assert advert.conf_smoothing_samples == 5

        # Change smoothing_samples - should take effect immediately
        options[CONF_SMOOTHING_SAMPLES] = 10
        assert advert.conf_smoothing_samples == 10

    def test_rssi_offset_affects_distance_calculation(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that changing RSSI offset affects distance calculations.

        This is a regression test for the bug where saved RSSI offsets
        were not being applied to distance calculations after a reboot.
        """
        scanner_address = normalize_mac("11:22:33:44:55:66")
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {scanner_address: 0},  # Start with 0 offset
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }

        # Mock monotonic_time_coarse to return a time that makes the scanner's
        # stamp (123.45) valid (not "in the future")
        with patch(
            "custom_components.bermuda.bermuda_advert.monotonic_time_coarse",
            return_value=125.0,
        ):
            advert = BermudaAdvert(
                parent_device=mock_parent_device,
                advertisementdata=mock_advertisement_data,
                options=options,
                scanner_device=mock_scanner_device,
            )

        # Get initial distance with 0 offset
        initial_distance = advert.rssi_distance_raw

        # Now change RSSI offset to +10 (stronger signal = closer)
        options[CONF_RSSI_OFFSETS][scanner_address] = 10

        # Force recalculation
        advert._update_raw_distance(reading_is_new=False)
        distance_with_positive_offset = advert.rssi_distance_raw

        # With positive offset, adjusted RSSI is stronger, so distance should be smaller
        assert distance_with_positive_offset < initial_distance, (
            f"Distance with +10 offset ({distance_with_positive_offset}m) "
            f"should be less than with 0 offset ({initial_distance}m)"
        )

        # Change to negative offset (weaker signal = farther)
        options[CONF_RSSI_OFFSETS][scanner_address] = -10
        advert._update_raw_distance(reading_is_new=False)
        distance_with_negative_offset = advert.rssi_distance_raw

        # With negative offset, adjusted RSSI is weaker, so distance should be larger
        assert distance_with_negative_offset > initial_distance, (
            f"Distance with -10 offset ({distance_with_negative_offset}m) "
            f"should be greater than with 0 offset ({initial_distance}m)"
        )

    def test_options_shared_reference_behavior(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test that advert uses shared options dict reference.

        This simulates how the coordinator's reload_options() method works:
        it updates the shared options dict, and all adverts should see
        the changes immediately via their reference to that dict.
        """
        scanner_address = normalize_mac("11:22:33:44:55:66")
        # This simulates coordinator.options - a shared dict
        shared_options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {scanner_address: 5},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }

        advert = BermudaAdvert(
            parent_device=mock_parent_device,
            advertisementdata=mock_advertisement_data,
            options=shared_options,
            scanner_device=mock_scanner_device,
        )

        # Verify initial value
        assert advert.conf_rssi_offset == 5

        # Simulate what reload_options() does: update the shared dict
        shared_options[CONF_RSSI_OFFSETS] = {scanner_address: 20}

        # The advert should see the new value immediately
        assert advert.conf_rssi_offset == 20, (
            "Advert should see updated RSSI offset after shared options dict is modified"
        )


class TestBermudaAdvertEdgeCases:
    """Tests for edge cases in BermudaAdvert."""

    def test_hash_method(self, bermuda_advert: BermudaAdvert) -> None:
        """Test __hash__ method returns consistent hash for device/scanner pair."""
        h1 = hash(bermuda_advert)
        h2 = hash(bermuda_advert)
        assert h1 == h2

    def test_median_rssi_with_no_history(self, bermuda_advert: BermudaAdvert) -> None:
        """Test median_rssi falls back to current rssi when no history."""
        bermuda_advert.hist_rssi_by_interval = []
        bermuda_advert.rssi = -75
        result = bermuda_advert.median_rssi()
        assert result == -75

    def test_median_rssi_with_history(self, bermuda_advert: BermudaAdvert) -> None:
        """Test median_rssi calculates median correctly."""
        bermuda_advert.hist_rssi_by_interval = [-70, -75, -80, -65, -72]
        result = bermuda_advert.median_rssi()
        # Sorted: [-80, -75, -72, -70, -65] -> median is -72
        assert result == -72

    def test_median_rssi_even_count(self, bermuda_advert: BermudaAdvert) -> None:
        """Test median_rssi with even number of samples."""
        bermuda_advert.hist_rssi_by_interval = [-70, -75, -80, -65]
        result = bermuda_advert.median_rssi()
        # Sorted: [-80, -75, -70, -65] -> median is (-75 + -70) / 2 = -72.5
        assert result == -72.5

    def test_get_effective_ref_power_device_calibrated(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_ref_power uses device-calibrated value."""
        bermuda_advert.ref_power = -55
        ref_power, source = bermuda_advert._get_effective_ref_power()
        assert ref_power == -55
        assert source == "device-calibrated"

    def test_get_effective_ref_power_beacon_power(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_ref_power uses iBeacon beacon_power."""
        bermuda_advert.ref_power = 0  # Not calibrated
        bermuda_advert._device.beacon_power = -60
        ref_power, source = bermuda_advert._get_effective_ref_power()
        assert ref_power == -60
        assert source == "iBeacon beacon_power"

    def test_get_effective_ref_power_global_config(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_ref_power uses global config default."""
        bermuda_advert.ref_power = 0  # Not calibrated
        bermuda_advert._device.beacon_power = None  # No beacon power
        ref_power, source = bermuda_advert._get_effective_ref_power()
        assert ref_power == -59  # from options CONF_REF_POWER
        assert source == "global config default"

    def test_get_effective_ref_power_fallback_default(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_ref_power uses DEFAULT_REF_POWER when no config."""
        bermuda_advert.ref_power = 0
        bermuda_advert._device.beacon_power = None
        bermuda_advert.options[CONF_REF_POWER] = None  # No global config
        ref_power, source = bermuda_advert._get_effective_ref_power()
        assert source == "global config default"

    def test_update_raw_distance_none_rssi(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _update_raw_distance with None rssi returns DISTANCE_INFINITE."""
        from custom_components.bermuda.const import DISTANCE_INFINITE

        bermuda_advert.rssi = None
        result = bermuda_advert._update_raw_distance(reading_is_new=True)
        assert result == DISTANCE_INFINITE
        assert bermuda_advert.rssi_distance_raw == DISTANCE_INFINITE

    def test_update_raw_distance_not_new_reading(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _update_raw_distance with reading_is_new=False."""
        bermuda_advert.rssi = -70
        bermuda_advert.rssi_distance = 5.0
        bermuda_advert.hist_distance = [5.0]
        bermuda_advert.hist_distance_by_interval = [5.0]
        # Force Kalman filter to be initialized
        bermuda_advert.rssi_kalman.update(-70)

        result = bermuda_advert._update_raw_distance(reading_is_new=False)
        assert result is not None

    def test_set_ref_power_same_value_no_reset(self, bermuda_advert: BermudaAdvert) -> None:
        """Test set_ref_power with same value doesn't reset history."""
        current_ref_power = bermuda_advert.ref_power
        bermuda_advert.hist_distance = [5.0, 4.5, 5.2]

        result = bermuda_advert.set_ref_power(current_ref_power)
        # Should return current distance without resetting history
        assert result == bermuda_advert.rssi_distance_raw
        assert len(bermuda_advert.hist_distance) == 3  # Not cleared

    def test_calculate_data_velocity_acceptable(self, bermuda_advert: BermudaAdvert) -> None:
        """Test calculate_data with acceptable velocity."""
        bermuda_advert.hist_stamp = [100.0, 99.0, 98.0]
        bermuda_advert.hist_distance = [5.0, 5.1, 5.2]
        bermuda_advert.new_stamp = 101.0
        bermuda_advert.stamp = 100.0
        bermuda_advert.rssi_distance_raw = 4.9  # Small change = low velocity
        bermuda_advert.rssi_distance = 5.0

        bermuda_advert.calculate_data()

        # Velocity should be acceptable, measurement accepted
        assert bermuda_advert.velocity_blocked_count == 0

    def test_clear_stale_history(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _clear_stale_history clears all distance-related history."""
        bermuda_advert.rssi_distance = 5.0
        bermuda_advert.rssi_filtered = -70.0
        bermuda_advert.hist_distance_by_interval = [5.0, 4.9, 5.1]
        bermuda_advert.hist_rssi_by_interval = [-70, -71, -69]
        bermuda_advert.hist_distance = [5.0, 4.9, 5.1]
        bermuda_advert.hist_stamp = [100.0, 99.0, 98.0]
        bermuda_advert.hist_velocity = [0.1, 0.2]

        bermuda_advert._clear_stale_history()

        assert bermuda_advert.rssi_distance is None
        assert bermuda_advert.rssi_filtered is None
        assert len(bermuda_advert.hist_distance_by_interval) == 0
        assert len(bermuda_advert.hist_rssi_by_interval) == 0
        assert len(bermuda_advert.hist_distance) == 0
        assert len(bermuda_advert.hist_stamp) == 0
        assert len(bermuda_advert.hist_velocity) == 0


class TestUpdateAdvertisementEdgeCases:
    """Tests for update_advertisement edge cases."""

    def test_update_advertisement_different_scanner(
        self,
        bermuda_advert: BermudaAdvert,
        mock_advertisement_data: MagicMock,
    ) -> None:
        """Test update_advertisement replaces scanner when different."""
        new_scanner = MagicMock()
        new_scanner.address = normalize_mac("22:33:44:55:66:77")
        new_scanner.name = "New Scanner"
        new_scanner.area_id = "new_room"
        new_scanner.area_name = "New Room"
        new_scanner.is_remote_scanner = True
        new_scanner.last_seen = 0.0
        new_scanner.async_as_scanner_get_stamp.return_value = 200.0

        with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=201.0):
            bermuda_advert.update_advertisement(mock_advertisement_data, new_scanner)

        # Scanner should be replaced
        assert bermuda_advert.scanner_device == new_scanner

    def test_update_advertisement_area_updated(
        self,
        bermuda_advert: BermudaAdvert,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test update_advertisement updates area from scanner."""
        # Change scanner's area
        mock_scanner_device.area_id = "new_area"
        mock_scanner_device.area_name = "New Area"
        mock_scanner_device.async_as_scanner_get_stamp.return_value = 200.0

        with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=201.0):
            bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)

        assert bermuda_advert.area_id == "new_area"
        assert bermuda_advert.area_name == "New Area"

    def test_update_advertisement_local_scanner_no_stamps(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
    ) -> None:
        """Test update_advertisement with local (non-remote) scanner."""
        scanner = MagicMock()
        scanner.address = normalize_mac("11:22:33:44:55:66")
        scanner.name = "Local Scanner"
        scanner.area_id = "local_room"
        scanner.area_name = "Local Room"
        scanner.is_remote_scanner = False  # Local scanner
        scanner.last_seen = 0.0

        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }

        with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=100.0):
            advert = BermudaAdvert(
                parent_device=mock_parent_device,
                advertisementdata=mock_advertisement_data,
                options=options,
                scanner_device=scanner,
            )

        assert advert.scanner_sends_stamps is False

    def test_update_advertisement_stale_stamp_ignored(
        self,
        bermuda_advert: BermudaAdvert,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test update_advertisement ignores older stamp."""
        bermuda_advert.stamp = 150.0  # Current stamp is newer
        mock_scanner_device.async_as_scanner_get_stamp.return_value = 100.0  # Older stamp

        with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=155.0):
            bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)

        # Stamp should not be updated
        assert bermuda_advert.stamp == 150.0

    def test_update_advertisement_future_stamp_ignored(
        self,
        bermuda_advert: BermudaAdvert,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test update_advertisement ignores future stamp."""
        mock_scanner_device.async_as_scanner_get_stamp.return_value = 200.0  # Future stamp
        bermuda_advert.stamp = 100.0

        # Current time is much earlier than the stamp
        with patch("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", return_value=100.0):
            bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)

        # Stale update count should increase
        assert bermuda_advert.stale_update_count > 0


class TestComputeSmoothedDistance:
    """Tests for _compute_smoothed_distance method."""

    def test_compute_smoothed_distance_with_kalman(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _compute_smoothed_distance uses Kalman-filtered RSSI."""
        bermuda_advert.rssi_filtered = -65.0
        result = bermuda_advert._compute_smoothed_distance()
        assert result is not None
        assert result > 0

    def test_compute_smoothed_distance_fallback_median(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _compute_smoothed_distance falls back to median."""
        bermuda_advert.rssi_filtered = None
        bermuda_advert.hist_distance_by_interval = [4.0, 5.0, 6.0, 4.5, 5.5]
        result = bermuda_advert._compute_smoothed_distance()
        # Median of sorted [4.0, 4.5, 5.0, 5.5, 6.0] is 5.0
        assert result == 5.0

    def test_compute_smoothed_distance_fallback_raw(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _compute_smoothed_distance falls back to raw distance."""
        bermuda_advert.rssi_filtered = None
        bermuda_advert.hist_distance_by_interval = []
        bermuda_advert.rssi_distance_raw = 7.5
        result = bermuda_advert._compute_smoothed_distance()
        assert result == 7.5

    def test_compute_smoothed_distance_even_history(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _compute_smoothed_distance median with even count."""
        bermuda_advert.rssi_filtered = None
        bermuda_advert.hist_distance_by_interval = [4.0, 5.0, 6.0, 7.0]
        result = bermuda_advert._compute_smoothed_distance()
        # Median of [4.0, 5.0, 6.0, 7.0] is (5.0 + 6.0) / 2 = 5.5
        assert result == 5.5


class TestDistanceVariance:
    """Tests for variance-based stability margin calculations.

    These tests verify the Gaussian Error Propagation formula for converting
    RSSI variance (dBm²) to distance variance (m²), including:
    - Correct formula: var_d = (d × ln(10) / (10 × n))² × var_RSSI
    - Edge cases: cold start, converged, uninitialized, near-field, far-field
    - Time-based staleness inflation
    """

    def test_get_distance_variance_normal_case(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance with normal initialized filter."""
        import math

        # Initialize Kalman filter with some samples
        for _ in range(10):
            bermuda_advert.rssi_kalman.update(-70.0)

        bermuda_advert.rssi_distance = 5.0
        bermuda_advert.rssi_distance_raw = 5.0

        variance = bermuda_advert.get_distance_variance()

        # Verify result is positive and reasonable
        assert variance > 0
        assert variance < 10.0  # Should be bounded

        # Verify formula: var_d = (d × ln(10) / (10 × n))² × var_RSSI
        # With d=5m, n=2.0, var_RSSI~4.0 (converged floor):
        # factor = 5 * ln(10) / 20 ≈ 0.576
        # var_d ≈ 0.576² * 4.0 ≈ 1.32
        expected_factor = (5.0 * math.log(10)) / (10.0 * 2.0)
        expected_var = (expected_factor**2) * 4.0  # Using converged floor
        assert abs(variance - expected_var) < 0.5  # Allow some tolerance

    def test_get_distance_variance_cold_start(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance during cold start (< 5 samples)."""
        from custom_components.bermuda.const import VARIANCE_FLOOR_COLD_START

        # Initialize Kalman filter with only 3 samples (cold start)
        for _ in range(3):
            bermuda_advert.rssi_kalman.update(-70.0)

        bermuda_advert.rssi_distance = 5.0
        bermuda_advert.rssi_distance_raw = 5.0

        variance = bermuda_advert.get_distance_variance()

        # Cold start should use higher variance floor (9.0)
        # factor = 5 * ln(10) / 20 ≈ 0.576
        # var_d ≈ 0.576² * 9.0 ≈ 2.98
        import math

        expected_factor = (5.0 * math.log(10)) / (10.0 * 2.0)
        expected_var = (expected_factor**2) * VARIANCE_FLOOR_COLD_START
        # Cold start variance should be higher than converged
        assert variance >= expected_var * 0.9  # Allow 10% tolerance

    def test_get_distance_variance_uninitialized_filter(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance with uninitialized Kalman filter."""
        from custom_components.bermuda.const import VARIANCE_FALLBACK_UNINIT

        # Don't initialize Kalman filter, but set distance
        # Use 2.0m to stay under MAX_DISTANCE_VARIANCE cap
        bermuda_advert.rssi_kalman.reset()
        bermuda_advert.rssi_distance = 2.0
        bermuda_advert.rssi_distance_raw = 2.0

        variance = bermuda_advert.get_distance_variance()

        # Uninitialized filter should use fallback variance (25.0 dBm²)
        import math

        expected_factor = (2.0 * math.log(10)) / (10.0 * 2.0)
        expected_var = (expected_factor**2) * VARIANCE_FALLBACK_UNINIT
        assert abs(variance - expected_var) < 0.1

    def test_get_distance_variance_near_field(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance returns fixed value for near-field (<0.5m)."""
        from custom_components.bermuda.const import NEAR_FIELD_DISTANCE_VARIANCE

        # Initialize filter
        for _ in range(10):
            bermuda_advert.rssi_kalman.update(-50.0)

        # Set very close distance
        bermuda_advert.rssi_distance = 0.3
        bermuda_advert.rssi_distance_raw = 0.3

        variance = bermuda_advert.get_distance_variance()

        # Near-field should return fixed variance (0.1 m²)
        assert variance == NEAR_FIELD_DISTANCE_VARIANCE

    def test_get_distance_variance_far_field_cap(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance is capped for far-field distances."""
        from custom_components.bermuda.const import MAX_DISTANCE_VARIANCE

        # Initialize filter with high variance
        bermuda_advert.rssi_kalman.update(-95.0)

        # Set very large distance
        bermuda_advert.rssi_distance = 50.0
        bermuda_advert.rssi_distance_raw = 50.0

        variance = bermuda_advert.get_distance_variance()

        # Far-field should be capped at MAX_DISTANCE_VARIANCE (4.0 m²)
        assert variance <= MAX_DISTANCE_VARIANCE

    def test_get_distance_variance_staleness_inflation(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance inflates variance for stale measurements."""
        import math

        # Initialize filter with some samples and set timestamp
        for i in range(10):
            bermuda_advert.rssi_kalman.update(-70.0, timestamp=100.0 + i)

        bermuda_advert.rssi_distance = 5.0
        bermuda_advert.rssi_distance_raw = 5.0

        # Get variance at current time (not stale)
        variance_fresh = bermuda_advert.get_distance_variance(nowstamp=110.0)

        # Get variance 30 seconds later (stale)
        variance_stale = bermuda_advert.get_distance_variance(nowstamp=140.0)

        # Stale variance should be higher due to time-based inflation
        assert variance_stale > variance_fresh

    def test_get_distance_variance_at_10m(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance at 10m distance (peer review edge case)."""
        import math

        # Initialize filter
        for _ in range(10):
            bermuda_advert.rssi_kalman.update(-80.0)

        bermuda_advert.rssi_distance = 10.0
        bermuda_advert.rssi_distance_raw = 10.0

        variance = bermuda_advert.get_distance_variance()

        # At d=10m, n=2.0, var_RSSI=4.0:
        # factor = 10 * ln(10) / 20 ≈ 1.151
        # var_d ≈ 1.151² * 4.0 ≈ 5.3 → capped at 4.0
        expected_factor = (10.0 * math.log(10)) / (10.0 * 2.0)
        expected_var = (expected_factor**2) * 4.0
        # Should be capped at MAX_DISTANCE_VARIANCE
        from custom_components.bermuda.const import MAX_DISTANCE_VARIANCE

        assert variance == min(expected_var, MAX_DISTANCE_VARIANCE)

    def test_get_distance_variance_different_attenuation(
        self,
        mock_parent_device: MagicMock,
        mock_advertisement_data: MagicMock,
        mock_scanner_device: MagicMock,
    ) -> None:
        """Test get_distance_variance with different attenuation values."""
        import math

        from custom_components.bermuda.const import VARIANCE_FLOOR_CONVERGED

        # Create advert with higher attenuation (3.5)
        options: dict[str, Any] = {
            CONF_RSSI_OFFSETS: {},
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 3.5,  # Higher attenuation
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        with patch(
            "custom_components.bermuda.bermuda_advert.monotonic_time_coarse",
            return_value=125.0,
        ):
            advert = BermudaAdvert(
                parent_device=mock_parent_device,
                advertisementdata=mock_advertisement_data,
                options=options,
                scanner_device=mock_scanner_device,
            )

        # Initialize filter
        for _ in range(10):
            advert.rssi_kalman.update(-70.0)

        advert.rssi_distance = 5.0
        advert.rssi_distance_raw = 5.0

        variance = advert.get_distance_variance()

        # Higher attenuation = smaller factor = smaller variance
        # factor = 5 * ln(10) / 35 ≈ 0.329
        # var_d ≈ 0.329² * 4.0 ≈ 0.43
        expected_factor = (5.0 * math.log(10)) / (10.0 * 3.5)
        expected_var = (expected_factor**2) * VARIANCE_FLOOR_CONVERGED
        assert abs(variance - expected_var) < 0.2

    def test_get_distance_variance_no_distance(self, bermuda_advert: BermudaAdvert) -> None:
        """Test get_distance_variance with no distance available falls back to 1m."""
        import math

        from custom_components.bermuda.const import VARIANCE_FALLBACK_UNINIT

        bermuda_advert.rssi_kalman.reset()
        bermuda_advert.rssi_distance = None
        bermuda_advert.rssi_distance_raw = None

        variance = bermuda_advert.get_distance_variance()

        # Should use fallback distance of 1m and fallback variance
        expected_factor = (1.0 * math.log(10)) / (10.0 * 2.0)
        expected_var = (expected_factor**2) * VARIANCE_FALLBACK_UNINIT
        assert abs(variance - expected_var) < 0.2

    def test_effective_rssi_variance_cold_start_floor(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_rssi_variance applies cold start floor."""
        from custom_components.bermuda.const import VARIANCE_FLOOR_COLD_START

        # Initialize with 2 samples (cold start)
        bermuda_advert.rssi_kalman.update(-70.0)
        bermuda_advert.rssi_kalman.update(-70.0)

        variance = bermuda_advert._get_effective_rssi_variance()

        # Cold start floor should be applied
        assert variance >= VARIANCE_FLOOR_COLD_START

    def test_effective_rssi_variance_converged_floor(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_rssi_variance applies converged floor."""
        from custom_components.bermuda.const import VARIANCE_FLOOR_CONVERGED

        # Initialize with many samples to converge
        for _ in range(50):
            bermuda_advert.rssi_kalman.update(-70.0)

        variance = bermuda_advert._get_effective_rssi_variance()

        # Converged floor should be applied
        assert variance >= VARIANCE_FLOOR_CONVERGED

    def test_effective_rssi_variance_time_inflation(self, bermuda_advert: BermudaAdvert) -> None:
        """Test _get_effective_rssi_variance applies time-based inflation."""
        # Initialize with timestamps
        for i in range(10):
            bermuda_advert.rssi_kalman.update(-70.0, timestamp=100.0 + i)

        # Last update was at timestamp 109.0
        variance_fresh = bermuda_advert._get_effective_rssi_variance(nowstamp=110.0)
        variance_stale = bermuda_advert._get_effective_rssi_variance(nowstamp=200.0)

        # Stale measurement should have higher variance
        assert variance_stale > variance_fresh

        # Check inflation amount: should be process_noise × DIFFERENCE in staleness
        # Fresh: staleness = 110.0 - 109.0 = 1.0s
        # Stale: staleness = 200.0 - 109.0 = 91.0s
        # Difference: 91.0 - 1.0 = 90.0s
        staleness_diff = (200.0 - 109.0) - (110.0 - 109.0)  # 90 seconds difference
        expected_inflation = bermuda_advert.rssi_kalman.process_noise * staleness_diff
        actual_inflation = variance_stale - variance_fresh
        assert abs(actual_inflation - expected_inflation) < 0.1


class TestVarianceConstants:
    """Tests to verify variance-related constants are correctly defined."""

    def test_variance_constants_values(self) -> None:
        """Test that variance constants have expected values."""
        from custom_components.bermuda.const import (
            MAX_DISTANCE_VARIANCE,
            MIN_DISTANCE_FOR_VARIANCE,
            MIN_VIRTUAL_VARIANCE,
            NEAR_FIELD_DISTANCE_VARIANCE,
            STABILITY_SIGMA_MOVING,
            STABILITY_SIGMA_SETTLING,
            STABILITY_SIGMA_STATIONARY,
            VARIANCE_COLD_START_SAMPLES,
            VARIANCE_FALLBACK_UNINIT,
            VARIANCE_FLOOR_COLD_START,
            VARIANCE_FLOOR_CONVERGED,
        )

        # Sigma factors
        assert STABILITY_SIGMA_MOVING == 2.0
        assert STABILITY_SIGMA_SETTLING == 2.0
        assert STABILITY_SIGMA_STATIONARY == 3.0

        # Variance floors (dBm²)
        assert VARIANCE_FLOOR_COLD_START == 9.0  # σ=3dB
        assert VARIANCE_FLOOR_CONVERGED == 4.0  # σ=2dB
        assert VARIANCE_FALLBACK_UNINIT == 25.0  # σ=5dB
        assert VARIANCE_COLD_START_SAMPLES == 5

        # Distance variance bounds (m²)
        assert MIN_DISTANCE_FOR_VARIANCE == 0.5
        assert NEAR_FIELD_DISTANCE_VARIANCE == 0.1
        assert MAX_DISTANCE_VARIANCE == 4.0
        assert MIN_VIRTUAL_VARIANCE == 0.25

    def test_variance_floor_cold_start_represents_3db_sigma(self) -> None:
        """Test that VARIANCE_FLOOR_COLD_START gives σ=3dB."""
        from custom_components.bermuda.const import VARIANCE_FLOOR_COLD_START
        import math

        sigma = math.sqrt(VARIANCE_FLOOR_COLD_START)
        assert abs(sigma - 3.0) < 0.01

    def test_variance_floor_converged_represents_2db_sigma(self) -> None:
        """Test that VARIANCE_FLOOR_CONVERGED gives σ=2dB."""
        from custom_components.bermuda.const import VARIANCE_FLOOR_CONVERGED
        import math

        sigma = math.sqrt(VARIANCE_FLOOR_CONVERGED)
        assert abs(sigma - 2.0) < 0.01

    def test_variance_fallback_represents_5db_sigma(self) -> None:
        """Test that VARIANCE_FALLBACK_UNINIT gives σ=5dB."""
        from custom_components.bermuda.const import VARIANCE_FALLBACK_UNINIT
        import math

        sigma = math.sqrt(VARIANCE_FALLBACK_UNINIT)
        assert abs(sigma - 5.0) < 0.01
