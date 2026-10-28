"""Redaction must not block the event loop and must not lose or leak data."""

from __future__ import annotations

import asyncio
import dataclasses
import random
import re
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_bytes

from custom_components.bermuda import services
from custom_components.bermuda.const import CONF_DEVICES
from custom_components.bermuda.diagnostics import async_get_config_entry_diagnostics
from custom_components.bermuda.services import BermudaServiceHandler

# Loop lag allowed in CI. asyncio itself calls a callback "slow" at 0.1 s; 0.25 s
# leaves room for a busy runner, while the old code blocks for several seconds.
MAX_LOOP_LAG_S = 0.25

# Size of the CI dummy: large enough that the old O(strings x keys) loop needs
# seconds, small enough to keep the test fast with the new code.
CI_DEVICES = 200
CI_STRINGS_PER_DEVICE = 20


def _mac(rng: random.Random) -> str:
    return ":".join(f"{rng.randrange(256):02x}" for _ in range(6))


def _forms(mac: str) -> list[str]:
    """Every spelling of a MAC that the dummy uses."""
    plain = mac.replace(":", "")
    return [mac, mac.upper(), mac.replace(":", "-"), mac.replace(":", "_"), plain, plain.upper()]


@dataclasses.dataclass
class _Reading:
    """Dataclass value inside the dump, like AreaTests in BermudaDevice.to_dict."""

    scanner: str
    rssi: float


@dataclasses.dataclass
class Attrappe:
    """Deterministic dump, the MACs it contains and the configuration that names them."""

    devices: dict[str, Any]
    scanners: list[str]
    device_macs: list[str]
    ibeacons: list[str]


def build_attrappe(devices: int, strings_per_device: int, seed: int = 1) -> Attrappe:
    """Build a dump with MACs in all spellings, advert keys and iBeacon ids."""
    rng = random.Random(seed)  # noqa: S311 - test data, not security
    scanners = [_mac(rng) for _ in range(8)]
    device_macs = [_mac(rng) for _ in range(devices)]
    ibeacons = [f"{rng.getrandbits(128):032x}_{rng.randrange(100)}_{rng.randrange(100)}" for _ in range(4)]
    out: dict[str, Any] = {}
    for idx, mac in enumerate(device_macs):
        strings: list[str] = []
        for n in range(strings_per_device):
            other = device_macs[(idx + n) % len(device_macs)]
            form = _forms(other)[n % 6]
            strings.append(f"seen {form} via {scanners[n % len(scanners)]} in area.raum_a")
        adverts = {f"{mac}__{scanners[k]}": {"rssi": -60 - k, "scanner": scanners[k]} for k in range(3)}
        out[mac] = {
            "address": mac,
            "name": f"office device {idx}",
            "history": strings,
            "adverts": adverts,
            "pair": (mac.upper(), scanners[idx % len(scanners)]),
            "reading": _Reading(scanner=scanners[idx % len(scanners)], rssi=-70.0),
            "beacon": f"{ibeacons[idx % len(ibeacons)]} raw {ibeacons[idx % len(ibeacons)].split('_')[0]}",
        }
    return Attrappe(devices=out, scanners=scanners, device_macs=device_macs, ibeacons=ibeacons)


def make_handler(hass: HomeAssistant | None, attrappe: Attrappe) -> BermudaServiceHandler:
    """Service handler whose coordinator knows every address of the dummy."""
    coord = MagicMock()
    coord.hass = hass
    coord.scanner_list = list(attrappe.scanners)
    coord.options = {CONF_DEVICES: list(attrappe.ibeacons)}
    coord.pb_state_sources = {}
    devices: dict[str, Any] = {}
    for mac, payload in attrappe.devices.items():
        device = MagicMock()
        device.address_type = "public"
        device.to_dict = MagicMock(return_value=payload)
        devices[mac] = device
    coord.devices = devices
    return BermudaServiceHandler(coord)


def raw_forms_pattern(attrappe: Attrappe) -> re.Pattern[str]:
    """Regex matching any raw identifier of the dummy, in any spelling."""
    alts: set[str] = set()
    for mac in attrappe.device_macs + attrappe.scanners:
        alts.update(re.escape(f) for f in _forms(mac))
    for beacon in attrappe.ibeacons:
        alts.add(re.escape(beacon.split("_")[0]))
    return re.compile("|".join(sorted(alts)), re.IGNORECASE)


