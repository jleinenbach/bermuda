"""Tests for apply_scanner_selection edge cases."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from bleak.backends.scanner import AdvertisementData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_DEVTRACK_TIMEOUT,
    CONF_REF_POWER,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_REF_POWER,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Build a minimal coordinator suitable for BermudaDevice construction."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_DEVTRACK_TIMEOUT: DEFAULT_DEVTRACK_TIMEOUT,
        CONF_REF_POWER: DEFAULT_REF_POWER,
        CONF_ATTENUATION: DEFAULT_ATTENUATION,
    }
    coordinator.devices = {}
    coordinator.metadevices = {}
    coordinator.correlations = {}  # Scanner correlation data for area confidence
    coordinator.room_profiles = {}  # Room-level scanner pair delta profiles
    coordinator._seed_configured_devices_done = False
    coordinator._scanner_init_pending = False
    coordinator._hascanners = set()
    coordinator._scanners = set()
    coordinator._scanner_list = set()
    coordinator._scanners_without_areas = None
    coordinator.ar = ar.async_get(hass)
    coordinator.fr = fr.async_get(hass)
    coordinator.irk_manager = BermudaIrkManager()
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.hass_version_min_2025_4 = False
    return coordinator


@pytest.mark.parametrize("create_sensor", [False, True])
async def test_apply_sets_area_from_advert(hass: HomeAssistant, create_sensor: bool) -> None:
    """Ensure selection applies advert metadata without crashing."""
    coordinator = _make_coordinator(hass)
    area_registry = ar.async_get(hass)
    area_entry = area_registry.async_create("Area Friendly Name")
    advert_area_id = area_entry.id

    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:01")
    device.create_sensor = create_sensor
    base_stamp = 1000.0

    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner.update_area_and_floor(advert_area_id)

    advert = SimpleNamespace(
        area_id=advert_area_id,
        area_name=area_entry.name,
        scanner_address=scanner.address,
        scanner_device=scanner,
        rssi_distance=None,
        rssi=-55.0,
        stamp=base_stamp,
    )

    device.apply_scanner_selection(advert, nowstamp=base_stamp + 1.0)  # type: ignore[arg-type]

    assert device.area_id == advert_area_id
    assert device.area_name == area_entry.name
    assert device.area_advert is advert  # type: ignore[comparison-overlap]


def test_apply_selection_does_not_log_spam(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Area change should log once and identical repeats should be quiet."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:10")
    device.create_sensor = True
    area_entry = ar.async_get(hass).async_create("Area 1")

    advert = SimpleNamespace(
        area_id=area_entry.id,
        area_name=area_entry.name,
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id=area_entry.id, area_name=area_entry.name),
        rssi_distance=1.0,
        rssi=-60.0,
        stamp=100.0,
    )

    with caplog.at_level(logging.DEBUG):
        device.apply_scanner_selection(advert, nowstamp=advert.stamp + 1.0)  # type: ignore[arg-type]
        device.apply_scanner_selection(advert, nowstamp=advert.stamp + 2.0)  # type: ignore[arg-type]

    area_change_logs = [rec for rec in caplog.records if "was in" in rec.getMessage()]
    assert len(area_change_logs) == 1


def test_apply_accepts_nowstamp_keyword(hass: HomeAssistant) -> None:
    """Coordinator-style invocation should not raise when passing nowstamp."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:02")

    advert = SimpleNamespace(
        area_id="kinderzimmer_yuna",
        area_name="Yunas Zimmer",
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id="kinderzimmer_yuna", area_name="Yunas Zimmer"),
        rssi_distance=None,
        rssi=-60.0,
        stamp=101.0,
    )

    device.apply_scanner_selection(advert, nowstamp=150.0)  # type: ignore[arg-type]

    assert device.area_advert is advert  # type: ignore[comparison-overlap]


@pytest.mark.parametrize("raw_timeout", [None, "", "30s"])
def test_tracker_timeout_parsing_is_resilient(hass: HomeAssistant, raw_timeout: object) -> None:
    """Invalid tracker timeout values should fall back safely."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:09")
    device.options[CONF_DEVTRACK_TIMEOUT] = raw_timeout
    device.last_seen = 0.0

    advert = SimpleNamespace(
        area_id="area-parse",
        area_name="Parse Area",
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id="area-parse", area_name="Parse Area"),
        rssi_distance=None,
        rssi=-55.0,
        stamp=100.0,
    )

    device.apply_scanner_selection(advert, nowstamp=120.0)  # type: ignore[arg-type]

    assert device.last_seen == pytest.approx(100.0)


def test_last_seen_not_bumped_from_stale_advert(hass: HomeAssistant) -> None:
    """Stale evidence must not promote last_seen to the refresh timestamp."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:03")

    base_stamp = 1_000.0
    device.last_seen = 800.0
    device.options[CONF_DEVTRACK_TIMEOUT] = 30.0

    advert = SimpleNamespace(
        area_id="windfang",
        area_name="Windfang",
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id="windfang", area_name="Windfang"),
        rssi_distance=None,
        rssi=-63.0,
        stamp=base_stamp,
    )

    device.apply_scanner_selection(advert, nowstamp=base_stamp + 40.0)  # type: ignore[arg-type]

    assert device.last_seen == pytest.approx(800.0)

    fresh_advert = SimpleNamespace(
        area_id="windfang",
        area_name="Windfang",
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id="windfang", area_name="Windfang"),
        rssi_distance=None,
        rssi=-61.0,
        stamp=base_stamp + 5.0,
    )

    device.apply_scanner_selection(fresh_advert, nowstamp=base_stamp + 6.0)  # type: ignore[arg-type]

    assert device.last_seen == pytest.approx(base_stamp + 5.0)


