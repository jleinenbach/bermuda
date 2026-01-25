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
    assert (
        bermuda_advert.rssi_distance == 1.5
    ), f"Expected raw distance 1.5m for quick approach response, got {bermuda_advert.rssi_distance}m"


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
    assert (
        2.9 <= bermuda_advert.rssi_distance <= 3.1
    ), f"Expected stable median ~3.0m, got {bermuda_advert.rssi_distance}m"


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
        assert (
            advert.conf_rssi_offset == 20
        ), "Advert should see updated RSSI offset after shared options dict is modified"