async def run_with_max_lag(coro: Any) -> tuple[Any, float]:
    """Run ``coro`` while a 10 ms heartbeat records the largest loop delay."""
    loop = asyncio.get_running_loop()
    lag = 0.0
    stop = False

    async def beat() -> None:
        nonlocal lag
        while not stop:
            start = loop.time()
            await asyncio.sleep(0.01)
            lag = max(lag, loop.time() - start - 0.01)

    task = asyncio.ensure_future(beat())
    await asyncio.sleep(0.03)
    try:
        result = await coro
    finally:
        stop = True
        await task
    return result, lag


def shape(value: Any) -> Any:
    """Container structure without the leaf values: key counts and lengths, recursively."""
    if isinstance(value, dict):
        return ("d", [shape(v) for v in value.values()])
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return ("d", [shape(getattr(value, f.name)) for f in dataclasses.fields(value)])
    if isinstance(value, list | tuple):
        return ("l", [shape(v) for v in value])
    return "x"


# --------------------------------------------------------------------------- Z1


@pytest.mark.asyncio
async def test_dump_devices_redact_nonblocking(hass: HomeAssistant) -> None:
    """T-R1: dump_devices with redact keeps the loop responsive."""
    attrappe = build_attrappe(CI_DEVICES, CI_STRINGS_PER_DEVICE)
    handler = make_handler(hass, attrappe)
    call = MagicMock()
    call.data = {"redact": True}

    result, lag = await run_with_max_lag(handler.async_dump_devices(call))

    assert lag <= MAX_LOOP_LAG_S, f"event loop blocked for {lag:.3f} s"
    assert not raw_forms_pattern(attrappe).search(json_bytes(result).decode())


@pytest.mark.asyncio
async def test_dump_devices_redact_nonblocking_equivalence(hass: HomeAssistant) -> None:
    """T-R2: the redacted dump contains no raw identifier, but every placeholder kind."""
    attrappe = build_attrappe(40, 12, seed=7)
    handler = make_handler(hass, attrappe)
    call = MagicMock()
    call.data = {"redact": True}

    result = await handler.async_dump_devices(call)
    rendered = json_bytes(result).decode()

    assert not raw_forms_pattern(attrappe).search(rendered)
    # Expected placeholders derived from the dummy, not from handler.redactions (TA-9).
    first_scanner = attrappe.scanners[0]
    assert f"{first_scanner[:2]}::SCANNER_" in rendered
    assert "::OTHER_MAC_" in rendered
    beacon = attrappe.ibeacons[0]
    assert f"{beacon[:4]}::CFG_iBea_" in rendered


@pytest.mark.asyncio
async def test_redact_ibeacon_leak_order(hass: HomeAssistant) -> None:
    """T-R4: an iBeacon id behind a separator-less MAC is redacted as well."""
    beacon = "f7826da64fa24e988024bc5b71e0893e_1_2"
    uuid = beacon.split("_")[0]
    coord = MagicMock()
    coord.hass = hass
    coord.scanner_list = []
    # iBeacon first, MAC second: the old loop restored the iBeacon on the second hit.
    coord.options = {CONF_DEVICES: [beacon, "a1:b2:c3:d4:e5:f6"]}
    coord.pb_state_sources = {}
    coord.devices = {}
    handler = BermudaServiceHandler(coord)

    result = handler.redact_data(f"a1b2c3d4e5f6 near {uuid}")

    assert uuid not in result.lower()
    assert "a1b2c3d4e5f6" not in result.lower()


def test_redact_copy_before_iterate() -> None:
    """T-R5: a value whose field access mutates its parent dict does not break the walk."""
    parent: dict[str, Any] = {}

    @dataclasses.dataclass
    class Mutating:
        value: str

        def __getattribute__(self, name: str) -> Any:
            if name == "value":
                parent[f"added_{len(parent)}"] = "x"
            return object.__getattribute__(self, name)

    parent["a"] = Mutating(value="aa:bb:cc:dd:ee:01")
    parent["b"] = "plain"
    handler = make_handler(None, build_attrappe(1, 1))

    result = handler.redact_data(parent)

    assert "aa:bb:cc:dd:ee:01" not in json_bytes(result).decode()