def test_future_stamp_does_not_bump_last_seen(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """Future-dated adverts must not advance last_seen in selection (unit test)."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:04")
    area_entry = ar.async_get(hass).async_create("Future Area")
    device.last_seen = 50.0

    base_time = 100.0
    future_stamp = base_time + 2.0
    advert = SimpleNamespace(
        area_id=area_entry.id,
        area_name=area_entry.name,
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id=area_entry.id, area_name=area_entry.name),
        rssi_distance=1.0,
        rssi=-60.0,
        stamp=future_stamp,
    )

    device.apply_scanner_selection(advert, nowstamp=base_time)  # type: ignore[arg-type]

    metadata = device.area_state_metadata(stamp_now=base_time)
    assert device.last_seen == pytest.approx(50.0)
    assert device.area_id == area_entry.id
    assert device.area_distance is None
    assert device.area_state_stamp is None
    last_good_age: float | bool | str | None = metadata["last_good_area_age_s"]
    assert last_good_age is None or (isinstance(last_good_age, (int, float)) and last_good_age >= 0)


def test_future_stamp_in_process_advertisement_guarded(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """Future-dated adverts must not advance last_seen in process_advertisement."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:0B")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:77")
    scanner._is_remote_scanner = True  # noqa: SLF001
    future_stamp = 200.0
    base_time = 100.0

    def _fake_mono() -> float:
        return base_time

    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", _fake_mono)
    scanner.async_as_scanner_get_stamp = MagicMock(return_value=future_stamp)  # type: ignore[method-assign]

    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -60
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    device.last_seen = base_time - 10.0

    device.process_advertisement(scanner, advertisement_data)

    assert device.last_seen == pytest.approx(base_time - 10.0)
    assert scanner.last_seen < base_time + 0.1


def test_future_stamp_skips_scanner_last_seen(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> None:
    """Scanner last_seen must not jump forward on future remote stamps (production path)."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:0C")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:78")
    scanner._is_remote_scanner = True  # noqa: SLF001
    base_time = 200.0
    future_stamp = base_time + 5.0
    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: base_time)
    scanner.async_as_scanner_get_stamp = MagicMock(return_value=future_stamp)  # type: ignore[method-assign]

    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -65
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    scanner.last_seen = base_time - 20

    device.process_advertisement(scanner, advertisement_data)

    assert scanner.last_seen <= base_time
    assert len(device.adverts) == 1


def test_process_advertisement_sets_distance_stamp_from_advert(
    monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant
) -> None:
    """Remote scanner stamps must carry through to distance_stamp (production path)."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:0D")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:79")
    area_entry = ar.async_get(hass).async_create("Stamped Area")
    scanner.update_area_and_floor(area_entry.id)
    base_time = 300.0

    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.rssi_to_metres", lambda *_: 1.5)
    scanner._is_remote_scanner = True  # noqa: SLF001
    scanner.async_as_scanner_get_stamp = MagicMock(return_value=base_time)  # type: ignore[method-assign]

    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -60
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    device.process_advertisement(scanner, advertisement_data)
    advert = next(iter(device.adverts.values()))
    advert.calculate_data()

    device.apply_scanner_selection(advert, nowstamp=base_time)

    assert device.area_id == area_entry.id
    assert advert.rssi_distance is not None
    assert device.area_distance == pytest.approx(advert.rssi_distance)
    assert device.area_distance_stamp == pytest.approx(base_time)


