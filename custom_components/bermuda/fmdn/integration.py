"""FMDN integration layer for Bermuda coordinator."""

# pylint: disable=import-error,no-name-in-module

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, cast

from homeassistant.const import Platform

from custom_components.bermuda.const import (
    _LOGGER,
    ADDR_TYPE_FMDN_DEVICE,
    DATA_EID_RESOLVER,
    DEFAULT_FMDN_EID_FORMAT,
    DOMAIN_GOOGLEFINDMY,
    METADEVICE_FMDN_DEVICE,
    METADEVICE_TYPE_FMDN_SOURCE,
)
from custom_components.bermuda.util import normalize_identifier

from .extraction import extract_fmdn_eids
from .manager import BermudaFmdnManager, EidResolutionStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.bermuda.bermuda_device import BermudaDevice
    from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


class EIDMatch(NamedTuple):
    """
    Local type definition matching GoogleFindMy-HA's EIDMatch structure.

    This provides type safety for Bermuda's EID resolution code without
    creating a hard dependency on GoogleFindMy-HA's internal types.

    See: https://github.com/jleinenbach/GoogleFindMy-HA/blob/1.7.0-3/
         custom_components/googlefindmy/eid_resolver.py

    Fields:
        device_id: HA Device Registry ID - unique per account, PRIMARY identifier.
        config_entry_id: HA Config Entry ID for the GoogleFindMy integration.
        canonical_id: Google UUID - shared across accounts for same physical device.
        time_offset: EID time window offset in seconds (used for match ranking).
        is_reversed: Whether EID bytes are in reversed order.
    """

    device_id: str
    config_entry_id: str
    canonical_id: str
    time_offset: int
    is_reversed: bool


def _convert_to_eid_match(raw_match: Any) -> EIDMatch | None:
    """
    Convert an external resolver match to our local EIDMatch type.

    This function safely converts the external resolver's return value to our
    typed EIDMatch structure, handling missing fields gracefully with defaults.

    Args:
        raw_match: The match object returned by the external resolver.

    Returns:
        EIDMatch if conversion succeeds, None if the match is invalid.

    """
    if raw_match is None:
        return None

    try:
        return EIDMatch(
            device_id=str(getattr(raw_match, "device_id", "") or ""),
            config_entry_id=str(getattr(raw_match, "config_entry_id", "") or ""),
            canonical_id=str(getattr(raw_match, "canonical_id", "") or ""),
            time_offset=int(getattr(raw_match, "time_offset", 0) or 0),
            is_reversed=bool(getattr(raw_match, "is_reversed", False)),
        )
    except (TypeError, ValueError, AttributeError) as ex:
        _LOGGER.debug("Failed to convert resolver match to EIDMatch: %s", ex)
        return None


