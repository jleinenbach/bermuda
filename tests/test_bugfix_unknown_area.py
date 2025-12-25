"""
Loud Test for 'Unknown Area' bug.
Verifies that updates are processed even if the winning scanner has no area_id.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.bermuda_advert import BermudaAdvert
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
)


def _make_coordinator() -> MagicMock:
    """Build a minimal coordinator stub with required attributes."""
    hass = MagicMock()
    hass.data = {}
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.options = {
        CONF_ATTENUATION: DEFAULT_ATTENUATION,
        CONF_REF_POWER: DEFAULT_REF_POWER,
        CONF_MAX_VELOCITY: DEFAULT_MAX_VELOCITY,
        CONF_SMOOTHING_SAMPLES: DEFAULT_SMOOTHING_SAMPLES,
        CONF_RSSI_OFFSETS: {},
    }
    coordinator.hass_version_min_2025_4 = True
    return coordinator


def _make_advertisement_data(rssi: int) -> MagicMock:
    """Create advertisement data with the required fields populated."""
    advertisement_data = MagicMock()
    advertisement_data.rssi = rssi
    advertisement_data.tx_power = None
    advertisement_data.local_name = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    return advertisement_data


def test_device_update_with_unknown_scanner_area() -> None:
    """
    SCENARIO:
    A tracker sends a strong signal to a scanner.
    The scanner (due to boot lag or config) has area_id = None.

    EXPECTATION:
    The device should ACCEPT the update (distance, scanner address).
    The device's area_id will correctly be None (since scanner is None),
    BUT area_distance must be set.

    FAILURE CONDITION (Old Code):
    The update is blocked, and area_distance remains None.
    """

    coordinator = _make_coordinator()
    device = BermudaDevice("AA:BB:CC:DD:EE:FF", coordinator)

    scanner = BermudaDevice("11:22:33:44:55:66", coordinator)
    scanner.area_id = None
    scanner.area_name = None

    advert_data = _make_advertisement_data(-50)
    advert = BermudaAdvert(device, advert_data, coordinator.options, scanner)
    advert.rssi_distance = 0.5
    advert.stamp = 1000.0

    device.apply_scanner_selection(advert, nowstamp=1000.0)

    print("\n--- LOUD TEST RESULT ---")
    print(f"Scanner Area: {scanner.area_id}")
    print(f"Device Distance: {device.area_distance}")
    print(f"Device Area: {device.area_id}")

    if device.area_distance == 0.5:
        print("✅ SUCCESS: Distance updated despite unknown scanner area.")
    else:
        print("❌ FAILURE: Distance ignored! Logic blocked the update.")
        pytest.fail("Device ignored update from scanner with no area_id")

    assert device.area_id is None
