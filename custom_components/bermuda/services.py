"""
Service handlers for Bermuda BLE Trilateration.

This module contains the service handlers for the Bermuda integration,
following Home Assistant's pattern of separating service logic from the
coordinator (similar to ESPHome, ZHA, and Bluetooth integrations).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    CONF_DEVICES,
    PRUNE_TIME_REDACTIONS,
)
from .util import mac_explode_formats

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall, ServiceResponse

    from .coordinator import BermudaDataUpdateCoordinator


# Soft limit for device dump to prevent excessive response sizes
DUMP_DEVICE_SOFT_LIMIT = 1200


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

    async def async_dump_devices(self, call: ServiceCall) -> ServiceResponse:
        """
        Return a dump of beacon advertisements by receiver.

        Args:
            call: The service call with optional parameters:
                - addresses: Space-separated list of addresses to include
                - redact: Whether to redact MAC addresses
                - configured_devices: Whether to include configured devices

        Returns:
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
                out[address] = device.to_dict()  # type: ignore[no-untyped-call]

        if summary is not None:
            out = {"summary": summary, "devices": out}

        if redact:
            _stamp_redact = monotonic_time_coarse()
            out_response = cast("ServiceResponse", self.redact_data(out))
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
        _stamp = monotonic_time_coarse()
        coord = self.coordinator

        # counter for incrementing replacement names (eg, SCANNER_n). The length
        # of the existing redaction list is a decent enough starting point.
        i = len(self.redactions)

        # SCANNERS
        for non_lower_address in coord.scanner_list:
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                for altmac in mac_explode_formats(address):
                    self.redactions[altmac] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"
        _LOGGER.debug("Redact scanners: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))

        # CONFIGURED DEVICES
        for non_lower_address in coord.options.get(CONF_DEVICES, []):
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                if address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::CFG_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::CFG_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is, but not a mac.
                    self.redactions[address] = f"CFG_OTHER_{1}_{address}"
        _LOGGER.debug("Redact confdevs: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))

        # EVERYTHING ELSE
        for non_lower_address, device in coord.devices.items():
            address = non_lower_address.lower()
            if address not in self.redactions:
                # Only add if they are not already there.
                i += 1
                if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                    self.redactions[address] = f"{address[:4]}::IRK_DEV_{i}"
                elif address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::OTHER_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::OTHER_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:  # a MAC
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is.
                    self.redactions[address] = f"OTHER_{i}_{address}"
        _LOGGER.debug("Redact therest: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))

        _elapsed = monotonic_time_coarse() - _stamp
        if _elapsed > 0.5:
            _LOGGER.warning("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))
        else:
            _LOGGER.debug("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))
        self.stamp_redactions_expiry = monotonic_time_coarse() + PRUNE_TIME_REDACTIONS

    def redact_data(self, data: Any, first_recursion: bool = True) -> Any:  # noqa: FBT001
        """
        Wash any collection of data of any MAC addresses.

        Uses the redaction list of substitutions if already created, then
        washes any remaining mac-like addresses. This routine is recursive,
        so if you're changing it bear that in mind!

        Args:
            data: The data to redact (can be str, dict, list, or other)
            first_recursion: Whether this is the first/outer call

        Returns:
            The redacted data with MAC addresses replaced.

        """
        if first_recursion:
            # On first/outer call, refresh the redaction list to ensure
            # we don't let any new addresses slip through. Might be expensive
            # on first call, but will be much cheaper for subsequent calls.
            self.redaction_list_update()
            first_recursion = False

        if isinstance(data, str):  # Base Case
            datalower = data.lower()
            # the end of the recursive wormhole, do the actual work:
            if datalower in self.redactions:
                # Full string match, a quick short-circuit
                data = self.redactions[datalower]
            else:
                # Search for any of the redaction strings in the data.
                items = tuple(self.redactions.items())
                for find, fix in items:
                    if find in datalower:
                        data = datalower.replace(find, fix)
                        # don't break out because there might be multiple fixes required.
            # redactions done, now replace any remaining MAC addresses
            # We are only looking for xx:xx:xx... format.
            return self._redact_generic_re.sub(self._redact_generic_sub, data)
        elif isinstance(data, dict):
            return {self.redact_data(k, False): self.redact_data(v, False) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.redact_data(v, False) for v in data]
        else:  # Base Case
            return data
