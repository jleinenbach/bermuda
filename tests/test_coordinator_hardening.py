"""Hardening tests for Bermuda coordinator hotspots."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.bermuda import coordinator as coordinator_mod
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.fmdn import BermudaFmdnManager, FmdnIntegration
from custom_components.bermuda.bermuda_irk import BermudaIrkManager
from custom_components.bermuda.const import (
    CONF_DEVICES,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _make_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
    """Build a lightweight coordinator for hotspot tests."""
    coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.options = {
        CONF_DEVICES: [],
        "attenuation": DEFAULT_ATTENUATION,
        "ref_power": DEFAULT_REF_POWER,
        "devtracker_nothome_timeout": DEFAULT_DEVTRACK_TIMEOUT,
        "smoothing_samples": DEFAULT_SMOOTHING_SAMPLES,
        "max_velocity": DEFAULT_MAX_VELOCITY,
        "max_radius": DEFAULT_MAX_RADIUS,
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
    coordinator.fmdn = FmdnIntegration(coordinator)
    coordinator.redactions = {}
    coordinator._redact_generic_re = re.compile(
        r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
    )
    coordinator._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"
    coordinator.pb_state_sources = {}
    coordinator.stamp_last_prune = 0
    coordinator.stamp_redactions_expiry = None
    coordinator.update_in_progress = False
    coordinator.last_update_success = False
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.config_entry = SimpleNamespace(async_on_unload=lambda cb: cb)  # type: ignore[assignment]
    return coordinator


def _configure_device(coordinator: BermudaDataUpdateCoordinator, address: str) -> BermudaDevice:
    """Create a BermudaDevice with default distance options."""
    device = coordinator._get_or_create_device(address)
    device.options.update(coordinator.options)
    return device


def test_prune_devices_handles_quota_shortfall(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> None:
    """Ensure prune_devices handles quota expansion without IndexError."""
    monkeypatch.setattr(coordinator_mod, "PRUNE_MAX_COUNT", 1)
    monkeypatch.setattr(coordinator_mod, "monotonic_time_coarse", lambda: 2000.0)
    coordinator = _make_coordinator(hass)

    kept_a = _configure_device(coordinator, "AA:BB:CC:00:00:01")
    kept_a.create_sensor = True
    kept_b = _configure_device(coordinator, "AA:BB:CC:00:00:02")
    kept_b.create_sensor = True
    prunable = _configure_device(coordinator, "AA:BB:CC:00:00:03")
    prunable.last_seen = 2000.0

    coordinator.prune_devices(force_pruning=True)

    assert kept_a.address in coordinator.devices
    assert kept_b.address in coordinator.devices
    assert prunable.address not in coordinator.devices


def test_refresh_area_by_min_distance_handles_empty_incumbent_history(
    monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant
) -> None:
    """A challenger with history should not fail when incumbent has none."""
    monkeypatch.setattr(coordinator_mod, "monotonic_time_coarse", lambda: 1000.0)
    coordinator = _make_coordinator(hass)
    device = _configure_device(coordinator, "AA:BB:CC:11:22:33")

    incumbent = SimpleNamespace(
        name="incumbent",
        area_id="area-inc",
        area_name="area-inc",
        rssi_distance=3.0,
        rssi=-60.0,
        stamp=995.0,
        scanner_device=SimpleNamespace(last_seen=995.0, name="scanner-inc", address="00:00:00:00:00:01", floor_id=None),
        hist_distance_by_interval=[],
    )
    incumbent.median_rssi = lambda: -60.0
    challenger = SimpleNamespace(
        name="challenger",
        area_id="area-new",
        area_name="area-new",
        rssi_distance=2.0,
        rssi=-55.0,
        stamp=999.0,
        scanner_device=SimpleNamespace(last_seen=999.0, name="scanner-new", address="00:00:00:00:00:02", floor_id=None),
        hist_distance_by_interval=[2.1, 2.0, 1.9, 1.8],
    )
    challenger.median_rssi = lambda: -55.0

    device.area_advert = incumbent  # type: ignore[assignment]
    device.adverts = {"incumbent": incumbent, "challenger": challenger}  # type: ignore[dict-item]

    coordinator._refresh_area_by_min_distance(device)

    assert device.area_advert is challenger  # type: ignore[comparison-overlap]


def test_redact_data_handles_many_entries(hass: HomeAssistant) -> None:
    """Large redaction sets should remain functional."""
    coordinator = _make_coordinator(hass)
    coordinator.redactions = {f"aa:bb:cc:dd:ee:{i:02x}": f"redacted-{i}" for i in range(500)}

    result = coordinator.redact_data("AA:BB:CC:DD:EE:00 is present", first_recursion=False)

    assert "redacted-0" in result


@pytest.mark.asyncio
async def test_async_update_data_returns_internal_result(hass: HomeAssistant) -> None:
    """Ensure _async_update_data propagates the internal result."""
    coordinator = _make_coordinator(hass)
    sentinel = object()

    coordinator._async_update_data_internal = lambda: sentinel  # type: ignore[method-assign, assignment, return-value]

    result = await coordinator._async_update_data()

    assert result is sentinel


@pytest.mark.asyncio
async def test_dump_devices_limits_when_over_soft_cap(
    monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant
) -> None:
    """Dump service should fall back when device graph is oversized."""
    monkeypatch.setattr(coordinator_mod, "DUMP_DEVICE_SOFT_LIMIT", 2)
    coordinator = _make_coordinator(hass)
    coordinator.options[CONF_DEVICES] = ["AA:BB:CC:DD:EE:01"]
    coordinator._scanner_list = {"aa:bb:cc:dd:ee:ff"}
    coordinator.pb_state_sources = {"entity.test": "AA:BB:CC:DD:EE:03"}

    class DummyDevice:
        def __init__(self, address: str) -> None:
            self.address = address
            self.address_type = 0

        def to_dict(self) -> dict[str, str]:
            return {"address": self.address}

    for suffix in ("01", "03", "ff", "10", "11"):
        address = f"AA:BB:CC:DD:EE:{suffix}"
        coordinator.devices[address.lower()] = DummyDevice(address.lower())  # type: ignore[assignment]

    response = await coordinator.service_dump_devices(SimpleNamespace(data={}))  # type: ignore[arg-type]

    assert isinstance(response, dict)
    assert "summary" in response
    summary = response["summary"]
    assert isinstance(summary, dict)
    assert summary["limited"] is True
    assert summary["requested_devices"] == 5
    devices = response["devices"]
    assert isinstance(devices, dict)
    assert len(devices) < len(coordinator.devices)
