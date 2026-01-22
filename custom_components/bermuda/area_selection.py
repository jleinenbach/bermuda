"""
Area selection logic for Bermuda BLE tracker.

This module contains all area/room selection algorithms including:
- UKF (Unscented Kalman Filter) fingerprint matching
- Min-distance heuristic fallback
- Virtual distance calculation for scannerless rooms
- Cross-floor protection and streak logic
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import (
    AREA_MAX_AD_AGE_DEFAULT,
    AREA_MAX_AD_AGE_LIMIT,
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    EVIDENCE_WINDOW_SECONDS,
    UKF_MIN_SCANNERS,
    UPDATE_INTERVAL,
    VIRTUAL_DISTANCE_MIN_SCORE,
    VIRTUAL_DISTANCE_SCALE,
)
from .correlation import z_scores_to_confidence

if TYPE_CHECKING:
    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class AreaTests:
    """
    Holds the results of Area-based tests.

    Likely to become a stand-alone class for performing the whole area-selection
    process.
    """

    device: str = ""
    scannername: tuple[str, str] = ("", "")
    areas: tuple[str, str] = ("", "")
    pcnt_diff: float = 0  # distance percentage difference.
    same_area: bool = False  # The old scanner is in the same area as us.
    last_ad_age: tuple[float, float] = (0, 0)  # seconds since we last got *any* ad from scanner
    this_ad_age: tuple[float, float] = (0, 0)  # how old the *current* advert is on this scanner
    distance: tuple[float, float] = (0, 0)
    hist_min_max: tuple[float, float] = (0, 0)  # min/max distance from history
    floors: tuple[str | None, str | None] = (None, None)
    floor_levels: tuple[str | int | None, str | int | None] = (None, None)
    reason: str | None = None  # reason/result

    def sensortext(self) -> str:
        """Return a text summary suitable for use in a sensor entity."""
        out = ""
        for var, val in vars(self).items():
            out += f"{var}|"
            if isinstance(val, tuple):
                for v in val:
                    if isinstance(v, float):
                        out += f"{v:.2f}|"
                    else:
                        out += f"{v}"
            elif var == "pcnt_diff":
                out += f"{val:.3f}"
            else:
                out += f"{val}"
            out += "\n"
        return out[:255]

    def __str__(self) -> str:
        """
        Create string representation for easy debug logging/dumping
        and potentially a sensor for logging Area decisions.
        """
        out = ""
        for var, val in vars(self).items():
            out += f"** {var:20} "
            if isinstance(val, tuple):
                for v in val:
                    if isinstance(v, float):
                        out += f"{v:.2f} "
                    else:
                        out += f"{v} "
                out += "\n"
            elif var == "pcnt_diff":
                out += f"{val:.3f}\n"
            else:
                out += f"{val}\n"
        return out


class AreaSelectionHandler:
    """
    Handles all area/room selection logic for Bermuda devices.

    This class encapsulates the complex area selection algorithms including:
    - UKF fingerprint matching for learned room patterns
    - Min-distance heuristic for simple proximity-based selection
    - Virtual distance calculation for scannerless rooms
    - Cross-floor protection with streak requirements
    - Stability margins and hysteresis to prevent flickering
    """

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        """
        Initialize the area selection handler.

        Args:
            coordinator: The parent coordinator that owns device state and configuration.

        """
        self.coordinator = coordinator

    # =========================================================================
    # Property accessors for coordinator state
    # =========================================================================

    @property
    def options(self) -> dict[str, Any]:
        """Access coordinator options."""
        return self.coordinator.options

    @property
    def correlations(self) -> dict[str, dict[str, Any]]:
        """Access device-specific correlation profiles."""
        return self.coordinator.correlations

    @property
    def room_profiles(self) -> dict[str, Any]:
        """Access device-independent room profiles."""
        return self.coordinator.room_profiles

    @property
    def device_ukfs(self) -> dict[str, Any]:
        """Access per-device UKF states."""
        return self.coordinator.device_ukfs

    @property
    def _scanners(self) -> set[Any]:
        """Access scanner set."""
        return self.coordinator._scanners  # noqa: SLF001

    @property
    def ar(self) -> Any:
        """Access Home Assistant area registry."""
        return self.coordinator.ar

    @property
    def devices(self) -> dict[str, BermudaDevice]:
        """Access device dictionary."""
        return self.coordinator.devices

    # =========================================================================
    # Pure helper functions (no coordinator state access)
    # =========================================================================

    def _calculate_virtual_distance(self, score: float, max_radius: float) -> float:
        """
        Convert a UKF fingerprint match score to a virtual distance.

        Uses a scaled quadratic formula that rewards medium scores (0.3-0.5)
        more aggressively than linear, allowing scannerless rooms to compete
        against physical scanners through walls.

        Formula: max_radius * SCALE * (1 - score)²

        Args:
            score: UKF match score (0.0 to 1.0)
            max_radius: Maximum radius from configuration

        Returns:
            Virtual distance in meters. Lower scores produce larger distances.

        """
        score_clamped = max(VIRTUAL_DISTANCE_MIN_SCORE, min(1.0, score))
        return max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score_clamped) ** 2)

    def _collect_current_stamps(self, device: BermudaDevice, nowstamp: float) -> dict[str, float]:
        """
        Collect current scanner timestamps from device adverts.

        This helper is used by BUG 20 fix to ensure streak counting only
        increments when NEW advertisement data arrives, preventing cached
        values from being counted multiple times.

        Args:
            device: The BermudaDevice to collect stamps from.
            nowstamp: Current monotonic timestamp for freshness check.

        Returns:
            Dictionary mapping scanner_address to advert timestamp.
            Only includes adverts within EVIDENCE_WINDOW_SECONDS.

        """
        current_stamps: dict[str, float] = {}
        for advert in device.adverts.values():
            if (
                advert.stamp is not None
                and advert.scanner_address is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
            ):
                current_stamps[advert.scanner_address] = advert.stamp
        return current_stamps

    def _has_new_advert_data(self, current_stamps: dict[str, float], last_stamps: dict[str, float]) -> bool:
        """
        Check if any scanner has newer advertisement data.

        Compares current timestamps against previously recorded timestamps
        to detect when new BLE advertisement data has arrived.

        Args:
            current_stamps: Current scanner timestamps from _collect_current_stamps().
            last_stamps: Previously recorded timestamps (e.g., device.pending_last_stamps).

        Returns:
            True if at least one scanner has a newer timestamp.

        """
        return any(current_stamps.get(scanner, 0) > last_stamps.get(scanner, 0) for scanner in current_stamps)

    # =========================================================================
    # Registry helper functions (access ar, _scanners)
    # =========================================================================

    def _resolve_floor_id_for_area(self, area_id: str | None) -> str | None:
        """
        Resolve floor_id for an area_id using the Home Assistant area registry.

        This is essential for scannerless rooms where we can't get floor_id from
        a scanner_device - we must look up the area directly.
        """
        if area_id is None:
            return None
        area = self.ar.async_get_area(area_id)
        if area is not None:
            return getattr(area, "floor_id", None)
        return None

    def _area_has_scanner(self, area_id: str) -> bool:
        """
        Check if an area has at least one scanner assigned to it.

        Args:
            area_id: The Home Assistant area ID to check.

        Returns:
            True if the area contains at least one scanner device.

        """
        return any(scanner.area_id == area_id for scanner in self._scanners)

    def resolve_area_name(self, area_id: str | None) -> str | None:
        """
        Given an area_id, return the current area name.

        Will return None if the area id does *not* resolve to a single
        known area name.
        """
        if area_id is None:
            return None

        areas = self.ar.async_get_area(area_id)
        if hasattr(areas, "name"):
            return getattr(areas, "name", "invalid_area")
        return None

    def effective_distance(self, advert: BermudaAdvert | None, nowstamp: float) -> float | None:
        """
        Calculate the best available distance estimate for an advert.

        Rules:
        1) If advert.rssi_distance is present, prefer it (smoothed distance).
        2) If the advert is fresh and has historical distance samples, return the most recent
           historical value to preserve the last known proximity when smoothing yields None.
        3) Otherwise return None.
        """
        if advert is None:
            return None

        if advert.rssi_distance is not None:
            return advert.rssi_distance

        # Use the advert's adaptive timeout if available, otherwise fall back to default.
        max_age = getattr(advert, "adaptive_timeout", None) or AREA_MAX_AD_AGE_DEFAULT
        max_age = min(max_age, AREA_MAX_AD_AGE_LIMIT)

        if advert.stamp < nowstamp - max_age:
            return None

        hist_distances = [
            value for value in getattr(advert, "hist_distance_by_interval", []) if isinstance(value, (int, float))
        ]
        if hist_distances:
            return hist_distances[0]

        return None

    # =========================================================================
    # Correlation confidence (accesses self.correlations)
    # =========================================================================

    def _get_correlation_confidence(  # noqa: PLR0911
        self,
        device_address: str,
        area_id: str,
        primary_rssi: float | None,
        current_readings: dict[str, float],
    ) -> float:
        """
        Calculate correlation confidence for a device in an area.

        Compares observed RSSI patterns against learned expectations.

        Args:
            device_address: The device's address.
            area_id: The area to check confidence for.
            primary_rssi: RSSI from the primary scanner.
            current_readings: Map of scanner_id to RSSI for all visible scanners.

        Returns:
            Confidence value 0.0-1.0. Returns 1.0 if no learned data exists.

        """
        if device_address not in self.correlations:
            return 1.0
        if area_id not in self.correlations[device_address]:
            return 1.0
        if primary_rssi is None:
            return 1.0

        profile = self.correlations[device_address][area_id]
        if profile.mature_correlation_count == 0:
            return 1.0

        z_scores = profile.get_z_scores(primary_rssi, current_readings)
        if not z_scores:
            return 1.0

        # FIX: Integrate absolute RSSI z-scores to detect far-field false positives.
        absolute_z_scores = profile.get_absolute_z_scores(current_readings)
        if absolute_z_scores:
            max_abs_z = max(z for _, z in absolute_z_scores)
            if max_abs_z > 3.0:
                absolute_penalty: float = 0.5 ** (max_abs_z - 2.0)
                delta_confidence: float = z_scores_to_confidence(z_scores)
                return float(delta_confidence * absolute_penalty)

        return z_scores_to_confidence(z_scores)

    # =========================================================================
    # Virtual distance for scannerless rooms
    # =========================================================================

    def _get_virtual_distances_for_scannerless_rooms(
        self,
        device: BermudaDevice,
        rssi_readings: dict[str, float],
    ) -> dict[str, float]:
        """
        Calculate virtual distances for scannerless rooms based on UKF fingerprint match.

        When UKF score is below threshold for switching, scannerless rooms would normally
        be invisible to min-distance fallback (since they have no scanner to measure).
        This method calculates a "virtual distance" based on how well the current RSSI
        pattern matches the trained fingerprint, allowing scannerless rooms to compete.

        Only considers rooms that:
        1. Have been explicitly button-trained by the user
        2. Have no physical scanner in the room
        3. Have a minimum UKF score (to avoid phantom matches)

        Args:
            device: The device to calculate virtual distances for.
            rssi_readings: Current RSSI readings from all visible scanners.

        Returns:
            Dict mapping area_id to virtual distance (meters) for scannerless rooms.

        """
        from .filters.ukf import UnscentedKalmanFilter  # noqa: PLC0415

        virtual_distances: dict[str, float] = {}

        # Need device profiles to calculate fingerprint matches
        if device.address not in self.correlations:
            return virtual_distances

        device_profiles = self.correlations[device.address]
        max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)

        # Need minimum scanners for meaningful score calculation
        if len(rssi_readings) < UKF_MIN_SCANNERS:
            return virtual_distances

        # Get or create UKF for this device
        if device.address not in self.device_ukfs:
            self.device_ukfs[device.address] = UnscentedKalmanFilter()

        ukf = self.device_ukfs[device.address]

        # Update UKF with current readings before matching
        ukf.predict(dt=UPDATE_INTERVAL)
        ukf.update_multi(rssi_readings)

        # Get all matches from UKF
        matches = ukf.match_fingerprints(device_profiles, self.room_profiles)

        # DEBUG logging
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Virtual distance check for %s: %d device_profiles, %d matches, rssi_readings=%s, ukf_scanners=%s",
                device.name,
                len(device_profiles),
                len(matches),
                list(rssi_readings.keys()),
                ukf.scanner_addresses,
            )
            for area_id, profile in device_profiles.items():
                has_btn = profile.has_button_training
                has_scanner = self._area_has_scanner(area_id)
                abs_details = []
                if hasattr(profile, "_absolute_profiles"):
                    for scanner_addr, abs_prof in profile._absolute_profiles.items():  # noqa: SLF001
                        abs_details.append(
                            f"{scanner_addr[-8:]}:btn={abs_prof.button_sample_count}/auto={abs_prof.auto_sample_count}"
                        )
                _LOGGER.debug(
                    "  Profile %s: has_button_training=%s, area_has_scanner=%s, abs_profiles=[%s]",
                    area_id,
                    has_btn,
                    has_scanner,
                    ", ".join(abs_details) if abs_details else "none",
                )

        for area_id, _d_squared, score in matches:
            # Only consider button-trained profiles (explicit user intent)
            area_profile = device_profiles.get(area_id)
            if area_profile is None or not area_profile.has_button_training:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "  Skipping %s: no button training (profile=%s, has_btn=%s)",
                        area_id,
                        area_profile is not None,
                        area_profile.has_button_training if area_profile else "N/A",
                    )
                continue

            # Only consider scannerless rooms (rooms with scanners use real distance)
            if self._area_has_scanner(area_id):
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("  Skipping %s: area has scanner", area_id)
                continue

            # Minimum score threshold to avoid phantom matches
            if score < VIRTUAL_DISTANCE_MIN_SCORE:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug("  Skipping %s: score %.4f < min %.4f", area_id, score, VIRTUAL_DISTANCE_MIN_SCORE)
                continue

            # Calculate virtual distance using scaled quadratic formula
            virtual_dist = self._calculate_virtual_distance(score, max_radius)
            virtual_distances[area_id] = virtual_dist

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Virtual distance for %s in scannerless room %s: score=%.3f → distance=%.2fm",
                    device.name,
                    area_id,
                    score,
                    virtual_dist,
                )

        return virtual_distances

    # =========================================================================
    # Main entry point - refresh areas for all devices
    # =========================================================================

    def refresh_areas_by_min_distance(self) -> None:
        """Set area for ALL devices based on UKF+RoomProfile or min-distance fallback."""
        # Check if we have mature room profiles (at least 2 scanner-pairs with 30+ samples)
        has_mature_profiles = any(profile.mature_pair_count >= 2 for profile in self.room_profiles.values())

        for device in self.devices.values():
            self._determine_area_for_device(device, has_mature_profiles=has_mature_profiles)

    def _determine_area_for_device(self, device: BermudaDevice, *, has_mature_profiles: bool) -> None:
        """
        Determine and set the area for a single device.

        This method handles the complete area determination flow:
        1. Check if device needs processing (is tracked and not a scanner)
        2. Handle manual area locks from training UI
        3. Try UKF fingerprint matching (when profiles are mature or device has correlations)
        4. Fall back to min-distance heuristic

        Args:
            device: The BermudaDevice to determine area for
            has_mature_profiles: Whether the system has mature RoomProfiles globally

        """
        # Skip scanners and devices not being tracked
        if device.is_scanner or not (device.create_sensor or device.create_tracker_done):
            return

        # Check if device is manually locked to an area
        if device.area_locked_id is not None:
            # Device is locked by user selection for training.
            if device.area_locked_scanner_addr is not None:
                locked_advert = None
                for advert in device.adverts.values():
                    if advert.scanner_address == device.area_locked_scanner_addr:
                        locked_advert = advert
                        break

                if locked_advert is None:
                    _LOGGER.info(
                        "Auto-unlocking %s: locked scanner %s no longer has any advert",
                        device.name,
                        device.area_locked_scanner_addr,
                    )
                    device.area_locked_id = None
                    device.area_locked_name = None
                    device.area_locked_scanner_addr = None
                else:
                    # FIX: ACTIVE OVERRIDE - Set the device area to the locked area immediately.
                    device.update_area_and_floor(device.area_locked_id)
                    return
            else:
                # Scannerless room: no specific scanner to track, just force the area.
                device.update_area_and_floor(device.area_locked_id)
                return

        # Primary: UKF with RoomProfile (when profiles are mature)
        # Fallback: Simple min-distance (bootstrap phase)
        device_has_correlations = device.address in self.correlations and len(self.correlations[device.address]) > 0
        if (has_mature_profiles or device_has_correlations) and self._refresh_area_by_ukf(device):
            return
        self._refresh_area_by_min_distance(device)

    # =========================================================================
    # UKF-based area selection (placeholder - to be implemented in Phase 3)
    # =========================================================================

    def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:
        """
        Use UKF (Unscented Kalman Filter) for area selection via fingerprint matching.

        Delegates to coordinator's implementation (will be migrated in Phase 3).
        """
        # Delegate to coordinator - the method stays there for now
        return self.coordinator._refresh_area_by_ukf(device)  # noqa: SLF001

    # =========================================================================
    # Min-distance area selection (delegates to coordinator for Phase 4)
    # =========================================================================

    def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:
        """
        Very basic Area setting by finding closest proxy to a given device.

        Delegates to coordinator's implementation (will be migrated in Phase 4).
        """
        # Delegate to coordinator - the method stays there for now
        return self.coordinator._refresh_area_by_min_distance(device)  # noqa: SLF001
