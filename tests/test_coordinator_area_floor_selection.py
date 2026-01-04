"""Coordinator area+floor selection tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bleak.backends.scanner import AdvertisementData
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.fmdn import BermudaFmdnManager
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_MAX_RADIUS,
    CONF_REF_POWER,
    DEFAULT_ATTENUATION,
    DEFAULT_MAX_RADIUS,
    DEFAULT_REF_POWER,
    EVIDENCE_WINDOW_SECONDS,
)


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Create a lightweight coordinator instance."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_MAX_RADIUS: DEFAULT_MAX_RADIUS,
        CONF_REF_POWER: DEFAULT_REF_POWER,
        CONF_ATTENUATION: DEFAULT_ATTENUATION,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn_manager = BermudaFmdnManager()
    return coordinator


def test_tracker_device_gets_area_and_floor(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Area selection should run for tracker-only devices."""
    coordinator = _make_coordinator(hass)
    floor_entry = coordinator.fr.async_create("Test Floor")
    area_entry = coordinator.ar.async_create("Test Area", floor_id=floor_entry.floor_id)

    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner._update_area_and_floor(area_entry.id)

    tracked = coordinator._get_or_create_device("AA:BB:CC:DD:EE:FF")
    tracked.create_sensor = False
    tracked.create_tracker_done = True
    selection_calls: dict[str, object] = {}

    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -50.0
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    base_time = monotonic_time_coarse()
    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.rssi_to_metres", lambda *_: 1.0)
    original_apply_selection = tracked.apply_scanner_selection

    def _capture_selection(
        advert: BermudaAdvert | None, *, nowstamp: float | None = None, source: str = "selection"
    ) -> None:
        selection_calls["advert"] = advert
        selection_calls["nowstamp"] = nowstamp
        selection_calls["source"] = source
        return original_apply_selection(advert, nowstamp=nowstamp, source=source)

    monkeypatch.setattr(tracked, "apply_scanner_selection", _capture_selection)
    tracked.apply_scanner_selection(None, nowstamp=base_time)
    assert selection_calls["advert"] is None
    selection_calls.clear()

    tracked.process_advertisement(scanner, advertisement_data)
    advert = next(iter(tracked.adverts.values()))
    assert advert.area_id == area_entry.id
    assert advert.stamp is not None
    assert advert.stamp >= base_time - EVIDENCE_WINDOW_SECONDS
    assert len(tracked.adverts) == 1
    tracked.calculate_data()
    assert advert.rssi_distance is not None
    assert coordinator.effective_distance(advert, base_time) == advert.rssi_distance
    assert advert.rssi_distance <= coordinator.options[CONF_MAX_RADIUS]

    with caplog.at_level("DEBUG"):
        coordinator._refresh_area_by_min_distance(tracked)

    debug_messages = [rec.getMessage() for rec in caplog.records]
    assert advert.area_id == area_entry.id
    assert selection_calls["advert"] is advert, debug_messages
    assert tracked.area_advert is not None
    assert tracked.area_distance is not None
    assert tracked.area_id == area_entry.id
    assert tracked.floor_id == floor_entry.floor_id
    assert tracked.area_advert is advert
