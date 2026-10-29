"""Redaction must not block the event loop and must not lose or leak data."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import random
import re
import threading
import time
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


async def _reference_spin_lag(seconds: float) -> float:
    """Largest loop delay while a pure-Python thread computes for ``seconds``."""

    def spin() -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            sum(range(1000))

    loop = asyncio.get_running_loop()
    _result, lag = await run_with_max_lag(loop.run_in_executor(None, spin))
    return lag


async def lag_limit(floor: float) -> float:
    """
    Allowed loop delay: ``floor`` or 1.5 times that of a pure-Python thread, at most 1 s.

    On a loaded host any computing worker thread delays the loop (GIL handoff
    every 5 ms, scheduling); a fixed limit would measure the machine. The old
    code blocks for seconds and stays far above this limit; the cap of 1 s keeps
    a partial relapse visible even on a very busy host.
    """
    return min(1.0, max(floor, 1.5 * await _reference_spin_lag(1.0)))


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
    limit = await lag_limit(MAX_LOOP_LAG_S)

    assert lag <= limit, f"event loop blocked for {lag:.3f} s (limit {limit:.3f} s)"
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
    limit = await lag_limit(MAX_LOOP_LAG_S)

    assert lag <= limit, f"event loop blocked for {lag:.3f} s (limit {limit:.3f} s)"
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


@pytest.mark.skipif(os.environ.get("BERMUDA_BENCH") != "1", reason="benchmark, set BERMUDA_BENCH=1")
@pytest.mark.asyncio
async def test_full_size_dump_does_not_block_loop(hass: HomeAssistant) -> None:
    """
    Z1 at production size: an 8 to 9.5 MB dump with about 25 000 keys.

    A fixed millisecond limit would measure the machine: on a loaded host a
    pure-Python worker thread alone delays the loop by 50 to 200 ms (GIL
    handoff, scheduling). The limit is therefore the larger of 100 ms and
    1.5 times the delay of such a reference thread in the same run.
    """
    attrappe = build_attrappe(5000, 21, seed=5)
    size_mb = len(json_bytes(attrappe.devices)) / 1e6
    assert 8.0 <= size_mb <= 9.5, f"dummy has {size_mb:.2f} MB"
    handler = make_handler(hass, attrappe)

    start = time.perf_counter()
    handler.redaction_list_update()
    list_update = time.perf_counter() - start

    reference = await _reference_spin_lag(3.0)
    handler.redactions.clear()  # the measured call below is a real first call
    start = time.perf_counter()
    result, lag = await run_with_max_lag(handler.async_redact_data(attrappe.devices))
    total = time.perf_counter() - start
    limit = max(0.100, 1.5 * reference)
    print(  # noqa: T201
        f"size {size_mb:.2f} MB, keys {len(handler.redactions)}, list_update {list_update:.3f} s, "
        f"max_lag {lag:.3f} s, reference {reference:.3f} s, limit {limit:.3f} s, total {total:.2f} s"
    )

    assert lag <= limit
    assert total <= 30.0
    # One regex over 30 000 alternatives would not finish in time (the very
    # problem fixed here); a sample of plain substring checks is enough.
    rendered = json_bytes(result).decode().lower()
    sample = attrappe.device_macs[::50] + attrappe.scanners
    assert not [form for mac in sample for form in _forms(mac) if form.lower() in rendered]


def test_trie_pattern_skips_empty_key() -> None:
    """An empty key (e.g. an empty configured device) never becomes a match-all pattern."""
    assert services._trie_pattern([""]) is None
    pattern = services._trie_pattern(["", "ab"])
    assert pattern is not None
    assert re.fullmatch(pattern, "ab")
    assert not re.fullmatch(pattern, "")


def test_copy_retries_then_raises() -> None:
    """A copy that hits a concurrent change is retried, and a lasting failure surfaces."""
    calls: list[int] = []

    def flaky(container: list[int]) -> tuple[int, ...]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return tuple(container)

    assert services._copy(flaky, [1, 2]) == (1, 2)
    assert len(calls) == 2

    def broken(_container: list[int]) -> tuple[int, ...]:
        raise RuntimeError("dictionary changed size during iteration")

    with pytest.raises(RuntimeError):
        services._copy(broken, [1])


def test_walk_still_aborts_on_lasting_copy_failure() -> None:
    """A container that keeps changing while it is copied aborts the dump; it is not replaced by a placeholder."""

    class AlwaysChanging(dict[str, str]):
        def items(self) -> Any:
            msg = "changed size near aa:bb:cc:dd:ee:ff"
            raise RuntimeError(msg)

    table = services._build_table({})
    with pytest.raises(RuntimeError, match="changed") as err:
        services._redact_walk(table, re.compile(r"(?!x)x"), "", {"outer": [AlwaysChanging(k="v")]})
    # Home Assistant logs the traceback with its chain; the container's text must not be in it.
    assert "aa:bb:cc:dd:ee:ff" not in str(err.value)
    assert err.value.__cause__ is None
    assert err.value.__suppress_context__


@pytest.mark.asyncio
async def test_list_update_in_batches_covers_every_device(hass: HomeAssistant) -> None:
    """More devices than one batch: the async list update still names every device."""
    attrappe = build_attrappe(services._LIST_UPDATE_BATCH + 44, 1, seed=9)
    handler = make_handler(hass, attrappe)

    await handler._async_redaction_list_update()

    assert all(mac in handler.redactions for mac in attrappe.device_macs)


def test_prune_between_batches_keeps_every_entry() -> None:
    """R1-F1: a prune while the list update is suspended between batches loses nothing."""
    attrappe = build_attrappe(services._LIST_UPDATE_BATCH + 44, 1, seed=13)
    handler = make_handler(None, attrappe)
    steps = handler._redaction_list_steps()

    next(steps)  # suspended after the first batch, as the async update would be
    handler.redactions.clear()  # what the prune routine does on the loop
    for _ in steps:
        pass

    assert all(mac in handler.redactions for mac in attrappe.device_macs)
    assert all(scanner.replace(":", "") in handler.redactions for scanner in attrappe.scanners)


def test_redact_run_prefers_the_longer_key() -> None:
    """R1-F2: inside a longer run the longer of two overlapping keys wins."""
    table = services._build_table(
        {"aabbccddeeff00112233445566778899": "SHORT", "aabbccddeeff00112233445566778899_1_2": "LONG"}
    )
    handler = make_handler(None, build_attrappe(1, 1))

    result = services._redact_walk(
        table, handler._redact_generic_re, handler._redact_generic_sub, "x0aabbccddeeff00112233445566778899_1_2 end"
    )

    assert result == "x0LONG end"


def test_redact_sets_deques_and_other_mappings() -> None:
    """R1-F8: set, frozenset, deque and read-only mappings are redacted too."""
    from collections import deque  # noqa: PLC0415
    from types import MappingProxyType  # noqa: PLC0415

    handler = make_handler(None, build_attrappe(1, 1))
    handler.redactions = {"a1b2c3d4e5f6": "HIDDEN"}
    data = {
        "set": {"a1b2c3d4e5f6", "plain"},
        "frozen": frozenset({"a1b2c3d4e5f6"}),
        "deque": deque(["a1b2c3d4e5f6"]),
        "proxy": MappingProxyType({"a1b2c3d4e5f6": "a1b2c3d4e5f6"}),
    }

    result = handler.redact_data(data, first_recursion=False)

    assert result["set"] == {"HIDDEN", "plain"}
    assert result["frozen"] == frozenset({"HIDDEN"})
    assert isinstance(result["deque"], deque)
    assert list(result["deque"]) == ["HIDDEN"]
    assert result["proxy"] == {"HIDDEN": "HIDDEN"}


def test_seen_ibeacon_device_gets_both_placeholders() -> None:
    """A seen (not configured) iBeacon device is redacted as full id and as raw UUID."""
    beacon = "0123456789abcdef0123456789abcdef_7_8"
    device = MagicMock()
    device.address_type = "ibeacon"
    coord = MagicMock()
    coord.scanner_list = []
    coord.options = {}
    coord.devices = {beacon: device}
    handler = BermudaServiceHandler(coord)

    handler.redaction_list_update()

    assert "::OTHER_iBea_" in handler.redactions[beacon]
    assert "::OTHER_iBea_" in handler.redactions[beacon.split("_")[0]]


def test_slow_list_update_is_logged_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    """An update slower than 0.5 s is reported with its size."""
    handler = make_handler(None, build_attrappe(2, 1))
    clock = iter([100.0] + [101.0] * 20)

    with caplog.at_level("WARNING"), pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(services, "monotonic_time_coarse", lambda: next(clock))
        handler.redaction_list_update()

    assert "Redaction list update took" in caplog.text
