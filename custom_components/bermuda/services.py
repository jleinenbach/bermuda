"""
Service handlers for Bermuda BLE Trilateration.

This module contains the service handlers for the Bermuda integration,
following Home Assistant's pattern of separating service logic from the
coordinator (similar to ESPHome, ZHA, and Bluetooth integrations).
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime as dt
import itertools
import numbers
import re
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    _LOGGER,
    ADDR_TYPE_FMDN_DEVICE,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    CONF_DEVICES,
    DOMAIN,
    PRUNE_TIME_REDACTIONS,
)
from .util import mac_explode_formats

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .coordinator import BermudaDataUpdateCoordinator


# Soft limit for device dump to prevent excessive response sizes
DUMP_DEVICE_SOFT_LIMIT = 1200


DUMP_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Optional("addresses"): cv.string,
        vol.Optional("configured_devices"): cv.boolean,
        vol.Optional("redact"): cv.boolean,
    }
)
RESET_ROOM_AUTO_LEARNING_SCHEMA = vol.Schema({vol.Required("area_id"): vol.All(cv.ensure_list, [cv.string])})

# Attempts to copy a container that another thread mutates while it is copied.
_COPY_ATTEMPTS = 3
# Devices handled between two hand-backs to the event loop in the async list update.
_LIST_UPDATE_BATCH = 64
# The walk hands the GIL back once every this many mappings (time.sleep(0) costs
# about 70 us on a loaded host; once per mapping added seconds to a large dump).
_GIL_YIELD_EVERY = 8
# Keys classified between two hand-backs while the table is built.
_BUILD_YIELD_EVERY = 1024
_mapping_counter = itertools.count()
# Containers walked member by member, and the type they come back as. Subclasses
# (a namedtuple, say) come back as the base type, as before.
_SEQUENCE_KINDS: tuple[tuple[type, Any], ...] = (
    (list, list),
    (tuple, tuple),
    (set, set),
    (frozenset, frozenset),
    (collections.deque, collections.deque),
)


# A "run" is a hex sequence with optional single separators, the shape of every
# MAC spelling, UUID, IRK, EID and HA device id in the redaction list.
_RUN_SHAPE = re.compile(r"[0-9a-f]+(?:[:._-][0-9a-f]+)*\Z")


@dataclasses.dataclass(frozen=True, slots=True)
class RedactionTable:
    """
    Immutable snapshot of the redaction list, ready for use in any thread.

    ``source`` is the list the table was built from (cache key) and ``mapping``
    holds its lower-cased keys. Keys shaped like a run are found by ``runs``,
    a small fixed pattern, and looked up in ``mapping``; ``run_lengths`` lists
    their lengths, longest first, for runs that contain a key instead of being
    one. The few remaining keys (e.g. ``fmdn:<id>``) are matched by ``other``.
    """

    source: Mapping[str, str]
    mapping: Mapping[str, str]
    runs: re.Pattern[str] | None
    run_lengths: tuple[int, ...]
    other: re.Pattern[str] | None


def _trie_pattern(keys: Iterable[str]) -> str | None:
    """
    Build one regex that matches any of ``keys``, longest match first.

    Only used for the few keys that are not runs: one regex over all keys
    does not scale (25 000 keys: 421 000 characters, 19 s to compile, and the
    parser's garbage delayed the event loop by 3.3 s through GC).
    """
    end = ""  # marks "a key ends here"; empty keys are skipped below, so it never collides
    root: dict[str, Any] = {}
    for key in keys:
        if not key:
            continue
        node = root
        for char in key:
            node = node.setdefault(char, {})
        node[end] = {}
    if not root:
        return None

    def render(node: dict[str, Any]) -> str:
        alts = [re.escape(char) + render(child) for char, child in sorted(node.items()) if char != end]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        # Greedy optional: the longer key wins, the shorter one is the fallback.
        return "(?:" + body + ")?" if end in node else body

    return render(root)


def _build_table(source: Mapping[str, str]) -> RedactionTable:
    """Compile the redaction list. Pure, so it may run in the executor."""
    mapping: dict[str, str] = {}
    for count, (key, placeholder) in enumerate(source.items()):
        if count % _BUILD_YIELD_EVERY == 0:
            time.sleep(0)  # hand the GIL to the event loop, see _redact_mapping
        # Keys that differ only in case collapse into one; the first one wins.
        mapping.setdefault(key.lower(), placeholder)
    run_keys = [key for key in mapping if _RUN_SHAPE.match(key)]
    other_keys = [key for key in mapping if not _RUN_SHAPE.match(key)]
    run_lengths = tuple(sorted({len(key) for key in run_keys}, reverse=True))
    runs = None
    if run_lengths:
        # Only runs at least as long as the shortest key can contain one.
        runs = re.compile(
            rf"(?=[0-9a-f:._-]{{{run_lengths[-1]}}})[0-9a-f]+(?:[:._-][0-9a-f]+)*", re.ASCII | re.IGNORECASE
        )
    other_text = _trie_pattern(other_keys)
    other = re.compile(other_text, re.ASCII | re.IGNORECASE) if other_text else None
    return RedactionTable(source=dict(source), mapping=mapping, runs=runs, run_lengths=run_lengths, other=other)


def _redact_run(table: RedactionTable, run: str) -> str:
    """
    Replace the keys inside one run, leftmost and longest first.

    Most runs are a key (a MAC, a UUID) and need one lookup. A longer run,
    such as manufacturer data, may contain a key anywhere; it is scanned
    with one lookup per position and key length.
    """
    low = run.lower()
    whole = table.mapping.get(low)
    if whole is not None:
        return whole
    parts: list[str] = []
    size = len(run)
    start = done = 0
    while start < size:
        for length in table.run_lengths:
            end = start + length
            if end <= size and (hit := table.mapping.get(low[start:end])) is not None:
                parts.extend((run[done:start], hit))
                start = done = end
                break
        else:
            start += 1
    if not parts:
        return run
    parts.append(run[done:])
    return "".join(parts)


def _copy(make: Any, container: Any) -> Any:
    """
    Copy ``container`` with ``make`` before iterating it.

    ``list(d.items())`` and ``tuple(seq)`` run as one C call and do not let the
    loop thread in between, so the walk never iterates a container that changes
    under it. A garbage collector run or a finalizer can still release the GIL
    in rare cases, hence the retry.
    """
    for attempt in range(_COPY_ATTEMPTS):
        try:
            return make(container)
        except RuntimeError:
            if attempt == _COPY_ATTEMPTS - 1:
                raise
    return make(container)  # pragma: no cover - loop above always returns or raises


def _redact_str(table: RedactionTable, generic_re: re.Pattern[str], generic_sub: str, text: str) -> str:
    """Replace every known identifier in ``text``, then any remaining MAC address."""
    full = table.mapping.get(text.lower())
    if full is not None:
        text = full
    else:
        mapping = table.mapping
        # Each pass replaces every hit; untouched text keeps its case.
        if table.other is not None:
            text = table.other.sub(lambda m: mapping.get(m.group(0).lower(), m.group(0)), text)
        if table.runs is not None:
            text = table.runs.sub(lambda m: _redact_run(table, m.group(0)), text)
    return generic_re.sub(generic_sub, text)


def _redact_mapping(
    table: RedactionTable, generic_re: re.Pattern[str], generic_sub: str, items: Iterable[tuple[Any, Any]]
) -> dict[Any, Any]:
    """
    Redact keys and values of a mapping without losing an entry.

    Two keys can redact to the same text (generic MAC form, case variants, a
    clear key equal to a placeholder). The later one gets the suffix ``~n``
    with the smallest free ``n``. The test is ``in out`` and not ``!= key``:
    a collision is about the result, not about whether this key changed.
    """
    # Hand the GIL to the event loop regularly. Without this, a waiting loop
    # thread gets the GIL only every switch interval (5 ms) per handoff, and a
    # heartbeat needs several handoffs (measured: 249 ms -> 31 ms delay at a
    # 6.4 MB dump). time.sleep(0) releases the GIL without sleeping.
    if next(_mapping_counter) % _GIL_YIELD_EVERY == 0:
        time.sleep(0)
    out: dict[Any, Any] = {}
    for key, value in items:
        new_key = _redact_walk(table, generic_re, generic_sub, key)
        if new_key in out:
            base = new_key if isinstance(new_key, str) else repr(new_key)
            n = 2
            while f"{base}~{n}" in out:
                n += 1
            new_key = f"{base}~{n}"
        out[new_key] = _redact_walk(table, generic_re, generic_sub, value)
    return out


def _redact_walk(table: RedactionTable, generic_re: re.Pattern[str], generic_sub: str, data: Any) -> Any:
    """
    Wash any collection of data of known identifiers and MAC addresses.

    Pure function of its arguments, so it may run in the executor. Every
    mutable container is copied before it is iterated (see ``_copy``).
    """
    if isinstance(data, str):
        return _redact_str(table, generic_re, generic_sub, data)
    if isinstance(data, Mapping):
        # dict, and read-only or custom mappings (e.g. MappingProxyType) as a plain dict.
        return _redact_mapping(table, generic_re, generic_sub, _copy(lambda d: list(d.items()), data))
    for kind, make in _SEQUENCE_KINDS:
        if isinstance(data, kind):
            # Redacted members of a set may coincide; a set has no place to keep both.
            # tuple() of a tuple returns the same object, so this copies only what can change.
            return make(_redact_walk(table, generic_re, generic_sub, v) for v in _copy(tuple, data))
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        # Dataclass values (e.g. AreaTests in BermudaDevice.to_dict) would otherwise be
        # serialized with their fields untouched, MAC addresses included. A shallow field
        # mapping avoids the deep copy of dataclasses.asdict(); nested values recurse here.
        return {
            f.name: _redact_walk(table, generic_re, generic_sub, getattr(data, f.name))
            for f in dataclasses.fields(data)
        }
    if data is None or isinstance(data, (numbers.Number, dt.date, dt.time, dt.timedelta)):
        return data
    return _redact_object(table, generic_re, generic_sub, data)


def _redact_object(table: RedactionTable, generic_re: re.Pattern[str], generic_sub: str, data: object) -> Any:
    """
    Redact an object that is neither a container nor a plain value.

    Such an object (e.g. a BLEDevice in the Bluetooth diagnostics) would be
    serialized after redaction: the diagnostics endpoint uses ExtendedJSONEncoder,
    which calls as_dict() or falls back to repr(), MAC address included. Produce
    the same shape here, redacted.
    """
    as_dict = getattr(data, "as_dict", None)
    if callable(as_dict):
        mapped = as_dict()
        if isinstance(mapped, Mapping):
            return _redact_walk(table, generic_re, generic_sub, mapped)
    return {"__type": str(type(data)), "repr": _redact_str(table, generic_re, generic_sub, repr(data))}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """
    Register the integration services.

    The services are registered once in async_setup, so they exist while no
    config entry is loaded; a call without a loaded entry fails with a
    ServiceValidationError. Bermuda allows a single config entry.
    """

    def _coordinator() -> BermudaDataUpdateCoordinator:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="integration_not_loaded")
        return cast("BermudaDataUpdateCoordinator", entries[0].runtime_data.coordinator)

    async def _dump_devices(call: ServiceCall) -> ServiceResponse:
        return await _coordinator().service_dump_devices(call)

    async def _reset_room_auto_learning(call: ServiceCall) -> ServiceResponse:
        return await _coordinator().service_reset_room_auto_learning(call)

    # Admin only: reset_room_auto_learning deletes learned data, dump_devices
    # exposes every address. Automations run without a user and stay allowed.
    async_register_admin_service(
        hass, DOMAIN, "dump_devices", _dump_devices, DUMP_DEVICES_SCHEMA, SupportsResponse.ONLY
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "reset_room_auto_learning",
        _reset_room_auto_learning,
        RESET_ROOM_AUTO_LEARNING_SCHEMA,
        SupportsResponse.OPTIONAL,
    )


class BermudaServiceHandler:
    """
    Handles service calls for Bermuda integration.

    This class encapsulates all service-related logic, keeping the
    coordinator focused on data updates and device management.
    """

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Initialize the service handler.

        Args:
        ----
            coordinator: The Bermuda data update coordinator instance.

        """
        self.coordinator = coordinator

        # Redaction data for privacy-safe dumps
        self.redactions: dict[str, str] = {}
        self.stamp_redactions_expiry: float | None = None

        # Compiled regex for generic MAC redaction (compile once, use many times)
        # MAC addresses may have [:_-] separators
        self._redact_generic_re = re.compile(
            r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
        )
        self._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"
        # Compiled redaction table, rebuilt only when the redaction list changed.
        self._redaction_table: RedactionTable | None = None

    async def async_reset_room_auto_learning(self, call: ServiceCall) -> ServiceResponse:
        """
        Remove auto-learned data from the shared room profiles of the given areas.

        Args:
        ----
            call: The service call with the required field area_id (one or more
                Home Assistant area ids).

        Returns:
        -------
            Number of removed auto-learned samples per area id.

        """
        removed = await self.coordinator.async_reset_room_auto_learning(list(call.data["area_id"]))
        return cast("ServiceResponse", removed)

    async def async_dump_devices(self, call: ServiceCall) -> ServiceResponse:
        """
        Return a dump of beacon advertisements by receiver.

        Args:
        ----
            call: The service call with optional parameters:
                - addresses: Space-separated list of addresses to include
                - redact: Whether to redact MAC addresses
                - configured_devices: Whether to include configured devices

        Returns:
        -------
            A dictionary containing device data, optionally redacted.

        """
        out: dict[str, Any] = {}
        addresses_input = call.data.get("addresses", "")
        redact = call.data.get("redact", False)
        configured_devices = call.data.get("configured_devices", False)
        summary: dict[str, Any] | None = None

        coord = self.coordinator

        # Choose filter for device/address selection
        addresses: list[str] = []
        if addresses_input != "":
            # Specific devices
            addresses += addresses_input.upper().split()
        if configured_devices:
            # configured and scanners
            addresses += list(coord.scanner_list)
            configured_devices_option = coord.options.get(CONF_DEVICES, [])
            if isinstance(configured_devices_option, list):
                addresses += [str(device) for device in configured_devices_option]
            # known IRK/Private BLE Devices
            addresses += list(coord.pb_state_sources)

        dump_all_devices = addresses_input == "" and not configured_devices
        if dump_all_devices and len(coord.devices) > DUMP_DEVICE_SOFT_LIMIT:
            fallback_addresses: set[str] = set(coord.scanner_list)
            configured_devices_option = coord.options.get(CONF_DEVICES, [])
            if isinstance(configured_devices_option, list):
                fallback_addresses.update(str(device) for device in configured_devices_option)
            fallback_addresses.update(
                str(source_address) for source_address in coord.pb_state_sources.values() if source_address is not None
            )
            addresses = list(map(str.lower, fallback_addresses))
            summary = {
                "limited": True,
                "reason": (
                    f"Device dump limited to configured devices because total devices "
                    f"({len(coord.devices)}) exceeded soft cap ({DUMP_DEVICE_SOFT_LIMIT})."
                ),
                "requested_devices": len(coord.devices),
                "returned_devices": len(addresses),
            }

        # lowercase all the addresses for matching
        addresses = list(map(str.lower, addresses))

        # Build the dict of devices
        for address, device in coord.devices.items():
            if len(addresses) == 0 or address.lower() in addresses:
                out[address] = device.to_dict()

        if summary is not None:
            out = {"summary": summary, "devices": out}

        if redact:
            _stamp_redact = monotonic_time_coarse()
            out_response = cast("ServiceResponse", await self.async_redact_data(out))
            _stamp_redact_elapsed = monotonic_time_coarse() - _stamp_redact
            if _stamp_redact_elapsed > 3:  # It should be fast now.
                _LOGGER.warning("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
            else:
                _LOGGER.debug("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
            return out_response

        return cast("ServiceResponse", out)

    def redaction_list_update(self) -> None:
        """
        Freshen or create the list of match/replace pairs for MAC redaction.

        This gives a set of helpful address replacements that still allows
        identifying device entries without disclosing MAC addresses.
        """
        for _ in self._redaction_list_steps():
            pass

    async def _async_redaction_list_update(self) -> None:
        """Same as ``redaction_list_update``, handing the loop back between batches."""
        for _ in self._redaction_list_steps():
            await asyncio.sleep(0)

    def _redaction_list_steps(self) -> Iterator[None]:
        """
        Do the work of ``redaction_list_update``, yielding between batches.

        Iterates copies of the coordinator collections, so a device added or
        removed while the async caller is suspended cannot break the loop. The
        first call for thousands of devices took 0.10 to 0.16 s in one piece.
        """
        _stamp = monotonic_time_coarse()
        coord = self.coordinator

        # counter for incrementing replacement names (eg, SCANNER_n). The length
        # of the existing redaction list is a decent enough starting point.
        # Work on a copy and publish it in one step at the end: the prune routine
        # clears self.redactions on the loop and may run while the async caller
        # is suspended between two batches.
        work = dict(self.redactions)
        i = len(work)

        # SCANNERS
        for non_lower_address in list(coord.scanner_list):
            address = non_lower_address.lower()
            if address not in work:
                i += 1
                for altmac in mac_explode_formats(address):
                    work[altmac] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"
        _LOGGER.debug("Redact scanners: %ss, %d items", monotonic_time_coarse() - _stamp, len(work))

        # CONFIGURED DEVICES
        for non_lower_address in list(coord.options.get(CONF_DEVICES, [])):
            address = non_lower_address.lower()
            if address not in work:
                i += 1
                if address.count("_") == 2:
                    work[address] = f"{address[:4]}::CFG_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    work[address.split("_")[0]] = f"{address[:4]}::CFG_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:
                    for altmac in mac_explode_formats(address):
                        work[altmac] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is, but not a mac.
                    work[address] = f"CFG_OTHER_{i}"
        _LOGGER.debug("Redact confdevs: %ss, %d items", monotonic_time_coarse() - _stamp, len(work))

        # EVERYTHING ELSE
        for count, (non_lower_address, device) in enumerate(list(coord.devices.items()), start=1):
            if count % _LIST_UPDATE_BATCH == 0:
                yield
            address = non_lower_address.lower()
            if address not in work:
                # Only add if they are not already there.
                i += 1
                if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                    work[address] = f"{address[:4]}::IRK_DEV_{i}"
                elif address.count("_") == 2:
                    work[address] = f"{address[:4]}::OTHER_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    work[address.split("_")[0]] = f"{address[:4]}::OTHER_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:  # a MAC
                    for altmac in mac_explode_formats(address):
                        work[altmac] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is.
                    work[address] = f"OTHER_{i}"
            # FMDN ids from the device itself: the manager only knows a tracker once an
            # EID was seen since start, but the device dump lists every known tracker.
            # Only FMDN meta devices carry these ids (fmdn/integration.py sets both together).
            if device.address_type == ADDR_TYPE_FMDN_DEVICE:
                for attr, prefix in (("fmdn_device_id", "FMDN_DEV"), ("fmdn_canonical_id", "FMDN_CANON")):
                    value = getattr(device, attr, None)
                    if isinstance(value, str) and value and value.lower() not in work:
                        i += 1
                        work[value.lower()] = f"{prefix}_{i}"
        _LOGGER.debug("Redact therest: %ss, %d items", monotonic_time_coarse() - _stamp, len(work))

        # IRKS: key material, kept to the first two bytes like upstream #840.
        for irk_hex in coord.irk_manager.known_irks_hex():
            irk_lower = irk_hex.lower()
            if irk_lower not in work:
                i += 1
                work[irk_lower] = f"{irk_lower[:4]}::IRK_{i}"

        yield
        # FMDN: EIDs, device ids and canonical ids identify a tracker.
        prefixes = {"eid": "EID", "device_id": "FMDN_DEV", "canonical_id": "FMDN_CANON"}
        for kind, value in coord.fmdn.manager.known_identifiers():
            value_lower = value.lower()
            if value_lower and value_lower not in work:
                i += 1
                work[value_lower] = f"{prefixes.get(kind, 'FMDN_ID')}_{i}"
        _LOGGER.debug("Redact irk/fmdn: %ss, %d items", monotonic_time_coarse() - _stamp, len(work))

        _elapsed = monotonic_time_coarse() - _stamp
        if _elapsed > 0.5:
            _LOGGER.warning("Redaction list update took %.3f seconds, has %d items", _elapsed, len(work))
        else:
            _LOGGER.debug("Redaction list update took %.3f seconds, has %d items", _elapsed, len(work))
        self.redactions = work
        self.stamp_redactions_expiry = monotonic_time_coarse() + PRUNE_TIME_REDACTIONS

    def _table_for(self, snapshot: Mapping[str, str]) -> RedactionTable | None:
        """Return the cached table if it was built from ``snapshot``, else None."""
        table = self._redaction_table
        if table is not None and table.source == snapshot:
            return table
        return None

    def redact_data(self, data: Any, first_recursion: bool = True) -> Any:  # noqa: FBT001
        """
        Wash any collection of data of any MAC addresses, synchronously.

        Uses the redaction list of substitutions, then washes any remaining
        mac-like addresses. Runs in the calling thread; on the event loop use
        ``async_redact_data`` for anything larger than a few values.

        Args:
        ----
            data: The data to redact (can be str, dict, list, or other)
            first_recursion: Whether to refresh the redaction list first

        Returns:
        -------
            The redacted data with MAC addresses replaced.

        """
        if first_recursion:
            # Refresh the redaction list so that no new address slips through.
            self.redaction_list_update()
        snapshot = dict(self.redactions)
        table = self._table_for(snapshot)
        if table is None:
            table = self._redaction_table = _build_table(snapshot)
        return _redact_walk(table, self._redact_generic_re, self._redact_generic_sub, data)

    async def async_redact_data(self, data: Any) -> Any:
        """
        Redact ``data`` without blocking the event loop.

        The redaction list is refreshed on the loop in batches and copied. Compiling the
        table and walking the data run in the executor on that copy: the prune
        routine clears ``self.redactions`` on the loop, and a thread iterating
        the live dict would fail or miss entries.
        """
        await self._async_redaction_list_update()
        snapshot = dict(self.redactions)
        hass = self.coordinator.hass
        table = self._table_for(snapshot)
        if table is None:
            table = await hass.async_add_executor_job(_build_table, snapshot)
            self._redaction_table = table
        return await hass.async_add_executor_job(
            _redact_walk, table, self._redact_generic_re, self._redact_generic_sub, data
        )
