from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.const import (
    DATA_EID_RESOLVER,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_TYPE_FMDN_SOURCE,
    SERVICE_UUID_FMDN,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.fmdn import extract_fmdn_eid


@pytest.fixture
def coordinator(hass):
    """Create a lightweight coordinator for testing."""

    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {}
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.er = er.async_get(hass)
    coordinator.dr = dr.async_get(hass)
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    return coordinator


def test_fmdn_resolution_registers_metadevice(hass, coordinator):
    """Resolve an FMDN frame and register the rotating source."""

    resolver = MagicMock()
    match = SimpleNamespace(device_id="fmdn-device-id")
    resolver.resolve_eid.return_value = match
    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: resolver}

    service_data = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x01" * 20}

    device_id = coordinator._process_fmdn_resolution("aa:bb:cc:dd:ee:ff", service_data)

    resolver.resolve_eid.assert_called_once_with(b"\x01" * 20)
    assert device_id == match.device_id

    coordinator._register_fmdn_source("aa:bb:cc:dd:ee:ff", device_id)

    metadevice = coordinator.metadevices[device_id]
    assert metadevice.create_sensor is True
    assert "aa:bb:cc:dd:ee:ff" in metadevice.metadevice_sources

    source_device = coordinator.devices["aa:bb:cc:dd:ee:ff"]
    assert METADEVICE_TYPE_FMDN_SOURCE in source_device.metadevice_type


def test_fmdn_resolution_without_googlefindmy(hass, coordinator):
    """Ignore FMDN adverts when the resolver integration is absent."""

    service_data = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x02" * 20}

    device_id = coordinator._process_fmdn_resolution("11:22:33:44:55:66", service_data)

    assert device_id is None
    assert DOMAIN_GOOGLEFINDMY not in hass.data


def test_fmdn_resolution_handles_missing_resolver_api(hass, coordinator):
    """Return None when a resolver object lacks the expected API."""

    hass.data[DOMAIN_GOOGLEFINDMY] = {DATA_EID_RESOLVER: object()}
    service_data = {SERVICE_UUID_FMDN: bytes([0x40]) + b"\x03" * 20}

    device_id = coordinator._process_fmdn_resolution("22:33:44:55:66:77", service_data)

    assert device_id is None


def test_extract_fmdn_eid_ignores_unknown_frame_types():
    """Return None for unsupported or malformed FMDN frames."""

    assert (
        extract_fmdn_eid({SERVICE_UUID_FMDN: bytes([0x41]) + b"\x04" * 20})
        is None
    )