def test_distance_stamp_not_inflated_without_stamp(hass: HomeAssistant) -> None:
    """Distance retention must not fabricate a stamp when advert stamp is missing (unit test)."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:0E")
    area_entry = ar.async_get(hass).async_create("No Stamp Area")

    advert = SimpleNamespace(
        area_id=area_entry.id,
        area_name=area_entry.name,
        scanner_address="scanner-addr",
        scanner_device=SimpleNamespace(area_id=area_entry.id, area_name=area_entry.name),
        rssi_distance=2.5,
        rssi=-60.0,
        stamp=None,
    )

    device.apply_scanner_selection(advert, nowstamp=10.0)  # type: ignore[arg-type]

    assert device.area_distance == pytest.approx(2.5)
    assert device.area_distance_stamp is None


def test_local_scanner_distance_uses_synthetic_stamp(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> None:
    """Local scanners without stamps should use their synthetic stamp, not nowstamp."""
    coordinator = _make_coordinator(hass)
    device = coordinator._get_or_create_device("AA:BB:CC:DD:EE:0F")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:80")
    area_entry = ar.async_get(hass).async_create("Local Area")
    scanner.update_area_and_floor(area_entry.id)
    base_time = 400.0

    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_device.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.coordinator.monotonic_time_coarse", lambda: base_time)
    monkeypatch.setattr("custom_components.bermuda.bermuda_advert.rssi_to_metres", lambda *_: 2.0)

    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -55
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    device.process_advertisement(scanner, advertisement_data)
    advert = next(iter(device.adverts.values()))
    advert.calculate_data()

    device.apply_scanner_selection(advert, nowstamp=base_time)

    assert device.area_id == area_entry.id
    assert advert.rssi_distance is not None
    assert device.area_distance == pytest.approx(advert.rssi_distance)
    assert device.area_distance_stamp == pytest.approx(base_time - 3.0)


def _make_advertisement_data(rssi: int = -60) -> AdvertisementData:
    """Construct minimal advertisement data object."""
    return SimpleNamespace(  # type: ignore[return-value]
        rssi=rssi,
        tx_power=None,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        local_name=None,
    )


def test_scanner_none_does_not_clear_advert_area(hass: HomeAssistant) -> None:
    """Scanner without area must not clobber advert area metadata."""
    coordinator = _make_coordinator(hass)
    parent = coordinator._get_or_create_device("AA:BB:CC:DD:EE:05")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner.area_id = "orig-area"
    scanner.area_name = "Original"

    advertisement_data = _make_advertisement_data()
    bermuda_advert = BermudaAdvert(parent, advertisement_data, parent.options, scanner)

    assert bermuda_advert.area_id == "orig-area"

    scanner.area_id = None
    scanner.area_name = None
    bermuda_advert.update_advertisement(_make_advertisement_data(-59), scanner)

    assert bermuda_advert.area_id == "orig-area"
    assert bermuda_advert.area_name == "Original"


def test_scanner_area_overwrite_applies(hass: HomeAssistant) -> None:
    """Scanner with a real area should overwrite advert metadata."""
    coordinator = _make_coordinator(hass)
    parent = coordinator._get_or_create_device("AA:BB:CC:DD:EE:06")
    scanner = coordinator._get_or_create_device("11:22:33:44:55:67")
    scanner.area_id = "first-area"
    scanner.area_name = "First"

    bermuda_advert = BermudaAdvert(parent, _make_advertisement_data(), parent.options, scanner)
    assert bermuda_advert.area_id == "first-area"

    scanner.area_id = "second-area"
    scanner.area_name = "Second"
    bermuda_advert.update_advertisement(_make_advertisement_data(-58), scanner)

    assert bermuda_advert.area_id == "second-area"
    assert bermuda_advert.area_name == "Second"


def test_metadevice_fallback_processes_advert(hass: HomeAssistant) -> None:
    """Metadevices should process adverts when linked to the source."""
    coordinator = _make_coordinator(hass)
    metadevice = coordinator._get_or_create_device("AA:BB:CC:DD:EE:07")
    metadevice.metadevice_sources.append("11-22-33-44-55-66")

    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    advertisement_data = MagicMock()
    advertisement_data.rssi = -60
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    metadevice.process_advertisement(scanner, advertisement_data)

    assert len(metadevice.adverts) == 1


def test_metadevice_rejects_unlinked_advert_when_existing(hass: HomeAssistant) -> None:
    """Metadevices should skip adverts from unrelated scanners when already populated."""
    coordinator = _make_coordinator(hass)
    metadevice = coordinator._get_or_create_device("AA:BB:CC:DD:EE:08")
    metadevice.metadevice_sources.append("11:22:33:44:55:66")

    scanner_allowed = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner_blocked = coordinator._get_or_create_device("AA:AA:AA:AA:AA:AA")
    advertisement_data = MagicMock(spec=AdvertisementData)
    advertisement_data.rssi = -60
    advertisement_data.tx_power = None
    advertisement_data.manufacturer_data = {}
    advertisement_data.service_data = {}
    advertisement_data.service_uuids = []
    advertisement_data.local_name = None

    metadevice.process_advertisement(scanner_allowed, advertisement_data)
    assert len(metadevice.adverts) == 1

    metadevice.process_advertisement(scanner_blocked, advertisement_data)

    assert len(metadevice.adverts) == 1