class EidResolver(Protocol):
    """Protocol for the googlefindmy EID resolver."""

    def resolve_eid(self, eid: bytes) -> EIDMatch | None:
        """Resolve an EID to a device match (best match for shared trackers)."""

    def resolve_eid_all(self, eid: bytes) -> list[EIDMatch]:
        """Resolve an EID to all matching devices (for shared trackers)."""


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
        # Cache for O(1) lookup of metadevice by fmdn_device_id (HA Device Registry ID)
        self._fmdn_device_id_cache: dict[str, str] = {}
        # Cache for O(1) lookup of metadevice by canonical_id (GoogleFindMy API ID)
        self._fmdn_canonical_id_cache: dict[str, str] = {}
        # Lock to prevent race conditions during metadevice registration
        self._registration_lock = threading.Lock()

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

    def format_metadevice_address(
        self,
        device_id: str | None,
        canonical_id: str | None,
    ) -> str:
        """
        Return the canonical key for an FMDN metadevice.

        IMPORTANT: Uses device_id (HA Device Registry ID) as PRIMARY identifier.

        This is critical for shared trackers: When a physical tracker (e.g., Moto Tag)
        is shared between multiple Google accounts, GoogleFindMy-HA creates separate
        HA device entries for each account. All these devices share the SAME canonical_id
        (the Google UUID), but have DIFFERENT device_ids.

        If we used canonical_id as primary:
        - Account A: format_metadevice_address("ha_id_A", "UUID") → "fmdn:UUID"
        - Account B: format_metadevice_address("ha_id_B", "UUID") → "fmdn:UUID" (COLLISION!)
        - Result: Only ONE metadevice, wrong device entry!

        By using device_id as primary:
        - Account A: format_metadevice_address("ha_id_A", "UUID") → "fmdn:ha_id_A"
        - Account B: format_metadevice_address("ha_id_B", "UUID") → "fmdn:ha_id_B"
        - Result: SEPARATE metadevices, correct device congealment!

        Args:
            device_id: HA Device Registry ID (PRIMARY - unique per account)
            canonical_id: Native ID from GoogleFindMy API (fallback - shared across accounts)

        """
        # Use device_id as primary identifier - it's unique per HA device entry,
        # allowing shared trackers to have separate metadevices for each account.
        if device_id:
            return normalize_identifier(f"fmdn:{device_id}")
        # Fallback to canonical_id only if device_id is missing (rare edge case)
        if canonical_id:
            _LOGGER.debug("Using canonical_id fallback for FMDN address (device_id unavailable)")
            return normalize_identifier(f"fmdn:{canonical_id}")
        # Should never happen, but handle gracefully
        _LOGGER.warning("FMDN metadevice has neither canonical_id nor device_id")
        return normalize_identifier("fmdn:unknown")

    def _get_cached_metadevice(
        self, fmdn_device_id: str | None = None, canonical_id: str | None = None
    ) -> BermudaDevice | None:
        """
        Look up a metadevice by fmdn_device_id or canonical_id using O(1) cache.

        IMPORTANT: device_id is the primary identifier and MUST be checked first.
        canonical_id fallback is ONLY used when device_id is None (not just "not in cache").

        For shared trackers (same physical device in multiple accounts):
        - device_id is UNIQUE per account → correct cache hit
        - canonical_id is SHARED across accounts → would cause wrong cache hit!

        CRITICAL: When a device_id is provided but not in cache, do NOT fall back to
        canonical_id! That canonical_id might belong to a different account's metadevice.
        The fallback to canonical_id is ONLY for cases where device_id is truly None.

        Returns the metadevice if found, None otherwise.
        """
        # Try device_id cache FIRST (primary identifier - unique per account)
        # This prevents shared tracker collisions where multiple accounts have the same canonical_id
        if fmdn_device_id:
            if fmdn_device_id in self._fmdn_device_id_cache:
                cached_address = self._fmdn_device_id_cache[fmdn_device_id]
                if cached_address in self.coordinator.metadevices:
                    return self.coordinator.metadevices[cached_address]
                # Cache entry is stale, remove it
                del self._fmdn_device_id_cache[fmdn_device_id]
            # CRITICAL: device_id was provided but not found in cache.
            # Do NOT fall back to canonical_id - that might return a different account's metadevice!
            # Return None to create a new metadevice for this device_id.
            return None

        # Try canonical_id cache ONLY when device_id is None
        # (This is the fallback for older EID resolver versions that don't provide device_id)
        if canonical_id and canonical_id in self._fmdn_canonical_id_cache:
            cached_address = self._fmdn_canonical_id_cache[canonical_id]
            if cached_address in self.coordinator.metadevices:
                return self.coordinator.metadevices[cached_address]
            # Cache entry is stale, remove it
            del self._fmdn_canonical_id_cache[canonical_id]

        return None

    def _update_cache(
        self,
        metadevice_address: str,
        fmdn_device_id: str | None = None,
        canonical_id: str | None = None,
    ) -> None:
        """Update the caches for metadevice lookup."""
        if fmdn_device_id:
            self._fmdn_device_id_cache[fmdn_device_id] = metadevice_address
        if canonical_id:
            self._fmdn_canonical_id_cache[canonical_id] = metadevice_address

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

    def process_resolution(self, eid_bytes: bytes) -> EIDMatch | None:
        """Resolve an EID payload to a Home Assistant device registry id."""
        match, _ = self.process_resolution_with_status(eid_bytes, source_mac="unknown")
        return match

    def process_resolution_with_status(
        self, eid_bytes: bytes, source_mac: str
    ) -> tuple[EIDMatch | None, EidResolutionStatus]:
        """
        Resolve an EID payload and track the resolution status.

        Returns:
            Tuple of (EIDMatch result, resolution status)

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
            raw_match = resolver.resolve_eid(normalized_eid)
        except (ValueError, TypeError, AttributeError, KeyError) as ex:
            # Known exceptions from data processing issues
            _LOGGER.debug(
                "Resolver raised %s while processing EID payload: %s",
                type(ex).__name__,
                ex,
            )
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_ERROR)
            return None, EidResolutionStatus.RESOLVER_ERROR
        except Exception as ex:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Catch-all for unexpected errors from external resolver
            _LOGGER.warning(
                "Unexpected %s from EID resolver: %s",
                type(ex).__name__,
                ex,
                exc_info=True,
            )
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_ERROR)
            return None, EidResolutionStatus.RESOLVER_ERROR

        # Convert external match to typed EIDMatch
        match = _convert_to_eid_match(raw_match)
        if match is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.NO_KNOWN_EID_MATCH)
            return None, EidResolutionStatus.NO_KNOWN_EID_MATCH

        # Diagnostic logging for typically unused fields
        if match.time_offset != 0:
            _LOGGER.debug(
                "FMDN resolution time_offset=%d (non-zero may indicate stale match)",
                match.time_offset,
            )
        if match.is_reversed:
            _LOGGER.debug("FMDN resolution is_reversed=True (byte order reversed)")

        # Match found - will be recorded by caller with device_id
        return match, EidResolutionStatus.NOT_EVALUATED

    def process_resolution_all_with_status(  # noqa: PLR0911  # pylint: disable=too-many-return-statements
        self, eid_bytes: bytes, source_mac: str
    ) -> tuple[list[EIDMatch], EidResolutionStatus]:
        """
        Resolve an EID payload to ALL matching devices (for shared trackers).

        When a physical tracker is shared between multiple Google accounts,
        each account has its own device entry in Home Assistant. This method
        returns all matching devices so that Bermuda sensors can be created
        for each one.

        Returns:
            Tuple of (list of EIDMatch, resolution status)

        """
        resolver = self.get_resolver()

        if resolver is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_UNAVAILABLE)
            return [], EidResolutionStatus.RESOLVER_UNAVAILABLE

        normalized_eid = self.normalize_eid_bytes(eid_bytes)
        if normalized_eid is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.NO_KNOWN_EID_MATCH)
            return [], EidResolutionStatus.NO_KNOWN_EID_MATCH

        # Try resolve_eid_all first (returns all matches for shared trackers)
        resolve_all = getattr(resolver, "resolve_eid_all", None)
        resolve_all_failed = False
        if callable(resolve_all):
            try:
                raw_matches = resolve_all(normalized_eid)  # pylint: disable=not-callable
                if raw_matches:
                    # Convert all matches to typed EIDMatch
                    typed_matches = [m for raw in raw_matches if (m := _convert_to_eid_match(raw)) is not None]
                    if typed_matches:
                        # Diagnostic logging for first match
                        first = typed_matches[0]
                        if first.time_offset != 0:
                            _LOGGER.debug(
                                "FMDN resolution time_offset=%d (non-zero may indicate stale match)",
                                first.time_offset,
                            )
                        if first.is_reversed:
                            _LOGGER.debug("FMDN resolution is_reversed=True (byte order reversed)")
                        return typed_matches, EidResolutionStatus.NOT_EVALUATED
            except (ValueError, TypeError, AttributeError, KeyError) as ex:
                # Known exceptions from data processing issues
                _LOGGER.debug(
                    "resolve_eid_all raised %s: %s - falling back to resolve_eid",
                    type(ex).__name__,
                    ex,
                )
                resolve_all_failed = True
            except Exception as ex:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                # Catch-all for unexpected errors from external resolver
                _LOGGER.warning(
                    "Unexpected %s from resolve_eid_all: %s - falling back to resolve_eid",
                    type(ex).__name__,
                    ex,
                    exc_info=True,
                )
                resolve_all_failed = True

        # Fallback to resolve_eid (single match) for older GoogleFindMy versions
        # or when resolve_eid_all failed
        if resolve_all_failed:
            _LOGGER.debug("Attempting fallback to resolve_eid after resolve_eid_all failure")

        try:
            raw_single_match = resolver.resolve_eid(normalized_eid)
        except (ValueError, TypeError, AttributeError, KeyError) as ex:
            # Known exceptions from data processing issues
            _LOGGER.debug(
                "Resolver raised %s while processing EID payload: %s",
                type(ex).__name__,
                ex,
            )
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_ERROR)
            return [], EidResolutionStatus.RESOLVER_ERROR
        except Exception as ex:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Catch-all for unexpected errors from external resolver
            _LOGGER.warning(
                "Unexpected %s from EID resolver: %s",
                type(ex).__name__,
                ex,
                exc_info=True,
            )
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.RESOLVER_ERROR)
            return [], EidResolutionStatus.RESOLVER_ERROR

        # Convert to typed EIDMatch
        single_match = _convert_to_eid_match(raw_single_match)
        if single_match is None:
            self.manager.record_resolution_failure(eid_bytes, source_mac, EidResolutionStatus.NO_KNOWN_EID_MATCH)
            return [], EidResolutionStatus.NO_KNOWN_EID_MATCH

        # Diagnostic logging
        if single_match.time_offset != 0:
            _LOGGER.debug(
                "FMDN resolution time_offset=%d (non-zero may indicate stale match)",
                single_match.time_offset,
            )
        if single_match.is_reversed:
            _LOGGER.debug("FMDN resolution is_reversed=True (byte order reversed)")

        # Wrap single match in a list for consistent handling
        return [single_match], EidResolutionStatus.NOT_EVALUATED

    def register_source(self, source_device: BermudaDevice, metadevice_address: str, match: EIDMatch) -> None:
        """
        Attach a rotating FMDN source MAC to its stable metadevice container.

        Uses a lock to prevent race conditions when multiple advertisements
        are processed concurrently, and a cache for O(1) metadevice lookup.
        """
        fmdn_device_id: str | None = match.device_id if match.device_id else None
        canonical_id: str | None = match.canonical_id if match.canonical_id else None

        # Use lock to prevent race conditions during metadevice creation
        with self._registration_lock:
            # IMPORTANT: Before creating a new metadevice, check if one already exists.
            # Uses O(1) cache lookup with both canonical_id and device_id.
            existing_metadevice = self._get_cached_metadevice(fmdn_device_id=fmdn_device_id, canonical_id=canonical_id)

            if existing_metadevice is not None:
                metadevice = existing_metadevice
                _LOGGER.debug(
                    "Found cached FMDN metadevice %s for device_id %s / canonical_id %s",
                    existing_metadevice.address,
                    fmdn_device_id,
                    canonical_id,
                )
            else:
                # pylint: disable-next=protected-access
                metadevice = self.coordinator._get_or_create_device(metadevice_address)  # noqa: SLF001

            metadevice.metadevice_type.add(METADEVICE_FMDN_DEVICE)
            metadevice.address_type = ADDR_TYPE_FMDN_DEVICE
            metadevice.fmdn_device_id = fmdn_device_id
            # Update fmdn_canonical_id from the resolver
            if canonical_id:
                metadevice.fmdn_canonical_id = canonical_id
            # Since the googlefindmy integration discovered this device, we always
            # create sensors for them (like Private BLE Devices).
            metadevice.create_sensor = True

            # Register metadevice in BOTH metadevices AND devices dictionaries.
            # The config_flow.py iterates over coordinator.devices (line 293),
            # so metadevices MUST be in coordinator.devices to appear in the UI.
            if metadevice.address not in self.coordinator.metadevices:
                self.coordinator.metadevices[metadevice.address] = metadevice
            # FIX: Ensure metadevice is also in coordinator.devices for config flow visibility
            if metadevice.address not in self.coordinator.devices:
                self.coordinator.devices[metadevice.address] = metadevice

            # Update cache for future O(1) lookups (both device_id and canonical_id)
            self._update_cache(
                metadevice_address=metadevice.address,
                fmdn_device_id=fmdn_device_id,
                canonical_id=canonical_id,
            )

        # These operations don't need the lock
        if metadevice.fmdn_device_id and (device_entry := self.coordinator.dr.async_get(metadevice.fmdn_device_id)):
            metadevice.name_devreg = device_entry.name
            metadevice.name_by_user = device_entry.name_by_user
            metadevice.make_name()

        source_device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        if source_device.address not in metadevice.metadevice_sources:
            metadevice.metadevice_sources.insert(0, source_device.address)

    def handle_advertisement(self, device: BermudaDevice, service_data: Mapping[str | int, Any]) -> None:
        """
        Process FMDN payloads for an advertisement.

        For shared trackers (same physical device registered in multiple Google accounts),
        this method creates/updates sensors for ALL matching devices, not just the first one.
        """
        if not service_data:
            return

        candidates = self.extract_eids(service_data)
        if not candidates:
            return

        device.metadevice_type.add(METADEVICE_TYPE_FMDN_SOURCE)

        # Track whether we found any match for this advertisement
        any_resolved = False

        for eid_bytes in candidates:
            # Use resolve_eid_all to get ALL matches (important for shared trackers)
            matches, _resolution_status = self.process_resolution_all_with_status(eid_bytes, device.address)

            if not matches:
                # EID was seen but not resolved - already tracked in process_resolution_all_with_status
                continue

            # Process ALL matches to support shared trackers between multiple accounts
            # Note: When resolve_eid_all returns multiple matches, it means the same
            # physical tracker is registered in multiple Google accounts.
            for match in matches:
                # With typed EIDMatch, we can access fields directly
                if not match.device_id:
                    _LOGGER.debug("Resolver returned match without device_id for candidate length %d", len(eid_bytes))
                    continue

                # Successfully resolved - record in FMDN manager with diagnostic fields
                self.manager.record_resolution_success(
                    eid_bytes,
                    device.address,
                    match.device_id,
                    match.canonical_id if match.canonical_id else None,
                    time_offset=match.time_offset,
                    is_reversed=match.is_reversed,
                )
                any_resolved = True

                metadevice_address = self.format_metadevice_address(match.device_id, match.canonical_id)
                self.register_source(device, metadevice_address, match)

            # Found matches for this EID candidate, no need to try other candidates
            if any_resolved:
                break

        # If no candidates resolved, record the first one as unresolved for diagnostics
        if not any_resolved and candidates:
            first_eid = next(iter(candidates))
            # Only record if not already tracked by process_resolution_all_with_status
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

        # FIX: Prevent duplicate entries - device may appear in multiple metadevices' sources
        if device.address not in prune_list:
            prune_list.append(device.address)
        return True

    @staticmethod
    def _extract_canonical_id(fmdn_device: Any) -> str | None:
        """
        Extract the canonical device identifier from googlefindmy's identifiers.

        GoogleFindMy-HA uses multiple identifier formats in the device registry:
        - (DOMAIN, "entry_id:subentry_id:device_id") - full format (2 colons)
        - (DOMAIN, "entry_id:device_id") - canonical format (1 colon)
        - (DOMAIN, "device_id") - simplest format (0 colons)

        The EID resolver normalizes canonical_id to UUID-only by splitting on ":"
        and taking the LAST segment. We must do the same to ensure consistency
        between devices discovered via entity enumeration and EID resolution.

        Returns the UUID-only device identifier, or None.
        """
        for identifier in fmdn_device.identifiers:
            if len(identifier) != 2 or identifier[0] != DOMAIN_GOOGLEFINDMY:
                continue
            # Found a googlefindmy identifier
            id_value: str = str(identifier[1])
            # Extract UUID-only portion (last segment after colons)
            # This matches what GoogleFindMy-HA's EID resolver does:
            # clean_canonical_id = identity.canonical_id.split(":")[-1]
            if ":" in id_value:
                return id_value.split(":")[-1]
            # Already UUID-only format
            return id_value
        return None

    def _process_fmdn_entity(self, fmdn_entity: Any) -> None:
        """Process a single FMDN entity and create/update its metadevice."""
        if fmdn_entity.domain != Platform.DEVICE_TRACKER:
            return

        _LOGGER.debug("Found a googlefindmy FMDN Device Tracker! %s", fmdn_entity.entity_id)

        # Grab the device entry (for the name and device_id)
        fmdn_device = None
        if fmdn_entity.device_id is not None:
            fmdn_device = self.coordinator.dr.async_get(fmdn_entity.device_id)

        if fmdn_device is None:
            _LOGGER.debug("No device registry entry for FMDN entity %s", fmdn_entity.entity_id)
            return

        # Extract canonical_id, falling back to entity unique_id
        canonical_id = self._extract_canonical_id(fmdn_device) or fmdn_entity.unique_id

        # Check if metadevice already exists (O(1) cache lookup with both IDs)
        existing_metadevice = self._get_cached_metadevice(fmdn_device_id=fmdn_device.id, canonical_id=canonical_id)

        if existing_metadevice is not None:
            metadevice = existing_metadevice
            _LOGGER.debug(
                "Found cached FMDN metadevice %s for device_id %s / canonical_id %s",
                metadevice.address,
                fmdn_device.id,
                canonical_id,
            )
        else:
            metadevice_address = self.format_metadevice_address(fmdn_device.id, canonical_id)
            # pylint: disable-next=protected-access
            metadevice = self.coordinator._get_or_create_device(metadevice_address)  # noqa: SLF001

        # Configure the metadevice
        metadevice.create_sensor = True
        metadevice.metadevice_type.add(METADEVICE_FMDN_DEVICE)
        metadevice.address_type = ADDR_TYPE_FMDN_DEVICE
        metadevice.fmdn_device_id = fmdn_device.id
        if canonical_id:
            metadevice.fmdn_canonical_id = canonical_id

        # Set name from device registry
        metadevice.name_by_user = fmdn_device.name_by_user
        metadevice.name_devreg = fmdn_device.name
        metadevice.make_name()

        # Register metadevice in BOTH metadevices AND devices dictionaries.
        # The config_flow.py iterates over coordinator.devices (line 293),
        # so metadevices MUST be in coordinator.devices to appear in the UI.
        if metadevice.address not in self.coordinator.metadevices:
            self.coordinator.metadevices[metadevice.address] = metadevice
        # FIX: Ensure metadevice is also in coordinator.devices for config flow visibility
        if metadevice.address not in self.coordinator.devices:
            self.coordinator.devices[metadevice.address] = metadevice
        self._update_cache(
            metadevice_address=metadevice.address,
            fmdn_device_id=fmdn_device.id,
            canonical_id=canonical_id,
        )

        _LOGGER.debug("Registered FMDN metadevice %s for %s", metadevice.address, fmdn_device.name)

    def discover_metadevices(self) -> None:
        """
        Access the googlefindmy integration to find FMDN metadevices to track.

        This function sets up the skeleton metadevice entry for FMDN (Google Find My Device)
        devices, ready for update_metadevices to manage. It works similarly to
        discover_private_ble_metadevices().
        """
        # pylint: disable=protected-access
        if not self.coordinator._do_fmdn_device_init:  # noqa: SLF001
            return
        self.coordinator._do_fmdn_device_init = False  # noqa: SLF001
        # pylint: enable=protected-access

        _LOGGER.debug("Refreshing FMDN Device list from googlefindmy integration")

        fmdn_entries = self.coordinator.hass.config_entries.async_entries(DOMAIN_GOOGLEFINDMY, include_disabled=False)
        for fmdn_entry in fmdn_entries:
            fmdn_entities = self.coordinator.er.entities.get_entries_for_config_entry_id(fmdn_entry.entry_id)
            for fmdn_entity in fmdn_entities:
                self._process_fmdn_entity(fmdn_entity)
