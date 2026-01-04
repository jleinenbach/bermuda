"""FMDN integration layer for Bermuda coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from homeassistant.const import Platform

from ..const import (
    _LOGGER,
    ADDR_TYPE_FMDN_DEVICE,
    DATA_EID_RESOLVER,
    DEFAULT_FMDN_EID_FORMAT,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
)
from ..util import normalize_identifier
from .extraction import extract_fmdn_eids
from .manager import BermudaFmdnManager, EidResolutionStatus

if TYPE_CHECKING:
    from ..bermuda_device import BermudaDevice
    from ..coordinator import BermudaDataUpdateCoordinator


class EidResolver(Protocol):
    """Protocol for the googlefindmy EID resolver."""

    def resolve_eid(self, eid: bytes) -> Any | None:
        """Resolve an EID to a device match."""


class FmdnIntegration:
    """
    FMDN integration layer for Bermuda coordinator.

    This class encapsulates all FMDN-specific logic that was previously
    scattered throughout the coordinator. It handles:
    - EID resolution via googlefindmy integration
    - FMDN metadevice creation and registration
    - FMDN source device management
    - Advertisement processing for FMDN devices
    """

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Initialize the FMDN integration."""
        self.coordinator = coordinator
        self.manager = BermudaFmdnManager()

    def get_resolver(self) -> EidResolver | None:
        """Return the googlefindmy resolver from ``hass.data`` when present."""
        bucket = self.coordinator.hass.data.get(DOMAIN_GOOGLEFINDMY)
        if not isinstance(bucket, dict):
            return None

        resolver = bucket.get(DATA_EID_RESOLVER)
        resolve_eid = getattr(resolver, "resolve_eid", None)
        if resolver is None:
            return None
        if not callable(resolve_eid):
            _LOGGER.debug("Resolver missing resolve_eid callable: %s", type(resolver))
            return None

        return cast("EidResolver", resolver)

    def format_metadevice_address(self, device_id: str, canonical_id: str | None) -> str:
        """
        Return the canonical key for an FMDN metadevice.

        Always uses device_id (HA Device Registry ID) for stable addresses across restarts.
        Previously, using canonical_id caused duplicate entities because:
        1. _register_fmdn_source() gets canonical_id from EID resolver
        2. discover_fmdn_metadevices() extracts it from Device Registry identifiers
        3. These can have different formats depending on GoogleFindMy version/config
        4. Whichever function runs first determines the address
        5. After reboot, execution order can change → different address → different
           unique_id → persisted entities with old unique_id + new entities = duplicates

        The device_id (HA Device Registry ID) is stable and unique per device entry,
        making the metadevice address deterministic regardless of execution order.
        """
        # canonical_id is kept as parameter for future use (logging, diagnostics)
        # but not used for address generation to ensure stability
        return normalize_identifier(f"fmdn:{device_id}")

    @staticmethod
    def normalize_eid_bytes(eid_data: bytes | bytearray | memoryview | str | None) -> bytes | None:
        """Return EID payload as bytes, accepting raw bytes or hex strings."""
        if eid_data is None:
            return None

        if isinstance(eid_data, (bytes, bytearray, memoryview)):
            return bytes(eid_data)

        if isinstance(eid_data, str):
            cleaned = eid_data.replace("0x", "").replace(":", "").replace(" ", "")
            try:
                return bytes.fromhex(cleaned)
            except ValueError:
                _LOGGER.debug("Failed to parse EID hex string: %s", eid_data)
                return None

        _LOGGER.debug("Unsupported EID payload type: %s", type(eid_data))
        return None

    def extract_eids(self, service_data: Mapping[str | int, Any]) -> set[bytes]:
        """Extract an FMDN EID using the configured format."""
        return extract_fmdn_eids(service_data, mode=DEFAULT_FMDN_EID_FORMAT)

    def process_resolution(self, eid_bytes: bytes) -> Any | None:
        """Resolve an EID payload to a Home Assistant device registry id."""
        match, _ = self.process_resolution_with_status(eid_bytes, source_mac="unknown")
        return match

    def process_resolution_with_status(
        self, eid_bytes: bytes, source_mac: str
    ) -> tuple[Any | None, EidResolutionStatus]:
        """
        Resolve an EID payload and track the resolution status.

        Returns:
            Tuple of (match result, resolution status)

        """
        resolver = self.get_resolver()

        if resolver is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_UNAVAILABLE)
            return None, EidResolutionStatus.RESOLVER_UNAVAILABLE

        normalized_eid = self.normalize_eid_bytes(eid_bytes)
        if normalized_eid is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.NO_KNOWN_EID_MATCH)
            return None, EidResolutionStatus.NO_KNOWN_EID_MATCH

        try:
            match = resolver.resolve_eid(normalized_eid)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Resolver raised while processing EID payload", exc_info=True)
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_ERROR)
            return None, EidResolutionStatus.RESOLVER_ERROR

        if match is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.NO_KNOWN_EID_MATCH)
            return None, EidResolutionStatus.NO_KNOWN_EID_MATCH
        # Match found - will be recorded by caller with device_id
        return match, EidResolutionStatus.NOT_EVALUATED

    def register_source(self, source_device: BermudaDevice, metadevice_address: str, match: Any) -> None:
        """Attach a rotating FMDN source MAC to its stable metadevice container."""
        fmdn_device_id = getattr(match, "device_id", None)

        # IMPORTANT: Before creating a new metadevice, check if one already exists
        # for this fmdn_device_id. This prevents duplicate devices when
        # discover_fmdn_metadevices() has already created a metadevice with a
        # different canonical_id format from the device registry identifiers.
        # The fmdn_device_id is the HA Device Registry ID, which is stable.
        existing_metadevice = None
        if fmdn_device_id:
            for existing in self.coordinator.metadevices.values():
                if existing.fmdn_device_id == fmdn_device_id:
                    existing_metadevice = existing
                    _LOGGER.debug(
                        "Found existing FMDN metadevice %s for device_id %s in register_source",
                        existing.address,
                        fmdn_device_id,
                    )
                    break

        if existing_metadevice is not None:
            metadevice = existing_metadevice
        else:
            metadevice = self.coordinator._get_or_create_device(metadevice_address)

        metadevice.metadevice_type.add(METADEVICE_FMDN_DEVICE)
        metadevice.address_type = ADDR_TYPE_FMDN_DEVICE
        metadevice.fmdn_device_id = fmdn_device_id
        # Update fmdn_canonical_id from the resolver if not already set
        canonical_id = getattr(match, "canonical_id", None)
        if canonical_id and (metadevice.fmdn_canonical_id is None):
            metadevice.fmdn_canonical_id = canonical_id
        # Since the googlefindmy integration discovered this device, we always
        # create sensors for them (like Private BLE Devices).
        metadevice.create_sensor = True

        if metadevice.address not in self.coordinator.metadevices:
            self.coordinator.metadevices[metadevice.address] = metadevice

        if metadevice.fmdn_device_id and (device_entry := self.coordinator.dr.async_get(metadevice.fmdn_device_id)):
            metadevice.name_devreg = device_entry.name
            metadevice.name_by_user = device_entry.name_by_user
            metadevice.make_name()

        source_device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        if source_device.address not in metadevice.metadevice_sources:
            metadevice.metadevice_sources.insert(0, source_device.address)

    def handle_advertisement(self, device: BermudaDevice, service_data: Mapping[str | int, Any]) -> None:
        """Process FMDN payloads for an advertisement."""
        if not service_data:
            return

        candidates = self.extract_eids(service_data)
        if not candidates:
            return

        device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        # Track whether we found any match for this advertisement
        any_resolved = False

        for eid_bytes in candidates:
            match, _resolution_status = self.process_resolution_with_status(eid_bytes, device.address)

            if match is None:
                # EID was seen but not resolved - already tracked in process_resolution_with_status
                continue

            resolved_device_id = getattr(match, "device_id", None)
            canonical_id = getattr(match, "canonical_id", None)
            is_shared = bool(getattr(match, "shared", False))

            if is_shared and resolved_device_id is None and canonical_id is None:
                _LOGGER.debug("Skipping shared FMDN match without identifiers")
                # Track as unresolved since we can't use it
                self.manager.record_resolution_failure(
                    eid_bytes, device.address, EidResolutionStatus.NO_KNOWN_EID_MATCH
                )
                continue
            if resolved_device_id is None:
                _LOGGER.debug("Resolver returned match without device_id for candidate length %d", len(eid_bytes))
                self.manager.record_resolution_failure(
                    eid_bytes, device.address, EidResolutionStatus.NO_KNOWN_EID_MATCH
                )
                continue

            # Successfully resolved - record in FMDN manager
            self.manager.record_resolution_success(
                eid_bytes, device.address, str(resolved_device_id), canonical_id
            )
            any_resolved = True

            metadevice_address = self.format_metadevice_address(str(resolved_device_id), canonical_id)
            self.register_source(device, metadevice_address, match)
            break

        # If no candidates resolved, record the first one as unresolved for diagnostics
        if not any_resolved and candidates:
            first_eid = next(iter(candidates))
            # Only record if not already tracked by process_resolution_with_status
            if self.manager.get_resolution_status(first_eid) is None:
                self.manager.record_resolution_failure(
                    first_eid, device.address, EidResolutionStatus.NO_KNOWN_EID_MATCH
                )

    def prune_source(self, device: BermudaDevice, stamp_fmdn: float, prune_list: list[str]) -> bool:
        """Prune stale FMDN rotating MACs and return True if pruned."""
        if METADEVICE_TYPE_FMDN_SOURCE not in device.metadevice_type:
            return False
        if device.last_seen >= stamp_fmdn:
            return False

        prune_list.append(device.address)
        return True

    def discover_metadevices(self) -> None:
        """
        Access the googlefindmy integration to find FMDN metadevices to track.

        This function sets up the skeleton metadevice entry for FMDN (Google Find My Device)
        devices, ready for update_metadevices to manage. It works similarly to
        discover_private_ble_metadevices().
        """
        if self.coordinator._do_fmdn_device_init:
            self.coordinator._do_fmdn_device_init = False
            _LOGGER.debug("Refreshing FMDN Device list from googlefindmy integration")

            # Iterate through the googlefindmy integration's entities,
            # and ensure for each "device" we create a metadevice.
            fmdn_entries = self.coordinator.hass.config_entries.async_entries(DOMAIN_GOOGLEFINDMY, include_disabled=False)
            for fmdn_entry in fmdn_entries:
                fmdn_entities = self.coordinator.er.entities.get_entries_for_config_entry_id(fmdn_entry.entry_id)
                # This will be a list of entities for a given googlefindmy device,
                # let's pull out the device_tracker one, since it has the device
                # info we need.
                for fmdn_entity in fmdn_entities:
                    if fmdn_entity.domain == Platform.DEVICE_TRACKER:
                        # We found a *device_tracker* entity for the googlefindmy device.
                        _LOGGER.debug(
                            "Found a googlefindmy FMDN Device Tracker! %s",
                            fmdn_entity.entity_id,
                        )

                        # Grab the device entry (for the name and device_id)
                        if fmdn_entity.device_id is not None:
                            fmdn_device = self.coordinator.dr.async_get(fmdn_entity.device_id)
                        else:
                            fmdn_device = None

                        if fmdn_device is None:
                            _LOGGER.debug(
                                "No device registry entry for FMDN entity %s",
                                fmdn_entity.entity_id,
                            )
                            continue

                        # Extract the canonical device identifier from googlefindmy's identifiers.
                        # Per docs/google_find_my_support.md, the EID resolver returns canonical_id
                        # in the format "{entry_id}:{device_id}" (with one colon separator).
                        # The googlefindmy integration uses multiple identifier formats:
                        # - (DOMAIN, "entry_id:subentry_id:device_id") - full format (2 colons)
                        # - (DOMAIN, "entry_id:device_id") - canonical format (1 colon)
                        # - (DOMAIN, "device_id") - simplest format (0 colons)
                        # We need to find the identifier matching the resolver's format.
                        canonical_id = None
                        for identifier in fmdn_device.identifiers:
                            if len(identifier) == 2 and identifier[0] == DOMAIN_GOOGLEFINDMY:
                                # Found a googlefindmy identifier
                                id_value = identifier[1]
                                colon_count = id_value.count(":")
                                # Prefer the "entry_id:device_id" format (1 colon) as it matches
                                # what the EID resolver returns for canonical_id
                                if colon_count == 1:
                                    canonical_id = id_value
                                    break
                                # Keep track of the simplest identifier as fallback
                                if colon_count == 0 and canonical_id is None:
                                    canonical_id = id_value

                        # Fall back to entity unique_id if no googlefindmy identifier found
                        if canonical_id is None:
                            canonical_id = fmdn_entity.unique_id

                        # IMPORTANT: Before creating a new metadevice, check if one already
                        # exists for this fmdn_device_id. This prevents duplicate devices when
                        # register_source() has already created a metadevice from BLE
                        # advertisements with a different canonical_id format.
                        # The fmdn_device_id is the HA Device Registry ID, which is stable
                        # and consistent between both registration paths.
                        existing_metadevice = None
                        for existing in self.coordinator.metadevices.values():
                            if existing.fmdn_device_id == fmdn_device.id:
                                existing_metadevice = existing
                                _LOGGER.debug(
                                    "Found existing FMDN metadevice %s for device_id %s",
                                    existing.address,
                                    fmdn_device.id,
                                )
                                break

                        if existing_metadevice is not None:
                            # Use the existing metadevice created by register_source
                            metadevice = existing_metadevice
                        else:
                            # Use the canonical_id as the basis for the metadevice address
                            # This matches the format used by register_source when
                            # receiving advertisements from the EID resolver.
                            metadevice_address = self.format_metadevice_address(fmdn_device.id, canonical_id)

                            # Create our Meta-Device and tag it up...
                            metadevice = self.coordinator._get_or_create_device(metadevice_address)

                        # Since user has already configured the googlefindmy Device, we
                        # always create sensors for them.
                        metadevice.create_sensor = True
                        metadevice.metadevice_type.add(METADEVICE_FMDN_DEVICE)
                        metadevice.address_type = ADDR_TYPE_FMDN_DEVICE
                        metadevice.fmdn_device_id = fmdn_device.id
                        # Update fmdn_canonical_id if not already set or if we have a
                        # better value (one extracted from identifiers)
                        if metadevice.fmdn_canonical_id is None or canonical_id is not None:
                            metadevice.fmdn_canonical_id = canonical_id

                        # Set a nice name
                        metadevice.name_by_user = fmdn_device.name_by_user
                        metadevice.name_devreg = fmdn_device.name
                        metadevice.make_name()

                        # Add metadevice to list so it gets included in update_metadevices
                        if metadevice.address not in self.coordinator.metadevices:
                            self.coordinator.metadevices[metadevice.address] = metadevice

                        _LOGGER.debug(
                            "Registered FMDN metadevice %s for %s",
                            metadevice.address,
                            fmdn_device.name,
                        )
