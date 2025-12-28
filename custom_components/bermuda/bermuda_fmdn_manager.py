"""BermudaFmdnManager for handling FMDN EID resolution tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import floor
from typing import Any

from bluetooth_data_tools import monotonic_time_coarse

from .const import _LOGGER, PRUNE_TIME_FMDN


class EidResolutionStatus(Enum):
    """
    Enum of EID resolution statuses.

    Values used to mark if an EID was successfully resolved or not.
    """

    NOT_EVALUATED = "NOT_EVALUATED"  # default, not yet checked
    NO_KNOWN_EID_MATCH = "NO_KNOWN_EID_MATCH"  # checked but no match found
    RESOLVER_UNAVAILABLE = "RESOLVER_UNAVAILABLE"  # googlefindmy resolver not available
    RESOLVER_ERROR = "RESOLVER_ERROR"  # resolver raised an exception


@dataclass
class SeenEid:
    """Stores an EID along with its resolution result and metadata."""

    eid: bytes
    first_seen: float
    last_seen: float
    source_mac: str
    resolution_status: EidResolutionStatus | str  # str for resolved device_id
    device_id: str | None = None
    canonical_id: str | None = None
    check_count: int = 1


@dataclass
class EidResolutionStats:
    """Statistics for FMDN EID resolution."""

    total_eids_seen: int = 0
    total_eids_resolved: int = 0
    total_eids_unresolved: int = 0
    resolver_errors: int = 0
    resolver_unavailable_count: int = 0


class BermudaFmdnManager:
    """
    Manager for FMDN EID resolution tracking in Bermuda.

    This class mirrors the IRK manager pattern but for FMDN (Google Find My Device Network)
    EID (Ephemeral Identifier) resolution. It tracks:
    - Which EIDs have been seen (raw data received)
    - Whether they were successfully resolved to a device_id
    - Resolution failures (NO_KNOWN_EID_MATCH equivalent)

    This provides diagnostic visibility into the FMDN resolution process.
    """

    def __init__(self) -> None:
        """Initialize the FMDN manager."""
        # Map of EID hex string -> SeenEid data
        self._seen_eids: dict[str, SeenEid] = {}
        # Map of source MAC -> list of EID hex strings (for tracking which MACs sent which EIDs)
        self._mac_to_eids: dict[str, list[str]] = {}
        # Resolution statistics
        self._stats = EidResolutionStats()
        # Prune interval tracking
        self._last_prune: float = 0.0

    def record_eid_seen(
        self,
        eid: bytes,
        source_mac: str,
        *,
        resolution_status: EidResolutionStatus | str = EidResolutionStatus.NOT_EVALUATED,
        device_id: str | None = None,
        canonical_id: str | None = None,
    ) -> None:
        """
        Record that an EID was seen from a BLE advertisement.

        Args:
            eid: The raw EID bytes extracted from service data
            source_mac: The MAC address of the device broadcasting this EID
            resolution_status: Result of resolution attempt (EidResolutionStatus or device_id string)
            device_id: HA device registry ID if resolved
            canonical_id: Canonical identifier from resolver if available

        """
        nowstamp = monotonic_time_coarse()
        eid_hex = eid.hex()

        if eid_hex in self._seen_eids:
            # Update existing entry
            seen = self._seen_eids[eid_hex]
            seen.last_seen = nowstamp
            seen.check_count += 1
            # Update resolution status if we got a better result
            if resolution_status != EidResolutionStatus.NOT_EVALUATED:
                seen.resolution_status = resolution_status
                if device_id:
                    seen.device_id = device_id
                if canonical_id:
                    seen.canonical_id = canonical_id
        else:
            # Create new entry
            self._seen_eids[eid_hex] = SeenEid(
                eid=eid,
                first_seen=nowstamp,
                last_seen=nowstamp,
                source_mac=source_mac,
                resolution_status=resolution_status,
                device_id=device_id,
                canonical_id=canonical_id,
            )
            self._stats.total_eids_seen += 1

        # Track MAC -> EID mapping
        if source_mac not in self._mac_to_eids:
            self._mac_to_eids[source_mac] = []
        if eid_hex not in self._mac_to_eids[source_mac]:
            self._mac_to_eids[source_mac].append(eid_hex)

        # Update statistics based on resolution status
        self._update_stats(resolution_status)

    def _update_stats(self, resolution_status: EidResolutionStatus | str) -> None:
        """Update resolution statistics based on status."""
        if isinstance(resolution_status, str) and resolution_status not in [e.value for e in EidResolutionStatus]:
            # This is a device_id string, meaning successful resolution
            self._stats.total_eids_resolved += 1
        elif resolution_status == EidResolutionStatus.NO_KNOWN_EID_MATCH:
            self._stats.total_eids_unresolved += 1
        elif resolution_status == EidResolutionStatus.RESOLVER_ERROR:
            self._stats.resolver_errors += 1
        elif resolution_status == EidResolutionStatus.RESOLVER_UNAVAILABLE:
            self._stats.resolver_unavailable_count += 1

    def record_resolution_success(
        self,
        eid: bytes,
        source_mac: str,
        device_id: str,
        canonical_id: str | None = None,
    ) -> None:
        """Record a successful EID resolution."""
        self.record_eid_seen(
            eid,
            source_mac,
            resolution_status="RESOLVED",
            device_id=device_id,
            canonical_id=canonical_id,
        )

    def record_resolution_failure(
        self,
        eid: bytes,
        source_mac: str,
        status: EidResolutionStatus = EidResolutionStatus.NO_KNOWN_EID_MATCH,
    ) -> None:
        """Record a failed EID resolution attempt."""
        self.record_eid_seen(eid, source_mac, resolution_status=status)

    def async_prune(self) -> None:
        """
        Prune expired EID entries to prevent memory growth.

        Uses PRUNE_TIME_FMDN as the expiration window.
        """
        nowstamp = monotonic_time_coarse()

        # Only prune periodically (every 60 seconds)
        if nowstamp - self._last_prune < 60:
            return

        self._last_prune = nowstamp
        expiry_threshold = nowstamp - PRUNE_TIME_FMDN

        # Find and remove expired EIDs
        expired_eids = [eid_hex for eid_hex, seen in self._seen_eids.items() if seen.last_seen < expiry_threshold]

        for eid_hex in expired_eids:
            seen = self._seen_eids.pop(eid_hex)
            # Also clean up MAC -> EID mapping
            if seen.source_mac in self._mac_to_eids:
                if eid_hex in self._mac_to_eids[seen.source_mac]:
                    self._mac_to_eids[seen.source_mac].remove(eid_hex)
                if not self._mac_to_eids[seen.source_mac]:
                    del self._mac_to_eids[seen.source_mac]

        if expired_eids:
            _LOGGER.debug(
                "BermudaFmdn pruned %d of %d EIDs from cache",
                len(expired_eids),
                len(expired_eids) + len(self._seen_eids),
            )

    def get_resolution_status(self, eid: bytes) -> EidResolutionStatus | str | None:
        """Get the resolution status for a specific EID."""
        eid_hex = eid.hex()
        if seen := self._seen_eids.get(eid_hex):
            return seen.resolution_status
        return None

    def async_diagnostics_no_redactions(self) -> dict[str, Any]:
        """
        Return diagnostic info for FMDN resolution.

        Make sure to run redactions over the results before exposing to users.
        Format mirrors the IRK manager diagnostics for consistency.
        """
        nowstamp = monotonic_time_coarse()

        # Build EID entries similar to IRK manager's MAC entries
        eids: dict[str, dict[str, Any]] = {}

        for eid_hex, seen in self._seen_eids.items():
            # Format resolution status for output
            if isinstance(seen.resolution_status, EidResolutionStatus):
                status_out = seen.resolution_status.name
            else:
                status_out = "RESOLVED"

            entry: dict[str, Any] = {
                "status": status_out,
                "source_mac": seen.source_mac,
                "expires_in": floor(seen.last_seen + PRUNE_TIME_FMDN - nowstamp),
                "check_count": seen.check_count,
                "eid_length": len(seen.eid),
            }

            # Include device_id for resolved EIDs
            if seen.device_id:
                entry["device_id"] = seen.device_id
            if seen.canonical_id:
                entry["canonical_id"] = seen.canonical_id

            eids[eid_hex] = entry

        # Separate resolved vs unresolved for easier debugging
        resolved_eids = {k: v for k, v in eids.items() if v["status"] == "RESOLVED"}
        unresolved_eids = {k: v for k, v in eids.items() if v["status"] != "RESOLVED"}

        return {
            "stats": {
                "total_eids_seen": self._stats.total_eids_seen,
                "total_eids_resolved": self._stats.total_eids_resolved,
                "total_eids_unresolved": self._stats.total_eids_unresolved,
                "resolver_errors": self._stats.resolver_errors,
                "resolver_unavailable_count": self._stats.resolver_unavailable_count,
                "current_cache_size": len(self._seen_eids),
            },
            "resolved_eids": resolved_eids,
            "unresolved_eids": unresolved_eids,
            "source_macs": {
                mac: {"eid_count": len(eid_list), "eids": eid_list} for mac, eid_list in self._mac_to_eids.items()
            },
        }