@pytest.mark.asyncio
async def test_redact_snapshot_survives_prune(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-R6: clearing redactions on the loop while the table builds does not leak."""
    attrappe = build_attrappe(5, 4, seed=3)
    handler = make_handler(hass, attrappe)
    entered = threading.Event()
    release = threading.Event()
    original = services._build_table

    def slow_build(mapping: dict[str, str]) -> Any:
        entered.set()
        assert release.wait(5)
        return original(mapping)

    monkeypatch.setattr(services, "_build_table", slow_build)
    task = asyncio.ensure_future(handler.async_redact_data(attrappe.devices))
    assert await hass.async_add_executor_job(entered.wait, 5)
    handler.redactions.clear()  # what the prune routine does on the loop
    release.set()
    result = await task

    assert not raw_forms_pattern(attrappe).search(json_bytes(result).decode())


@pytest.mark.asyncio
async def test_redact_walk_runs_off_loop_thread(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-R14: the data walk runs in the executor, not on the event loop thread."""
    attrappe = build_attrappe(3, 2)
    handler = make_handler(hass, attrappe)
    seen: list[int] = []
    original = services._redact_walk

    def recording_walk(*args: Any) -> Any:
        seen.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(services, "_redact_walk", recording_walk)
    await handler.async_redact_data(attrappe.devices)

    assert seen, "_redact_walk was not called"
    assert seen[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_diagnostics_redact_nonblocking(hass: HomeAssistant) -> None:
    """T-R8: the integration diagnostics keep the loop responsive."""
    from custom_components.bermuda.bermuda_irk import BermudaIrkManager  # noqa: PLC0415
    from custom_components.bermuda.fmdn.manager import BermudaFmdnManager  # noqa: PLC0415

    attrappe = build_attrappe(CI_DEVICES, CI_STRINGS_PER_DEVICE)
    handler = make_handler(hass, attrappe)
    coord = handler.coordinator
    coord.service_handler = handler
    coord.service_dump_devices = handler.async_dump_devices
    coord.count_active_devices = MagicMock(return_value=1)
    coord.count_active_scanners = MagicMock(return_value=1)
    coord.irk_manager = BermudaIrkManager()
    coord.fmdn.manager = BermudaFmdnManager()
    coord.area_selection.get_auto_learning_diagnostics = MagicMock(return_value={})
    coord.area_selection.get_reference_tracker_diagnostics = MagicMock(return_value={})

    async def _bt() -> dict[str, Any]:
        return {}

    coord._manager.async_diagnostics = _bt
    entry = MagicMock()
    entry.runtime_data.coordinator = coord

    result, lag = await run_with_max_lag(async_get_config_entry_diagnostics(hass, entry))

    assert lag <= MAX_LOOP_LAG_S, f"event loop blocked for {lag:.3f} s"
    assert not raw_forms_pattern(attrappe).search(json_bytes(result).decode())


# ------------------------------------------------------------- key collisions


def test_redact_key_collision_keeps_both_entries() -> None:
    """T-R9: two keys that redact to the same text keep both entries."""
    handler = make_handler(None, build_attrappe(1, 1))
    data = {"aa:11:22:33:44:ff": 1, "aa:55:66:77:88:ff": 2}

    result = handler.redact_data(data)

    assert len(result) == 2
    assert sorted(result.values()) == [1, 2]
    assert any(key.endswith("~2") for key in result)


def test_redact_structure_preserved() -> None:
    """T-R10: redaction never changes how many keys or items a container has."""
    attrappe = build_attrappe(30, 6, seed=11)
    handler = make_handler(None, attrappe)
    handler.redaction_list_update()
    scanner = attrappe.scanners[0]
    placeholder = handler.redactions[scanner]
    data: dict[Any, Any] = dict(attrappe.devices)
    data["collisions"] = {
        "x~2": "before",
        "aa:11:22:33:44:ff": 1,  # fallback form, collides with the next key
        "aa:55:66:77:88:ff": 2,
        scanner: 3,  # collides with its upper-case twin
        scanner.upper(): 4,
        placeholder: 5,  # a clear key equal to a placeholder
        "x": 6,
        7: "int key",
        ("t", 1): "tuple key",
    }
    data["collisions"]["x~2 after"] = "after"

    result = handler.redact_data(data)

    assert shape(result) == shape(data)
    assert result["collisions"][7] == "int key"
    assert result["collisions"][("t", 1)] == "tuple key"


def test_redact_uuid_inside_manufacturer_hex() -> None:
    """T-R15: a configured iBeacon id inside manufacturer hex is still redacted."""
    uuid = "e2c56db5dffb48d2b060d0f5a71096e0"
    coord = MagicMock()
    coord.scanner_list = []
    coord.options = {CONF_DEVICES: [f"{uuid}_1_2"]}
    coord.pb_state_sources = {}
    coord.devices = {}
    handler = BermudaServiceHandler(coord)

    result = handler.redact_data({"manufacturer_data": f"4c000215{uuid}0001000200c5"})

    assert uuid not in result["manufacturer_data"].lower()
